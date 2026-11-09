#!/usr/bin/env python3
"""
Step 5: Verify beam steering to real angles (and the sign convention)
on the X410 4-element ULA.

Flow (one session, the calibration stays valid while the device stays tuned):
  1. RX antenna at broadside (0 deg): calibrate.
  2. Scan the calibrated beam: the peak should be at 0 deg.
  3. Move the RX antenna to a known angle on an arc (same distance from the
     array center), type the angle, and the script scans the beam and
     reports where the peak is. It should match the true angle.

Geometry / sign convention used by steer():
  ch0 at x = 0, ch1 at x = d, ch2 at x = 2d, ch3 at x = 3d.
  theta is measured from broadside; POSITIVE theta points toward the ch3 end.
  If the measured peaks come out mirrored, the physical order is reversed.

Requires bf_cal_scan.py in the same folder (reuses its helpers).

Example:
  python3 steer_verify.py
  python3 steer_verify.py --no-amp-eq --spacing 0.061
"""
import argparse

import numpy as np
import uhd

from bf_cal_scan import C, TX_CHANS, measure_segments, run_burst, setup_usrp


def parse_args():
    p = argparse.ArgumentParser(description="X410 ULA: verify steering to real angles")
    p.add_argument("--args", default="addr=192.168.10.2")
    p.add_argument("--freq", type=float, default=2.45e9)
    p.add_argument("--rate", type=float, default=1.92e6)
    p.add_argument("--tone", type=float, default=60e3)
    p.add_argument("--amp", type=float, default=0.25)
    p.add_argument("--tx-gain", type=float, default=10.0)
    p.add_argument("--rx-gain", type=float, default=20.0)
    p.add_argument("--rx-chan", type=int, default=0)
    p.add_argument("--rx-ant", default="RX1")
    p.add_argument("--spacing", type=float, default=None, help="Element spacing [m]")
    p.add_argument("--seg-ms", type=float, default=5.0, help="Segment length per angle [ms]")
    p.add_argument("--scan-min", type=float, default=-80.0)
    p.add_argument("--scan-max", type=float, default=80.0)
    p.add_argument("--scan-step", type=float, default=1.0)
    p.add_argument("--no-amp-eq", action="store_true")
    p.add_argument("--save", default="steer_verify.npz")
    p.add_argument("--plot", default="steer_verify.png")
    return p.parse_args()


