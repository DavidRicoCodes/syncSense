"""Trace-driven emulation of coordination schemes on real t9/t10 channels (200 Hz grid).
Each scheme turns into a per-burst fate on the measured grid: received (possibly at a jittered
time, then resampled by the receiver) or lost. Sensing is then recomputed with the same pipeline."""
import sys, json, numpy as np
from multiprocessing import Pool
RUN = sys.argv[1]                                  # t9 | t10
sys.path.insert(0, f"../{RUN}")
from t9c import *                                  # noqa
import loc9
P = 5e-3; BURST = 0.40e-3; GUARD = 50e-6
pre = {"t9": "t9_tiles", "t10": "t10_tiles"}[RUN]
D = {"A→B": (load(f"../{RUN}/{pre}_B"), "A", "B"), "B→A": (load(f"../{RUN}/{pre}_A"), "B", "A")}
N = min(len(D["A→B"][0]["t"]), len(D["B→A"][0]["t"]))
TT = D["A→B"][0]["t"][:N]

def phase_series(eps_rel_ppm, phi0):
    """offset of A's burst start relative to B's own TX start, modulo P (s), per grid sample."""
    return (phi0 + eps_rel_ppm * 1e-6 * (TT - TT[0])) % P

def fate(scheme, seed):
    rng = np.random.default_rng(seed)
    ok = np.ones(N, bool); jit = np.zeros(N)
    info = {}
    if scheme["kind"] == "s5g":
        pass
    elif scheme["kind"] in ("b0", "b1"):
        phi = phase_series(scheme["eps"], scheme["phi0"])
        if scheme["kind"] == "b0":       # B listens where the agreed schedule says (2.5 ms after own TX) on its own clock
            ok = np.abs(((phi - 2.5e-3 + P / 2) % P) - P / 2) <= GUARD
        else:                            # B listens all the time except while transmitting
            d = np.abs(((phi + P / 2) % P) - P / 2)
            ok = d > BURST + GUARD
        info["collision_or_miss"] = float(1 - ok.mean())
    elif scheme["kind"] == "b3":         # negotiated, contention-based access with background load rho
        rho = scheme["rho"]; ton = 2e-3
        toff = ton * (1 - rho) / rho if rho > 0 else np.inf
        # busy/idle renewal process over the run
        t_end = TT[-1] - TT[0] + 1; t = 0.0; busy = []
        while rho > 0 and t < t_end:
            t += rng.exponential(toff); b0 = t; t += rng.exponential(ton); busy.append((b0, t))
        busy = np.array(busy) if busy else np.zeros((0, 2))
        ctrl = scheme.get("ctrl", 0.30e-3)
        bi = 0; delays = np.zeros(N)
        for i in range(N):
            t0 = TT[i] - TT[0]; t = t0
            while True:
                while bi < len(busy) and busy[bi, 1] <= t: bi += 1
                if bi < len(busy) and busy[bi, 0] <= t: t = busy[bi, 1]; continue      # wait for idle
                need = 34e-6 + rng.integers(0, 16) * 9e-6 + ctrl                      # DIFS + backoff + control
                if bi < len(busy) and busy[bi, 0] < t + need: t = busy[bi, 1]; continue  # interrupted: retry
                t += need; break
            delays[i] = t - t0
        ok = delays < P; jit = np.where(ok, delays, 0.0)
        info.update(delay_median_ms=float(np.median(delays) * 1e3), delay_p95_ms=float(np.percentile(delays, 95) * 1e3),
                    dropped=float(1 - ok.mean()))
    return ok, jit, info

def apply(d, ok, jit):
    H = d["H"][:N].copy(); v = d["valid"][:N] & ok
    if np.any(jit > 0):                      # sample taken later: channel at t+jit (linear between grid points)
        a = jit / P; i1 = np.minimum(np.arange(N) + 1, N - 1)
        H = H * (1 - a)[:, None, None] + d["H"][:N][i1] * a[:, None, None]
    idx = np.flatnonzero(v); flat = H.reshape(N, -1); ii = np.arange(N)
    if len(idx) > 10:
        for j in range(flat.shape[1]):
            flat[~v, j] = np.interp(ii[~v], idx, flat[idx, j].real) + 1j * np.interp(ii[~v], idx, flat[idx, j].imag)
    return dict(H=flat.reshape(H.shape), t=d["t"][:N], valid=v, ref=d["ref"])

