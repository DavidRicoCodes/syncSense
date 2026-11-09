#!/usr/bin/env python3
"""
Baselines WITHOUT 5G coordination for the two-AP bistatic sensing comparison
(docs/BF_MULTI_AP_TESTBED_STATUS.md, section 10.2). Same radio, bursts, extraction
and output format as x410_two_ap_sense.py; only the timing differs.

  --mode freerun --rx schedule   B0: each AP transmits every 5 ms on its OWN clock
                                 (phase --phase-ms after --tx-start, period error
                                 --period-ppm) and listens where the agreed schedule
                                 says (partner offset - own offset), also on its own
                                 clock. Nothing aligns the two APs.
  --mode freerun --rx track      B1: same transmitter; the receiver searches the
                                 partner's bursts with wide windows, then follows them
                                 (linear fit of the arrivals, narrow windows at the
                                 predicted time). It never moves its own TX, so
                                 partner bursts that fall on its own TX are lost.
  --mode follow                  B2 (follower, run on B): no TX until it has found
                                 the leader; then it transmits --follow-offset-ms after
                                 each predicted leader burst (timing from WiFi instead
                                 of 5G). The leader (A) runs --mode freerun --rx schedule.

The 5G chain still runs and is logged (nr_events.jsonl) as ground truth only; it is
never used for scheduling here.

  python3 x410_two_ap_baseline.py --ap A --mode freerun --rx track --tx-start 15:30:00 ...
"""
import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import x410_two_ap_sense as S  # noqa: E402
from x410_two_ap_sense import base  # noqa: E402
from x410_two_ap import Clock  # noqa: E402


def parse_args():
    pre = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    pre.add_argument("--mode", choices=("freerun", "follow"), required=True)
    pre.add_argument("--rx", choices=("schedule", "track"), default="track",
                     help="freerun receiver: agreed schedule on own clock (B0) or search-and-follow (B1)")
    pre.add_argument("--period-ppm", type=float, default=0.0,
                     help="Own schedule period error (emulates a WiFi crystal, e.g. +10)")
    pre.add_argument("--phase-ms", type=float, default=None,
                     help="Own first burst this long after --tx-start (default: random in [0, period))")
    pre.add_argument("--acq-every", type=int, default=20,
                     help="While searching, one search window every this many periods (20 = 10 per s)")
    pre.add_argument("--acq-win-ms", type=float, default=1.2,
                     help="Search window length; successive windows slide over the period")
    pre.add_argument("--acq-max-pending", type=int, default=4,
                     help="Do not open a new search window while this many are armed or being processed")
    pre.add_argument("--track-lost", type=int, default=40, help="Consecutive misses -> search again")
    pre.add_argument("--track-window", type=int, default=50, help="Arrivals in the linear fit")
    pre.add_argument("--detect-q", type=float, default=20.0, help="peak/floor to accept a burst")
    pre.add_argument("--follow-offset-ms", type=float, default=None,
                     help="Follower TX after the leader burst (default: own - partner offset, i.e. 2.5 ms)")
    pre.add_argument("--turnaround-us", type=float, default=20.0, help="Own TX keep-out for RX windows")
    pre.add_argument("--sim-partner", choices=("freerun", "follower"), default="freerun")
    pre.add_argument("--sim-partner-ppm", type=float, default=0.0)
    pre.add_argument("--sim-partner-phase-ms", type=float, default=2.6)
    b, rest = pre.parse_known_args()
    sys.argv = [sys.argv[0]] + rest
    a = S.parse_args()
    for k, v in vars(b).items():
        setattr(a, k, v)
    if a.mode == "follow":
        a.rx = "track"
    if a.follow_offset_ms is None:
        a.follow_offset_ms = (a.own_off - a.partner_off) % a.burst_period_ms
    if a.phase_ms is None:
        a.phase_ms = float(np.random.default_rng().uniform(0, a.burst_period_ms))
    return a


