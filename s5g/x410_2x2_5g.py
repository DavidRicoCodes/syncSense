#!/usr/bin/env python3
"""
X410 bench smoke: WiFi 2x2 (tone sounding / MRT / ZF, as in 2x2.py) and
5G SSB reception at the same time, in ONE UHD session.

Channel plan (defaults, all configurable):
  DB0  ch0  TX WiFi (TX/RX0)   RX WiFi A (RX1)      2.45 GHz
  DB0  ch1  TX WiFi (TX/RX0)   RX WiFi B (RX1)      2.45 GHz
  DB1  ch2  -                  RX 5G SSB (RX1)      3541.44 MHz @ 15.36 Msps
  DB1  ch3  -                  RX WiFi observer (RX1), sees our own TX

Steps:
  0. Setup: rates, tuning and LO lock of every channel are verified.
  1. Leakage check: sounding with the WiFi RX on RX1 (antennas) and on
     TERMINATION; the difference shows how much of H is internal leakage.
  2. Lock the 5G cell (CFO warmup + PSS/SSS) on ch2.
  3. Phase A: 5G only.       Phase B: 5G + 2x2 loop.       Phase C: 5G only.
     The 5G receiver runs continuously in its own thread for all phases.
  4. Summary: 5G valid rate / metrics per phase, SSB period jitter in X410
     ticks, and 2x2 channel/ZF stability across iterations.

No sync logic yet: 5G timestamps (X410 ticks = block time_spec + detector
offset) are only recorded, not used to schedule WiFi.

The 5G DSP is imported unchanged from modulos_rx_tx (fusion_dataset).

Example:
  python3 x410_2x2_5g.py --phase-s 30
  python3 x410_2x2_5g.py --nr-gain 50 --tx-gain 15 --observer-chan -1
  python3 x410_2x2_5g.py --dry-run          # checks imports/args, no USRP
"""
import argparse
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np


def parse_args():
    p = argparse.ArgumentParser(description="X410 WiFi 2x2 + 5G SSB RX smoke")
    p.add_argument("--args", default="addr=192.168.10.2")
    p.add_argument("--modulos-root", type=Path,
                   default=Path.home() / "syncSense" / "modulos_rx_tx")
    p.add_argument("--out-dir", type=Path, default=None,
                   help="Default: ./x410_2x2_5g_<timestamp>")
    p.add_argument("--dry-run", action="store_true",
                   help="Only check imports and arguments, do not open the USRP")
    # WiFi 2x2
    p.add_argument("--wifi-freq", type=float, default=2.45e9)
    p.add_argument("--wifi-rate", type=float, default=1.92e6)
    p.add_argument("--tone", type=float, default=60e3)
    p.add_argument("--amp", type=float, default=0.25, help="Digital amplitude per TX channel")
    p.add_argument("--tx-gain", type=float, default=10.0)
    p.add_argument("--rx-gain", type=float, default=20.0)
    p.add_argument("--tx-chans", type=int, nargs=2, default=[0, 1])
    p.add_argument("--rx-chans", type=int, nargs=2, default=[0, 1], help="RX A, RX B")
    p.add_argument("--rx-ant", default="RX1")
    p.add_argument("--observer-chan", type=int, default=3,
                   help="Extra WiFi RX that watches our own TX (-1 = disabled)")
    p.add_argument("--seg-ms", type=float, default=10.0)
    p.add_argument("--skip-leakage", action="store_true")
    # 5G
    p.add_argument("--nr-chan", type=int, default=2)
    p.add_argument("--nr-ant", default="RX1")
    p.add_argument("--nr-freq", type=float, default=3541.44e6)
    p.add_argument("--nr-rate", type=float, default=15.36e6)
    p.add_argument("--nr-gain", type=float, default=40.0)
    p.add_argument("--nr-block-ms", type=float, default=20.0)
    p.add_argument("--force-nid2", type=int, choices=(0, 1, 2), default=0)
    p.add_argument("--force-ibar-ssb", type=int, default=0)
    p.add_argument("--force-ncellid", type=int, default=None)
    p.add_argument("--no-cfo-correction", action="store_true")
    p.add_argument("--cfo-warmup-iters", type=int, default=30)
    p.add_argument("--nr-workers", type=int, default=2)
    # Phases
    p.add_argument("--phase-s", type=float, default=30.0, help="Duration of each phase A/B/C")
    return p.parse_args()


