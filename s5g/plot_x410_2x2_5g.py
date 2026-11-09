#!/usr/bin/env python3
"""
Dashboard for a run of x410_2x2_5g.py (post-processing, no hardware needed).

Reads <run_dir>/summary.json, nr_events.jsonl and wifi_2x2.npz and draws:
  Row 1 (5G):   PSS/SSS metric timeline with phases A/B/C, valid rate per
                phase, RX peak level (clipping check).
  Row 2 (5G):   SSB timing in X410 ticks: residual after a linear fit
                (jitter) and the clock offset implied by the SSB period,
                compared with the CFO measured on the carrier.
  Row 3 (WiFi): |H| of every link, phase stability of TX1/TX0 per RX
                (what beamforming weights depend on), MRT gain / ZF isolation
                per iteration, leakage check.

Example:
  python3 plot_x410_2x2_5g.py x410_2x2_5g_20260924_135149
  python3 plot_x410_2x2_5g.py RUN_DIR --light --out run1.png
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

PHASES = [("A_5g_only", "A · 5G only"), ("B_5g_plus_2x2", "B · 5G + WiFi 2x2"),
          ("C_5g_only", "C · 5G only")]


def parse_args():
    p = argparse.ArgumentParser(description="Dashboard for x410_2x2_5g.py runs")
    p.add_argument("run_dir", type=Path)
    p.add_argument("--light", action="store_true", help="Light background")
    p.add_argument("--out", type=Path, default=None, help="Default: <run_dir>/dashboard.png")
    return p.parse_args()


def db20(x):
    return 20 * np.log10(np.maximum(np.abs(x), 1e-15))


def main():
    args = parse_args()
    run = args.run_dir
    summary = json.loads((run / "summary.json").read_text())
    rows = [json.loads(line) for line in (run / "nr_events.jsonl").open()]
    wifi_path = run / "wifi_2x2.npz"
    z = np.load(wifi_path) if wifi_path.exists() else None

    if args.light:
        plt.style.use("default")
        fg, bg, panel, grid = "#1b1f24", "#ffffff", "#f6f8fa", "#d0d7de"
    else:
        plt.style.use("dark_background")
        fg, bg, panel, grid = "#e6edf3", "#0d1117", "#161b22", "#30363d"
    c_phase = {"A_5g_only": "#58a6ff", "B_5g_plus_2x2": "#f778ba", "C_5g_only": "#3fb950"}
    c_bad, c_warn = "#f85149", "#d29922"
    plt.rcParams.update({"axes.facecolor": panel, "figure.facecolor": bg,
                         "axes.edgecolor": grid, "grid.color": grid, "text.color": fg,
                         "axes.labelcolor": fg, "xtick.color": fg, "ytick.color": fg,
                         "font.size": 9, "axes.titlesize": 10, "axes.titleweight": "bold"})

    # ---------------- 5G data ----------------
    rate = float(rows[0]["device_tick_rate_hz"])
    block_ms = float(summary["args"]["nr_block_ms"])
    per = int(round(rate * block_ms * 1e-3))
    t0 = rows[0]["block_start_device_ticks"]
    t_blk = np.array([(r["block_start_device_ticks"] - t0) / rate for r in rows])
    valid = np.array([bool(r.get("valid")) for r in rows])
    phase = np.array([r["phase"] for r in rows])
    v_rows = [r for r in rows if r.get("valid")]
    ev = np.array([r["event_device_ticks"] for r in v_rows], dtype=np.int64)
    k = np.round((ev - ev[0]) / per)
    fit = np.polyfit(k, ev - ev[0], 1)
    resid_ns = ((ev - ev[0]) - np.polyval(fit, k)) / rate * 1e9
    t_ev = (ev - t0) / rate
    clock_ppm = (fit[0] / per - 1) * 1e6
    nr_freq = float(summary["args"]["nr_freq"])
    cfo_ppm = summary["nr_lock"]["cfo_hz"] / nr_freq * 1e6
    expected = int(k[-1]) + 1
    coverage = 100 * len(v_rows) / expected

    fig = plt.figure(figsize=(18, 13.5))
    gs = GridSpec(3, 3, figure=fig, hspace=0.45, wspace=0.25,
                  left=0.05, right=0.98, top=0.88, bottom=0.05)
    nr_sum = {s["phase"]: s for s in summary["nr_phases"]}
    fig.suptitle("X410 bench: WiFi 2x2 (DB0) + 5G SSB RX (DB1) in one UHD session",
                 fontsize=15, fontweight="bold", x=0.05, ha="left")
    lock = summary["nr_lock"]
    a = summary["args"]
    fig.text(0.05, 0.94,
             f"5G ch{a['nr_chan']} {nr_freq/1e6:.2f} MHz @ {rate/1e6:.2f} Msps, gain {a['nr_gain']:.0f} dB, "
             f"NCellID {lock['ncellid']}, ibar_ssb {lock['ibar_ssb']}, CFO {lock['cfo_hz']:+.0f} Hz  |  "
             f"WiFi TX ch{a['tx_chans']} RX ch{a['rx_chans']}+obs ch{a['observer_chan']} "
             f"{a['wifi_freq']/1e9:.3f} GHz, tone, TX gain {a['tx_gain']:.0f} dB, RX gain {a['rx_gain']:.0f} dB  |  "
             f"run {run.name}", fontsize=9, color=fg, alpha=0.8)

    def shade(ax):
        for key, label in PHASES:
            m = phase == key
            if m.any():
                ax.axvspan(t_blk[m].min(), t_blk[m].max(), color=c_phase[key], alpha=0.08, lw=0)

    # 1a. PSS / SSS timeline
    ax = fig.add_subplot(gs[0, :2])
    shade(ax)
    pss = np.array([r["pss_metric"] for r in v_rows])
    sss = np.array([r["sss_metric"] for r in v_rows])
    t_v = t_blk[valid]
    ax.plot(t_v, sss, ".", ms=2, color="#a371f7", alpha=0.5, label="SSS metric")
    for key, label in PHASES:
        m = np.array([r["phase"] == key for r in v_rows])
        ax.plot(t_v[m], pss[m], ".", ms=2.5, color=c_phase[key], label=f"PSS · {label}")
    ax.plot(t_blk[~valid], np.full((~valid).sum(), 0.02), "x", ms=5, color=c_bad,
            label=f"invalid block ({(~valid).sum()})")
    thr = 0.5
    ax.axhline(thr, color=c_warn, ls="--", lw=1)
    ax.text(t_blk[-1], thr + 0.01, "PSS threshold", color=c_warn, ha="right", va="bottom", fontsize=8)
    ax.set_ylim(0, 1.05)
    ax.set_xlabel("X410 device time since first block [s]")
    ax.set_ylabel("metric")
    ax.set_title("5G SSB detection over time (continuous RX on ch2 through the 3 phases)")
    ax.grid(True, alpha=0.4)
    ax.legend(fontsize=7.5, loc="lower center", ncol=5, markerscale=3)

    # 1b. Valid rate per phase
    ax = fig.add_subplot(gs[0, 2])
    names = [label for key, label in PHASES if key in nr_sum]
    vals = [nr_sum[key]["valid_pct"] for key, _ in PHASES if key in nr_sum]
    cols = [c_phase[key] for key, _ in PHASES if key in nr_sum]
    bars = ax.bar(names, vals, color=cols, width=0.6)
    for b, (key, _) in zip(bars, PHASES):
        s = nr_sum[key]
        ax.text(b.get_x() + b.get_width() / 2, b.get_height() + 0.1,
                f"{s['valid_pct']:.2f} %\n{s['valid']}/{s['captured']}", ha="center", va="bottom", fontsize=8)
    lo = max(0, min(vals) - 5)
    ax.set_ylim(lo, 101.5)
    ax.set_ylabel("valid SSB blocks [%]")
    delta = summary.get("nr_valid_delta_pct_points", 0.0)
    ax.set_title(f"5G valid rate per phase (B − A = {delta:+.2f} pts)")
    ax.grid(True, axis="y", alpha=0.4)
    ax.tick_params(axis="x", labelsize=8)

    # 2a. SSB timing residual
    ax = fig.add_subplot(gs[1, :2])
    shade(ax)
    for key, label in PHASES:
        m = np.array([r["phase"] == key for r in v_rows])
        ax.plot(t_ev[m], resid_ns[m], ".", ms=2.5, color=c_phase[key], label=label)
    samp_ns = 1e9 / rate
    for s in (-1, 1):
        ax.axhline(s * samp_ns, color=fg, ls=":", lw=0.8, alpha=0.6)
    ax.text(t_ev[-1], samp_ns, " ±1 sample", color=fg, fontsize=8, va="bottom", ha="right", alpha=0.8)
    ax.set_xlabel("X410 device time since first block [s]")
    ax.set_ylabel("PSS start − linear fit [ns]")
    ax.set_title(f"SSB timestamps in X410 ticks: residual jitter {resid_ns.std():.0f} ns rms, "
                 f"max {np.abs(resid_ns).max():.0f} ns  (1 sample = {samp_ns:.0f} ns)")
    ax.grid(True, alpha=0.4)
    ax.legend(fontsize=8, loc="upper left", markerscale=3)

    # 2b. Clock offset from SSB period vs CFO
    ax = fig.add_subplot(gs[1, 2])
    labels = ["SSB period\n(timing)", "CFO on carrier\n(frequency)"]
    vals2 = [abs(clock_ppm), abs(cfo_ppm)]
    bars = ax.bar(labels, vals2, color=["#58a6ff", "#a371f7"], width=0.55)
    for b, v in zip(bars, vals2):
        ax.text(b.get_x() + b.get_width() / 2, v, f"{v:+.3f} ppm", ha="center",
                va="bottom" if v >= 0 else "top", fontsize=10, fontweight="bold")
    ax.axhline(0, color=fg, lw=0.8)
    ax.set_ylabel("|X410 clock offset vs gNB| [ppm]")
    ax.set_ylim(min(0, *vals2) * 1.4 - 0.02, max(0, *vals2) * 1.4 + 0.02)
    ax.set_title("Same oscillator seen two ways")
    ax.text(0.5, 0.04, f"measured SSB period = {fit[0]:.2f} samples\n"
            f"(nominal {per}); SSB coverage {coverage:.1f} % of {expected}",
            transform=ax.transAxes, ha="center", fontsize=8, alpha=0.85)
    ax.grid(True, axis="y", alpha=0.4)

    # ---------------- WiFi ----------------
    if z is not None:
        H, amp, Yt = z["H"], float(z["amp"]), z["Yt"]
        rxn = [str(x) for x in z["rx_names"]]
        rxc = [int(x) for x in z["rx_chans"]]
        txc = [int(x) for x in z["tx_chans"]]
        it = np.arange(H.shape[0])
        link_cols = ["#58a6ff", "#79c0ff", "#f778ba", "#ffa7d8", "#3fb950", "#7ee787"]

        ax = fig.add_subplot(gs[2, 0])
        j = 0
        for r in range(H.shape[1]):
            for n in range(2):
                ls = "-" if n == 1 else "--"
                ax.plot(it, db20(H[:, r, n] * amp), ls, marker="o", ms=3, color=link_cols[j],
                        label=f"RX {rxn[r]} (ch{rxc[r]}) ← TX ch{txc[n]}")
                j += 1
        ax.set_xlabel("2x2 iteration (~1.1 s each)")
        ax.set_ylabel("|H| [dBFS]")
        ax.set_title("WiFi link levels during phase B")
        ax.grid(True, alpha=0.4)
        ax.legend(fontsize=7, ncol=2, loc="lower right")

        ax = fig.add_subplot(gs[2, 1])
        rat = H[:, :, 1] / H[:, :, 0]
        for r in range(H.shape[1]):
            ph = np.rad2deg(np.angle(rat[:, r] / rat[0, r]))
            sd = np.std(ph)
            ax.plot(it, ph, marker="o", ms=3, color=link_cols[2 * r],
                    label=f"RX {rxn[r]}: std {sd:.1f}°, amp ratio {np.median(db20(rat[:, r])):+.1f} dB")
        ax.axhline(0, color=fg, lw=0.8)
        ax.set_xlabel("2x2 iteration")
        ax.set_ylabel("∠(H_TX1 / H_TX0) − first [deg]")
        ax.set_title("Relative TX phase per RX (what the BF weights depend on)")
        ax.grid(True, alpha=0.4)
        ax.legend(fontsize=7.5, loc="lower left")

        ax = fig.add_subplot(gs[2, 2])
        ref = np.mean(np.abs(H[:, :2, :]) ** 2, axis=2)
        pa = 10 * np.log10(np.abs(Yt[:, 0, :]) ** 2 / ref[:, [0]])
        pb = 10 * np.log10(np.abs(Yt[:, 1, :]) ** 2 / ref[:, [1]])
        mrt_a, mrt_b = pa[:, 1], pb[:, 2]
        zf_a, zf_b = pa[:, 3] - pb[:, 3], pb[:, 4] - pa[:, 4]
        ax.plot(it, zf_a, "o-", ms=3, color="#f778ba", label=f"ZF isolation A over B (median {np.median(zf_a):+.1f} dB)")
        ax.plot(it, zf_b, "s-", ms=3, color="#ffa7d8", label=f"ZF isolation B over A (median {np.median(zf_b):+.1f} dB)")
        ax.plot(it, mrt_a, "o-", ms=3, color="#58a6ff", label=f"MRT gain at A (median {np.median(mrt_a):+.1f} dB)")
        ax.plot(it, mrt_b, "s-", ms=3, color="#79c0ff", label=f"MRT gain at B (median {np.median(mrt_b):+.1f} dB)")
        ax.axhline(6.02, color=fg, ls=":", lw=0.8)
        ax.text(0, 6.3, "ideal MRT, 2 elements (+6 dB)", fontsize=7.5, alpha=0.8)
        ax.set_xlabel("2x2 iteration")
        ax.set_ylabel("dB")
        ax.set_title("Beamforming results per iteration")
        ax.grid(True, alpha=0.4)
        ax.legend(fontsize=7, loc="upper right")

    if "leakage" in summary:
        txt = "Leakage check, WiFi RX on antenna vs TERMINATION (margin): " + "   ".join(
            f"RX {x['rx']} ← TX ch{x['tx']}: {x['margin_db']:.1f} dB" for x in summary["leakage"])
        fig.text(0.05, 0.918, txt, fontsize=9, color=fg, alpha=0.8)

    out = args.out or run / "dashboard.png"
    fig.savefig(out, dpi=110)
    print(f"Saved {out}")


if __name__ == "__main__":
    main()
