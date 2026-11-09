#!/usr/bin/env python3
"""
Dashboard for a run of x410_pss_timed_tx.py (post-processing, no hardware).

  Row 1: one cycle drawn to scale (median measured times of phase B).
  Row 2: sync error per turn for every TX chain (seen on the first RX chain)
         and its distribution.
  Row 3: latency per TX->RX link relative to the median (chain / daughterboard
         skew), phase stability of every link relative to TX chain 0 (what BF
         weights depend on), 5G quality per phase (coexistence).
  Row 4: arming margin, tracker, TX->RX latency over time.

Example:
  python3 plot_pss_timed_tx.py pss_4x3_run1
  python3 plot_pss_timed_tx.py RUN_DIR --light --out run.png
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

OUT_NS = 1000.0  # |deviation from the median| above this = gross outlier


def parse_args():
    p = argparse.ArgumentParser(description="Dashboard for x410_pss_timed_tx.py runs")
    p.add_argument("run_dir", type=Path)
    p.add_argument("--light", action="store_true")
    p.add_argument("--out", type=Path, default=None, help="Default: <run_dir>/dashboard.png")
    return p.parse_args()


def main():
    args = parse_args()
    run = args.run_dir
    s = json.loads((run / "summary.json").read_text())
    turns = [json.loads(line) for line in (run / "turns.jsonl").open()]
    a, res = s["args"], s["results"]
    R = float(a["rate"])
    per_ms = float(a["nr_block_ms"])
    txc, rxc = a["tx_chans"], a["rx_chans"]
    samp = 1e9 / R

    if args.light:
        plt.style.use("default")
        fg, bg, panel, grid = "#1b1f24", "#ffffff", "#f6f8fa", "#d0d7de"
        cmap = "RdBu_r"
    else:
        plt.style.use("dark_background")
        fg, bg, panel, grid = "#e6edf3", "#0d1117", "#161b22", "#30363d"
        cmap = "coolwarm"
    tx_col = ["#58a6ff", "#f778ba", "#3fb950", "#d29922", "#a371f7", "#79c0ff"]
    c_pred, c_bad, c_warn, c_ok = "#a371f7", "#f85149", "#d29922", "#3fb950"
    ph_col = {"A_5g_only": "#58a6ff", "B_offset": "#f778ba", "C_overlap_ssb": "#d29922"}
    plt.rcParams.update({"axes.facecolor": panel, "figure.facecolor": bg, "axes.edgecolor": grid,
                         "grid.color": grid, "text.color": fg, "axes.labelcolor": fg,
                         "xtick.color": fg, "ytick.color": fg, "font.size": 9,
                         "axes.titlesize": 10, "axes.titleweight": "bold"})

    ex = [t for t in turns if str(t["state"]).startswith("EXECUTED")]
    exB = [t for t in ex if t.get("phase") == "B_offset"] or ex
    t_ref = min(t["now_arm"] for t in turns if "now_arm" in t)
    tt = lambda t: (t["now_arm"] - t_ref) / R  # noqa: E731

    def link(rx, c, key, rows=ex):
        x, y = [], []
        for t in rows:
            d = (t.get("obs") or {}).get(str(rx))
            if d and d[c].get("found") and key in d[c]:
                x.append(tt(t))
                y.append(d[c][key])
        return np.array(x), np.array(y)

    def clean(y):
        m = np.median(y) if len(y) else 0.0
        return np.abs(y - m) <= OUT_NS

    fig = plt.figure(figsize=(19, 17))
    gs = GridSpec(4, 3, figure=fig, hspace=0.55, wspace=0.28, left=0.05, right=0.97,
                  top=0.9, bottom=0.04, height_ratios=[0.7, 1, 1, 1])
    sim = " (SIMULATION)" if a.get("sim") else ""
    fig.suptitle(f"X410 {len(txc)} TX × {len(rxc)} RX + 5G: bursts scheduled from the 5G PSS{sim}",
                 fontsize=15, fontweight="bold", x=0.05, ha="left")
    ev = res["async_events"]
    fig.text(0.05, 0.945,
             f"TX ch{txc} gain {a['tx_gain']:g} dB | RX ch{rxc} gain {a['rx_gain']:g} dB | 5G ch{a['nr_chan']} "
             f"gain {a['nr_gain']:g} dB | {R/1e6:.2f} Msps | turn every {a['cycle_ssb']} SSB, armed "
             f"{a['lookahead_ssb']} SSB ahead | burst {s['burst']['len_us']/1e3:.2f} ms, {a['n_pulses']} ZC pulses "
             f"| run {run.name}", fontsize=9, alpha=0.8)
    phases_txt = "   ".join(
        f"{p}: " + ", ".join(f"{k} {v}" for k, v in sorted(ph["states"].items()))
        for p, ph in res["phases"].items() if ph["states"])
    fig.text(0.05, 0.93, f"{phases_txt}  |  TX async {ev}  |  5G overflows "
             f"{res['radio_stats']['nr_overflows']}, DSP drops {res['radio_stats']['nr_dsp_drops']}, "
             f"RX errors {res['radio_stats']['rx_errors']}", fontsize=9, alpha=0.8)

    # ---- Row 1: one cycle to scale (phase B)
    ax = fig.add_subplot(gs[0, :])
    off_ms = float(a["slot_offset_ms"])
    arm_rel = [(t["now_arm"] - t["T_pred"]) / R * 1e3 for t in exB if "T_pred" in t]
    arm_med = float(np.median(arm_rel)) if arm_rel else -a["lookahead_ssb"] * per_ms + 45
    lead = a["lookahead_ssb"] * per_ms
    for kk in range(-a["lookahead_ssb"], 1):
        x = kk * per_ms
        ax.axvline(x, color=c_pred if kk == 0 else grid, lw=2 if kk == 0 else 1)
        ax.text(x, 1.02, "target PSS  T" if kk == 0 else f"PSS {kk:+d}", ha="center",
                va="bottom", fontsize=8, color=c_pred if kk == 0 else fg)
    ax.annotate("", xy=(arm_med, 0.55), xytext=(-lead, 0.55), arrowprops=dict(arrowstyle="->", color="#58a6ff"))
    ax.text((-lead + arm_med) / 2, 0.6, "PSS processed (block + DSP)", ha="center", fontsize=8, color="#58a6ff")
    ax.plot([arm_med], [0.5], "v", color=c_warn, ms=10)
    ax.text(arm_med, 0.3, f"turn armed\n(median {arm_med:+.1f} ms)", ha="center", fontsize=8, color=c_warn)
    bl = s["burst"]["len_us"] / 1e3
    ax.axvspan(off_ms, off_ms + bl, ymin=0.1, ymax=0.8, color=c_ok, alpha=0.6)
    ax.text(off_ms + bl / 2, 0.85, f"B: burst T+{off_ms:g} ms", ha="center", fontsize=8, color=c_ok)
    if res["phases"].get("C_overlap_ssb", {}).get("turns"):
        oc = float(a["overlap_offset_ms"])
        ax.axvspan(oc, oc + bl, ymin=0.1, ymax=0.8, color=c_warn, alpha=0.35)
        ax.text(oc + bl / 2, 0.85, "C: burst over SSB", ha="center", fontsize=8, color=c_warn)
    mb = res["phases"].get("B_offset", {}).get("margin_ms")
    if mb:
        ax.annotate("", xy=(off_ms, 0.15), xytext=(arm_med, 0.15),
                    arrowprops=dict(arrowstyle="<->", color=fg, alpha=0.7))
        ax.text((arm_med + off_ms) / 2, 0.18, f"margin {mb['mean']:.0f} ms (min {a['min_margin_ms']:g})",
                ha="center", fontsize=8)
    ax.set_xlim(-lead - 5, off_ms + 10)
    ax.set_ylim(0, 1.15)
    ax.set_yticks([])
    ax.set_xlabel("time relative to the target PSS [ms]")
    ax.set_title("One cycle, to scale: the X410 fires at a timestamp armed tens of ms before (no reactive TX)")

    # ---- Row 2: sync error per TX chain (first RX)
    r0 = rxc[0]
    ax = fig.add_subplot(gs[1, :2])
    stats = []
    for c, tc in enumerate(txc):
        x, y = link(r0, c, "sync_err_ns", exB)
        if not len(y):
            continue
        good = clean(y)
        g = y[good]
        stats.append((tc, g.std(), 100 * np.mean(np.abs(g - g.mean()) <= samp + 5)))
        ax.plot(x[good], g - g.mean() + 2 * c, ".", ms=3, color=tx_col[c % len(tx_col)],
                label=f"TX ch{tc}: {g.std():.1f} ns rms"
                + (f", {int((~good).sum())} outliers" if (~good).any() else "")
                + f" (+{2*c} ns for display)")
    xp = np.array([tt(t) for t in exB if "pred_err_ns" in t])
    yp = np.array([t["pred_err_ns"] for t in exB if "pred_err_ns" in t])
    if len(yp):
        ax.plot(xp, yp - yp.mean(), "-", lw=0.6, color=c_pred, alpha=0.6,
                label=f"tracker prediction error: {yp.std():.1f} ns rms")
    for sg in (-1, 1):
        ax.axhline(sg * samp, color=fg, ls=":", lw=0.8, alpha=0.6)
    ax.set_xlabel("time since first turn [s]")
    ax.set_ylabel("burst seen − (real PSS + offset) [ns]")
    ax.set_title(f"Phase B sync error per TX chain, seen on RX ch{r0} (dotted = ±1 sample)")
    ax.grid(True, alpha=0.4)
    ax.legend(fontsize=7.5, loc="upper right", ncol=2)

    ax = fig.add_subplot(gs[1, 2])
    for c, tc in enumerate(txc):
        _, y = link(r0, c, "sync_err_ns", exB)
        if len(y):
            g = y[clean(y)]
            ax.hist(g - g.mean(), bins=np.arange(-4.5, 4.6, 1) * samp, alpha=0.45,
                    color=tx_col[c % len(tx_col)], label=f"TX ch{tc}")
    ax.set_xlabel("sync error, demeaned [ns] (bins = 1 sample)")
    ax.set_ylabel("turns")
    if stats:
        worst = max(st[1] for st in stats)
        within = min(st[2] for st in stats)
        ax.set_title(f"Sync error: worst chain {worst:.1f} ns rms, {within:.2f} % within ±1 sample",
                     color=c_ok if worst < 200 else c_bad)
    ax.grid(True, alpha=0.4)
    ax.legend(fontsize=8)

    # ---- Row 3a: latency matrix (skew)
    ax = fig.add_subplot(gs[2, 0])
    lat = np.full((len(rxc), len(txc)), np.nan)
    sd = np.full_like(lat, np.nan)
    for i, r in enumerate(rxc):
        for j, tc in enumerate(txc):
            _, y = link(r, j, "latency_ns", exB)
            if len(y):
                g = y[clean(y)]
                lat[i, j], sd[i, j] = g.mean(), g.std()
    if np.isfinite(lat).any():
        base = np.nanmedian(lat)
        dev = lat - base
        lim = max(0.5, float(np.nanmax(np.abs(dev))))
        im = ax.imshow(dev, cmap=cmap, vmin=-lim, vmax=lim)
        for i in range(len(rxc)):
            for j in range(len(txc)):
                if np.isfinite(dev[i, j]):
                    ax.text(j, i, f"{dev[i, j]:+.2f}\n±{sd[i, j]:.2f}", ha="center", va="center",
                            fontsize=8, color="k" if abs(dev[i, j]) < 0.6 * lim else "w")
        fig.colorbar(im, ax=ax, label="ns vs median")
        tx_skew = np.nanmean(dev, axis=0)
        txt = "TX skew (mean over RX): " + ", ".join(f"ch{t} {v:+.2f}" for t, v in zip(txc, tx_skew))
        ax.set_title(f"Latency per link − median {base:.1f} ns (± = std over turns, >1 µs outliers removed)"
                     f"\n{txt} ns", fontsize=9)
    ax.set_xticks(range(len(txc)), [f"TX ch{t}\n{'DB0' if t < 2 else 'DB1'}" for t in txc])
    ax.set_yticks(range(len(rxc)), [f"RX ch{r} ({'DB0' if r < 2 else 'DB1'})" for r in rxc])

    # ---- Row 3b: relative phase stability
    ax = fig.add_subplot(gs[2, 1])
    for i, r in enumerate(rxc):
        for j, tc in enumerate(txc[1:], start=1):
            pts = []
            for t in exB:
                d = (t.get("obs") or {}).get(str(r))
                if d and d[j].get("found") and d[0].get("found"):
                    pts.append((tt(t), complex(*d[j]["h"]) / complex(*d[0]["h"])))
            if len(pts) < 2:
                continue
            x, z = zip(*pts)
            ph = np.rad2deg(np.unwrap(np.angle(np.array(z))))
            ax.plot(x, ph - ph[0], lw=0.8, color=tx_col[j % len(tx_col)], ls=["-", "--", ":"][i % 3],
                    label=f"RX ch{r}, TX ch{tc}/ch{txc[0]}: σ {np.std(ph):.2f}°")
    ax.set_xlabel("time since first turn [s]")
    ax.set_ylabel("relative phase − first [deg]")
    ax.set_title(f"Phase of every link relative to TX ch{txc[0]} (BF calibration stability)")
    ax.grid(True, alpha=0.4)
    if ax.get_legend_handles_labels()[0]:
        ax.legend(fontsize=6.5, ncol=2, loc="best")

    # ---- Row 3c: 5G per phase
    ax = fig.add_subplot(gs[2, 2])
    names = [p for p in res["phases"] if res["phases"][p]["nr"]["blocks"]]
    vals = [res["phases"][p]["nr"]["valid_pct"] for p in names]
    bars = ax.bar(range(len(names)), vals, color=[ph_col.get(p, fg) for p in names], width=0.6)
    for b, p, v in zip(bars, names, vals):
        ph = res["phases"][p]["nr"]
        ax.text(b.get_x() + b.get_width() / 2, v + 0.05,
                f"{v:.2f} %\n{ph['valid']}/{ph['blocks']}\nPSS {ph['pss_metric'] or 0:.3f}",
                ha="center", va="bottom", fontsize=8)
    ax.set_xticks(range(len(names)), [p.replace("_", "\n", 1) for p in names], fontsize=8)
    ax.set_ylim(max(0, min(vals) - 4) if vals else 0, 101.5)
    ax.set_ylabel("valid SSB blocks [%]")
    ax.set_title(f"5G on ch{a['nr_chan']} per phase (TX ch{a['nr_chan']} active in B and C)")
    ax.grid(True, axis="y", alpha=0.4)

    # ---- Row 4: margin, tracker, latency vs time
    ax = fig.add_subplot(gs[3, 0])
    for st, col, mk in (("EXECUTED_OBSERVED", c_ok, "."), ("EXECUTED_PARTIAL", c_warn, "x"),
                        ("EXECUTED_NOT_OBSERVED", c_bad, "x"), ("MISSED_LATE", c_bad, "+"),
                        ("MISSED_NO_LOCK", c_bad, "4")):
        pts = [(tt(t), t["margin_ms"]) for t in turns
               if str(t["state"]).split(":")[0] == st and "margin_ms" in t]
        if pts:
            x, y = zip(*pts)
            ax.plot(x, y, mk, ms=4, color=col, label=f"{st} ({len(pts)})")
    ax.axhline(a["min_margin_ms"], color=c_bad, ls="--", lw=1)
    ax.set_xlabel("time since first turn [s]")
    ax.set_ylabel("t_tx − now at arming [ms]")
    ax.set_title("Arming margin (below the dashed line the turn is skipped)")
    ax.grid(True, alpha=0.4)
    ax.legend(fontsize=7)

    ax = fig.add_subplot(gs[3, 1])
    x = np.array([tt(t) for t in turns if t.get("track_ppm") is not None])
    if len(x):
        ppm = np.array([t["track_ppm"] for t in turns if t.get("track_ppm") is not None])
        rs = np.array([t["track_resid_ns"] for t in turns if t.get("track_ppm") is not None])
        ax.plot(x, np.abs(ppm), color="#58a6ff", lw=0.8,
                label=f"|clock offset| (median {np.median(np.abs(ppm)):.3f} ppm)")
        ax.set_ylabel("|X410 vs gNB| [ppm]", color="#58a6ff")
        ax2 = ax.twinx()
        ax2.plot(x, rs, color=c_warn, lw=0.8, alpha=0.8, label=f"fit residual (median {np.median(rs):.0f} ns)")
        ax2.set_ylabel("fit residual [ns]", color=c_warn)
        h1, l1 = ax.get_legend_handles_labels()
        h2, l2 = ax2.get_legend_handles_labels()
        ax.legend(h1 + h2, l1 + l2, fontsize=7, loc="upper right")
    ax.set_xlabel("time since first turn [s]")
    ax.set_title(f"Tracker ({a['track_window']} PSS window)")
    ax.grid(True, alpha=0.4)

    ax = fig.add_subplot(gs[3, 2])
    for c, tc in enumerate(txc):
        xl, yl = link(r0, c, "latency_ns")
        if len(yl):
            good = clean(yl)
            ax.plot(xl[good], yl[good], ".", ms=2.5, color=tx_col[c % len(tx_col)],
                    label=f"TX ch{tc}: {yl[good].mean():.2f} ± {yl[good].std():.2f} ns")
    ax.set_xlabel("time since first turn [s]")
    ax.set_ylabel("t_obs − t_tx [ns]")
    ax.set_title(f"TX → air → RX ch{r0} delay per TX chain (should be constant)")
    ax.grid(True, alpha=0.4)
    if ax.get_legend_handles_labels()[0]:
        ax.legend(fontsize=7.5)

    out = args.out or run / "dashboard.png"
    fig.savefig(out, dpi=100)
    print(f"Saved {out}")


if __name__ == "__main__":
    main()