# --------------------------------------------------------------------------
# 5G DSP from modulos_rx_tx (unchanged)
# --------------------------------------------------------------------------
def load_nr_dsp(modulos_root, args):
    root = modulos_root.resolve()
    if not (root / "src" / "python" / "fusion_dataset").is_dir():
        raise SystemExit(f"modulos_rx_tx not found at {root}")
    sys.path.insert(0, str(root))
    from src.python.fusion_dataset import online_5g_hssb_jsonl as base
    from src.python.fusion_dataset.online_5g_hssb_jsonl_parallel import analyze
    from src.python.fusion_dataset.cfo_warmup import estimate_cfo_warmup
    from profile_online_datassb_pipeline import capture_one_block_with_metadata

    # Reuse the receiver's own defaults; only the fields we need are overridden.
    saved = sys.argv
    sys.argv = ["x410_2x2_5g", "--serial", "unused", "--duration-s", "1",
                "--clock-anchor", "unused", "--output-jsonl", "unused",
                "--output-rxgrid-cf32", "unused", "--output-hssb-cf32", "unused"]
    try:
        nr_args = base.arguments()
    finally:
        sys.argv = saved
    nr_args.freq = args.nr_freq
    nr_args.rate = args.nr_rate
    nr_args.gain = args.nr_gain
    nr_args.channel = args.nr_chan
    nr_args.duration_ms = args.nr_block_ms
    nr_args.force_nid2 = args.force_nid2
    nr_args.force_ibar_ssb = args.force_ibar_ssb
    nr_args.force_ncellid = args.force_ncellid
    nr_args.enable_cfo_correction = not args.no_cfo_correction
    nr_args.cfo_warmup_iters = args.cfo_warmup_iters
    return nr_args, analyze, estimate_cfo_warmup, capture_one_block_with_metadata


# --------------------------------------------------------------------------
# USRP setup
# --------------------------------------------------------------------------
def setup_usrp(uhd, args, wifi_rx):
    usrp = uhd.usrp.MultiUSRP(args.args)
    wifi_tune = uhd.types.TuneRequest(args.wifi_freq)
    nr_tune = uhd.types.TuneRequest(args.nr_freq)
    # Untimed tuning, no set_time_now(): the X410 keeps its own device time.
    for ch in args.tx_chans:
        usrp.set_tx_rate(args.wifi_rate, ch)
        usrp.set_tx_antenna("TX/RX0", ch)
        usrp.set_tx_gain(args.tx_gain, ch)
        usrp.set_tx_freq(wifi_tune, ch)
    for ch in wifi_rx:
        usrp.set_rx_rate(args.wifi_rate, ch)
        usrp.set_rx_antenna(args.rx_ant, ch)
        usrp.set_rx_gain(args.rx_gain, ch)
        usrp.set_rx_freq(wifi_tune, ch)
    usrp.set_rx_rate(args.nr_rate, args.nr_chan)
    usrp.set_rx_antenna(args.nr_ant, args.nr_chan)
    usrp.set_rx_gain(args.nr_gain, args.nr_chan)
    usrp.set_rx_freq(nr_tune, args.nr_chan)
    time.sleep(0.5)

    report = []
    checks = [("TX", ch, args.wifi_freq, args.wifi_rate) for ch in args.tx_chans]
    checks += [("RX", ch, args.wifi_freq, args.wifi_rate) for ch in wifi_rx]
    checks += [("RX", args.nr_chan, args.nr_freq, args.nr_rate)]
    print("\n=== 0. Setup ===")
    for kind, ch, f_req, r_req in checks:
        if kind == "TX":
            f, r = usrp.get_tx_freq(ch), usrp.get_tx_rate(ch)
            lock = usrp.get_tx_sensor("lo_locked", ch).to_bool()
            ant, gain = usrp.get_tx_antenna(ch), usrp.get_tx_gain(ch)
        else:
            f, r = usrp.get_rx_freq(ch), usrp.get_rx_rate(ch)
            lock = usrp.get_rx_sensor("lo_locked", ch).to_bool()
            ant, gain = usrp.get_rx_antenna(ch), usrp.get_rx_gain(ch)
        ok = lock and abs(f - f_req) < 1e3 and abs(r - r_req) < 1.0
        print(f"{kind} ch{ch}: {f/1e6:10.4f} MHz  {r/1e6:7.3f} Msps  ant={ant:<7} "
              f"gain={gain:4.1f} dB  LO locked={lock}  {'OK' if ok else 'FAIL'}")
        report.append(dict(kind=kind, chan=ch, freq=f, rate=r, antenna=ant,
                           gain=gain, lo_locked=lock, ok=ok))
        if not ok:
            raise RuntimeError(f"{kind} ch{ch} not locked, mistuned or rate coerced "
                               f"(asked {f_req/1e6:.4f} MHz @ {r_req/1e6:.3f} Msps)")
    return usrp, report


