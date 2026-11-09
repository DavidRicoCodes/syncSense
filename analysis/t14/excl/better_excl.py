import sys, json, numpy as np
sys.path.insert(0, "."); from t9c import *
xs = np.arange(-0.1, 3.101, 0.05); GX, GY = np.meshgrid(xs, xs); G = np.column_stack([GX.ravel(), GY.ravel()])
G = G[((G - CEN["A"]) @ NRM["A"] > 0.1) & ((G - CEN["B"]) @ NRM["B"] > 0.1)]
R0 = 0.5                                   # AP exclusion zone, as in Section VIII (2026-10-28)
G = G[(np.linalg.norm(G - CEN["A"], axis=1) > R0) & (np.linalg.norm(G - CEN["B"], axis=1) > R0)]
def rot(u, deg):
    c, s_ = np.cos(np.radians(deg)), np.sin(np.radians(deg)); return np.array([c*u[0]-s_*u[1], s_*u[0]+c*u[1]])
SP0 = {ap: np.sin(np.radians(theta(ap, CEN["B" if ap == "A" else "A"]))) for ap in "AB"}
def idx_map(ap, k, delta, th):
    u = rot(U[ap], delta); other = CEN["B" if ap == "A" else "A"]
    d = G - CEN[ap]; d = d / np.linalg.norm(d, axis=1, keepdims=True); do = (other - CEN[ap]) / np.linalg.norm(other - CEN[ap])
    s = SP0[ap] + k * (d @ u - do @ u)
    return np.clip(np.round(np.degrees(np.arcsin(np.clip(s, -1, 1))) - th[0]).astype(int), 0, len(th) - 1)
DIRS = ("A→B", "B→A")
# ---- calibration on t13 (16 tiles, segment-averaged maps)
z13 = np.load("../t13/angmaps_t13.npz"); th = z13["th"]
S13 = [s for s in segments(3) if isinstance(s[3], tuple)]
maps13 = {nm: {dn: z13[f"{nm}|{dn}"] / z13[f"{nm}|{dn}"].max() for dn in DIRS} for nm, _, _, _ in S13}
def score13(par):
    I = {ap: idx_map(ap, par[ap][0], par[ap][1], th) for ap in "AB"}
    e = []
    for nm, _, _, tl in S13:
        sc = sum(maps13[nm][dn][I[dn[2]], I[dn[0]]] for dn in DIRS)
        e.append(np.linalg.norm(G[np.argmax(sc)] - tile_xy(*tl)))
    return np.median(e), np.mean(e)
par = {"A": [1.11, 0.0], "B": [1.11, 0.0]}
for it in range(3):
    for ap in "AB":
        best = None
        for k in np.arange(0.8, 1.31, 0.03):
            for dl in np.arange(-15, 15.1, 1.5):
                trial = {**par, ap: [k, dl]}; m = score13(trial)
                if best is None or m[1] < best[0][1]: best = (m, [k, dl])
        par[ap] = best[1]
    print("iter", it, {a: [round(v, 2) for v in p] for a, p in par.items()}, "t13 mediana/media", np.round(score13(par), 3))
# ---- apply to t14 windows
z = np.load("winmaps_t14.npz")
I = {ap: idx_map(ap, par[ap][0], par[ap][1], th) for ap in "AB"}
ta, Pa = z["t|A→B"], z["P|A→B"]; tb, Pb = z["t|B→A"], z["P|B→A"]
est = []
for i, t in enumerate(ta):
    if not (21 <= t <= 166): continue
    j = int(np.argmin(np.abs(tb - t)))
    sc = Pa[i][I["B"], I["A"]] / Pa[i].max()
    if abs(tb[j] - t) <= 0.13: sc = sc + Pb[j][I["A"], I["B"]] / Pb[j].max()
    est.append((t, *G[np.argmax(sc)]))
