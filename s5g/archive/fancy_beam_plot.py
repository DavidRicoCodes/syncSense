#!/usr/bin/env python3
"""
Fancy beam-pointing dashboard (post-processing, no hardware needed).

Reads a .npz from bf_multibeam.py (H, W, labels, G_sim) and draws:
  - Polar top view: the 4-element array at the center, every beam as a
    filled lobe, and the RX antennas at their estimated angles, tagged as
    line-of-sight or multipath (from the phase-fit residual).
  - Heatmap: measured power at each RX from each beam (model in brackets),
    target pairs outlined.
  - Bars: each RX's own-beam power, measured vs free-space model.

Requires beam_pointing.py in the same folder.

Examples:
  python3 fancy_beam_plot.py mb_geom.npz
  python3 fancy_beam_plot.py mb_zf.npz --light
  python3 fancy_beam_plot.py mb_geom.npz --theta-a 3 --out geom_dashboard.png
"""
import argparse

import numpy as np
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.gridspec import GridSpec  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import Patch, Rectangle  # noqa: E402

from beam_pointing import C, lobe_info, rx_angles  # noqa: E402

DARK = dict(bg="#0d1117", panel="#161b22", fg="#e6edf3", grid="#30363d", muted="#8b949e",
            colors=["#4cc9f0", "#f72585", "#b8f35a", "#ffb703", "#a78bfa", "#fb7185"],
            cmap="magma")
LIGHT = dict(bg="#ffffff", panel="#f6f8fa", fg="#1f2328", grid="#d0d7de", muted="#57606a",
             colors=["#0077b6", "#d62868", "#2a9d8f", "#e76f00", "#6d28d9", "#be123c"],
             cmap="viridis")


def parse_args():
    p = argparse.ArgumentParser(description="Fancy beam-pointing dashboard")
    p.add_argument("file", help=".npz from bf_multibeam.py")
    p.add_argument("--theta-a", type=float, default=0.0, help="Angle of RX A [deg]")
    p.add_argument("--light", action="store_true", help="Light theme (for reports)")
    p.add_argument("--los-resid", type=float, default=20.0,
                   help="Phase-fit residual [deg] above which an RX is flagged as multipath")
    p.add_argument("--out", default=None, help="Output PNG (default: <file>_dashboard.png)")
    return p.parse_args()


