#!/usr/bin/env python3
"""
Two-AP bistatic sensing sessions, timed by the 5G (same script on both APs).

Timing comes from the 5G PSS of each AP (as in x410_two_ap.py); SSB numbering
is still PROVISIONAL (host time / NTP), to be replaced by the PBCH/MIB SFN.

Sessions: cycle c (5 SSB = 100 ms) is in a session when
          (c % --session-period-cycles) in [--session-start, --session-start + --session-len)
          (trigger hard-coded by cycle number; later: passive 5G detector).
          Outside sessions both APs are silent (only the 5G is tracked).
In a session, every 10 ms of 5G time (two per SSB):
          AP A transmits at PSS + --offset-a-ms (+10 ms), AP B at PSS + --offset-b-ms (+10 ms),
          each listens to the other on its 3 WiFi RX chains -> both bistatic
          directions at 100 Hz, interleaved.
Burst:    --n-pulses ZC pulses every --pulse-spacing-us on the 4 TX chains, a
          distinct root per pulse, chain and AP (MIMO: H 3x4 per pulse).
RX:       the burst arrives at a known time (5G sync), so no search: matched
          filter at the expected position +-search, per pulse/RX/TX chain
          -> h[pulse, rx, tx] per burst, saved with its exact X410 timestamp.

Outputs: sense_rx.npz (what this AP received), tx_log.jsonl, summary.json.
Process both APs' folders together with sense_doppler.py.

Examples:
  continuous:  python3 x410_two_ap_sense.py --ap A --tx-start 17:00:00 --duration-s 120
  on demand:   ... --session-start 70 --session-len 30      (7 s silent + 3 s session)
  simulation:  python3 x410_two_ap_sense.py --ap B --sim --tx-start-in-s 4 --duration-s 10
"""
import argparse
import copy
import datetime as dt
import json
import queue
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import x410_pss_timed_tx as base  # noqa: E402
from x410_two_ap import CYCLE, ROOTS, Clock, SimTwoAp  # noqa: E402

C0 = 299792458.0


