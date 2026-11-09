#!/usr/bin/env python3
"""
RX-only check of the PBCH/MIB (SFN) decoding on one X410 (nothing is transmitted).

Captures the 5G cell continuously on --nr-chan, runs the usual PSS/SSS/channel
pipeline of modulos_rx_tx on every 20 ms block and decodes the PBCH of every
SSB with nr_pbch.py. Reports decode rate, CRC failures, SFN continuity against
the local PSS tracker, decode time and the MIB fields.

Run it on both APs at the same time and compare with compare_pbch.py: both must
assign the same common SSB number to the same physical SSB.

  python3 x410_pbch_check.py --duration-s 60 --out-dir pbch_check_A
"""
import argparse
import json
import queue
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import x410_pss_timed_tx as base  # noqa: E402
from x410_two_ap import Clock  # noqa: E402

MIB_FIELDS = (("message_choice", 1), ("sfn_msb", 6), ("scs_common", 1), ("ssb_subcarrier_offset", 4),
              ("dmrs_typeA_position", 1), ("coreset0", 4), ("searchspace0", 4), ("cell_barred", 1),
              ("intra_freq_reselection", 1), ("spare", 1))


def parse_mib(bits):
    out, i = {}, 0
    for name, n in MIB_FIELDS:
        out[name] = int(bits[i:i + n], 2)
        i += n
    out["scs_common"] = "15 or 60 kHz" if out["scs_common"] == 0 else "30 or 120 kHz"
    out["dmrs_typeA_position"] = 2 + out["dmrs_typeA_position"]
    out["cell_barred"] = "barred" if out["cell_barred"] == 0 else "notBarred"
    return out


def parse_args():
    p = argparse.ArgumentParser(description="RX-only PBCH/MIB (SFN) check on one X410")
    p.add_argument("--duration-s", type=float, default=60.0)
    p.add_argument("--args", default="addr=192.168.10.2")
    p.add_argument("--modulos-root", type=Path, default=Path.home() / "syncSense" / "modulos_rx_tx")
    p.add_argument("--out-dir", type=Path, default=None)
    p.add_argument("--nr-chan", type=int, default=2)
    p.add_argument("--nr-ant", default="RX1")
    p.add_argument("--nr-freq", type=float, default=3541.44e6)
    p.add_argument("--nr-gain", type=float, default=32.0)
    p.add_argument("--nr-rate", type=float, default=15.36e6)
    p.add_argument("--tick-rate", type=float, default=245.76e6)
    p.add_argument("--nr-block-ms", type=float, default=20.0)
    p.add_argument("--nr-overlap-ms", type=float, default=1.0)
    p.add_argument("--force-nid2", type=int, choices=(0, 1, 2), default=0)
    p.add_argument("--force-ibar-ssb", type=int, default=0)
    p.add_argument("--force-ncellid", type=int, default=None)
    p.add_argument("--no-cfo-correction", action="store_true")
    p.add_argument("--cfo-warmup-iters", type=int, default=30)
    p.add_argument("--nr-workers", type=int, default=2)
    p.add_argument("--pbch-lmax", type=int, default=8, choices=(4, 8, 64))
    p.add_argument("--track-window", type=int, default=50)
    p.add_argument("--track-min-pts", type=int, default=20)
    p.add_argument("--track-max-resid-ns", type=float, default=300.0)
    p.add_argument("--holdover-ms", type=float, default=200.0)
    p.add_argument("--outlier-ns", type=float, default=1000.0)
    a = p.parse_args()
    a.rate = a.nr_rate
    a.wifi_rate = a.nr_rate
    a.tx_chans, a.rx_chans = [], []
    return a


class NrOnlyRadio(base.UhdRadio):
    """UhdRadio with only the 5G channel configured and streamed (no TX path at all)."""

    def _setup(self):
        uhd, a = self.uhd, self.args
        u = uhd.usrp.MultiUSRP(a.args)
        self.usrp = u
        u.set_rx_rate(self.Rnr, a.nr_chan)
        u.set_rx_antenna(a.nr_ant, a.nr_chan)
        u.set_rx_gain(a.nr_gain, a.nr_chan)
        u.set_rx_freq(uhd.types.TuneRequest(a.nr_freq), a.nr_chan)
        time.sleep(0.5)
        f, r = u.get_rx_freq(a.nr_chan), u.get_rx_rate(a.nr_chan)
        lock = u.get_rx_sensor("lo_locked", a.nr_chan).to_bool()
        ok = lock and abs(f - a.nr_freq) < 1e3 and abs(r - self.Rnr) < 1.0
        print(f"\n=== Setup ===\nRX ch{a.nr_chan}: {f/1e6:.4f} MHz {r/1e6:.3f} Msps LO locked={lock} "
              f"{'OK' if ok else 'FAIL'} (no TX configured)")
        self.setup_report.append(dict(kind="RX", chan=a.nr_chan, freq=f, rate=r, lo_locked=lock, ok=ok))
        if not ok:
            raise RuntimeError("5G channel not locked, mistuned or rate coerced")
        sa = uhd.usrp.StreamArgs("fc32", "sc16")
        sa.channels = [a.nr_chan]
        self.nr = u.get_rx_stream(sa)
        self.tx = self.obs = None

    def start(self):
        import threading
        self.threads = [threading.Thread(target=self._nr_thread, daemon=True)]
        for t in self.threads:
            t.start()


