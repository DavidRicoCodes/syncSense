#!/usr/bin/env python3
"""
Dashboard for a run of x410_pss_timed_tx.py (post-processing, no hardware).

  Top:    one cycle drawn to scale with the median measured times: PSS every
          20 ms, when the turn was armed, the target PSS and the burst.
  Middle: sync error per turn (burst seen by the observer vs the real PSS +
          slot offset, constant hardware delay removed) and its histogram.
  Bottom: arming margin per turn (late-command safety), tracker clock offset
          and residual, TX->observer latency.

Example:
  python3 plot_pss_timed_tx.py pss_timed_tx_20260924_150000
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
    off_ms = float(a["slot_offset_ms"])

    if args.light:
        plt.style.use("default")
        fg, bg, panel, grid = "#1b1f24", "#ffffff", "#f6f8fa", "#d0d7de"
    else:
        plt.style.use("dark_background")
        fg, bg, panel, grid = "#e6edf3", "#0d1117", "#161b22", "#30363d"
    c0, c1, c_pred, c_bad, c_warn, c_ok = "#58a6ff", "#f778ba", "#a371f7", "#f85149", "#d29922", "#3fb950"
    plt.rcParams.update({"axes.facecolor": panel, "figure.facecolor": bg, "axes.edgecolor": grid,
                         "grid.color": grid, "text.color": fg, "axes.labelcolor": fg,
                         "xtick.color": fg, "ytick.color": fg, "font.size": 9,
                         "axes.titlesize": 10, "axes.titleweight": "bold"})

    ex = [t for t in turns if str(t["state"]).startswith("EXECUTED")]
    t_ref = min(t["now_arm"] for t in turns if "now_arm" in t)
    tt = lambda t: (t["now_arm"] - t_ref) / R  # noqa: E731

    def chain(c, key):
        x, y = [], []
        for t in ex:
            o = t.get("obs") or []
            if len(o) > c and o[c].get("found") and key in o[c]:
                x.append(tt(t))
                y.append(o[c][key])
        return np.array(x), np.array(y)

    fig = plt.figure(figsize=(18, 13))
    gs = GridSpec(3, 3, figure=fig, hspace=0.5, wspace=0.28, left=0.05, right=0.98,
                  top=0.87, bottom=0.05, height_ratios=[0.8, 1, 1])
    states = res["states"]
    sim = " (SIMULATION)" if a.get("sim") else ""
    fig.suptitle(f"X410: WiFi burst scheduled from the 5G PSS, verified by an observer{sim}",
                 fontsize=15, fontweight="bold", x=0.05, ha="left")
    ev = res["async_events"]
    fig.text(0.05, 0.935,
             f"turn every {a['cycle_ssb']} SSB, armed {a['lookahead_ssb']} SSB ahead, burst at T+{off_ms:g} ms "
             f"({s['burst']['len_us']/1e3:.2f} ms, {a['n_pulses']} ZC pulses), TX ch{a['tx_chans']} gain "
             f"{a['tx_gain']:g} dB, observer ch{a['obs_chan']}, 5G ch{a['nr_chan']} gain {a['nr_gain']:g} dB, "
             f"{R/1e6:.2f} Msps  |  run {run.name}", fontsize=9, alpha=0.8)
    fig.text(0.05, 0.915,
             "Turns: " + ", ".join(f"{k} {v}" for k, v in sorted(states.items()))
             + f"  |  TX async: {ev}  |  5G overflows {res['radio_stats']['nr_overflows']}, "
             f"discontinuities {res['radio_stats']['nr_discontinuities']}, DSP drops "
             f"{res['radio_stats']['nr_dsp_drops']}", fontsize=9, alpha=0.8)

    # ---- Top: one cycle to scale
    ax = fig.add_subplot(gs[0, :])
    arm_rel = [(t["now_arm"] - t["T_pred"]) / R * 1e3 for t in ex if "T_pred" in t]
    arm_med = float(np.median(arm_rel)) if arm_rel else -a["lookahead_ssb"] * per_ms + 45
    lead = a["lookahead_ssb"] * per_ms
    lo, hi = -lead - 5, off_ms + 12
    for kk in range(-a["lookahead_ssb"], 2):
        x = kk * per_ms
        ax.axvline(x, color=c_pred if kk == 0 else grid, lw=2 if kk == 0 else 1)
        ax.text(x, 1.02, "target PSS  T" if kk == 0 else f"PSS {kk:+d}", ha="center",
                va="bottom", fontsize=8, color=c_pred if kk == 0 else fg)
    ax.axvline(-lead, color=c0, lw=1, ls="--")
    ax.annotate("", xy=(arm_med, 0.55), xytext=(-lead, 0.55),
                arrowprops=dict(arrowstyle="->", color=c0))
    ax.text((-lead + arm_med) / 2, 0.6, "PSS processed\n(block + DSP)", ha="center", fontsize=8, color=c0)
    ax.plot([arm_med], [0.5], "v", color=c_warn, ms=10)
    ax.text(arm_med, 0.3, f"turn armed\n(median {arm_med:+.1f} ms)", ha="center", fontsize=8, color=c_warn)
    bl = s["burst"]["len_us"] / 1e3
    ax.axvspan(off_ms, off_ms + bl, ymin=0.1, ymax=0.8, color=c_ok, alpha=0.6)
    ax.text(off_ms + bl / 2, 0.85, f"burst\nT+{off_ms:g} ms", ha="center", fontsize=8, color=c_ok)
    g = float(a["obs_guard_ms"])
    ax.axvspan(off_ms - g, off_ms + bl + g, ymin=0.05, ymax=0.9, fill=False, ec=c_ok, ls=":")
    marg = res["margin_ms"]["mean"] if res.get("margin_ms") else None
    if marg:
        ax.annotate("", xy=(off_ms, 0.15), xytext=(arm_med, 0.15),
                    arrowprops=dict(arrowstyle="<->", color=fg, alpha=0.7))
        ax.text((arm_med + off_ms) / 2, 0.18, f"margin {marg:.0f} ms (min {a['min_margin_ms']:g})",
                ha="center", fontsize=8)
    ax.set_xlim(lo, hi)
    ax.set_ylim(0, 1.15)
    ax.set_yticks([])
    ax.set_xlabel("time relative to the target PSS [ms]  (median of all executed turns)")
    ax.set_title("One cycle, to scale: nothing is sent reactively, the X410 fires at a timestamp armed tens of ms before")

    # ---- Middle: sync error (robust: centred on the median, gross outliers counted apart)
    OUT_NS = 1000.0

    def clean(y):
        m = np.median(y) if len(y) else 0.0
        good = np.abs(y - m) <= OUT_NS
        return m, good

    ax = fig.add_subplot(gs[1, :2])
    n_out = {}
    for c, col in ((0, c0), (1, c1)):
        x, y = chain(c, "sync_err_ns")
        if len(y):
            m, good = clean(y)
            n_out[c] = int((~good).sum())
            g = y[good]
            ax.plot(x[good], g - g.mean(), ".", ms=4, color=col,
                    label=f"TX ch{a['tx_chans'][c]}: std {g.std():.1f} ns, fixed delay {g.mean():.0f} ns removed"
                    + (f", {n_out[c]} outliers >1 µs not shown" if n_out[c] else ""))
    xp = np.array([tt(t) for t in ex if "pred_err_ns" in t])
    yp = np.array([t["pred_err_ns"] for t in ex if "pred_err_ns" in t])
    if len(yp):
        ax.plot(xp, yp - yp.mean(), "-", lw=0.7, color=c_pred, alpha=0.7,
                label=f"tracker prediction error: std {yp.std():.1f} ns")
    samp = 1e9 / R
    for sg in (-1, 1):
        ax.axhline(sg * samp, color=fg, ls=":", lw=0.8, alpha=0.6)
    ax.set_xlabel("time since first turn [s]")
    ax.set_ylabel("burst seen − (real PSS + offset) [ns]")
    ax.set_title("Sync error per turn (dotted = ±1 sample)")
    ax.grid(True, alpha=0.4)
    ax.legend(fontsize=8, loc="upper right")

    ax = fig.add_subplot(gs[1, 2])
    stds = []
    for c, col in ((0, c0), (1, c1)):
        _, y = chain(c, "sync_err_ns")
        if len(y):
            m, good = clean(y)
            g = y[good] - y[good].mean()
            stds.append((g.std(), np.percentile(np.abs(g), 99), 100 * np.mean(np.abs(g) <= samp + 1)))
            ax.hist(g, bins=np.arange(-4.5, 4.6, 1) * samp, color=col, alpha=0.6,
                    label=f"TX ch{a['tx_chans'][c]}")
    ax.set_xlabel("sync error, demeaned [ns]  (bins = 1 sample)")
    ax.set_ylabel("turns")
    ok = stds and stds[0][0] < 200 and not ev.get("time_error") and not ev.get("underflow")
    if stds:
        ax.set_title(f"Sync error: {stds[0][0]:.1f} ns rms, {stds[0][2]:.2f} % within ±1 sample",
                     color=c_ok if ok else c_bad)
    ax.grid(True, alpha=0.4)
    ax.legend(fontsize=8)

    # ---- Bottom: margin, tracker, latency
    ax = fig.add_subplot(gs[2, 0])
    for st, col, mk in (("EXECUTED_OBSERVED", c_ok, "."), ("EXECUTED_NOT_OBSERVED", c_warn, "x"),
                        ("MISSED_LATE", c_bad, "x"), ("MISSED_NO_LOCK", c_bad, "+")):
        pts = [(tt(t), t["margin_ms"]) for t in turns
               if str(t["state"]).split(":")[0] == st and "margin_ms" in t]
        if pts:
            x, y = zip(*pts)
            ax.plot(x, y, mk, ms=4, color=col, label=f"{st} ({len(pts)})")
    ax.axhline(a["min_margin_ms"], color=c_bad, ls="--", lw=1)
    ax.text(0, a["min_margin_ms"], " min margin", color=c_bad, va="bottom", fontsize=8)
    ax.set_xlabel("time since first turn [s]")
    ax.set_ylabel("t_tx − now at arming [ms]")
    ax.set_title("Arming margin (below the line the turn is skipped)")
    ax.grid(True, alpha=0.4)
    ax.legend(fontsize=7)

    ax = fig.add_subplot(gs[2, 1])
    x = np.array([tt(t) for t in turns if t.get("track_ppm") is not None])
    ppm = np.array([t["track_ppm"] for t in turns if t.get("track_ppm") is not None])
    rs = np.array([t["track_resid_ns"] for t in turns if t.get("track_ppm") is not None])
    if len(x):
        ax.plot(x, np.abs(ppm), color=c0, label=f"|clock offset| (median {np.median(np.abs(ppm)):.3f} ppm)")
        ax.set_ylabel("|X410 vs gNB| [ppm]", color=c0)
        ax2 = ax.twinx()
        ax2.plot(x, rs, color=c_warn, alpha=0.8, label=f"fit residual (median {np.median(rs):.0f} ns)")
        ax2.set_ylabel("fit residual [ns]", color=c_warn)
        h1, l1 = ax.get_legend_handles_labels()
        h2, l2 = ax2.get_legend_handles_labels()
        ax.legend(h1 + h2, l1 + l2, fontsize=7, loc="upper right")
    ax.set_xlabel("time since first turn [s]")
    ax.set_title(f"Tracker ({a['track_window']} PSS window)")
    ax.grid(True, alpha=0.4)

    ax = fig.add_subplot(gs[2, 2])
    for c, col in ((0, c0), (1, c1)):
        xl, yl = chain(c, "latency_ns")
        if len(yl):
            m, good = clean(yl)
            g = yl[good]
            ax.plot(xl[good], g, ".", ms=4, color=col,
                    label=f"TX ch{a['tx_chans'][c]}: {g.mean():.1f} ± {g.std():.1f} ns"
                    + (f" ({int((~good).sum())} outliers)" if (~good).any() else ""))
    ax.set_xlabel("time since first turn [s]")
    ax.set_ylabel("t_obs − t_tx [ns]")
    ax.set_title("TX → air → observer delay (should be constant)")
    ax.grid(True, alpha=0.4)
    ax.legend(fontsize=8)

    out = args.out or run / "dashboard.png"
    fig.savefig(out, dpi=110)
    print(f"Saved {out}")


if __name__ == "__main__":
    main()
