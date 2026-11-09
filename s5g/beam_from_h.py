#!/usr/bin/env python3
"""
Beam pointing derived from the complex H (RX x TX) measured every turn by
x410_pss_timed_tx.py (post-processing; no beams were actually transmitted).

Geometry assumed: TX ULA ch0..ch3 and RX ULA (rx chans in order) with spacing
--spacing, facing each other broadside at --distance.

  TX side: element responses from the RX element nearest broadside (calibration
           source); calibrated and uncalibrated array patterns; angle-of-departure
           of the other RX elements from the phase slope across the TX array.
  RX side: same with the TX element nearest broadside as calibration source.
  Time:    calibration taken from the first --cal-s seconds and kept; angle
           estimates and MRT loss over the rest of the run.

Example: python3 beam_from_h.py RUN_DIR --spacing 0.061 --distance 1.0
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
    p.add_argument("run_dir", type=Path)
    p.add_argument("--spacing", type=float, default=0.061, help="Element spacing [m], both arrays")
    p.add_argument("--distance", type=float, default=1.0, help="TX-RX array distance [m]")
    p.add_argument("--cal-s", type=float, default=30.0, help="Calibration window at the start [s]")
    p.add_argument("--out", type=Path, default=None)
    return p.parse_args()


def load(run):
    s = json.loads((run / "summary.json").read_text())
    a = s["args"]
    rx = [str(r) for r in a["rx_chans"]]
    H, ts = [], []
    for line in (run / "turns.jsonl").open():
        t = json.loads(line)
        if t.get("phase") != "B_offset" or not str(t["state"]).startswith("EXEC"):
            continue
        obs = t.get("obs") or {}
        if all(obs.get(r) and all(d.get("found") for d in obs[r]) for r in rx):
            H.append([[complex(*d["h"]) for d in obs[r]] for r in rx])
            ts.append(t["now_arm"] / float(a["rate"]))
    H, ts = np.array(H), np.array(ts)
    return a, H, ts - ts[0]


def steer(n, psi):
    return np.exp(1j * np.arange(n) * psi)


def pattern(resp, w, theta, kd):
    """|sum_n resp_n w_n exp(j n kd sin(theta))|^2 normalised to one average element."""
    n = np.arange(len(resp))
    A = np.exp(1j * np.outer(kd * np.sin(theta), n))
    return np.abs(A @ (resp * w)) ** 2 / np.mean(np.abs(resp) ** 2)


def slope_angle(ratio, kd):
    """Angle from the phase slope across the array (least squares, unwrapped)."""
    ph = np.unwrap(np.angle(ratio))
    n = np.arange(len(ph))
    slope = np.polyfit(n, ph, 1)[0]
    s = slope / kd
    return np.rad2deg(np.arcsin(np.clip(s, -1, 1))), float(np.rad2deg(np.std(ph - np.polyval(np.polyfit(n, ph, 1), n))))


def lobe(theta_deg, p_db):
    i = int(np.argmax(p_db))
    lo, hi = i, i
    while lo > 0 and p_db[lo - 1] >= p_db[i] - 3:
        lo -= 1
    while hi < len(p_db) - 1 and p_db[hi + 1] >= p_db[i] - 3:
        hi += 1
    return theta_deg[i], theta_deg[hi] - theta_deg[lo]


def main():
    args = parse_args()
    a, H, ts = load(args.run_dir)
    f = float(a["wifi_freq"])
    lam = C / f
    kd = 2 * np.pi * args.spacing / lam
    nrx, ntx = H.shape[1], H.shape[2]
    txc, rxc = a["tx_chans"], a["rx_chans"]
    # remove the common phase of every turn (LO phase between turns), then average the calibration window
    Hn = H * np.exp(-1j * np.angle(H[:, :1, :1]))
    Hcal = Hn[ts < args.cal_s].mean(0)

    # geometry: lateral position of every element relative to its array centre
    xtx = (np.arange(ntx) - (ntx - 1) / 2) * args.spacing
    xrx = (np.arange(nrx) - (nrx - 1) / 2) * args.spacing
    # angle seen from the TX array centre to each RX element, and from the RX centre to each TX element
    geo_aod = np.rad2deg(np.arctan2(xrx, args.distance))
    geo_aoa = np.rad2deg(np.arctan2(xtx, args.distance))
    r_ref = int(np.argmin(np.abs(xrx)))           # RX element closest to TX broadside
    c_ref = int(np.argmin(np.abs(xtx)))           # TX element closest to RX broadside

    # ---- TX array: element responses seen by RX r_ref (its geometric phase removed)
    t_tx = Hcal[r_ref] * np.conj(steer(ntx, kd * np.sin(np.deg2rad(geo_aod[r_ref]))))
    cal_tx = np.exp(-1j * np.angle(t_tx / t_tx[0]))
    aod = [slope_angle(Hcal[r] / Hcal[r_ref], kd) for r in range(nrx)]
    # ---- RX array: element responses seen from TX c_ref
    t_rx = Hcal[:, c_ref] * np.conj(steer(nrx, kd * np.sin(np.deg2rad(geo_aoa[c_ref]))))
    cal_rx = np.exp(-1j * np.angle(t_rx / t_rx[0]))
    aoa = [slope_angle(Hcal[:, c] / Hcal[:, c_ref], kd) for c in range(ntx)]

    theta = np.deg2rad(np.linspace(-90, 90, 1441))
    th_deg = np.rad2deg(theta)
    targets = [0, 15, 30, 45]

    def beams(resp, cal, n):
        out = {}
        for t0 in targets:
            w = cal * np.conj(steer(n, kd * np.sin(np.deg2rad(t0))))
            out[t0] = 10 * np.log10(np.maximum(pattern(resp, w, theta, kd), 1e-6))
        raw = 10 * np.log10(np.maximum(pattern(resp, np.ones(n), theta, kd), 1e-6))
        return out, raw

    btx, rawtx = beams(t_tx, cal_tx, ntx)
    brx, rawrx = beams(t_rx, cal_rx, nrx)

    # ---- over time: angle estimates and loss of a beam calibrated at the start
    win = 10.0
    edges = np.arange(0, ts[-1] + 1e-9, win)
    tw, aod_t, aoa_t, loss_tx, loss_rx = [], [], [], [], []
    w_tx_mrt = np.conj(Hcal[r_ref]) / np.abs(Hcal[r_ref])
    w_rx_mrt = np.conj(Hcal[:, c_ref]) / np.abs(Hcal[:, c_ref])
    for e in edges:
        m = (ts >= e) & (ts < e + win)
        if m.sum() < 5:
            continue
        Hw = Hn[m].mean(0)
        tw.append(e + win / 2)
        aod_t.append([slope_angle(Hw[r] / Hw[r_ref], kd)[0] for r in range(nrx)])
        aoa_t.append([slope_angle(Hw[:, c] / Hw[:, c_ref], kd)[0] for c in range(ntx)])
        h = Hw[r_ref]
        loss_tx.append(20 * np.log10(np.abs(h @ w_tx_mrt) / np.sum(np.abs(h))))
        h = Hw[:, c_ref]
        loss_rx.append(20 * np.log10(np.abs(h @ w_rx_mrt) / np.sum(np.abs(h))))
    tw, aod_t, aoa_t = np.array(tw), np.array(aod_t), np.array(aoa_t)

    # ---- report
    print(f"{len(H)} turns, {ts[-1]:.0f} s, f = {f/1e9:.3f} GHz, d = {args.spacing*100:.1f} cm = {args.spacing/lam:.3f} λ")
    print("|H| dBFS (rows RX, cols TX):\n", np.round(20 * np.log10(np.abs(Hcal)), 1))
    print("TX hardware phase offsets vs TX ch%d [deg]:" % txc[0], np.round(np.rad2deg(np.angle(t_tx / t_tx[0])), 1))
    print("RX hardware phase offsets vs RX ch%d [deg]:" % rxc[0], np.round(np.rad2deg(np.angle(t_rx / t_rx[0])), 1))
    print("AoD of each RX element (from TX array) est / geometry:",
          [(f"RX ch{rxc[r]}", round(aod[r][0], 1), round(geo_aod[r] - geo_aod[r_ref], 1)) for r in range(nrx)])
    print("AoA of each TX element (at RX array) est / geometry:",
          [(f"TX ch{txc[c]}", round(aoa[c][0], 1), round(geo_aoa[c] - geo_aoa[c_ref], 1)) for c in range(ntx)])
    for name, b, raw in (("TX 4-el", btx, rawtx), ("RX 3-el", brx, rawrx)):
        pk, bw = lobe(th_deg, raw)
        print(f"{name} uncalibrated (w=1): peak at {pk:+.1f}°, -3 dB width {bw:.0f}°")
        for t0, p in b.items():
            pk, bw = lobe(th_deg, p)
            print(f"{name} calibrated, steered {t0:2d}°: peak {pk:+.1f}°, width {bw:.0f}°, gain {p.max():+.1f} dB")
    print(f"MRT loss over time with calibration kept from the first {args.cal_s:.0f} s: "
          f"TX worst {min(loss_tx):.2f} dB, RX worst {min(loss_rx):.2f} dB")

    # ---- figure
    plt.style.use("dark_background")
    fg, bg, panel, grid = "#e6edf3", "#0d1117", "#161b22", "#30363d"
    cols = {0: "#58a6ff", 15: "#3fb950", 30: "#f778ba", 45: "#d29922"}
    plt.rcParams.update({"axes.facecolor": panel, "figure.facecolor": bg, "axes.edgecolor": grid,
                         "grid.color": grid, "font.size": 9, "axes.titlesize": 10,
                         "axes.titleweight": "bold"})
    fig = plt.figure(figsize=(18, 11.5))
    gs = GridSpec(2, 3, figure=fig, hspace=0.38, wspace=0.28, left=0.05, right=0.97, top=0.88, bottom=0.06)
    fig.suptitle("X410 4 TX × 3 RX: beam pointing derived from the measured channel (pss_4x3_run1)",
                 fontsize=15, fontweight="bold", x=0.05, ha="left")
    fig.text(0.05, 0.925, f"{len(H)} turns over {ts[-1]:.0f} s · {f/1e9:.3f} GHz · ULA d = {args.spacing*100:.1f} cm "
             f"({args.spacing/lam:.2f} λ) · arrays {args.distance:.1f} m apart, broadside · calibration from the "
             f"first {args.cal_s:.0f} s · no beams were transmitted: patterns are computed from H",
             fontsize=9, alpha=0.8)

    for k, (name, b, raw, n, ref_lbl) in enumerate((
            ("TX array, 4 elements (ch" + ",".join(map(str, txc)) + ")", btx, rawtx, ntx, f"RX ch{rxc[r_ref]}"),
            ("RX array, 3 elements (ch" + ",".join(map(str, rxc)) + ")", brx, rawrx, nrx, f"TX ch{txc[c_ref]}"))):
        ax = fig.add_subplot(gs[0, k], projection="polar")
        ax.set_theta_zero_location("N")
        ax.set_theta_direction(-1)
        ax.set_thetamin(-90)
        ax.set_thetamax(90)
        top = 20 * np.log10(n) + 1
        floor = top - 25
        for t0, p in b.items():
            pk, bw = lobe(th_deg, p)
            ax.plot(theta, np.clip(p, floor, None) - floor, color=cols[t0], lw=1.6,
                    label=f"steer {t0}° → peak {pk:+.0f}°, {bw:.0f}° wide")
        pk, _ = lobe(th_deg, raw)
        ax.plot(theta, np.clip(raw, floor, None) - floor, color=fg, lw=1, ls="--",
                label=f"uncalibrated (w=1) → peak {pk:+.0f}°")
        ax.set_rticks([5, 15, 25])
        ax.set_yticklabels([f"{floor+5:.0f}", f"{floor+15:.0f}", f"{floor+25:.0f} dB"], fontsize=7)
        ax.set_title(f"{name}\ncalibrated with {ref_lbl} as source at broadside", pad=12)
        ax.legend(fontsize=7, loc="lower center", bbox_to_anchor=(0.5, -0.32), ncol=2)

    # cartesian comparison TX vs RX
    ax = fig.add_subplot(gs[0, 2])
    for t0, ls in ((0, "-"), (30, "--")):
        ax.plot(th_deg, btx[t0], color="#58a6ff", ls=ls, label=f"TX 4-el, steer {t0}°")
        ax.plot(th_deg, brx[t0], color="#f778ba", ls=ls, label=f"RX 3-el, steer {t0}°")
    ax.set_xlim(-90, 90)
    ax.set_ylim(-20, 20 * np.log10(ntx) + 1.5)
    ax.axvspan(-45, 45, color=fg, alpha=0.05)
    ax.text(0, -18.5, "useful steering range ≈ ±45°", ha="center", fontsize=8, alpha=0.7)
    ax.set_xlabel("angle from broadside [deg]")
    ax.set_ylabel("gain vs one element [dB]")
    ax.set_title("TX (4) vs RX (3): beamwidth, sidelobes and gain")
    ax.grid(True, alpha=0.4)
    ax.legend(fontsize=8, loc="upper left")

    # angle estimates vs geometry
    ax = fig.add_subplot(gs[1, 0])
    labels, est, geo = [], [], []
    for r in range(nrx):
        if r != r_ref:
            labels.append(f"AoD\nRX ch{rxc[r]}")
            est.append(aod[r][0])
            geo.append(geo_aod[r] - geo_aod[r_ref])
    for c in range(ntx):
        if c != c_ref:
            labels.append(f"AoA\nTX ch{txc[c]}")
            est.append(aoa[c][0])
            geo.append(geo_aoa[c] - geo_aoa[c_ref])
    x = np.arange(len(labels))
    ax.bar(x - 0.18, geo, 0.36, color="#8b949e", label="geometry (1 m, ideal far field)")
    ax.bar(x + 0.18, est, 0.36, color="#58a6ff", label="estimated from H")
    for i, (g_, e_) in enumerate(zip(geo, est)):
        ax.text(i + 0.18, e_, f"{e_:+.1f}°", ha="center", va="bottom" if e_ >= 0 else "top", fontsize=8)
    ax.axhline(0, color=fg, lw=0.6)
    ax.set_xticks(x, labels, fontsize=8)
    ax.set_ylabel("angle relative to the calibration source [deg]")
    ax.set_title("Where does each array 'see' the other antennas?")
    ax.grid(True, axis="y", alpha=0.4)
    ax.legend(fontsize=8)

    ax = fig.add_subplot(gs[1, 1])
    for r in range(nrx):
        if r != r_ref:
            ax.plot(tw, aod_t[:, r], "o-", ms=3, label=f"AoD RX ch{rxc[r]} (TX array)")
    for c in range(ntx):
        if c != c_ref:
            ax.plot(tw, aoa_t[:, c], "s--", ms=3, label=f"AoA TX ch{txc[c]} (RX array)")
    ax.set_xlabel("time [s] (10 s windows)")
    ax.set_ylabel("estimated angle [deg]")
    ax.set_title("Angle estimates over the run (drift = phase drift between chains)")
    ax.grid(True, alpha=0.4)
    ax.legend(fontsize=7)

    ax = fig.add_subplot(gs[1, 2])
    ax.plot(tw, loss_tx, "o-", ms=3, color="#58a6ff", label=f"TX MRT towards RX ch{rxc[r_ref]}")
    ax.plot(tw, loss_rx, "s-", ms=3, color="#f778ba", label=f"RX MRT towards TX ch{txc[c_ref]}")
    ax.axvspan(0, args.cal_s, color=fg, alpha=0.06)
    ax.text(args.cal_s / 2, 0.02, "calibration", ha="center", fontsize=8, alpha=0.7)
    ax.set_xlabel("time [s]")
    ax.set_ylabel("loss vs ideal beam [dB]")
    ax.set_ylim(min(-0.5, min(loss_tx + loss_rx) * 1.3), 0.1)
    ax.set_title("Beam calibrated at the start and never updated")
    ax.grid(True, alpha=0.4)
    ax.legend(fontsize=8)

    out = args.out or args.run_dir / "beam_pointing_from_h.png"
    fig.savefig(out, dpi=100)
    print("Saved", out)


if __name__ == "__main__":
    main()
