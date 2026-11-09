#!/usr/bin/env python3
"""
Phase 0 beamforming analysis for a two-AP run (x410_two_ap.py, A and B).

For each direction (A->B measured at B, B->A measured at A) the complex gain of
every link (3 RX x 4 TX) is available per cycle. From it:
  - link levels and per-chain hardware phase offsets of both arrays
  - TX pattern (4 elements) and RX pattern (3 elements), calibrated with the
    partner AP as the reference source, in absolute angle assuming the partner
    sits at --partner-deg-a / --partner-deg-b from each array's broadside
  - sectors over the walking zone (zone centre at --zone-deg-* from broadside)
  - MRT gain reached vs ideal and its loss over time if calibrated only at start
  - stability of the relative phases (TX chain c vs chain 0, RX chain r vs 0)

Ratios between chains of the same X410 cancel the CFO between the two APs, so
they are valid even though the two APs are not phase coherent.

Example:
  python3 beam_two_ap.py two_ap_L_run1_A two_ap_L_run1_B
"""
import argparse
import json
import warnings
from pathlib import Path

import numpy as np

warnings.filterwarnings("ignore", message="Unable to import Axes3D")
import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.gridspec import GridSpec  # noqa: E402

C = 299792458.0


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("dir_a", type=Path)
    p.add_argument("dir_b", type=Path)
    p.add_argument("--spacing", type=float, default=0.061)
    p.add_argument("--partner-deg-a", type=float, default=32.0, help="Where B is, seen from A's broadside")
    p.add_argument("--partner-deg-b", type=float, default=30.0, help="Where A is, seen from B's broadside")
    p.add_argument("--zone-deg-a", type=float, default=-22.0, help="Zone centre seen from A's broadside")
    p.add_argument("--zone-deg-b", type=float, default=-18.0, help="Zone centre seen from B's broadside")
    p.add_argument("--zone-half-deg", type=float, default=25.0, help="Half width of the zone as seen from each AP")
    p.add_argument("--cal-s", type=float, default=30.0)
    p.add_argument("--out", type=Path, default=None)
    return p.parse_args()


def load_h(d):
    """H[cycle] (n_rx x n_tx) of the partner's bursts heard by this AP, and times."""
    s = json.loads((d / "summary.json").read_text())
    rx = [str(r) for r in s["args"]["rx_chans"]]
    H, t = [], []
    for line in (d / "records.jsonl").open():
        r = json.loads(line)
        if r.get("role") != "RX" or r.get("phase") != "B_tx_window" or "obs" not in r:
            continue
        if all(all(x.get("found") for x in r["obs"][c]) for c in rx):
            H.append([[complex(*x["h"]) for x in r["obs"][c]] for c in rx])
            t.append(r["unix_slot"])
    return s, np.array(H), np.array(t) - t[0]


def af(resp, w, theta, kd):
    n = np.arange(len(resp))
    A = np.exp(1j * np.outer(kd * np.sin(theta), n))
    return np.abs(A @ (resp * w)) ** 2 / np.mean(np.abs(resp) ** 2)


def lobe(th_deg, p_db):
    i = int(np.argmax(p_db))
    lo = hi = i
    while lo > 0 and p_db[lo - 1] >= p_db[i] - 3:
        lo -= 1
    while hi < len(p_db) - 1 and p_db[hi + 1] >= p_db[i] - 3:
        hi += 1
    return th_deg[i], th_deg[hi] - th_deg[lo], p_db[i]


def sectors(amp, kd, lo_deg, hi_deg, cross_db=-2.0):
    """Beams tiling [lo, hi] (absolute angles) with neighbours crossing at cross_db."""
    u = np.linspace(-1, 1, 20001)
    n = np.arange(len(amp))
    p0 = np.abs(np.exp(1j * np.outer(kd * u, n)) @ amp) ** 2
    p0 /= p0.max()
    m = u[p0 >= 10 ** (cross_db / 10)]
    wu = m.max() - m.min()
    ulo, uhi = np.sin(np.deg2rad(lo_deg)), np.sin(np.deg2rad(hi_deg))
    k = max(1, int(np.ceil((uhi - ulo) / wu)))
    centres = np.linspace(ulo + wu / 2, uhi - wu / 2, k) if k > 1 else [(ulo + uhi) / 2]
    return [float(np.rad2deg(np.arcsin(c))) for c in centres], float(np.rad2deg(2 * np.arcsin(wu / 2)))


