#!/usr/bin/env python3
"""
RX-only spectrum occupancy scan with one X410 channel (nothing is transmitted).

Steps the LO across the requested bands, captures --dwell-s per step (in short
blocks spread over the dwell), computes 1024-point spectra and keeps, per
frequency bin: mean power, max hold and occupancy (fraction of spectra above
the noise floor + --thr-db). Only the central --use-mhz of every capture is
kept (away from the DC/LO and the filter edges). Results per 20 MHz WiFi
channel and a figure.

Example:
  python3 x410_spectrum_scan.py --out scan_A
"""
import argparse
import json
import time
from pathlib import Path

import numpy as np

BANDS = {"2.4 GHz": (2400e6, 2500e6), "5 GHz": (5150e6, 5850e6), "6 GHz (6E)": (5925e6, 7125e6)}


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--args", default="addr=192.168.10.2")
    p.add_argument("--chan", type=int, default=0)
    p.add_argument("--ant", default="TX/RX0")
    p.add_argument("--gain", type=float, default=40.0)
    p.add_argument("--rate", type=float, default=61.44e6)
    p.add_argument("--use-mhz", type=float, default=40.0)
    p.add_argument("--dwell-s", type=float, default=1.0)
    p.add_argument("--blocks", type=int, default=40, help="capture blocks per step, spread over the dwell")
    p.add_argument("--block-samps", type=int, default=131072)
    p.add_argument("--nfft", type=int, default=1024)
    p.add_argument("--thr-db", type=float, default=10.0)
    p.add_argument("--passes", type=int, default=2)
    p.add_argument("--out", type=Path, default=Path("spectrum_scan"))
    return p.parse_args()


def main():
    a = parse_args()
    import uhd
    a.out.mkdir(parents=True, exist_ok=True)
    u = uhd.usrp.MultiUSRP(a.args)
    u.set_rx_rate(a.rate, a.chan)
    u.set_rx_antenna(a.ant, a.chan)
    u.set_rx_gain(a.gain, a.chan)
    R = u.get_rx_rate(a.chan)
    sa = uhd.usrp.StreamArgs("fc32", "sc16")
    sa.channels = [a.chan]
    st = u.get_rx_stream(sa)
    mx = st.get_max_num_samps()
    nb = a.block_samps - a.block_samps % a.nfft
    f_bin = np.fft.fftshift(np.fft.fftfreq(a.nfft, 1 / R))
    keep = np.abs(f_bin) <= a.use_mhz * 1e6 / 2
    keep &= np.abs(f_bin) > R / a.nfft * 2          # drop the DC bins
    win = np.hanning(a.nfft).astype(np.float32)
    rows = []
    centres = []
    for name, (lo, hi) in BANDS.items():
        c = lo + a.use_mhz * 1e6 / 2
        while c - a.use_mhz * 1e6 / 2 < hi:
            centres.append((name, c))
            c += a.use_mhz * 1e6
    print(f"{len(centres)} steps x {a.passes} passes, {a.dwell_s} s each, rate {R/1e6:.2f} Msps, "
          f"gain {a.gain} dB, ch{a.chan} {a.ant}")
    buf = np.empty(mx, dtype=np.complex64)
    md = uhd.types.RXMetadata()
    peak_fs = 0.0
    for ps in range(a.passes):
        for name, c in centres:
            u.set_rx_freq(uhd.types.TuneRequest(c), a.chan)
            time.sleep(0.05)
            spectra = []
            for b in range(a.blocks):
                cmd = uhd.types.StreamCMD(uhd.types.StreamMode.num_done)
                cmd.num_samps = nb
                cmd.stream_now = True
                st.issue_stream_cmd(cmd)
                x = np.empty(nb, dtype=np.complex64)
                got = 0
                while got < nb:
                    n = st.recv(buf, md, 1.0)
                    if md.error_code != uhd.types.RXMetadataErrorCode.none:
                        break
                    k = min(n, nb - got)
                    x[got:got + k] = buf[:k]
                    got += k
                if got < nb:
                    continue
                peak_fs = max(peak_fs, float(np.max(np.abs(x))))
                X = np.fft.fftshift(np.fft.fft(x.reshape(-1, a.nfft) * win, axis=1), axes=1)
                spectra.append(np.abs(X) ** 2)
                time.sleep(max(0.0, a.dwell_s / a.blocks - nb / R))
            if not spectra:
                continue
            P = np.concatenate(spectra)[:, keep]                 # (frames, bins)
            mean = P.mean(0)
            floor_step = np.percentile(mean, 10)                 # quietest 10 % of this capture
            occ = (P > floor_step * 10 ** (a.thr_db / 10)).mean(0)
            rows.append(dict(band=name, pass_=ps, f=(c + f_bin[keep]).tolist(),
                             mean=mean.tolist(), maxh=P.max(0).tolist(), occ=occ.tolist(),
                             floor_step=float(floor_step)))
            print(f"  pass {ps} {name} {c/1e6:7.1f} MHz done")
    # common noise floor: low percentile of the per-bin mean over the whole scan
    floor = float(np.median([r["floor_step"] for r in rows]))
    res = {}
    f_all = np.concatenate([np.array(r["f"]) for r in rows])
    m_all = np.concatenate([np.array(r["mean"]) for r in rows])
    x_all = np.concatenate([np.array(r["maxh"]) for r in rows])
    o_all = np.concatenate([np.array(r["occ"]) for r in rows])
    order = np.argsort(f_all, kind="stable")
    f_all, m_all, x_all, o_all = f_all[order], m_all[order], x_all[order], o_all[order]
    for name, (lo, hi) in BANDS.items():
        chans = []
        c0 = lo
        while c0 + 20e6 <= hi + 1:
            m = (f_all >= c0) & (f_all < c0 + 20e6)
            if m.any():
                chans.append(dict(f_lo_mhz=c0 / 1e6, f_hi_mhz=(c0 + 20e6) / 1e6,
                                  occupancy_pct=round(100 * float(np.mean(o_all[m])), 2),
                                  mean_excess_db=round(10 * np.log10(np.mean(m_all[m]) / floor), 2),
                                  max_excess_db=round(10 * np.log10(np.max(x_all[m]) / floor), 2)))
            c0 += 20e6
        res[name] = chans
    summary = dict(args={k: str(v) for k, v in vars(a).items()}, peak_fullscale=peak_fs,
                   noise_floor_db=10 * np.log10(floor), channels=res, finished=time.time())
    (a.out / "scan.json").write_text(json.dumps(summary, indent=2))
    np.savez_compressed(a.out / "scan.npz", f=f_all, mean=m_all, maxh=x_all, occ=o_all, floor=floor)
    print(f"\nPeak |x| {peak_fs:.3f} of full scale (saturation if near 1)")
    for name, chans in res.items():
        print(f"\n{name}: 20 MHz channels sorted by occupancy (lowest first)")
        for ch in sorted(chans, key=lambda c: (c["occupancy_pct"], c["mean_excess_db"]))[:8]:
            print(f"   {ch['f_lo_mhz']:7.0f}-{ch['f_hi_mhz']:7.0f} MHz: occupied {ch['occupancy_pct']:6.2f} % of the "
                  f"time, mean +{ch['mean_excess_db']:.1f} dB, max +{ch['max_excess_db']:.1f} dB over floor")
    print(f"Saved {a.out}/")


if __name__ == "__main__":
    main()
