#!/usr/bin/env python3
"""
Step 2: Synchronized 4-channel TX on a USRP X410 (ZBX daughterboards).

Transmits the same complex baseband tone on all selected TX channels,
starting at a common timestamp, with an optional per-channel phase weight.
The weights are the hook for beamforming in later steps; for now they
default to 0 deg (and the real RF phases are NOT calibrated yet).

Example:
    python3 tx_4ch_test.py --freq 3.5e9 --gain 10
    python3 tx_4ch_test.py --channels 0          # one channel at a time
    python3 tx_4ch_test.py --phases 0 90 180 270
"""
import argparse
import signal
import threading
import time

import numpy as np
import uhd


def parse_args():
    p = argparse.ArgumentParser(description="X410 synchronized 4-channel TX test")
    p.add_argument("--args", default="addr=192.168.10.2", help="UHD device args")
    p.add_argument("--freq", type=float, default=3.5e9, help="RF center frequency [Hz]")
    p.add_argument("--rate", type=float, default=7.68e6,
                   help="Sample rate [Hz] (245.76e6/N works best)")
    p.add_argument("--gain", type=float, default=10.0, help="TX gain [dB] (0-60)")
    p.add_argument("--tone", type=float, default=120e3,
                   help="Baseband tone offset [Hz] (keeps it away from DC/LO leakage)")
    p.add_argument("--amp", type=float, default=0.3, help="Digital amplitude (0-1)")
    p.add_argument("--duration", type=float, default=0.0,
                   help="Seconds to transmit, 0 = until Ctrl+C")
    p.add_argument("--channels", type=int, nargs="+", default=[0, 1, 2, 3],
                   help="TX channels to use")
    p.add_argument("--phases", type=float, nargs=4, default=[0.0, 0.0, 0.0, 0.0],
                   help="Per-channel phase weight in degrees (indexed by channel 0..3)")
    p.add_argument("--no-timed-tune", action="store_true",
                   help="Tune channels immediately instead of with a timed command")
    return p.parse_args()


def setup_usrp(args):
    usrp = uhd.usrp.MultiUSRP(args.args)
    chans = args.channels

    # NOTE: do NOT call set_clock_source()/set_time_source() here. On X4xx that
    # reconfigures the clocking and forces an RFDC resync, which can leave the
    # converter tiles unsynchronized. Select sources through the device args
    # instead, e.g. --args "addr=192.168.10.2,clock_source=external,time_source=external"
    # (defaults are internal).

    for ch in chans:
        usrp.set_tx_rate(args.rate, ch)
        usrp.set_tx_antenna("TX/RX0", ch)
        usrp.set_tx_gain(args.gain, ch)

    # NOTE: no set_time_now() here. All timing below is relative to
    # get_time_now(), and resetting the timekeeper appears to break the RFDC
    # sync on the next session's initialization.

    # Tune all channels, ideally at the same timestamp
    tune_req = uhd.types.TuneRequest(args.freq)
    if args.no_timed_tune:
        for ch in chans:
            usrp.set_tx_freq(tune_req, ch)
    else:
        try:
            cmd_t = usrp.get_time_now().get_real_secs() + 0.2
            usrp.set_command_time(uhd.types.TimeSpec(cmd_t))
            for ch in chans:
                usrp.set_tx_freq(tune_req, ch)
            usrp.clear_command_time()
        except RuntimeError as e:
            print(f"[WARN] Timed tune failed ({e}); tuning immediately instead")
            usrp.clear_command_time()
            for ch in chans:
                usrp.set_tx_freq(tune_req, ch)
    time.sleep(0.5)  # let the timed tune execute and the LOs settle

    # Report actual settings and LO lock status
    for ch in chans:
        locked = usrp.get_tx_sensor("lo_locked", ch).to_bool()
        print(f"TX ch{ch}: freq={usrp.get_tx_freq(ch)/1e6:.6f} MHz  "
              f"rate={usrp.get_tx_rate(ch)/1e6:.4f} Msps  "
              f"gain={usrp.get_tx_gain(ch):.1f} dB  "
              f"ant={usrp.get_tx_antenna(ch)}  LO locked={locked}")
        if not locked:
            raise RuntimeError(f"LO on TX channel {ch} is not locked")
        err = abs(usrp.get_tx_freq(ch) - args.freq)
        if err > 1e3:
            raise RuntimeError(
                f"TX ch{ch} tuned to {usrp.get_tx_freq(ch)/1e6:.6f} MHz instead of "
                f"{args.freq/1e6:.6f} MHz (off by {err/1e6:.3f} MHz). The RFDC is "
                f"probably unsynchronized: restart the device "
                f"(ssh root@<ip> 'systemctl restart usrp-hwd') and retry.")
    return usrp


