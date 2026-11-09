#!/usr/bin/env python3
"""
Localization on the known-tile run (t4_tiles): geometry-based steering.

Element world positions from the measured layout (tiles of 0.60 m, grid 3x3 m):
  AP A centre (1.50, -0.03), antennas 1,2,4,3 (ch0, ch1, ch3, ch2) from right to left,
       tilted ~14 deg towards B.  AP B centre (3.03, 1.50), antennas 1,2,4,3 from top
       to bottom, tilted ~18 deg towards A.  Spacing 3.0 cm.
Steering a_n(d) = exp(j s k r_n . d), r_n element position, d unit vector to the
point; s = +1 expected for UHD complex baseband (s = -1 evaluated as a check).
Per direction: CFO / common phase as sense_doppler, rank-1 static calibration with
the partner at its true geometric direction, Doppler bins 2.5-80 Hz, static
subspace nulled, projected beamscan over floor points (front of both arrays).
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

C = 299792458.0
F0 = 5.18e9
K = 2 * np.pi * F0 / C
DSP = 0.030
CEN = {"A": np.array([1.50, -0.03]), "B": np.array([3.03, 1.50])}
U = {"A": np.array([-1.0, -0.25]), "B": np.array([-0.25, -1.0])}          # from antenna 1 (ch0) to 3 (ch2)
for k in U:
    U[k] = U[k] / np.linalg.norm(U[k])
NRM = {ap: np.array([-U[ap][1], U[ap][0]]) for ap in U}                   # broadside candidate
for ap in NRM:                                                           # make it point to the grid centre
    if NRM[ap] @ (np.array([1.5, 1.5]) - CEN[ap]) < 0:
        NRM[ap] = -NRM[ap]
IDX_TX = np.array([0, 1, 3, 2])          # TX chans 0,1,2,3 -> physical slots
IDX_RX = np.array([0, 1, 2])             # RX chans 0,1,3 -> physical slots 0,1,2
TILES = [(3, 3), (2, 3), (3, 4), (2, 4), (4, 4), (2, 2), (1, 4), (4, 2)]


def tile_xy(c, r):
    return np.array([(c - 0.5) * 0.6, (r - 0.5) * 0.6])


def elem(ap, idx, n_slots=4):
    return CEN[ap][None, :] + ((idx - (n_slots - 1) / 2) * DSP)[:, None] * U[ap][None, :]


def steer(ap, idx, pts, s):
    """(P, n) steering of AP array elements idx towards points pts (P, 2)."""
    d = pts - CEN[ap][None, :]
    d = d / np.linalg.norm(d, axis=1, keepdims=True)
    r = elem(ap, idx) - CEN[ap][None, :]
    return np.exp(1j * s * K * d @ r.T)


def theta(ap, p):
    d = (p - CEN[ap]) / np.linalg.norm(p - CEN[ap])
    return float(np.degrees(np.arcsin(np.clip(U[ap] @ d, -1, 1))))


def burst_cfo(h, dt_p):
    zp = np.einsum("bkrc,brc->bk", h, np.conj(h[:, 0]))
    ph = np.unwrap(np.angle(zp), axis=1)
    return np.polyfit(np.arange(h.shape[1]) * dt_p, ph.T, 1)[0] / (2 * np.pi)


def load(folder):
    z = np.load(Path(folder) / "sense_rx.npz")
    h = z["h"].astype(np.complex128)
    dt_p = int(z["pulse_spacing"]) / float(z["rate"])
    cfo = burst_cfo(h, dt_p)
    Kp = h.shape[1]
    Hb = np.einsum("bkrc,bk->brc", h, np.exp(-2j * np.pi * cfo[:, None] * np.arange(Kp)[None] * dt_p)) / Kp
    q = z["q"]
    good = (q >= np.median(q) / 4) & ~z["acq"]
    idx = np.arange(len(q))
    flat = Hb.reshape(len(q), -1)
    for j in range(flat.shape[1]):
        flat[~good, j] = np.interp(idx[~good], idx[good], flat[good, j].real) + \
            1j * np.interp(idx[~good], idx[good], flat[good, j].imag)
    Hb = flat.reshape(Hb.shape)
    ref = Hb.mean(0)
    for _ in range(2):
        phi = np.angle(np.einsum("brc,rc->b", Hb, np.conj(ref)))
        Hb = Hb * np.exp(-1j * phi)[:, None, None]
        ref = Hb.mean(0)
    s = json.loads((Path(folder) / "summary.json").read_text())
    return Hb, z["unix"] - s["args"]["tx_start_unix"], ref


def run_direction(folder, tx, rx, s, a, grid_pts):
    H, t, Hbar = load(folder)
    R, T = H.shape[1], H.shape[2]
    # rank-1 calibration with the partner at its true direction
    Uu, sv, Vh = np.linalg.svd(Hbar)
    u, v = Uu[:, 0], Vh[0]
    g_rx = u / steer(rx, IDX_RX, CEN[tx][None, :], s)[0]
    g_tx = v / steer(tx, IDX_TX, CEN[rx][None, :], s)[0]
    g_rx, g_tx = g_rx / np.abs(g_rx), g_tx / np.abs(g_tx)
    Hc = H / g_rx[None, :, None] / g_tx[None, None, :]
    Hs = Hbar / g_rx[:, None] / g_tx[None, :]
    Us, ss, Vs = np.linalg.svd(Hs)
    Q, _ = np.linalg.qr(np.stack([np.outer(Us[:, i], Vs[i]).ravel() for i in range(a.null_static)], 1))
    Pp = np.eye(R * T) - Q @ Q.conj().T
    Ar = steer(rx, IDX_RX, grid_pts, s)
    At = steer(tx, IDX_TX, grid_pts, s)
    B = (Ar[:, :, None] * At[:, None, :]).reshape(len(grid_pts), R * T)
    BP = np.conj(B) @ Pp
    bn_raw = np.sum(np.abs(BP) ** 2, 1)
    bn = np.ones_like(bn_raw) if a.norm_floor >= 1 else np.maximum(bn_raw, a.norm_floor * bn_raw.max())
    fs = 1 / np.median(np.diff(t))
    nw, hop = int(a.win_s * fs), int(a.hop_s * fs)
    f = np.fft.fftfreq(nw, 1 / fs)
    mov = (np.abs(f) > a.fmin) & (np.abs(f) < a.fmax)
    w = np.hanning(nw)[:, None, None]
    out = []
    for s0 in range(0, len(t) - nw, hop):
        seg = Hc[s0:s0 + nw]
        X = np.fft.fft((seg - seg.mean(0)) * w, axis=0)[mov].reshape(int(mov.sum()), -1).T
        Pm = np.sum(np.abs(BP @ X) ** 2, 1) / bn
        out.append(dict(t=float(t[s0 + nw // 2]), P=Pm / Pm.max(), e=float(np.sum(np.abs(X) ** 2))))
    return out, float(sv[0] ** 2 / np.sum(sv ** 2))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--a", type=Path, default=Path("t4_tiles_A"))
    p.add_argument("--b", type=Path, default=Path("t4_tiles_B"))
    p.add_argument("--win-s", type=float, default=0.5)
    p.add_argument("--hop-s", type=float, default=0.25)
    p.add_argument("--fmin", type=float, default=2.5)
    p.add_argument("--fmax", type=float, default=80.0)
    p.add_argument("--null-static", type=int, default=1)
    p.add_argument("--norm-floor", type=float, default=1e-3, help=">=1: no normalization of the projected beamscan")
    p.add_argument("--trim-s", type=float, default=3.0, help="seconds dropped at both ends of each tile segment")
    p.add_argument("--sign", type=int, nargs="+", default=[1, -1])
    p.add_argument("--out", type=Path, default=Path("loc_tiles"))
    a = p.parse_args()
    a.out.mkdir(exist_ok=True)
    xs = np.arange(-0.3, 3.31, 0.05)
    ys = np.arange(0.05, 3.31, 0.05)
    GX, GY = np.meshgrid(xs, ys)
    G = np.column_stack([GX.ravel(), GY.ravel()])
    front = np.ones(len(G), bool)
    for ap in "AB":
        front &= ((G - CEN[ap]) @ NRM[ap]) > 0.15
    G = G[front]
    segs = [(25 + 25 * k + a.trim_s, 45 + 25 * k - a.trim_s, TILES[k]) for k in range(len(TILES))]
    report = dict(geometry=dict(A_sees_B=theta("A", CEN["B"]), B_sees_A=theta("B", CEN["A"])), signs={})
    for s in a.sign:
        res = {}
        for dn, fold, tx, rx in (("A→B", a.b, "A", "B"), ("B→A", a.a, "B", "A")):
            res[dn], r1 = run_direction(fold, tx, rx, s, a, G)
            report.setdefault("rank1", {})[dn] = r1
        tiles = []
        for t0, t1, tl in segs:
            truth = tile_xy(*tl)
            est = {}
            maps = {}
            for dn in res:
                ws = [w for w in res[dn] if t0 <= w["t"] <= t1]
                Pavg = np.mean([w["P"] for w in ws], 0)
                maps[dn] = Pavg
                per = np.array([G[np.argmax(w["P"])] for w in ws])
                est[dn] = dict(avg=G[np.argmax(Pavg)].tolist(), per_median=np.median(per, 0).tolist(),
                               err_avg=float(np.linalg.norm(G[np.argmax(Pavg)] - truth)),
                               err_per_median=float(np.median(np.linalg.norm(per - truth, axis=1))))
            comb = maps["A→B"] + maps["B→A"]
            pc = G[np.argmax(comb)]
            tiles.append(dict(tile=tl, truth=truth.tolist(), est=est, both=pc.tolist(),
                              err_both=float(np.linalg.norm(pc - truth)), theta_true=dict(
                                  A=theta("A", truth), B=theta("B", truth))))
            if s == a.sign[0]:
                np.save(a.out / f"map_s{s}_{tl[0]}{tl[1]}.npy", comb)
        errs = [t["err_both"] for t in tiles]
        rnd = np.random.default_rng(0)
        chance = np.median([np.linalg.norm(G[rnd.integers(len(G))] - tile_xy(*TILES[rnd.integers(8)])) for _ in range(5000)])
        report["signs"][str(s)] = dict(tiles=tiles, median_err_both_m=float(np.median(errs)), chance_median_m=float(chance))
        # figure
        fig, axs = plt.subplots(2, 4, figsize=(18, 9))
        for ax, (tl_rec) in zip(axs.ravel(), tiles):
            tl = tuple(tl_rec["tile"])
            comb = None
            # recompute combined map for display
            t0, t1, _ = segs[TILES.index(tl)]
            comb = sum(np.mean([w["P"] for w in res[dn] if t0 <= w["t"] <= t1], 0) for dn in res)
            img = np.full(GX.shape, np.nan)
            img.ravel()[np.flatnonzero(front)] = comb
            ax.imshow(img, origin="lower", extent=[xs[0], xs[-1], ys[0], ys[-1]], cmap="magma")
            for c in range(6):
                ax.axvline(c * 0.6, color="w", lw=0.3); ax.axhline(c * 0.6, color="w", lw=0.3)
            tr = tl_rec["truth"]
            ax.plot(*tr, "c*", ms=16, label="verdad")
            ax.plot(*tl_rec["both"], "gx", ms=12, mew=3, label="estimada")
            for ap in "AB":
                ax.plot(*CEN[ap], "ws", ms=8)
            ax.set_title(f"baldosa {tl}: error {tl_rec['err_both']:.2f} m")
            ax.set_xlim(-0.3, 3.4); ax.set_ylim(-0.2, 3.4)
        axs[0, 0].legend(fontsize=8)
        fig.suptitle(f"Mapas combinados A→B + B→A por baldosa (signo {s}) · mediana del error "
                     f"{np.median(errs):.2f} m (azar {chance:.2f} m)")
        fig.tight_layout()
        fig.savefig(a.out / f"loc_tiles_s{s}.png", dpi=80)
    (a.out / "loc_tiles.json").write_text(json.dumps(report, indent=1, default=float))
    for s, r in report["signs"].items():
        print(f"sign {s}: median error both {r['median_err_both_m']:.2f} m (chance {r['chance_median_m']:.2f} m)")
        for t in r["tiles"]:
            print(f"  {tuple(t['tile'])} truth ({t['truth'][0]:.2f},{t['truth'][1]:.2f}) both ({t['both'][0]:.2f},{t['both'][1]:.2f}) "
                  f"err {t['err_both']:.2f} | A→B err {t['est']['A→B']['err_avg']:.2f} B→A err {t['est']['B→A']['err_avg']:.2f}"
                  f" | per-window median err A→B {t['est']['A→B']['err_per_median']:.2f} B→A {t['est']['B→A']['err_per_median']:.2f}")
    print("geometry", report["geometry"], "rank1", report["rank1"])


if __name__ == "__main__":
    main()
