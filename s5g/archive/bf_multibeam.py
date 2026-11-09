#!/usr/bin/env python3
"""
Step 8: Simultaneous multi-beam transmission on a USRP X410 (4 TX x 4 RX).

Each beam k has its own weight vector W[:, k] (N TX elements) and carries its
own tone f_k, so all beams are transmitted AT THE SAME TIME and each RX can
still tell them apart:

    x(t) = sum_k  W[:, k] * exp(j 2 pi f_k t)      ->  received G = H @ W

Modes (--mode):
  angles : beams steered to the directions in --beams (deg), using a
           calibration against RX A (assumed at --theta-a).
  mrt    : one beam per RX, max power at that RX (phase-only).
  zf     : one beam per RX, max at that RX with nulls on all the others.

Measured in one session:
  1. Sounding -> H (M RX x N TX)
  2. All beams simultaneously + each beam alone (superposition check)
  3. G measured vs predicted H @ W, per-RX metrics, heatmap
  4. Optional --hold: keep all beams on until Ctrl+C (e.g. for the other USRP)

Wiring as in bf_4x4.py: TX on TX/RX0 of ch0..ch3 (linear, spacing d),
RX A..D on RX1 of ch0..ch3. Requires bf_4x4.py in the same folder.
Sign convention: positive angles point toward the LAST --tx-chans element.

Examples:
  python3 bf_multibeam.py                                  # beams at -45 -15 15 45 deg
  python3 bf_multibeam.py --beams -30 0 30                 # 3 beams
  python3 bf_multibeam.py --mode zf                        # one interference-free beam per RX
  python3 bf_multibeam.py --mode angles --beams -20 20 --hold
"""
import argparse
import signal
import threading
import time

import numpy as np
import uhd

from bf4x4 import C, db10, run_burst, setup_usrp


def parse_args():
    p = argparse.ArgumentParser(description="X410 simultaneous multi-beam test")
    p.add_argument("--args", default="addr=192.168.10.2")
    p.add_argument("--freq", type=float, default=2.45e9)
    p.add_argument("--rate", type=float, default=1.92e6)
    p.add_argument("--amp", type=float, default=0.25,
                   help="Max digital amplitude per TX channel (shared by all beams)")
    p.add_argument("--tx-gain", type=float, default=10.0)
    p.add_argument("--rx-gain", type=float, default=20.0)
    p.add_argument("--tx-chans", type=int, nargs="+", default=[0, 1, 2, 3])
    p.add_argument("--rx-chans", type=int, nargs="+", default=[0, 1, 2, 3])
    p.add_argument("--rx-ant", default="RX1")
    p.add_argument("--spacing", type=float, default=None, help="TX element spacing [m]")
    p.add_argument("--theta-a", type=float, default=0.0, help="Angle of RX A [deg]")
    p.add_argument("--mode", choices=["angles", "mrt", "zf"], default="angles")
    p.add_argument("--beams", type=float, nargs="+", default=[-45.0, -15.0, 15.0, 45.0],
                   help="Beam directions [deg] (mode angles)")
    p.add_argument("--tone0", type=float, default=40e3, help="Tone of beam 0 [Hz]")
    p.add_argument("--tone-step", type=float, default=30e3, help="Tone spacing between beams [Hz]")
    p.add_argument("--grid", type=float, default=10e3,
                   help="Tones are snapped to multiples of this [Hz] (keeps them orthogonal)")
    p.add_argument("--seg-ms", type=float, default=10.0)
    p.add_argument("--hold", action="store_true", help="Keep all beams on until Ctrl+C")
    p.add_argument("--save", default="bf_multibeam.npz")
    p.add_argument("--plot", default="bf_multibeam.png")
    return p.parse_args()


