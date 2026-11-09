#!/usr/bin/env python3
"""
Step 3: Over-the-air phase calibration + beam scan on a USRP X410,
using the 4 TX channels as a 4-element uniform linear array (ULA) and one
RX channel of the same device as the measurement receiver (monostatic setup).

Everything runs in ONE session: the ZBX channels get a random phase offset on
every tune, so the calibration is only valid while the device stays tuned.

Setup:
  - TX antennas on TX/RX0 of channels 0..3, in a line, in order, spacing d.
  - RX antenna on RX1 of channel 0, facing the array center (broadside),
    1-2 m away, same height and polarization.

Examples:
  python3 bf_cal_scan.py                         # cal + scans + plot
  python3 bf_cal_scan.py --cal-only              # just measure per-channel levels
  python3 bf_cal_scan.py --hold 20               # after scanning, keep a beam at +20 deg
  python3 bf_cal_scan.py --freq 2.45e9 --spacing 0.061
"""
import argparse
import signal
import threading
import time

import numpy as np
import uhd

C = 299792458.0
TX_CHANS = [0, 1, 2, 3]


# --------------------------------------------------------------------------
# Arguments and device setup
# --------------------------------------------------------------------------
def parse_args():
    p = argparse.ArgumentParser(description="X410 4-element ULA: OTA calibration + beam scan")
    p.add_argument("--args", default="addr=192.168.10.2", help="UHD device args")
    p.add_argument("--freq", type=float, default=2.45e9, help="RF frequency [Hz]")
    p.add_argument("--rate", type=float, default=1.92e6, help="Sample rate [Hz] (245.76e6/N)")
    p.add_argument("--tone", type=float, default=60e3, help="Baseband tone offset [Hz]")
    p.add_argument("--amp", type=float, default=0.25, help="Digital amplitude per channel (0-1)")
    p.add_argument("--tx-gain", type=float, default=10.0, help="TX gain [dB]")
    p.add_argument("--rx-gain", type=float, default=20.0, help="RX gain [dB]")
    p.add_argument("--rx-chan", type=int, default=0, help="RX channel used as the probe")
    p.add_argument("--rx-ant", default="RX1", help="RX antenna port")
    p.add_argument("--spacing", type=float, default=None,
                   help="Element spacing [m] (default: lambda/2 at --freq)")
    p.add_argument("--seg-ms", type=float, default=10.0, help="Duration of each segment [ms]")
    p.add_argument("--scan-min", type=float, default=-60.0)
    p.add_argument("--scan-max", type=float, default=60.0)
    p.add_argument("--scan-step", type=float, default=2.0)
    p.add_argument("--cal-only", action="store_true", help="Only run the calibration burst")
    p.add_argument("--no-amp-eq", action="store_true",
                   help="Correct phase only, no amplitude equalization")
    p.add_argument("--hold", type=float, default=None,
                   help="After the scans, transmit a calibrated beam at this angle [deg] until Ctrl+C")
    p.add_argument("--plot", default="beam_scan.png", help="Output plot file")
    p.add_argument("--save", default="bf_results.npz", help="Output data file")
    return p.parse_args()