def analyse(name, H, t, kd, partner_deg, zone_deg, half, cal_s):
    n_rx, n_tx = H.shape[1:]
    Hn = H * np.exp(-1j * np.angle(H[:, :1, :1]))         # remove common (CFO / LO) phase per cycle
    Hc = Hn[t < cal_s].mean(0)
    psi_p = kd * np.sin(np.deg2rad(partner_deg))
    mid_rx = n_rx // 2
    t_tx = Hc[mid_rx] * np.exp(-1j * np.arange(n_tx) * psi_p)  # TX element responses (partner geometry removed)
    off_tx = np.rad2deg(np.angle(t_tx / t_tx[0]))
    # RX side: partner TX array seen from this RX array; geometry of the partner angle at RX unknown here,
    # so RX offsets are referred to the partner direction (assumed broadside-equivalent of --partner of RX side)
    out = dict(name=name, levels=20 * np.log10(np.abs(Hc)), off_tx=off_tx, t_tx=t_tx)
    theta = np.deg2rad(np.linspace(-90, 90, 1441))
    thd = np.rad2deg(theta)
    cal = np.exp(-1j * np.angle(t_tx / t_tx[0]))
    beams = {}
    for target in (partner_deg, zone_deg, zone_deg - half / 2, zone_deg + half / 2):
        w = cal * np.exp(-1j * np.arange(n_tx) * kd * np.sin(np.deg2rad(target)))
        p = 10 * np.log10(np.maximum(af(t_tx, w, theta, kd), 1e-6))
        beams[round(target, 1)] = p
    raw = 10 * np.log10(np.maximum(af(t_tx, np.ones(n_tx), theta, kd), 1e-6))
    out.update(theta=thd, beams=beams, raw=raw)
    # sectors over the zone (TX array, measured amplitudes)
    sc, w2 = sectors(np.abs(t_tx), kd, zone_deg - half, zone_deg + half)
    out.update(zone_sectors=sc, sector_width_2db=w2)
    # MRT towards the partner (central RX) and its loss over time with the start calibration kept
    w_mrt = np.conj(Hc[mid_rx]) / np.abs(Hc[mid_rx])
    ideal_gain = 20 * np.log10(np.sum(np.abs(Hc[mid_rx])) / np.sqrt(np.mean(np.abs(Hc[mid_rx]) ** 2)))
    win = 10.0
    tw, loss, rel_tx, rel_rx = [], [], [], []
    for e in np.arange(0, t[-1], win):
        m = (t >= e) & (t < e + win)
        if m.sum() < 5:
            continue
        Hw = Hn[m].mean(0)
        h = Hw[mid_rx]
        tw.append(e + win / 2)
        loss.append(20 * np.log10(np.abs(h @ w_mrt) / np.sum(np.abs(h))))
        rel_tx.append(np.rad2deg(np.angle(Hw[mid_rx] / Hw[mid_rx, 0] / (Hc[mid_rx] / Hc[mid_rx, 0]))))
        rel_rx.append(np.rad2deg(np.angle(Hw[:, 0] / Hw[0, 0] / (Hc[:, 0] / Hc[0, 0]))))
    out.update(ideal_mrt_db=ideal_gain, tw=np.array(tw), mrt_loss=np.array(loss),
               rel_tx=np.array(rel_tx), rel_rx=np.array(rel_rx))
    # per-cycle relative phase noise (short term)
    rt = np.angle(Hn[:, mid_rx, 1:] / Hn[:, mid_rx, :1])
    rr = np.angle(Hn[:, 1:, 0] / Hn[:, :1, 0])
    out["rel_tx_std_deg"] = np.rad2deg(np.std(np.unwrap(rt, axis=0), axis=0))
    out["rel_rx_std_deg"] = np.rad2deg(np.std(np.unwrap(rr, axis=0), axis=0))
    return out


