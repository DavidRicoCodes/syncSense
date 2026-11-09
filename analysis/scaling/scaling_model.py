"""Scaling model: S5G (5G-timed TDMA) vs B1 (free-running + follow), B2 (WiFi follower chains)
and B3 (802.11bf-style negotiated, trigger-based sensing) for N APs. Uses the best measured
values of each scheme from the X410 testbed (2026-10-14/15)."""
import json, numpy as np
rng = np.random.default_rng(1)
# ---------------- measured inputs (best runs)
T_S = 0.40e-3          # sensing burst (8 ZC pulses x 50 us), same signal for every scheme
GUARD = 50e-6          # RX guard used in the testbed
P = 5e-3               # 200 Hz per AP
SYNC_S5G_NS = 6.0      # t16, empty room (best); t11 8.9 ns
SYNC_B2_HOP_NS = 4.9   # c3_b2, per hop, leader<->follower that hear each other well
LAT_SPREAD_NS = 175.0  # half-range of measured TX->air->RX latencies (1157..1507 ns) -> per-hop bias if not two-way calibrated
B2_FIRST_TX_S = 0.34   # c3_b2: follower first TX after start (measured)
B2_LOST_S = 40 * P     # follower declares the leader lost after 40 misses
# ---------------- 802.11 MAC constants (5 GHz OFDM)
SIFS, DIFS, SLOT, CWMIN = 16e-6, 34e-6, 9e-6, 15
PRE, RATE = 20e-6, 6e6                      # legacy preamble, basic rate for control frames
def frame(nbytes): return PRE + nbytes * 8 / RATE
def access(): return DIFS + CWMIN / 2 * SLOT

# ---------------- M3: airtime per sensing round (every AP transmits once, all others listen)
def round_airtime(N, scheme, reports=False):
    if scheme == "S5G":
        return N * (T_S + 2 * GUARD), 0.0                         # no control at all
    if scheme == "B2":
        return N * (T_S + 2 * GUARD), 0.0                         # while everybody senses: same as S5G
    if scheme == "B3":   # 802.11bf TB sensing, each AP initiates once (DL sounding to N-1 responders)
        per = (access() + frame(24 + 6 * (N - 1)) + SIFS + frame(14) + SIFS     # sensing polling trigger + CTS
               + frame(21 + 4 * (N - 1)) + SIFS + T_S)                         # sensing NDPA + NDP
        if reports:
            per += SIFS + frame(24 + 6 * (N - 1)) + SIFS + (N - 1) * (frame(200) + SIFS)   # report trigger + small reports
        ctrl = per - T_S
        return N * per, N * ctrl
def comm_fraction(N, scheme, rate_hz=200, reports=False):
    t, ctrl = round_airtime(N, scheme, reports)
    used = t * rate_hz
    return max(0.0, 1 - used), used, ctrl * rate_hz
def max_N(scheme, rate_hz=200, budget=1.0, reports=False):
    n = 1
    while n < 200 and round_airtime(n + 1, scheme, reports)[0] * rate_hz <= budget: n += 1
    return n
# idle airtime: sensing paused, keeping sync alive (B2 leader beacons; B3 nothing until next session; S5G nothing)
def idle_airtime(scheme, beacon_hz=200):
    return {"S5G": 0.0, "B2": T_S * beacon_hz, "B3": 0.0}[scheme]

# ---------------- M1: connectivity in a building (hidden APs), TGax enterprise path loss
def pathloss(d, walls, f=5.18):
    d = np.maximum(d, 1.0)
    return 40.05 + 20 * np.log10(f / 2.4) + 20 * np.log10(np.minimum(d, 10)) + (d > 10) * 35 * np.log10(d / 10) + 7 * walls
def deployment(N, W=60.0, H=30.0, room=(10.0, 10.0)):
    pts = np.column_stack([rng.uniform(0, W, N), rng.uniform(0, H, N)])
    return pts
