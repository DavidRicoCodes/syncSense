"""Per-AP array rotation estimated from the calibration tile only (first (3,3) segment),
spacing fixed (k = 1.11, measured 3.33 cm). Then error on the other 15 segments."""
import sys, numpy as np
run = sys.argv[1]; sys.path.insert(0, run)
from t9c import *
z = np.load(f"{run}/angmaps_{run}.npz"); th = z["th"]
S = [s for s in segments(3) if isinstance(s[3], tuple)]
xs = np.arange(0.0, 3.001, 0.05); GX, GY = np.meshgrid(xs, xs); G = np.column_stack([GX.ravel(), GY.ravel()])
G = G[((G - CEN["A"]) @ NRM["A"] > 0.1) & ((G - CEN["B"]) @ NRM["B"] > 0.1)]
K = float(sys.argv[2]) if len(sys.argv) > 2 else 1.11
def rot(u, deg):
    c, s_ = np.cos(np.radians(deg)), np.sin(np.radians(deg)); return np.array([c * u[0] - s_ * u[1], s_ * u[0] + c * u[1]])
def sin_rel(ap, P, u):
    d = P - CEN[ap]; d = d / np.linalg.norm(d, axis=-1, keepdims=True); return d @ u
SP0 = {ap: np.sin(np.radians(theta(ap, CEN["B" if ap == "A" else "A"]))) for ap in "AB"}
def idx_map(ap, delta):
    u = rot(U[ap], delta); other = CEN["B" if ap == "A" else "A"]
    s = SP0[ap] + K * (sin_rel(ap, G, u) - sin_rel(ap, other[None], u)[0])
    return np.clip(np.round(np.degrees(np.arcsin(np.clip(s, -1, 1))) - th[0]).astype(int), 0, len(th) - 1)
def locate(nm, dA, dB, dirs):
    I = {"A": idx_map("A", dA), "B": idx_map("B", dB)}; sc = 0
    for dn in dirs:
        P = z[f"{nm}|{dn}"]; P = P / P.max(); tx, rx = dn[0], dn[2]; sc = sc + P[I[rx], I[tx]]
    return G[np.argmax(sc)]
for dirs in (("A→B",), ("A→B", "B→A")):
    cal = S[0]
    best = min(((np.linalg.norm(locate(cal[0], a, b, dirs) - tile_xy(*cal[3])), abs(a) + abs(b), a, b)
                for a in np.arange(-20, 20.1, 1) for b in np.arange(-20, 20.1, 1)))
    _, _, dA, dB = best
    e0 = [np.linalg.norm(locate(nm, 0, 0, dirs) - tile_xy(*tl)) for nm, _, _, tl in S[1:]]
    e1 = [np.linalg.norm(locate(nm, dA, dB, dirs) - tile_xy(*tl)) for nm, _, _, tl in S[1:]]
    print(f"{run} {'+'.join(dirs):8s} k={K}: giro estimado con (3,3) A {dA:+.0f}°, B {dB:+.0f}° | otras 15: sin corregir mediana {np.median(e0):.2f} m, corregido mediana {np.median(e1):.2f} m (máx {np.max(e1):.2f}, ≤0,3 m {np.sum(np.array(e1)<=0.3)}/15)")
