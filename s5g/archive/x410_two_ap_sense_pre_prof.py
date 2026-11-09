#!/usr/bin/env python3
"""
Two-AP bistatic sensing sessions, timed by the 5G (same script on both APs).
Tanda 2 version: WiFi rate separate from the 5G rate, scheduling at the
X410 master-clock resolution, fractional-delay extraction, 200 Hz.

Timing comes from the 5G PSS of each AP (as in x410_two_ap.py); the SSB
numbering comes from the SFN decoded from PBCH/MIB (--numbering sfn, default) or
from the old provisional host-time grid (--numbering ntp). Host time is still
used to define when the run starts and ends (operational), never which slot is
whose.

Time units:
  ticks   --tick-rate (default 245.76 MHz = X410 master clock, 4.07 ns): every
          timed command, every timestamp and the 5G tracker use them
  5G      sampled at --nr-rate (15.36 Msps), PSS position = block time_spec +
          detector offset converted to ticks
  WiFi    sampled at --wifi-rate (61.44 Msps default; 122.88 exceeds a single
          10GbE link for 4 TX / 3 RX chains)

Sessions: cycle c (5 SSB = 100 ms) is in a session when
          (c % --session-period-cycles) in [--session-start, --session-start + --session-len)
          Outside sessions both APs are silent (only the 5G is tracked).
In a session, every --burst-period-ms of 5G time (4 per 20 ms SSB = 200 Hz):
          AP A transmits at PSS + --offset-a-ms + j*period, AP B at PSS + --offset-b-ms + j*period
          (A and B interleaved), each listens to the other on its 3 WiFi RX chains.
Burst:    --n-pulses ZC pulses of --pulse-us every --pulse-spacing-us on the 4 TX
          chains, a distinct root per pulse, chain and AP (MIMO: H 3x4 per pulse).
RX:       the burst arrives at a known time (5G sync). The expected position in
          the window uses the actual time_spec of the first sample; integer lag
          search +-search, then fractional refinement (windowed-sinc
          interpolation of the matched-filter output, 1/40 sample) -> h[pulse,
          rx, tx] at the true arrival, and the residual lag in fractional samples.
          (Integer-only extraction made the gain oscillate at R * delta_ppm,
          the ~1 Hz artifact of tanda 1; --integer-extract reproduces it.)

Outputs: sense_rx.npz (what this AP received), tx_log.jsonl, summary.json.
Process both APs' folders together with sense_doppler.py.

Examples:
  continuous:  python3 x410_two_ap_sense.py --ap A --tx-start 17:00:00 --duration-s 120
  on demand:   ... --session-start 70 --session-len 30      (7 s silent + 3 s session)
  simulation:  python3 x410_two_ap_sense.py --ap B --sim --tx-start-in-s 4 --duration-s 10
  tanda 1 behaviour (sim): --wifi-rate 15.36e6 --sched-grid-ticks 16 --integer-extract
"""
import argparse
import copy
import datetime as dt
import json
import queue
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import x410_pss_timed_tx as base  # noqa: E402
from x410_two_ap import CYCLE, ROOTS, SFN_SSB, Clock, SfnNumbering, SimTwoAp, add_numbering_args  # noqa: E402

C0 = 299792458.0
SSB_MS = 20.0


def largest_prime_le(n):
    n = int(n)
    while n > 2:
        if all(n % d for d in range(2, int(n ** 0.5) + 1)):
            return n
        n -= 1
    return 2


