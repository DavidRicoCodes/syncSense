import sys, numpy as np
import os; run = sys.argv[1]; sys.argv = ["x"]; sys.path.insert(0, os.path.abspath(run))
import os; os.chdir(run)
exec(open("pos9.py").read().split('rng=np.random')[0])
names = [nm for nm, _, _, _ in S]
for dirs in (("A→B",), ("A→B", "B→A")):
    calset = set(names[:3])
    best = min(((np.mean([np.linalg.norm(locate(nm, {"A": ka, "B": kb}, dirs) - tile_xy(*tl)) for nm, _, _, tl in S if nm in calset]), ka, kb)
                for ka in np.arange(0.8, 1.31, 0.02) for kb in np.arange(0.8, 1.31, 0.02)))
    k = {"A": best[1], "B": best[2]}
    e = np.array([np.linalg.norm(locate(nm, k, dirs) - tile_xy(*tl)) for nm, _, _, tl in S if nm not in calset])
    print(f"{run:4s} {'+'.join(dirs):8s} k calibrado con 3 baldosas: A {k['A']:.2f}, B {k['B']:.2f} | 13 restantes: mediana {np.median(e):.2f} m, máx {e.max():.2f}, ≤0,3 m {np.sum(e<=0.3)}/13")
