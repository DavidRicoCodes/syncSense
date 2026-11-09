#!/usr/bin/env python3
"""
Step 6: 2 TX + 2 RX beamforming test on a USRP X410 (no antenna moves needed).

  TX: 2-element array on TX/RX0 of --tx-chans (default ch0, ch1), spacing d.
  RX: two fixed receive antennas on RX1 of --rx-chans (default ch2 = "A",
      ch3 = "B"), at different positions/angles, 1.5-3 m from the array.

Everything runs in ONE session (ZBX phases are random per tune):
  1. Sounding: each TX alone -> 2x2 channel matrix H (rows RX A/B, cols TX).
  2. Angle estimate of RX B relative to RX A from the phase of H
     (random LO phases cancel in the ratio; needs line of sight).
  3. Beamforming tests, measured at both RX simultaneously:
       - no correction [1, 1]
       - MRT toward A, MRT toward B (phase-only, max power at the target)
       - Zero-forcing: max at A with a null on B, and vice versa
     Measured results are compared with the prediction H @ w.
  4. Beam scan calibrated toward A, measured at both RX: A should peak at
     its angle (--theta-a) and B at its own angle.

Sign convention: the element on the SECOND --tx-chans entry is at x = +d;
positive angles point toward that element's side.

Example:
  python3 bf_2x2.py
  python3 bf_2x2.py --tx-gain 20 --rx-gain 30 --theta-a 0
"""
import argparse
import threading
import time

import numpy as np
import uhd

C = 299792458.0


def parse_args():
    p = argparse.ArgumentParser(description="X410 2 TX + 2 RX beamforming test")
    p.add_argument("--args", default="addr=192.168.10.2")
    p.add_argument("--freq", type=float, default=2.45e9)
    p.add_argument("--rate", type=float, default=1.92e6)
    p.add_argument("--tone", type=float, default=60e3)
    p.add_argument("--amp", type=float, default=0.25, help="Digital amplitude per TX channel")
    p.add_argument("--tx-gain", type=float, default=10.0)
    p.add_argument("--rx-gain", type=float, default=20.0)
    p.add_argument("--tx-chans", type=int, nargs=2, default=[0, 1])
    p.add_argument("--rx-chans", type=int, nargs=2, default=[2, 3], help="RX A, RX B")
    p.add_argument("--rx-ant", default="RX1")
    p.add_argument("--spacing", type=float, default=None, help="TX element spacing [m]")
    p.add_argument("--theta-a", type=float, default=0.0,
                   help="Known angle of RX A from broadside [deg]")
    p.add_argument("--seg-ms", type=float, default=10.0)
    p.add_argument("--scan-step", type=float, default=2.0)
    p.add_argument("--no-scan", action="store_true")
    p.add_argument("--save", default="bf_2x2.npz")
    p.add_argument("--plot", default="bf_2x2.png")
    return p.parse_args()


def setup_usrp(args):
    usrp = uhd.usrp.MultiUSRP(args.args)
    tune = uhd.types.TuneRequest(args.freq)
    # Untimed tuning, no set_time_now(), no clock/time source changes (see step 2)
    for ch in args.tx_chans:
        usrp.set_tx_rate(args.rate, ch)
        usrp.set_tx_antenna("TX/RX0", ch)
        usrp.set_tx_gain(args.tx_gain, ch)
        usrp.set_tx_freq(tune, ch)
    for ch in args.rx_chans:
        usrp.set_rx_rate(args.rate, ch)
        usrp.set_rx_antenna(args.rx_ant, ch)
        usrp.set_rx_gain(args.rx_gain, ch)
        usrp.set_rx_freq(tune, ch)
    time.sleep(0.5)

    for kind, chans, get_f, get_s in (
            ("TX", args.tx_chans, usrp.get_tx_freq, usrp.get_tx_sensor),
            ("RX", args.rx_chans, usrp.get_rx_freq, usrp.get_rx_sensor)):
        for ch in chans:
            f = get_f(ch)
            locked = get_s("lo_locked", ch).to_bool()
            print(f"{kind} ch{ch}: freq={f/1e6:.6f} MHz  LO locked={locked}")
            if not locked or abs(f - args.freq) > 1e3:
                raise RuntimeError(f"{kind} ch{ch} not locked or mistuned; restart usrp-hwd")
    return usrp