def setup_usrp(args):
    usrp = uhd.usrp.MultiUSRP(args.args)
    tune = uhd.types.TuneRequest(args.freq)

    # Untimed tuning, no set_time_now(), no clock/time source changes:
    # see step 2 (these caused RFDC sync failures between sessions).
    for ch in TX_CHANS:
        usrp.set_tx_rate(args.rate, ch)
        usrp.set_tx_antenna("TX/RX0", ch)
        usrp.set_tx_gain(args.tx_gain, ch)
        usrp.set_tx_freq(tune, ch)

    r = args.rx_chan
    usrp.set_rx_rate(args.rate, r)
    usrp.set_rx_antenna(args.rx_ant, r)
    usrp.set_rx_gain(args.rx_gain, r)
    usrp.set_rx_freq(tune, r)
    time.sleep(0.5)

    def check(kind, ch, freq, locked):
        print(f"{kind} ch{ch}: freq={freq/1e6:.6f} MHz  LO locked={locked}")
        if not locked:
            raise RuntimeError(f"{kind} ch{ch} LO not locked")
        if abs(freq - args.freq) > 1e3:
            raise RuntimeError(f"{kind} ch{ch} tuned to {freq/1e6:.6f} MHz; RFDC probably "
                               f"unsynchronized. Restart usrp-hwd and retry.")

    for ch in TX_CHANS:
        check("TX", ch, usrp.get_tx_freq(ch), usrp.get_tx_sensor("lo_locked", ch).to_bool())
    check("RX", r, usrp.get_rx_freq(r), usrp.get_rx_sensor("lo_locked", r).to_bool())
    print(f"Rate: TX {usrp.get_tx_rate(0)/1e6:.4f} Msps, RX {usrp.get_rx_rate(r)/1e6:.4f} Msps")
    return usrp


# --------------------------------------------------------------------------
# Timed TX burst with simultaneous RX capture
# --------------------------------------------------------------------------
def run_burst(usrp, tx, rx, txbuf, lead=0.4):
    """Send txbuf (4 x N) as one timed burst and capture N RX samples
    starting at the same device time. Returns (rx_samples, error_list)."""
    n_total = txbuf.shape[1]
    t0 = usrp.get_time_now().get_real_secs() + lead
    rx_out = np.zeros(n_total, dtype=np.complex64)
    errors = []

    def rx_worker():
        cmd = uhd.types.StreamCMD(uhd.types.StreamMode.num_done)
        cmd.num_samps = n_total
        cmd.stream_now = False
        cmd.time_spec = uhd.types.TimeSpec(t0)
        rx.issue_stream_cmd(cmd)
        spp = rx.get_max_num_samps()
        tmp = np.zeros((1, spp), dtype=np.complex64)
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
            rx_out[got:got + k] = tmp[0, :k]
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

    # Drain TX async messages: anything but burst_ack means a corrupted burst
    amd = uhd.types.TXAsyncMetadata()
    while tx.recv_async_msg(amd, 0.1):
        if amd.event_code != uhd.types.TXMetadataEventCode.burst_ack:
            errors.append(f"TX {amd.event_code} ch{amd.channel}")
    return rx_out, errors


def measure_segments(rx_out, ref, seg_len, nseg, guard=0.2):
    """Complex amplitude of the tone in each segment (guard trims transients
    and the TX->RX pipeline latency at the segment edges)."""
    g = int(seg_len * guard)
    h = np.zeros(nseg, dtype=np.complex128)
    for i in range(nseg):
        a, b = i * seg_len + g, (i + 1) * seg_len - g
        h[i] = np.mean(rx_out[a:b] * np.conj(ref[a:b]))
    return h