# --------------------------------------------------------------------------
# WiFi 2x2 (same method as 2x2.py)
# --------------------------------------------------------------------------
class Wifi2x2:
    def __init__(self, uhd, usrp, args, wifi_rx):
        self.uhd, self.usrp, self.args = uhd, usrp, args
        self.rx_chans = wifi_rx
        tx_args = uhd.usrp.StreamArgs("fc32", "sc16")
        tx_args.channels = list(args.tx_chans)
        self.tx = usrp.get_tx_stream(tx_args)
        rx_args = uhd.usrp.StreamArgs("fc32", "sc16")
        rx_args.channels = list(wifi_rx)
        self.rx = usrp.get_rx_stream(rx_args)
        self.rate = usrp.get_tx_rate(args.tx_chans[0])
        n_per = int(round(self.rate / args.tone))
        self.tone = self.rate / n_per
        self.seg_len = max(1, int(round(args.seg_ms * 1e-3 * self.rate / n_per))) * n_per

    def run_burst(self, txbuf, lead=0.4):
        uhd, usrp, n_rx = self.uhd, self.usrp, len(self.rx_chans)
        n_total = txbuf.shape[1]
        t0 = usrp.get_time_now().get_real_secs() + lead
        rx_out = np.zeros((n_rx, n_total), dtype=np.complex64)
        errors = []

        def rx_worker():
            cmd = uhd.types.StreamCMD(uhd.types.StreamMode.num_done)
            cmd.num_samps = n_total
            cmd.stream_now = False
            cmd.time_spec = uhd.types.TimeSpec(t0)
            self.rx.issue_stream_cmd(cmd)
            tmp = np.zeros((n_rx, self.rx.get_max_num_samps()), dtype=np.complex64)
            md = uhd.types.RXMetadata()
            got, timeout = 0, lead + 1.0
            while got < n_total:
                n = self.rx.recv(tmp, md, timeout)
                timeout = 0.5
                if md.error_code != uhd.types.RXMetadataErrorCode.none:
                    errors.append(f"RX {md.error_code}")
                    break
                k = min(n, n_total - got)
                rx_out[:, got:got + k] = tmp[:, :k]
                got += k

        th = threading.Thread(target=rx_worker)
        th.start()
        md = uhd.types.TXMetadata()
        md.start_of_burst = True
        md.has_time_spec = True
        md.time_spec = uhd.types.TimeSpec(t0)
        chunk = 20000
        for a in range(0, n_total, chunk):
            b = min(a + chunk, n_total)
            md.end_of_burst = (b == n_total)
            sent = self.tx.send(np.ascontiguousarray(txbuf[:, a:b]), md, 1.0)
            if sent != b - a:
                errors.append(f"TX send short ({sent}/{b - a})")
                break
            md.start_of_burst = False
            md.has_time_spec = False
        th.join()
        amd = uhd.types.TXAsyncMetadata()
        while self.tx.recv_async_msg(amd, 0.1):
            if amd.event_code != uhd.types.TXMetadataEventCode.burst_ack:
                errors.append(f"TX {amd.event_code} ch{amd.channel}")
        return rx_out, errors

    def burst(self, weights):
        """One segment per 2-element weight vector. Returns Y (n_rx x nseg), peak."""
        a, L = self.args, self.seg_len
        nseg = len(weights)
        n = np.arange(nseg * L)
        s = np.exp(2j * np.pi * self.tone * n / self.rate).astype(np.complex64)
        buf = np.zeros((2, nseg * L), dtype=np.complex64)
        for i, w in enumerate(weights):
            sl = slice(i * L, (i + 1) * L)
            buf[:, sl] = (a.amp * np.asarray(w)[:, None] * s[None, sl]).astype(np.complex64)
        out, errors = self.run_burst(buf)
        g = int(0.2 * L)
        Y = np.zeros((out.shape[0], nseg), dtype=np.complex128)
        for r in range(out.shape[0]):
            for i in range(nseg):
                lo, hi = i * L + g, (i + 1) * L - g
                Y[r, i] = np.mean(out[r, lo:hi] * np.conj(s[lo:hi]))
        return Y / a.amp, float(np.max(np.abs(out))), errors

    def sound(self):
        Y, peak, errors = self.burst([[0, 0], [1, 0], [0, 1]])
        floor = np.abs(Y[:, 0]) * self.args.amp
        return Y[:, 1:3], floor, peak, errors

    def tests(self, H):
        hA, hB = H[0], H[1]

        def unit_max(w):
            w = np.asarray(w, dtype=np.complex128)
            return w / np.max(np.abs(w))

        tests = [
            ("No correction [1,1]", np.array([1, 1], dtype=np.complex128)),
            ("MRT -> A", np.exp(-1j * np.angle(hA))),
            ("MRT -> B", np.exp(-1j * np.angle(hB))),
            ("ZF: max A, null B", unit_max([hB[1], -hB[0]])),
            ("ZF: max B, null A", unit_max([hA[1], -hA[0]])),
        ]
        Yt, peak, errors = self.burst([w for _, w in tests])
        return tests, Yt, peak, errors


