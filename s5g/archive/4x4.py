#!/usr/bin/env python3
"""
Step 7: N TX x M RX beamforming test on a USRP X410 (default 4 x 4).

  TX: linear array on TX/RX0 of --tx-chans (default ch0..ch3), spacing d.
      Element i of --tx-chans is at x = i*d.
  RX: fixed receive antennas on RX1 of --rx-chans (default ch0..ch3),
      named A, B, C, D in that order, at different positions/angles,
      1.5-3 m from the array. No antenna has to move.

Also works as 3x3:  --tx-chans 0 1 2 --rx-chans 0 1 2
          or 2x2:   --tx-chans 0 1 --rx-chans 2 3

Everything runs in ONE session (ZBX phases are random per tune):
  1. Sounding: each TX alone -> channel matrix H (M RX x N TX).
  2. Angle of each RX relative to RX A, from a linear fit of the phase
     across the array (random LO phases cancel; needs line of sight).
  3. Beamforming tests, measured at all RX simultaneously:
       - no correction (all ones)
       - MRT toward each RX (phase-only)
       - Zero-forcing toward each RX: max there, nulls on all the others
         (columns of pinv(H))
     Measured results are compared with the prediction H @ w.
  4. Beam scan calibrated toward A, measured at all RX: each RX should
     peak at its own angle.

Sign convention: positive angles point toward the LAST --tx-chans element.

Examples:
  python3 bf_4x4.py
  python3 bf_4x4.py --tx-gain 20 --rx-gain 30 --theta-a 0
  python3 bf_4x4.py --tx-chans 0 1 2 --rx-chans 0 1 2
"""
import argparse
import threading
import time

import numpy as np
import uhd

C = 299792458.0


