#!/usr/bin/env python3
"""
X410 bench: WiFi burst scheduled from the 5G PSS, verified with an observer.

One X410, one timekeeper (no second AP, no PBCH/MIB yet):

  ch2 RX  5G SSB, continuous stream -> PSS start in X410 ticks
  tracker linear fit tick = a + b*k over the last PSS (k = SSB index) ->
          real SSB period in X410 ticks and prediction of future PSS
  sched   every --cycle-ssb SSB (100 ms) arm, --lookahead-ssb SSB ahead,
          a timed burst at t_tx = T_pred + --slot-offset-ms on ch0/ch1
          (10 Zadoff-Chu pulses every 200 us, ~1.85 ms, a distinct root per pulse)
          late or not locked -> the turn is MISSED and nothing is sent
  ch3 RX  observer: timed capture around every t_tx, correlates the pulses
          and returns the real start of the burst in X410 ticks

Measured per turn:
  pred_err_ns  = T_pred - T_actual          (tracker; T_actual measured later)
  latency_ns   = t_obs - t_tx               (TX -> air -> observer chain)
  sync_err_ns  = t_obs - (T_actual + slot_offset)
  (the constant part of latency is a fixed hardware delay; the jitter of
   sync_err is what matters)

--sim replaces the USRP by a software model (clock offset, PSS jitter,
processing delay, fractional TX->RX delay) to test the whole logic offline.

All channels run at --rate (15.36 Msps) so every timestamp is in the same
tick unit. Requires x410_2x2_5g.py in the same folder (5G DSP loader).

Examples:
  python3 x410_pss_timed_tx.py --duration-s 180
  python3 x410_pss_timed_tx.py --sim --duration-s 20
"""
import argparse
import json
import queue
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np


def parse_args():
    p = argparse.ArgumentParser(description="X410 PSS-anchored timed WiFi TX + observer")
    p.add_argument("--args", default="addr=192.168.10.2")
    p.add_argument("--modulos-root", type=Path,
                   default=Path.home() / "syncSense" / "modulos_rx_tx")
    p.add_argument("--out-dir", type=Path, default=None,
                   help="Default: ./pss_timed_tx_<timestamp>")
    p.add_argument("--sim", action="store_true", help="Software model instead of the USRP")
    p.add_argument("--duration-s", type=float, default=180.0,
                   help="Scheduling time after the tracker locks")
    p.add_argument("--rate", type=float, default=15.36e6, help="Rate of ALL channels")
    # WiFi TX / observer
    p.add_argument("--wifi-freq", type=float, default=2.45e9)
    p.add_argument("--tx-gain", type=float, default=20.0)
    p.add_argument("--tx-chans", type=int, nargs=2, default=[0, 1])
    p.add_argument("--amp", type=float, default=0.25)
    p.add_argument("--obs-chan", type=int, default=3)
    p.add_argument("--obs-ant", default="RX1")
    p.add_argument("--obs-gain", type=float, default=20.0)
    p.add_argument("--obs-guard-ms", type=float, default=1.0,
                   help="Observer capture starts this long before t_tx and ends after the burst")
    p.add_argument("--detect-thr", type=float, default=8.0,
                   help="Burst correlation peak over median needed to declare it observed")
    # Burst
    p.add_argument("--zc-len", type=int, default=761, help="Zadoff-Chu length (prime)")
    p.add_argument("--zc-roots", type=int, nargs=2, default=[25, 34], help="Base ZC root per TX chain (pulse k uses base + 37k)")
    p.add_argument("--n-pulses", type=int, default=10)
    p.add_argument("--pulse-spacing-us", type=float, default=200.0)
    # Scheduler
    p.add_argument("--cycle-ssb", type=int, default=5, help="One turn every N SSB (5 = 100 ms)")
    p.add_argument("--lookahead-ssb", type=int, default=5,
                   help="Arm a turn when the latest PSS is this many SSB before it")
    p.add_argument("--slot-offset-ms", type=float, default=5.0)
    p.add_argument("--min-margin-ms", type=float, default=20.0,
                   help="t_tx - now below this at arming time -> MISSED_LATE")
    # Tracker
    p.add_argument("--track-window", type=int, default=50)
    p.add_argument("--track-min-pts", type=int, default=20)
    p.add_argument("--track-max-resid-ns", type=float, default=300.0)
    p.add_argument("--holdover-ms", type=float, default=200.0,
                   help="Latest PSS older than this -> not locked")
    p.add_argument("--outlier-ns", type=float, default=1000.0,
                   help="Reject a PSS this far from the prediction once locked")
    # 5G
    p.add_argument("--nr-chan", type=int, default=2)
    p.add_argument("--nr-ant", default="RX1")
    p.add_argument("--nr-freq", type=float, default=3541.44e6)
    p.add_argument("--nr-gain", type=float, default=32.0)
    p.add_argument("--nr-block-ms", type=float, default=20.0)
    p.add_argument("--nr-overlap-ms", type=float, default=1.0,
                   help="Blocks overlap so an SSB on a block edge is not lost")
    p.add_argument("--force-nid2", type=int, choices=(0, 1, 2), default=0)
    p.add_argument("--force-ibar-ssb", type=int, default=0)
    p.add_argument("--force-ncellid", type=int, default=None)
    p.add_argument("--no-cfo-correction", action="store_true")
    p.add_argument("--cfo-warmup-iters", type=int, default=30)
    p.add_argument("--nr-workers", type=int, default=2)
    a = p.parse_args()
    a.nr_rate = a.rate  # name used by the shared 5G loader
    return a


