#!/usr/bin/env python3
"""
X410 bench: WiFi bursts on N TX chains scheduled from the 5G PSS, received
by M WiFi RX chains of the same X410 (one timekeeper, no second AP yet).

Default channel plan (4 TX x 3 RX + 5G):
  ch0  TX WiFi (TX/RX0)  + RX WiFi A (RX1)     DB0
  ch1  TX WiFi (TX/RX0)  + RX WiFi B (RX1)     DB0
  ch2  TX WiFi (TX/RX0)  + RX 5G     (RX1)     DB1   <- TX 2.45 GHz / RX 3.54 GHz
  ch3  TX WiFi (TX/RX0)  + RX WiFi C (RX1)     DB1

  5G      continuous RX -> PSS start in X410 ticks
  tracker linear fit tick = a + b*k over the last PSS -> predicts future PSS
  sched   every --cycle-ssb SSB, armed --lookahead-ssb SSB ahead, a timed
          burst at T_pred + slot offset on all TX chains (10 Zadoff-Chu
          pulses every 200 us, a distinct root per pulse and chain)
          late or not locked -> MISSED, nothing is sent
  RX      timed capture around every burst on the WiFi RX chains; the burst of
          every TX chain is found by correlation -> arrival time and complex
          gain of every TX->RX link, every turn

Phases (after the 5G lock):
  A  5G only (no TX)                      --nr-only-s
  B  scheduled, burst at T + 5 ms         --duration-s     (never overlaps the SSB)
  C  scheduled, burst covering the SSB    --overlap-s      (worst-case coexistence
                                                            of the ch2 TX with the 5G RX)

Per turn / link:
  pred_err_ns = T_pred - T_actual              (tracker)
  latency_ns  = t_obs - t_tx                   (TX -> air -> RX, constant part = hardware)
  sync_err_ns = t_obs - (T_actual + offset)
  H           complex gain -> phase stability of every link over the run
Skew between chains/daughterboards = differences of the mean latency per link
(includes < ~0.2 ns of geometry for arrays 1 m apart near broadside).

--sim replaces the USRP by a software model to test the logic offline.
Requires x410_2x2_5g.py in the same folder (5G DSP loader) for hardware runs.

Examples:
  python3 x410_pss_timed_tx.py --duration-s 180
  python3 x410_pss_timed_tx.py --tx-chans 0 1 --rx-chans 3 --overlap-s 0
  python3 x410_pss_timed_tx.py --sim --duration-s 20
"""
import argparse
import bisect
import json
import queue
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

try:
    from scipy.fft import next_fast_len
except ImportError:  # pragma: no cover
    def next_fast_len(n):
        return 1 << int(np.ceil(np.log2(n)))


def parse_args():
    p = argparse.ArgumentParser(description="X410 PSS-anchored timed WiFi TX (N TX x M RX)")
    p.add_argument("--args", default="addr=192.168.10.2")
    p.add_argument("--modulos-root", type=Path,
                   default=Path.home() / "syncSense" / "modulos_rx_tx")
    p.add_argument("--out-dir", type=Path, default=None,
                   help="Default: ./pss_timed_tx_<timestamp>")
    p.add_argument("--sim", action="store_true", help="Software model instead of the USRP")
    p.add_argument("--rate", type=float, default=15.36e6, help="Rate of ALL channels")
    # Phases
    p.add_argument("--nr-only-s", type=float, default=20.0, help="Phase A: 5G only, no TX")
    p.add_argument("--duration-s", type=float, default=180.0, help="Phase B: scheduled at T+offset")
    p.add_argument("--overlap-s", type=float, default=30.0,
                   help="Phase C: scheduled so the burst covers the SSB (0 = skip)")
    p.add_argument("--overlap-offset-ms", type=float, default=-0.5,
                   help="Burst start relative to the PSS in phase C")
    # WiFi TX / RX
    p.add_argument("--wifi-freq", type=float, default=2.45e9)
    p.add_argument("--tx-gain", type=float, default=20.0)
    p.add_argument("--tx-chans", type=int, nargs="+", default=[0, 1, 2, 3])
    p.add_argument("--amp", type=float, default=0.25, help="Digital amplitude per TX chain")
    p.add_argument("--rx-chans", type=int, nargs="+", default=[0, 1, 3],
                   help="WiFi RX chains that capture every burst")
    p.add_argument("--rx-ant", default="RX1")
    p.add_argument("--rx-gain", type=float, default=20.0)
    p.add_argument("--rx-guard-ms", type=float, default=0.25,
                   help="RX capture starts this long before t_tx and ends after the burst")
    p.add_argument("--detect-thr", type=float, default=8.0,
                   help="Burst correlation peak over median needed to declare it observed")
    p.add_argument("--min-peak-to-side", type=float, default=1.5,
                   help="Main peak over the highest peak elsewhere; below -> ambiguous, not observed")
    # Burst
    p.add_argument("--zc-len", type=int, default=761, help="Zadoff-Chu length (prime)")
    p.add_argument("--zc-roots", type=int, nargs="+", default=None,
                   help="Base root per TX chain (default 25, 34, 43, ...; pulse k uses base + 37k)")
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
    if a.zc_roots is None:
        a.zc_roots = [25 + 9 * i for i in range(len(a.tx_chans))]
    if len(a.zc_roots) != len(a.tx_chans):
        p.error("--zc-roots needs one value per TX chain")
    if a.nr_chan in a.rx_chans:
        p.error("the 5G channel cannot also be a WiFi RX channel")
    return a