def run_burst(usrp, tx, rx, txbuf, n_rx, lead=0.4):
    """Timed TX burst (n_tx x N) with a simultaneous N-sample capture on all RX channels."""
    n_total = txbuf.shape[1]
    t0 = usrp.get_time_now().get_real_secs() + lead
    rx_out = np.zeros((n_rx, n_total), dtype=np.complex64)
    errors = []

    def rx_worker():
        cmd = uhd.types.StreamCMD(uhd.types.StreamMode.num_done)
        cmd.num_samps = n_total
        cmd.stream_now = False
        cmd.time_spec = uhd.types.TimeSpec(t0)
        rx.issue_stream_cmd(cmd)
        spp = rx.get_max_num_samps()
        tmp = np.zeros((n_rx, spp), dtype=np.complex64)
        md = uhd.types.RXMetadata()
        got = 0
        timeout = lead + 1.0
        while got < n_total:
            n = rx.recv(tmp, md, timeout)
            timeout = 0.5
            if md.error_code != uhd.types.RXMetadataErrorCode.none:
                errors.append(f"RX {md.error_code}")
                break
            k = min(n, n_total - got)
            rx_out[:, got:got + k] = tmp[:, :k]
            got += k

    th = threading.Thread(target=rx_worker)
    th.start()

    md = uhd.types.TXMetadata()
    md.start_of_burst = True
    md.has_time_spec = True
    md.time_spec = uhd.types.TimeSpec(t0)
    chunk = 20000
    for a in range(0, n_total, chunk):
        b = min(a + chunk, n_total)
        md.end_of_burst = (b == n_total)
        sent = tx.send(np.ascontiguousarray(txbuf[:, a:b]), md, 1.0)
        if sent != b - a:
            errors.append(f"TX send short ({sent}/{b - a})")
            break
        md.start_of_burst = False
        md.has_time_spec = False
    th.join()

    amd = uhd.types.TXAsyncMetadata()
    while tx.recv_async_msg(amd, 0.1):
        if amd.event_code != uhd.types.TXMetadataEventCode.burst_ack:
            errors.append(f"TX {amd.event_code} ch{amd.channel}")
    return rx_out, errors


def db10(x):
    return 10 * np.log10(np.maximum(x, 1e-15))


def parabolic_peak(angles, p_db):
    i = int(np.argmax(p_db))
    delta = 0.0
    if 0 < i < len(angles) - 1:
        y0, y1, y2 = p_db[i - 1:i + 2]
        den = y0 - 2 * y1 + y2
        if den != 0:
            delta = 0.5 * (y0 - y2) / den
    return angles[i] + delta * (angles[1] - angles[0])