# --------------------------------------------------------------------------
# Burst: ZC pulses, one root per TX chain, and their detection
# --------------------------------------------------------------------------
class Burst:
    """Every pulse of every chain uses its own ZC root, so the burst template is
    unique: correlating the whole burst has a single peak (no ambiguity of one
    pulse spacing if some pulses are hit by interference)."""

    def __init__(self, args):
        self.rate = args.rate
        self.N = args.zc_len
        self.K = args.n_pulses
        self.S = int(round(args.pulse_spacing_us * 1e-6 * self.rate))
        self.len = (self.K - 1) * self.S + self.N
        n = np.arange(self.N)
        self.roots = [[(args.zc_roots[c] + 37 * k) % self.N or 1 for k in range(self.K)]
                      for c in range(2)]
        flat = [u for r in self.roots for u in r]
        if len(set(flat)) != len(flat):
            raise SystemExit(f"ZC roots not distinct: {self.roots}")
        self.buf = np.zeros((2, self.len), dtype=np.complex64)
        for c in range(2):
            for k, u in enumerate(self.roots[c]):
                self.buf[c, k * self.S:k * self.S + self.N] = (
                    args.amp * np.exp(-1j * np.pi * u * n * (n + 1) / self.N))
        self.templates = [self.buf[c] / args.amp for c in range(2)]
        self.thr = args.detect_thr
        self._fft_cache = {}

    def detect(self, y):
        """Burst start (fractional samples from the start of y) per TX chain."""
        L = self.len
        M = 1 << int(np.ceil(np.log2(len(y) + L)))
        Y = np.fft.fft(y, M)
        out = []
        for c, tpl in enumerate(self.templates):
            key = (c, M)
            if key not in self._fft_cache:
                self._fft_cache[key] = np.conj(np.fft.fft(tpl, M))
            corr = np.abs(np.fft.ifft(Y * self._fft_cache[key]))[:len(y) - L + 1] / (self.K * self.N)
            if len(corr) < 3:
                out.append(dict(found=False, reason="window too short"))
                continue
            i = int(np.argmax(corr))
            ratio = float(corr[i] / max(np.median(corr), 1e-15))
            if ratio < self.thr:
                out.append(dict(found=False, reason=f"peak ratio {ratio:.1f}", comb_ratio=ratio))
                continue
            frac = 0.0
            if 0 < i < len(corr) - 1:
                y0, y1, y2 = corr[i - 1], corr[i], corr[i + 1]
                den = y0 - 2 * y1 + y2
                frac = 0.5 * (y0 - y2) / den if den != 0 else 0.0
            # second-best peak away from the main lobe: how unambiguous the peak is
            lo, hi = max(i - 4, 0), i + 5
            rest = np.concatenate([corr[:lo], corr[hi:]])
            side = float(corr[i] / max(rest.max(), 1e-15)) if len(rest) else np.inf
            out.append(dict(found=True, start=float(i + frac), spread=0.0, comb_ratio=ratio,
                            peak_to_side=side,
                            level_dbfs=float(20 * np.log10(max(corr[i], 1e-15)))))
        return out


