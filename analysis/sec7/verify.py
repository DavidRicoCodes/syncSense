import json, os, numpy as np
R = os.path.expanduser("~/sync_backups/x410_two_ap_results_20261014/runs")
def pairs(run):
    L = {}; cc = None; t0 = None; U = {}
    for ap in "AB":
        z = np.load(f"{R}/{run}_{ap}/sense_rx.npz"); s = json.load(open(f"{R}/{run}_{ap}/summary.json")); t0 = s["args"]["tx_start_unix"]
        g = (~z["acq"]) & (z["q"] >= 20); r = float(z["rate"]); n = z["n"][g].astype(int) % 512; u = z["unix"][g]; x = u - n * 0.02
        if cc is None: cc = np.median(x % 10.24)
        ep = np.round((x - cc) / 10.24).astype(int)
        L[ap] = {(a, b, e): (l / r * 1e9, uu - t0, uu, nn) for a, b, e, l, uu, nn in zip(n, z["sub"][g].astype(int), ep, z["lag"][g], u, z["n"][g])}
    com = sorted(set(L["A"]) & set(L["B"]), key=lambda k: L["A"][k][1])
    la = np.array([L["A"][k][0] for k in com]); lb = np.array([L["B"][k][0] for k in com]); t = np.array([L["A"][k][1] for k in com])
    ua = np.array([L["A"][k][2] for k in com]); ub = np.array([L["B"][k][2] for k in com]); ssb = np.array([k[0] + 512 * k[2] for k in com])
    return la, lb, t, ua, ub, ssb
def tdev(x, dt, taus):
    out = []
    for tau in taus:
        m = int(round(tau / dt))
        if 3 * m >= len(x): out.append(np.nan); continue
        c = np.cumsum(np.r_[0, x]); s1 = (c[m:] - c[:-m]) / m   # moving averages
        d = s1[2 * m:] - 2 * s1[m:-m] + s1[:-2 * m]
        out.append(float(np.sqrt(np.mean(d ** 2) / 6)))
    return out
res = {}
for run in ("t11_nrw4", "t12_fsync", "t13_tiles_fs", "t14_track", "t15c_check", "t16_tiles", "t18_zc80_g3", "t10_tiles"):
    la, lb, t, ua, ub, ssb = pairs(run); e = (lb - la) / 2; path = (la + lb) / 2
    # within-SSB noise: deviations from per-SSB mean
    u_, inv = np.unique(ssb, return_inverse=True); mean = np.bincount(inv, e) / np.bincount(inv); cnt = np.bincount(inv)
    w = e - mean[inv]; ok = cnt[inv] >= 2; within = float(np.sqrt(np.sum(w[ok] ** 2) / (ok.sum() - len(np.unique(inv[ok])))))
    # 200 Hz uniform grid for spectrum / TDEV (interp small gaps)
    tg = np.arange(t[0], t[-1], 0.005); eg = np.interp(tg, t, e)
    taus = [0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 30, 60]; td = tdev(eg - eg.mean(), 0.005, taus)
    # spectrum of SSB means
    f = np.fft.rfftfreq(len(eg), 0.005); P = np.abs(np.fft.rfft(eg - eg.mean())) ** 2; tot = P[1:].sum()
    top = np.argsort(P[1:])[::-1][:6] + 1
    s = {ap: json.load(open(f"{R}/{run}_{ap}/summary.json")) for ap in "AB"}
    lat = {ap: s[ap]["results"].get("latency", {}).get("latency_ns") for ap in "AB"}
    clk = np.median((ua - ub) - 0.0025) * 1e3
    res[run] = dict(sigma=float(e.std()), within_ssb=within, tdev={str(k): v for k, v in zip(taus, td)}, path_sd=float(path.std()),
                    path_1s_sd=float(np.std([np.median(path[np.floor(t) == k]) for k in np.unique(np.floor(t))])),
                    maxabs_raw=dict(A=float(np.abs(la).max()), B=float(np.abs(lb).max())), lat=lat,
                    uncal_bias_ns=None if None in lat.values() else (lat["A"] - lat["B"]) / 2, host_clock_ms=float(clk),
                    lines=[(round(float(f[i]), 2), round(float(P[i] / tot), 3)) for i in top])
    r = res[run]; print(run, "sigma %.1f within-SSB %.2f path1s_sd %.2f maxraw A %.0f B %.0f uncal %s hostclk %.1f ms" % (r["sigma"], within, r["path_1s_sd"], r["maxabs_raw"]["A"], r["maxabs_raw"]["B"], r["uncal_bias_ns"], clk), "tdev", {k: round(v, 1) for k, v in r["tdev"].items() if v == v}, "lines", r["lines"][:4])
json.dump(res, open("verify.json", "w"))
