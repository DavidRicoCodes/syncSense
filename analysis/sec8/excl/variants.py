import os, sys, json, numpy as np
SP = "/tmp/claude-1000/-home-nextnet-sync/a6138dab-49bb-4e3a-be51-ab04509f60b6/scratchpad"
K = {"A": 1.12, "B": 1.12}
res = {}
for run in ("t9", "t10", "t13", "t16"):
    os.chdir(f"{SP}/{run}"); sys.path.insert(0, os.getcwd()); sys.modules.pop("t9c", None)
    g = {}; exec(open("pos9.py").read().split("rng=np.random")[0], g)
    G, S, z, ix, ma, txy = g["G"], g["S"], g["z"], g["ix"], g["meas_angle"], g["tile_xy"]
    xs = np.round(G[:, 0] / 0.05).astype(int); ys = np.round(G[:, 1] / 0.05).astype(int)
    grid = -np.inf * np.ones((ys.max() + 2, xs.max() + 2)); inside = np.zeros_like(grid, bool)
    inside[ys, xs] = True
    def maps(nm, dirs):
        out = []
        for dn in dirs:
            P = z[f"{nm}|{dn}"]; tx, rx = dn[0], dn[2]
            out.append(P[ix(ma(rx, G, K)), ix(ma(tx, G, K))] / P.max())
        return out
    def interior_argmax(sc):
        A = grid.copy(); A[ys, xs] = sc; best, bi = -np.inf, None
        for i in range(len(G)):
            y, x = ys[i], xs[i]
            if y == 0 or x == 0: continue
            nb = A[y-1:y+2, x-1:x+2]; ins = inside[y-1:y+2, x-1:x+2]
            if not ins.all(): continue           # on the edge of the search area
            if sc[i] >= nb.max() and sc[i] > best: best, bi = sc[i], i
        return G[bi] if bi is not None else G[np.argmax(sc)]
    def contrast(m): return (m.max() - np.median(m)) / m.max()
    for mode, dirs in (("A->B", ("A→B",)), ("B->A", ("B→A",)), ("both", ("A→B", "B→A"))):
        for var in ("orig", "interior", "weighted", "both_fixes"):
            e = []; rows = []
            for nm, _, _, tl in S:
                ms = maps(nm, dirs)
                if var in ("weighted", "both_fixes") and len(ms) == 2:
                    w = [contrast(m) for m in ms]; sc = sum(wi * m for wi, m in zip(w, ms))
                else:
                    sc = sum(ms)
                p = interior_argmax(sc) if var in ("interior", "both_fixes") else G[np.argmax(sc)]
                er = float(np.linalg.norm(p - txy(*tl))); e.append(er)
                rows.append(dict(order=int(nm.split(":")[0]), tile=list(tl), true=txy(*tl).tolist(), est=[float(p[0]), float(p[1])], err=er))
            e = np.array(e); res[f"{run}|{mode}|{var}"] = rows
            if mode == "both" or var in ("orig", "interior"):
                print(f"{run:4s} {mode:5s} {var:10s} median {np.median(e):.3f} mean {e.mean():.3f} max {e.max():.2f} <=0.3 {int((e<=.3).sum())}/16")
    sys.path.pop(0)
json.dump(res, open("/tmp/claude-1000/-home-nextnet-sync/7f0a7418-12e1-4da8-9e08-b4e5daae8a38/scratchpad/outlier/variants.json", "w"))