def main():
    args = parse_args()
    lam = C / args.freq
    d = args.spacing if args.spacing else lam / 2
    txc, rxc = args.tx_chans, args.rx_chans
    N, M = len(txc), len(rxc)
    names = [chr(ord("A") + i) for i in range(M)]
    k_idx = np.arange(N)

    usrp = setup_usrp(args)
    rate = usrp.get_tx_rate(txc[0])
    tx_args = uhd.usrp.StreamArgs("fc32", "sc16")
    tx_args.channels = txc
    tx = usrp.get_tx_stream(tx_args)
    rx_args = uhd.usrp.StreamArgs("fc32", "sc16")
    rx_args.channels = rxc
    rx = usrp.get_rx_stream(rx_args)

    # Tone plan: all tones on a common grid -> orthogonal over any window that
    # is a multiple of one grid period
    n_grid = int(round(rate / args.grid))
    grid = rate / n_grid
    K = len(args.beams) if args.mode == "angles" else M
    tones = [grid * round((args.tone0 + k * args.tone_step) / grid) for k in range(K)]
    if len(set(tones)) != K or max(tones) > 0.4 * rate:
        raise SystemExit(f"Bad tone plan {tones}: adjust --tone0/--tone-step/--rate")
    seg_len = max(1, int(round(args.seg_ms * 1e-3 * rate / n_grid))) * n_grid
    guard = n_grid * int(np.ceil(0.2 * seg_len / n_grid))

    print(f"\n{N} TX x {M} RX, mode '{args.mode}', {K} beams, "
          f"tones: {', '.join(f'{t/1e3:.0f}' for t in tones)} kHz")

    def build(segments):
        """segments: list of lists of (amplitude, weight vector, tone index)."""
        buf = np.zeros((N, len(segments) * seg_len), dtype=np.complex64)
        for i, comps in enumerate(segments):
            sl = slice(i * seg_len, (i + 1) * seg_len)
            idx = np.arange(i * seg_len, (i + 1) * seg_len)
            for amp, w, k in comps:
                s = np.exp(2j * np.pi * tones[k] * idx / rate)
                buf[:, sl] += (amp * np.asarray(w)[:, None] * s[None, :]).astype(np.complex64)
        return buf

    def measure(segments):
        """Run one burst; returns Y[seg, rx, tone] (complex amplitude of every tone)."""
        buf = build(segments)
        out, e = run_burst(usrp, tx, rx, buf, M)
        if e:
            raise RuntimeError(f"Burst failed: {e}")
        pk = np.max(np.abs(out))
        if pk > 0.8:
            print(f"[WARN] RX peak {pk:.2f}: close to saturation, lower the gains")
        Y = np.zeros((len(segments), M, K), dtype=np.complex128)
        for i in range(len(segments)):
            a, b = i * seg_len + guard, (i + 1) * seg_len - guard
            idx = np.arange(a, b)
            for k, f in enumerate(tones):
                ref = np.exp(-2j * np.pi * f * idx / rate)
                Y[i, :, k] = np.mean(out[:, a:b] * ref[None, :], axis=1)
        return Y

    # ---------------- 1. Sounding ----------------
    eye = np.eye(N)
    Ys = measure([[]] + [[(args.amp, eye[n], 0)] for n in range(N)])
    floor = np.abs(Ys[0, :, 0]) * args.amp
    H = Ys[1:, :, 0].T / args.amp  # M x N
    print("\n=== 1. Sounding: level dBFS (SNR dB) ===")
    print(f"{'':>9}" + "".join(f"{'TX ch'+str(c):>16}" for c in txc))
    for r in range(M):
        cells = []
        for n in range(N):
            lvl = 20 * np.log10(np.abs(H[r, n]) * args.amp)
            cells.append(f"{lvl:6.1f} ({lvl - 20*np.log10(max(floor[r], 1e-12)):4.1f})")
        print(f"RX {names[r]} ch{rxc[r]:<2}" + "".join(f"{c:>16}" for c in cells))
    if M > 1:
        print(f"Condition number of H: {np.linalg.cond(H):.1f}")

    # ---------------- 2. Weight matrix W (N x K) ----------------
    if args.mode == "angles":
        psi_a = 2 * np.pi * d * np.sin(np.deg2rad(args.theta_a)) / lam
        c = np.exp(-1j * np.angle(H[0] / H[0, 0])) * np.exp(1j * k_idx * psi_a)
        W = np.stack([c * np.exp(-1j * k_idx * 2 * np.pi * d * np.sin(np.deg2rad(th)) / lam)
                      for th in args.beams], axis=1)
        labels = [f"{th:+.0f}°" for th in args.beams]
    elif args.mode == "mrt":
        W = np.stack([np.exp(-1j * np.angle(H[r])) for r in range(M)], axis=1)
        labels = [f"->{n}" for n in names]
    else:
        W = np.linalg.pinv(H)
        W = W / np.max(np.abs(W), axis=0, keepdims=True)
        labels = [f"->{n}" for n in names]

    print("\nBeam weights (phase in deg, |w|):")
    for k in range(K):
        print(f"  beam {k} ({labels[k]:>5}, {tones[k]/1e3:.0f} kHz): " +
              "  ".join(f"{np.rad2deg(np.angle(W[n, k])):+6.1f}/{np.abs(W[n, k]):.2f}"
                        for n in range(N)))

    # ---------------- 3. All beams together + each alone ----------------
    beam_amp = args.amp / K  # keeps the sum of all beams within --amp per channel
    together = [(beam_amp, W[:, k], k) for k in range(K)]
    Yb = measure([together] + [[(beam_amp, W[:, k], k)] for k in range(K)])
    G_sim = Yb[0] / beam_amp                                     # M x K
    G_alone = np.stack([Yb[1 + k, :, k] for k in range(K)], axis=1) / beam_amp
    G_pred = H @ W

    ref = np.mean(np.abs(H) ** 2, axis=1, keepdims=True)  # single-element power per RX
    P_sim = db10(np.abs(G_sim) ** 2 / ref)
    P_pred = db10(np.abs(G_pred) ** 2 / ref)
    P_alone = db10(np.abs(G_alone) ** 2 / ref)

    hdr = f"{'':>9}" + "".join(f"{'beam '+l:>12}" for l in labels)
    for title, P in (("Measured, all beams simultaneous", P_sim),
                     ("Predicted from H @ W", P_pred)):
        print(f"\n=== {title} (dB vs single element at each RX) ===")
        print(hdr)
        for r in range(M):
            print(f"RX {names[r]} ch{rxc[r]:<2}" + "".join(f"{P[r, k]:>+12.1f}" for k in range(K)))
    print(f"\nSuperposition check (simultaneous vs alone): "
          f"max |diff| = {np.max(np.abs(P_sim - P_alone)):.2f} dB "
          f"on links above -10 dB: "
          f"{np.max(np.abs((P_sim - P_alone)[P_sim > -10])) if np.any(P_sim > -10) else 0:.2f} dB")

    print("\n=== Summary ===")
    if args.mode == "angles":
        for r in range(M):
            order = np.argsort(P_sim[r])[::-1]
            margin = P_sim[r, order[0]] - P_sim[r, order[1]] if K > 1 else np.inf
            print(f"RX {names[r]}: strongest beam {labels[order[0]]} "
                  f"({P_sim[r, order[0]]:+.1f} dB), margin over next {margin:.1f} dB")
    else:
        for r in range(M):
            s = np.abs(G_sim[r, r]) ** 2
            i = np.sum(np.abs(G_sim[r]) ** 2) - s
            print(f"RX {names[r]}: own beam {P_sim[r, r]:+.1f} dB, "
                  f"SIR = {10*np.log10(s / max(i, 1e-15)):+.1f} dB")

    np.savez(args.save, freq=args.freq, spacing=d, mode=args.mode, H=H, W=W,
             tones=np.array(tones), labels=np.array(labels),
             G_sim=G_sim, G_alone=G_alone, G_pred=G_pred)
    print(f"Saved {args.save}")

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(1.6 * K + 3, 1.0 * M + 2.5))
        vmax = np.max(P_sim)
        im = ax.imshow(P_sim, cmap="viridis", vmin=vmax - 35, vmax=vmax)
        for r in range(M):
            for k in range(K):
                ax.text(k, r, f"{P_sim[r, k]:+.1f}", ha="center", va="center",
                        color="w" if P_sim[r, k] < vmax - 15 else "k", fontsize=10)
        ax.set_xticks(range(K), [f"beam {l}\n{tones[k]/1e3:.0f} kHz" for k, l in enumerate(labels)])
        ax.set_yticks(range(M), [f"RX {n} (ch{c})" for n, c in zip(names, rxc)])
        fig.colorbar(im, ax=ax, label="dB vs single element")
        ax.set_title(f"Multi-beam '{args.mode}', all beams simultaneous @ {args.freq/1e9:.3f} GHz")
        fig.tight_layout()
        fig.savefig(args.plot, dpi=120)
        print(f"Saved {args.plot}")
    except ImportError:
        print("matplotlib not available: skipping plot")

    # ---------------- 4. Optional: keep all beams on ----------------
    if args.hold:
        n_loop = n_grid * int(np.ceil(20000 / n_grid))
        idx = np.arange(n_loop)
        buf = np.zeros((N, n_loop), dtype=np.complex64)
        for k in range(K):
            buf += (beam_amp * W[:, k][:, None] *
                    np.exp(2j * np.pi * tones[k] * idx / rate)[None, :]).astype(np.complex64)
        buf = np.ascontiguousarray(buf)
        stop = threading.Event()
        signal.signal(signal.SIGINT, lambda *_: stop.set())
        md = uhd.types.TXMetadata()
        md.start_of_burst = True
        md.has_time_spec = True
        md.time_spec = uhd.types.TimeSpec(usrp.get_time_now().get_real_secs() + 0.3)
        print(f"\nHolding {K} beams: " +
              ", ".join(f"{l} @ {(args.freq + t)/1e6:.3f} MHz" for l, t in zip(labels, tones)) +
              ". Ctrl+C to stop.")
        while not stop.is_set():
            tx.send(buf, md, 1.0)
            md.start_of_burst = False
            md.has_time_spec = False
        md.end_of_burst = True
        tx.send(np.zeros((N, 100), dtype=np.complex64), md, 1.0)
        time.sleep(0.3)
        print("Stopped.")


if __name__ == "__main__":
    main()
