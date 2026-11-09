"""Section VII numbers (v2, after review). Sign: e_AB = (l_B - l_A)/2, where l_X is the arrival of the partner's burst at AP X
relative to its expected instant (calibrated latency). Bursts with valid detection (q >= 20), acquisition bursts excluded."""
import json, os, numpy as np
R = os.path.expanduser("~/sync_backups/x410_two_ap_results_20261014/runs")
RUNS = [("t9_tiles", "person on 16 tiles", "2026-10-14"), ("t10_tiles", "tiles; B's 5G antenna degraded", "2026-10-14"), ("t11_nrw4", "empty room", "2026-10-14"),
        ("t12_fsync", "empty, then walking; f-sync", "2026-10-14"), ("t13_tiles_fs", "person on 16 tiles; f-sync", "2026-10-14"),
        ("t14_track", "person walking; f-sync", "2026-10-14"), ("t15c_check", "empty room", "2026-10-15"),
        ("t16_tiles", "person on 16 tiles; f-sync", "2026-10-15"), ("t18_zc80_g3", "empty room; f-sync", "2026-10-21")]
TILES = [(25 + 25 * i + 3, 45 + 25 * i - 3) for i in range(16)]; EMPTY = [(3, 17), (488, 507)]; BEHIND = [(430, 480)]
def burst_cfo(h, dt_p):
    zp = np.einsum("bkrc,brc->bk", h, np.conj(h[:, 0])); ph = np.unwrap(np.angle(zp), axis=1)
    return np.polyfit(np.arange(h.shape[1]) * dt_p, ph.T, 1)[0] / (2 * np.pi)
def tdev(x, dt, tau):
    m = int(round(tau / dt))
    if 3 * m >= len(x): return float("nan")
    c = np.cumsum(np.r_[0, x]); s1 = (c[m:] - c[:-m]) / m; d = s1[2 * m:] - 2 * s1[m:-m] + s1[:-2 * m]
    return float(np.sqrt(np.mean(d ** 2) / 6))