# --------------------------------------------------------------------------
# Burst: ZC pulses with a distinct root per pulse and chain, and detection
# --------------------------------------------------------------------------
class Burst:
    """Every pulse of every chain uses its own ZC root, so each chain's burst
    template is unique: one correlation peak, no ambiguity of one pulse spacing
    when some pulses are hit by interference."""

    def __init__(self, args):
        self.rate = args.rate
        self.N = args.zc_len
        self.K = args.n_pulses
        self.S = int(round(args.pulse_spacing_us * 1e-6 * self.rate))
        self.len = (self.K - 1) * self.S + self.N
        self.n_tx = len(args.tx_chans)
        n = np.arange(self.N)
        self.roots = [[(b + 37 * k) % self.N or 1 for k in range(self.K)] for b in args.zc_roots]
        flat = [u for r in self.roots for u in r]
        if len(set(flat)) != len(flat):
            raise SystemExit(f"ZC roots not distinct: {self.roots}")
        self.buf = np.zeros((self.n_tx, self.len), dtype=np.complex64)
        for c in range(self.n_tx):
            for k, u in enumerate(self.roots[c]):
                self.buf[c, k * self.S:k * self.S + self.N] = (
                    args.amp * np.exp(-1j * np.pi * u * n * (n + 1) / self.N))
        self.templates = [self.buf[c] / args.amp for c in range(self.n_tx)]
        self.thr = args.detect_thr
        self.min_side = args.min_peak_to_side
        self._fft_cache = {}

    @staticmethod
    def _frac_delay(cc, i, half=128):
        """Sub-sample refinement of the correlation peak at integer lag i.
        The correlation of band-limited signals is itself band-limited, so its
        value at a fractional lag is the sinc interpolation of the integer lags
        around the peak; |corr(tau)| is maximised on a grid that is then zoomed.
        (A parabola on |corr| is not used: for a full-band ZC it is almost blind
        near integer delays.)"""
        lo, hi = max(i - half, 0), min(i + half + 1, len(cc))
        n = np.arange(lo, hi)
        v = cc[lo:hi]
        tau = i + np.linspace(-1.0, 1.0, 41)
        for step in (1 / 20, 1 / 400, 1 / 8000):
            mag = np.abs(np.sinc(tau[:, None] - n[None, :]) @ v)
            t0 = tau[int(np.argmax(mag))]
            tau = t0 + np.arange(-20, 21) * step / 20
        return float(t0 - i)

    def detect(self, y):
        """For one RX window: per TX chain, burst start (fractional samples from
        the start of y) and complex gain."""
        L = self.len
        n_lags = len(y) - L + 1
        if n_lags < 3:
            return [dict(found=False, reason="window too short")] * self.n_tx
        M = next_fast_len(len(y))  # circular corr is exact for lags 0..len(y)-L
        Y = np.fft.fft(y, M)
        out = []
        for c, tpl in enumerate(self.templates):
            key = (c, M)
            if key not in self._fft_cache:
                self._fft_cache[key] = np.conj(np.fft.fft(tpl, M))
            cc = np.fft.ifft(Y * self._fft_cache[key])[:n_lags] / (self.K * self.N)
            corr = np.abs(cc)
            i = int(np.argmax(corr))
            ratio = float(corr[i] / max(np.median(corr), 1e-15))
            if ratio < self.thr:
                out.append(dict(found=False, reason=f"peak ratio {ratio:.1f}", peak_ratio=ratio))
                continue
            frac = self._frac_delay(cc, i)
            rest = np.concatenate([corr[:max(i - 4, 0)], corr[i + 5:]])
            side = float(corr[i] / max(rest.max(), 1e-15)) if len(rest) else float("inf")
            if side < self.min_side:
                out.append(dict(found=False, reason=f"ambiguous peak (main/side {side:.2f})",
                                peak_ratio=ratio, peak_to_side=side))
                continue
            h = complex(cc[i])
            out.append(dict(found=True, start=float(i + frac), peak_ratio=ratio,
                            peak_to_side=side, h=[h.real, h.imag],
                            level_dbfs=float(20 * np.log10(max(abs(h), 1e-15)))))
        return out