def main():
    args = parse_args()
    th = LIGHT if args.light else DARK
    z = np.load(args.file, allow_pickle=True)
    H, W, G = z["H"], z["W"], z["G_sim"]
    labels = [str(x) for x in z["labels"]]
    d = float(z["spacing"])
    lam = C / float(z["freq"])
    M, N = H.shape
    K = W.shape[1]
    names = [chr(ord("A") + i) for i in range(M)]
    n = np.arange(N)
    cols = th["colors"]

    # ---- patterns (same model as beam_pointing.py) ----
    psi_a = 2 * np.pi * d * np.sin(np.deg2rad(args.theta_a)) / lam
    t = H[0] * np.exp(-1j * n * psi_a)
    t = t / np.sqrt(np.mean(np.abs(t) ** 2))
    theta = np.linspace(-90, 90, 721)
    A = np.exp(1j * np.outer(2 * np.pi * d * np.sin(np.deg2rad(theta)) / lam, n))
    AF_db = 10 * np.log10(np.maximum(np.abs(A @ (t[:, None] * W)) ** 2, 1e-12))
    peaks = [lobe_info(theta, AF_db[:, k])[0] for k in range(K)]
    gains = [lobe_info(theta, AF_db[:, k])[2] for k in range(K)]

    rx = rx_angles(H, d, lam, args.theta_a)
    ref = np.mean(np.abs(H) ** 2, axis=1)
    P_meas = 10 * np.log10(np.maximum(np.abs(G) ** 2 / ref[:, None], 1e-12))
    P_model = np.full((M, K), np.nan)
    for r, (a, _) in enumerate(rx):
        if a is not None:
            P_model[r] = AF_db[int(np.argmin(np.abs(theta - a))), :]

    # Target beam of each RX: the beam whose main lobe is closest to it
    target = {}
    for r, (a, _) in enumerate(rx):
        if a is not None:
            target[r] = int(np.argmin([abs(p - a) for p in peaks]))

    plt.rcParams.update({"font.family": "DejaVu Sans", "text.color": th["fg"],
                         "axes.labelcolor": th["fg"], "xtick.color": th["muted"],
                         "ytick.color": th["muted"], "axes.edgecolor": th["grid"]})
    fig = plt.figure(figsize=(18, 9), facecolor=th["bg"])
    gs = GridSpec(2, 1, height_ratios=[1.15, 1], left=0.655, right=0.97, top=0.86,
                  bottom=0.07, hspace=0.42)

    # ================= Polar top view =================
    # A half-disc polar axes is drawn centred in a square box, so the box is
    # made as tall as it is wide and positioned so the half-disc fills the left side
    fw, fh = fig.get_size_inches()
    box_w = 0.585
    box_h = box_w * fw / fh
    disc_center = 0.44
    ax = fig.add_axes([0.02, disc_center - box_h / 2, box_w, box_h], projection="polar",
                      facecolor=th["panel"])
    floor, top = -15.0, np.ceil(max(gains) / 2) * 2 + 1
    r_max = top - floor
    r_rx = r_max + 3.0
    ax.set_theta_zero_location("N")
    ax.set_theta_direction(-1)
    ax.set_thetamin(-90)
    ax.set_thetamax(90)
    ax.set_ylim(0, r_rx + 7.5)
    ax.set_thetagrids(range(-90, 91, 15), [f"{a:+d}°" if a else "0°" for a in range(-90, 91, 15)],
                      color=th["muted"], fontsize=9)
    ax.tick_params(axis="x", pad=4)
    rt = np.arange(0, r_max + 0.1, 5)
    ax.set_rgrids(rt[1:], [f"{floor + x:+.0f} dB" for x in rt[1:]], angle=-37.5,
                  color=th["muted"], fontsize=8)
    ax.set_rlabel_position(-37.5)
    ax.grid(color=th["grid"], lw=0.8)
    ax.spines["polar"].set_color(th["grid"])

    tr = np.deg2rad(theta)
    for k in range(K):
        r_k = np.clip(AF_db[:, k], floor, None) - floor
        ax.fill(tr, r_k, color=cols[k % len(cols)], alpha=0.13, zorder=2)
        ax.plot(tr, r_k, color=cols[k % len(cols)], lw=2.2, zorder=3)
        # beam axis + tag at the lobe tip
        ax.plot([np.deg2rad(peaks[k])] * 2, [0, gains[k] - floor], color=cols[k % len(cols)],
                lw=1, ls=(0, (2, 2)), zorder=3)
        ax.annotate(f" beam {labels[k]} ", xy=(np.deg2rad(peaks[k]), gains[k] - floor + 1.2),
                    ha="center", va="bottom", fontsize=9, fontweight="bold", color=th["bg"],
                    bbox=dict(boxstyle="round,pad=0.25", fc=cols[k % len(cols)], ec="none"),
                    zorder=6)

    # array elements at the center (ch0 on the negative side, + toward the last element)
    pos = (n - (N - 1) / 2) * 1.1
    for i, x in enumerate(pos):
        ax.scatter(np.deg2rad(90 if x >= 0 else -90), abs(x), marker="s", s=70,
                   color=th["fg"], edgecolor=th["bg"], zorder=7)
    ax.annotate(f"{N}-element ULA  (ch{0} ◀ ▶ ch{N-1}),  d = {d/lam:.2f} λ", xy=(0, 0),
                xytext=(0, -26), textcoords="offset points", ha="center", va="top",
                fontsize=9, color=th["muted"], annotation_clip=False)

    # RX antennas
    for r, (a, res) in enumerate(rx):
        if a is None:
            continue
        los = r == 0 or res < args.los_resid
        c = cols[target[r] % len(cols)] if r in target else th["fg"]
        ar = np.deg2rad(a)
        ax.plot([ar, ar], [0, r_rx - 1.5], color=c, lw=0.9, ls=":", alpha=0.7, zorder=1)
        ax.scatter(ar, r_rx, marker="v", s=260, color=c if los else th["panel"],
                   edgecolor=c, linewidth=2.2, zorder=8)
        status = "reference" if r == 0 else ("LOS ✓" if los else f"multipath ({res:.0f}°)")
        ax.annotate(f"RX {names[r]}  {a:+.1f}°\n{status}", xy=(ar, r_rx + 1.6),
                    ha="center", va="bottom", fontsize=9, fontweight="bold", color=c, zorder=8)

    fig.text(0.02 + box_w / 2, 0.855, "Top view: beam lobes & receiver positions",
             color=th["fg"], fontsize=13, ha="center")

    # ================= Heatmap =================
    axh = fig.add_subplot(gs[0], facecolor=th["panel"])
    vmax = np.nanmax(P_meas)
    im = axh.imshow(P_meas, cmap=th["cmap"], vmin=vmax - 25, vmax=vmax, aspect="auto")
    for r in range(M):
        for k in range(K):
            v = P_meas[r, k]
            txtc = "#000000" if v > vmax - 9 else "#ffffff"
            mdl = "" if np.isnan(P_model[r, k]) else f"\n({P_model[r, k]:+.1f})"
            axh.text(k, r, f"{v:+.1f}{mdl}", ha="center", va="center", fontsize=10,
                     color=txtc, fontweight="bold" if target.get(r) == k else "normal")
        if r in target:
            axh.add_patch(Rectangle((target[r] - 0.48, r - 0.48), 0.96, 0.96, fill=False,
                                    ec=th["fg"], lw=2.2))
    axh.set_xticks(range(K), [f"beam {l}" for l in labels], fontsize=10)
    axh.set_yticks(range(M), [f"RX {x}" for x in names], fontsize=10)
    for k, tl in enumerate(axh.get_xticklabels()):
        tl.set_color(cols[k % len(cols)])
        tl.set_fontweight("bold")
    axh.tick_params(length=0)
    for s in axh.spines.values():
        s.set_visible(False)
    cb = fig.colorbar(im, ax=axh, fraction=0.04, pad=0.02)
    cb.set_label("dB vs single element", color=th["fg"])
    cb.outline.set_edgecolor(th["grid"])
    axh.set_title("Measured power per RX & beam   (free-space model)", color=th["fg"],
                  fontsize=12, pad=10)

    # ================= Bars: own beam, measured vs model =================
    axb = fig.add_subplot(gs[1], facecolor=th["panel"])
    rs = [r for r in range(M) if r in target]
    x = np.arange(len(rs))
    wbar = 0.36
    for i, r in enumerate(rs):
        k = target[r]
        c = cols[k % len(cols)]
        m, mo = P_meas[r, k], P_model[r, k]
        axb.bar(i - wbar / 2, m, wbar, color=c, zorder=3)
        axb.bar(i + wbar / 2, mo, wbar, color="none", edgecolor=c, hatch="///", lw=1.5, zorder=3)
        gap = m - mo
        axb.text(i, max(m, mo) + 0.6, f"{gap:+.1f} dB", ha="center", va="bottom", fontsize=10,
                 fontweight="bold", color=th["fg"] if abs(gap) < 2 else "#ff6b6b")
    axb.axhline(20 * np.log10(N), color=th["muted"], ls="--", lw=1)
    axb.set_xticks(x, [f"RX {names[r]}\n← beam {labels[target[r]]}" for r in rs], fontsize=10)
    axb.set_ylabel("Own-beam power [dB vs single element]")
    axb.set_ylim(0, 20 * np.log10(N) + 6.5)
    axb.grid(axis="y", color=th["grid"], lw=0.8, zorder=0)
    for s in ("top", "right"):
        axb.spines[s].set_visible(False)
    leg = axb.legend(handles=[Patch(color=th["muted"], label="measured"),
                              Patch(fc="none", ec=th["muted"], hatch="///", label="free-space model"),
                              Line2D([], [], color=th["muted"], ls="--",
                                     label=f"ideal {N}-element gain ({20*np.log10(N):.1f} dB)")],
                     loc="upper center", ncol=3, fontsize=9, frameon=False)
    for tx in leg.get_texts():
        tx.set_color(th["fg"])
    axb.set_title("Does each RX get its beam?  (gap = measured − model)", color=th["fg"],
                  fontsize=12, pad=10)

    mode = str(z["mode"]) if "mode" in z.files else "?"
    fig.suptitle(f"X410 beam pointing — {N} TX × {M} RX, mode '{mode}', "
                 f"{float(z['freq'])/1e9:.3f} GHz", color=th["fg"], fontsize=17,
                 fontweight="bold", x=0.03, ha="left", y=0.97)
    n_los = sum(1 for r, (a, res) in enumerate(rx) if r > 0 and a is not None and res < args.los_resid)
    fig.text(0.03, 0.925, f"Calibrated against RX A at {args.theta_a:+.1f}°.  "
             f"Solid RX markers = line of sight, hollow = multipath "
             f"(phase-fit residual > {args.los_resid:.0f}°).  "
             f"{n_los} of {M - 1} non-reference RX in LOS.",
             color=th["muted"], fontsize=10, ha="left")

    out = args.out or args.file.replace(".npz", "") + "_dashboard.png"
    fig.savefig(out, dpi=140, facecolor=th["bg"])
    print(f"Saved {out}")


if __name__ == "__main__":
    main()
