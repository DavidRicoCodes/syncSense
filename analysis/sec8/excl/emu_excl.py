import os, sys, json, numpy as np
SP = "/tmp/claude-1000/-home-nextnet-sync/a6138dab-49bb-4e3a-be51-ab04509f60b6/scratchpad/pulse_emu"
K = {"A": 1.12, "B": 1.12}; out = []
for run in ("t10", "t16"):
    for NP in (8, 2, 1):
        os.chdir(f"{SP}/{run}"); sys.path.insert(0, os.getcwd()); sys.modules.pop("t9c", None)
        g = {}; exec(open("pos9.py").read().split("rng=np.random")[0].replace(f'np.load("angmaps_{run}.npz")', f'np.load("angmaps_{run}_np{NP}.npz")'), g)
        G, S, CEN, txy = g["G"], g["S"], g["CEN"], g["tile_xy"]
        keep = (np.linalg.norm(G - CEN["A"], axis=1) > 0.5) & (np.linalg.norm(G - CEN["B"], axis=1) > 0.5)
        r = dict(run=run, npulse=NP, sounding_us=NP * 50)
        for mn, dirs in (("A->B", ("A→B",)), ("B->A", ("B→A",)), ("both", ("A→B", "B→A"))):
            e = np.array([np.linalg.norm(G[np.argmax(np.where(keep, g["score"](nm, K, dirs), -np.inf))] - txy(*tl)) for nm, _, _, tl in S])
            r[mn] = dict(median=round(float(np.median(e)), 3), mean=round(float(e.mean()), 3), max=round(float(e.max()), 3), le30=int((e <= .3).sum()), n=len(e))
        print(json.dumps(r)); out.append(r); sys.path.pop(0)
json.dump(out, open(sys.argv[1], "w"), indent=1)