# --------------------------------------------------------------------------
# PSS tracker
# --------------------------------------------------------------------------
class Tracker:
    def __init__(self, args):
        tr = getattr(args, "tick_rate", args.rate)    # time unit of the ticks (may differ from the 5G rate)
        self.rate = tr
        self.per = int(round(tr * args.nr_block_ms * 1e-3))
        self.W, self.min_pts = args.track_window, args.track_min_pts
        self.max_resid = args.track_max_resid_ns
        self.holdover = args.holdover_ms * 1e-3 * tr
        self.outlier = args.outlier_ns * 1e-9 * tr
        self.ref = None
        self.pts = {}
        self.recent = []  # sorted k of the last W accepted PSS (keeps refit O(W))
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
        bisect.insort(self.recent, k)
        if len(self.recent) > self.W:
            self.recent.pop(0)
        self.last_tick = tick if self.last_tick is None else max(self.last_tick, tick)
        self._refit()
        return k

    def _refit(self):
        ks = self.recent
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
        return self.recent[-1] if self.recent else None

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


def _new_stats():
    return dict(nr_blocks=0, nr_overflows=0, nr_discontinuities=0, nr_rx_errors=0,
                nr_dsp_drops=0, rx_errors=0, tx_send_short=0)


# --------------------------------------------------------------------------
# Real radio (UHD)
# --------------------------------------------------------------------------
class UhdRadio:
    def __init__(self, args, burst):
        import uhd
        from x410_2x2_5g import load_nr_dsp
        self.uhd, self.args, self.burst = uhd, args, burst
        # ticks (time_spec unit), 5G sample rate and WiFi sample rate can differ;
        # by default all equal --rate (old behaviour)
        self.R = getattr(args, "tick_rate", args.rate)
        self.Rnr = args.nr_rate
        self.Rw = getattr(args, "wifi_rate", args.rate)
        self.tpn = self.R / self.Rnr
        if abs(self.tpn - round(self.tpn)) > 1e-9:
            raise SystemExit("tick rate must be an integer multiple of the 5G rate")
        self.tpn = int(round(self.tpn))
        self.tpw = int(round(self.R / self.Rw))      # ticks per WiFi sample
        (self.nr_args, self.analyze, self.cfo_warmup,
         self.capture) = load_nr_dsp(args.modulos_root, args)
        self.nr_q, self.obs_q = queue.Queue(), queue.Queue()
        self.stop_ev = threading.Event()
        self.async_events = []
        self.tx_q = queue.Queue()
        self.obs_tags = []
        self.obs_lock = threading.Lock()
        self.stats = _new_stats()
        self.phase = "lock"
        self.setup_report = []
        self._setup()

    def _setup(self):
        uhd, a = self.uhd, self.args
        u = uhd.usrp.MultiUSRP(a.args)
        self.usrp = u
        wt, nt = uhd.types.TuneRequest(a.wifi_freq), uhd.types.TuneRequest(a.nr_freq)
        for ch in a.tx_chans:
            u.set_tx_rate(self.Rw, ch)
            u.set_tx_antenna("TX/RX0", ch)
            u.set_tx_gain(a.tx_gain, ch)
            u.set_tx_freq(wt, ch)
        for ch in a.rx_chans:
            u.set_rx_rate(self.Rw, ch)
            u.set_rx_antenna(a.rx_ant, ch)
            u.set_rx_gain(a.rx_gain, ch)
            u.set_rx_freq(wt, ch)
        u.set_rx_rate(self.Rnr, a.nr_chan)
        u.set_rx_antenna(a.nr_ant, a.nr_chan)
        u.set_rx_gain(a.nr_gain, a.nr_chan)
        u.set_rx_freq(nt, a.nr_chan)
        time.sleep(0.5)
        print("\n=== Setup ===")
        checks = [("TX", ch, a.wifi_freq, self.Rw) for ch in a.tx_chans]
        checks += [("RX", ch, a.wifi_freq, self.Rw) for ch in a.rx_chans]
        checks += [("RX", a.nr_chan, a.nr_freq, self.Rnr)]
        mcr = u.get_master_clock_rate()
        print(f"master clock {mcr/1e6:.2f} MHz, tick rate {self.R/1e6:.2f} MHz")
        if self.R > mcr * (1 + 1e-9) or abs(mcr / self.R - round(mcr / self.R)) > 1e-9:
            raise RuntimeError(f"tick rate {self.R} must divide the master clock {mcr}")
        for kind, ch, f_req, r_req in checks:
            g = (u.get_tx_freq, u.get_tx_rate, u.get_tx_sensor) if kind == "TX" else \
                (u.get_rx_freq, u.get_rx_rate, u.get_rx_sensor)
            f, r, lock = g[0](ch), g[1](ch), g[2]("lo_locked", ch).to_bool()
            ok = lock and abs(f - f_req) < 1e3 and abs(r - r_req) < 1.0
            print(f"{kind} ch{ch}: {f/1e6:10.4f} MHz {r/1e6:7.3f} Msps LO locked={lock} "
                  f"{'OK' if ok else 'FAIL'}")
            self.setup_report.append(dict(kind=kind, chan=ch, freq=f, rate=r, lo_locked=lock, ok=ok))
            if not ok:
                raise RuntimeError(f"{kind} ch{ch} not locked, mistuned or rate coerced")
        sa = uhd.usrp.StreamArgs("fc32", "sc16")
        sa.channels = list(a.tx_chans)
        self.tx = u.get_tx_stream(sa)
        sa = uhd.usrp.StreamArgs("fc32", "sc16")
        sa.channels = list(a.rx_chans)
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
        blk_n = int(round(self.Rnr * a.nr_block_ms * 1e-3))
        mx = self.nr.get_max_num_samps()
        self.cfo_hz = 0.0
        print("\n=== 5G lock ===")
        if self.nr_args.enable_cfo_correction:
            self.cfo_hz, _ = self.cfo_warmup(rx_streamer=self.nr, samples_per_block=blk_n,
                                             max_samps=mx, sample_rate=self.Rnr, args=self.nr_args)
        self.ncellid, self.ibar = a.force_ncellid, a.force_ibar_ssb
        for attempt in range(1, 21):
            b = self.capture(rx_streamer=self.nr, total_samples=blk_n, max_samps=mx,
                             sample_rate_hz=self.Rnr)
            try:
                res = self.analyze(b.waveform, self.nr_args, self.cfo_hz, self.Rnr,
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
        N = int(round(self.Rnr * a.nr_block_ms * 1e-3))
        ov = int(round(self.Rnr * a.nr_overlap_ms * 1e-3))
        tpn = self.tpn
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
                expected = tick + n * tpn
                i = 0
                while i < n:
                    if fill == 0:
                        blk_tick = tick + i * tpn
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
                            pending.append(pool.submit(self._analyze_block, blk.copy(),
                                                       blk_tick, self.phase))
                        blk[:ov] = blk[N:L]
                        fill, blk_tick = ov, blk_tick + N * tpn
            self.nr.issue_stream_cmd(uhd.types.StreamCMD(uhd.types.StreamMode.stop_cont))

    def _analyze_block(self, wf, blk_tick, phase):
        row = dict(phase=phase, block_start_device_ticks=int(blk_tick),
                   peak_dbfs=round(float(20 * np.log10(max(np.max(np.abs(wf)), 1e-12))), 2))
        try:
            res = self.analyze(wf, self.nr_args, self.cfo_hz, self.Rnr, self.ncellid, self.ibar)
            off = int(res["timing"]["timing_offset_samples"])
            row.update(valid=True, detector_offset_samples=off,
                       event_device_ticks=int(blk_tick) + off * self.tpn,
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
                    event=str(md.event_code).split(".")[-1], channel=int(md.channel),
                    tick=int(md.time_spec.to_ticks(self.R)) if md.has_time_spec else None,
                    host=time.time()))

    def send_burst(self, k, t_tx, rec):
        self.tx_q.put((k, t_tx, rec))

    # ---- WiFi RX windows ----
    def arm_observer(self, k, t_start, n):
        uhd = self.uhd
        cmd = uhd.types.StreamCMD(uhd.types.StreamMode.num_done)
        cmd.num_samps = int(n)
        cmd.stream_now = False
        cmd.time_spec = self._ts(t_start)
        with self.obs_lock:
            self.obs_tags.append((k, int(t_start), int(n)))
        self.obs.issue_stream_cmd(cmd)

    def _obs_thread(self):
        """One recv per window (fewer Python iterations). Every block of samples is
        matched to its window by the time_spec of its first sample, so an
        overflow or a lost window cannot shift the following windows onto the
        wrong command (it used to: one overflow made every later window fail)."""
        uhd = self.uhd
        E = uhd.types.RXMetadataErrorCode
        n_rx = len(self.args.rx_chans)
        md = uhd.types.RXMetadata()
        tpw = self.tpw
        self.stats.setdefault("rx_lost_windows", 0)
        self.stats.setdefault("rx_overflows", 0)
        while not self.stop_ev.is_set():
            with self.obs_lock:
                head = self.obs_tags[0] if self.obs_tags else None
            if head is None:
                time.sleep(0.001)
                continue
            k, t_start, n_need = head
            w = np.empty((n_rx, n_need), dtype=np.complex64)
            n = self.obs.recv(w, md, 0.2)
            if md.error_code == E.timeout and n == 0:
                continue
            if md.error_code == E.overflow:
                self.stats["rx_overflows"] += 1
            elif md.error_code != E.none:
                self.stats["rx_errors"] += 1
            if n == 0 or not md.has_time_spec:
                continue
            t0 = int(md.time_spec.to_ticks(self.R))
            got = n
            # same window not complete yet (no error): keep reading it
            while got < n_need and md.error_code == E.none and not md.end_of_burst \
                    and not self.stop_ev.is_set():
                tmp = np.empty((n_rx, n_need - got), dtype=np.complex64)
                m = self.obs.recv(tmp, md, 0.2)
                if m == 0:
                    break
                w[:, got:got + m] = tmp[:, :m]
                got += m
            if md.error_code == E.overflow and got < n_need:
                self.stats["rx_overflows"] += 1
            # match by time
            with self.obs_lock:
                while self.obs_tags:
                    kk, ts, nn = self.obs_tags[0]
                    if ts + nn * tpw <= t0:                     # window entirely before this data: lost
                        self.obs_tags.pop(0)
                        self.stats["rx_lost_windows"] += 1
                        self.obs_q.put(dict(k=kk, error="lost"))
                        continue
                    if abs(t0 - ts) < tpw:                       # data starts at this window
                        self.obs_tags.pop(0)
                        if got == nn:
                            self.obs_q.put(dict(k=kk, w0=t0, y=w))
                        else:
                            self.stats["rx_lost_windows"] += 1
                            self.obs_q.put(dict(k=kk, error="partial"))
                    elif ts < t0:                                # remainder of a broken window
                        self.obs_tags.pop(0)
                        self.stats["rx_lost_windows"] += 1
                        self.obs_q.put(dict(k=kk, error="partial"))
                    break                                        # ts > t0: data before any window, drop it

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
    """Device clock = host monotonic; SSB period off by PPM; PSS timestamps with
    Gaussian jitter after a processing delay; every TX->RX link has its own
    complex gain and a fractional delay (TX chains 2/3, i.e. DB1, +SKEW_NS)."""
    PPM, JITTER_NS, DELAY_MS, INVALID, LATENCY_NS, SNR_DB, SKEW_NS = \
        -0.151, 27.0, 40.0, 0.01, 3973.0, 25.0, 0.8

    def __init__(self, args, burst):
        self.args, self.burst, self.R = args, burst, getattr(args, "tick_rate", args.rate)
        self.nr_q, self.obs_q = queue.Queue(), queue.Queue()
        self.stop_ev = threading.Event()
        self.async_events = []
        self.t0 = time.monotonic()
        self.base = 51_000_000
        self.bursts = []
        self.lock = threading.Lock()
        self.obs_pending = []
        self.rng = np.random.default_rng(1)
        self.stats = _new_stats()
        self.phase = "lock"
        self.setup_report = [dict(sim=True)]
        n_rx, n_tx = len(args.rx_chans), burst.n_tx
        self.g = (10 ** (-62 / 20) * np.exp(1j * self.rng.uniform(0, 2 * np.pi, (n_rx, n_tx))))
        self.delay = np.array([[self.LATENCY_NS + (self.SKEW_NS if tc >= 2 else 0.0)
                                for tc in args.tx_chans] for _ in args.rx_chans]) * 1e-9 * self.R

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
                self.nr_q.put(dict(phase=self.phase, valid=False, error="sim invalid"))
            else:
                meas = int(round(T + self.rng.normal(0, self.JITTER_NS * 1e-9 * self.R)))
                self.nr_q.put(dict(phase=self.phase, valid=True, event_device_ticks=meas,
                                   pss_metric=0.95, sss_metric=0.98))
            k += 1

    def send_burst(self, k, t_tx, rec):
        now = self.now_ticks()
        rec["send_start_now_ticks"] = rec["send_done_now_ticks"] = now
        if t_tx < now:
            self.async_events.append(dict(event="time_error", channel=0, tick=None, host=time.time()))
            return
        with self.lock:
            self.bursts.append(t_tx)
        for c in range(self.burst.n_tx):
            self.async_events.append(dict(event="burst_ack", channel=c, tick=None, host=time.time()))

    def arm_observer(self, k, t_start, n):
        with self.lock:
            self.obs_pending.append((k, int(t_start), int(n)))

    def _obs_thread(self):
        b = self.burst
        while not self.stop_ev.is_set():
            with self.lock:
                ready = [p for p in self.obs_pending if self.now_ticks() > p[1] + p[2]]
                self.obs_pending = [p for p in self.obs_pending if p not in ready]
                bursts = list(self.bursts)
            for k, t_start, n in ready:
                n_rx = len(self.args.rx_chans)
                noise = np.abs(self.g).mean() * self.args.amp * 10 ** (-self.SNR_DB / 20)
                y = (noise / np.sqrt(2) * (self.rng.standard_normal((n_rx, n))
                                           + 1j * self.rng.standard_normal((n_rx, n)))).astype(np.complex64)
                M = next_fast_len(n + b.len)
                f = np.fft.fftfreq(M)
                for t_tx in bursts:
                    if not (-b.len < t_tx - t_start < n):
                        continue
                    for c in range(b.n_tx):
                        x = np.zeros(M, dtype=complex)
                        x[:b.len] = b.buf[c]
                        X = np.fft.fft(x)
                        for r in range(n_rx):
                            d = t_tx + self.delay[r, c] - t_start
                            y[r] += (self.g[r, c] * np.fft.ifft(X * np.exp(-2j * np.pi * f * d))[:n]
                                     ).astype(np.complex64)
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
    return dict(n=int(len(x)), mean=round(float(x.mean()), 2), std=round(float(x.std()), 2),
                p99_abs_dev=round(float(np.percentile(np.abs(x - x.mean()), 99)), 2),
                max_abs_dev=round(float(np.max(np.abs(x - x.mean()))), 2))


