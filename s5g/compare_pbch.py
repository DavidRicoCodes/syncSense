#!/usr/bin/env python3
"""
Compare the SFN-based SSB numbering of two APs (outputs of x410_pbch_check.py or
the nr_events.jsonl of the two-AP scripts).

Each AP gives, for every decoded SSB, its common number n = (2*SFN + hrf)/4 mod
512 and the time of that SSB on its host clock, obtained from the device
timestamp through a device<->host anchor (not the processing-dependent arrival
time). Host clocks are only NTP-aligned (ms); one SSB of disagreement would move
the epoch by 20 ms, five SSB (same slot, invisible to the sentinels) by 100 ms.
For x410_two_ap.py runs, pass the records.jsonl files instead.

  python3 compare_pbch.py pbch_check_A/pbch_events.jsonl pbch_check_B/pbch_events.jsonl
"""
import json
import sys

import numpy as np


def load(path):
    rows = [json.loads(line) for line in open(path)]
    if any("n_common" in r and "unix_slot" in r for r in rows):          # x410_two_ap records.jsonl
        d = [r for r in rows if "n_common" in r and "unix_slot" in r]
        return np.array([r["n_common"] for r in d]), np.array([r["unix_slot"] for r in d]), rows
    key = "ssb_unix" if any("ssb_unix" in r for r in rows) else "host_unix"
    if key == "host_unix":
        print(f"[WARN] {path}: no device anchor, using arrival time (includes tens of ms of DSP latency)")
    d = [r for r in rows if r.get("pbch_ok") and key in r]
    n = np.array([((r["sfn"] * 2 + r["hrf"]) // 4) % 512 for r in d])
    t = np.array([r[key] for r in d])
    return n, t, rows


def main():
    (na, ta, ra), (nb, tb, rb) = load(sys.argv[1]), load(sys.argv[2])
    for nm, n, t, r in (("A", na, ta, ra), ("B", nb, tb, rb)):
        print(f"AP {nm}: {len(n)} numbered SSB / slots")
    ea = (ta - na * 0.02) % 10.24
    eb = (tb - nb * 0.02) % 10.24
    sa, sb = np.median(ea), np.median(eb)
    diff = ((sa - sb + 5.12) % 10.24) - 5.12
    print(f"host time of common SSB 0 (mod 10.24 s): A {sa:.5f} s (spread {np.ptp(ea) * 1e3:.2f} ms), "
          f"B {sb:.5f} s (spread {np.ptp(eb) * 1e3:.2f} ms), difference {diff * 1e3:+.2f} ms")
    ok = abs(diff) < 0.010
    print(f"Verdict: {'SAME numbering' if ok else 'DIFFERENT numbering'} "
          f"(|difference| < 10 ms; one SSB of disagreement would be 20 ms)")


if __name__ == "__main__":
    main()
