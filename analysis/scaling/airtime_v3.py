"""Scaling model v3 (2026-10-19): fair airtime comparison.
- Same sounding in every scheme; reference = standard HE NDP (80 us); ZC burst of the testbed (0.4 ms) as secondary case.
- S5G guard G = 3 us (supported by the measured residual: <= 70 ns LoS, <= 0.45 us degraded, Section 10.8), plus the
  testbed's 50 us capture window as a reference.
- 802.11bf TB per AP initiator: channel access + sensing polling trigger + CTS-to-self + NDPA + 3 SIFS + NDP, control frames at
  24 Mb/s (legacy preamble 20 us). AP-AP sensing: results travel over each AP's wired backhaul, so no over-the-air reports by
  default; variant with over-the-air reports: report trigger + (N-1) HE PPDUs of 1.5 kB CSI at 144 Mb/s (40 us preamble).
- Negotiated ideal (802.11bn MAPC / TB best case): ONE negotiated TXOP per period shared by all APs: control once, then N NDPs
  separated by SIFS. Requires all APs to hear each other, same channel, same network or mutual trust, Wi-Fi 8.
- Free-running (B1) collision window 2 (T_S + G_b1)/P with G_b1 = 50 us (search window of the follower)."""
import json, math
SIFS, DIFS, SLOT, CWMIN = 16e-6, 34e-6, 9e-6, 15
ACC = DIFS + CWMIN / 2 * SLOT
CRATE = 24e6
def leg(nb, rate=CRATE): return 20e-6 + nb * 8 / rate
def he(nb, rate): return 40e-6 + nb * 8 / rate
WAVES = {"he_ndp_80us": 80e-6, "zc_0.4ms": 0.4e-3}
G_S5G, G_TESTBED, G_B1 = 3e-6, 50e-6, 50e-6
P = 5e-3
def ctrl_tb(N):        # per instance, AP-AP (no over-the-air reports)
    return ACC + leg(24 + 6 * (N - 1)) + leg(14) + leg(21 + 4 * (N - 1)) + 3 * SIFS
def reports(N):
    return leg(24 + 6 * (N - 1)) + 2 * SIFS + (N - 1) * (he(1500, 144e6) + SIFS)
def used(N, f, TS, scheme):
    if scheme == "S5G": return N * f * (TS + 2 * G_S5G)
    if scheme == "S5G_g50": return N * f * (TS + 2 * G_TESTBED)
    if scheme == "TB": return N * f * (ctrl_tb(N) + TS)
    if scheme == "TBrep": return N * f * (ctrl_tb(N) + reports(N) + TS)
    if scheme == "IDEAL": return f * (ctrl_tb(N) + N * (TS + SIFS))
SCHEMES = ("S5G", "S5G_g50", "TB", "TBrep", "IDEAL")
def maxN(f, TS, scheme):
    n = 0
    while n < 5000 and used(n + 1, f, TS, scheme) <= 1.0: n += 1
    return n
res = dict(notes=__doc__, inputs=dict(G_S5G=G_S5G, G_TESTBED=G_TESTBED, CRATE=CRATE, P=P))
res["airtime"] = [dict(waveform=w, rate_hz=f, N=N, **{"used_" + s: used(N, f, TS, s) for s in SCHEMES},
                       **{"free_" + s: max(0.0, 1 - used(N, f, TS, s)) for s in SCHEMES})
                  for w, TS in WAVES.items() for f in (10, 50, 100, 200) for N in range(2, 17)]
res["maxN"] = [dict(waveform=w, rate_hz=f, **{s: maxN(f, TS, s) for s in SCHEMES}) for w, TS in WAVES.items() for f in (10, 50, 100, 200)]
res["ctrl_per_measurement_us"] = [dict(N=N, TB=ctrl_tb(N) * 1e6, TBrep=(ctrl_tb(N) + reports(N)) * 1e6, IDEAL=ctrl_tb(N) / N * 1e6, S5G=0.0) for N in range(2, 17)]
res["b1"] = [dict(waveform=w, N=N, b1_lost=1 - (1 - 2 * (TS + G_B1) / P) ** (N - 1)) for w, TS in WAVES.items() for N in range(2, 17)]
res["idle_b2"] = {w: TS * 200 for w, TS in WAVES.items()}
json.dump(res, open("airtime_v3.json", "w"), indent=1)
print("ctrl TB (us) N=2,8,16:", [round(ctrl_tb(N) * 1e6) for N in (2, 8, 16)], " +reports:", [round((ctrl_tb(N) + reports(N)) * 1e6) for N in (2, 8, 16)])
for r in res["maxN"]: print("maxN", r)
for w in WAVES:
    for f, N in ((10, 4), (50, 8), (100, 4), (100, 8), (200, 2), (200, 4)):
        TS = WAVES[w]; print(w, f, N, {s: round(100 * used(N, f, TS, s), 1) for s in SCHEMES})
for b in res["b1"]:
    if b["N"] in (2, 5, 8, 11): print("B1", b)
print("idle B2", res["idle_b2"])