def main():
    args = parse_args()
    lam = C / args.freq
    d = args.spacing if args.spacing else lam / 2
    print(f"lambda = {lam*100:.2f} cm, d = {d*100:.2f} cm (d/lambda = {d/lam:.3f})")

    usrp = setup_usrp(args)
    rate = usrp.get_tx_rate(0)
    tx_args = uhd.usrp.StreamArgs("fc32", "sc16")
    tx_args.channels = TX_CHANS
    tx = usrp.get_tx_stream(tx_args)
    rx_args = uhd.usrp.StreamArgs("fc32", "sc16")
    rx_args.channels = [args.rx_chan]
    rx = usrp.get_rx_stream(rx_args)

    n_per = int(round(rate / args.tone))
    tone = rate / n_per
    seg_len = max(1, int(round(args.seg_ms * 1e-3 * rate / n_per))) * n_per
    k_idx = np.arange(len(TX_CHANS))
    angles = np.arange(args.scan_min, args.scan_max + args.scan_step / 2, args.scan_step)

    def ref_tone(n_samps):
        n = np.arange(n_samps)
        return np.exp(2j * np.pi * tone * n / rate).astype(np.complex64)

    def steer(theta_deg):
        psi = 2 * np.pi * d * np.sin(np.deg2rad(theta_deg)) / lam
        return np.exp(-1j * k_idx * psi)

    # ---------------- Calibration (RX must be at 0 deg) ----------------
    def calibrate():
        nseg = 1 + len(TX_CHANS)
        s = ref_tone(nseg * seg_len)
        buf = np.zeros((len(TX_CHANS), nseg * seg_len), dtype=np.complex64)
        for k in k_idx:
            sl = slice((k + 1) * seg_len, (k + 2) * seg_len)
            buf[k, sl] = args.amp * s[sl]
        out, e = run_burst(usrp, tx, rx, buf)
        if e:
            raise RuntimeError(f"Calibration burst failed: {e}")
        h_all = measure_segments(out, s, seg_len, nseg) / args.amp
        floor = np.abs(h_all[0]) * args.amp
        h = h_all[1:]
        print("\nCalibration:")
        for k in k_idx:
            snr = 20 * np.log10(np.abs(h[k]) * args.amp / max(floor, 1e-12))
            rel = 20 * np.log10(np.abs(h[k]) / np.abs(h[0]))
            print(f"  ch{k}: rel amp {rel:+6.2f} dB   SNR {snr:5.1f} dB")
        if np.min(20 * np.log10(np.abs(h) * args.amp / max(floor, 1e-12))) < 20:
            print("  [WARN] SNR < 20 dB on some channel")
        c = np.exp(-1j * np.angle(h / h[0]))
        if not args.no_amp_eq:
            c *= np.min(np.abs(h)) / np.abs(h)
        return c

    # ---------------- Calibrated beam scan ----------------
    def scan(c):
        n_ang = len(angles)
        s = ref_tone(n_ang * seg_len)
        buf = np.empty((len(TX_CHANS), n_ang * seg_len), dtype=np.complex64)
        for i, th in enumerate(angles):
            sl = slice(i * seg_len, (i + 1) * seg_len)
            buf[:, sl] = (args.amp * (c * steer(th))[:, None] * s[None, sl]).astype(np.complex64)
        out, e = run_burst(usrp, tx, rx, buf)
        if e:
            raise RuntimeError(f"Scan failed: {e}")
        return np.abs(measure_segments(out, s, seg_len, n_ang) / args.amp) ** 2

    def analyze(p):
        p_db = 10 * np.log10(p / p.max() + 1e-15)
        i = int(np.argmax(p))
        delta = 0.0
        if 0 < i < len(angles) - 1:  # parabolic interpolation of the peak
            y0, y1, y2 = p_db[i - 1:i + 2]
            den = y0 - 2 * y1 + y2
            if den != 0:
                delta = 0.5 * (y0 - y2) / den
        est = angles[i] + delta * args.scan_step
        lo, hi = i, i  # -3 dB beamwidth around the peak
        while lo > 0 and p_db[lo - 1] >= -3:
            lo -= 1
        while hi < len(angles) - 1 and p_db[hi + 1] >= -3:
            hi += 1
        return est, angles[hi] - angles[lo], p_db

    c = calibrate()
    p0 = scan(c)
    est0, bw0, _ = analyze(p0)
    ref_power = p0.max()
    print(f"\nReference scan (RX at 0 deg): peak at {est0:+.1f} deg, -3 dB width {bw0:.0f} deg")
    bw_theory = np.rad2deg(2 * np.arcsin(min(1.0, 0.886 * lam / (len(TX_CHANS) * d))))
    print(f"(theoretical -3 dB width at broadside: ~{bw_theory:.0f} deg)")

    true_list, est_list, pats = [0.0], [est0], [p0]

    print("\nPlace the RX antenna on an arc at the SAME distance from the array center.")
    print("Positive angles = toward the ch3 end of the array.")
    while True:
        try:
            s_in = input("\nMove the RX, then type its true angle in deg "
                         "(Enter = unknown, 'c' = recalibrate with RX at 0, 'q' = quit): ")
        except EOFError:
            break
        s_in = s_in.strip().lower()
        if s_in == "q":
            break
        if s_in == "c":
            c = calibrate()
            continue
        true = None
        if s_in:
            try:
                true = float(s_in)
            except ValueError:
                print("Not a number, try again")
                continue

        p = scan(c)
        est, bw, _ = analyze(p)
        rel = 10 * np.log10(p.max() / ref_power)
        print(f"Beam peak at {est:+.1f} deg | -3 dB width {bw:.0f} deg | "
              f"peak power {rel:+.1f} dB vs reference")
        if true is not None:
            err = est - true
            print(f"True angle {true:+.1f} deg -> error {err:+.1f} deg")
            if abs(true) >= 5 and abs(est + true) < abs(err):
                print("[NOTE] Peak is on the OPPOSITE side: positive angles actually point "
                      "toward ch0 (the physical element order is reversed)")
        true_list.append(np.nan if true is None else true)
        est_list.append(est)
        pats.append(p)

    np.savez(args.save, angles=angles, true_deg=np.array(true_list),
             est_deg=np.array(est_list), patterns=np.array(pats),
             freq=args.freq, spacing=d)
    print(f"\nSaved {args.save}")
    print(f"{'true[deg]':>10} {'est[deg]':>9} {'err[deg]':>9}")
    for t, e in zip(true_list, est_list):
        err = "" if np.isnan(t) else f"{e - t:+9.1f}"
        tt = "?" if np.isnan(t) else f"{t:+.1f}"
        print(f"{tt:>10} {e:>+9.1f} {err:>9}")

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(9, 5))
        for t, e, p in zip(true_list, est_list, pats):
            lbl = f"RX at {'?' if np.isnan(t) else f'{t:+.0f}'}° (peak {e:+.1f}°)"
            ax.plot(angles, 10 * np.log10(p / ref_power + 1e-15), label=lbl)
            if not np.isnan(t):
                ax.axvline(t, color="gray", ls=":", lw=0.8)
        ax.set_xlabel("Steering angle [deg]  (+ = toward ch3)")
        ax.set_ylabel("Received power [dB vs reference peak]")
        ax.set_ylim(-40, 5)
        ax.grid(True, alpha=0.3)
        ax.legend(fontsize=8)
        ax.set_title(f"Steering verification @ {args.freq/1e9:.3f} GHz, d/λ = {d/lam:.2f}")
        fig.tight_layout()
        fig.savefig(args.plot, dpi=120)
        print(f"Saved {args.plot}")
    except ImportError:
        print("matplotlib not available: skipping plot")


if __name__ == "__main__":
    main()