def parse_args():
    p = argparse.ArgumentParser(description="Two-AP bistatic sensing sessions timed by 5G")
    p.add_argument("--ap", choices=("A", "B"), required=True)
    p.add_argument("--tx-start", default=None, help="HH:MM:SS, same on both APs")
    p.add_argument("--tx-start-in-s", type=float, default=None)
    p.add_argument("--duration-s", type=float, default=120.0)
    p.add_argument("--session-period-cycles", type=int, default=100)
    p.add_argument("--session-start", type=int, default=0)
    p.add_argument("--session-len", type=int, default=100, help="100 of 100 = always sensing")
    p.add_argument("--offset-a-ms", type=float, default=2.5)
    p.add_argument("--offset-b-ms", type=float, default=7.5)
    p.add_argument("--n-pulses", type=int, default=8)
    p.add_argument("--pulse-spacing-us", type=float, default=100.0)
    p.add_argument("--guard-us", type=float, default=50.0, help="RX window margin each side")
    p.add_argument("--latency-ns", type=float, default=4000.0, help="Expected TX->air->RX delay")
    p.add_argument("--search-samples", type=int, default=12, help="+- lag search around expected")
    p.add_argument("--grid-offset-ms", type=float, default=0.0)
    p.add_argument("--min-grid-margin-ms", type=float, default=3.0)
    p.add_argument("--args", default="addr=192.168.10.2")
    p.add_argument("--modulos-root", type=Path, default=Path.home() / "syncSense" / "modulos_rx_tx")
    p.add_argument("--out-dir", type=Path, default=None)
    p.add_argument("--sim", action="store_true")
    p.add_argument("--sim-target", default="1.0,-20,-25,15,30",
                   help="(sim) echo: speed m/s, level dB vs direct, AoD deg, AoA deg, sweep deg/s")
    p.add_argument("--rate", type=float, default=15.36e6)
    p.add_argument("--wifi-freq", type=float, default=2.45e9)
    p.add_argument("--tx-gain", type=float, default=20.0)
    p.add_argument("--tx-chans", type=int, nargs="+", default=[0, 1, 2, 3])
    p.add_argument("--amp", type=float, default=0.25)
    p.add_argument("--rx-chans", type=int, nargs="+", default=[0, 1, 3])
    p.add_argument("--rx-ant", default="TX/RX0")
    p.add_argument("--rx-gain", type=float, default=20.0)
    p.add_argument("--zc-len", type=int, default=761)
    p.add_argument("--lookahead-ssb", type=int, default=6)
    p.add_argument("--min-margin-ms", type=float, default=15.0)
    p.add_argument("--track-window", type=int, default=50)
    p.add_argument("--track-min-pts", type=int, default=20)
    p.add_argument("--track-max-resid-ns", type=float, default=300.0)
    p.add_argument("--holdover-ms", type=float, default=200.0)
    p.add_argument("--outlier-ns", type=float, default=1000.0)
    p.add_argument("--nr-chan", type=int, default=2)
    p.add_argument("--nr-ant", default="RX1")
    p.add_argument("--nr-freq", type=float, default=3541.44e6)
    p.add_argument("--nr-gain", type=float, default=32.0)
    p.add_argument("--nr-block-ms", type=float, default=20.0)
    p.add_argument("--nr-overlap-ms", type=float, default=1.0)
    p.add_argument("--force-nid2", type=int, choices=(0, 1, 2), default=0)
    p.add_argument("--force-ibar-ssb", type=int, default=0)
    p.add_argument("--force-ncellid", type=int, default=None)
    p.add_argument("--no-cfo-correction", action="store_true")
    p.add_argument("--cfo-warmup-iters", type=int, default=30)
    p.add_argument("--nr-workers", type=int, default=2)
    a = p.parse_args()
    a.nr_rate = a.rate
    a.detect_thr, a.min_peak_to_side = 8.0, 1.5      # used by base.Burst
    if (a.tx_start is None) == (a.tx_start_in_s is None):
        p.error("give exactly one of --tx-start HH:MM:SS or --tx-start-in-s")
    if a.tx_start is not None:
        h, m, s = (int(x) for x in a.tx_start.split(":"))
        a.tx_start_unix = dt.datetime.now().replace(hour=h, minute=m, second=s, microsecond=0).timestamp()
    else:
        a.tx_start_unix = time.time() + a.tx_start_in_s
    a.partner = "B" if a.ap == "A" else "A"
    a.own_off = a.offset_a_ms if a.ap == "A" else a.offset_b_ms
    a.partner_off = a.offset_b_ms if a.ap == "A" else a.offset_a_ms
    burst_ms = ((a.n_pulses - 1) * a.pulse_spacing_us + a.zc_len / a.rate * 1e6) / 1e3
    if abs(a.offset_a_ms - a.offset_b_ms) < burst_ms + 2 * a.guard_us / 1e3 + 0.5:
        p.error("offsets of A and B too close for the burst length")
    # slot_offset/partner_slot are not used here, but SimTwoAp reads them
    a.slot_offset_ms, a.partner_slot, a.sim_partner_shift = a.partner_off, -1, 0
    return a


def make_burst(args, ap):
    b = copy.copy(args)
    b.zc_roots = ROOTS[ap][:len(args.tx_chans)]
    return base.Burst(b)


def pulse_templates(burst):
    """P[c, k, n]: unit-amplitude ZC of pulse k on TX chain c."""
    P = np.zeros((burst.n_tx, burst.K, burst.N), dtype=np.complex64)
    for c in range(burst.n_tx):
        for k in range(burst.K):
            seg = burst.templates[c][k * burst.S:k * burst.S + burst.N]
            P[c, k] = seg
    return P


def in_session(args, cycle):
    return (cycle % args.session_period_cycles - args.session_start) % args.session_period_cycles < args.session_len