class PartnerTracker:
    """Arrival ticks of the partner's bursts -> index m and linear prediction."""

    def __init__(self, per_b, window):
        self.per0, self.W = per_b, window
        self.pts = []                     # (m, tick)
        self.misses = 0
        self.losses = 0
        self.fit = None

    def reset(self):
        if self.pts:
            self.losses += 1
        self.pts, self.misses, self.fit = [], 0, None

    @property
    def locked(self):
        return len(self.pts) >= 3

    @property
    def found(self):
        return len(self.pts) >= 1

    def index_of(self, tick):
        if not self.pts:
            return 0
        return int(round((tick - self.predict(0)) / self.period()))

    def period(self):
        return self.fit[0] if self.fit is not None else self.per0

    def predict(self, m):
        if self.fit is not None:
            return self.fit[1] + self.fit[0] * m
        (ma, ta), (mb, tb) = min(self.pts), max(self.pts)
        per = (tb - ta) / (mb - ma) if mb > ma else self.per0
        return tb + (m - mb) * per

    def add(self, tick):
        m = self.index_of(tick)
        if any(p[0] == m for p in self.pts):
            return m                                        # same burst seen twice
        self.pts.append((m, float(tick)))
        self.pts = self.pts[-self.W:]
        self.misses = 0
        if len(self.pts) >= 3:
            x = np.array([p[0] for p in self.pts], float)
            y = np.array([p[1] for p in self.pts], float)
            slope, icpt = np.polyfit(x - x[-1], y - y[-1], 1)
            self.fit = (slope, y[-1] + icpt - slope * x[-1])
        return m


class SimBaseline(S.SimSense):
    """Partner either free-running on its own clock (period error --sim-partner-ppm on top
    of --sim-dppm, first burst --sim-partner-phase-ms after --tx-start) or an ideal follower
    of our own bursts (+ --follow-offset-ms seen from it)."""

    def __init__(self, args, own_burst, partner_burst):
        super().__init__(args, own_burst, partner_burst)
        self.q0 = None

    def _q0(self):
        if self.q0 is None:
            a = self.args
            self.q0 = self.base + (a.tx_start_unix - self.unix0) * self.R + a.sim_partner_phase_ms * 1e-3 * self.R
        return self.q0

    def partner_tx_true(self, t_start):
        a = self.args
        L = self.pb.len * a.tpw
        if a.sim_partner == "follower":
            with self.lock:
                own = list(self.bursts)
            off = (a.partner_off - a.own_off) % a.burst_period_ms * 1e-3 * self.R
            cand = [t + off for t in own if t + off >= t_start - L]
            return min(cand) if cand else t_start - 10 * L
        per = a.burst_period_ms * 1e-3 * self.R * (1 + (a.sim_partner_ppm + a.sim_dppm) * 1e-6)
        q0 = self._q0()
        m = int(np.ceil((t_start - L - q0) / per))
        return q0 + m * per