def detect_into(burst, rec, ob, rx_chans):
    rec["obs"] = {}
    for r, ch in enumerate(rx_chans):
        det = burst.detect(ob["y"][r])
        for d in det:
            if d.get("found"):
                d["t_obs"] = ob["w0"] + d["start"]
        rec["obs"][str(ch)] = det


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
                              spacing_samples=burst.S, pulse_samples=burst.N, roots=burst.roots))
    print(f"Burst: {burst.K} ZC pulses (N={burst.N}, {burst.N/R*1e6:.1f} us) every "
          f"{burst.S/R*1e6:.0f} us -> {burst.len/R*1e3:.3f} ms on TX ch{args.tx_chans} | "
          f"RX ch{args.rx_chans} | turn every {args.cycle_ssb} SSB, armed {args.lookahead_ssb} SSB ahead")
    summary["nr_lock"] = radio.acquire_nr()

    guard = int(round(args.rx_guard_ms * 1e-3 * R))
    n_obs = burst.len + 2 * guard
    min_margin = args.min_margin_ms * 1e-3 * R
    C, L = args.cycle_ssb, args.lookahead_ssb
    phase_plan = [("A_5g_only", args.nr_only_s, None),
                  ("B_offset", args.duration_s, args.slot_offset_ms),
                  ("C_overlap_ssb", args.overlap_s, args.overlap_offset_ms)]
    phase_plan = [p for p in phase_plan if p[1] > 0]

    nr_f = open(out_dir / "nr_events.jsonl", "w")
    turns, k_actual = {}, {}
    next_target = None
    phase_idx, phase_end = None, None
    last_print = time.monotonic()
    state = "SEARCHING"
    radio.start()
    t_start_mono = time.monotonic()
    print("\n=== Running (Ctrl+C to stop early) ===")

    def cur_phase():
        return phase_plan[phase_idx] if phase_idx is not None else ("lock", 0, None)

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
                print(f"[{state}] {trk.fit[5]} PSS, period {trk.fit[2]:.3f} ticks "
                      f"({trk.ppm():+.3f} ppm), resid {trk.fit[4]:.1f} ns")
                phase_idx, phase_end = 0, time.monotonic() + phase_plan[0][1]
                radio.phase = phase_plan[0][0]
                print(f"--- phase {phase_plan[0][0]} ({phase_plan[0][1]:.0f} s)")
                next_target = ((trk.k_last + L + C - 1) // C) * C
            if phase_idx is not None and time.monotonic() > phase_end:
                phase_idx += 1
                if phase_idx >= len(phase_plan):
                    break
                phase_end = time.monotonic() + phase_plan[phase_idx][1]
                radio.phase = phase_plan[phase_idx][0]
                print(f"--- phase {phase_plan[phase_idx][0]} ({phase_plan[phase_idx][1]:.0f} s)")

            # --- scheduler
            pname, _, off_ms = cur_phase()
            if next_target is not None:
                kl = trk.k_last
                while next_target <= kl:  # passed without being armed
                    turns[next_target] = dict(k=next_target, phase=pname, state="MISSED_NOT_ARMED")
                    next_target += C
                while next_target <= kl + L:
                    k = next_target
                    next_target += C
                    if off_ms is None:  # phase A: no TX, only 5G
                        continue
                    slot_off = off_ms * 1e-3 * R
                    now = radio.now_ticks()
                    locked, why = trk.locked(now)
                    rec = dict(k=k, phase=pname, slot_offset_ms=off_ms, now_arm=int(now),
                               k_last=kl, horizon_ssb=k - kl)
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

            # --- RX windows
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
                detect_into(burst, rec, ob, args.rx_chans)

            if time.monotonic() - last_print > 5 and turns:
                last_print = time.monotonic()
                ex = [r for r in turns.values() if r.get("state") == "ARMED"]
                ms = [r for r in turns.values() if str(r.get("state", "")).startswith("MISSED")]
                r0 = str(args.rx_chans[0])
                lat = [r["obs"][r0][0]["t_obs"] - r["t_tx"] for r in ex
                       if r.get("obs") and r["obs"][r0][0].get("found")]
                se = [r["obs"][r0][0]["t_obs"] - (r["T_actual"] + r["slot_offset_ms"] * 1e-3 * R)
                      for r in ex if r.get("obs") and r["obs"][r0][0].get("found") and "T_actual" in r]
                msg = f"  [{pname}] turns {len(turns)} | armed {len(ex)} missed {len(ms)} | observed {len(lat)}"
                if lat:
                    msg += f" | latency {np.median(lat)/R*1e9:.0f} ns"
                if len(se) > 1:
                    msg += f" | sync err std {np.std(se[-50:])/R*1e9:.1f} ns"
                if trk.fit:
                    msg += f" | tracker {trk.ppm():+.3f} ppm resid {trk.fit[4]:.0f} ns"
                bad = sum(1 for e in radio.async_events if e["event"] != "burst_ack")
                msg += f" | async non-ack {bad} | 5G ovf {radio.stats['nr_overflows']}"
                print(msg)

            if phase_idx is None and time.monotonic() - t_start_mono > 60:
                raise RuntimeError(f"Tracker did not lock in 60 s ({why})")
            time.sleep(0.002)
        # let the last turns finish (burst, RX window and T_actual)
        t_wait = time.monotonic() + 0.5
        while time.monotonic() < t_wait:
            try:
                ob = radio.obs_q.get(timeout=0.05)
            except queue.Empty:
                continue
            rec = turns.get(ob["k"])
            if rec is not None and "error" not in ob:
                detect_into(burst, rec, ob, args.rx_chans)
    except KeyboardInterrupt:
        print("\nInterrupted: writing results")
    finally:
        radio.stop()
        while not radio.nr_q.empty():
            row = radio.nr_q.get()
            if row.get("valid"):
                k_actual.setdefault(trk.k_of(row["event_device_ticks"]), row["event_device_ticks"])
            nr_f.write(json.dumps(row) + "\n")
        nr_f.close()

    # ---------------- results per turn ----------------
    for k, rec in turns.items():
        if "T_actual" not in rec and k in k_actual:
            rec["T_actual"] = k_actual[k]
        if "T_pred" in rec and "T_actual" in rec:
            rec["pred_err_ns"] = (rec["T_pred"] - rec["T_actual"]) / R * 1e9
        for ch, det in (rec.get("obs") or {}).items():
            for d in det:
                if d.get("found"):
                    d["latency_ns"] = (d["t_obs"] - rec["t_tx"]) / R * 1e9
                    if "T_actual" in rec:
                        d["sync_err_ns"] = (d["t_obs"] - rec["T_actual"]
                                            - rec["slot_offset_ms"] * 1e-3 * R) / R * 1e9
        if rec.get("state") == "ARMED":
            obs = rec.get("obs") or {}
            n_found = sum(1 for det in obs.values() for d in det if d.get("found"))
            n_links = len(args.rx_chans) * burst.n_tx
            rec["links_found"] = n_found
            rec["state"] = ("EXECUTED_OBSERVED" if n_found == n_links else
                            "EXECUTED_PARTIAL" if n_found else "EXECUTED_NOT_OBSERVED")
    with open(out_dir / "turns.jsonl", "w") as f:
        for k in sorted(turns):
            f.write(json.dumps(turns[k], default=float) + "\n")

    # ---------------- summary ----------------
    res = dict(turns=len(turns), async_events={}, radio_stats=radio.stats,
               tracker=dict(ppm=trk.ppm(), resid_ns=trk.fit[4] if trk.fit else None,
                            pss_points=len(trk.pts), duplicates=trk.dups, outliers=trk.outliers))
    for e in radio.async_events:
        res["async_events"][e["event"]] = res["async_events"].get(e["event"], 0) + 1
    res["phases"] = {}
    nr_rows = [json.loads(line) for line in open(out_dir / "nr_events.jsonl")]
    for pname, _, _ in phase_plan:
        pt = [r for r in turns.values() if r.get("phase") == pname]
        states = {}
        for r in pt:
            s = str(r["state"]).split(":")[0]
            states[s] = states.get(s, 0) + 1
        ex = [r for r in pt if str(r["state"]).startswith("EXECUTED")]
        rows = [r for r in nr_rows if r.get("phase") == pname]
        valid = [r for r in rows if r.get("valid")]
        ph = dict(turns=len(pt), states=states,
                  nr=dict(blocks=len(rows), valid=len(valid),
                          valid_pct=round(100 * len(valid) / len(rows), 2) if rows else None,
                          pss_metric=round(float(np.mean([r["pss_metric"] for r in valid])), 4) if valid else None,
                          sss_metric=round(float(np.mean([r["sss_metric"] for r in valid])), 4) if valid else None),
                  margin_ms=stats_ns([r.get("margin_ms") for r in ex]),
                  pred_err_ns=stats_ns([r.get("pred_err_ns") for r in ex]))
        links = {}
        for ch in args.rx_chans:
            for c, tc in enumerate(args.tx_chans):
                both = [r for r in ex if r.get("obs") and r["obs"][str(ch)][c].get("found")]
                ds = [r["obs"][str(ch)][c] for r in both]
                ph_std = None
                pair = [r for r in both if r["obs"][str(ch)][0].get("found")]
                if len(pair) > 1:
                    # phase of this link relative to TX chain 0 on the same RX (what BF weights use)
                    hs = np.array([complex(*r["obs"][str(ch)][c]["h"]) for r in pair])
                    ref = np.array([complex(*r["obs"][str(ch)][0]["h"]) for r in pair])
                    rel = np.unwrap(np.angle(hs / ref))
                    ph_std = round(float(np.rad2deg(np.std(rel))), 3)
                links[f"rx{ch}_tx{tc}"] = dict(
                    observed=len(ds), of=len(ex),
                    latency_ns=stats_ns([d["latency_ns"] for d in ds]),
                    sync_err_ns=stats_ns([d.get("sync_err_ns") for d in ds]),
                    level_dbfs=round(float(np.mean([d["level_dbfs"] for d in ds])), 2) if ds else None,
                    rel_phase_std_deg=ph_std)
        ph["links"] = links
        res["phases"][pname] = ph
    summary["results"] = res
    summary["finished_unix"] = time.time()
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, default=float))

    # ---------------- console report ----------------
    print("\n=== Summary ===")
    print(f"TX async events: {res['async_events']} | radio: {radio.stats}")
    print(f"Tracker: {res['tracker']}")
    ok = True
    ev = res["async_events"]
    for pname, ph in res["phases"].items():
        print(f"\n--- {pname}: turns {ph['turns']} {ph['states']} | 5G valid {ph['nr']['valid_pct']} % "
              f"PSS {ph['nr']['pss_metric']} SSS {ph['nr']['sss_metric']}")
        if not ph["turns"]:
            continue
        print(f"    margin [ms] {ph['margin_ms']} | pred err [ns] {ph['pred_err_ns']}")
        lat = {k: v["latency_ns"]["mean"] for k, v in ph["links"].items() if v["latency_ns"]}
        if pname == "B_offset":
            worst_obs = min(v["observed"] / max(v["of"], 1) for v in ph["links"].values())
            ok = ok and bool(lat) and worst_obs >= 0.99
        if lat:
            base = np.median(list(lat.values()))
            print(f"    latency per link, ns relative to the median {base:.1f} ns "
                  f"(std over turns in brackets):")
            print("            " + "".join(f"{'TX ch' + str(t):>18}" for t in args.tx_chans))
            for ch in args.rx_chans:
                cells = []
                for tc in args.tx_chans:
                    v = ph["links"][f"rx{ch}_tx{tc}"]
                    cells.append(f"{v['latency_ns']['mean'] - base:+7.2f} ({v['latency_ns']['std']:.2f})"
                                 if v["latency_ns"] else "      -")
                print(f"    RX ch{ch:<3}" + "".join(f"{c:>18}" for c in cells))
            print("    relative phase std vs TX ch%d [deg]: " % args.tx_chans[0] + ", ".join(
                f"{k} {v['rel_phase_std_deg']}" for k, v in ph["links"].items()
                if v["rel_phase_std_deg"] is not None and not k.endswith(f"_tx{args.tx_chans[0]}")))
            se = [v["sync_err_ns"]["std"] for v in ph["links"].values() if v["sync_err_ns"]]
            obs = min(v["observed"] / max(v["of"], 1) for v in ph["links"].values())
            if se:
                print(f"    sync error std per link [ns]: min {min(se):.1f} max {max(se):.1f} | "
                      f"worst link observed {100*obs:.2f} %")
            if pname == "B_offset":
                ok = ok and bool(se) and max(se) < 200
    a_pct = res["phases"].get("A_5g_only", {}).get("nr", {}).get("valid_pct")
    b_pct = res["phases"].get("B_offset", {}).get("nr", {}).get("valid_pct")
    late = sum(ph["states"].get("MISSED_LATE", 0) for ph in res["phases"].values())
    ok = ok and late == 0 and not ev.get("time_error") and not ev.get("underflow")
    if a_pct is not None and b_pct is not None:
        print(f"\n5G valid rate B - A: {b_pct - a_pct:+.2f} points")
        ok = ok and b_pct - a_pct > -1.0
    print(f"\nVerdict (phase B): {'PASS' if ok else 'CHECK'} (no late/underflow, >=99 % observed on "
          f"every link, sync error std < 200 ns, 5G not degraded)")
    print(f"Saved {out_dir}/ (summary.json, turns.jsonl, nr_events.jsonl)")


if __name__ == "__main__":
    main()