def parse_args():
    p = argparse.ArgumentParser(description="Two-AP bistatic sensing sessions timed by 5G")
    p.add_argument("--ap", choices=("A", "B"), required=True)
    p.add_argument("--tx-start", default=None, help="HH:MM:SS, same on both APs")
    p.add_argument("--tx-start-in-s", type=float, default=None)
    p.add_argument("--duration-s", type=float, default=120.0)
    p.add_argument("--session-period-cycles", type=int, default=100)
    p.add_argument("--session-start", type=int, default=0)
    p.add_argument("--session-len", type=int, default=100, help="100 of 100 = always sensing")
    p.add_argument("--burst-period-ms", type=float, default=5.0, help="Per AP; must divide 20 ms")
    p.add_argument("--offset-a-ms", type=float, default=1.25)
    p.add_argument("--offset-b-ms", type=float, default=3.75)
    p.add_argument("--n-pulses", type=int, default=8)
    p.add_argument("--pulse-us", type=float, default=50.0, help="ZC length = largest prime <= this * wifi rate")
    p.add_argument("--zc-len", type=int, default=None, help="Overrides --pulse-us")
    p.add_argument("--pulse-spacing-us", type=float, default=100.0)
    p.add_argument("--guard-us", type=float, default=50.0, help="RX window margin each side")
    p.add_argument("--latency-ns", type=float, default=4000.0, help="Expected TX->air->RX delay")
    p.add_argument("--search-ns", type=float, default=500.0, help="+- lag search around expected")
    p.add_argument("--integer-extract", action="store_true", help="Tanda 1 extraction (no fractional refinement)")
    p.add_argument("--sched-grid-ticks", type=int, default=1, help="Round timed commands to this many ticks")
    p.add_argument("--workers", type=int, default=4, help="Extraction processes")
    p.add_argument("--acq-bursts", type=int, default=40,
                   help="Measure the TX->RX latency on this many bursts (full-window search) before "
                        "switching to the narrow search; 0 = trust --latency-ns")
    add_numbering_args(p)
    p.add_argument("--grid-offset-ms", type=float, default=0.0)
    p.add_argument("--min-grid-margin-ms", type=float, default=3.0)
    p.add_argument("--args", default="addr=192.168.10.2")
    p.add_argument("--modulos-root", type=Path, default=Path.home() / "syncSense" / "modulos_rx_tx")
    p.add_argument("--out-dir", type=Path, default=None)
    p.add_argument("--sim", action="store_true")
    p.add_argument("--sim-target", default="1.0,-20,-25,15,30",
                   help="(sim) echo: speed m/s, level dB vs direct, AoD deg, AoA deg, sweep deg/s")
    p.add_argument("--sim-dppm", type=float, default=0.07, help="(sim) partner clock minus own clock, ppm")
    p.add_argument("--sim-latency-ns", type=float, default=1730.0, help="(sim) true TX->air->RX delay")
    p.add_argument("--sim-spacing", type=float, default=0.030, help="(sim) array element spacing, m")
    p.add_argument("--tick-rate", type=float, default=245.76e6)
    p.add_argument("--nr-rate", type=float, default=15.36e6)
    p.add_argument("--wifi-rate", type=float, default=61.44e6)
    p.add_argument("--wifi-freq", type=float, default=5.18e9)
    p.add_argument("--tx-gain", type=float, default=20.0)
    p.add_argument("--tx-chans", type=int, nargs="+", default=[0, 1, 2, 3])
    p.add_argument("--amp", type=float, default=0.25)
    p.add_argument("--rx-chans", type=int, nargs="+", default=[0, 1, 3])
    p.add_argument("--rx-ant", default="TX/RX0")
    p.add_argument("--rx-gain", type=float, default=20.0)
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
    a.rate = a.nr_rate                       # legacy name read by the 5G loader / sim
    a.detect_thr, a.min_peak_to_side = 8.0, 1.5      # used by base.Burst
    for name in ("nr_rate", "wifi_rate"):
        r = a.tick_rate / getattr(a, name)
        if abs(r - round(r)) > 1e-9:
            p.error(f"--tick-rate must be an integer multiple of --{name.replace('_', '-')}")
    a.tpw = int(round(a.tick_rate / a.wifi_rate))
    if a.zc_len is None:
        a.zc_len = largest_prime_le(a.pulse_us * 1e-6 * a.wifi_rate)
    a.n_sub = SSB_MS / a.burst_period_ms
    if abs(a.n_sub - round(a.n_sub)) > 1e-9:
        p.error("--burst-period-ms must divide 20 ms")
    a.n_sub = int(round(a.n_sub))
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
    burst_ms = ((a.n_pulses - 1) * a.pulse_spacing_us + a.zc_len / a.wifi_rate * 1e6) / 1e3
    need = burst_ms + 2 * a.guard_us / 1e3 + 0.3
    d = abs(a.offset_a_ms - a.offset_b_ms) % a.burst_period_ms
    if min(d, a.burst_period_ms - d) < need:
        p.error(f"A and B bursts ({burst_ms:.2f} ms + guards) overlap with these offsets/period")
    # slot_offset/partner_slot are not used here, but SimTwoAp reads them
    a.slot_offset_ms, a.partner_slot, a.sim_partner_shift = a.partner_off, -1, 0
    return a