E = np.array(est)
# ---- constant-velocity Kalman filter + RTS smoother
def kalman(E, sig_meas=0.25, q=0.6):
    t = E[:, 0]; n = len(t); x = np.zeros((n, 4)); P = np.zeros((n, 4, 4)); xp = np.zeros((n, 4)); Pp = np.zeros((n, 4, 4))
    H = np.array([[1, 0, 0, 0], [0, 1, 0, 0]]); Rm = np.eye(2) * sig_meas ** 2
    x0 = np.median(E[:5, 1:3], 0); xk = np.array([x0[0], x0[1], 0, 0]); Pk = np.diag([0.1, 0.1, 0.5, 0.5])
    Fs = []
    for i in range(n):
        dt = t[i] - t[i - 1] if i else 0.25
        F = np.eye(4); F[0, 2] = F[1, 3] = dt
        Q = q * np.array([[dt**3/3, 0, dt**2/2, 0], [0, dt**3/3, 0, dt**2/2], [dt**2/2, 0, dt, 0], [0, dt**2/2, 0, dt]])
        xk = F @ xk; Pk = F @ Pk @ F.T + Q; xp[i], Pp[i] = xk, Pk; Fs.append(F)
        z_ = E[i, 1:3]; y = z_ - H @ xk; Sm = H @ Pk @ H.T + Rm
        if y @ np.linalg.solve(Sm, y) < 16:                       # gate outliers (4 sigma)
            Kg = Pk @ H.T @ np.linalg.inv(Sm); xk = xk + Kg @ y; Pk = (np.eye(4) - Kg @ H) @ Pk
        x[i], P[i] = xk, Pk
    xs_ = x.copy()
    for i in range(n - 2, -1, -1):
        C = P[i] @ Fs[i + 1].T @ np.linalg.inv(Pp[i + 1]); xs_[i] = x[i] + C @ (xs_[i + 1] - xp[i + 1])
    return x[:, :2], xs_[:, :2]
filt, smooth = kalman(E)
path = [(1,2),(1,3),(1,4),(1,5),(2,5),(3,5),(4,5),(4,4),(4,3),(3,3),(2,3),(2,2)]
def truth(t):
    k = (t - 20) / 2.0; i = int(np.floor(k)); f = k - i; p0 = tile_xy(*path[i % 12]); p1 = tile_xy(*path[(i+1) % 12]); return p0 + f * (p1 - p0)
med5 = np.array([np.median(E[max(0, i-2):i+3, 1:3], 0) for i in range(len(E))])
def evalp(P, L):
    m = (E[:, 0] + L >= 20) & (E[:, 0] + L <= 164)
    e = np.array([np.linalg.norm(P[i] - truth(E[i, 0] + L)) for i in np.flatnonzero(m)]); return e
for lab, P in (("ventana 0,5 s cruda", E[:, 1:3]), ("mediana 1,25 s", med5), ("Kalman (tiempo real)", filt), ("Kalman + RTS (offline)", smooth)):
    best = min((np.median(evalp(P, L)), L) for L in np.arange(-4, 0.01, 0.25))
    e = evalp(P, best[1])
    print(f"{lab:24s} desfase {best[1]:+.2f} s | mediana {np.median(e):.2f} m, p80 {np.percentile(e,80):.2f}, ≤0,3 m {100*np.mean(e<=0.3):.0f} %, ≤0,6 m {100*np.mean(e<=0.6):.0f} %")
json.dump(dict(par={a: [float(v) for v in p] for a, p in par.items()}, E=E.tolist(), filt=filt.tolist(), smooth=smooth.tolist()), open("/tmp/claude-1000/-home-nextnet-sync/7f0a7418-12e1-4da8-9e08-b4e5daae8a38/scratchpad/walk/better_est_excl.json", "w"))

_, sm4 = kalman(E, 0.25, 1.5)                               # v4: light RTS smoothing
json.dump({"Kalman+RTS σ=0.25 q=1.5": sm4.tolist(), "Kalman causal σ=0.25 q=0.6": filt.tolist()}, open("/tmp/claude-1000/-home-nextnet-sync/7f0a7418-12e1-4da8-9e08-b4e5daae8a38/scratchpad/walk/cands_excl.json", "w"))
for lab, P in (("v4 Kalman+RTS σ=0.25 q=1.5", sm4), ("Kalman causal", filt)):
    e = evalp(P, -2.5); print(f"{lab}: L=-2.5 mediana {np.median(e):.3f}, ≤0,3 {100*np.mean(e<=0.3):.1f}%, ≤0,6 {100*np.mean(e<=0.6):.1f}% n={len(e)}")
