import sys, json, numpy as np
run = sys.argv[1]                      # t9 or t10
sys.path.insert(0, run)
from t9c import *
import loc9
prefix = {"t9": "t9_tiles", "t10": "t10_tiles"}[run]
D = {"A→B": (load(f"{run}/{prefix}_B"), "A", "B"), "B→A": (load(f"{run}/{prefix}_A"), "B", "A")}
th = loc9.th
xs = np.arange(0.0, 3.001, 0.05); GX, GY = np.meshgrid(xs, xs); G = np.column_stack([GX.ravel(), GY.ravel()])
G = G[((G-CEN["A"])@NRM["A"] > 0.1) & ((G-CEN["B"])@NRM["B"] > 0.1)]
k = {"A": 1.12, "B": 1.12}
SP = {ap: np.sin(np.radians(theta(ap, CEN["B" if ap == "A" else "A"]))) for ap in "AB"}
def mang(ap):
    d = G-CEN[ap]; d /= np.linalg.norm(d, axis=1, keepdims=True); s = SP[ap]+k[ap]*(d@U[ap]-SP[ap])
    return np.clip(np.round(np.degrees(np.arcsin(np.clip(s, -1, 1)))-th[0]).astype(int), 0, len(th)-1)
IDX = {dn: (mang(dn[2]), mang(dn[0])) for dn in D}          # (rx index, tx index) per grid point
W = {}
for dn, (d, tx, rx) in D.items():
    out, _ = loc9.maps(d, tx, rx)
    t = np.array([o[0] for o in out]); P = np.stack([o[1].astype(np.float32) for o in out])
    W[dn] = (t, P)
res = {}
segs = [s for s in segments(3) if isinstance(s[3], tuple)]
for win in (0.5, 1.0, 2.0, 5.0, 14.0):
    n = max(1, int(round(win/0.25))-1)                     # windows of 0.5 s with hop 0.25 s
    for mode in (("A→B",), ("A→B", "B→A")):
        errs = []
        for nm, t0, t1, tl in segs:
            tr = tile_xy(*tl)
            ta = W["A→B"][0]; idx = np.flatnonzero((ta >= t0) & (ta <= t1))
            for i0 in range(0, len(idx)-n+1, n):
                sel = idx[i0:i0+n]; tsel = ta[sel]
                sc = 0
                for dn in mode:
                    tt, P = W[dn]; j = np.flatnonzero((tt >= tsel[0]-0.13) & (tt <= tsel[-1]+0.13))
                    if len(j) == 0: continue
                    Pm = P[j].mean(0); Pm = Pm/Pm.max(); ir, it = IDX[dn]; sc = sc+Pm[ir, it]
                errs.append(float(np.linalg.norm(G[np.argmax(sc)]-tr)))
        e = np.array(errs)
        res[f"{win}|{'+'.join(mode)}"] = dict(n=len(e), median=float(np.median(e)), p80=float(np.percentile(e, 80)),
                                             p95=float(np.percentile(e, 95)), le30=float(np.mean(e <= 0.3)), le60=float(np.mean(e <= 0.6)))
        print(run, win, mode, {k_: round(v, 3) for k_, v in res[f"{win}|{'+'.join(mode)}"].items()}, flush=True)
json.dump(res, open(f"dash/shortwin_{run}.json", "w"), indent=1)