def make_burst(args, ap):
    b = copy.copy(args)
    b.rate = args.wifi_rate
    b.zc_roots = ROOTS[ap][:len(args.tx_chans)]
    return base.Burst(b)


def pulse_templates(burst):
    """P[c, k, n]: unit-amplitude ZC of pulse k on TX chain c."""
    P = np.zeros((burst.n_tx, burst.K, burst.N), dtype=np.complex64)
    for c in range(burst.n_tx):
        for k in range(burst.K):
            P[c, k] = burst.templates[c][k * burst.S:k * burst.S + burst.N]
    return P


def in_session(args, cycle):
    return (cycle % args.session_period_cycles - args.session_start) % args.session_period_cycles < args.session_len


class Extractor:
    """Matched filter of every pulse/RX/TX at the known position.
    Per pulse k and RX chain r: segment around the expected start, FFT, times the
    conjugate spectrum of every TX template -> correlation at the integer lags
    -search-TAPS .. +search+TAPS. The integer lag with most energy (all pulses,
    links) is refined to 1/40 sample with a Hann-windowed sinc over +-TAPS lags,
    maximising the total energy, and h is read at that fractional lag."""
    TAPS = 24

    def __init__(self, P, S, search, integer=False):
        self.P, self.S, self.search, self.integer = P, S, int(search), integer
        n_tx, K, N = P.shape
        self.K, self.N = K, N
        self.Lseg = N + 2 * (self.search + self.TAPS) + 1
        self.M = base.next_fast_len(self.Lseg)
        self.Pf = np.conj(np.fft.fft(P, self.M, axis=2)).transpose(1, 0, 2)[:, None]   # (K,1,tx,M)
        self.nl = self.Lseg - N + 1
        self.deltas = np.arange(-24, 25) / 40.0
        x = self.deltas[:, None] - np.arange(-self.TAPS, self.TAPS + 1)[None, :]
        self.W = (np.sinc(x) * (0.5 + 0.5 * np.cos(np.pi * x / (self.TAPS + 1)))).astype(np.complex64)

    def segments(self, y, p):
        """Cheap part (caller side): the K pulse segments around expected start p."""
        i0 = int(np.floor(p))
        st = i0 + np.arange(self.K) * self.S - self.search - self.TAPS
        if st[0] < 0 or st[-1] + self.Lseg > y.shape[1]:
            return None
        idx = st[:, None] + np.arange(self.Lseg)[None, :]
        return y[:, idx].transpose(1, 0, 2).copy()                          # (K, rx, Lseg)

    def __call__(self, y, p):
        """y: (n_rx, L) window; p: expected start of pulse 0 [fractional samples].
        Returns h (K, rx, tx), lag = measured - expected [samples], quality."""
        seg = self.segments(y, p)
        return None if seg is None else self.compute(seg, p)

    def compute(self, seg, p):
        K, S, N, s, T = self.K, self.S, self.N, self.search, self.TAPS
        i0 = int(np.floor(p))
        Y = np.fft.fft(seg, self.M, axis=2)[:, :, None, :]                  # (K, rx, 1, M)
        cc = np.fft.ifft(Y * self.Pf, axis=3)[..., :self.nl] / N            # (K, rx, tx, nl)
        e = np.sum(np.abs(cc) ** 2, axis=(0, 1, 2))
        lo, hi = T, T + 2 * s + 2                                           # lags -s .. s+1
        j = lo + int(np.argmax(e[lo:hi]))
        q = float(e[j] / max(np.median(e), 1e-30))
        if self.integer:
            return cc[..., j].astype(np.complex64), float(i0 + j - s - T - p), q
        loc = cc[..., j - T:j + T + 1]                                      # (K, rx, tx, 2T+1)
        hi_ = np.einsum("dt,krct->dkrc", self.W, loc)
        ed = np.sum(np.abs(hi_) ** 2, axis=(1, 2, 3))
        b = int(np.argmax(ed))
        return hi_[b].astype(np.complex64), float(i0 + j - s - T + self.deltas[b] - p), q


# ---- extraction in separate processes (no GIL contention with the 5G/scheduler threads)
_W = {}


def _worker_init(args):
    import os
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    pb = make_burst(args, args.partner)
    search = int(np.ceil(args.search_ns * 1e-9 * args.wifi_rate))
    _W["ext"] = Extractor(pulse_templates(pb), pb.S, search, args.integer_extract)
    _W["burst"] = pb


