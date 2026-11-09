#!/usr/bin/env python3
"""
Where are the beams pointing?  (post-processing, no hardware needed)

Loads .npz results from bf_multibeam.py (H, W) or bf_4x4.py / bf_nxm.py
(H, test_w) and computes the real radiation pattern of every beam:

  1. Element responses t_n from the row of H measured at RX A, removing the
     geometric phase of RX A (assumed at --theta-a with line of sight):
         H[A, n] = g_A * t_n * exp(j n psi_A)  ->  t_n
  2. Pattern of beam k:  AF_k(theta) = | sum_n t_n W[n, k] exp(j n psi(theta)) |^2
     with psi = 2 pi d sin(theta) / lambda  (+theta toward the last TX element)
  3. Main-lobe direction, -3 dB width, gain vs a single element, nulls.
  4. RX angles estimated from H are marked, and the power the model predicts
     at each RX is compared with the measured one (large differences =
     multipath: the real pattern differs from the free-space one there).

Examples:
  python3 beam_pointing.py mb_geom.npz
  python3 beam_pointing.py bf_multibeam.npz mb_zf.npz bf_4x4.npz
  python3 beam_pointing.py mb_geom.npz --theta-a 5
"""
import argparse

import numpy as np

C = 299792458.0


def parse_args():
    p = argparse.ArgumentParser(description="Compute where each beam points from saved H and W")
    p.add_argument("files", nargs="+", help=".npz files from bf_multibeam.py or bf_4x4.py")
    p.add_argument("--theta-a", type=float, default=0.0, help="Angle of RX A [deg]")
    p.add_argument("--suffix", default="_pointing.png", help="Plot file suffix")
    return p.parse_args()


def rx_angles(H, d, lam, theta_a):
    """Angle of each RX from the phase slope across the array (RX A = reference)."""
    n = np.arange(H.shape[1])
    sin_a = np.sin(np.deg2rad(theta_a))
    out = [(theta_a, 0.0)]
    for r in range(1, H.shape[0]):
        ratio = H[r] * np.conj(H[0])
        ratio = ratio * np.conj(ratio[0])
        ph = np.unwrap(np.angle(ratio))
        slope = np.sum(n * ph) / np.sum(n ** 2)
        resid = np.rad2deg(np.sqrt(np.mean((ph - slope * n) ** 2)))
        s = sin_a + slope * lam / (2 * np.pi * d)
        out.append((np.rad2deg(np.arcsin(s)) if abs(s) <= 1 else None, resid))
    return out


def lobe_info(theta, p_db):
    i = int(np.argmax(p_db))
    delta = 0.0
    if 0 < i < len(theta) - 1:
        y0, y1, y2 = p_db[i - 1:i + 2]
        den = y0 - 2 * y1 + y2
        if den != 0:
            delta = 0.5 * (y0 - y2) / den
    peak = theta[i] + delta * (theta[1] - theta[0])
    lo, hi = i, i
    while lo > 0 and p_db[lo - 1] >= p_db[i] - 3:
        lo -= 1
    while hi < len(theta) - 1 and p_db[hi + 1] >= p_db[i] - 3:
        hi += 1
    rel = p_db - p_db[i]
    nulls = [theta[j] for j in range(1, len(theta) - 1)
             if rel[j] < -15 and rel[j] <= rel[j - 1] and rel[j] <= rel[j + 1]]
    return peak, theta[hi] - theta[lo], p_db[i], nulls