def walls_between(a, b, room=(10.0, 10.0)):
    return abs(int(a[0] // room[0]) - int(b[0] // room[0])) + abs(int(a[1] // room[1]) - int(b[1] // room[1]))
def b2_reach(pts, ptx=20.0, sens=-82.0, max_hops=99):
    N = len(pts); A = np.zeros((N, N), bool)
    for i in range(N):
        for j in range(N):
            if i != j:
                d = np.linalg.norm(pts[i] - pts[j]); A[i, j] = ptx - pathloss(d, walls_between(pts[i], pts[j])) >= sens
    c = np.array([30.0, 15.0]); leader = int(np.argmin(np.linalg.norm(pts - c, axis=1)))
    hops = np.full(N, -1); hops[leader] = 0; frontier = [leader]
    while frontier:
        nxt = []
        for u in frontier:
            for v in np.flatnonzero(A[u]):
                if hops[v] < 0: hops[v] = hops[u] + 1; nxt.append(v)
        frontier = nxt
    ok = (hops >= 0) & (hops <= max_hops)
    return ok, hops
def m1(Ns=(2, 4, 8, 12, 16, 24, 32), trials=400, sens=-82.0, room=10.0):
    out = []
    for N in Ns:
        frac, hmax, hmean = [], [], []
        for _ in range(trials):
            pts = deployment(N); ok, h = b2_reach(pts, sens=sens)
            frac.append(ok.mean()); hmax.append(h[ok].max()); hmean.append(h[ok & (h > 0)].mean() if (ok & (h > 0)).any() else 0)
        out.append(dict(N=N, b2_sync_fraction=float(np.mean(frac)), b2_all_synced=float(np.mean(np.array(frac) == 1.0)),
                        b2_max_hops_mean=float(np.mean(hmax)), b2_mean_hops=float(np.mean(hmean)), s5g_sync_fraction=1.0))
    return out

# ---------------- M2: sync error vs hops
def m2(hops=range(0, 9), trials=20000):
    out = []
    for k in hops:
        noise = rng.normal(0, SYNC_B2_HOP_NS, (trials, max(k, 1)))[:, :k].sum(1) if k else np.zeros(trials)
        bias = rng.uniform(-LAT_SPREAD_NS, LAT_SPREAD_NS, (trials, max(k, 1)))[:, :k].sum(1) if k else np.zeros(trials)
        out.append(dict(hops=k, b2_calibrated_ns=float(np.percentile(abs(noise), 95)), b2_uncalibrated_ns=float(np.percentile(abs(noise + bias), 95)),
                        s5g_ns=float(1.96 * SYNC_S5G_NS)))
    return out

# ---------------- M4: B1 (free-running) collision fraction with N APs
def m4(Ns=range(2, 17)):
    w = 2 * (T_S + GUARD) / P
    return [dict(N=N, b1_lost_fraction=float(1 - (1 - w) ** (N - 1)), s5g_lost_fraction=0.0 if N * (T_S + 2 * GUARD) <= P else None) for N in Ns]

# ---------------- M5: coordination frames
def m5(Ns=range(2, 17), rate_hz=200):
    out = []
    for N in Ns:
        setup_b2 = 4 * N * (N - 1) / 2            # MAPC discovery req/resp + negotiation req/resp per AP pair
        per_s_b3 = N * rate_hz * 3                # polling trigger, CTS, NDPA per measurement instance
        out.append(dict(N=N, s5g_setup_frames=0, s5g_frames_per_s=0, b2_setup_frames=setup_b2, b2_frames_per_s=0, b3_setup_frames=setup_b2, b3_frames_per_s=per_s_b3))
    return out

# ---------------- M1b: pairs that interfere with each other's sensing but cannot be coordinated by B2
def m1b(Ns=(4, 8, 12, 16, 24, 32), trials=300, sens=-82.0, i_th=-93.0):
    out = []
    for N in Ns:
        bad, tot = 0, 0; any_bad = 0
        for _ in range(trials):
            pts = deployment(N); ok, h = b2_reach(pts, sens=sens)
            # components of the B2 sync graph (each could have its own local leader)
            Adec = np.zeros((N, N), bool); Aint = np.zeros((N, N), bool)
            for i in range(N):
                for j in range(N):
                    if i != j:
                        prx = 20 - pathloss(np.linalg.norm(pts[i] - pts[j]), walls_between(pts[i], pts[j]))
                        Adec[i, j] = prx >= sens; Aint[i, j] = prx >= i_th
            comp = -np.ones(N, int); c = 0
            for s0 in range(N):
                if comp[s0] >= 0: continue
                st = [s0]; comp[s0] = c
                while st:
                    u = st.pop()
                    for v in np.flatnonzero(Adec[u]):
                        if comp[v] < 0: comp[v] = c; st.append(v)
                c += 1
            iu = np.triu_indices(N, 1); inter = Aint[iu]; diff = comp[iu[0]] != comp[iu[1]]
            b = int(np.sum(inter & diff)); bad += b; tot += int(inter.sum()); any_bad += b > 0
        out.append(dict(N=N, interfering_pairs_uncoordinated=bad / max(tot, 1), deployments_with_conflict=any_bad / trials))
    return out
# ---------------- M2b: S5G static bias from different distances to the gNB (APs on a 60 x 30 m floor)
# |d_i - d_j| <= ||p_i - p_j|| (triangle inequality); distant gNB -> planar wavefront, uniform azimuth.
# v2 (2026-10-19): also conditioned on the pairs that matter for (C1): pairs that interfere (>= -93 dBm, TGax
# enterprise model) and pairs closer than 15 m (typical sensing links); plus the bias versus AP separation.
def s5g_gnb_bias(trials=40000, i_th=-93.0):
    a = rng.uniform(0, 1, (trials, 2)) * [60, 30]; b = rng.uniform(0, 1, (trials, 2)) * [60, 30]
    th = rng.uniform(0, 2 * np.pi, trials); u = np.column_stack([np.cos(th), np.sin(th)])
    sep = np.linalg.norm(a - b, axis=1)
    d = np.abs(np.sum((a - b) * u, 1)) / 299792458.0 * 1e9
    walls = np.array([walls_between(a[k], b[k]) for k in range(trials)])
    prx = 20 - pathloss(sep, walls)
    inter = prx >= i_th; close = sep < 15
    pc = lambda m: dict(p50_ns=float(np.percentile(d[m], 50)), p95_ns=float(np.percentile(d[m], 95)), frac_pairs=float(m.mean()))
    bins = np.arange(0, 66, 5); curve = []
    for lo, hi in zip(bins[:-1], bins[1:]):
        m = (sep >= lo) & (sep < hi)
        if m.sum() > 50: curve.append(dict(sep_m=float((lo + hi) / 2), p50_ns=float(np.percentile(d[m], 50)), p95_ns=float(np.percentile(d[m], 95)), bound_ns=float(hi / 0.299792458)))
    return dict(p50_ns=float(np.percentile(d, 50)), p95_ns=float(np.percentile(d, 95)),
                all_pairs=pc(np.ones(trials, bool)), interfering_pairs=pc(inter), pairs_lt15m=pc(close), vs_separation=curve)

# ---------------- M6: extended airtime cases (sensing waveform, rate, MAPC estimate, reduced S5G guard)
def ctrl_mapc(N):   # 802.11bn Co-TDMA-like: access + ICF (MU-RTS TXS) + ICR (CTS) per shared TXOP (estimate)
    return access() + frame(20 + 6 * (N - 1)) + SIFS + frame(14) + SIFS
def ctrl_tb(N, reports=False):
    c = access() + frame(24 + 6 * (N - 1)) + SIFS + frame(14) + SIFS + frame(21 + 4 * (N - 1)) + SIFS
    if reports: c += SIFS + frame(24 + 6 * (N - 1)) + SIFS + (N - 1) * (frame(200) + SIFS)
    return c
def m6(Ns=range(2, 17), rates=(10, 50, 100, 200)):
    out = []
    for TS, wf in ((0.4e-3, "zc_0.4ms"), (80e-6, "he_ndp_80us")):
        for f in rates:
            for N in Ns:
                row = dict(waveform=wf, rate_hz=f, N=N,
                           S5G=N * f * (TS + 2 * GUARD), S5G_g1us=N * f * (TS + 2 * 1e-6),
                           TB=N * f * (TS + ctrl_tb(N)), TBrep=N * f * (TS + ctrl_tb(N, True)),
                           MAPC=N * f * (TS + 2 * GUARD + ctrl_mapc(N)),
                           TB_ctrl=N * f * ctrl_tb(N), TBrep_ctrl=N * f * ctrl_tb(N, True), MAPC_ctrl=N * f * ctrl_mapc(N))
                out.append(row)
    return out
def maxN_ext(TS, f, scheme):
    def used(N):
        return dict(S5G=N * f * (TS + 2 * GUARD), S5G_g1us=N * f * (TS + 2e-6), TB=N * f * (TS + ctrl_tb(N)),
                    TBrep=N * f * (TS + ctrl_tb(N, True)), MAPC=N * f * (TS + 2 * GUARD + ctrl_mapc(N)))[scheme]
    n = 0
    while n < 5000 and used(n + 1) <= 1.0: n += 1
    return n

res = {}
Ns = list(range(2, 17))
res["conflicts_m82"] = m1b(); res["conflicts_m95"] = m1b(sens=-95.0)
res["s5g_gnb_bias"] = s5g_gnb_bias()
res["airtime"] = [dict(N=N, **{f"{s}_sensing_airtime": comm_fraction(N, s)[1] for s in ("S5G", "B2", "B3")},
                       B3_control_airtime=comm_fraction(N, "B3")[2], B3r_sensing_airtime=comm_fraction(N, "B3", reports=True)[1],
                       **{f"{s}_comm_fraction": comm_fraction(N, s)[0] for s in ("S5G", "B3")}, B3r_comm_fraction=comm_fraction(N, "B3", reports=True)[0]) for N in Ns]
res["maxN"] = {f"{s}@{r}Hz": max_N(s, r) for s in ("S5G", "B3") for r in (50, 100, 200)} | {f"B3reports@{r}Hz": max_N("B3", r, reports=True) for r in (50, 100, 200)}
res["per_instance_overhead_us"] = {N: round((round_airtime(N, "B3")[1] / N) * 1e6, 1) for N in (2, 4, 8, 16)}
res["idle_airtime"] = {s: idle_airtime(s) for s in ("S5G", "B2", "B3")}
res["connectivity"] = m1() ; res["connectivity_sens95"] = m1(sens=-95.0)
res["sync_vs_hops"] = m2()
res["b1_collisions"] = m4()
res["frames"] = m5()
res["airtime_ext"] = m6()
res["maxN_ext"] = [dict(waveform=wf, rate_hz=f, **{sc: maxN_ext(TS, f, sc) for sc in ("S5G", "S5G_g1us", "TB", "TBrep", "MAPC")})
                   for TS, wf in ((0.4e-3, "zc_0.4ms"), (80e-6, "he_ndp_80us")) for f in (10, 50, 100, 200)]
res["per_instance_ctrl_us"] = [dict(N=N, TB=ctrl_tb(N) * 1e6, TBrep=ctrl_tb(N, True) * 1e6, MAPC=ctrl_mapc(N) * 1e6) for N in range(2, 17)]
res["leader_failure"] = dict(b2_outage_s=dict(detect=B2_LOST_S, reacquire_and_first_tx=B2_FIRST_TX_S, total_min=B2_LOST_S + B2_FIRST_TX_S), s5g_outage_s=0.0)
res["inputs"] = dict(T_S=T_S, GUARD=GUARD, P=P, SYNC_S5G_NS=SYNC_S5G_NS, SYNC_B2_HOP_NS=SYNC_B2_HOP_NS, LAT_SPREAD_NS=LAT_SPREAD_NS, B2_FIRST_TX_S=B2_FIRST_TX_S)
json.dump(res, open("scaling_results.json", "w"), indent=1, default=float)
print("per-instance B3 overhead (us):", res["per_instance_overhead_us"])
print("max N:", res["maxN"])
for r in res["airtime"][::2]: print({k: round(v, 3) if isinstance(v, float) else v for k, v in r.items()})
for r in res["connectivity"]: print("conn -82", {k: round(v, 2) if isinstance(v, float) else v for k, v in r.items()})
for r in res["connectivity_sens95"]: print("conn -95", {k: round(v, 2) if isinstance(v, float) else v for k, v in r.items()})
for r in res["sync_vs_hops"]: print({k: round(v, 1) if isinstance(v, float) else v for k, v in r.items()})
for r in res["b1_collisions"][::3]: print(r)
print(res["leader_failure"], res["idle_airtime"])
for r in res["conflicts_m82"]: print("conflict -82", r)
for r in res["conflicts_m95"]: print("conflict -95", r)
b = res["s5g_gnb_bias"]; print("S5G gNB bias all", b["all_pairs"], "interfering", b["interfering_pairs"], "<15m", b["pairs_lt15m"])
for r in res["maxN_ext"]: print("maxN", r)