# --------------------------------------------------------------------------
# PSS tracker
# --------------------------------------------------------------------------
class Tracker:
    def __init__(self, args):
        self.rate = args.rate
        self.per = int(round(args.rate * args.nr_block_ms * 1e-3))
        self.W, self.min_pts = args.track_window, args.track_min_pts
        self.max_resid = args.track_max_resid_ns
        self.holdover = args.holdover_ms * 1e-3 * args.rate
        self.outlier = args.outlier_ns * 1e-9 * args.rate
        self.ref = None
        self.pts = {}
        self.fit = None  # (k0, t0, slope, intercept, resid_ns, n)
        self.last_tick = None
        self.dups = self.outliers = 0

    def k_of(self, tick):
        if self.ref is None:
            self.ref = tick
        return int(round((tick - self.ref) / self.per))

    def add(self, tick):
        k = self.k_of(tick)
        if k in self.pts:
            self.dups += 1  # same SSB seen in two overlapping blocks
            return None
        if self.fit is not None and abs(tick - self.predict(k)) > self.outlier:
            self.outliers += 1
            return None
        self.pts[k] = tick
        self.last_tick = tick if self.last_tick is None else max(self.last_tick, tick)
        self._refit()
        return k

    def _refit(self):
        ks = sorted(self.pts)[-self.W:]
        if len(ks) < self.min_pts:
            self.fit = None
            return
        k0, t0 = ks[-1], self.pts[ks[-1]]
        x = np.array(ks, dtype=float) - k0
        y = np.array([self.pts[k] for k in ks], dtype=float) - t0
        slope, icpt = np.polyfit(x, y, 1)
        resid = y - (slope * x + icpt)
        self.fit = (k0, t0, slope, icpt, float(np.std(resid) / self.rate * 1e9), len(ks))

    def predict(self, k):
        k0, t0, slope, icpt, _, _ = self.fit
        return t0 + icpt + slope * (k - k0)

    @property
    def k_last(self):
        return max(self.pts) if self.pts else None

    def locked(self, now):
        if self.fit is None:
            return False, "no_fit"
        if self.fit[4] > self.max_resid:
            return False, "resid_high"
        if now - self.last_tick > self.holdover:
            return False, "pss_stale"
        return True, "ok"

    def ppm(self):
        return (self.fit[2] / self.per - 1) * 1e6 if self.fit else None


