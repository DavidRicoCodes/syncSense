import json, re, numpy as np, sys
sys.path.insert(0, "t9"); from t9c import segments
T3 = "/tmp/claude-1000/-home-nextnet-sync/4e23c7f8-604a-4ac2-bf95-af600292fd2d/scratchpad/t3/t3_empty_"
def pairs(pre):
    L = {}; c = None
    for ap in "AB":
        z = np.load(pre+ap+"/sense_rx.npz"); s = json.load(open(pre+ap+"/summary.json"))
        q = z["q"]; g = (~z["acq"]) & (q > np.median(q)/4)
        n = z["n"][g].astype(int) % 512; u = z["unix"][g]; x = u-n*0.02
        if c is None: c = np.median(x % 10.24)
        ep = np.round((x-c)/10.24).astype(int)
        L[ap] = ({(a, b, e): (l/z["rate"]*1e9, uu) for a, b, e, l, uu in zip(n, z["sub"][g].astype(int), ep, z["lag"][g], u)}, s["args"]["tx_start_unix"])
    com = sorted(set(L["A"][0]) & set(L["B"][0]), key=lambda k: L["A"][0][k][1])
    la = np.array([L["A"][0][k][0] for k in com]); lb = np.array([L["B"][0][k][0] for k in com])
    t = np.array([L["A"][0][k][1] for k in com])-L["A"][1]
    return (la-lb)/2, (la+lb)/2, t
def stats(off, t):
    sec = np.floor(t); om = np.array([np.median(off[sec == s]) for s in np.unique(sec)])
    d = np.diff(off); d = d[np.abs(d) < 200]
    return dict(std_ns=round(float(np.std(off)), 1), fast_ns=round(float(np.std(d)/np.sqrt(2)), 1),
                slow_ns=round(float(np.std(om)), 1), range_1s_ns=round(float(np.ptp(om)), 1), bursts=int(len(off)))
rows = []
for run, pre, date, cond, rate, sel in (
        ("t3_empty", T3, "2026-10-09", "sin chapas, sala vacía", 61.44, None),
        ("t6_nopanel", "t6/t6_nopanel_", "2026-10-14", "sin chapas, sala vacía", 122.88, None),
        ("t7_smallpanel", "t7/t7_smallpanel_", "2026-10-14", "chapas pequeñas, sala vacía", 122.88, None),
        ("t9_tiles (vacío)", "t9/t9_tiles_", "2026-10-14", "tramos vacíos de t9", 122.88, "empty"),
        ("t10_tiles (vacío)", "t10/t10_tiles_", "2026-10-14", "tramos vacíos de t10", 122.88, "empty"),
        ("t11_nrw4", "t11/t11_nrw4_", "2026-10-14", "chapas pequeñas, sala vacía, 4 hilos 5G", 122.88, None)):
    off, path, t = pairs(pre)
    if sel == "empty":
        m = ((t >= 0) & (t < 20)) | ((t >= 485) & (t < 510)); off, path, t = off[m], path[m], t[m]
    r = dict(run=run, date=date, condition=cond, wifi_msps=rate, **stats(off, t), wifi_path_std_ns=round(float(np.std(path)), 2))
    rows.append(r); print(r)
out = {"sync_runs": rows}
# t11 time series (1 s)
off, path, t = pairs("t11/t11_nrw4_"); t = t-t.min(); sec = np.floor(t)
out["sync_t11"] = [dict(second=int(s), median_ns=round(float(np.median(off[sec == s])), 2), p10_ns=round(float(np.percentile(off[sec == s], 10)), 2),
                        p90_ns=round(float(np.percentile(off[sec == s], 90)), 2), bursts=int((sec == s).sum())) for s in np.unique(sec)]
# per segment t9 / t10
rows = []
for run, pre in (("t9", "t9/t9_tiles_"), ("t10", "t10/t10_tiles_")):
    off, path, t = pairs(pre)
    for nm, t0, t1, tl in segments(0):
        m = (t >= t0) & (t < t1)
        if m.sum() < 20: continue
        o = off[m]; q75, q25 = np.percentile(o, [75, 25])
        rows.append(dict(run=run, segment=nm, tile=f"({tl[0]},{tl[1]})" if isinstance(tl, tuple) else "", person_in_zone=isinstance(tl, tuple),
                         offset_median_ns=round(float(np.median(o)), 1), offset_spread_ns=round(float((q75-q25)/1.35), 1),
                         wifi_path_ns=round(float(np.median(path[m])), 2)))
out["sync_segments"] = rows
# Doppler energy per segment (coverage + back rejection)
rows = []
for run, path in (("t9", "t9/energy_out.txt"), ("t10", "t10/energy.out")):
    for line in open(path):
        m = re.match(r"(\S.*?)\s{2,}([-\d.]+)\s+([-\d.]+)\s+\[", line)
        if m: rows.append(dict(run=run, segment=m.group(1).strip(), a_to_b_db=float(m.group(2)), b_to_a_db=float(m.group(3))))
out["energy"] = rows
json.dump(out, open("dash/tmp_part2.json", "w"))
print({k: len(v) for k, v in out.items()})