def main():
    a = parse_args()
    sA, HA, tA = load_h(a.dir_a)   # at A: B's bursts  -> B's TX array, A's RX array
    sB, HB, tB = load_h(a.dir_b)   # at B: A's bursts  -> A's TX array, B's RX array
    f = float(sA["args"]["wifi_freq"])
    kd = 2 * np.pi * a.spacing / (C / f)
    resA_tx = analyse("A transmits (seen at B)", HB, tB, kd, a.partner_deg_a, a.zone_deg_a, a.zone_half_deg, a.cal_s)
    resB_tx = analyse("B transmits (seen at A)", HA, tA, kd, a.partner_deg_b, a.zone_deg_b, a.zone_half_deg, a.cal_s)
    for r in (resA_tx, resB_tx):
        print(f"\n== {r['name']}")
        print(" |H| dBFS (rows RX of the receiver, cols TX of the transmitter):\n", np.round(r["levels"], 1))
        print(" TX chain hardware phase offsets vs chain 0 [deg]:", np.round(r["off_tx"], 1))
        print(f" MRT gain towards partner (ideal for these amplitudes): {r['ideal_mrt_db']:.2f} dB of 12.04 dB max;"
              f" worst loss over the run with start calibration: {r['mrt_loss'].min():.2f} dB")
        print(" short-term relative phase std, TX chains vs 0 [deg]:", np.round(r["rel_tx_std_deg"], 2),
              "| RX chains vs 0:", np.round(r["rel_rx_std_deg"], 2))
        print(" drift of relative phases over the run (10 s windows) max |deg|: TX",
              np.round(np.abs(r["rel_tx"]).max(0), 1), "RX", np.round(np.abs(r["rel_rx"]).max(0), 1))
        for tg, p in r["beams"].items():
            pk, bw, g = lobe(r["theta"], p)
            print(f"   beam steered {tg:+6.1f}°: peak {pk:+6.1f}°, -3 dB width {bw:5.1f}°, gain {g:+5.1f} dB")
        pk, bw, g = lobe(r["theta"], r["raw"])
        print(f"   uncalibrated (w=1): peak {pk:+.1f}° width {bw:.0f}°")
        print(f" zone sectors (-2 dB crossover): {len(r['zone_sectors'])} at {np.round(r['zone_sectors'], 1)}°,"
              f" width at broadside {r['sector_width_2db']:.1f}°")

    plt.style.use("dark_background")
    fg, bg, panel, grid = "#e6edf3", "#0d1117", "#161b22", "#30363d"
    plt.rcParams.update({"axes.facecolor": panel, "figure.facecolor": bg, "axes.edgecolor": grid,
                         "grid.color": grid, "font.size": 9, "axes.titlesize": 10, "axes.titleweight": "bold"})
    fig = plt.figure(figsize=(18, 11))
    gs = GridSpec(2, 3, figure=fig, hspace=0.45, wspace=0.3, left=0.05, right=0.97, top=0.88, bottom=0.06)
    fig.suptitle("Phase 0 — beamforming capability of the two-AP L setup (from the measured channel)",
                 fontsize=15, fontweight="bold", x=0.05, ha="left")
    fig.text(0.05, 0.93, f"{a.dir_a.name} + {a.dir_b.name} · {f/1e9:.3f} GHz · d = {a.spacing*100:.1f} cm · "
             f"partner assumed at {a.partner_deg_a:+.0f}° (from A) / {a.partner_deg_b:+.0f}° (from B), zone centre at "
             f"{a.zone_deg_a:+.0f}° / {a.zone_deg_b:+.0f}° · calibration from first {a.cal_s:.0f} s · patterns computed, "
             f"not transmitted", fontsize=9, alpha=0.8)
    cols = ["#58a6ff", "#3fb950", "#f778ba", "#d29922"]
    for k, (r, zd, pd) in enumerate(((resA_tx, a.zone_deg_a, a.partner_deg_a), (resB_tx, a.zone_deg_b, a.partner_deg_b))):
        ax = fig.add_subplot(gs[0, k], projection="polar")
        ax.set_theta_zero_location("N")
        ax.set_theta_direction(-1)
        ax.set_thetamin(-90)
        ax.set_thetamax(90)
        top, floor = 13, -12
        th = np.deg2rad(r["theta"])
        for c, (tg, p) in zip(cols, r["beams"].items()):
            pk, bw, _ = lobe(r["theta"], p)
            lab = "→ partner" if abs(tg - pd) < 0.1 else ("→ zone centre" if abs(tg - zd) < 0.1 else "zone edge")
            ax.plot(th, np.clip(p, floor, None) - floor, color=c, lw=1.5, label=f"{lab} ({tg:+.0f}°): {bw:.0f}° wide")
        ax.plot(th, np.clip(r["raw"], floor, None) - floor, color=fg, ls="--", lw=1, label="uncalibrated")
        ax.fill_between(np.deg2rad(np.linspace(zd - a.zone_half_deg, zd + a.zone_half_deg, 50)), 0, top - floor,
                        color=fg, alpha=0.07)
        ax.set_rticks([2, 12, 22])
        ax.set_yticklabels([f"{floor+2:.0f}", f"{floor+12:.0f}", f"{floor+22:.0f} dB"], fontsize=7)
        ax.set_title(f"{r['name']}: TX array (4)\nshaded = walking zone", pad=12)
        ax.legend(fontsize=7, loc="lower center", bbox_to_anchor=(0.5, -0.38), ncol=2)

    ax = fig.add_subplot(gs[0, 2])
    M = np.vstack([resA_tx["levels"], resB_tx["levels"]])
    im = ax.imshow(M, cmap="viridis", aspect="auto")
    for i in range(M.shape[0]):
        for j in range(M.shape[1]):
            ax.text(j, i, f"{M[i, j]:.0f}", ha="center", va="center", color="w", fontsize=8)
    rx = sA["args"]["rx_chans"]
    ax.set_yticks(range(6), [f"B rx{r} ← A" for r in rx] + [f"A rx{r} ← B" for r in rx], fontsize=8)
    ax.set_xticks(range(4), [f"TX ch{t}" for t in sA["args"]["tx_chans"]])
    ax.axhline(2.5, color="w")
    fig.colorbar(im, ax=ax, label="dBFS")
    ax.set_title("Measured level per link")

    ax = fig.add_subplot(gs[1, 0])
    for r, c in ((resA_tx, "#58a6ff"), (resB_tx, "#f778ba")):
        ax.plot(r["tw"], r["mrt_loss"], "o-", ms=3, color=c, label=r["name"])
    ax.set_xlabel("time [s]")
    ax.set_ylabel("loss vs ideal MRT [dB]")
    ax.set_title("Beam calibrated in the first 30 s, never updated")
    ax.set_ylim(min(-0.5, min(resA_tx["mrt_loss"].min(), resB_tx["mrt_loss"].min()) * 1.3), 0.1)
    ax.grid(True, alpha=0.4)
    ax.legend(fontsize=8)

    for k, key, title in ((1, "rel_tx", "TX chains vs chain 0"), (2, "rel_rx", "RX chains vs chain 0")):
        ax = fig.add_subplot(gs[1, k])
        for r, ls in ((resA_tx, "-"), (resB_tx, "--")):
            for j in range(r[key].shape[1]):
                if np.allclose(r[key][:, j], 0):
                    continue
                ax.plot(r["tw"], r[key][:, j], ls, lw=1, label=f"{r['name'][:13]} · {j}")
        ax.set_xlabel("time [s]")
        ax.set_ylabel("phase drift [deg]")
        ax.set_title(f"Relative phase drift, {title}")
        ax.grid(True, alpha=0.4)
        ax.legend(fontsize=6.5, ncol=2)

    out = a.out or a.dir_a.parent / f"phase0_bf_{a.dir_a.name}_{a.dir_b.name}.png"
    fig.savefig(out, dpi=100)
    print("\nSaved", out)


if __name__ == "__main__":
    main()