def _worker_narrow(seg, p):
    t0 = time.perf_counter()
    res = _W["ext"].compute(seg, p)
    return res, time.perf_counter() - t0


def _worker_acquire(y, p_guess):
    """Full-window detection (latency unknown): burst start from the strongest TX
    chain on every RX chain, median; then extraction at that position."""
    t0 = time.perf_counter()
    starts, peaks = [], []
    for r in range(y.shape[0]):
        det = [d for d in _W["burst"].detect(y[r]) if d.get("found")]
        if det:
            d = max(det, key=lambda d: d["peak_ratio"])
            starts.append(d["start"])
            peaks.append(d["peak_ratio"])
    if not starts:
        return None, None, time.perf_counter() - t0
    st = float(np.median(starts))
    res = _W["ext"](y, st)
    return res, (st, float(max(peaks))), time.perf_counter() - t0


class SimSense(SimTwoAp):
    """Partner bursts at the session times through a channel = direct path +
    one moving echo (Doppler, AoD at the partner array, AoA here). The partner
    has its own clock (--sim-dppm vs ours): it schedules in ITS ticks, rounded
    to --sched-grid-ticks, so its bursts slide over our sample grid; the CFO
    between the APs is dppm * f. Our windows start exactly at the armed tick
    (multiple of the grid) and are sampled every tick_rate/wifi_rate ticks."""

    def __init__(self, args, own_burst, partner_burst):
        super().__init__(args, own_burst, partner_burst)
        v, lvl, aod, aoa, sweep = (float(x) for x in args.sim_target.split(","))
        self.tgt = dict(v=v, amp=10 ** (lvl / 20), aod=aod, aoa=aoa, sweep=sweep)
        self.kd = 2 * np.pi * args.sim_spacing / (C0 / args.wifi_freq)
        rng = np.random.default_rng(7)
        self.hw_tx = np.exp(1j * rng.uniform(0, 2 * np.pi, partner_burst.n_tx))
        self.hw_rx = np.exp(1j * rng.uniform(0, 2 * np.pi, len(args.rx_chans)))
        self.los_aod, self.los_aoa = 30.0, 32.0
        self.dppm = args.sim_dppm * 1e-6
        self.cfo = args.sim_dppm * 1e-6 * args.wifi_freq
        self.c_p = 7_777_777.37                       # partner tick = (1 + dppm) * own tick + c_p
        self.Mx = base.next_fast_len(partner_burst.len + 64)
        self.Xf = np.fft.fft(partner_burst.buf, self.Mx, axis=1)
        self.fM = np.fft.fftfreq(self.Mx)
        self.n_gen = 0

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

    def partner_tx_true(self, t_start):
        """True (own-tick) start of the partner burst the window at t_start was armed for."""
        a = self.args
        G = a.sched_grid_ticks
        off = a.partner_off * 1e-3 * self.R
        per_b = a.burst_period_ms * 1e-3 * self.R
        k = int(np.floor((t_start - self.first) / self.per))
        best = None
        for kk in (k - 1, k, k + 1):
            T = self.first + kk * self.per                       # true PSS, own ticks
            Tp = (1 + self.dppm) * T + self.c_p + self.rng.normal(0, 3e-9 * self.R)
            for j in range(a.n_sub):
                tp = np.round((Tp + off + j * per_b) / G) * G     # partner's armed tick
                tt = (tp - self.c_p) / (1 + self.dppm)
                if best is None or abs(tt - t_start) < abs(best - t_start):
                    best = tt
        return best

    def _obs_thread(self):
        a, b = self.args, self.pb
        n_rx = len(a.rx_chans)
        tpw = a.tpw
        lat = a.sim_latency_ns * 1e-9 * self.R          # true latency (the script must find it)
        while not self.stop_ev.is_set():
            with self.lock:
                ready = [p for p in self.obs_pending if self.now_ticks() > p[1] + p[2] * tpw]
                self.obs_pending = [p for p in self.obs_pending if p not in ready]
            for k, t_start, n in ready:
                noise = 10 ** (-80 / 20) * a.amp * 10 ** (-30 / 20)
                y = (noise / np.sqrt(2) * (self.rng.standard_normal((n_rx, n))
                                           + 1j * self.rng.standard_normal((n_rx, n)))).astype(np.complex64)
                d0 = (self.partner_tx_true(t_start) + lat - t_start) / tpw     # fractional samples
                t_s = self.unix_true(t_start) - self.unix0
                H = self.channel(t_s)
                i0 = int(np.floor(d0)) - 32
                sh = np.fft.ifft(self.Xf * np.exp(-2j * np.pi * self.fM * (d0 - i0)), axis=1)
                ns = np.arange(self.Mx)
                sh = sh * np.exp(2j * np.pi * self.cfo * (t_s + (i0 + ns) / a.wifi_rate))
                z = H @ sh                                                    # (rx, Mx)
                j0, j1 = max(i0, 0), min(i0 + self.Mx, n)
                if j1 > j0:
                    y[:, j0:j1] += z[:, j0 - i0:j1 - i0].astype(np.complex64)
                self.n_gen += 1
                self.obs_q.put(dict(k=k, w0=t_start, y=y))
            time.sleep(0.0005)

    def pending_obs(self):
        with self.lock:
            return len(self.obs_pending)


