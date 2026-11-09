#!/usr/bin/env python3
"""
Angle-crossing localization prototype on x410_two_ap_sense.py runs (offline).

Per direction (A->B received at B, B->A received at A):
  1. pulses averaged with per-burst CFO correction, common phase referenced to
     the static channel (as sense_doppler.py)
  2. array calibration from the static mean channel: rank-1 fit
     Hbar ~ g_rx a_rx(th_partner_rx) a_tx(th_partner_tx)^T g_tx (partner = the
     other AP at its assumed angle)
  3. sliding window (--win-s, hop --hop-s): Doppler spectrum per link, keep
     the bins with --fmin < |f| < --fmax (the moving person), calibrated
  4. 2-D beamscan over (AoD at the TX array, AoA at the RX array), summed over
     the moving Doppler bins -> peak (th_tx, th_rx) and peak-to-mean quality
  5. rays from both APs (positions from the tile geometry) crossed -> (x, y)
Outputs a figure and a JSON with the per-window estimates.
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.argv_saved = sys.argv
import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

C = 299792458.0
T = 0.6
P = {"A": np.array([4 * T, 8 * T]), "B": np.array([0.8 * T, 5.8 * T])}
PZ = np.array([4 * T, 5 * T])
ZONE = np.array([[2.5 * T, 3.5 * T], [5.5 * T, 3.5 * T], [5.5 * T, 6.5 * T], [2.5 * T, 6.5 * T], [2.5 * T, 3.5 * T]])
PARTNER = {"A": 32.0, "B": 30.0}          # angle of the other AP from each array's broadside


def bearing(p, q):
    v = q - p
    return np.degrees(np.arctan2(v[1], v[0]))


# steering angle theta -> map bearing, fitted to partner/zone angles (A: +towards B, zone -22; B: zone -18)
BS = {"A": bearing(P["A"], PZ) - 22.0, "B": bearing(P["B"], PZ) + 18.0}
SGN = {"A": -1.0, "B": +1.0}             # bearing = BS + SGN * theta


def theta_of(ap, p):
    return ((bearing(P[ap], p) - BS[ap]) * SGN[ap] + 180) % 360 - 180


def ray(ap, th):
    b = np.radians(BS[ap] + SGN[ap] * th)
    return P[ap], np.array([np.cos(b), np.sin(b)])


def cross(ap1, th1, ap2, th2):
    p1, d1 = ray(ap1, th1)
    p2, d2 = ray(ap2, th2)
    M = np.column_stack([d1, -d2])
    if abs(np.linalg.det(M)) < 1e-3:
        return None
    t = np.linalg.solve(M, p2 - p1)
    if t[0] <= 0 or t[1] <= 0:
        return None
    return p1 + t[0] * d1


def burst_cfo(h, dt_p):
    K = h.shape[1]
    zp = np.einsum("bkrc,brc->bk", h, np.conj(h[:, 0]))
    ph = np.unwrap(np.angle(zp), axis=1)
    return np.polyfit(np.arange(K) * dt_p, ph.T, 1)[0] / (2 * np.pi)


def load(folder):
    z = np.load(Path(folder) / "sense_rx.npz")
    h = z["h"].astype(np.complex128)
    rate, S = float(z["rate"]), int(z["pulse_spacing"])
    dt_p = S / rate
    cfo = burst_cfo(h, dt_p)
    K = h.shape[1]
    Hb = np.einsum("bkrc,bk->brc", h, np.exp(-2j * np.pi * cfo[:, None] * np.arange(K)[None] * dt_p)) / K
    q = z["q"]
    good = q >= np.median(q) / 4
    if "acq" in z.files:
        good &= ~z["acq"]
    idx = np.arange(len(q))
    flat = Hb.reshape(len(q), -1)
    for j in range(flat.shape[1]):                     # interpolate interference-hit bursts
        flat[~good, j] = np.interp(idx[~good], idx[good], flat[good, j].real) + \
            1j * np.interp(idx[~good], idx[good], flat[good, j].imag)
    Hb = flat.reshape(Hb.shape)
    ref = Hb.mean(0)
    for _ in range(2):
        phi = np.angle(np.einsum("brc,rc->b", Hb, np.conj(ref)))
        Hb = Hb * np.exp(-1j * phi)[:, None, None]
        ref = Hb.mean(0)
    s = json.loads((Path(folder) / "summary.json").read_text())
    t = z["unix"] - s["args"]["tx_start_unix"]
    return dict(H=Hb, t=t, f0=float(z["wifi_freq"]), Hbar=ref)


# physical element position (in spacings) of each channel in the order stored in h:
# TX chans [0,1,2,3] are mounted ch0, ch1, ch3, ch2 -> positions 0, 1, 3, 2; RX chans [0,1,3] -> 0, 1, 2
POS = {4: np.array([0, 1, 3, 2]), 3: np.array([0, 1, 2])}


def steer(n, kd, th):
    th = np.atleast_1d(th)
    pos = POS[n] if USE_POS else np.arange(n)
    return np.exp(1j * np.outer(np.sin(np.radians(th)), pos) * kd)      # (len(th), n)


USE_POS = True


def process(rx_folder, tx_ap, rx_ap, a):
    d = load(rx_folder)
    H, t, Hbar = d["H"], d["t"], d["Hbar"]
    kd = 2 * np.pi * a.spacing / (C / d["f0"])
    R, Tt = H.shape[1], H.shape[2]
    # rank-1 calibration on the static channel, partner at its assumed angles
    U, s, Vh = np.linalg.svd(Hbar)
    u, v = U[:, 0] * np.sqrt(s[0]), Vh[0] * np.sqrt(s[0])          # Hbar ~ u v
    g_rx = u / steer(R, kd, PARTNER[rx_ap])[0]
    g_tx = v / steer(Tt, kd, PARTNER[tx_ap])[0]
    g_rx, g_tx = g_rx / np.abs(g_rx), g_tx / np.abs(g_tx)
    Hc = H / g_rx[None, :, None] / g_tx[None, None, :]
    rank1 = s[0] ** 2 / np.sum(s ** 2)
    fs = 1 / np.median(np.diff(t))
    nw = int(round(a.win_s * fs))
    hop = int(round(a.hop_s * fs))
    f = np.fft.fftfreq(nw, 1 / fs)
    mov = (np.abs(f) > a.fmin) & (np.abs(f) < a.fmax)
    grid = np.arange(-89, 90, 1.0)
    Ar, At = steer(R, kd, grid), steer(Tt, kd, grid)               # (G, R), (G, Tt)
    # joint (rx, tx) steering vectors b = vec(a_rx a_tx^T), rows ordered (th_rx, th_tx)
    Bm = (Ar[:, None, :, None] * At[None, :, None, :]).reshape(len(grid) ** 2, R * Tt)
    # null the dominant static subspace (direct path + strongest static multipath):
    # the moving bins also contain the direct path modulated by the person / residual
    # phase noise, with the direct path's spatial signature
    Hs = Hbar / g_rx[:, None] / g_tx[None, :]
    Us, ss, Vhs = np.linalg.svd(Hs)
    if a.null_static > 0:
        S0 = np.stack([np.outer(Us[:, i], Vhs[i]).ravel() for i in range(a.null_static)], 1)   # (R*Tt, k)
        Qs, _ = np.linalg.qr(S0)
        Pperp = np.eye(R * Tt) - Qs @ Qs.conj().T
    else:
        Pperp = np.eye(R * Tt)
    BP = np.conj(Bm) @ Pperp                                         # (G2, R*Tt)
    bnorm = np.sum(np.abs(BP) ** 2, axis=1)
    win = np.hanning(nw)[:, None, None]
    out = []
    starts = np.arange(0, len(t) - nw, hop)
    for s0 in starts:
        seg = Hc[s0:s0 + nw]
        seg = seg - seg.mean(0)
        X = np.fft.fft(seg * win, axis=0)[mov]                     # (F, R, Tt) moving bins
        e = float(np.sum(np.abs(X) ** 2))
        Xv = X.reshape(len(X), -1).T                                # (R*Tt, F)
        Pm = (np.sum(np.abs(BP @ Xv) ** 2, axis=1) / np.maximum(bnorm, 1e-3 * bnorm.max())
              ).reshape(len(grid), len(grid))                       # (th_rx, th_tx), projected beamscan
        i, j = np.unravel_index(np.argmax(Pm), Pm.shape)
        th_rx, th_tx = grid[i], grid[j]
        q = float(Pm[i, j] / np.mean(Pm))
        pos = cross(tx_ap, th_tx, rx_ap, th_rx)
        out.append(dict(t=float(t[s0 + nw // 2]), th_tx=float(th_tx), th_rx=float(th_rx), q=q, energy=e,
                        x=None if pos is None else float(pos[0]), y=None if pos is None else float(pos[1])))
    return out, dict(rank1=float(rank1), kd=kd, Pm_example=None)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--a", type=Path, required=True)
    p.add_argument("--b", type=Path, required=True)
    p.add_argument("--empty-a", type=Path, default=None)
    p.add_argument("--empty-b", type=Path, default=None)
    p.add_argument("--spacing", type=float, default=0.030)
    p.add_argument("--win-s", type=float, default=0.5)
    p.add_argument("--hop-s", type=float, default=0.1)
    p.add_argument("--fmin", type=float, default=2.5)
    p.add_argument("--fmax", type=float, default=80.0)
    p.add_argument("--walk", type=float, nargs=2, default=[35.0, 95.0])
    p.add_argument("--quiet", type=float, nargs=2, default=[2.0, 30.0], help="empty part of the same run (threshold)")
    p.add_argument("--naive-order", action="store_true", help="old (wrong) assumption: TX elements in channel order")
    p.add_argument("--null-static", type=int, default=1, help="static rank-1 terms projected out (0 = none)")
    p.add_argument("--out", type=Path, default=Path("loc_proto"))
    a = p.parse_args()
    global USE_POS
    USE_POS = not a.naive_order
    res = {}
    res["A→B"], info_ab = process(a.b, "A", "B", a)
    res["B→A"], info_ba = process(a.a, "B", "A", a)
    thr = {dn: float(np.percentile([w["energy"] for w in res[dn] if a.quiet[0] <= w["t"] <= a.quiet[1]], 99))
           for dn in res}
    if False:
        for dn, (fold, tx, rx) in {"A→B": (a.empty_b, "A", "B"), "B→A": (a.empty_a, "B", "A")}.items():
            e, _ = process(fold, tx, rx, a)
            thr[dn] = float(np.percentile([w["energy"] for w in e], 99))
    a.out.mkdir(exist_ok=True)
    summary = dict(rank1_static={"A→B": info_ab["rank1"], "B→A": info_ba["rank1"]}, energy_threshold=thr,
                   expected_zone_theta=dict(A=[float(theta_of("A", c)) for c in ZONE[:4]],
                                            B=[float(theta_of("B", c)) for c in ZONE[:4]]),
                   partner_check=dict(A_sees_B=float(theta_of("A", P["B"])), B_sees_A=float(theta_of("B", P["A"]))))
    # windows with movement in both directions
    tw = a.walk
    pairs = []
    tab = np.array([w["t"] for w in res["A→B"]])
    for w in res["B→A"]:
        i = int(np.argmin(np.abs(tab - w["t"])))
        if abs(tab[i] - w["t"]) > 0.05:
            continue
        v = res["A→B"][i]
        act = all(thr.get(dn) is None or x["energy"] > thr[dn] for dn, x in (("A→B", v), ("B→A", w)))
        pairs.append(dict(t=w["t"], ab=v, ba=w, active=act))
    walk = [pp for pp in pairs if tw[0] <= pp["t"] <= tw[1] and pp["active"]]
    zx = (ZONE[:, 0].min(), ZONE[:, 0].max())
    zy = (ZONE[:, 1].min(), ZONE[:, 1].max())

    def in_zone(x, y, m=0.0):
        return x is not None and zx[0] - m <= x <= zx[1] + m and zy[0] - m <= y <= zy[1] + m

    stats = {}
    for dn, key in (("A→B", "ab"), ("B→A", "ba")):
        w = [pp[key] for pp in walk]
        ok = [x for x in w if x["x"] is not None]
        stats[dn] = dict(windows=len(w), crossed=len(ok),
                         in_zone_pct=100 * np.mean([in_zone(x["x"], x["y"]) for x in ok]) if ok else None,
                         in_zone_pm60cm_pct=100 * np.mean([in_zone(x["x"], x["y"], 0.6) for x in ok]) if ok else None,
                         th_tx_median=float(np.median([x["th_tx"] for x in w])) if w else None,
                         th_rx_median=float(np.median([x["th_rx"] for x in w])) if w else None)
    both = [(pp["ab"], pp["ba"]) for pp in walk if pp["ab"]["x"] is not None and pp["ba"]["x"] is not None]
    dists = [np.hypot(u["x"] - v["x"], u["y"] - v["y"]) for u, v in both]
    stats["agreement_AB_vs_BA_m"] = dict(n=len(dists), median=float(np.median(dists)) if dists else None,
                                         p75=float(np.percentile(dists, 75)) if dists else None)
    # angle reciprocity: AoD at A (A->B) vs AoA at A (B->A); AoD at B vs AoA at B
    stats["reciprocity_deg"] = dict(
        A=float(np.median([abs(pp["ab"]["th_tx"] - pp["ba"]["th_rx"]) for pp in walk])) if walk else None,
        B=float(np.median([abs(pp["ab"]["th_rx"] - pp["ba"]["th_tx"]) for pp in walk])) if walk else None)
    summary["walk_stats"] = stats
    (a.out / "loc_summary.json").write_text(json.dumps(dict(summary=summary, windows=res), indent=1, default=float))
    print(json.dumps(summary, indent=1, default=float))

    # ---------------- figure
    fig, ax = plt.subplots(2, 3, figsize=(17, 10))
    for k, (dn, key, col) in enumerate((("A→B", "ab", "tab:blue"), ("B→A", "ba", "tab:orange"))):
        axm = ax[0, k]
        pts = [pp[key] for pp in walk if pp[key]["x"] is not None]
        tt = np.array([pp["t"] for pp in walk if pp[key]["x"] is not None])
        if pts:
            sc = axm.scatter([x["x"] for x in pts], [x["y"] for x in pts], c=tt, s=10, cmap="viridis")
            fig.colorbar(sc, ax=axm, label="t [s]")
        axm.plot(ZONE[:, 0], ZONE[:, 1], "k--", lw=1)
        for ap in "AB":
            axm.plot(*P[ap], "ks"); axm.annotate(f"AP {ap}", P[ap] + 0.05)
        axm.set_xlim(-0.5, 4.5); axm.set_ylim(0.5, 6.0); axm.set_aspect("equal")
        axm.set_title(f"{dn}: crossed positions while walking ({len(pts)} windows)")
        axm.grid(alpha=0.3)
    axb = ax[0, 2]
    if both:
        axb.scatter([u["x"] for u, _ in both], [u["y"] for u, _ in both], s=8, c="tab:blue", label="A→B")
        axb.scatter([v["x"] for _, v in both], [v["y"] for _, v in both], s=8, c="tab:orange", label="B→A")
        for u, v in both[::5]:
            axb.plot([u["x"], v["x"]], [u["y"], v["y"]], color="gray", lw=0.4)
    axb.plot(ZONE[:, 0], ZONE[:, 1], "k--", lw=1)
    for ap in "AB":
        axb.plot(*P[ap], "ks")
    axb.set_xlim(-0.5, 4.5); axb.set_ylim(0.5, 6.0); axb.set_aspect("equal"); axb.legend(); axb.grid(alpha=0.3)
    axb.set_title(f"Both directions, same windows; median distance {stats['agreement_AB_vs_BA_m']['median']}")
    for k, (dn, lab) in enumerate((("A→B", "AoD at A / AoA at B"), ("B→A", "AoD at B / AoA at A"))):
        axt = ax[1, k]
        w = res[dn]
        tt = [x["t"] for x in w]
        axt.plot(tt, [x["th_tx"] for x in w], ".", ms=3, label="AoD (TX array)")
        axt.plot(tt, [x["th_rx"] for x in w], ".", ms=3, label="AoA (RX array)")
        axt.axvspan(*tw, color="gray", alpha=0.15)
        axt.set_ylim(-90, 90); axt.set_xlabel("t [s]"); axt.set_ylabel("deg"); axt.legend(fontsize=8)
        axt.set_title(f"{dn}: {lab} of the moving component"); axt.grid(alpha=0.3)
    axe = ax[1, 2]
    for dn, col in (("A→B", "tab:blue"), ("B→A", "tab:orange")):
        w = res[dn]
        axe.semilogy([x["t"] for x in w], [x["energy"] for x in w], color=col, lw=0.8, label=dn)
        if dn in thr:
            axe.axhline(thr[dn], color=col, ls="--", lw=0.8)
    axe.axvspan(*tw, color="gray", alpha=0.15); axe.legend(); axe.set_title("Moving energy (dashed: empty-room p99)")
    fig.tight_layout()
    fig.savefig(a.out / "loc_proto.png", dpi=90)
    print("saved", a.out / "loc_proto.png")


if __name__ == "__main__":
    main()