def db10(x):
    return 10 * np.log10(np.maximum(x, 1e-15))


def db20(x):
    return 20 * np.log10(np.maximum(np.abs(x), 1e-12))


# --------------------------------------------------------------------------
# 5G receiver thread
# --------------------------------------------------------------------------
class NrReceiver:
    def __init__(self, uhd, usrp, args, nr_args, analyze, cfo_warmup, capture, out_path):
        self.uhd, self.usrp, self.args, self.nr_args = uhd, usrp, args, nr_args
        self.analyze, self.cfo_warmup, self.capture = analyze, cfo_warmup, capture
        sa = uhd.usrp.StreamArgs("fc32", "sc16")
        sa.channels = [args.nr_chan]
        self.streamer = usrp.get_rx_stream(sa)
        self.rate = float(usrp.get_rx_rate(args.nr_chan))
        self.block = int(round(self.rate * args.nr_block_ms * 1e-3))
        self.max_samps = self.streamer.get_max_num_samps()
        self.phase = "lock"
        self.stop = threading.Event()
        self.rows = []
        self.lock = threading.Lock()
        self.out = open(out_path, "w")
        self.cfo_hz = 0.0
        self.ncellid = args.force_ncellid
        self.ibar = args.force_ibar_ssb

    def grab(self):
        return self.capture(rx_streamer=self.streamer, total_samples=self.block,
                            max_samps=self.max_samps, sample_rate_hz=self.rate)

    def acquire(self):
        print("\n=== 2. 5G lock ===")
        if self.nr_args.enable_cfo_correction:
            self.cfo_hz, _ = self.cfo_warmup(rx_streamer=self.streamer,
                                             samples_per_block=self.block,
                                             max_samps=self.max_samps,
                                             sample_rate=self.rate, args=self.nr_args)
        for attempt in range(1, 21):
            blk = self.grab()
            try:
                res = self.analyze(blk.waveform, self.nr_args, self.cfo_hz, self.rate,
                                   self.ncellid, self.ibar)
            except ValueError as e:
                print(f"  lock attempt {attempt}: {e}")
                continue
            self.ncellid = res["estimate"].ncellid
            self.ibar = res["estimate"].ibar_ssb
            peak = db20(np.max(np.abs(blk.waveform)))
            print(f"5G locked: NCellID={self.ncellid} ibar_ssb={self.ibar} "
                  f"CFO={self.cfo_hz:+.1f} Hz PSS={res['pss_metric']:.3f} "
                  f"SSS={res['estimate'].sss_metric:.3f} peak={peak:.1f} dBFS")
            if peak > -3:
                print("[WARN] 5G RX close to full scale: lower --nr-gain")
            return
        raise RuntimeError("Could not lock the 5G cell in 20 attempts "
                           "(check antenna on the 5G channel, --nr-gain, --force-*)")

    def _record(self, it, phase, blk, fut):
        row = dict(iteration=it, phase=phase,
                   block_start_device_ticks=blk.block_start_device_ticks,
                   device_tick_rate_hz=blk.tick_rate_hz,
                   peak_dbfs=round(float(db20(np.max(np.abs(blk.waveform)))), 2))
        try:
            res = fut.result()
            est, off = res["estimate"], int(res["timing"]["timing_offset_samples"])
            row.update(valid=True, error="", detector_offset_samples=off,
                       event_device_ticks=blk.block_start_device_ticks + off,
                       reference_point="ssb_pss_start",
                       ncellid=est.ncellid, ibar_ssb=est.ibar_ssb,
                       pss_metric=round(res["pss_metric"], 4),
                       sss_metric=round(float(est.sss_metric), 4),
                       pbch_dmrs_metric_db=round(float(est.dmrs_metric_db), 2),
                       analysis_time_ms=round(res["analysis_time_ms"], 2))
        except Exception as e:  # invalid block: keep it, it counts for the rate
            row.update(valid=False, error=str(e)[:200])
        with self.lock:
            self.rows.append(row)
        self.out.write(json.dumps(row) + "\n")

    def run(self):
        it = 0
        pending = []
        with ThreadPoolExecutor(max_workers=self.args.nr_workers) as pool:
            while not self.stop.is_set():
                phase = self.phase
                try:
                    blk = self.grab()
                except RuntimeError as e:
                    row = dict(iteration=it, phase=phase, valid=False,
                               error=f"capture: {e}", capture_error=True)
                    with self.lock:
                        self.rows.append(row)
                    self.out.write(json.dumps(row) + "\n")
                    it += 1
                    continue
                fut = pool.submit(self.analyze, blk.waveform, self.nr_args, self.cfo_hz,
                                  self.rate, self.ncellid, self.ibar)
                pending.append((it, phase, blk, fut))
                while pending and (pending[0][3].done() or len(pending) > 8):
                    self._record(*pending.pop(0))
                it += 1
            for p in pending:
                self._record(*p)
        self.out.close()