def db(x):
    return 20 * np.log10(np.maximum(np.abs(x), 1e-12))


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------
def main():
    args = parse_args()
    lam = C / args.freq
    d = args.spacing if args.spacing else lam / 2
    print(f"lambda = {lam*100:.2f} cm, spacing d = {d*100:.2f} cm (d/lambda = {d/lam:.3f})")
    if d / lam > 0.5 + 1e-3:
        print("[WARN] d > lambda/2: expect grating lobes inside the scan range")

    usrp = setup_usrp(args)
    rate = usrp.get_tx_rate(0)

    tx_args = uhd.usrp.StreamArgs("fc32", "sc16")
    tx_args.channels = TX_CHANS
    tx = usrp.get_tx_stream(tx_args)
    rx_args = uhd.usrp.StreamArgs("fc32", "sc16")
    rx_args.channels = [args.rx_chan]
    rx = usrp.get_rx_stream(rx_args)

    # Tone with an integer number of periods per segment
    n_per = int(round(rate / args.tone))
    tone = rate / n_per
    seg_len = max(1, int(round(args.seg_ms * 1e-3 * rate / n_per))) * n_per

    def ref_tone(n_samps):
        n = np.arange(n_samps)
        return np.exp(2j * np.pi * tone * n / rate).astype(np.complex64)

    k_idx = np.arange(len(TX_CHANS))

    def steer(theta_deg):
        psi = 2 * np.pi * d * np.sin(np.deg2rad(theta_deg)) / lam
        return np.exp(-1j * k_idx * psi)

    # ---------------- Calibration burst ----------------
    # Segments: [silence, ch0 only, ch1 only, ch2 only, ch3 only]
    nseg = 1 + len(TX_CHANS)
    s = ref_tone(nseg * seg_len)
    txbuf = np.zeros((len(TX_CHANS), nseg * seg_len), dtype=np.complex64)
    for k in k_idx:
        sl = slice((k + 1) * seg_len, (k + 2) * seg_len)
        txbuf[k, sl] = args.amp * s[sl]

    print(f"\n=== Calibration burst ({nseg} x {seg_len} samples, tone {tone/1e3:.2f} kHz) ===")
    rx_out, errs = run_burst(usrp, tx, rx, txbuf)
    if errs:
        raise RuntimeError(f"Calibration burst failed: {errs}")

    peak = np.max(np.abs(rx_out))
    print(f"RX peak |x| = {peak:.3f}")
    if peak > 0.8:
        print("[WARN] RX close to saturation: lower --rx-gain or --tx-gain")

    h_all = measure_segments(rx_out, s, seg_len, nseg) / args.amp
    floor = np.abs(h_all[0]) * args.amp  # silence segment, in RX full-scale units
    h = h_all[1:]                         # per-channel complex response

    print(f"\n{'ch':>3} {'level[dBFS]':>12} {'SNR[dB]':>8} {'rel amp[dB]':>12} {'rel phase[deg]':>15}")
    for k in k_idx:
        lvl = db(h[k] * args.amp)
        snr = lvl - db(floor)
        rel_amp = db(h[k]) - db(h[0])
        rel_ph = np.rad2deg(np.angle(h[k] / h[0]))
        print(f"{k:>3} {lvl:>12.1f} {snr:>8.1f} {rel_amp:>12.2f} {rel_ph:>15.1f}")
    print(f"Noise floor (silence segment): {db(floor):.1f} dBFS")
    if np.min(db(h * args.amp) - db(floor)) < 20:
        print("[WARN] SNR < 20 dB on some channel: raise gains or move the RX antenna closer")

    # Correction weights: align all phases to ch0, optionally equalize amplitude
    c = np.exp(-1j * np.angle(h / h[0]))
    if not args.no_amp_eq:
        c *= np.min(np.abs(h)) / np.abs(h)
    print("\nCorrection weights (applied on top of the steering vector):")
    for k in k_idx:
        print(f"  ch{k}: |c|={np.abs(c[k]):.3f}  phase={np.rad2deg(np.angle(c[k])):+7.1f} deg")

    results = dict(freq=args.freq, rate=rate, tone=tone, spacing=d, h=h, c=c, floor=floor)
    if args.cal_only:
        np.savez(args.save, **results)
        print(f"\nSaved {args.save}. (--cal-only: stopping here)")
        return

    # ---------------- Beam scans ----------------
    angles = np.arange(args.scan_min, args.scan_max + args.scan_step / 2, args.scan_step)

    def scan(cal_w, label):
        n_ang = len(angles)
        s_scan = ref_tone(n_ang * seg_len)
        buf = np.zeros((len(TX_CHANS), n_ang * seg_len), dtype=np.complex64)
        for i, th in enumerate(angles):
            w = cal_w * steer(th)
            sl = slice(i * seg_len, (i + 1) * seg_len)
            buf[:, sl] = (args.amp * w[:, None] * s_scan[None, sl]).astype(np.complex64)
        print(f"\n=== Scan {label}: {n_ang} angles, {n_ang*seg_len/rate:.2f} s ===")
        out, e = run_burst(usrp, tx, rx, buf)
        if e:
            raise RuntimeError(f"Scan '{label}' failed: {e}")
        return measure_segments(out, s_scan, seg_len, n_ang) / args.amp

    y_uncal = scan(np.ones(len(TX_CHANS)), "uncalibrated")
    y_cal = scan(c, "calibrated")

    # Coherence at broadside: |sum c_k h_k| / sum |c_k h_k| (100% = perfect)
    i0 = int(np.argmin(np.abs(angles)))
    coherence = np.abs(y_cal[i0]) / np.sum(np.abs(c * h))
    print(f"\nCoherence at {angles[i0]:+.0f} deg (calibrated): {100*coherence:.1f} %  "
          f"(ideal 100 %; this is measured vs the per-channel cal values)")

    p_uncal = np.abs(y_uncal) ** 2
    p_cal = np.abs(y_cal) ** 2
    ref_p = max(p_cal.max(), p_uncal.max())
    p_uncal_db = 10 * np.log10(p_uncal / ref_p + 1e-12)
    p_cal_db = 10 * np.log10(p_cal / ref_p + 1e-12)
    af = np.array([np.abs(np.sum(steer(th))) ** 2 / len(TX_CHANS) ** 2 for th in angles])
    theory_db = 10 * np.log10(af + 1e-12)

    print(f"\n{'angle':>6} {'uncal[dB]':>10} {'cal[dB]':>9} {'theory[dB]':>11}")
    for i in range(0, len(angles), max(1, int(round(10 / args.scan_step)))):
        print(f"{angles[i]:>6.0f} {p_uncal_db[i]:>10.1f} {p_cal_db[i]:>9.1f} {theory_db[i]:>11.1f}")
    print(f"Calibrated peak at {angles[np.argmax(p_cal)]:+.0f} deg "
          f"(expected 0 deg, the RX position)")

    results.update(angles=angles, y_uncal=y_uncal, y_cal=y_cal, theory_db=theory_db)
    np.savez(args.save, **results)
    print(f"Saved {args.save}")

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(8, 4.5))
        ax.plot(angles, theory_db, "k--", label="Theory (ideal ULA)")
        ax.plot(angles, p_uncal_db, "o-", ms=3, label="Measured, uncalibrated")
        ax.plot(angles, p_cal_db, "s-", ms=3, label="Measured, calibrated")
        ax.set_xlabel("Steering angle [deg]  (RX probe at 0 deg)")
        ax.set_ylabel("Received power [dB, normalized]")
        ax.set_ylim(-40, 3)
        ax.grid(True, alpha=0.3)
        ax.legend()
        ax.set_title(f"X410 4-element ULA @ {args.freq/1e9:.3f} GHz, d/λ = {d/lam:.2f}")
        fig.tight_layout()
        fig.savefig(args.plot, dpi=120)
        print(f"Saved {args.plot}")
    except ImportError:
        print("matplotlib not installed: skipping plot (pip install matplotlib)")

    # ---------------- Optional: hold a steered beam ----------------
    if args.hold is not None:
        w = c * steer(args.hold)
        reps = int(np.ceil(20000 / n_per))
        base = args.amp * ref_tone(n_per * reps)
        buf = np.ascontiguousarray((w[:, None] * base[None, :]).astype(np.complex64))
        stop = threading.Event()
        signal.signal(signal.SIGINT, lambda *_: stop.set())
        md = uhd.types.TXMetadata()
        md.start_of_burst = True
        md.has_time_spec = True
        md.time_spec = uhd.types.TimeSpec(usrp.get_time_now().get_real_secs() + 0.3)
        print(f"\nHolding calibrated beam at {args.hold:+.1f} deg "
              f"({(args.freq + tone)/1e6:.6f} MHz). Ctrl+C to stop.")
        while not stop.is_set():
            tx.send(buf, md, 1.0)
            md.start_of_burst = False
            md.has_time_spec = False
        md.end_of_burst = True
        tx.send(np.zeros((len(TX_CHANS), 100), dtype=np.complex64), md, 1.0)
        time.sleep(0.3)
        print("Stopped.")


if __name__ == "__main__":
    main()
