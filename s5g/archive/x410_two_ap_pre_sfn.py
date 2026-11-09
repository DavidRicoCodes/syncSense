#!/usr/bin/env python3
"""
Two-AP smoke: X410 AP A and X410 AP B, independent clocks, no backhaul,
coordinated only by the same commercial 5G cell.

Run the same script on both servers (--ap A on one, --ap B on the other).

  5G      ch2 RX1, continuous: PSS start in local X410 ticks -> tracker
          (fine timing, from the 5G only)
  numbering  global SSB number n shared by both APs, so they agree on which
          SSB is slot 0 of every 5-SSB (100 ms) cycle.
          PROVISIONAL: derived from host time (NTP) by rounding the PSS time to
          the 20 ms grid. To be replaced by the SFN decoded from PBCH/MIB.
  agenda  hard-coded, provisioned once: AP A transmits in slot --slot-a,
          AP B in slot --slot-b (n % 5), at PSS + --slot-offset-ms.
  TX      own slot: 4 TX chains on TX/RX0, 10 ZC pulses (distinct roots per AP
          and chain), timed command armed --lookahead-ssb SSB ahead; late or
          not locked -> silent
  RX      partner slot: timed capture on the 3 WiFi RX chains (TX/RX0 of ch0,
          ch1, ch3); every other non-own slot: sentinel capture (first RX
          chain only) to catch a numbering mismatch (partner burst in an
          unexpected slot)

Inter-AP sync error (measured by the receiving AP, in its own clock):
  sync_err_ns = t_obs(partner burst) - (T_actual_own(n) + slot_offset)
  constant part = TX pipeline of the partner + RX pipeline here + air path +
  difference of the 5G propagation gNB->A vs gNB->B; jitter = what matters.

Both APs start transmitting at the same wall-clock time (--tx-start HH:MM:SS),
listen before and after, and stop at --tx-start + --duration-s.

Requires x410_pss_timed_tx.py and x410_2x2_5g.py in the same folder.

Examples (same --tx-start on both, about a minute ahead):
  python3 x410_two_ap.py --ap A --tx-start 16:30:00 --duration-s 180
  python3 x410_two_ap.py --ap B --tx-start 16:30:00 --duration-s 180
  python3 x410_two_ap.py --ap A --sim --tx-start-in-s 5 --duration-s 20
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

CYCLE = 5  # SSB per cycle (100 ms with 20 ms SSB periodicity)
ROOTS = {"A": [25, 34, 43, 52], "B": [113, 122, 131, 140]}


def parse_args():
    p = argparse.ArgumentParser(description="Two-AP X410 smoke coordinated by 5G")
    p.add_argument("--ap", choices=("A", "B"), required=True)
    p.add_argument("--slot-a", type=int, default=1, help="Slot (n %% 5) where AP A transmits")
    p.add_argument("--slot-b", type=int, default=3, help="Slot (n %% 5) where AP B transmits")
    p.add_argument("--tx-start", default=None, help="Local wall-clock time HH:MM:SS to start TX (same on both)")
    p.add_argument("--tx-start-in-s", type=float, default=None, help="Alternative: start TX this long after launch")
    p.add_argument("--duration-s", type=float, default=180.0, help="TX duration")
    p.add_argument("--grid-offset-ms", type=float, default=0.0,
                   help="Offset of the 20 ms host-time grid used for SSB numbering (same on both APs)")
    p.add_argument("--min-grid-margin-ms", type=float, default=3.0,
                   help="Abort if the PSS is closer than this to a grid boundary (numbering could differ)")
    p.add_argument("--no-sentinel", action="store_true", help="Only listen in the partner slot")
    p.add_argument("--args", default="addr=192.168.10.2")
    p.add_argument("--modulos-root", type=Path, default=Path.home() / "syncSense" / "modulos_rx_tx")
    p.add_argument("--out-dir", type=Path, default=None)
    p.add_argument("--sim", action="store_true")
    p.add_argument("--sim-partner-shift", type=int, default=0,
                   help="(sim) partner numbering off by this many SSB, to test the sentinel")
    p.add_argument("--rate", type=float, default=15.36e6)
    # WiFi
    p.add_argument("--wifi-freq", type=float, default=2.45e9)
    p.add_argument("--tx-gain", type=float, default=20.0)
    p.add_argument("--tx-chans", type=int, nargs="+", default=[0, 1, 2, 3])
    p.add_argument("--amp", type=float, default=0.25)
    p.add_argument("--rx-chans", type=int, nargs="+", default=[0, 1, 3])
    p.add_argument("--rx-ant", default="TX/RX0", help="Same antennas for TX and RX (TDD)")
    p.add_argument("--rx-gain", type=float, default=20.0)
    p.add_argument("--rx-guard-ms", type=float, default=0.25)
    p.add_argument("--detect-thr", type=float, default=8.0)
    p.add_argument("--min-peak-to-side", type=float, default=1.5)
    p.add_argument("--zc-len", type=int, default=761)
    p.add_argument("--n-pulses", type=int, default=10)
    p.add_argument("--pulse-spacing-us", type=float, default=200.0)
    # scheduler / tracker
    p.add_argument("--lookahead-ssb", type=int, default=6)
    p.add_argument("--slot-offset-ms", type=float, default=5.0)
    p.add_argument("--min-margin-ms", type=float, default=20.0)
    p.add_argument("--track-window", type=int, default=50)
    p.add_argument("--track-min-pts", type=int, default=20)
    p.add_argument("--track-max-resid-ns", type=float, default=300.0)
    p.add_argument("--holdover-ms", type=float, default=200.0)
    p.add_argument("--outlier-ns", type=float, default=1000.0)
    # 5G
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
    if a.slot_a == a.slot_b or not (0 <= a.slot_a < CYCLE and 0 <= a.slot_b < CYCLE):
        p.error("--slot-a and --slot-b must be different values in 0..4")
    if (a.tx_start is None) == (a.tx_start_in_s is None):
        p.error("give exactly one of --tx-start HH:MM:SS or --tx-start-in-s")
    if a.tx_start is not None:
        h, m, s = (int(x) for x in a.tx_start.split(":"))
        t = dt.datetime.now().replace(hour=h, minute=m, second=s, microsecond=0)
        a.tx_start_unix = t.timestamp()
    else:
        a.tx_start_unix = time.time() + a.tx_start_in_s
    a.partner = "B" if a.ap == "A" else "A"
    a.own_slot = a.slot_a if a.ap == "A" else a.slot_b
    a.partner_slot = a.slot_b if a.ap == "A" else a.slot_a
    if a.nr_chan in a.rx_chans:
        p.error("the 5G channel cannot also be a WiFi RX channel")
    return a


def make_burst(args, ap):
    b = copy.copy(args)
    b.zc_roots = ROOTS[ap][:len(args.tx_chans)]
    return base.Burst(b)


class Clock:
    """Mapping X410 ticks -> host unix time (NTP), refreshed periodically.
    Only used to number SSBs (ms accuracy needed); fine timing is the 5G."""

    def __init__(self, radio, rate):
        self.radio, self.R = radio, rate
        self.refresh()

    def refresh(self):
        best = None
        for _ in range(15):
            h0 = time.time_ns()
            d = self.radio.now_ticks()
            h1 = time.time_ns()
            if best is None or h1 - h0 < best[2]:
                best = (d, (h0 + h1) // 2, h1 - h0)
        self.tick, self.unix_ns, self.rtt_ns = best

    def unix(self, tick):
        return (self.unix_ns + (tick - self.tick) / self.R * 1e9) / 1e9


class SimTwoAp(base.SimRadio):
    """Own AP simulated as in SimRadio; the partner transmits in its slots at
    the true PSS + offset (with its own small jitter), seen here after a fixed
    TX->air->RX delay. Global numbering uses the true host-time mapping."""
    PARTNER_JITTER_NS, AIR_LATENCY_NS = 25.0, 3983.0

    def __init__(self, args, own_burst, partner_burst):
        super().__init__(args, own_burst)
        self.pb = partner_burst
        self.unix0 = time.time()
        self.first = None  # true PSS k = first + k * per (set by the 5G thread)
        self.per = self.R * args.nr_block_ms * 1e-3 * (1 + self.PPM * 1e-6)

    def unix_true(self, tick):
        return self.unix0 + (tick - self.base) / self.R

    def _nr_thread(self):
        per = self.per
        first = self.first = self.now_ticks() + 0.003 * self.R
        k = 0
        while not self.stop_ev.is_set():
            T = first + k * per
            deliver = T + (self.DELAY_MS + self.rng.uniform(-5, 5)) * 1e-3 * self.R
            while self.now_ticks() < deliver and not self.stop_ev.is_set():
                time.sleep(0.001)
            self.stats["nr_blocks"] += 1
            meas = int(round(T + self.rng.normal(0, self.JITTER_NS * 1e-9 * self.R)))
            self.nr_q.put(dict(phase=self.phase, valid=True, event_device_ticks=meas,
                               pss_metric=0.95, sss_metric=0.98))
            k += 1

    def _obs_thread(self):
        a, b = self.args, self.pb
        off = a.slot_offset_ms * 1e-3 * self.R
        g = a.grid_offset_ms * 1e-3
        n_rx = len(a.rx_chans)
        gains = 10 ** (-65 / 20) * np.exp(1j * self.rng.uniform(0, 2 * np.pi, (n_rx, b.n_tx)))
        while not self.stop_ev.is_set():
            with self.lock:
                ready = [p for p in self.obs_pending if self.now_ticks() > p[1] + p[2]]
                self.obs_pending = [p for p in self.obs_pending if p not in ready]
            for k, t_start, n in ready:
                k0 = int(np.floor((t_start - self.first) / self.per))
                Ts = [self.first + j * self.per for j in range(k0 - 2, k0 + 2) if j >= 0]
                noise = 10 ** (-65 / 20) * a.amp * 10 ** (-self.SNR_DB / 20)
                y = (noise / np.sqrt(2) * (self.rng.standard_normal((n_rx, n))
                                           + 1j * self.rng.standard_normal((n_rx, n)))).astype(np.complex64)
                M = base.next_fast_len(n + b.len)
                f = np.fft.fftfreq(M)
                tx_on = self.unix_true(t_start) < a.tx_start_unix + a.duration_s and \
                    self.unix_true(t_start) >= a.tx_start_unix
                for T in Ts:
                    gn = int(np.floor((self.unix_true(T) - g) / 0.02)) + a.sim_partner_shift
                    if not tx_on or gn % CYCLE != a.partner_slot:
                        continue
                    t_tx = T + off + self.rng.normal(0, self.PARTNER_JITTER_NS * 1e-9 * self.R)
                    d0 = t_tx + self.AIR_LATENCY_NS * 1e-9 * self.R - t_start
                    if not (-b.len < d0 < n):
                        continue
                    for c in range(b.n_tx):
                        x = np.zeros(M, dtype=complex)
                        x[:b.len] = b.buf[c]
                        X = np.fft.fft(x) * np.exp(-2j * np.pi * f * d0)
                        sig = np.fft.ifft(X)[:n]
                        for r in range(n_rx):
                            y[r] += (gains[r, c] * sig).astype(np.complex64)
                self.obs_q.put(dict(k=k, w0=t_start, y=y))
            time.sleep(0.002)


SENTINEL_MIN_SIDE = 2.5     # a stray burst must be unambiguous...
SENTINEL_POS_TOL = 64       # ...and arrive where a real burst would (samples from guard + ~4 us)


def detect_window(rec, ob, pburst, rx_chans, sentinel, guard=0):
    obs = {}
    chans = rx_chans[:1] if sentinel else rx_chans
    for r, ch in enumerate(chans):
        det = pburst.detect(ob["y"][r])
        for d in det:
            if d.get("found") and sentinel and (
                    d["peak_to_side"] < SENTINEL_MIN_SIDE
                    or abs(d["start"] - guard - 61) > SENTINEL_POS_TOL):
                d.update(found=False, reason="sentinel: ambiguous or off-position", rejected=True)
            if d.get("found"):
                d["t_obs"] = ob["w0"] + d["start"]
        obs[str(ch)] = det
    # found_links first: the main loop treats "obs" in rec as "detection finished"
    rec["found_links"] = sum(1 for det in obs.values() for d in det if d.get("found"))
    rec["obs"] = obs


def main():
    args = parse_args()
    R = args.rate
    out_dir = args.out_dir or Path(f"two_ap_{args.ap}_{time.strftime('%Y%m%d_%H%M%S')}")
    out_dir.mkdir(parents=True, exist_ok=True)
    own_b, part_b = make_burst(args, args.ap), make_burst(args, args.partner)
    roots = [u for r in own_b.roots + part_b.roots for u in r]
    if len(set(roots)) != len(roots):
        raise SystemExit("ZC roots of the two APs overlap")
    trk = base.Tracker(args)
    radio = SimTwoAp(args, own_b, part_b) if args.sim else base.UhdRadio(args, own_b)
    start_txt = dt.datetime.fromtimestamp(args.tx_start_unix).strftime("%H:%M:%S")
    print(f"AP {args.ap}: TX in slot {args.own_slot}, listens to AP {args.partner} in slot "
          f"{args.partner_slot} (cycle of {CYCLE} SSB), offset {args.slot_offset_ms} ms | TX from "
          f"{start_txt} for {args.duration_s:.0f} s | WiFi RX ch{args.rx_chans} on {args.rx_ant}")
    if args.tx_start_unix - time.time() < 45 and not args.sim:
        print("[WARN] TX start is less than ~45 s away: the 5G lock may not be ready in time; "
              "turns before lock are skipped")
    summary = dict(args={k: (str(v) if isinstance(v, Path) else v) for k, v in vars(args).items()},
                   setup=radio.setup_report, started_unix=time.time(),
                   numbering_method="PROVISIONAL host time (NTP) rounded to the 20 ms grid; to be "
                                    "replaced by the SFN decoded from PBCH/MIB")
    summary["nr_lock"] = radio.acquire_nr()

    off = args.slot_offset_ms * 1e-3 * R
    guard = int(round(args.rx_guard_ms * 1e-3 * R))
    n_win = part_b.len + 2 * guard
    min_margin = args.min_margin_ms * 1e-3 * R
    L = args.lookahead_ssb
    g = args.grid_offset_ms * 1e-3

    nr_f = open(out_dir / "nr_events.jsonl", "w")
    recs, k_actual = {}, {}
    clock = None
    K0 = None
    numbering = dict(checks=0, mismatches=0)
    next_n = None
    state = "SEARCHING"
    pool = ThreadPoolExecutor(max_workers=1)
    pending = []
    t_end_unix = args.tx_start_unix + args.duration_s
    radio.phase = "A_listen_only"
    radio.start()
    clock = Clock(radio, R)
    t_launch = time.monotonic()
    last_print, last_anchor = time.monotonic(), time.monotonic()
    print("\n=== Running (Ctrl+C to stop early) ===")

    def gnum(tick):
        u = clock.unix(tick)
        ph = (u - g) % 0.02
        return int(np.floor((u - g) / 0.02)), ph

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
                        if K0 is not None and (k + K0) in recs:
                            recs[k + K0]["T_actual"] = row["event_device_ticks"]
                nr_f.write(json.dumps(row) + "\n")
            now = radio.now_ticks()
            locked, why = trk.locked(now)
            if state == "SEARCHING" and trk.pts:
                state = "TRACKING"
                print(f"[{state}] first PSS received")
            if state == "TRACKING" and locked:
                # global numbering from the last PSS (provisional, host time)
                ks = sorted(trk.pts)[-trk.min_pts:]
                nums = [gnum(trk.pts[k]) for k in ks]
                offs = [n - k for (n, _), k in zip(nums, ks)]
                phases = np.array([ph for _, ph in nums]) * 1e3
                margin = float(min(np.min(phases), np.min(20 - phases)))
                summary["numbering"] = numbering
                numbering.update(pss_phase_in_grid_ms=round(float(np.median(phases)), 3),
                                 margin_ms=round(margin, 3), host_rtt_us=clock.rtt_ns / 1e3,
                                 grid_offset_ms=args.grid_offset_ms)
                print(f"[NUMBERING] PSS sits at {np.median(phases):.2f} ms of the 20 ms host-time grid "
                      f"(margin to the edge {margin:.2f} ms, grid offset {args.grid_offset_ms} ms)")
                if len(set(offs)) != 1 or margin < args.min_grid_margin_ms:
                    sug = (np.median(phases) - 10 + args.grid_offset_ms) % 20
                    raise RuntimeError(
                        f"PSS too close to the numbering grid edge ({margin:.2f} ms): both APs could "
                        f"number SSBs differently. Re-run BOTH APs with --grid-offset-ms {sug:.0f}")
                K0 = offs[0]
                state = "LOCKED"
                kl = trk.k_last
                next_n = kl + K0 + 1
                print(f"[{state}] tracker {trk.ppm():+.3f} ppm, resid {trk.fit[4]:.1f} ns | latest SSB "
                      f"n={kl + K0} (slot {(kl + K0) % CYCLE})")

            if K0 is not None and time.monotonic() - last_anchor > 10:
                last_anchor = time.monotonic()
                clock.refresh()
                k = trk.k_last
                n_chk, ph = gnum(trk.pts[k])
                numbering["checks"] += 1
                if n_chk != k + K0:
                    numbering["mismatches"] += 1
                    print(f"[WARN] numbering check: SSB k={k} -> n={n_chk}, expected {k + K0} "
                          f"(phase {ph*1e3:.2f} ms)")

            # --- scheduler
            if next_n is not None:
                nl = trk.k_last + K0
                while next_n <= nl:  # passed without being armed
                    if next_n % CYCLE in (args.own_slot, args.partner_slot):
                        recs[next_n] = dict(n=next_n, role="TX" if next_n % CYCLE == args.own_slot
                                            else "RX", state="MISSED_NOT_ARMED")
                    next_n += 1
                while next_n <= nl + L:
                    n = next_n
                    next_n += 1
                    slot = n % CYCLE
                    if slot == args.own_slot:
                        role = "TX"
                    elif slot == args.partner_slot:
                        role = "RX"
                    elif not args.no_sentinel:
                        role = "SENTINEL"
                    else:
                        continue
                    now = radio.now_ticks()
                    locked, why = trk.locked(now)
                    T = trk.predict(n - K0)
                    t0 = int(round(T + off))
                    u0 = clock.unix(t0)
                    phase = "B_tx_window" if args.tx_start_unix <= u0 < t_end_unix else "A_listen_only"
                    radio.phase = phase
                    rec = dict(n=n, slot=slot, cycle=n // CYCLE, role=role, phase=phase,
                               k_local=n - K0, T_pred=float(T), t_slot=t0, unix_slot=u0,
                               margin_ms=(t0 - now) / R * 1e3, track_resid_ns=trk.fit[4],
                               track_ppm=trk.ppm())
                    if (n - K0) in k_actual:
                        rec["T_actual"] = k_actual[n - K0]
                    if role == "TX" and phase != "B_tx_window":
                        rec["state"] = "IDLE_OUTSIDE_TX_WINDOW"
                    elif not locked:
                        rec["state"] = f"MISSED_NO_LOCK:{why}"
                    elif t0 - guard - now < min_margin:
                        rec["state"] = "MISSED_LATE"
                    elif role == "TX":
                        rec["state"] = "ARMED"
                        radio.send_burst(n, t0, rec)
                    else:
                        rec["state"] = "ARMED"
                        radio.arm_observer(n, t0 - guard, n_win)
                    recs[n] = rec
                    if u0 > t_end_unix + 1.0:
                        raise StopIteration

            # --- RX windows -> detection in a worker
            while True:
                try:
                    ob = radio.obs_q.get_nowait()
                except queue.Empty:
                    break
                rec = recs.get(ob["k"])
                if rec is None:
                    continue
                if "error" in ob:
                    rec["obs_error"] = ob["error"]
                    continue
                pending.append(pool.submit(detect_window, rec, ob, part_b, args.rx_chans,
                                           rec["role"] == "SENTINEL", guard))
            pending = [f for f in pending if not f.done()]

            if time.monotonic() - last_print > 5 and K0 is not None:
                last_print = time.monotonic()
                rx = [r for r in recs.values() if r["role"] == "RX" and "obs" in r]
                tx = [r for r in recs.values() if r["role"] == "TX" and r["state"] == "ARMED"]
                sen = [r for r in recs.values() if r["role"] == "SENTINEL" and "obs" in r]
                heard = [r for r in rx if r["found_links"]]
                stray = [r for r in sen if r["found_links"]]
                r0 = str(args.rx_chans[0])
                se = [r["obs"][r0][0]["t_obs"] - (r["T_actual"] + off) for r in heard
                      if r["obs"][r0][0].get("found") and "T_actual" in r]
                msg = (f"  [{radio.phase}] TX sent {len(tx)} | partner windows {len(rx)}, heard "
                       f"{len(heard)} | sentinel hits {len(stray)}")
                if len(se) > 1:
                    se = np.array(se[-50:]) / R * 1e9
                    msg += f" | inter-AP offset {np.median(se):.0f} ns, std {np.std(se):.1f} ns"
                msg += (f" | {trk.ppm():+.3f} ppm | 5G ovf {radio.stats['nr_overflows']} | "
                        f"numbering mismatches {numbering['mismatches']}")
                print(msg)
            if K0 is None and time.monotonic() - t_launch > 90:
                raise RuntimeError(f"Tracker did not lock in 90 s ({why})")
            time.sleep(0.002)
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
            rec = recs.get(ob["k"])
            if rec is not None and "error" not in ob:
                pending.append(pool.submit(detect_window, rec, ob, part_b, args.rx_chans,
                                           rec["role"] == "SENTINEL", guard))
        pool.shutdown(wait=True)
        radio.stop()
        while not radio.nr_q.empty():
            row = radio.nr_q.get()
            if row.get("valid"):
                k_actual.setdefault(trk.k_of(row["event_device_ticks"]), row["event_device_ticks"])
            nr_f.write(json.dumps(row) + "\n")
        nr_f.close()

    # ---------------- per record ----------------
    for n, rec in recs.items():
        if "T_actual" not in rec and rec.get("k_local") in k_actual:
            rec["T_actual"] = k_actual[rec["k_local"]]
        if "T_pred" in rec and "T_actual" in rec:
            rec["pred_err_ns"] = (rec["T_pred"] - rec["T_actual"]) / R * 1e9
        for det in (rec.get("obs") or {}).values():
            for d in det:
                if d.get("found") and "T_actual" in rec:
                    d["sync_err_ns"] = (d["t_obs"] - rec["T_actual"] - off) / R * 1e9
        if rec.get("state") == "ARMED":
            rec["state"] = "EXECUTED" if rec["role"] == "TX" else (
                "HEARD" if rec.get("found_links") else "NOT_HEARD")
    with open(out_dir / "records.jsonl", "w") as f:
        for n in sorted(recs):
            f.write(json.dumps(recs[n], default=float) + "\n")

    # ---------------- summary ----------------
    def states(rs):
        out = {}
        for r in rs:
            s = str(r["state"]).split(":")[0]
            out[s] = out.get(s, 0) + 1
        return out

    ev = {}
    for e in radio.async_events:
        ev[e["event"]] = ev.get(e["event"], 0) + 1
    res = dict(async_events=ev, radio_stats=radio.stats, numbering=numbering,
               tracker=dict(ppm=trk.ppm(), resid_ns=trk.fit[4] if trk.fit else None,
                            outliers=trk.outliers, duplicates=trk.dups))
    nr_rows = [json.loads(line) for line in open(out_dir / "nr_events.jsonl")]
    for ph in ("A_listen_only", "B_tx_window"):
        rows = [r for r in nr_rows if r.get("phase") == ph]
        v = [r for r in rows if r.get("valid")]
        res[f"nr_{ph}"] = dict(blocks=len(rows), valid_pct=round(100 * len(v) / len(rows), 2) if rows else None)
    inwin = [r for r in recs.values() if r.get("phase") == "B_tx_window"]
    tx = [r for r in inwin if r["role"] == "TX"]
    rx = [r for r in inwin if r["role"] == "RX"]
    sen = [r for r in recs.values() if r["role"] == "SENTINEL" and "obs" in r]
    res["tx"] = dict(turns=len(tx), states=states(tx),
                     margin_ms=base.stats_ns([r.get("margin_ms") for r in tx if r["state"] == "EXECUTED"]),
                     pred_err_ns=base.stats_ns([r.get("pred_err_ns") for r in tx]))
    res["rx_partner"] = dict(windows=len(rx), states=states(rx))
    res["sentinel"] = dict(windows=len(sen), hits=[(r["n"], r["slot"]) for r in sen if r["found_links"]][:50],
                           n_hits=sum(1 for r in sen if r["found_links"]))
    links = {}
    for ch in args.rx_chans:
        for c, tc in enumerate(args.tx_chans):
            ds = [r["obs"][str(ch)][c] for r in rx if r.get("obs") and r["obs"][str(ch)][c].get("found")]
            se = np.array([d["sync_err_ns"] for d in ds if "sync_err_ns" in d])
            good = se[np.abs(se - np.median(se)) <= 1000] if len(se) else se
            links[f"rx{ch}_partner_tx{tc}"] = dict(
                observed=len(ds), of=len(rx), outliers=int(len(se) - len(good)),
                sync_err_ns=base.stats_ns(good) if len(good) else None,
                level_dbfs=round(float(np.mean([d["level_dbfs"] for d in ds])), 2) if ds else None)
    res["links"] = links
    summary["results"] = res
    summary["finished_unix"] = time.time()
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, default=float))

    print("\n=== Summary ===")
    print(f"AP {args.ap} | numbering {numbering} | tracker {res['tracker']}")
    print(f"TX async {ev} | radio {radio.stats}")
    print(f"5G valid: listen-only {res['nr_A_listen_only']['valid_pct']} % | "
          f"TX window {res['nr_B_tx_window']['valid_pct']} %")
    print(f"Own TX (slot {args.own_slot}) in window: {res['tx']['turns']} {res['tx']['states']} | "
          f"margin {res['tx']['margin_ms']}")
    print(f"Partner windows (slot {args.partner_slot}) in window: {res['rx_partner']['windows']} "
          f"{res['rx_partner']['states']}")
    print(f"Sentinel windows: {res['sentinel']['windows']}, partner heard there: "
          f"{res['sentinel']['n_hits']} {res['sentinel']['hits'][:10]}")
    print("Inter-AP sync error per link (partner burst vs own PSS + offset), ns:")
    for k, v in links.items():
        s = v["sync_err_ns"]
        print(f"   {k}: observed {v['observed']}/{v['of']}, outliers {v['outliers']}, level "
              f"{v['level_dbfs']} dBFS" + (f", mean {s['mean']:.1f} std {s['std']:.1f} "
                                           f"p99 {s['p99_abs_dev']:.1f}" if s else ""))
    heard = res["rx_partner"]["states"].get("HEARD", 0)
    obs_ok = rx and heard >= 0.99 * len(rx)
    std_ok = all(v["sync_err_ns"] is None or v["sync_err_ns"]["std"] < 200 for v in links.values())
    late = res["tx"]["states"].get("MISSED_LATE", 0) + ev.get("time_error", 0) + ev.get("underflow", 0)
    ok = obs_ok and std_ok and late == 0 and res["sentinel"]["n_hits"] == 0 and numbering["mismatches"] == 0
    print(f"\nVerdict: {'PASS' if ok else 'CHECK'} (partner heard in >=99 % of its slots, 0 sentinel hits, "
          f"0 late, 0 numbering mismatches, inter-AP std < 200 ns)")
    print(f"Saved {out_dir}/ (summary.json, records.jsonl, nr_events.jsonl)")


if __name__ == "__main__":
    main()