def main():
    args = parse_args()
    R, Rw, tpw = args.tick_rate, args.wifi_rate, args.tpw
    out_dir = args.out_dir or Path(f"sense_{args.ap}_{time.strftime('%Y%m%d_%H%M%S')}")
    out_dir.mkdir(parents=True, exist_ok=True)
    own_b, part_b = make_burst(args, args.ap), make_burst(args, args.partner)
    search = int(np.ceil(args.search_ns * 1e-9 * Rw))
    ext = Extractor(pulse_templates(part_b), part_b.S, search, args.integer_extract)
    # extraction processes are forked now, before UHD and its threads exist
    import multiprocessing as mp
    pool = ProcessPoolExecutor(max_workers=args.workers, mp_context=mp.get_context("fork"),
                               initializer=_worker_init, initargs=(args,))
    list(pool.map(time.sleep, [0.3] * args.workers))            # forces every worker to start
    trk = base.Tracker(args)
    radio = SimSense(args, own_b, part_b) if args.sim else base.UhdRadio(args, own_b)
    t0txt = dt.datetime.fromtimestamp(args.tx_start_unix).strftime("%H:%M:%S")
    sess = "always" if args.session_len >= args.session_period_cycles else (
        f"cycles {args.session_start}..{args.session_start + args.session_len - 1} of every "
        f"{args.session_period_cycles}")
    print(f"AP {args.ap}: TX at PSS+{args.own_off} ms + j*{args.burst_period_ms} ms, listens at "
          f"PSS+{args.partner_off} ms + j*{args.burst_period_ms} ms ({1e3 / args.burst_period_ms:.0f} Hz per "
          f"direction) | sessions: {sess} | from {t0txt} for {args.duration_s:.0f} s")
    print(f"WiFi {args.wifi_freq/1e9:.3f} GHz at {Rw/1e6:.2f} Msps | burst {own_b.K} pulses ZC {own_b.N} "
          f"({own_b.N / Rw * 1e6:.1f} us) every {args.pulse_spacing_us} us = {own_b.len / Rw * 1e3:.2f} ms | "
          f"ticks {R/1e6:.2f} MHz, grid {args.sched_grid_ticks} | extraction "
          f"{'INTEGER (tanda 1)' if args.integer_extract else 'fractional'}, search +-{search} samples")
    summary = dict(args={k: (str(v) if isinstance(v, Path) else v) for k, v in vars(args).items()},
                   setup=radio.setup_report, started_unix=time.time(),
                   burst=dict(len=own_b.len, S=own_b.S, N=own_b.N, K=own_b.K,
                              roots_own=own_b.roots, roots_partner=part_b.roots),
                   numbering_method=("SFN decoded from PBCH/MIB (OTA); timing from 5G PSS" if args.numbering == "sfn"
                                     else "PROVISIONAL host time (NTP) -> SSB number; timing from 5G PSS"))
    summary["nr_lock"] = radio.acquire_nr()
    sfn = SfnNumbering(args) if args.numbering == "sfn" else None
    radio.pbch_every = args.pbch_every if sfn else 0
    radio.pbch_force = sfn is not None
    cm = (lambda n: n % SFN_SSB) if sfn else (lambda n: n)     # common SSB number

    G = args.sched_grid_ticks
    guard_s = int(round(args.guard_us * 1e-6 * Rw))              # WiFi samples
    guard_t = guard_s * tpw                                      # ticks
    n_win = part_b.len + 2 * guard_s
    min_margin = args.min_margin_ms * 1e-3 * R
    per_b = args.burst_period_ms * 1e-3 * R
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
    acq = dict(done=args.acq_bursts <= 0, lat=[], peaks=[], n_try=0, latency_ns=args.latency_ns)
    lat_box = [args.latency_ns * 1e-9 * R]
    pending = []
    eid = 0
    state = "SEARCHING"
    radio.phase = "listen"
    radio.start()
    clock = Clock(radio, R)
    t_launch = time.monotonic()
    last_print = last_anchor = time.monotonic()
    proc_s = []
    print("\n=== Running (Ctrl+C to stop early) ===")

    def gnum(tick):
        u = clock.unix(tick)
        return int(np.floor((u - g) / 0.02)), (u - g) % 0.02

    def submit(ev, ob):
        p = (ev["t_ev"] + lat_box[0] - ob["w0"]) / tpw        # expected burst start in this window
        if not acq["done"]:
            acq["n_try"] += 1
            return (ev, ob["w0"], p, lat_box[0], True, pool.submit(_worker_acquire, ob["y"], p))
        seg = ext.segments(ob["y"], p)
        if seg is None:
            counts["rx_fail"] += 1
            return None
        return (ev, ob["w0"], p, lat_box[0], False, pool.submit(_worker_narrow, seg, p))

    def collect(final=False):
        nonlocal pending
        still = []
        for item in pending:
            ev, w0, p, lat_used, is_acq, f = item
            if not (final or f.done()):
                still.append(item)
                continue
            if is_acq:
                res, det, dt_s = f.result()
                if det is not None and not acq["done"]:
                    acq["lat"].append((w0 + det[0] * tpw - ev["t_ev"]) / R * 1e9)
                    acq["peaks"].append(det[1])
                    if len(acq["lat"]) >= args.acq_bursts:
                        lat_ns = float(np.median(acq["lat"]))
                        lat_box[0] = lat_ns * 1e-9 * R
                        acq.update(done=True, latency_ns=lat_ns,
                                   spread_ns=float(np.percentile(acq["lat"], 90) - np.percentile(acq["lat"], 10)))
                        print(f"[LATENCY] partner bursts found in {len(acq['lat'])} of {acq['n_try']} windows: "
                              f"TX->air->RX {lat_ns:.0f} ns (expected {args.latency_ns:.0f}), p10-p90 spread "
                              f"{acq['spread_ns']:.1f} ns, peak ratio median {np.median(acq['peaks']):.0f} "
                              f"-> narrow search from now on")
                if det is not None:
                    res = (res[0], float(det[0] + res[1] - p), res[2]) if res is not None else None
            else:
                res, dt_s = f.result()
            proc_s.append(dt_s)
            if res is None:
                counts["rx_fail"] += 1
                continue
            h, lag, q = res
            counts["rx_ok"] += 1
            rx_rows.append(dict(ev=ev, ok=True, h=h, lag=lag, q=q, w0=w0, acq=is_acq, lat_used=lat_used))
        pending = still

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
                    if sfn is not None and sfn.feed(row, k) is False:
                        print(f"[WARN] SFN check failed: SSB k={k} decoded SFN {row['sfn']} hrf {row['hrf']}")
                nr_f.write(json.dumps(row) + "\n")
            now = radio.now_ticks()
            locked, why = trk.locked(now)
            if state == "SEARCHING" and trk.pts:
                state = "TRACKING"
                print("[TRACKING] first PSS received")
            if state == "TRACKING" and locked and sfn is not None and sfn.try_lock():
                K0 = sfn.K0
                radio.pbch_force = False
                state = "LOCKED"
                next_n = trk.k_last + K0 + 1
                print(f"[NUMBERING] SFN from PBCH/MIB: {sfn.decodes} decodes ({sfn.fails} CRC failures), MIB {sfn.mib}")
                print(f"[LOCKED] {trk.ppm():+.3f} ppm, resid {trk.fit[4]:.1f} ns | latest SSB n={cm(trk.k_last + K0)}")
            if state == "TRACKING" and locked and sfn is None:
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
            if sfn is not None and K0 is not None:
                numbering.update(checks=sfn.checks, mismatches=sfn.mismatches, decodes=sfn.decodes,
                                 crc_fails=sfn.fails, mib_changes=sfn.mib_changes)
            if K0 is not None and time.monotonic() - last_anchor > 10:
                last_anchor = time.monotonic()
                clock.refresh()
                if sfn is None:
                    k = trk.k_last
                    numbering["checks"] += 1
                    if gnum(trk.pts[k])[0] != k + K0:
                        numbering["mismatches"] += 1
                        print("[WARN] numbering check failed")

            # --- scheduler: per SSB n, n_sub TX and n_sub RX events when the cycle is in a session
            if next_n is not None:
                nl = trk.k_last + K0
                while next_n <= nl + L:
                    n = next_n
                    next_n += 1
                    cyc = cm(n) // CYCLE
                    T = trk.predict(n - K0)
                    if not in_session(args, cyc):
                        continue
                    for sub in range(args.n_sub):
                        for role, off_ms in (("TX", args.own_off), ("RX", args.partner_off)):
                            t_ev = int(np.round((T + off_ms * 1e-3 * R + sub * per_b) / G) * G)
                            u = clock.unix(t_ev)
                            if not (args.tx_start_unix <= u < t_end):
                                continue
                            now = radio.now_ticks()
                            ev = dict(eid=eid, n=n, cycle=cyc, sub=sub, role=role, t_ev=t_ev, unix=u,
                                      T_pred=float(T), ppm=trk.ppm(), margin_ms=(t_ev - now) / R * 1e3)
                            eid += 1
                            locked, why = trk.locked(now)
                            if not locked or t_ev - guard_t - now < min_margin:
                                ev["state"] = "MISSED_LATE" if locked else f"MISSED_NO_LOCK:{why}"
                                counts["tx_late" if role == "TX" else "rx_late"] += 1
                            elif role == "TX":
                                ev["state"] = "ARMED"
                                radio.send_burst(ev["eid"], t_ev, ev)
                                counts["tx_armed"] += 1
                            else:
                                ev["state"] = "ARMED"
                                radio.arm_observer(ev["eid"], t_ev - guard_t, n_win)
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
                it = submit(ev, ob)
                if it is not None:
                    pending.append(it)
            collect()
            if not acq["done"] and acq["n_try"] >= 20 * max(args.acq_bursts, 1) and acq["n_try"] % 400 == 0:
                print(f"[LATENCY] partner found in only {len(acq['lat'])} of {acq['n_try']} windows "
                      f"(+-{args.guard_us:.0f} us): check partner TX, antennas, gains")

            if time.monotonic() - last_print > 5 and K0 is not None:
                last_print = time.monotonic()
                q = [r["q"] for r in rx_rows[-200:]]
                lags = [r["lag"] for r in rx_rows[-200:]]
                msg = (f"  TX {counts['tx_armed']} (late {counts['tx_late']}) | RX {counts['rx_ok']} ok, "
                       f"{counts['rx_fail']} fail, late {counts['rx_late']}, err {counts['rx_err']} | "
                       f"queue {len(pending)}")
                if q:
                    msg += (f" | peak/floor {np.median(q):.0f} | lag {np.median(lags):+.2f} samp "
                            f"(std {np.std(lags) / Rw * 1e9:.1f} ns) | extract {np.median(proc_s[-200:]) * 1e3:.1f} ms")
                msg += f" | {trk.ppm():+.3f} ppm | 5G ovf {radio.stats['nr_overflows']} disc " \
                       f"{radio.stats['nr_discontinuities']}"
                if not acq["done"]:
                    msg += f" | LATENCY SEARCH: found {len(acq['lat'])}/{acq['n_try']}"
                print(msg)
            if K0 is None and time.monotonic() - t_launch > 90:
                extra = (f", PBCH decodes {sfn.decodes}, CRC failures {sfn.fails}, candidates {sfn.cand[-5:]}"
                         if sfn is not None else "")
                raise RuntimeError(f"Tracker/numbering did not lock in 90 s ({why}{extra})")
            time.sleep(0.001)
    except StopIteration:
        pass
    except KeyboardInterrupt:
        print("\nInterrupted: writing results")
    finally:
        t_wait = time.monotonic() + 1.0
        while time.monotonic() < t_wait or (args.sim and radio.pending_obs() and
                                            time.monotonic() < t_wait + 60):
            try:
                ob = radio.obs_q.get(timeout=0.05)
            except queue.Empty:
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
            row = radio.nr_q.get()
            if row.get("valid"):
                k_actual.setdefault(trk.k_of(row["event_device_ticks"]), row["event_device_ticks"])
            nr_f.write(json.dumps(row) + "\n")
        nr_f.close()
        tx_f.close()

    rx_rows.sort(key=lambda r: r["ev"]["t_ev"])
    for r in rx_rows:                       # every lag relative to the final latency estimate
        r["lag"] += (r["lat_used"] - lat_box[0]) / tpw
    if rx_rows:
        ev = [r["ev"] for r in rx_rows]
        T_act = np.array([k_actual.get(e["n"] - K0, np.nan) for e in ev], dtype=float)
        np.savez_compressed(
            out_dir / "sense_rx.npz",
            h=np.stack([r["h"] for r in rx_rows]),              # (burst, pulse, rx, tx)
            lag=np.array([r["lag"] for r in rx_rows]),          # fractional WiFi samples vs expected
            q=np.array([r["q"] for r in rx_rows]),
            t_ev=np.array([e["t_ev"] for e in ev], dtype=np.int64),
            w0=np.array([r["w0"] for r in rx_rows], dtype=np.int64),
            T_pred=np.array([e["T_pred"] for e in ev]), T_actual=T_act,
            n=np.array([e["n"] for e in ev], dtype=np.int64), cycle=np.array([e["cycle"] for e in ev]),
            sub=np.array([e["sub"] for e in ev]), half=np.array([e["sub"] for e in ev]),
            unix=np.array([e["unix"] for e in ev]), ppm=np.array([e["ppm"] for e in ev]),
            rate=Rw, tick_rate=R, pulse_spacing=part_b.S, rx_chans=np.array(args.rx_chans),
            tx_chans=np.array(args.tx_chans), wifi_freq=args.wifi_freq, ap=args.ap,
            integer_extract=args.integer_extract, acq=np.array([r["acq"] for r in rx_rows]),
            latency_ns=acq["latency_ns"])
    ev_all = list(events.values())
    tx_ev = [e for e in ev_all if e["role"] == "TX"]
    ack = sum(1 for e in radio.async_events if e["event"] == "burst_ack")
    bad = {}
    for e in radio.async_events:
        if e["event"] != "burst_ack":
            bad[e["event"]] = bad.get(e["event"], 0) + 1
    m = [e["margin_ms"] for e in tx_ev if e["state"] == "ARMED"]
    lag_ns = [r["lag"] / Rw * 1e9 for r in rx_rows]
    if sfn is not None:
        numbering.update(sfn.summary())
        summary["sfn_checks"] = sfn.log
    res = dict(counts=counts, async_ack=ack, async_other=bad, numbering=numbering, radio_stats=radio.stats,
               tracker=dict(ppm=trk.ppm(), resid_ns=trk.fit[4] if trk.fit else None),
               margin_ms=base.stats_ns(m), lag_ns=base.stats_ns(lag_ns),
               extract_ms=base.stats_ns(np.array(proc_s) * 1e3),
               latency=dict(done=acq["done"], latency_ns=acq["latency_ns"], spread_ns=acq.get("spread_ns"),
                            found=len(acq["lat"]), tried=acq["n_try"]),
               peak_over_floor_median=float(np.median([r["q"] for r in rx_rows])) if rx_rows else None)
    summary["results"] = res
    summary["finished_unix"] = time.time()
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, default=float))
    print("\n=== Summary ===")
    print(f"AP {args.ap}: {counts} | TX acks {ack} {bad} | numbering {numbering}")
    print(f"margin [ms] {res['margin_ms']} | lag [ns] {res['lag_ns']} | peak/floor median "
          f"{res['peak_over_floor_median']} | extraction [ms] {res['extract_ms']} | tracker {res['tracker']}")
    print(f"radio {radio.stats}")
    expected = counts["rx_armed"]
    heard = (res["peak_over_floor_median"] or 0) > 10 and acq["done"]
    ok = (counts["tx_late"] == 0 and counts["rx_late"] == 0 and not bad and expected
          and counts["rx_ok"] >= 0.99 * expected and numbering["mismatches"] == 0 and heard)
    print(f"Verdict: {'PASS' if ok else 'CHECK'} (0 late, no TX errors, >=99 % windows extracted, numbering OK, "
          f"partner heard: peak/floor median > 10)")
    print(f"Saved {out_dir}/ (sense_rx.npz, tx_log.jsonl, nr_events.jsonl, summary.json)")


if __name__ == "__main__":
    main()