def main():
    a = parse_args()
    out = a.out_dir or Path(f"pbch_check_{time.strftime('%Y%m%d_%H%M%S')}")
    out.mkdir(parents=True, exist_ok=True)
    radio = NrOnlyRadio(a, None)
    radio.pbch_force = True
    lock = radio.acquire_nr()
    trk = base.Tracker(a)
    f = open(out / "pbch_events.jsonl", "w")
    radio.phase = "pbch_check"
    radio.start()
    clock = Clock(radio, radio.R)      # device ticks <-> host time (RTT of the get_time_now query only)
    last_anchor = time.monotonic()
    t_end = time.monotonic() + a.duration_s
    last = time.monotonic()
    rows = []
    print(f"\n=== Decoding PBCH for {a.duration_s:.0f} s (RX only) ===")
    try:
        while time.monotonic() < t_end:
            try:
                row = radio.nr_q.get(timeout=0.2)
            except queue.Empty:
                continue
            row["host_unix"] = time.time()          # operational only: when the result reached the host
            if time.monotonic() - last_anchor > 10:
                clock.refresh()
                last_anchor = time.monotonic()
            if row.get("valid"):
                row["k"] = trk.add(row["event_device_ticks"])
                row["ssb_unix"] = clock.unix(row["event_device_ticks"])   # host-clock time of the SSB itself
            f.write(json.dumps(row) + "\n")
            rows.append(row)
            if time.monotonic() - last > 5:
                last = time.monotonic()
                v = [r for r in rows if r.get("valid")]
                d = [r for r in v if r.get("pbch_ok")]
                msg = f"  SSB {len(v)} | PBCH ok {len(d)} ({100 * len(d) / max(1, len(v)):.1f} %)"
                if d:
                    msg += f" | last SFN {d[-1]['sfn']} hrf {d[-1]['hrf']} | decode {np.median([r['pbch_ms'] for r in d[-50:]]):.1f} ms"
                print(msg)
    except KeyboardInterrupt:
        print("Interrupted")
    finally:
        radio.stop()
        f.close()

    v = [r for r in rows if r.get("valid")]
    d = [r for r in v if r.get("pbch_ok") and r.get("k") is not None]
    res = dict(nr_lock=lock, ssb_valid=len(v), blocks=len(rows), pbch_ok=len(d),
               pbch_crc_fail=sum(1 for r in v if r.get("pbch_ok") is False),
               radio_stats=radio.stats, tracker=dict(ppm=trk.ppm(), resid_ns=trk.fit[4] if trk.fit else None))
    if d:
        idx = np.array([base.ssb_index_from_sfn(r["sfn"], r["hrf"]) for r in d])
        ks = np.array([r["k"] for r in d])
        k0 = (idx - ks) % 512
        vals, cnt = np.unique(k0, return_counts=True)
        res["sfn_vs_tracker"] = dict(consistent_pct=round(100 * cnt.max() / len(k0), 3),
                                     distinct_offsets={int(x): int(c) for x, c in zip(vals, cnt)})
        res["hrf_values"] = sorted({r["hrf"] for r in d})
        res["sfn_parity"] = sorted({r["sfn"] % 2 for r in d})
        mibs = {r["mib"][7:] for r in d}
        res["mib_constant_part_distinct"] = len(mibs)
        res["mib_fields"] = parse_mib(d[-1]["mib"])
        res["decode_ms"] = base.stats_ns([r["pbch_ms"] for r in d])
        res["evm_db"] = base.stats_ns([r["pbch_evm_db"] for r in d])
        # host time of common SSB 0 (only to compare APs; NTP accuracy)
        host = np.array([r["ssb_unix"] for r in d])
        res["host_time_of_ssb0_mod_10p24s"] = round(float(np.median((host - idx * 0.02) % 10.24)), 5)
    (out / "summary.json").write_text(json.dumps(res, indent=2, default=float))
    print("\n=== Summary ===")
    print(f"SSB valid {res['ssb_valid']} | PBCH decoded {res['pbch_ok']} | CRC failures {res['pbch_crc_fail']}")
    for k in ("sfn_vs_tracker", "hrf_values", "sfn_parity", "mib_constant_part_distinct", "mib_fields",
              "decode_ms", "evm_db", "host_time_of_ssb0_mod_10p24s"):
        if k in res:
            print(f"  {k}: {res[k]}")
    ok = bool(d) and res["pbch_ok"] >= 0.95 * res["ssb_valid"] and res["sfn_vs_tracker"]["consistent_pct"] == 100.0 \
        and res["mib_constant_part_distinct"] == 1
    print(f"Verdict: {'PASS' if ok else 'CHECK'} (>=95 % of SSB decoded, SFN always consistent with the PSS tracker, "
          f"constant MIB)")
    print(f"Saved {out}/ (pbch_events.jsonl, summary.json)")


if __name__ == "__main__":
    main()