def nr_phase_summary(rows, phase, rate_hz, block_ms):
    rs = [r for r in rows if r["phase"] == phase]
    v = [r for r in rs if r.get("valid")]
    out = dict(phase=phase, captured=len(rs), valid=len(v),
               capture_errors=sum(1 for r in rs if r.get("capture_error")),
               valid_pct=round(100 * len(v) / len(rs), 2) if rs else 0.0)
    if v:
        for k in ("pss_metric", "sss_metric", "pbch_dmrs_metric_db", "peak_dbfs"):
            out[f"{k}_mean"] = round(float(np.mean([r[k] for r in v])), 3)
        out["ncellids"] = sorted({r["ncellid"] for r in v})
        # SSB period check in X410 ticks: consecutive valid events should be
        # separated by multiples of the SSB period (nominal 20 ms).
        ev = np.array([r["event_device_ticks"] for r in v], dtype=np.int64)
        if len(ev) > 2:
            per = int(round(rate_hz * block_ms * 1e-3))
            d = np.diff(ev)
            resid = ((d + per // 2) % per) - per // 2
            out["ssb_period_resid_samples_std"] = round(float(np.std(resid)), 3)
            out["ssb_period_resid_samples_maxabs"] = int(np.max(np.abs(resid)))
            out["ssb_period_resid_ns_std"] = round(float(np.std(resid)) / rate_hz * 1e9, 1)
    return out


# --------------------------------------------------------------------------
def main():
    args = parse_args()
    wifi_rx = list(args.rx_chans) + ([args.observer_chan] if args.observer_chan >= 0 else [])
    used_rx = wifi_rx + [args.nr_chan]
    if len(set(used_rx)) != len(used_rx):
        raise SystemExit(f"RX channel used twice: {used_rx}")
    rx_names = ["A", "B"] + (["Obs"] if args.observer_chan >= 0 else [])

    nr_args, analyze, cfo_warmup, capture = load_nr_dsp(args.modulos_root, args)
    import uhd
    print("5G DSP imported from", args.modulos_root)
    print(f"WiFi TX ch{args.tx_chans}  RX {dict(zip(rx_names, wifi_rx))} @ "
          f"{args.wifi_freq/1e9:.3f} GHz | 5G RX ch{args.nr_chan} @ {args.nr_freq/1e6:.2f} MHz")
    if args.dry_run:
        print("Dry run: imports and arguments OK, USRP not opened")
        return

    out_dir = args.out_dir or Path(f"x410_2x2_5g_{time.strftime('%Y%m%d_%H%M%S')}")
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = dict(args={k: (str(v) if isinstance(v, Path) else v) for k, v in vars(args).items()},
                   started_unix=time.time())

    usrp, setup_report = setup_usrp(uhd, args, wifi_rx)
    summary["setup"] = setup_report
    wifi = Wifi2x2(uhd, usrp, args, wifi_rx)

    # ---------------- 1. Leakage check ----------------
    if not args.skip_leakage:
        print("\n=== 1. Leakage check (WiFi RX on antenna vs TERMINATION) ===")
        H_ant, floor_ant, pk, e1 = wifi.sound()
        for ch in args.rx_chans:
            usrp.set_rx_antenna("TERMINATION", ch)
        time.sleep(0.2)
        H_term, floor_term, _, e2 = wifi.sound()
        for ch in args.rx_chans:
            usrp.set_rx_antenna(args.rx_ant, ch)
        time.sleep(0.2)
        if e1 or e2:
            print(f"[WARN] burst errors: {e1 + e2}")
        leak = []
        for r in range(2):
            for k in range(2):
                ota, term = db20(H_ant[r, k] * args.amp), db20(H_term[r, k] * args.amp)
                print(f"RX {rx_names[r]} ch{wifi_rx[r]} <- TX ch{args.tx_chans[k]}: "
                      f"antenna {ota:6.1f} dBFS | termination {term:6.1f} dBFS | "
                      f"margin {ota - term:5.1f} dB")
                leak.append(dict(rx=rx_names[r], tx=args.tx_chans[k], antenna_dbfs=ota,
                                 termination_dbfs=term, margin_db=ota - term))
        if min(x["margin_db"] for x in leak) < 20:
            print("[WARN] internal leakage within 20 dB of the OTA signal on some link: "
                  "H/ZF will be biased. Note TERMINATION does not include port-to-port "
                  "coupling; for that, put 50 ohm on RX1 instead of the antenna.")
        summary["leakage"] = leak

    # ---------------- 2. 5G lock ----------------
    nr = NrReceiver(uhd, usrp, args, nr_args, analyze, cfo_warmup, capture,
                    out_dir / "nr_events.jsonl")
    nr.acquire()
    summary["nr_lock"] = dict(ncellid=nr.ncellid, ibar_ssb=nr.ibar, cfo_hz=nr.cfo_hz)
    nr_thread = threading.Thread(target=nr.run, daemon=True)

    # ---------------- 3. Phases ----------------
    wifi_iters = []
    try:
        nr.phase = "A_5g_only"
        nr_thread.start()
        print(f"\n=== 3A. 5G only ({args.phase_s:.0f} s) ===")
        time.sleep(args.phase_s)

        nr.phase = "B_5g_plus_2x2"
        print(f"\n=== 3B. 5G + WiFi 2x2 ({args.phase_s:.0f} s) ===")
        t_end = time.monotonic() + args.phase_s
        i = 0
        while time.monotonic() < t_end:
            H, floor, pk1, e1 = wifi.sound()
            tests, Yt, pk2, e2 = wifi.tests(H[:2])
            ref = np.mean(np.abs(H[:2]) ** 2, axis=1)
            pa = db10(np.abs(Yt[0]) ** 2 / ref[0])
            pb = db10(np.abs(Yt[1]) ** 2 / ref[1])
            snr = db20(H * args.amp) - db20(np.maximum(floor, 1e-12))[:, None]
            it = dict(i=i, t=time.time(), H=H, floor=floor, Yt=Yt,
                      zf_iso_a=float(pa[3] - pb[3]), zf_iso_b=float(pb[4] - pa[4]),
                      mrt_gain_a=float(pa[1]), mrt_gain_b=float(pb[2]),
                      min_snr=float(np.min(snr[:2])), rx_peak=max(pk1, pk2),
                      errors=e1 + e2)
            wifi_iters.append(it)
            with nr.lock:
                nv = sum(1 for r in nr.rows if r["phase"] == nr.phase and r.get("valid"))
                nt = sum(1 for r in nr.rows if r["phase"] == nr.phase)
            print(f"  it {i:3d}: |H| dBFS A=[{db20(H[0,0]*args.amp):5.1f} {db20(H[0,1]*args.amp):5.1f}] "
                  f"B=[{db20(H[1,0]*args.amp):5.1f} {db20(H[1,1]*args.amp):5.1f}] "
                  f"minSNR {it['min_snr']:4.1f} dB | ZF iso {it['zf_iso_a']:+5.1f}/{it['zf_iso_b']:+5.1f} dB"
                  f" | 5G {nv}/{nt}" + (f" | ERR {it['errors']}" if it["errors"] else ""))
            if it["rx_peak"] > 0.8:
                print("  [WARN] WiFi RX close to saturation, lower gains")
            i += 1

        nr.phase = "C_5g_only"
        print(f"\n=== 3C. 5G only again ({args.phase_s:.0f} s) ===")
        time.sleep(args.phase_s)
    except KeyboardInterrupt:
        print("\nInterrupted: finishing and writing the summary")
    finally:
        nr.stop.set()
        if nr_thread.is_alive():
            nr_thread.join(timeout=10)

    # ---------------- 4. Summary ----------------
    print("\n=== 4. Summary ===")
    nr_sum = [nr_phase_summary(nr.rows, ph, nr.rate, args.nr_block_ms)
              for ph in ("A_5g_only", "B_5g_plus_2x2", "C_5g_only")]
    for s in nr_sum:
        extra = ""
        if "pss_metric_mean" in s:
            extra = (f" | PSS {s['pss_metric_mean']:.3f} SSS {s['sss_metric_mean']:.3f} "
                     f"DMRS {s['pbch_dmrs_metric_db_mean']:.1f} dB peak {s['peak_dbfs_mean']:.1f} dBFS")
        if "ssb_period_resid_samples_std" in s:
            extra += (f" | SSB period resid std {s['ssb_period_resid_samples_std']:.2f} samp "
                      f"({s['ssb_period_resid_ns_std']:.0f} ns), max {s['ssb_period_resid_samples_maxabs']}")
        print(f"5G {s['phase']:<15} {s['valid']:5d}/{s['captured']:<5d} valid "
              f"({s['valid_pct']:6.2f} %) cap_err {s['capture_errors']}{extra}")
    summary["nr_phases"] = nr_sum

    if wifi_iters:
        Hs = np.array([w["H"] for w in wifi_iters])
        drift = np.rad2deg(np.angle(Hs[:, :2, :] / Hs[0, :2, :]))
        ws = dict(iterations=len(wifi_iters),
                  burst_errors=sum(len(w["errors"]) for w in wifi_iters),
                  min_snr_db=round(min(w["min_snr"] for w in wifi_iters), 1),
                  zf_iso_a_db_median=round(float(np.median([w["zf_iso_a"] for w in wifi_iters])), 1),
                  zf_iso_b_db_median=round(float(np.median([w["zf_iso_b"] for w in wifi_iters])), 1),
                  mrt_gain_a_db_median=round(float(np.median([w["mrt_gain_a"] for w in wifi_iters])), 1),
                  mrt_gain_b_db_median=round(float(np.median([w["mrt_gain_b"] for w in wifi_iters])), 1),
                  H_phase_drift_deg_maxabs=round(float(np.max(np.abs(drift))), 1),
                  H_amp_std_db=round(float(np.max(np.std(db20(Hs[:, :2, :]), axis=0))), 2))
        print(f"WiFi 2x2: {ws['iterations']} iterations, burst errors {ws['burst_errors']}, "
              f"min SNR {ws['min_snr_db']} dB")
        print(f"  MRT gain A/B (median) {ws['mrt_gain_a_db_median']:+.1f}/{ws['mrt_gain_b_db_median']:+.1f} dB "
              f"(ideal ~+6 dB for 2 elements) | ZF isolation {ws['zf_iso_a_db_median']:+.1f}/"
              f"{ws['zf_iso_b_db_median']:+.1f} dB")
        print(f"  H stability in session: phase drift max {ws['H_phase_drift_deg_maxabs']:.1f} deg, "
              f"amplitude std max {ws['H_amp_std_db']:.2f} dB")
        summary["wifi"] = ws
        np.savez(out_dir / "wifi_2x2.npz", H=Hs, floor=np.array([w["floor"] for w in wifi_iters]),
                 Yt=np.array([w["Yt"] for w in wifi_iters]),
                 t=np.array([w["t"] for w in wifi_iters]),
                 rx_names=np.array(rx_names), rx_chans=np.array(wifi_rx),
                 tx_chans=np.array(args.tx_chans), freq=args.wifi_freq,
                 tone=wifi.tone, rate=wifi.rate, amp=args.amp,
                 tx_gain=args.tx_gain, rx_gain=args.rx_gain)

    a, b = nr_sum[0], nr_sum[1]
    if a["captured"] and b["captured"]:
        delta = b["valid_pct"] - a["valid_pct"]
        print(f"\n5G valid rate change with WiFi active: {delta:+.2f} points "
              f"({'OK' if delta > -1.0 else 'DEGRADED'})")
        summary["nr_valid_delta_pct_points"] = round(delta, 2)
    summary["finished_unix"] = time.time()
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, default=float))
    print(f"\nSaved {out_dir}/ (summary.json, nr_events.jsonl, wifi_2x2.npz)")


if __name__ == "__main__":
    main()