def build_buffer(args, actual_rate):
    """Integer number of tone periods, so the buffer repeats seamlessly."""
    n_per = int(round(actual_rate / args.tone))
    tone = actual_rate / n_per
    if abs(tone - args.tone) > 1e-6:
        print(f"[INFO] Tone adjusted to {tone/1e3:.3f} kHz for a seamless loop")
    reps = int(np.ceil(20000 / n_per))
    n = np.arange(n_per * reps)
    base = args.amp * np.exp(2j * np.pi * tone * n / actual_rate)

    phases = np.deg2rad(np.array(args.phases)[args.channels])
    weights = np.exp(1j * phases)
    buf = (weights[:, None] * base[None, :]).astype(np.complex64)
    return np.ascontiguousarray(buf), tone


def async_monitor(tx_stream, stop_evt, counts):
    md = uhd.types.TXAsyncMetadata()
    while not stop_evt.is_set():
        if tx_stream.recv_async_msg(md, 0.1):
            name = str(md.event_code).split(".")[-1]
            counts[name] = counts.get(name, 0) + 1
            if md.event_code in (uhd.types.TXMetadataEventCode.time_error,
                                 uhd.types.TXMetadataEventCode.seq_error):
                print(f"[ASYNC] {name} on ch{md.channel}")


def main():
    args = parse_args()
    usrp = setup_usrp(args)
    chans = args.channels
    rate = usrp.get_tx_rate(chans[0])
    buf, tone = build_buffer(args, rate)

    st_args = uhd.usrp.StreamArgs("fc32", "sc16")
    st_args.channels = chans
    tx = usrp.get_tx_stream(st_args)

    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())

    mon_stop = threading.Event()
    counts = {}
    mon = threading.Thread(target=async_monitor, args=(tx, mon_stop, counts), daemon=True)
    mon.start()

    # All channels start at the same device timestamp
    md = uhd.types.TXMetadata()
    md.start_of_burst = True
    md.end_of_burst = False
    md.has_time_spec = True
    start_t = usrp.get_time_now().get_real_secs() + 1.0
    md.time_spec = uhd.types.TimeSpec(start_t)

    print(f"\nTransmitting tone at {(args.freq + tone)/1e6:.6f} MHz on channels {chans}, "
          f"phases {[args.phases[c] for c in chans]} deg")
    print("Start at device time %.3f s. Ctrl+C to stop.\n" % start_t)

    total = 0
    max_samps = int(args.duration * rate) if args.duration > 0 else None
    while not stop.is_set():
        sent = tx.send(buf, md, 2.0)
        md.start_of_burst = False
        md.has_time_spec = False
        total += sent
        if max_samps is not None and total >= max_samps:
            break

    # Close the burst cleanly
    md.end_of_burst = True
    tx.send(np.zeros((len(chans), 100), dtype=np.complex64), md, 1.0)
    time.sleep(0.5)  # give time for the burst ACK
    mon_stop.set()
    mon.join()

    print(f"\nSent {total} samples/channel ({total/rate:.2f} s)")
    print("Async events:", counts if counts else "none")


if __name__ == "__main__":
    main()
