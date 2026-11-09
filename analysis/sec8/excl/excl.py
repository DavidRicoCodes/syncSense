import os, sys, json, numpy as np
SP = "/tmp/claude-1000/-home-nextnet-sync/a6138dab-49bb-4e3a-be51-ab04509f60b6/scratchpad"
K = {"A": 1.12, "B": 1.12}; R0 = float(sys.argv[1]); save = len(sys.argv) > 2
res = {}; summ = []
for run in ("t9", "t10", "t13", "t16"):
    os.chdir(f"{SP}/{run}"); sys.path.insert(0, os.getcwd()); sys.modules.pop("t9c", None)
    g = {}; exec(open("pos9.py").read().split("rng=np.random")[0], g)
    G, S, z, ix, ma, txy, CEN = g["G"], g["S"], g["z"], g["ix"], g["meas_angle"], g["tile_xy"], g["CEN"]
    keep = (np.linalg.norm(G - CEN["A"], axis=1) > R0) & (np.linalg.norm(G - CEN["B"], axis=1) > R0)
    for mode, dirs in (("A->B", ("A→B",)), ("B->A", ("B→A",)), ("both", ("A→B", "B→A"))):
        rows = []
        for nm, _, _, tl in S:
            sc = g["score"](nm, K, dirs); sc = np.where(keep, sc, -np.inf); p = G[np.argmax(sc)]
            tr = txy(*tl); rows.append(dict(order=int(nm.split(":")[0]), tile=list(tl), true=tr.tolist(), est=[float(p[0]), float(p[1])], err=float(np.linalg.norm(p - tr))))
        e = np.array([r["err"] for r in rows]); res[f"{run}|{mode}"] = rows
        summ.append(dict(run=run, mode=mode, median=float(np.median(e)), mean=float(e.mean()), max=float(e.max()), le30=int((e <= .3).sum()), le60=int((e <= .6).sum()), n=len(e)))
        print(f"R0={R0} {run:4s} {mode:5s} median {np.median(e):.3f} mean {e.mean():.3f} max {e.max():.2f} <=0.3 {int((e<=.3).sum())}/16")
    sys.path.pop(0)
if save: json.dump(dict(R0=R0, loc=res, loc_summary=summ), open(sys.argv[2], "w"))
