import sys, json, numpy as np
run = sys.argv[1]
D0 = {"t9": ("t9", "t9_tiles"), "t10": ("t10", "t10_tiles"), "t13": ("t13", "t13_tiles_fs"), "t16": ("t16", "t16_tiles")}[run]
import os; sys.path.insert(0, os.path.abspath(D0[0])); os.chdir(D0[0])
from t9c import *
out = {}
dirs = {"A→B": ("B", "A", "B"), "B→A": ("A", "B", "A")}
Ld = {dn: load(f"{D0[1]}_{rxap}", profile=True) for dn, (rxap, tx, rx) in dirs.items()}
segs = segments(0)
def seg_of(t):
    for nm, a, b, tl in segs:
        if a <= t < b: return nm
    return ""
# ---- Doppler energy timeline (0.5 s windows) and spectrogram (1 s, 5 Hz bins)
nw = 100; f = np.fft.fftfreq(nw, 1/200); mov = (abs(f) > 2.5) & (abs(f) < 80); w = np.hanning(nw)[:, None, None]
ts = {}; spec = []
for dn, d in Ld.items():
    H, t, v = d["H"], d["t"], d["valid"]; Ps = np.sum(abs(d["ref"])**2)
    T = []; E = []
    for s0 in range(0, len(t) - nw, nw // 2):
        vv = v[s0:s0+nw]
        if np.max(np.diff(np.flatnonzero(np.r_[True, vv, True]))) - 1 > 10: continue
        seg = H[s0:s0+nw]; X = np.fft.fft((seg - seg.mean(0)) * w, axis=0)
        E.append(10*np.log10(np.sum(abs(X[mov])**2) / (np.sum(w**2) * Ps))); T.append(t[s0+nw//2])
    ts[dn] = (np.array(T), np.array(E))
    nw2 = 200; w2 = np.hanning(nw2)[:, None, None]; f2 = np.fft.fftshift(np.fft.fftfreq(nw2, 1/200))
    edges = np.arange(-100, 100.1, 5)
    for s0 in range(0, len(t) - nw2, nw2):
        seg = H[s0:s0+nw2]; X = np.fft.fftshift(np.fft.fft((seg - seg.mean(0)) * w2, axis=0), axes=0)
        p = np.sum(abs(X)**2, axis=(1, 2)) / (np.sum(w2**2) * Ps)
        b = [10*np.log10(max(p[(f2 >= edges[i]) & (f2 < edges[i+1])].mean(), 1e-12)) for i in range(len(edges)-1)]
        spec.append(dict(run=run, direction=dn, t_s=round(float(t[s0+nw2//2]), 2), db=[round(x, 1) for x in b]))
rows = []
ta, ea = ts["A→B"]; tb, eb = ts["B→A"]
for t_, e_ in zip(ta, ea):
    j = int(np.argmin(abs(tb - t_))); e2 = eb[j] if abs(tb[j] - t_) < 0.2 else None
    rows.append(dict(run=run, t_s=round(float(t_), 2), segment=seg_of(t_), ab_db=round(float(e_), 2), ba_db=None if e2 is None else round(float(e2), 2)))
out["doppler_ts"] = rows; out["spectrogram"] = spec
# ---- delay profile per segment (static + Doppler motion, spatial null of rank 2), and fingerprint
prof = []; fp = []
for dn, d in Ld.items():
    P = d["prof"]; v = d["valid"]; t = d["t"]; N, R, T_, Lg = P.shape; first = d["pfirst"]
    Pm = P[v].mean(0); stat = np.sum(abs(Pm)**2, axis=(0, 1))
    M = np.stack([Pm[:, :, l].ravel() for l in range(Lg)], 1); Uq, sq, _ = np.linalg.svd(M); Q = Uq[:, :2]; Pp = np.eye(R*T_) - Q @ Q.conj().T
    Y = (P.reshape(N, R*T_, Lg).transpose(0, 2, 1) @ Pp.T)
    acc = {}
    for s0 in range(0, N - nw, nw // 2):
        vv = v[s0:s0+nw]
        if np.max(np.diff(np.flatnonzero(np.r_[True, vv, True]))) - 1 > 10: continue
        sg = seg_of(t[s0+nw//2])
        if not sg: continue
        X = np.fft.fft((Y[s0:s0+nw] - Y[s0:s0+nw].mean(0)) * np.hanning(nw)[:, None, None], axis=0)[mov]
        acc.setdefault(sg, []).append(np.sum(abs(X)**2, axis=(0, 2)))
    for sg, lst in acc.items():
        E = np.mean(lst, 0)
        for l in range(Lg):
            prof.append(dict(run=run, direction=dn, segment=sg, lag=int(first + l), lag_m=round((first + l) * C / d["rate"], 2),
                             static_db=round(float(10*np.log10(stat[l] / stat.max())), 2), motion_db=round(float(10*np.log10(E[l] / E.max())), 2)))
    ref = d["ref"]; Uu, s_, Vh = np.linalg.svd(ref)
    for side, vec, n in (("RX", np.abs(Uu[:, 0]), R), ("TX", np.abs(Vh[0]), T_)):
        vec = vec / vec.max()
        for i in range(n): fp.append(dict(run=run, direction=dn, side=side, element=i, amp=round(float(vec[i]), 3)))
out["profile"] = prof; out["fingerprint"] = fp
# ---- CFO per second
cf = []
for dn, (rxap, tx, rx) in dirs.items():
    z = np.load(f"{D0[1]}_{rxap}/sense_rx.npz"); s = json.load(open(f"{D0[1]}_{rxap}/summary.json")); q = z["q"]; g = (~z["acq"]) & (q > np.median(q)/4)
    c = burst_cfo(z["h"][g].astype(complex), int(z["pulse_spacing"]) / float(z["rate"])); u = z["unix"][g] - s["args"]["tx_start_unix"]
    sec = np.floor(u)
    for k in np.unique(sec):
        if 0 <= k < 520: cf.append(dict(run=run, direction=dn, second=int(k), cfo_hz=round(float(np.median(c[sec == k])), 2)))
out["cfo_ts"] = cf
# ---- sync per second and per segment
L = {}; cc = None
for ap in "AB":
    z = np.load(f"{D0[1]}_{ap}/sense_rx.npz"); s = json.load(open(f"{D0[1]}_{ap}/summary.json")); q = z["q"]; g = (~z["acq"]) & (q > np.median(q)/4)
    n = z["n"][g].astype(int) % 512; u = z["unix"][g]; x = u - n*0.02
    if cc is None: cc = np.median(x % 10.24)
    ep = np.round((x - cc)/10.24).astype(int)
    L[ap] = {(a, b, e): (l/z["rate"]*1e9, uu - s["args"]["tx_start_unix"]) for a, b, e, l, uu in zip(n, z["sub"][g].astype(int), ep, z["lag"][g], u)}
com = sorted(set(L["A"]) & set(L["B"]), key=lambda k: L["A"][k][1])
la = np.array([L["A"][k][0] for k in com]); lb = np.array([L["B"][k][0] for k in com]); t = np.array([L["A"][k][1] for k in com])
off = (la - lb)/2; pth = (la + lb)/2; sec = np.floor(t)
out["sync_ts"] = [dict(run=run, second=int(k), median_ns=round(float(np.median(off[sec == k])), 2), p10_ns=round(float(np.percentile(off[sec == k], 10)), 2),
                       p90_ns=round(float(np.percentile(off[sec == k], 90)), 2), path_ns=round(float(np.median(pth[sec == k])), 2), segment=seg_of(k + 0.5))
                  for k in np.unique(sec) if 0 <= k < 520]
ss = []
for nm, a, b, tl in segs:
    m = (t >= a) & (t < b)
    if m.sum() < 20: continue
    o = off[m]; sc = np.floor(t[m]); om = np.array([np.median(o[sc == k]) for k in np.unique(sc)]); d_ = np.diff(o); d_ = d_[abs(d_) < 200]
    ss.append(dict(run=run, segment=nm, tile="" if not isinstance(tl, tuple) else f"({tl[0]},{tl[1]})", person_in_zone=isinstance(tl, tuple),
                   median_ns=round(float(np.median(o)), 1), std_ns=round(float(np.std(o)), 1), fast_ns=round(float(np.std(d_)/np.sqrt(2)), 2),
                   slow_ns=round(float(np.std(om)), 1), spread_ns=round(float(np.subtract(*np.percentile(o, [75, 25]))/1.35), 1)))
out["sync_seg"] = ss
json.dump(out, open(f"../dash2/part_{run}.json", "w"))
print(run, {k: len(v) for k, v in out.items()})
