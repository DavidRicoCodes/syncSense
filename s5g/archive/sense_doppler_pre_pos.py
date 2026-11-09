#!/usr/bin/env python3
"""
Doppler per sector from x410_two_ap_sense.py runs (post-processing).

For every direction available (A->B from B's folder, B->A from A's folder):
  1. CFO between the APs: phase slope across the pulses of each burst
     (compared with the CFO predicted by the 5G alone: (ppm_tx - ppm_rx) * f,
     each AP's ppm measured against the gNB by its own tracker)
  2. pulses averaged -> one 3x4 snapshot per burst (100 Hz); bursts hit by
     interference (matched-filter quality < 1/4 of the median) are replaced by
     interpolation of their neighbours
  3. common phase per burst referenced to the static channel (direct path)
  4. static removal: subtract the mean channel
  5. calibration of both arrays with the mean channel (partner AP = reference
     source at --*-partner-deg) and virtual sectors: TX sectors of the
     transmitter x RX sectors of the receiver
  6. spectrogram per sector pair and "moving" energy (|f| > --static-hz)

Time axis: seconds since --tx-start of the run; --marks draws protocol lines.
Doppler f -> bistatic path-length rate v = f * lambda (m/s).

Example:
  python3 sense_doppler.py --a sense_L_walk_A --b sense_L_walk_B --marks 60 90 --out walk.png
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
    p.add_argument("--a", type=Path, default=None, help="AP A folder (contains B->A data)")
    p.add_argument("--b", type=Path, default=None, help="AP B folder (contains A->B data)")
    p.add_argument("--spacing", type=float, default=0.030)
    p.add_argument("--a-partner-deg", type=float, default=32.0, help="B seen from A's broadside")
    p.add_argument("--b-partner-deg", type=float, default=30.0, help="A seen from B's broadside")
    p.add_argument("--a-sectors", type=float, nargs="+", default=[-31.0, -9.0])
    p.add_argument("--b-sectors", type=float, nargs="+", default=[-26.0, -7.0])
    p.add_argument("--win-s", type=float, default=0.5)
    p.add_argument("--hop-s", type=float, default=0.1)
    p.add_argument("--max-hz", type=float, default=80.0)
    p.add_argument("--static-hz", type=float, default=2.5, help="|f| below this is not counted as movement")
    p.add_argument("--marks", type=float, nargs="*", default=[], help="Protocol times [s since TX start]")
    p.add_argument("--title", default="")
    p.add_argument("--out", type=Path, default=Path("sense_doppler.png"))
    return p.parse_args()


def steer(n, kd, deg):
    return np.exp(1j * np.arange(n) * kd * np.sin(np.deg2rad(deg)))


def tx_start(folder):
    try:
        return float(json.loads((folder / "summary.json").read_text())["args"]["tx_start_unix"])
    except Exception:
        return None


def burst_cfo(h, dt_p):
    K = h.shape[1]
    zp = np.einsum("bkrc,brc->bk", h, np.conj(h[:, 0]))
    ph = np.unwrap(np.angle(zp), axis=1)
    return np.polyfit(np.arange(K) * dt_p, ph.T, 1)[0] / (2 * np.pi)


def process(rx_folder, tx_folder, kd, tx_partner, rx_partner, tx_sectors, rx_sectors, args):
    z = np.load(rx_folder / "sense_rx.npz")
    h = z["h"].astype(np.complex128)                 # (B, K, R, T)
    Rs, S = float(z["rate"]), int(z["pulse_spacing"])
    f0 = float(z["wifi_freq"])
    t0 = tx_start(rx_folder) or float(z["unix"][0])
    t = z["unix"] - t0
    Bn, K, Rr, Tt = h.shape
    dt_p = S / Rs
    # 1. CFO measured vs predicted by the 5G (partner's ppm from its own folder)
    cfo_b = burst_cfo(h, dt_p)
    cfo = float(np.median(cfo_b))
    pred = None
    if tx_folder is not None and (tx_folder / "sense_rx.npz").exists():
        zt = np.load(tx_folder / "sense_rx.npz")
        ppm_tx = np.interp(z["unix"], zt["unix"], zt["ppm"])
        pred = f0 * (ppm_tx - z["ppm"]) * 1e-6
    corr = np.exp(-2j * np.pi * cfo * np.arange(K) * dt_p)
    Hb = np.einsum("bkrc,k->brc", h, corr) / K
    # 2. interference-hit bursts -> interpolated from the neighbours
    q = z["q"]
    bad = q < np.median(q) / 4
    if bad.any() and (~bad).sum() > 2:
        idx = np.arange(Bn)
        flat = Hb.reshape(Bn, -1)
        for j in range(flat.shape[1]):
            re = np.interp(idx[bad], idx[~bad], flat[~bad, j].real)
            im = np.interp(idx[bad], idx[~bad], flat[~bad, j].imag)
            flat[bad, j] = re + 1j * im
        Hb = flat.reshape(Bn, Rr, Tt)
    # 3. common phase referenced to the static channel
    ref = Hb.mean(0)
    for _ in range(2):
        phi = np.angle(np.einsum("brc,rc->b", Hb, np.conj(ref)))
        Hb = Hb * np.exp(-1j * phi)[:, None, None]
        ref = Hb.mean(0)
    Hbar = ref
    D = Hb - Hbar
    # 5. calibration + sectors
    rm, cm = Rr // 2, Tt // 2
    hw_tx = Hbar[rm] / steer(Tt, kd, tx_partner)
    hw_rx = Hbar[:, cm] / steer(Rr, kd, rx_partner)
    Wt = np.stack([np.conj(hw_tx / np.abs(hw_tx) * steer(Tt, kd, s)) for s in tx_sectors])
    Wr = np.stack([np.conj(hw_rx / np.abs(hw_rx) * steer(Rr, kd, s)) for s in rx_sectors])
    Y = np.einsum("qr,brc,sc->bqs", Wr, D, Wt)
    Yall = D.reshape(Bn, -1)
    # 6. spectrograms
    fs = 1 / np.median(np.diff(t))
    nw = int(round(args.win_s * fs))
    hop = max(1, int(round(args.hop_s * fs)))
    nfft = 1 << int(np.ceil(np.log2(4 * nw)))
    w = np.hanning(nw)
    starts = np.arange(0, Bn - nw, hop)
    f = np.fft.fftshift(np.fft.fftfreq(nfft, 1 / fs))

    def spec(x):
        x = x if x.ndim == 2 else x[:, None]
        P = np.zeros((len(starts), nfft))
        for i, s0 in enumerate(starts):
            seg = x[s0:s0 + nw] * w[:, None]
            P[i] = np.sum(np.abs(np.fft.fftshift(np.fft.fft(seg, nfft, axis=0), axes=0)) ** 2, axis=1)
        return P

    specs = {(qq, s): spec(Y[:, qq, s]) for qq in range(len(rx_sectors)) for s in range(len(tx_sectors))}
    spec_all = spec(Yall)
    tc = t[starts + nw // 2]
    mov = np.abs(f) > args.static_hz
    energy = {k: v[:, mov].sum(1) for k, v in specs.items()}
    Pm = spec_all.copy()
    Pm[:, ~mov] = 0
    fdom = f[np.argmax(Pm, axis=1)]
    return dict(t=t, cfo=cfo, cfo_b=cfo_b, pred=pred, f=f, tc=tc, specs=specs, spec_all=spec_all,
                energy=energy, n=Bn, fs=fs, bad=int(bad.sum()), fdom=fdom, lam=C / f0,
                e_all=spec_all[:, mov].sum(1), tx_sectors=tx_sectors, rx_sectors=rx_sectors)


def main():
    a = parse_args()
    dirs = []
    if a.b is not None and (a.b / "sense_rx.npz").exists():
        dirs.append(("A → B (received at B)", a.b, a.a, a.a_partner_deg, a.b_partner_deg, a.a_sectors, a.b_sectors))
    if a.a is not None and (a.a / "sense_rx.npz").exists():
        dirs.append(("B → A (received at A)", a.a, a.b, a.b_partner_deg, a.a_partner_deg, a.b_sectors, a.a_sectors))
    if not dirs:
        raise SystemExit("no sense_rx.npz found")
    results = []
    for name, rxf, txf, txp, rxp, txs, rxs in dirs:
        f0 = float(np.load(rxf / "sense_rx.npz")["wifi_freq"])
        kd = 2 * np.pi * a.spacing / (C / f0)
        r = process(rxf, txf, kd, txp, rxp, txs, rxs, a)
        r["name"] = name
        results.append(r)
        line = (f"{name}: {r['n']} bursts at {r['fs']:.1f} Hz, {r['bad']} interference-hit bursts interpolated | "
                f"CFO measured {r['cfo']:+.1f} Hz")
        if r["pred"] is not None:
            line += f", predicted by 5G {np.median(r['pred']):+.1f} Hz (diff {r['cfo'] - np.median(r['pred']):+.1f} Hz)"
        print(line)
        segs = [0.0] + list(a.marks) + [float(r["tc"][-1]) + 0.1]
        for s0, s1 in zip(segs[:-1], segs[1:]):
            m = (r["tc"] >= s0) & (r["tc"] < s1)
            if m.any():
                fd = r["fdom"][m]
                print(f"   {s0:5.0f}-{s1:5.0f} s: moving energy (all links) median "
                      f"{10*np.log10(np.median(r['e_all'][m])+1e-30):6.1f} dB | |dominant Doppler| median "
                      f"{np.median(np.abs(fd)):5.1f} Hz ≈ {np.median(np.abs(fd))*r['lam']:.2f} m/s "
                      f"(p90 {np.percentile(np.abs(fd), 90):.1f} Hz)")
                for k, e in r["energy"].items():
                    print(f"        RX {rxs[k[0]]:+.0f}° × TX {txs[k[1]]:+.0f}°: "
                          f"{10*np.log10(np.median(e[m])+1e-30):6.1f} dB")

    plt.style.use("dark_background")
    bg, panel, grid = "#0d1117", "#161b22", "#30363d"
    plt.rcParams.update({"axes.facecolor": panel, "figure.facecolor": bg, "axes.edgecolor": grid,
                         "grid.color": grid, "font.size": 9, "axes.titlesize": 10, "axes.titleweight": "bold"})
    ncol = max(len(r["specs"]) for r in results) + 1
    fig_h = 6.4 * len(results) + 1.2
    fig = plt.figure(figsize=(4.2 * ncol + 1, fig_h))
    gs = GridSpec(len(results) * 3, ncol, figure=fig, hspace=0.75, wspace=0.3, left=0.05, right=0.98,
                  top=1 - 1.25 / fig_h, bottom=0.05, height_ratios=[1, 0.45, 0.4] * len(results))
    fig.suptitle(f"Bistatic Doppler per sector — two APs timed by 5G, {results[0]['fs']:.0f} Hz per direction {a.title}",
                 fontsize=14, fontweight="bold", x=0.05, ha="left", y=0.995)
    lam = results[0]["lam"]
    fig.text(0.05, 1 - 0.75 / fig_h, f"time since TX start · Doppler f ↔ bistatic path rate v = f·λ "
             f"(λ = {lam*100:.1f} cm): 10 Hz ≈ {10*lam:.2f} m/s · static channel removed · CFO corrected per burst · "
             f"dashed lines = protocol marks {a.marks}", fontsize=9, alpha=0.8)
    for i, r in enumerate(results):
        allP = [10 * np.log10(v + 1e-30) for v in r["specs"].values()] + [10 * np.log10(r["spec_all"] + 1e-30)]
        vmax = max(p.max() for p in allP)
        vmin = vmax - 40
        panels = [("all links", r["spec_all"])] + [
            (f"RX {r['rx_sectors'][q]:+.0f}° × TX {r['tx_sectors'][s]:+.0f}°", v) for (q, s), v in r["specs"].items()]
        for j, (title, P) in enumerate(panels):
            ax = fig.add_subplot(gs[3 * i, j])
            ax.imshow(10 * np.log10(P.T + 1e-30), aspect="auto", origin="lower", cmap="magma", vmin=vmin, vmax=vmax,
                      extent=[r["tc"][0], r["tc"][-1], r["f"][0], r["f"][-1]])
            ax.set_ylim(-a.max_hz, a.max_hz)
            for mk in a.marks:
                ax.axvline(mk, color="w", ls="--", lw=0.8, alpha=0.7)
            ax.set_xlabel("time [s]")
            ax.set_ylabel("Doppler [Hz]" if j == 0 else "")
            ax.set_title(f"{r['name']}\n{title}" if j == 0 else title, fontsize=9)
        ax = fig.add_subplot(gs[3 * i + 1, :])
        for (q, s), e in r["energy"].items():
            ax.plot(r["tc"], 10 * np.log10(e + 1e-30), lw=1.1,
                    label=f"RX {r['rx_sectors'][q]:+.0f}° × TX {r['tx_sectors'][s]:+.0f}°")
        for mk in a.marks:
            ax.axvline(mk, color="w", ls="--", lw=0.8, alpha=0.7)
        ax.set_ylabel("moving energy [dB]")
        ax.set_title(f"Energy with |Doppler| > {a.static_hz} Hz per sector pair", fontsize=9)
        ax.grid(True, alpha=0.4)
        ax.legend(fontsize=7, ncol=4, loc="upper right")
        ax = fig.add_subplot(gs[3 * i + 2, :])
        tt = r["t"]
        edges = np.arange(tt[0], tt[-1], 5.0)
        mid = edges[:-1] + 2.5
        meas = [np.median(r["cfo_b"][(tt >= e0) & (tt < e0 + 5)]) for e0 in edges[:-1]]
        ax.plot(mid, meas, "o-", ms=3, color="#58a6ff", label=f"measured on the WiFi pulses ({r['cfo']:+.1f} Hz)")
        if r["pred"] is not None:
            pr = [np.median(r["pred"][(tt >= e0) & (tt < e0 + 5)]) for e0 in edges[:-1]]
            ax.plot(mid, pr, "s-", ms=3, color="#d29922",
                    label=f"predicted by the 5G alone ({np.median(r['pred']):+.1f} Hz)")
        ax.set_xlabel("time [s]")
        ax.set_ylabel("CFO [Hz]")
        ax.set_title("CFO between the APs: WiFi measurement vs 5G prediction (5 s medians)", fontsize=9)
        ax.grid(True, alpha=0.4)
        ax.legend(fontsize=7, loc="lower right")
    fig.savefig(a.out, dpi=90)
    print("Saved", a.out)


if __name__ == "__main__":
    main()