class SimSense(SimTwoAp):
    """Partner bursts at the 100 Hz session times, through a channel = direct
    path + one moving echo (Doppler, AoD at the partner array, AoA here), with
    the CFO between the two APs. Array responses use d = 6.1 cm, random
    per-chain hardware phases (fixed for the run)."""
    CFO_HZ = 320.0

    def __init__(self, args, own_burst, partner_burst):
        super().__init__(args, own_burst, partner_burst)
        v, lvl, aod, aoa, sweep = (float(x) for x in args.sim_target.split(","))
        self.tgt = dict(v=v, amp=10 ** (lvl / 20), aod=aod, aoa=aoa, sweep=sweep)
        self.kd = 2 * np.pi * 0.061 / (C0 / args.wifi_freq)
        rng = np.random.default_rng(7)
        self.hw_tx = np.exp(1j * rng.uniform(0, 2 * np.pi, partner_burst.n_tx))
        self.hw_rx = np.exp(1j * rng.uniform(0, 2 * np.pi, len(args.rx_chans)))
        self.los_aod, self.los_aoa = 30.0, 32.0

    def steer(self, n, deg):
        return np.exp(1j * np.arange(n) * self.kd * np.sin(np.deg2rad(deg)))

    def channel(self, t_s):
        a, n_rx, n_tx = self.args, len(self.args.rx_chans), self.pb.n_tx
        Hlos = np.outer(self.steer(n_rx, self.los_aoa), self.steer(n_tx, self.los_aod))
        tg = self.tgt
        sweep = tg["sweep"] * np.sin(2 * np.pi * t_s / 8.0)          # target walks back and forth
        # Doppler fd(t) = fmax cos(2 pi t / 8): the phase is its integral (fmax 8/(2 pi)) sin(2 pi t / 8)
        fmax = tg["v"] / (C0 / a.wifi_freq) * 1.4
        phase = 2 * np.pi * fmax * 8.0 / (2 * np.pi) * np.sin(2 * np.pi * t_s / 8.0)
        Ht = tg["amp"] * np.exp(1j * phase) * np.outer(
            self.steer(n_rx, tg["aoa"] - sweep / 2), self.steer(n_tx, tg["aod"] + sweep / 2))
        H = Hlos + Ht
        return (self.hw_rx[:, None] * H * self.hw_tx[None, :]) * 10 ** (-80 / 20)

    def _obs_thread(self):
        a, b = self.args, self.pb
        n_rx = len(a.rx_chans)
        while not self.stop_ev.is_set():
            with self.lock:
                ready = [p for p in self.obs_pending if self.now_ticks() > p[1] + p[2]]
                self.obs_pending = [p for p in self.obs_pending if p not in ready]
            for k, t_start, n in ready:
                noise = 10 ** (-80 / 20) * a.amp * 10 ** (-30 / 20)
                y = (noise / np.sqrt(2) * (self.rng.standard_normal((n_rx, n))
                                           + 1j * self.rng.standard_normal((n_rx, n)))).astype(np.complex64)
                # the partner burst this window was armed for starts guard + latency after t_start
                d0 = a.guard_us * 1e-6 * self.R + a.latency_ns * 1e-9 * self.R + 0.37
                t_s = self.unix_true(t_start) - self.unix0
                H = self.channel(t_s)
                i0 = int(np.floor(d0))
                ns = np.arange(b.len)
                cfo = np.exp(2j * np.pi * self.CFO_HZ * (t_s + ns / self.R))
                for c in range(b.n_tx):
                    sig = (b.buf[c] * cfo).astype(np.complex64)
                    for r in range(n_rx):
                        y[r, i0:i0 + b.len] += (H[r, c] * sig[:max(0, min(b.len, n - i0))]).astype(np.complex64)
                self.obs_q.put(dict(k=k, w0=t_start, y=y))
            time.sleep(0.001)


def extract(y, P, S, base_idx, search):
    """Matched filter at the known position: h[lag, pulse, rx, tx] for lags in
    +-search, pick the lag with most energy. y: (n_rx, L)."""
    n_tx, K, N = P.shape
    lags = np.arange(-search, search + 1)
    idx = base_idx + lags[:, None, None] + (np.arange(K) * S)[None, :, None] + np.arange(N)[None, None, :]
    if idx.min() < 0 or idx.max() >= y.shape[1]:
        return None
    seg = y[:, idx]                                    # (rx, lag, K, N)
    h = np.einsum("rlkn,ckn->lkrc", seg, np.conj(P)) / N
    e = np.sum(np.abs(h) ** 2, axis=(1, 2, 3))
    j = int(np.argmax(e))
    floor = np.median(e)
    return h[j], int(lags[j]), float(e[j] / max(floor, 1e-30))