# --------------------------------------------------------------------------
# Real radio (UHD)
# --------------------------------------------------------------------------
class UhdRadio:
    def __init__(self, args, burst):
        import uhd
        from x410_2x2_5g import load_nr_dsp
        self.uhd, self.args, self.burst = uhd, args, burst
        self.R = args.rate
        (self.nr_args, self.analyze, self.cfo_warmup,
         self.capture) = load_nr_dsp(args.modulos_root, args)
        self.nr_q, self.obs_q = queue.Queue(), queue.Queue()
        self.stop_ev = threading.Event()
        self.async_events = []
        self.tx_q = queue.Queue()
        self.obs_tags = []
        self.obs_lock = threading.Lock()
        self.stats = dict(nr_blocks=0, nr_overflows=0, nr_discontinuities=0, nr_rx_errors=0,
                          nr_dsp_drops=0, obs_errors=0, tx_send_short=0)
        self.setup_report = []
        self._setup()

    def _setup(self):
        uhd, a = self.uhd, self.args
        u = uhd.usrp.MultiUSRP(a.args)
        self.usrp = u
        wt, nt = uhd.types.TuneRequest(a.wifi_freq), uhd.types.TuneRequest(a.nr_freq)
        for ch in a.tx_chans:
            u.set_tx_rate(a.rate, ch)
            u.set_tx_antenna("TX/RX0", ch)
            u.set_tx_gain(a.tx_gain, ch)
            u.set_tx_freq(wt, ch)
        u.set_rx_rate(a.rate, a.obs_chan)
        u.set_rx_antenna(a.obs_ant, a.obs_chan)
        u.set_rx_gain(a.obs_gain, a.obs_chan)
        u.set_rx_freq(wt, a.obs_chan)
        u.set_rx_rate(a.rate, a.nr_chan)
        u.set_rx_antenna(a.nr_ant, a.nr_chan)
        u.set_rx_gain(a.nr_gain, a.nr_chan)
        u.set_rx_freq(nt, a.nr_chan)
        time.sleep(0.5)
        print("\n=== Setup ===")
        checks = [("TX", ch, a.wifi_freq) for ch in a.tx_chans]
        checks += [("RX", a.obs_chan, a.wifi_freq), ("RX", a.nr_chan, a.nr_freq)]
        for kind, ch, f_req in checks:
            g = (u.get_tx_freq, u.get_tx_rate, u.get_tx_sensor) if kind == "TX" else \
                (u.get_rx_freq, u.get_rx_rate, u.get_rx_sensor)
            f, r, lock = g[0](ch), g[1](ch), g[2]("lo_locked", ch).to_bool()
            ok = lock and abs(f - f_req) < 1e3 and abs(r - a.rate) < 1.0
            print(f"{kind} ch{ch}: {f/1e6:10.4f} MHz {r/1e6:7.3f} Msps LO locked={lock} "
                  f"{'OK' if ok else 'FAIL'}")
            self.setup_report.append(dict(kind=kind, chan=ch, freq=f, rate=r, lo_locked=lock, ok=ok))
            if not ok:
                raise RuntimeError(f"{kind} ch{ch} not locked, mistuned or rate coerced")
        sa = uhd.usrp.StreamArgs("fc32", "sc16")
        sa.channels = list(a.tx_chans)
        self.tx = u.get_tx_stream(sa)
        sa = uhd.usrp.StreamArgs("fc32", "sc16")
        sa.channels = [a.obs_chan]
        self.obs = u.get_rx_stream(sa)
        sa = uhd.usrp.StreamArgs("fc32", "sc16")
        sa.channels = [a.nr_chan]
        self.nr = u.get_rx_stream(sa)

    def now_ticks(self):
        return self.usrp.get_time_now().to_ticks(self.R)

    def _ts(self, ticks):
        return self.uhd.types.TimeSpec.from_ticks(int(ticks), self.R)

    # ---- 5G lock + continuous stream ----
    def acquire_nr(self):
        a = self.args
        blk_n = int(round(self.R * a.nr_block_ms * 1e-3))
        mx = self.nr.get_max_num_samps()
        self.cfo_hz = 0.0
        print("\n=== 5G lock ===")
        if self.nr_args.enable_cfo_correction:
            self.cfo_hz, _ = self.cfo_warmup(rx_streamer=self.nr, samples_per_block=blk_n,
                                             max_samps=mx, sample_rate=self.R, args=self.nr_args)
        self.ncellid, self.ibar = a.force_ncellid, a.force_ibar_ssb
        for attempt in range(1, 21):
            b = self.capture(rx_streamer=self.nr, total_samples=blk_n, max_samps=mx,
                             sample_rate_hz=self.R)
            try:
                res = self.analyze(b.waveform, self.nr_args, self.cfo_hz, self.R,
                                   self.ncellid, self.ibar)
            except ValueError as e:
                print(f"  lock attempt {attempt}: {e}")
                continue
            self.ncellid, self.ibar = res["estimate"].ncellid, res["estimate"].ibar_ssb
            print(f"5G locked: NCellID={self.ncellid} ibar_ssb={self.ibar} CFO={self.cfo_hz:+.1f} Hz "
                  f"PSS={res['pss_metric']:.3f}")
            return dict(ncellid=self.ncellid, ibar_ssb=self.ibar, cfo_hz=self.cfo_hz)
        raise RuntimeError("Could not lock the 5G cell in 20 attempts")

    def _nr_thread(self):
        uhd, a = self.uhd, self.args
        N = int(round(self.R * a.nr_block_ms * 1e-3))
        ov = int(round(self.R * a.nr_overlap_ms * 1e-3))
        L = N + ov
        mx = self.nr.get_max_num_samps()
        buf = np.empty(mx, dtype=np.complex64)
        blk = np.empty(L, dtype=np.complex64)
        fill, blk_tick, expected = 0, None, None
        md = uhd.types.RXMetadata()
        pending = []
        cmd = uhd.types.StreamCMD(uhd.types.StreamMode.start_cont)
        cmd.stream_now = True
        self.nr.issue_stream_cmd(cmd)
        E = uhd.types.RXMetadataErrorCode
        with ThreadPoolExecutor(max_workers=a.nr_workers) as pool:
            while not self.stop_ev.is_set():
                n = self.nr.recv(buf, md, 0.5)
                if md.error_code == E.overflow:
                    self.stats["nr_overflows"] += 1
                    fill, expected = 0, None
                    continue
                if md.error_code != E.none:
                    if md.error_code != E.timeout:
                        self.stats["nr_rx_errors"] += 1
                    continue
                if n == 0:
                    continue
                tick = md.time_spec.to_ticks(self.R) if md.has_time_spec else expected
                if expected is not None and tick != expected:
                    self.stats["nr_discontinuities"] += 1
                    fill = 0
                expected = tick + n
                i = 0
                while i < n:
                    if fill == 0:
                        blk_tick = tick + i
                    k = min(n - i, L - fill)
                    blk[fill:fill + k] = buf[i:i + k]
                    fill += k
                    i += k
                    if fill == L:
                        self.stats["nr_blocks"] += 1
                        pending = [f for f in pending if not f.done()]
                        if len(pending) >= 3 * a.nr_workers:
                            self.stats["nr_dsp_drops"] += 1
                        else:
                            f = pool.submit(self._analyze_block, blk.copy(), blk_tick)
                            pending.append(f)
                        blk[:ov] = blk[N:L]
                        fill, blk_tick = ov, blk_tick + N
            self.nr.issue_stream_cmd(uhd.types.StreamCMD(uhd.types.StreamMode.stop_cont))

    def _analyze_block(self, wf, blk_tick):
        row = dict(block_start_device_ticks=int(blk_tick),
                   peak_dbfs=round(float(20 * np.log10(max(np.max(np.abs(wf)), 1e-12))), 2))
        try:
            res = self.analyze(wf, self.nr_args, self.cfo_hz, self.R, self.ncellid, self.ibar)
            off = int(res["timing"]["timing_offset_samples"])
            row.update(valid=True, detector_offset_samples=off,
                       event_device_ticks=int(blk_tick) + off,
                       pss_metric=round(res["pss_metric"], 4),
                       sss_metric=round(float(res["estimate"].sss_metric), 4))
        except Exception as e:
            row.update(valid=False, error=str(e)[:160])
        self.nr_q.put(row)

    # ---- TX ----
    def _tx_thread(self):
        uhd = self.uhd
        while not self.stop_ev.is_set():
            try:
                k, t_tx, rec = self.tx_q.get(timeout=0.2)
            except queue.Empty:
                continue
            md = uhd.types.TXMetadata()
            md.start_of_burst = md.end_of_burst = md.has_time_spec = True
            md.time_spec = self._ts(t_tx)
            rec["send_start_now_ticks"] = int(self.now_ticks())
            sent = self.tx.send(self.burst.buf, md, 1.0)
            rec["send_done_now_ticks"] = int(self.now_ticks())
            if sent != self.burst.len:
                self.stats["tx_send_short"] += 1
                rec["tx_send_short"] = True

    def _async_thread(self):
        uhd = self.uhd
        md = uhd.types.TXAsyncMetadata()
        while not self.stop_ev.is_set():
            if self.tx.recv_async_msg(md, 0.1):
                self.async_events.append(dict(
                    event=str(md.event_code).split(".")[-1],
                    tick=int(md.time_spec.to_ticks(self.R)) if md.has_time_spec else None,
                    host=time.time()))

    def send_burst(self, k, t_tx, rec):
        self.tx_q.put((k, t_tx, rec))

    # ---- Observer ----
    def arm_observer(self, k, t_start, n):
        uhd = self.uhd
        cmd = uhd.types.StreamCMD(uhd.types.StreamMode.num_done)
        cmd.num_samps = int(n)
        cmd.stream_now = False
        cmd.time_spec = self._ts(t_start)
        with self.obs_lock:
            self.obs_tags.append((k, int(n)))
        self.obs.issue_stream_cmd(cmd)

    def _obs_thread(self):
        uhd = self.uhd
        E = uhd.types.RXMetadataErrorCode
        mx = self.obs.get_max_num_samps()
        buf = np.empty(mx, dtype=np.complex64)
        md = uhd.types.RXMetadata()
        cur, w, fill, w0 = None, None, 0, None
        while not self.stop_ev.is_set():
            if cur is None:
                with self.obs_lock:
                    if self.obs_tags:
                        cur = self.obs_tags.pop(0)
                        w, fill, w0 = np.empty(cur[1], dtype=np.complex64), 0, None
                if cur is None:
                    time.sleep(0.002)
                    continue
            n = self.obs.recv(buf, md, 0.2)
            if md.error_code == E.timeout:
                continue
            if md.error_code != E.none:
                self.stats["obs_errors"] += 1
                self.obs_q.put(dict(k=cur[0], error=str(md.error_code).split(".")[-1]))
                cur = None
                continue
            if n == 0:
                continue
            if w0 is None:
                w0 = md.time_spec.to_ticks(self.R)
            k = min(n, len(w) - fill)
            w[fill:fill + k] = buf[:k]
            fill += k
            if fill == len(w):
                self.obs_q.put(dict(k=cur[0], w0=int(w0), y=w))
                cur = None

    def start(self):
        self.threads = [threading.Thread(target=f, daemon=True) for f in
                        (self._nr_thread, self._tx_thread, self._async_thread, self._obs_thread)]
        for t in self.threads:
            t.start()

    def stop(self):
        self.stop_ev.set()
        for t in self.threads:
            t.join(timeout=3)