th = loc9.th
xs = np.arange(0.0, 3.001, 0.05); GX, GY = np.meshgrid(xs, xs); G = np.column_stack([GX.ravel(), GY.ravel()])
G = G[((G - CEN["A"]) @ NRM["A"] > 0.1) & ((G - CEN["B"]) @ NRM["B"] > 0.1)]
k = {"A": 1.12, "B": 1.12}
SP = {ap: np.sin(np.radians(theta(ap, CEN["B" if ap == "A" else "A"]))) for ap in "AB"}
def mang(ap):
    dd = G - CEN[ap]; dd /= np.linalg.norm(dd, axis=1, keepdims=True); s = SP[ap] + k[ap] * (dd @ U[ap] - SP[ap])
    return np.clip(np.round(np.degrees(np.arcsin(np.clip(s, -1, 1))) - th[0]).astype(int), 0, len(th) - 1)
IDX = {dn: (mang(dn[2]), mang(dn[0])) for dn in D}
SEGS = [s for s in segments(3) if isinstance(s[3], tuple)]

def evaluate(args):
    name, scheme, seed = args
    ok, jit, info = fate(scheme, seed)
    W = {}
    for dn, (d, tx, rx) in D.items():
        dd = apply(d, ok, jit)
        out, _ = loc9.maps(dd, tx, rx)
        W[dn] = ({round(o[0], 3): o[1] for o in out})
    res = dict(name=name, scheme=scheme, seed=seed, **info, received=float(ok.mean()))
    for mode in (("A→B",), ("A→B", "B→A")):
        tile_err, win_err, win_total = [], [], 0
        for nm, t0, t1, tl in SEGS:
            tr = tile_xy(*tl)
            keys = sorted(t for t in W["A→B"] if t0 <= t <= t1)
            nominal = int((t1 - t0) / 0.25) - 1
            win_total += nominal
            def loc(ws):
                sc = 0
                for dn in mode:
                    Ps = [W[dn][t] for t in ws if t in W[dn]] if dn == "A→B" else \
                         [W[dn][t2] for t in ws for t2 in W[dn] if abs(t2 - t) <= 0.13]
                    if not Ps: continue
                    Pm = np.mean(Ps, 0); Pm = Pm / Pm.max(); ir, it = IDX[dn]; sc = sc + Pm[ir, it]
                return None if np.isscalar(sc) else float(np.linalg.norm(G[np.argmax(sc)] - tr))
            e = loc(keys); tile_err.append(np.nan if e is None else e)
            for t in keys:
                e1 = loc([t])
                if e1 is not None: win_err.append(e1)
        te = np.array(tile_err); we = np.array(win_err)
        tag = "ab" if len(mode) == 1 else "both"
        res[f"tile_median_{tag}"] = float(np.nanmedian(te)) if np.any(~np.isnan(te)) else None
        res[f"tile_failed_{tag}"] = int(np.isnan(te).sum())
        res[f"win_available_{tag}"] = float(len(we) / max(win_total, 1))
        res[f"win_median_{tag}"] = float(np.median(we)) if len(we) else None
        res[f"win_le30_of_nominal_{tag}"] = float(np.sum(we <= 0.3) / max(win_total, 1))
    print(json.dumps({k_: (round(v, 3) if isinstance(v, float) else v) for k_, v in res.items() if k_ != "scheme"}), flush=True)
    return res

SCHEMES = [
    ("S5G (medido)", dict(kind="s5g"), 0),
    ("B0 libre ε=0 (fase al azar)", dict(kind="b0", eps=0.0, phi0=1.3e-3), 0),
    ("B0 libre ±10 ppm", dict(kind="b0", eps=20.0, phi0=1.3e-3), 0),
    ("B1 sigue, ε=0, fase buena", dict(kind="b1", eps=0.0, phi0=2.5e-3), 0),
    ("B1 sigue, ε=0, fase en colisión", dict(kind="b1", eps=0.0, phi0=0.2e-3), 0),
    ("B1 sigue, ±10 ppm (s1)", dict(kind="b1", eps=20.0, phi0=1.0e-3), 1),
    ("B1 sigue, ±10 ppm (s2)", dict(kind="b1", eps=20.0, phi0=3.0e-3), 2),
    ("B1 sigue, ±2 ppm (TCXO)", dict(kind="b1", eps=4.0, phi0=1.0e-3), 3),
    ("B3 negociado, carga 0", dict(kind="b3", rho=0.0), 4),
    ("B3 negociado, carga 30 %", dict(kind="b3", rho=0.3), 5),
    ("B3 negociado, carga 60 %", dict(kind="b3", rho=0.6), 6),
    ("B3 negociado, carga 80 %", dict(kind="b3", rho=0.8), 7),
]
if __name__ == "__main__":
    with Pool(min(12, len(SCHEMES))) as pool:
        R = pool.map(evaluate, SCHEMES)
    json.dump(R, open(f"emu_{RUN}.json", "w"), indent=1, default=float)
