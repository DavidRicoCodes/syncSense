import sys, numpy as np, json
sys.path.insert(0, "."); from t9c import *
import loc9
D = {"A→B": (load("t14_track_B"), "A", "B"), "B→A": (load("t14_track_A"), "B", "A")}
th = loc9.th
W = {}
for dn, (d, tx, rx) in D.items():
    out, _ = loc9.maps(d, tx, rx); W[dn] = (np.array([o[0] for o in out]), np.stack([o[1].astype(np.float32) for o in out]))
path = [(1,2),(1,3),(1,4),(1,5),(2,5),(3,5),(4,5),(4,4),(4,3),(3,3),(2,3),(2,2)]
def truth(t):
    k = (t - 20) / 2.0; i = int(np.floor(k)); f = k - i
    p0 = tile_xy(*path[i % 12]); p1 = tile_xy(*path[(i + 1) % 12]); return p0 + f * (p1 - p0)
xs = np.arange(0.0, 3.001, 0.05); GX, GY = np.meshgrid(xs, xs); G = np.column_stack([GX.ravel(), GY.ravel()])
G = G[((G - CEN["A"]) @ NRM["A"] > 0.1) & ((G - CEN["B"]) @ NRM["B"] > 0.1)]
SP = {ap: np.sin(np.radians(theta(ap, CEN["B" if ap == "A" else "A"]))) for ap in "AB"}
def mang(ap, k):
    dd = G - CEN[ap]; dd /= np.linalg.norm(dd, axis=1, keepdims=True); s = SP[ap] + k * (dd @ U[ap] - SP[ap])
    return np.clip(np.round(np.degrees(np.arcsin(np.clip(s, -1, 1))) - th[0]).astype(int), 0, len(th) - 1)
res = {}
for lab, k, dirs in (("t13 calib (A 0.92, B 0.96), ambos", {"A": 0.92, "B": 0.96}, ("A→B", "B→A")),
                     ("t13 calib (A 0.92, B 0.84), A→B", {"A": 0.92, "B": 0.84}, ("A→B",)),
                     ("t9/t10 (1.12), ambos", {"A": 1.12, "B": 1.12}, ("A→B", "B→A"))):
    I = {dn: (mang(dn[2], k[dn[2]]), mang(dn[0], k[dn[0]])) for dn in D}
    ta = W["A→B"][0]; errs = []; est = []
    for i, t in enumerate(ta):
        if not (22 <= t <= 162): continue
        sc = 0
        for dn in dirs:
            tt, P = W[dn]; j = int(np.argmin(np.abs(tt - t)))
            if abs(tt[j] - t) > 0.13: continue
            Pm = P[j] / P[j].max(); ir, it = I[dn]; sc = sc + Pm[ir, it]
        if np.isscalar(sc): continue
        p = G[np.argmax(sc)]; e = np.linalg.norm(p - truth(t)); errs.append(e); est.append((t, *p, *truth(t)))
    e = np.array(errs)
    # light smoothing: median of 5 consecutive window estimates (1.25 s)
    E = np.array(est); sm = []
    for i in range(2, len(E) - 2):
        p = np.median(E[i - 2:i + 3, 1:3], 0); sm.append(np.linalg.norm(p - E[i, 3:5]))
    sm = np.array(sm)
    print(f"{lab:36s}: ventanas 0,5 s {len(e)} | error mediana {np.median(e):.2f} m, p80 {np.percentile(e,80):.2f}, ≤0,3 m {100*np.mean(e<=0.3):.0f} % | con mediana de 5 ventanas: {np.median(sm):.2f} m, ≤0,3 m {100*np.mean(sm<=0.3):.0f} %")
    res[lab] = E.tolist()
json.dump(res, open("track_est.json", "w"))
