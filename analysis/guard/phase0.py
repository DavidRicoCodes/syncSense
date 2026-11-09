# Phase 0: how small can the RX guard be? Per-burst lag (measured - expected arrival, after centering on the
# acquired latency) from every S5G run, plus failures. Output: guard_phase0.json
import json, numpy as np, os, sys
B = os.path.expanduser("~/sync_backups/x410_two_ap_results_20261014/runs")
RUNS = ["t10_tiles", "t11_nrw4", "t12_fsync", "t13_tiles_fs", "t14_track", "t15c_check", "t16_tiles", "c3_b2"]
G_US = [0.1, 0.2, 0.5, 1, 2, 5]
out = []
for run in RUNS:
    for ap in "AB":
        d = f"{B}/{run}_{ap}"
        if not os.path.isdir(d): continue
        s = json.load(open(f"{d}/summary.json")); c = s["results"]["counts"]
        z = np.load(f"{d}/sense_rx.npz", allow_pickle=False, mmap_mode=None) if False else np.load(f"{d}/sense_rx.npz")
        rate = float(z["rate"]); lag = z["lag"] / rate * 1e9; acq = z["acq"]; t = z["unix"]
        m = ~acq; lag, t = lag[m], t[m]
        a = np.abs(lag - np.median(lag))
        # slow component: 1 s medians
        sec = np.floor(t - t[0]).astype(int); u = np.unique(sec)
        med1 = np.array([np.median(lag[sec == k]) for k in u])
        jumps = np.abs(np.diff(med1)) if len(med1) > 1 else np.array([0.0])
        armed = c.get("rx_armed", 0); fail = c.get("rx_fail", 0); late = c.get("rx_late", 0)
        row = dict(run=run, ap=ap, mode=s["args"].get("mode", "s5g") if run.startswith("c3") else "s5g",
                   duration_s=float(t[-1] - t[0]), n_ok=int(m.sum()), rx_armed=int(armed), rx_fail=int(fail), rx_late=int(late),
                   search_ns=s["args"].get("search_ns"), guard_us=s["args"].get("guard_us"),
                   lag_median_ns=float(np.median(lag)), lag_std_ns=float(np.std(lag)),
                   dev_p50_ns=float(np.percentile(a, 50)), dev_p99_ns=float(np.percentile(a, 99)),
                   dev_p999_ns=float(np.percentile(a, 99.9)), dev_max_ns=float(a.max()),
                   slow_range_ns=float(med1.max() - med1.min()), slow_max_jump_ns=float(jumps.max()),
                   frac_outside={str(g): float((a > g * 1e3).mean()) for g in G_US},
                   n_outside={str(g): int((a > g * 1e3).sum()) for g in G_US})
        out.append(row)
        print(f"{run:13s} {ap} {row['duration_s']:6.0f}s ok {row['n_ok']:6d} fail {fail:4d} late {late:4d} | "
              f"std {row['lag_std_ns']:6.1f} p99 {row['dev_p99_ns']:6.1f} p99.9 {row['dev_p999_ns']:6.1f} max {row['dev_max_ns']:7.1f} ns | "
              f"slow range {row['slow_range_ns']:6.1f} jump {row['slow_max_jump_ns']:5.1f} | >0.2us {row['n_outside']['0.2']} >0.5us {row['n_outside']['0.5']} >1us {row['n_outside']['1']}")
json.dump(out, open("guard_phase0.json", "w"), indent=1)