def main():
    args = parse_args()
    R, Rw, tpw = args.tick_rate, args.wifi_rate, args.tpw
    out_dir = args.out_dir or Path(f"base_{args.ap}_{time.strftime('%Y%m%d_%H%M%S')}")
    out_dir.mkdir(parents=True, exist_ok=True)
    own_b, part_b = S.make_burst(args, args.ap), S.make_burst(args, args.partner)
    search = int(np.ceil(args.search_ns * 1e-9 * Rw))
    ext = S.Extractor(S.pulse_templates(part_b), part_b.S, search, args.integer_extract)
    import multiprocessing as mp
    from concurrent.futures import ProcessPoolExecutor
    pool = ProcessPoolExecutor(max_workers=args.workers, mp_context=mp.get_context("fork"),
                               initializer=S._worker_init, initargs=(args,))
    list(pool.map(time.sleep, [0.3] * args.workers))
    trk = base.Tracker(args)                                       # 5G: ground truth only
    radio = SimBaseline(args, own_b, part_b) if args.sim else base.UhdRadio(args, own_b)
    desc = {("freerun", "schedule"): "B0 free-running, agreed schedule on own clock",
            ("freerun", "track"): "B1 free-running TX, search-and-follow RX",
            ("follow", "track"): "B2 WiFi follower (timing from the leader's bursts)"}[(args.mode, args.rx)]
    print(f"AP {args.ap}: {desc} | period {args.burst_period_ms} ms x (1{args.period_ppm:+.2f} ppm), "
          f"phase {args.phase_ms:.3f} ms | from {time.strftime('%H:%M:%S', time.localtime(args.tx_start_unix))} "
          f"for {args.duration_s:.0f} s")
    summary = dict(args={k: (str(v) if isinstance(v, Path) else v) for k, v in vars(args).items()},
                   scheme=desc, setup=radio.setup_report, started_unix=time.time())
    summary["nr_lock"] = radio.acquire_nr()
    radio.pbch_every, radio.pbch_force = 0, False

    guard_s = int(round(args.guard_us * 1e-6 * Rw))
    guard_t = guard_s * tpw
    n_win = part_b.len + 2 * guard_s
    own_len_t = own_b.len * tpw
    keep = args.turnaround_us * 1e-6 * R
    min_margin = args.min_margin_ms * 1e-3 * R
    per_b = args.burst_period_ms * 1e-3 * R
    per_own = per_b * (1 + args.period_ppm * 1e-6)
    horizon = args.lookahead_ssb * args.nr_block_ms * 1e-3 * R
    lat_t = args.latency_ns * 1e-9 * R
    t_end = args.tx_start_unix + args.duration_s

    nr_f = open(out_dir / "nr_events.jsonl", "w")
    tx_f = open(out_dir / "tx_log.jsonl", "w")
    rx_f = open(out_dir / "rx_log.jsonl", "w")
    counts = dict(tx_armed=0, tx_late=0, tx_silent=0, rx_armed=0, rx_late=0, rx_collision=0, rx_ok=0, rx_miss=0,
                  rx_fail=0, acq_windows=0, acq_found=0)
    events, rx_rows, pending, own_tx = {}, [], [], []
    eid = 0
    pt = PartnerTracker(per_b, args.track_window)
    acq = dict(done=args.acq_bursts <= 0 or args.rx == "track", lat=[], n_try=0)
    lat_box = [lat_t]
    radio.phase = "listen"
    radio.start()
    clock = Clock(radio, R)

    def tick_at(u):
        return clock.tick + (u * 1e9 - clock.unix_ns) * 1e-9 * R

    T0 = tick_at(args.tx_start_unix) + args.phase_ms * 1e-3 * R
    next_j = 0                          # own schedule index (freerun)
    next_m = None                       # partner index for the next narrow window (track)
    armed_m, next_acq = set(), 0.0
    acq_slot = 0                        # which part of the period the next search window covers
    acq_out = [0]                       # search windows armed whose samples have not arrived yet
    tx_m = [None]                       # follower: next leader index to transmit after
    t_first_found = t_first_tx = None
    last_print = last_anchor = time.monotonic()
    import signal
    stop_req = []
    signal.signal(signal.SIGINT, lambda *_: stop_req.append(1))

    def overlaps_own_tx(w0, w1):
        return any(t - keep < w1 and w0 < t + own_len_t + keep for t in own_tx[-8:])

    def arm_rx(kind, t_ev, w0, n, idx):
        nonlocal eid
        now = radio.now_ticks()
        ev = dict(eid=eid, role="RX", kind=kind, idx=int(idx), t_ev=int(t_ev), w0=int(w0), n=int(n),
                  unix=clock.unix(int(t_ev)), margin_ms=(w0 - now) / R * 1e3)
        eid += 1
        if w0 - now < min_margin:
            ev["state"] = "MISSED_LATE"
            counts["rx_late"] += 1
        else:
            ev["state"] = "ARMED"
            radio.arm_observer(ev["eid"], int(w0), int(n))
            counts["rx_armed"] += 1
            if kind == "acq":
                counts["acq_windows"] += 1
                acq_out[0] += 1
        events[ev["eid"]] = ev
        return ev

    def submit(ev, ob):
        if ev["kind"] == "acq" or ev.get("wide") or not acq["done"]:
            p = (ev["t_ev"] + lat_box[0] - ob["w0"]) / tpw
            return (ev, ob["w0"], p, True, pool.submit(S._worker_acquire, ob["y"], p))
        p = (ev["t_ev"] + lat_box[0] - ob["w0"]) / tpw
        seg = ext.segments(ob["y"], p)
        if seg is None:
            counts["rx_fail"] += 1
            return None
        return (ev, ob["w0"], p, False, pool.submit(S._worker_narrow, seg, p))

    def collect(final=False):
        nonlocal pending, t_first_found
        still = []
        for item in pending:
            ev, w0, p, is_acq, f = item
            if not (final or f.done()):
                still.append(item)
                continue
            if is_acq:
                res, det, _ = f.result()
                if det is None or res is None or det[1] < args.detect_q:
                    counts["rx_miss"] += 1
                    ev["fate"] = "miss"
                    if ev["kind"] == "track":
                        pt.misses += 1
                    rx_f.write(json.dumps(ev) + "\n")
                    continue
                arrival = w0 + det[0] * tpw
                if ev["kind"] == "acq":
                    counts["acq_found"] += 1
                if args.rx == "schedule" and not acq["done"]:
                    acq["lat"].append((arrival - ev["t_ev"]) / R * 1e9)
                    if len(acq["lat"]) >= args.acq_bursts:
                        lat_box[0] = float(np.median(acq["lat"])) * 1e-9 * R
                        acq["done"] = True
                        print(f"[LATENCY] partner found in {len(acq['lat'])} windows: {np.median(acq['lat']):.0f} ns")
                res = (res[0], float(det[0] + res[1] - p), res[2], res[3], res[4])
            else:
                res, _ = f.result()
                if res is None or res[2] < args.detect_q:
                    counts["rx_miss"] += 1
                    ev["fate"] = "miss"
                    if ev["kind"] == "track":
                        pt.misses += 1
                    rx_f.write(json.dumps(ev) + "\n")
                    continue
                arrival = w0 + (p + res[1]) * tpw
            h, lag, q, prof, dl = res
            idx = ev["idx"]
            if args.rx == "track":
                idx = pt.add(arrival)
                if t_first_found is None:
                    t_first_found = clock.unix(int(arrival)) - args.tx_start_unix
                    print(f"[TRACK] partner found {t_first_found:+.2f} s after the start")
            counts["rx_ok"] += 1
            ev.update(fate="ok", arrival=float(arrival), m=int(idx), q=float(q))
            rx_f.write(json.dumps(ev) + "\n")
            rx_rows.append(dict(ev=ev, idx=int(idx), h=h, lag=lag, q=q, w0=w0, acq=is_acq and ev["kind"] != "acq",
                                prof=prof, prof_delta=dl, arrival=float(arrival)))
        pending = still

    print("\n=== Running (Ctrl+C to stop early) ===")
    t_launch = time.monotonic()
    try:
        while True:
            while True:
                try:
                    row = radio.nr_q.get_nowait()
                except Exception:
                    break
                if row.get("valid"):
                    row["k"] = trk.add(row["event_device_ticks"])
                nr_f.write(json.dumps(row) + "\n")
            now = radio.now_ticks()
            if time.monotonic() - last_anchor > 10:
                last_anchor = time.monotonic()
                clock.refresh()
            now_u = clock.unix(now)
            if now_u > t_end + 0.5:
                break

            # ---- own TX
            if args.mode == "freerun":
                while T0 + next_j * per_own < now + horizon:
                    t_tx = int(round(T0 + next_j * per_own))
                    j = next_j
                    next_j += 1
                    u = clock.unix(t_tx)
                    if not (args.tx_start_unix <= u < t_end):
                        continue
                    ev = dict(eid=eid, role="TX", idx=j, t_ev=t_tx, unix=u, margin_ms=(t_tx - now) / R * 1e3)
                    eid += 1
                    if t_tx - now < min_margin:
                        ev["state"] = "MISSED_LATE"
                        counts["tx_late"] += 1
                    else:
                        ev["state"] = "ARMED"
                        radio.send_burst(ev["eid"], t_tx, ev)
                        own_tx.append(t_tx)
                        counts["tx_armed"] += 1
                        t_first_tx = u - args.tx_start_unix if t_first_tx is None else t_first_tx
                    tx_f.write(json.dumps(ev) + "\n")
                    # B0: listen where the agreed schedule says, on the own clock
                    if args.rx == "schedule":
                        t_rx = t_tx + (args.partner_off - args.own_off) % args.burst_period_ms * 1e-3 * R
                        arm_rx("schedule", t_rx, t_rx - guard_t, n_win, j)
            elif pt.locked and pt.misses < args.track_lost:
                # B2 follower: TX after each predicted leader burst (own pointer, independent of RX)
                off_t = args.follow_offset_ms * 1e-3 * R - lat_box[0]
                if tx_m[0] is None:
                    tx_m[0] = int(np.ceil((now + min_margin - off_t - pt.predict(0)) / pt.period()))
                while True:
                    m = tx_m[0]
                    t_tx = int(round(pt.predict(m) + off_t))
                    if t_tx >= now + horizon:
                        break
                    tx_m[0] = m + 1
                    u = clock.unix(t_tx)
                    if not (args.tx_start_unix <= u < t_end) or t_tx - now < min_margin:
                        if t_tx - now < min_margin and args.tx_start_unix <= u < t_end:
                            counts["tx_late"] += 1
                        continue
                    ev = dict(eid=eid, role="TX", idx=m, t_ev=t_tx, unix=u, margin_ms=(t_tx - now) / R * 1e3,
                              state="ARMED")
                    eid += 1
                    radio.send_burst(ev["eid"], t_tx, ev)
                    own_tx.append(t_tx)
                    counts["tx_armed"] += 1
                    t_first_tx = u - args.tx_start_unix if t_first_tx is None else t_first_tx
                    tx_f.write(json.dumps(ev) + "\n")
            elif args.mode == "follow" and now_u >= args.tx_start_unix:
                counts["tx_silent"] += 1 if now_u - getattr(main, "_last_silent", 0) > 0.005 else 0
                main._last_silent = now_u

            # ---- RX in track mode
            if args.rx == "track" and now_u >= args.tx_start_unix - 0.05:
                if pt.found and pt.misses >= args.track_lost:
                    print(f"[TRACK] lost after {pt.misses} misses -> searching again")
                    pt.reset()
                    next_m = None
                    armed_m.clear()
                    tx_m[0] = None
                if not pt.found:
                    # short search windows that slide over the free part of the period, few per second
                    busy = acq_out[0] + sum(1 for it in pending if it[0]["kind"] == "acq")
                    if now_u >= next_acq and busy < args.acq_max_pending:
                        next_acq = now_u + args.acq_every * args.burst_period_ms * 1e-3
                        win_t = args.acq_win_ms * 1e-3 * R
                        step = win_t - own_len_t - 2 * guard_t            # consecutive windows overlap by a burst
                        base_t = now + 0.6 * horizon
                        if own_tx:
                            nxt = [t for t in own_tx if t + own_len_t > base_t]
                            gap0 = (nxt[0] if nxt else own_tx[-1] + per_own) + own_len_t + keep
                            free = per_own - own_len_t - 2 * keep
                        else:
                            gap0, free = base_t, per_b
                        n_steps = max(1, int(np.ceil((free - win_t) / step)) + 1)
                        off = (acq_slot % n_steps) * step
                        acq_slot += 1
                        w0 = gap0 + min(off, max(free - win_t, 0))
                        arm_rx("acq", w0 + guard_t, w0, int(round(win_t / tpw)), -1)
                else:
                    if next_m is None:
                        next_m = pt.pts[-1][0] + 1
                    # extrapolate only a few periods until the period itself is measured
                    m_max = max(p[0] for p in pt.pts) + (10 ** 9 if pt.locked else 150)
                    while pt.predict(next_m) - guard_t < now + horizon and next_m <= m_max:
                        m = next_m
                        next_m += 1
                        a_m = pt.predict(m)
                        w0 = a_m - guard_t
                        if a_m - guard_t - now < min_margin:
                            continue
                        if overlaps_own_tx(w0, w0 + n_win * tpw):
                            counts["rx_collision"] += 1
                            pt.misses += 1
                            rx_f.write(json.dumps(dict(kind="track", idx=m, fate="collision",
                                                       unix=clock.unix(int(a_m)))) + "\n")
                            continue
                        ev_t = arm_rx("track", a_m - lat_box[0], w0, n_win, m)
                        ev_t["wide"] = not pt.locked        # search the whole +-guard until the period is known

            # ---- observations
            while True:
                try:
                    ob = radio.obs_q.get_nowait()
                except Exception:
                    break
                ev = events.get(ob["k"])
                if ev is not None and ev.get("kind") == "acq" and ev.get("state") == "ARMED":
                    acq_out[0] = max(0, acq_out[0] - 1)
                if ev is None or "error" in ob:
                    if "error" in ob:
                        counts["rx_fail"] += 1
                    continue
                it = submit(ev, ob)
                if it is not None:
                    pending.append(it)
            collect()
            if time.monotonic() - last_print > 5:
                last_print = time.monotonic()
                st = "locked" if pt.locked else ("found" if pt.found else "searching")
                print(f"  TX {counts['tx_armed']} (late {counts['tx_late']}) | RX ok {counts['rx_ok']}, miss "
                      f"{counts['rx_miss']}, collision {counts['rx_collision']}, late {counts['rx_late']} | "
                      f"acq {counts['acq_found']}/{counts['acq_windows']} | partner {st} "
                      f"(losses {pt.losses}) | queue {len(pending)}")
            if stop_req:
                raise KeyboardInterrupt
            time.sleep(0.001)
    except KeyboardInterrupt:
        print("\nInterrupted: writing results")
    finally:
        t_wait = time.monotonic() + 1.0
        while time.monotonic() < t_wait or (args.sim and radio.pending_obs() and time.monotonic() < t_wait + 120):
            try:
                ob = radio.obs_q.get(timeout=0.05)
            except Exception:
                continue
            ev = events.get(ob["k"])
            if ev is not None and "error" not in ob:
                it = submit(ev, ob)
                if it is not None:
                    pending.append(it)
        collect(final=True)
        pool.shutdown(wait=True)
        radio.stop()
        while not radio.nr_q.empty():
            nr_f.write(json.dumps(radio.nr_q.get()) + "\n")
        for f in (nr_f, tx_f, rx_f):
            f.close()

    rx_rows.sort(key=lambda r: r["arrival"])
    if rx_rows:
        idx = np.array([r["idx"] for r in rx_rows], dtype=np.int64)
        idx = idx - idx.min()
        np.savez_compressed(
            out_dir / "sense_rx.npz",
            h=np.stack([r["h"] for r in rx_rows]), lag=np.array([r["lag"] for r in rx_rows]),
            q=np.array([r["q"] for r in rx_rows]), t_ev=np.array([r["ev"]["t_ev"] for r in rx_rows], dtype=np.int64),
            w0=np.array([r["w0"] for r in rx_rows], dtype=np.int64),
            arrival=np.array([r["arrival"] for r in rx_rows]),
            n=idx // args.n_sub, sub=idx % args.n_sub, half=idx % args.n_sub,
            unix=np.array([clock.unix(int(r["arrival"])) for r in rx_rows]),
            rate=Rw, tick_rate=R, pulse_spacing=part_b.S, rx_chans=np.array(args.rx_chans),
            tx_chans=np.array(args.tx_chans), wifi_freq=args.wifi_freq, ap=args.ap,
            integer_extract=args.integer_extract, acq=np.array([r["acq"] for r in rx_rows]),
            profile=np.stack([r["prof"] for r in rx_rows]), profile_first_lag=-S.Extractor.PROF_PRE,
            profile_delta=np.array([r["prof_delta"] for r in rx_rows], dtype=np.float32),
            latency_ns=lat_box[0] / R * 1e9, ppm=np.zeros(len(rx_rows)))
    # expected partner bursts in the run (one every period) vs received
    expected = int(args.duration_s / (args.burst_period_ms * 1e-3))
    res = dict(counts=counts, expected_partner_bursts=expected,
               received_fraction=round(counts["rx_ok"] / max(expected, 1), 4),
               partner_found_after_s=t_first_found, first_own_tx_after_s=t_first_tx,
               track_losses=pt.losses, latency_ns=lat_box[0] / R * 1e9,
               tracker_5g=dict(ppm=trk.ppm(), resid_ns=trk.fit[4] if trk.fit else None))
    summary["results"] = res
    summary["finished_unix"] = time.time()
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, default=float))
    print("\n=== Summary ===")
    print(f"AP {args.ap} [{desc}]: {counts}")
    print(f"partner bursts received {counts['rx_ok']} of ~{expected} ({100 * res['received_fraction']:.1f} %) | "
          f"found after {t_first_found} s | first own TX after {t_first_tx} s | track losses {pt.losses}")
    print(f"Saved {out_dir}/ (sense_rx.npz, tx_log.jsonl, rx_log.jsonl, nr_events.jsonl, summary.json)")


if __name__ == "__main__":
    main()