# --------------------------------------------------------------------------
# Simulated radio (same interface, no hardware)
# --------------------------------------------------------------------------
class SimRadio:
    """Device clock = host monotonic; the gNB SSB period is off by --sim-ppm;
    PSS timestamps carry Gaussian jitter and arrive after a processing delay;
    the observer sees the burst after a fractional TX->RX latency."""
    PPM, JITTER_NS, DELAY_MS, INVALID, LATENCY_NS, SNR_DB = -0.151, 27.0, 40.0, 0.01, 3100.0, 25.0

    def __init__(self, args, burst):
        self.args, self.burst, self.R = args, burst, args.rate
        self.nr_q, self.obs_q = queue.Queue(), queue.Queue()
        self.stop_ev = threading.Event()
        self.async_events = []
        self.t0 = time.monotonic()
        self.base = 51_000_000
        self.bursts = []
        self.lock = threading.Lock()
        self.obs_pending = []
        self.rng = np.random.default_rng(1)
        self.stats = dict(nr_blocks=0, nr_overflows=0, nr_discontinuities=0, nr_rx_errors=0,
                          nr_dsp_drops=0, obs_errors=0, tx_send_short=0)
        self.setup_report = [dict(sim=True)]

    def now_ticks(self):
        return int(self.base + (time.monotonic() - self.t0) * self.R)

    def acquire_nr(self):
        print("\n=== 5G lock (sim) ===")
        return dict(ncellid=0, ibar_ssb=0, cfo_hz=0.0, sim=True)

    def _nr_thread(self):
        per = self.R * self.args.nr_block_ms * 1e-3 * (1 + self.PPM * 1e-6)
        first = self.now_ticks() + 0.003 * self.R
        k = 0
        while not self.stop_ev.is_set():
            T = first + k * per
            deliver = T + (self.DELAY_MS + self.rng.uniform(-5, 5)) * 1e-3 * self.R
            while self.now_ticks() < deliver and not self.stop_ev.is_set():
                time.sleep(0.001)
            self.stats["nr_blocks"] += 1
            if self.rng.random() < self.INVALID:
                self.nr_q.put(dict(valid=False, error="sim invalid"))
            else:
                meas = int(round(T + self.rng.normal(0, self.JITTER_NS * 1e-9 * self.R)))
                self.nr_q.put(dict(valid=True, event_device_ticks=meas, pss_metric=0.95,
                                   sss_metric=0.98, sim_true_ticks=float(T)))
            k += 1

    def send_burst(self, k, t_tx, rec):
        now = self.now_ticks()
        rec["send_start_now_ticks"] = rec["send_done_now_ticks"] = now
        if t_tx < now:
            self.async_events.append(dict(event="time_error", tick=None, host=time.time()))
            return
        with self.lock:
            self.bursts.append(t_tx)
        self.async_events.append(dict(event="burst_ack", tick=None, host=time.time()))

    def arm_observer(self, k, t_start, n):
        with self.lock:
            self.obs_pending.append((k, int(t_start), int(n)))

    def _obs_thread(self):
        lat = self.LATENCY_NS * 1e-9 * self.R
        g = np.array([10 ** (-60 / 20) * np.exp(1j * 0.7), 10 ** (-63 / 20) * np.exp(-1j * 1.9)])
        while not self.stop_ev.is_set():
            with self.lock:
                ready = [p for p in self.obs_pending if self.now_ticks() > p[1] + p[2]]
                self.obs_pending = [p for p in self.obs_pending if p not in ready]
                bursts = list(self.bursts)
            for k, t_start, n in ready:
                noise_amp = np.abs(g[0]) * self.args.amp * 10 ** (-self.SNR_DB / 20)
                y = (noise_amp / np.sqrt(2) * (self.rng.standard_normal(n)
                                               + 1j * self.rng.standard_normal(n))).astype(np.complex64)
                for t_tx in bursts:
                    d = t_tx + lat - t_start
                    if -self.burst.len < d < n:
                        sig = (g[:, None] * self.burst.buf).sum(0)
                        M = 1 << int(np.ceil(np.log2(n + self.burst.len)))
                        f = np.fft.fftfreq(M)
                        x = np.zeros(M, dtype=complex)
                        x[:self.burst.len] = sig
                        x = np.fft.ifft(np.fft.fft(x) * np.exp(-2j * np.pi * f * d))[:n]
                        y += x.astype(np.complex64)
                self.obs_q.put(dict(k=k, w0=t_start, y=y))
            time.sleep(0.002)

    def start(self):
        self.threads = [threading.Thread(target=f, daemon=True)
                        for f in (self._nr_thread, self._obs_thread)]
        for t in self.threads:
            t.start()

    def stop(self):
        self.stop_ev.set()
        for t in self.threads:
            t.join(timeout=3)