def main():
    args = parse_args()
    R = args.rate
    out_dir = args.out_dir or Path(f"sense_{args.ap}_{time.strftime('%Y%m%d_%H%M%S')}")
    out_dir.mkdir(parents=True, exist_ok=True)
    own_b, part_b = make_burst(args, args.ap), make_burst(args, args.partner)
    P = pulse_templates(part_b)
    trk = base.Tracker(args)
    radio = SimSense(args, own_b, part_b) if args.sim else base.UhdRadio(args, own_b)
    t0txt = dt.datetime.fromtimestamp(args.tx_start_unix).strftime("%H:%M:%S")
    sess = "always" if args.session_len >= args.session_period_cycles else (
        f"cycles {args.session_start}..{args.session_start + args.session_len - 1} of every "
        f"{args.session_period_cycles}")
    print(f"AP {args.ap}: TX at PSS+{args.own_off} ms and +{args.own_off + 10} ms, listens at "
          f"+{args.partner_off}/+{args.partner_off + 10} ms | sessions: {sess} | from {t0txt} for "
          f"{args.duration_s:.0f} s | burst {own_b.K} pulses / {own_b.len / R * 1e3:.2f} ms")
    summary = dict(args={k: (str(v) if isinstance(v, Path) else v) for k, v in vars(args).items()},
                   setup=radio.setup_report, started_unix=time.time(),
                   burst=dict(len=own_b.len, S=own_b.S, N=own_b.N, K=own_b.K,
                              roots_own=own_b.roots, roots_partner=part_b.roots),
                   numbering_method="PROVISIONAL host time (NTP) -> SSB number; timing from 5G PSS")
    summary["nr_lock"] = radio.acquire_nr()

    guard = int(round(args.guard_us * 1e-6 * R))
    lat = args.latency_ns * 1e-9 * R
    n_win = part_b.len + 2 * guard
    base_idx = int(round(guard + lat))
    min_margin = args.min_margin_ms * 1e-3 * R
    L = args.lookahead_ssb
    g = args.grid_offset_ms * 1e-3
    t_end = args.tx_start_unix + args.duration_s

    nr_f = open(out_dir / "nr_events.jsonl", "w")
    tx_f = open(out_dir / "tx_log.jsonl", "w")
    events = {}
    k_actual = {}
    rx_rows = []
    K0, next_n = None, None
    numbering = dict(checks=0, mismatches=0)
    counts = dict(tx_armed=0, tx_late=0, rx_armed=0, rx_late=0, rx_ok=0, rx_fail=0, rx_err=0)
    pool = ThreadPoolExecutor(max_workers=1)
    pending = []
    eid = 0
    state = "SEARCHING"
    radio.phase = "listen"
    radio.start()
    clock = Clock(radio, R)
    t_launch = time.monotonic()
    last_print = last_anchor = time.monotonic()
    print("\n=== Running (Ctrl+C to stop early) ===")

    def gnum(tick):
        u = clock.unix(tick)
        return int(np.floor((u - g) / 0.02)), (u - g) % 0.02

    def work(ev, ob):
        res = extract(ob["y"], P, part_b.S, base_idx, args.search_samples)
        if res is None:
            return dict(ev=ev, ok=False)
        h, lag, q = res
        return dict(ev=ev, ok=True, h=h.astype(np.complex64), lag=lag, q=q, w0=ob["w0"])

    try:
        while True:
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
                nr_f.write(json.dumps(row) + "\n")
            now = radio.now_ticks()
            locked, why = trk.locked(now)
            if state == "SEARCHING" and trk.pts:
                state = "TRACKING"
                print("[TRACKING] first PSS received")
            if state == "TRACKING" and locked:
                ks = sorted(trk.pts)[-trk.min_pts:]
                nums = [gnum(trk.pts[k]) for k in ks]
                offs = {n - k for (n, _), k in zip(nums, ks)}
                ph = np.array([q for _, q in nums]) * 1e3
                margin = float(min(ph.min(), (20 - ph).min()))
                numbering.update(pss_phase_in_grid_ms=round(float(np.median(ph)), 3), margin_ms=round(margin, 3))
                print(f"[NUMBERING] PSS at {np.median(ph):.2f} ms of the 20 ms grid (margin {margin:.2f} ms)")
                if len(offs) != 1 or margin < args.min_grid_margin_ms:
                    sug = (np.median(ph) - 10 + args.grid_offset_ms) % 20
                    raise RuntimeError(f"PSS too close to the grid edge: re-run BOTH APs with "
                                       f"--grid-offset-ms {sug:.0f}")
                K0 = offs.pop()
                state = "LOCKED"
                next_n = trk.k_last + K0 + 1
                print(f"[LOCKED] {trk.ppm():+.3f} ppm, resid {trk.fit[4]:.1f} ns")
            if K0 is not None and time.monotonic() - last_anchor > 10:
                last_anchor = time.monotonic()
                clock.refresh()
                k = trk.k_last
                numbering["checks"] += 1
                if gnum(trk.pts[k])[0] != k + K0:
                    numbering["mismatches"] += 1
                    print("[WARN] numbering check failed")

            # --- scheduler: per SSB n, two TX and two RX events when the cycle is in a session
            if next_n is not None:
                nl = trk.k_last + K0
                while next_n <= nl + L:
                    n = next_n
                    next_n += 1
                    cyc = n // CYCLE
                    T = trk.predict(n - K0)
                    if not in_session(args, cyc):
                        continue
                    for half in (0, 1):
                        for role, off_ms in (("TX", args.own_off), ("RX", args.partner_off)):
                            t_ev = int(round(T + (off_ms + 10 * half) * 1e-3 * R))
                            u = clock.unix(t_ev)
                            if not (args.tx_start_unix <= u < t_end):
                                continue
                            now = radio.now_ticks()
                            ev = dict(eid=eid, n=n, cycle=cyc, half=half, role=role, t_ev=t_ev, unix=u,
                                      T_pred=float(T), ppm=trk.ppm(), margin_ms=(t_ev - now) / R * 1e3)
                            eid += 1
                            locked, why = trk.locked(now)
                            if not locked or t_ev - guard - now < min_margin:
                                ev["state"] = "MISSED_LATE" if locked else f"MISSED_NO_LOCK:{why}"
                                counts["tx_late" if role == "TX" else "rx_late"] += 1
                            elif role == "TX":
                                ev["state"] = "ARMED"
                                radio.send_burst(ev["eid"], t_ev, ev)
                                counts["tx_armed"] += 1
                            else:
                                ev["state"] = "ARMED"
                                radio.arm_observer(ev["eid"], t_ev - guard, n_win)
                                counts["rx_armed"] += 1
                            events[ev["eid"]] = ev
                            if role == "TX":
                                tx_f.write(json.dumps(ev) + "\n")
                    if clock.unix(int(T)) > t_end + 0.5:
                        raise StopIteration

            while True:
                try:
                    ob = radio.obs_q.get_nowait()
                except queue.Empty:
                    break
                ev = events.get(ob["k"])
                if ev is None:
                    continue
                if "error" in ob:
                    counts["rx_err"] += 1
                    ev["obs_error"] = ob["error"]
                    continue
                pending.append(pool.submit(work, ev, ob))
            still = []
            for f in pending:
                if f.done():
                    r = f.result()
                    if r["ok"]:
                        counts["rx_ok"] += 1
                        rx_rows.append(r)
                    else:
                        counts["rx_fail"] += 1
                else:
                    still.append(f)
            pending = still

            if time.monotonic() - last_print > 5 and K0 is not None:
                last_print = time.monotonic()
                q = [r["q"] for r in rx_rows[-100:]]
                lags = [r["lag"] for r in rx_rows[-100:]]
                msg = (f"  TX {counts['tx_armed']} (late {counts['tx_late']}) | RX {counts['rx_ok']} ok, "
                       f"{counts['rx_fail']} fail, late {counts['rx_late']}, err {counts['rx_err']}")
                if q:
                    msg += f" | peak/floor {np.median(q):.0f} | lag median {np.median(lags):+.0f} samp"
                msg += f" | {trk.ppm():+.3f} ppm | 5G ovf {radio.stats['nr_overflows']}"
                print(msg)
            if K0 is None and time.monotonic() - t_launch > 90:
                raise RuntimeError(f"Tracker did not lock in 90 s ({why})")
            time.sleep(0.001)
    except StopIteration:
        pass
    except KeyboardInterrupt:
        print("\nInterrupted: writing results")
    finally:
        t_wait = time.monotonic() + 1.0
        while time.monotonic() < t_wait:
            try:
                ob = radio.obs_q.get(timeout=0.05)
            except queue.Empty:
                continue
            ev = events.get(ob["k"])
            if ev is not None and "error" not in ob:
                pending.append(pool.submit(work, ev, ob))
        pool.shutdown(wait=True)
        for f in pending:
            r = f.result()
            if r["ok"]:
                counts["rx_ok"] += 1
                rx_rows.append(r)
        radio.stop()
        while not radio.nr_q.empty():
            row = radio.nr_q.get()
            if row.get("valid"):
                k_actual.setdefault(trk.k_of(row["event_device_ticks"]), row["event_device_ticks"])
            nr_f.write(json.dumps(row) + "\n")
        nr_f.close()
        tx_f.close()

    rx_rows.sort(key=lambda r: r["ev"]["t_ev"])
    if rx_rows:
        ev = [r["ev"] for r in rx_rows]
        T_act = np.array([k_actual.get(e["n"] - K0, np.nan) for e in ev], dtype=float)
        np.savez_compressed(
            out_dir / "sense_rx.npz",
            h=np.stack([r["h"] for r in rx_rows]),              # (burst, pulse, rx, tx)
            lag=np.array([r["lag"] for r in rx_rows]),
            q=np.array([r["q"] for r in rx_rows]),
            t_ev=np.array([e["t_ev"] for e in ev], dtype=np.int64),
            T_pred=np.array([e["T_pred"] for e in ev]), T_actual=T_act,
            n=np.array([e["n"] for e in ev], dtype=np.int64), cycle=np.array([e["cycle"] for e in ev]),
            half=np.array([e["half"] for e in ev]), unix=np.array([e["unix"] for e in ev]),
            ppm=np.array([e["ppm"] for e in ev]),
            rate=R, pulse_spacing=part_b.S, base_idx=base_idx, rx_chans=np.array(args.rx_chans),
            tx_chans=np.array(args.tx_chans), wifi_freq=args.wifi_freq, ap=args.ap)
    ev_all = list(events.values())
    tx_ev = [e for e in ev_all if e["role"] == "TX"]
    ack = sum(1 for e in radio.async_events if e["event"] == "burst_ack")
    bad = {}
    for e in radio.async_events:
        if e["event"] != "burst_ack":
            bad[e["event"]] = bad.get(e["event"], 0) + 1
    m = [e["margin_ms"] for e in tx_ev if e["state"] == "ARMED"]
    res = dict(counts=counts, async_ack=ack, async_other=bad, numbering=numbering, radio_stats=radio.stats,
               tracker=dict(ppm=trk.ppm(), resid_ns=trk.fit[4] if trk.fit else None),
               margin_ms=base.stats_ns(m), lag=base.stats_ns([r["lag"] for r in rx_rows]),
               peak_over_floor_median=float(np.median([r["q"] for r in rx_rows])) if rx_rows else None)
    summary["results"] = res
    summary["finished_unix"] = time.time()
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, default=float))
    print("\n=== Summary ===")
    print(f"AP {args.ap}: {counts} | TX acks {ack} {bad} | numbering {numbering}")
    print(f"margin [ms] {res['margin_ms']} | lag [samples] {res['lag']} | peak/floor median "
          f"{res['peak_over_floor_median']} | tracker {res['tracker']} | radio {radio.stats}")
    expected = counts["rx_armed"]
    ok = (counts["tx_late"] == 0 and counts["rx_late"] == 0 and not bad and expected
          and counts["rx_ok"] >= 0.99 * expected and numbering["mismatches"] == 0)
    print(f"Verdict: {'PASS' if ok else 'CHECK'} (0 late, no TX errors, >=99 % windows extracted, numbering OK)")
    print(f"Saved {out_dir}/ (sense_rx.npz, tx_log.jsonl, nr_events.jsonl, summary.json)")


if __name__ == "__main__":
    main()