def main():
    args = parse_args()
    lam = C / args.freq
    d = args.spacing if args.spacing else lam / 2
    txc, rxc = args.tx_chans, args.rx_chans
    print(f"lambda = {lam*100:.2f} cm, d = {d*100:.2f} cm (d/lambda = {d/lam:.3f})")
    print(f"TX array: ch{txc[0]} (x=0), ch{txc[1]} (x=+d).  RX A = ch{rxc[0]}, RX B = ch{rxc[1]}")

    usrp = setup_usrp(args)
    rate = usrp.get_tx_rate(txc[0])
    tx_args = uhd.usrp.StreamArgs("fc32", "sc16")
    tx_args.channels = txc
    tx = usrp.get_tx_stream(tx_args)
    rx_args = uhd.usrp.StreamArgs("fc32", "sc16")
    rx_args.channels = rxc
    rx = usrp.get_rx_stream(rx_args)

    n_per = int(round(rate / args.tone))
    tone = rate / n_per
    seg_len = max(1, int(round(args.seg_ms * 1e-3 * rate / n_per))) * n_per
    k_idx = np.arange(2)

    def ref_tone(n_samps):
        n = np.arange(n_samps)
        return np.exp(2j * np.pi * tone * n / rate).astype(np.complex64)

    def burst(weights):
        """One segment per 2-element weight vector. Returns Y (2 RX x nseg) = H @ w."""
        nseg = len(weights)
        s = ref_tone(nseg * seg_len)
        buf = np.zeros((2, nseg * seg_len), dtype=np.complex64)
        for i, w in enumerate(weights):
            sl = slice(i * seg_len, (i + 1) * seg_len)
            buf[:, sl] = (args.amp * np.asarray(w)[:, None] * s[None, sl]).astype(np.complex64)
        out, e = run_burst(usrp, tx, rx, buf, 2)
        if e:
            raise RuntimeError(f"Burst failed: {e}")
        pk = np.max(np.abs(out))
        if pk > 0.8:
            print(f"[WARN] RX peak {pk:.2f}: close to saturation, lower the gains")
        g = int(0.2 * seg_len)
        Y = np.zeros((2, nseg), dtype=np.complex128)
        for r in range(2):
            for i in range(nseg):
                a, b = i * seg_len + g, (i + 1) * seg_len - g
                Y[r, i] = np.mean(out[r, a:b] * np.conj(s[a:b]))
        return Y / args.amp

    # ---------------- 1. Sounding: H ----------------
    print(f"\n=== 1. Channel sounding (tone {tone/1e3:.2f} kHz) ===")
    Ys = burst([[0, 0], [1, 0], [0, 1]])
    floor = np.abs(Ys[:, 0]) * args.amp
    H = Ys[:, 1:3]  # rows: RX A, RX B ; cols: TX0, TX1
    print(f"{'':>10} {'TX ch'+str(txc[0]):>18} {'TX ch'+str(txc[1]):>18}")
    for r, name in enumerate(["A", "B"]):
        cells = []
        for k in range(2):
            lvl = 20 * np.log10(np.abs(H[r, k]) * args.amp)
            snr = lvl - 20 * np.log10(max(floor[r], 1e-12))
            cells.append(f"{lvl:6.1f} dBFS ({snr:4.1f})")
        print(f"RX {name} ch{rxc[r]:<3} {cells[0]:>18} {cells[1]:>18}")
    print("(values in parentheses: SNR in dB)")
    min_snr = np.min(20 * np.log10(np.abs(H) * args.amp / np.maximum(floor[:, None], 1e-12)))
    if min_snr < 20:
        print("[WARN] SNR < 20 dB on some link: raise --tx-gain / --rx-gain")

    # ---------------- 2. Angle estimate of RX B ----------------
    dphi = np.angle((H[1, 1] / H[1, 0]) / (H[0, 1] / H[0, 0]))
    sin_b = np.sin(np.deg2rad(args.theta_a)) + dphi * lam / (2 * np.pi * d)
    print(f"\n=== 2. Geometry from H ===")
    print(f"Phase difference B vs A across the array: {np.rad2deg(dphi):+.1f} deg")
    theta_b_est = None
    if abs(sin_b) <= 1:
        theta_b_est = np.rad2deg(np.arcsin(sin_b))
        print(f"Estimated angle of RX B: {theta_b_est:+.1f} deg "
              f"(RX A assumed at {args.theta_a:+.1f} deg; + = toward ch{txc[1]})")
    else:
        print(f"sin(theta_B) = {sin_b:.2f} is outside [-1, 1]: ambiguous (multipath or d > lambda/2)")

    # ---------------- 3. MRT / zero-forcing tests ----------------
    hA, hB = H[0], H[1]

    def unit_max(w):
        w = np.asarray(w, dtype=np.complex128)
        return w / np.max(np.abs(w))

    tests = [
        ("No correction [1,1]", np.array([1, 1], dtype=np.complex128)),
        ("MRT -> A", np.exp(-1j * np.angle(hA))),
        ("MRT -> B", np.exp(-1j * np.angle(hB))),
        ("ZF: max A, null B", unit_max([hB[1], -hB[0]])),
        ("ZF: max B, null A", unit_max([hA[1], -hA[0]])),
    ]
    print("\n=== 3. Beamforming tests (power relative to a single element at that RX) ===")
    Yt = burst([w for _, w in tests])
    ref = np.mean(np.abs(H) ** 2, axis=1)  # average single-element power at each RX
    print(f"{'test':<22} {'A meas':>7} {'A pred':>7} {'B meas':>7} {'B pred':>7} {'A-B':>7}")
    rows = []
    for i, (name, w) in enumerate(tests):
        pred = H @ w
        pa, pb = db10(np.abs(Yt[0, i]) ** 2 / ref[0]), db10(np.abs(Yt[1, i]) ** 2 / ref[1])
        qa, qb = db10(np.abs(pred[0]) ** 2 / ref[0]), db10(np.abs(pred[1]) ** 2 / ref[1])
        rows.append((pa, pb, qa, qb))
        print(f"{name:<22} {pa:>+7.1f} {qa:>+7.1f} {pb:>+7.1f} {qb:>+7.1f} {pa - pb:>+7.1f}")
    print("(MRT ideal: about +6 dB at the target; ZF: deep null at the other RX,"
          " limited by noise, drift and multipath)")
    iso_a = rows[3][0] - rows[3][1]
    iso_b = rows[4][1] - rows[4][0]
    print(f"Zero-forcing isolation: {iso_a:+.1f} dB (A over B), {iso_b:+.1f} dB (B over A)")

    results = dict(freq=args.freq, spacing=d, H=H, floor=floor, dphi=dphi,
                   test_names=np.array([n for n, _ in tests]),
                   test_w=np.array([w for _, w in tests]), Yt=Yt)

    # ---------------- 4. Beam scan calibrated toward A ----------------
    if not args.no_scan:
        angles = np.arange(-80, 80 + args.scan_step / 2, args.scan_step)
        psi_a = 2 * np.pi * d * np.sin(np.deg2rad(args.theta_a)) / lam
        c = np.exp(-1j * np.angle(hA / hA[0])) * np.exp(1j * k_idx * psi_a)  # remove A's geometry
        weights = []
        for th in angles:
            psi = 2 * np.pi * d * np.sin(np.deg2rad(th)) / lam
            weights.append(c * np.exp(-1j * k_idx * psi))
        print(f"\n=== 4. Beam scan calibrated toward A ({len(angles)} angles) ===")
        Ysc = burst(weights)
        pA = db10(np.abs(Ysc[0]) ** 2)
        pB = db10(np.abs(Ysc[1]) ** 2)
        pA -= pA.max()
        pB -= pB.max()
        peak_a = parabolic_peak(angles, pA)
        peak_b = parabolic_peak(angles, pB)
        print(f"RX A peak at {peak_a:+.1f} deg (expected {args.theta_a:+.1f})")
        exp_b = f"{theta_b_est:+.1f}" if theta_b_est is not None else "?"
        print(f"RX B peak at {peak_b:+.1f} deg (expected from H: {exp_b})")
        if theta_b_est is not None and abs(theta_b_est - args.theta_a) > 10:
            same_side = np.sign(peak_b - args.theta_a) == np.sign(theta_b_est - args.theta_a)
            print("Sign check:", "consistent" if same_side else "MISMATCH (check element order)")
        results.update(angles=angles, pA=pA, pB=pB, peak_a=peak_a, peak_b=peak_b)

        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
            fig, ax = plt.subplots(figsize=(8, 4.5))
            ax.plot(angles, pA, "o-", ms=3, label=f"RX A (ch{rxc[0]}), peak {peak_a:+.1f}°")
            ax.plot(angles, pB, "s-", ms=3, label=f"RX B (ch{rxc[1]}), peak {peak_b:+.1f}°")
            ax.axvline(args.theta_a, color="C0", ls=":", lw=1)
            if theta_b_est is not None:
                ax.axvline(theta_b_est, color="C1", ls=":", lw=1, label="RX B angle from H")
            ax.set_xlabel(f"Steering angle [deg]  (+ = toward ch{txc[1]})")
            ax.set_ylabel("Received power [dB, each RX normalized]")
            ax.set_ylim(-30, 3)
            ax.grid(True, alpha=0.3)
            ax.legend(fontsize=8)
            ax.set_title(f"X410 2 TX + 2 RX @ {args.freq/1e9:.3f} GHz, d/λ = {d/lam:.2f}")
            fig.tight_layout()
            fig.savefig(args.plot, dpi=120)
            print(f"Saved {args.plot}")
        except ImportError:
            print("matplotlib not available: skipping plot")

    np.savez(args.save, **results)
    print(f"Saved {args.save}")


if __name__ == "__main__":
    main()