TAUS = [0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 30, 60, 120]
out = dict(runs=[], series={}, cfo={}, ccdf={}, tdev={})
for run, desc, day in RUNS:
    Z, S, L = {}, {}, {}; cc = None
    for ap in "AB":
        Z[ap] = np.load(f"{R}/{run}_{ap}/sense_rx.npz"); S[ap] = json.load(open(f"{R}/{run}_{ap}/summary.json"))
    t0 = S["A"]["args"]["tx_start_unix"]; rate = float(Z["A"]["rate"]); raw = []
    for ap in "AB":
        z = Z[ap]; g = (~z["acq"]) & (z["q"] >= 20); lag = z["lag"][g] / rate * 1e9; raw.append(np.abs(lag))
        n = z["n"][g].astype(int) % 512; u = z["unix"][g]; x = u - n * 0.02
        if cc is None: cc = np.median(x % 10.24)
        ep = np.round((x - cc) / 10.24).astype(int)
        L[ap] = {(a, b, e): (l, uu - t0, uu) for a, b, e, l, uu in zip(n, z["sub"][g].astype(int), ep, lag, u)}
    com = sorted(set(L["A"]) & set(L["B"]), key=lambda k: L["A"][k][1])
    la = np.array([L["A"][k][0] for k in com]); lb = np.array([L["B"][k][0] for k in com]); t = np.array([L["A"][k][1] for k in com])
    ua = np.array([L["A"][k][2] for k in com]); ub = np.array([L["B"][k][2] for k in com]); ssb = np.array([k[0] + 512 * k[2] for k in com])
    e = (lb - la) / 2; path = (la + lb) / 2; sec = np.floor(t)
    _, inv = np.unique(ssb, return_inverse=True); cnt = np.bincount(inv); mean = np.bincount(inv, e) / cnt; ok = cnt[inv] >= 2
    within = float(np.sqrt(np.sum((e - mean[inv])[ok] ** 2) / (ok.sum() - len(np.unique(inv[ok])))))
    tg = np.arange(t[0], t[-1], 0.005); eg = np.interp(tg, t, e) - e.mean()
    f = np.fft.rfftfreq(len(eg), 0.005); P = np.abs(np.fft.rfft(eg)) ** 2; tot = P[1:].sum()
    ks = np.unique(sec); ks = ks[ks >= 0]; med1 = np.array([np.median(e[sec == k]) for k in ks])
    rawall = np.concatenate(raw)
    s = {ap: S[ap]["results"] for ap in "AB"}; aps = "A" if run == "t18_zc80_g3" else "AB"
    after30 = []
    for a in aps:
        ev = [json.loads(l) for l in open(f"{R}/{run}_{a}/tx_log.jsonl") if '"role": "TX"' in l]
        uu = np.array([v["unix"] - t0 for v in ev]); miss = np.array([v["state"].startswith("MISSED") for v in ev]); m = uu >= 30
        after30.append((int(miss[m].sum()), int(m.sum())))
    late = sum(s[a]["counts"]["tx_late"] for a in aps); armed = sum(s[a]["counts"]["tx_armed"] + s[a]["counts"]["tx_late"] for a in aps)
    numb = {k: sum(s[a]["numbering"][k] for a in aps) for k in ("decodes", "checks", "mismatches", "crc_fails")}
    lat = {a: float(Z[a]["latency_ns"]) for a in "AB"}   # from the npz: t18 B summary was overwritten
    row = dict(run=run, desc=desc, day=day, duration_s=S["A"]["args"]["duration_s"], n_pulses=Z["A"]["h"].shape[1], guard_us=S["A"]["args"]["guard_us"],
               bursts=int(sum(len(r) for r in raw)), pairs=int(len(e)), sigma=float(e.std()), median=float(np.median(e)), wifi_noise=within,
               tdev1=tdev(eg, .005, 1), tdev30=tdev(eg, .005, 30), max_raw=float(rawall.max()), p999_raw=float(np.percentile(rawall, 99.9)),
               wander_1s=[float(med1.min() - np.median(e)), float(med1.max() - np.median(e))], path_1s_sd=float(np.std([np.median(path[sec == k]) for k in ks])),
               skipped_total=late / armed, skipped_after30=sum(a for a, b in after30) / sum(b for a, b in after30), numbering=numb,
               resid_ns={a: s[a]["tracker"]["resid_ns"] for a in aps}, uncal_bias_ns=None if None in lat.values() else (lat["A"] - lat["B"]) / 2,
               host_clock_ms=float(np.median((ua - ub) - 0.0025) * 1e3),
               lines=[(round(float(f[i]), 2), round(float(P[i] / tot), 3)) for i in np.argsort(P[1:])[::-1][:5] + 1])
    if "tiles" in desc:
        row["tile_medians"] = [float(np.median(e[(t >= a) & (t < b)])) for a, b in TILES]
        row["empty_segments"] = [dict(s=a, e=b, sigma=float(e[(t >= a) & (t < b)].std()), median=float(np.median(e[(t >= a) & (t < b)]))) for a, b in EMPTY]
    if run == "t12_fsync": row["empty_segments"] = [dict(s=0, e=60, sigma=float(e[t < 60].std()), median=float(np.median(e[t < 60])))]
    if run == "t18_zc80_g3":
        band = lambda f0: P[(f > f0 - 0.06) & (f < f0 + 0.06)].sum() / tot
        row["line_fraction"] = {fr: float(band(fr)) for fr in (2.29, 3.41, 4.59, 6.82, 6.88)}
    out["runs"].append(row)
    out["series"][run] = dict(t=ks.tolist(), med=med1.tolist(), p10=[float(np.percentile(e[sec == k], 10)) for k in ks], p90=[float(np.percentile(e[sec == k], 90)) for k in ks])
    x = np.sort(rawall); p = 1 - np.arange(len(x)) / len(x); keep = (np.arange(len(x)) % max(1, len(x) // 3000) == 0) | (p <= 0.01)
    out["ccdf"][run] = dict(x=x[keep].tolist(), p=p[keep].tolist())
    out["tdev"][run] = {str(tau): tdev(eg, .005, tau) for tau in TAUS}
    if run in ("t11_nrw4", "t12_fsync", "t16_tiles", "t18_zc80_g3"):
        cf = {}
        for dn, rx in (("A->B", "B"), ("B->A", "A")):
            z = Z[rx]; g = (~z["acq"]) & (z["q"] >= 20); c = burst_cfo(z["h"][g].astype(complex), int(z["pulse_spacing"]) / rate); u = z["unix"][g] - t0
            sc = np.floor(u); kk = np.unique(sc); kk = kk[kk >= 0]
            cf[dn] = dict(t=kk.tolist(), cfo=[float(np.median(c[sc == k])) for k in kk], median=float(np.median(c)), std_burst=float(np.std(c)))
        ab, ba = np.array(cf["A->B"]["cfo"]), np.array(cf["B->A"]["cfo"]); n_ = min(len(ab), len(ba)); anti = (ab[:n_] - ba[:n_]) / 2; sym = (ab[:n_] + ba[:n_]) / 2
        mins = [float(np.median(anti[i:i + 60])) for i in range(0, n_ - 59, 60)]
        cf["anti"] = dict(t=cf["A->B"]["t"][:n_], v=anti.tolist(), median=float(np.median(anti)), minute_medians=mins)
        cf["sym_median"] = float(np.median(sym))
        # prediction from the trackers' own ppm (median over TX events of the run)
        ppm = {}
        for a in "AB":
            try: ppm[a] = float(np.median([json.loads(l)["ppm"] for l in open(f"{R}/{run}_{a}/tx_log.jsonl") if '"ppm"' in l]))
            except Exception: ppm[a] = None
        cf["ppm_tracker"] = ppm; cf["fsync_ppm"] = {a: S[a]["results"].get("freq_sync", {}).get("ppm_used") for a in "AB"}
        out["cfo"][run] = cf
    print(f"{run:13s} sig {row['sigma']:5.1f} wifi {within:4.2f} tdev1 {row['tdev1']:4.1f} tdev30 {row['tdev30']:4.1f} maxraw {row['max_raw']:5.0f} wander {row['wander_1s'][0]:+.0f}/{row['wander_1s'][1]:+.0f} "
          f"skip30 {100*row['skipped_after30']:.2f}% tot {100*row['skipped_total']:.2f}% uncal {row['uncal_bias_ns']} clk {row['host_clock_ms']:.1f} numb {numb['checks']}/{numb['mismatches']} resid {row['resid_ns']}")
for run, cf in out["cfo"].items():
    pa, pb = cf["ppm_tracker"].get("A"), cf["ppm_tracker"].get("B")
    print("CFO", run, "AB %.1f BA %.1f anti %.1f sym %.1f minute-medians %s pred(tracker ppm) %s fsync %s" % (cf["A->B"]["median"], cf["B->A"]["median"], cf["anti"]["median"], cf["sym_median"],
          [round(x, 1) for x in cf["anti"]["minute_medians"]], None if None in (pa, pb) else round((pa - pb) * 5180, 1), cf["fsync_ppm"]))
for r in out["runs"]:
    if "tile_medians" in r: print(r["run"], "tiles %.1f..%.1f" % (min(r["tile_medians"]), max(r["tile_medians"])), "empty", [(x["s"], x["e"], round(x["sigma"], 1), round(x["median"], 1)) for x in r["empty_segments"]])
    if "line_fraction" in r: print("t18 line fractions", r["line_fraction"], "sum", round(sum(r["line_fraction"].values()), 3))
print("totals bursts", sum(r["bursts"] for r in out["runs"]), "pairs", sum(r["pairs"] for r in out["runs"]), "checks", sum(r["numbering"]["checks"] for r in out["runs"]), "decodes", sum(r["numbering"]["decodes"] for r in out["runs"]))
json.dump(out, open("sec7_stats.json", "w"))
