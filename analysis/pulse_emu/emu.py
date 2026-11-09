import os, sys, json, numpy as np
run = sys.argv[1]; NP = int(os.environ["NPULSE"]); os.chdir(run); sys.path.insert(0, os.getcwd())
from t9c import *
from loc9 import maps, th
from energy import doppler_energy
pre = f"{run}_tiles"
D = {"A→B": (load(f"{pre}_B"), "A", "B"), "B→A": (load(f"{pre}_A"), "B", "A")}
M = {}
for dn, (d, tx, rx) in D.items(): M[dn], _ = maps(d, tx, rx)
seg = {nm: {dn: np.mean([P for tt, P in M[dn] if t0 <= tt <= t1], 0) for dn in M} for nm, t0, t1, tl in segments(3)}
fn = f"angmaps_{run}_np{NP}.npz"
np.savez_compressed(fn, th=th, **{f"{nm}|{dn}": seg[nm][dn] for nm in seg for dn in seg[nm]})
# Doppler energy: empty-room floor and motion contrast
en = {dn: doppler_energy(d) for dn, (d, _, _) in D.items()}
def segE(nm, t0, t1):
    return [10*np.log10(np.median(E[(T >= t0) & (T <= t1)])) for dn, (T, E) in en.items()]
S_all = segments(3); empty = np.nanmean([segE(nm, t0, t1) for nm, t0, t1, tl in S_all if tl is None], 0)
tiles = np.array([segE(nm, t0, t1) for nm, t0, t1, tl in S_all if isinstance(tl, tuple)])
# localization with the pre-registered rule (k = 1.12, i.e. 3.36 cm)
g = {}; src = open("pos9.py").read().split("rng=np.random")[0].replace(f'np.load("angmaps_{run}.npz")', f'np.load("{fn}")')
exec(src, g)
out = dict(run=run, npulse=NP, sounding_us=NP*50, floor_db=[round(float(x), 2) for x in empty], motion_over_floor_db=[round(float(x), 2) for x in np.median(tiles, 0) - empty])
for mname, dirs in (("A→B", ("A→B",)), ("B→A", ("B→A",)), ("ambos", ("A→B", "B→A"))):
    e = np.array([er for nm, tl, p, er in g["errs"]({"A": 1.12, "B": 1.12}, dirs)])
    out[mname] = dict(median=round(float(np.median(e)), 3), mean=round(float(e.mean()), 3), max=round(float(e.max()), 3), le30=int((e <= 0.3).sum()), n=len(e))
print(json.dumps(out, ensure_ascii=False))