# --------------------------------------------------------------------------
def stats_ns(x):
    x = np.asarray([v for v in x if v is not None], dtype=float)
    if len(x) == 0:
        return None
    return dict(n=int(len(x)), mean=round(float(x.mean()), 1), std=round(float(x.std()), 1),
                p99_abs_dev=round(float(np.percentile(np.abs(x - x.mean()), 99)), 1),
                max_abs_dev=round(float(np.max(np.abs(x - x.mean()))), 1))


def main():
    args = parse_args()
    if not args.sim:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
    out_dir = args.out_dir or Path(f"pss_timed_tx_{time.strftime('%Y%m%d_%H%M%S')}")
    out_dir.mkdir(parents=True, exist_ok=True)
    R = args.rate
    burst = Burst(args)
    trk = Tracker(args)
    radio = SimRadio(args, burst) if args.sim else UhdRadio(args, burst)
    summary = dict(args={k: (str(v) if isinstance(v, Path) else v) for k, v in vars(args).items()},
                   setup=radio.setup_report, started_unix=time.time(),
                   burst=dict(len_samples=burst.len, len_us=burst.len / R * 1e6,
                              spacing_samples=burst.S, pulse_samples=burst.N))
    print(f"Burst: {burst.K} ZC pulses (N={burst.N}, {burst.N/R*1e6:.1f} us) every "
          f"{burst.S/R*1e6:.0f} us -> {burst.len/R*1e3:.3f} ms | turn every {args.cycle_ssb} SSB, "
          f"T+{args.slot_offset_ms} ms, armed {args.lookahead_ssb} SSB ahead")
    summary["nr_lock"] = radio.acquire_nr()

    slot_off = args.slot_offset_ms * 1e-3 * R
    guard = int(round(args.obs_guard_ms * 1e-3 * R))
    n_obs = burst.len + 2 * guard
    min_margin = args.min_margin_ms * 1e-3 * R
    C, L = args.cycle_ssb, args.lookahead_ssb

    nr_f = open(out_dir / "nr_events.jsonl", "w")
    turns = {}
    k_actual = {}
    next_target = None
    t_sched_end = None
    last_print = time.monotonic()
    state = "SEARCHING"
    radio.start()
    t_start_mono = time.monotonic()
    print("\n=== Running (Ctrl+C to stop early) ===")
    try:
        while True:
            # --- 5G events -> tracker
            while True:
                try:
                    row = radio.nr_q.get_nowait()
                except queue.Empty:
                    break
                if row.get("valid"):
                    k = trk.add(row["event_device_ticks"])
                    row["k"] = k
                    if k is not None:
                        k_actual[k] = row["event_device_ticks"]
                        if k in turns:
                            turns[k]["T_actual"] = row["event_device_ticks"]
                nr_f.write(json.dumps(row) + "\n")
            now = radio.now_ticks()
            locked, why = trk.locked(now)
            if state == "SEARCHING" and trk.pts:
                state = "TRACKING"
                print(f"[{state}] first PSS received")
            if state == "TRACKING" and locked:
                state = "LOCKED"
                t_sched_end = time.monotonic() + args.duration_s
                print(f"[{state}] {trk.fit[5]} PSS, period {trk.fit[2]:.3f} ticks "
                      f"({trk.ppm():+.3f} ppm), resid {trk.fit[4]:.1f} ns -> scheduling "
                      f"{args.duration_s:.0f} s")
                next_target = ((trk.k_last + L + C - 1) // C) * C

            # --- scheduler
            if next_target is not None:
                kl = trk.k_last
                while next_target <= kl:  # passed without being armed
                    turns[next_target] = dict(k=next_target, state="MISSED_NOT_ARMED")
                    next_target += C
                while next_target <= kl + L:
                    k = next_target
                    now = radio.now_ticks()
                    locked, why = trk.locked(now)
                    rec = dict(k=k, now_arm=int(now), k_last=kl, horizon_ssb=k - kl)
                    if trk.fit is not None:
                        T = trk.predict(k)
                        t_tx = int(round(T + slot_off))
                        rec.update(T_pred=float(T), t_tx=t_tx, margin_ms=(t_tx - now) / R * 1e3,
                                   track_n=trk.fit[5], track_resid_ns=trk.fit[4],
                                   track_ppm=trk.ppm())
                    if not locked:
                        rec["state"] = f"MISSED_NO_LOCK:{why}"
                    elif t_tx - now < min_margin:
                        rec["state"] = "MISSED_LATE"
                    else:
                        rec["state"] = "ARMED"
                        radio.arm_observer(k, t_tx - guard, n_obs)
                        radio.send_burst(k, t_tx, rec)
                    if k in k_actual:
                        rec["T_actual"] = k_actual[k]
                    turns[k] = rec
                    next_target += C

            # --- observer windows
            while True:
                try:
                    ob = radio.obs_q.get_nowait()
                except queue.Empty:
                    break
                rec = turns.get(ob["k"])
                if rec is None:
                    continue
                if "error" in ob:
                    rec["obs_error"] = ob["error"]
                    continue
                det = burst.detect(ob["y"])
                rec["obs"] = []
                for c, d in enumerate(det):
                    if d.get("found"):
                        d["t_obs"] = ob["w0"] + d["start"]
                    rec["obs"].append(d)

            if time.monotonic() - last_print > 5 and turns:
                last_print = time.monotonic()
                ex = [r for r in turns.values() if r.get("state") == "ARMED"]
                ms = [r for r in turns.values() if str(r.get("state", "")).startswith("MISSED")]
                lat = [r["obs"][0]["t_obs"] - r["t_tx"] for r in ex
                       if r.get("obs") and r["obs"][0].get("found")]
                se = [r["obs"][0]["t_obs"] - (r["T_actual"] + slot_off) for r in ex
                      if r.get("obs") and r["obs"][0].get("found") and "T_actual" in r]
                msg = (f"  turns {len(turns)} | armed {len(ex)} missed {len(ms)} | "
                       f"observed {len(lat)}")
                if lat:
                    msg += f" | latency {np.mean(lat)/R*1e9:.0f} ns"
                if len(se) > 1:
                    msg += f" | sync err std {np.std(se)/R*1e9:.1f} ns"
                if trk.fit:
                    msg += f" | tracker {trk.ppm():+.3f} ppm resid {trk.fit[4]:.0f} ns"
                late = sum(1 for e in radio.async_events if e["event"] != "burst_ack")
                msg += f" | async non-ack {late} | 5G ovf {radio.stats['nr_overflows']}"
                print(msg)

            if t_sched_end is not None and time.monotonic() > t_sched_end:
                break
            if t_sched_end is None and time.monotonic() - t_start_mono > 60:
                raise RuntimeError(f"Tracker did not lock in 60 s ({why})")
            time.sleep(0.002)
        # let the last turns finish (burst, observer window and T_actual)
        t_wait = time.monotonic() + 0.5
        while time.monotonic() < t_wait:
            try:
                ob = radio.obs_q.get(timeout=0.05)
            except queue.Empty:
                continue
            rec = turns.get(ob["k"])
            if rec is not None and "error" not in ob:
                det = burst.detect(ob["y"])
                for d in det:
                    if d.get("found"):
                        d["t_obs"] = ob["w0"] + d["start"]
                rec["obs"] = det
    except KeyboardInterrupt:
        print("\nInterrupted: writing results")
    finally:
        radio.stop()
        while not radio.nr_q.empty():
            row = radio.nr_q.get()
            if row.get("valid"):
                k = trk.k_of(row["event_device_ticks"])
                k_actual.setdefault(k, row["event_device_ticks"])
            nr_f.write(json.dumps(row) + "\n")
        nr_f.close()

    # ---------------- results per turn ----------------
    for k, rec in turns.items():
        if "T_actual" not in rec and k in k_actual:
            rec["T_actual"] = k_actual[k]
        if "T_pred" in rec and "T_actual" in rec:
            rec["pred_err_ns"] = (rec["T_pred"] - rec["T_actual"]) / R * 1e9
        for c, d in enumerate(rec.get("obs", [])):
            if d.get("found"):
                d["latency_ns"] = (d["t_obs"] - rec["t_tx"]) / R * 1e9
                if "T_actual" in rec:
                    d["sync_err_ns"] = (d["t_obs"] - rec["T_actual"] - slot_off) / R * 1e9
        if rec.get("state") == "ARMED":
            obs_ok = any(d.get("found") for d in rec.get("obs", []))
            rec["state"] = "EXECUTED_OBSERVED" if obs_ok else "EXECUTED_NOT_OBSERVED"
    with open(out_dir / "turns.jsonl", "w") as f:
        for k in sorted(turns):
            f.write(json.dumps(turns[k], default=float) + "\n")

    states = {}
    for r in turns.values():
        s = str(r["state"]).split(":")[0]
        states[s] = states.get(s, 0) + 1
    ex = [r for r in turns.values() if str(r["state"]).startswith("EXECUTED")]
    ev = {}
    for e in radio.async_events:
        ev[e["event"]] = ev.get(e["event"], 0) + 1
    res = dict(turns=len(turns), states=states, async_events=ev, radio_stats=radio.stats,
               tracker=dict(ppm=trk.ppm(), resid_ns=trk.fit[4] if trk.fit else None,
                            pss_points=len(trk.pts), duplicates=trk.dups, outliers=trk.outliers),
               margin_ms=stats_ns([r.get("margin_ms") for r in ex]),
               pred_err_ns=stats_ns([r.get("pred_err_ns") for r in ex]))
    for c in range(2):
        res[f"chain{c}"] = dict(
            observed=sum(1 for r in ex if len(r.get("obs", [])) > c and r["obs"][c].get("found")),
            latency_ns=stats_ns([r["obs"][c].get("latency_ns") for r in ex if len(r.get("obs", [])) > c]),
            sync_err_ns=stats_ns([r["obs"][c].get("sync_err_ns") for r in ex if len(r.get("obs", [])) > c]),
            pulse_spread_samples=stats_ns([r["obs"][c].get("spread") for r in ex if len(r.get("obs", [])) > c]))
    summary["results"] = res
    summary["finished_unix"] = time.time()
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, default=float))

    print("\n=== Summary ===")
    print(f"Turns {len(turns)}: " + ", ".join(f"{k} {v}" for k, v in sorted(states.items())))
    print(f"TX async events: {ev} | radio: {radio.stats}")
    print(f"Tracker: {res['tracker']}")
    print(f"Arming margin [ms]: {res['margin_ms']}")
    print(f"Prediction error T_pred - T_actual [ns]: {res['pred_err_ns']}")
    for c in range(2):
        r = res[f"chain{c}"]
        print(f"Chain {c} (TX ch{args.tx_chans[c]}): observed {r['observed']}/{len(ex)}")
        print(f"   latency t_obs - t_tx [ns]:            {r['latency_ns']}")
        print(f"   sync error t_obs - (T_actual+off) [ns]: {r['sync_err_ns']}")
    late_bad = states.get("MISSED_LATE", 0) + ev.get("time_error", 0) + ev.get("underflow", 0)
    s0 = res["chain0"]["sync_err_ns"]
    ok = (late_bad == 0 and ex and res["chain0"]["observed"] >= 0.99 * len(ex)
          and s0 is not None and s0["std"] < 200)
    print(f"\nVerdict: {'PASS' if ok else 'CHECK'} (no late/underflow, >=99 % observed, "
          f"sync error std < 200 ns)")
    print(f"Saved {out_dir}/ (summary.json, turns.jsonl, nr_events.jsonl)")


if __name__ == "__main__":
    main()