def process(path, theta_a, suffix):
    z = np.load(path, allow_pickle=True)
    H = z["H"]
    d = float(z["spacing"])
    lam = C / float(z["freq"])
    if "W" in z.files:
        W = z["W"]
        labels = [str(x) for x in z["labels"]]
        G = z["G_sim"]
    elif "test_w" in z.files:
        W = z["test_w"].T
        labels = [str(x) for x in z["test_names"]]
        G = z["Yt"]
    else:
        print(f"{path}: no weights found, skipping")
        return
    M, N = H.shape
    K = W.shape[1]
    names = [chr(ord("A") + i) for i in range(M)]
    n = np.arange(N)

    # Element responses (hardware offsets), referenced to RX A
    psi_a = 2 * np.pi * d * np.sin(np.deg2rad(theta_a)) / lam
    t = H[0] * np.exp(-1j * n * psi_a)
    t = t / np.sqrt(np.mean(np.abs(t) ** 2))  # single element = 0 dB

    theta = np.linspace(-90, 90, 721)
    A = np.exp(1j * np.outer(2 * np.pi * d * np.sin(np.deg2rad(theta)) / lam, n))
    AF = np.abs(A @ (t[:, None] * W)) ** 2          # (angles x beams), vs single element
    AF_db = 10 * np.log10(np.maximum(AF, 1e-12))

    rx = rx_angles(H, d, lam, theta_a)

    print(f"\n==================== {path} ====================")
    print(f"{N} TX, d/lambda = {d/lam:.2f}, RX A assumed at {theta_a:+.1f} deg "
          f"(+ = toward the last TX element)")
    print("RX angles from H: " + ", ".join(
        f"{names[r]} {'?' if a is None else f'{a:+.1f}'}°"
        + ("" if r == 0 else f" (resid {res:.0f}°)") for r, (a, res) in enumerate(rx)))
    print(f"\n{'beam':<16} {'points to':>10} {'-3dB width':>11} {'gain':>7}   nulls")
    for k in range(K):
        peak, bw, g, nulls = lobe_info(theta, AF_db[:, k])
        near = [names[r] for r, (a, _) in enumerate(rx) if a is not None and abs(a - peak) <= bw / 2]
        tag = f"  (on RX {', '.join(near)})" if near else ""
        print(f"{labels[k]:<16} {peak:>+9.1f}° {bw:>10.0f}° {g:>+6.1f} dB   "
              + (", ".join(f"{x:+.0f}°" for x in nulls[:6]) or "-") + tag)
    print(f"(ideal gain for {N} elements with unit weights: {20*np.log10(N):+.1f} dB)")

    # Model vs measurement at each RX
    ref = np.mean(np.abs(H) ** 2, axis=1)
    P_meas = 10 * np.log10(np.maximum(np.abs(G) ** 2 / ref[:, None], 1e-12))
    print("\nPower at each RX, dB vs single element:  measured / free-space model")
    print(f"{'':>6}" + "".join(f"{l[:14]:>17}" for l in labels))
    for r, (a, _) in enumerate(rx):
        row = f"RX {names[r]:<3}"
        for k in range(K):
            if a is None:
                row += f"{P_meas[r, k]:>+9.1f} /   ?   "
            else:
                j = int(np.argmin(np.abs(theta - a)))
                row += f"{P_meas[r, k]:>+9.1f} / {AF_db[j, k]:>+5.1f} "
        print(row)
    print("(RX A agrees by construction; big gaps on other RX = multipath / no line of sight)")

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig = plt.figure(figsize=(13, 5.2))
        ax1 = fig.add_subplot(1, 2, 1)
        ax2 = fig.add_subplot(1, 2, 2, projection="polar")
        top = np.ceil(AF_db.max() / 3) * 3 + 1
        floor = top - 30
        for k in range(K):
            peak = lobe_info(theta, AF_db[:, k])[0]
            lbl = f"{labels[k]} -> {peak:+.1f}°"
            ax1.plot(theta, AF_db[:, k], color=f"C{k}", label=lbl)
            ax2.plot(np.deg2rad(theta), np.clip(AF_db[:, k], floor, None) - floor,
                     color=f"C{k}", label=lbl)
        for r, (a, _) in enumerate(rx):
            if a is None:
                continue
            ax1.axvline(a, color="gray", ls="--", lw=0.8)
            ax1.text(a, top - 1.5, f"RX {names[r]}", rotation=90, va="top", ha="right", fontsize=8)
            ax2.plot([np.deg2rad(a)] * 2, [0, top - floor], color="gray", ls="--", lw=0.8)
            ax2.text(np.deg2rad(a), top - floor + 1.5, names[r], ha="center", fontsize=9)
        ax1.set_xlim(-90, 90)
        ax1.set_ylim(floor, top)
        ax1.set_xlabel("Angle [deg]  (+ = toward last TX element)")
        ax1.set_ylabel("Gain vs single element [dB]")
        ax1.grid(True, alpha=0.3)
        ax1.legend(fontsize=8, loc="lower center")
        ax2.set_theta_zero_location("N")
        ax2.set_theta_direction(-1)
        ax2.set_thetamin(-90)
        ax2.set_thetamax(90)
        ticks = np.arange(0, 31, 10)
        ax2.set_rticks(ticks)
        ax2.set_yticklabels([f"{floor + x:.0f}" for x in ticks], fontsize=7)
        ax2.set_title("Polar view (array at the center, broadside up)", fontsize=10)
        fig.suptitle(f"Beam pointing: {path}")
        fig.tight_layout()
        out = path.replace(".npz", "") + suffix
        fig.savefig(out, dpi=120)
        print(f"Saved {out}")
    except ImportError:
        print("matplotlib not available: skipping plot")


def main():
    args = parse_args()
    for f in args.files:
        process(f, args.theta_a, args.suffix)


if __name__ == "__main__":
    main()