def parse_args():
    p = argparse.ArgumentParser(description="X410 N TX x M RX beamforming test")
    p.add_argument("--args", default="addr=192.168.10.2")
    p.add_argument("--freq", type=float, default=2.45e9)
    p.add_argument("--rate", type=float, default=1.92e6)
    p.add_argument("--tone", type=float, default=60e3)
    p.add_argument("--amp", type=float, default=0.25, help="Digital amplitude per TX channel")
    p.add_argument("--tx-gain", type=float, default=10.0)
    p.add_argument("--rx-gain", type=float, default=20.0)
    p.add_argument("--tx-chans", type=int, nargs="+", default=[0, 1, 2, 3])
    p.add_argument("--rx-chans", type=int, nargs="+", default=[0, 1, 2, 3],
                   help="RX A, B, C, D ...")
    p.add_argument("--rx-ant", default="RX1")
    p.add_argument("--spacing", type=float, default=None, help="TX element spacing [m]")
    p.add_argument("--theta-a", type=float, default=0.0,
                   help="Known angle of RX A from broadside [deg]")
    p.add_argument("--seg-ms", type=float, default=10.0)
    p.add_argument("--scan-step", type=float, default=2.0)
    p.add_argument("--no-scan", action="store_true")
    p.add_argument("--save", default="bf_4x4.npz")
    p.add_argument("--plot", default="bf_4x4.png")
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
    N, M = len(txc), len(rxc)
    if N < 2 or M < 1:
        raise SystemExit("Need at least 2 TX channels and 1 RX channel")
    names = [chr(ord("A") + i) for i in range(M)]
    print(f"{N} TX x {M} RX | lambda = {lam*100:.2f} cm, d = {d*100:.2f} cm (d/lambda = {d/lam:.3f})")
    print("TX array: " + ", ".join(f"ch{c} (x={i}d)" for i, c in enumerate(txc)))
    print("RX: " + ", ".join(f"{n} = ch{c}" for n, c in zip(names, rxc)))

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
    k_idx = np.arange(N)

    def ref_tone(n_samps):
        n = np.arange(n_samps)
        return np.exp(2j * np.pi * tone * n / rate).astype(np.complex64)

    def burst(weights):
        """One segment per N-element weight vector. Returns Y (M x nseg) = H @ w."""
        nseg = len(weights)
        s = ref_tone(nseg * seg_len)
        buf = np.zeros((N, nseg * seg_len), dtype=np.complex64)
        for i, w in enumerate(weights):
            sl = slice(i * seg_len, (i + 1) * seg_len)
            buf[:, sl] = (args.amp * np.asarray(w)[:, None] * s[None, sl]).astype(np.complex64)
        out, e = run_burst(usrp, tx, rx, buf, M)
        if e:
            raise RuntimeError(f"Burst failed: {e}")
        pk = np.max(np.abs(out))
        if pk > 0.8:
            print(f"[WARN] RX peak {pk:.2f}: close to saturation, lower the gains")
        g = int(0.2 * seg_len)
        Y = np.zeros((M, nseg), dtype=np.complex128)
        for r in range(M):
            for i in range(nseg):
                a, b = i * seg_len + g, (i + 1) * seg_len - g
                Y[r, i] = np.mean(out[r, a:b] * np.conj(s[a:b]))
        return Y / args.amp

    # ---------------- 1. Sounding: H ----------------
    print(f"\n=== 1. Channel sounding (tone {tone/1e3:.2f} kHz) ===")
    Ys = burst([np.zeros(N)] + [np.eye(N)[k] for k in range(N)])
    floor = np.abs(Ys[:, 0]) * args.amp
    H = Ys[:, 1:]  # M x N
    print("Level in dBFS (SNR in dB):")
    print(f"{'':>9}" + "".join(f"{'TX ch'+str(c):>17}" for c in txc))
    for r in range(M):
        cells = []
        for k in range(N):
            lvl = 20 * np.log10(np.abs(H[r, k]) * args.amp)
            snr = lvl - 20 * np.log10(max(floor[r], 1e-12))
            cells.append(f"{lvl:6.1f} ({snr:4.1f})")
        print(f"RX {names[r]} ch{rxc[r]:<2}" + "".join(f"{c:>17}" for c in cells))
    min_snr = np.min(20 * np.log10(np.abs(H) * args.amp / np.maximum(floor[:, None], 1e-12)))
    if min_snr < 20:
        print("[WARN] SNR < 20 dB on some link: raise --tx-gain / --rx-gain")
    cond = np.linalg.cond(H) if M > 1 else 1.0
    print(f"Condition number of H: {cond:.1f}"
          + ("  [WARN] ill-conditioned: RX too close/similar, ZF will lose power" if cond > 10 else ""))

    # ---------------- 2. Angles relative to RX A ----------------
    print("\n=== 2. Geometry from H (line-of-sight assumption) ===")
    sin_a = np.sin(np.deg2rad(args.theta_a))
    theta_est = [args.theta_a]
    print(f"RX A: {args.theta_a:+.1f} deg (reference, from --theta-a)")
    for r in range(1, M):
        ratio = H[r] * np.conj(H[0])          # removes TX hardware offsets
        ratio = ratio * np.conj(ratio[0])      # removes RX offsets -> exp(j k dpsi)
        ph = np.unwrap(np.angle(ratio))
        slope = np.sum(k_idx * ph) / np.sum(k_idx ** 2)
        resid = np.rad2deg(np.sqrt(np.mean((ph - slope * k_idx) ** 2)))
        sin_r = sin_a + slope * lam / (2 * np.pi * d)
        if abs(sin_r) <= 1:
            th = np.rad2deg(np.arcsin(sin_r))
            theta_est.append(th)
            print(f"RX {names[r]}: {th:+.1f} deg   (fit residual {resid:.1f} deg rms"
                  + (", strong multipath" if resid > 20 else "") + ")")
        else:
            theta_est.append(None)
            print(f"RX {names[r]}: ambiguous, sin = {sin_r:.2f} (residual {resid:.1f} deg)")
    print(f"(+ = toward ch{txc[-1]})")

    # ---------------- 3. MRT / zero-forcing tests ----------------
    def unit_max(w):
        w = np.asarray(w, dtype=np.complex128)
        return w / np.max(np.abs(w))

    tests = [("No correction", np.ones(N, dtype=np.complex128))]
    for r in range(M):
        tests.append((f"MRT -> {names[r]}", np.exp(-1j * np.angle(H[r]))))
    if M > 1:
        W = np.linalg.pinv(H)  # N x M, H @ W = I (if M <= N)
        for r in range(M):
            tests.append((f"ZF -> {names[r]}", unit_max(W[:, r])))

    print("\n=== 3. Beamforming tests (dB relative to a single element at each RX) ===")
    Yt = burst([w for _, w in tests])
    ref = np.mean(np.abs(H) ** 2, axis=1)
    meas = db10(np.abs(Yt) ** 2 / ref[:, None])                        # M x T
    pred = db10(np.abs(H @ np.array([w for _, w in tests]).T) ** 2 / ref[:, None])

    hdr = f"{'test':<15}" + "".join(f"{'RX '+n:>9}" for n in names)
    print("Measured:")
    print(hdr)
    for i, (name, _) in enumerate(tests):
        print(f"{name:<15}" + "".join(f"{meas[r, i]:>+9.1f}" for r in range(M)))
    print("Predicted from H:")
    print(hdr)
    for i, (name, _) in enumerate(tests):
        print(f"{name:<15}" + "".join(f"{pred[r, i]:>+9.1f}" for r in range(M)))

    print(f"\nSummary (ideal MRT gain for {N} elements: {20*np.log10(N):+.1f} dB):")
    for r in range(M):
        i_mrt = 1 + r
        line = f"  RX {names[r]}: MRT gain {meas[r, i_mrt]:+.1f} dB"
        if M > 1:
            i_zf = 1 + M + r
            others = [meas[q, i_zf] for q in range(M) if q != r]
            line += (f" | ZF target {meas[r, i_zf]:+.1f} dB, "
                     f"isolation vs worst other {meas[r, i_zf] - max(others):+.1f} dB")
        print(line)

    results = dict(freq=args.freq, spacing=d, H=H, floor=floor,
                   tx_chans=np.array(txc), rx_chans=np.array(rxc),
                   test_names=np.array([n for n, _ in tests]),
                   test_w=np.array([w for _, w in tests]), Yt=Yt,
                   theta_est=np.array([np.nan if t is None else t for t in theta_est]))

    # ---------------- 4. Beam scan calibrated toward A ----------------
    if not args.no_scan:
        angles = np.arange(-80, 80 + args.scan_step / 2, args.scan_step)
        psi_a = 2 * np.pi * d * sin_a / lam
        c = np.exp(-1j * np.angle(H[0] / H[0, 0])) * np.exp(1j * k_idx * psi_a)
        weights = [c * np.exp(-1j * k_idx * 2 * np.pi * d * np.sin(np.deg2rad(th)) / lam)
                   for th in angles]
        print(f"\n=== 4. Beam scan calibrated toward A ({len(angles)} angles) ===")
        Ysc = burst(weights)
        P = db10(np.abs(Ysc) ** 2)
        P -= P.max(axis=1, keepdims=True)
        peaks = [parabolic_peak(angles, P[r]) for r in range(M)]
        for r in range(M):
            exp = "?" if theta_est[r] is None else f"{theta_est[r]:+.1f}"
            print(f"RX {names[r]}: scan peak {peaks[r]:+.1f} deg | from H: {exp} deg")
        results.update(angles=angles, P=P, peaks=np.array(peaks))

        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
            fig, ax = plt.subplots(figsize=(9, 5))
            for r in range(M):
                ax.plot(angles, P[r], "-", marker="o", ms=2,
                        label=f"RX {names[r]} (ch{rxc[r]}), peak {peaks[r]:+.1f}°", color=f"C{r}")
                if theta_est[r] is not None:
                    ax.axvline(theta_est[r], color=f"C{r}", ls=":", lw=1)
            ax.set_xlabel(f"Steering angle [deg]  (+ = toward ch{txc[-1]})")
            ax.set_ylabel("Received power [dB, each RX normalized]")
            ax.set_ylim(-35, 3)
            ax.grid(True, alpha=0.3)
            ax.legend(fontsize=8)
            ax.set_title(f"X410 {N} TX x {M} RX @ {args.freq/1e9:.3f} GHz, d/λ = {d/lam:.2f}"
                         "  (dotted: angles from H)")
            fig.tight_layout()
            fig.savefig(args.plot, dpi=120)
            print(f"Saved {args.plot}")
        except ImportError:
            print("matplotlib not available: skipping plot")

    np.savez(args.save, **results)
    print(f"Saved {args.save}")


if __name__ == "__main__":
    main()
