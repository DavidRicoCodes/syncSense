"""Section VII numbers: two-way inter-AP offset, per-burst deviation, skipped turns, numbering, CFO.
Reads the backed-up runs; writes sec7_stats.json (+ per-second series for the figures)."""
import json, os, numpy as np
R = os.path.expanduser("~/sync_backups/x410_two_ap_results_20261014/runs")
RUNS = [("t10_tiles", "tiles, B 5G degraded", "2026-10-14"), ("t11_nrw4", "empty", "2026-10-14"), ("t12_fsync", "empty, then walking; f-sync", "2026-10-14"),
        ("t13_tiles_fs", "tiles; f-sync", "2026-10-14"), ("t14_track", "walking; f-sync", "2026-10-14"), ("t15c_check", "empty", "2026-10-15"),
        ("t16_tiles", "tiles; f-sync", "2026-10-15"), ("t18_zc80_g3", "empty; 80 us, G=3 us; f-sync", "2026-10-21")]
TILE_EMPTY = [(3, 17), (488, 507)]          # empty-room segments of the tile protocol (s from tx start)
def burst_cfo(h, dt_p):
    zp = np.einsum("bkrc,brc->bk", h, np.conj(h[:, 0])); ph = np.unwrap(np.angle(zp), axis=1)
    return np.polyfit(np.arange(h.shape[1]) * dt_p, ph.T, 1)[0] / (2 * np.pi)
def load(run, ap):
    d = f"{R}/{run}_{ap}"; z = np.load(f"{d}/sense_rx.npz"); s = json.load(open(f"{d}/summary.json"))
    return z, s
out = dict(runs=[], series={}, cfo={}, cdf={})
for run, desc, day in RUNS:
    Z, S = {}, {}
    for ap in "AB": Z[ap], S[ap] = load(run, ap)
    t0 = S["A"]["args"]["tx_start_unix"]; rate = float(Z["A"]["rate"])
    L = {}; dev_all = []; cc = None
    for ap in "AB":
        z = Z[ap]; g = (~z["acq"]) & (z["q"] >= 20)
        lag = z["lag"][g] / rate * 1e9; dev_all.append(np.abs(lag - np.median(lag)))
        n = z["n"][g].astype(int) % 512; u = z["unix"][g]; x = u - n * 0.02
        if cc is None: cc = np.median(x % 10.24)
        ep = np.round((x - cc) / 10.24).astype(int)
        L[ap] = {(a, b, e): (l, uu - t0) for a, b, e, l, uu in zip(n, z["sub"][g].astype(int), ep, lag, u)}
    com = sorted(set(L["A"]) & set(L["B"]), key=lambda k: L["A"][k][1])
    la = np.array([L["A"][k][0] for k in com]); lb = np.array([L["B"][k][0] for k in com]); t = np.array([L["A"][k][1] for k in com])
    off = (la - lb) / 2; sec = np.floor(t)
    def stats(m):
        o = off[m]; sc = np.floor(t[m]); om = np.array([np.median(o[sc == k]) for k in np.unique(sc)]); d_ = np.diff(o); d_ = d_[np.abs(d_) < 200]
        return dict(n=int(m.sum()), std=float(np.std(o)), p99=float(np.percentile(np.abs(o - np.median(o)), 99)), maxdev=float(np.max(np.abs(o - np.median(o)))),
                    fast=float(np.std(d_) / np.sqrt(2)), slow=float(np.std(om)), median=float(np.median(o)))
    whole = stats(np.ones(len(off), bool))
    empty = None
    if "tiles" in desc: empty = stats(np.any([(t >= a) & (t < b) for a, b in TILE_EMPTY], 0))
    if run == "t12_fsync": empty = stats(t < 60)
    dev = np.concatenate(dev_all)
    # skipped turns (TX events missed) and numbering, from summaries (t18 B summary was overwritten: A only)
    aps = "A" if run == "t18_zc80_g3" else "AB"
    late = sum(S[a]["results"]["counts"]["tx_late"] for a in aps); armed = sum(S[a]["results"]["counts"]["tx_armed"] + S[a]["results"]["counts"]["tx_late"] for a in aps)
    after30 = []
    for a in aps:
        ev = [json.loads(l) for l in open(f"{R}/{run}_{a}/tx_log.jsonl") if '"role": "TX"' in l]
        u = np.array([e["unix"] - t0 for e in ev]); miss = np.array([e["state"].startswith("MISSED") for e in ev])
        m = u >= 30; after30.append((miss[m].sum(), m.sum()))
    numb = {k: sum(S[a]["results"]["numbering"][k] for a in aps) for k in ("decodes", "checks", "mismatches", "crc_fails")}
    row = dict(run=run, desc=desc, day=day, duration_s=S["A"]["args"]["duration_s"], bursts=int(sum(len(d) for d in dev_all)), pairs=int(len(off)),
               whole=whole, empty=empty, dev_max=float(dev.max()), dev_p999=float(np.percentile(dev, 99.9)), dev_beyond_100ns=int((dev > 100).sum()),
               skipped=late / armed, skipped_after30=sum(a for a, b in after30) / max(1, sum(b for a, b in after30)), numbering=numb, numbering_aps=aps,
               ppm={a: S[a]["results"]["tracker"]["ppm"] for a in "AB" if "tracker" in S[a]["results"]},
               tracker_resid_ns={a: S[a]["results"]["tracker"]["resid_ns"] for a in "AB" if "tracker" in S[a]["results"]})
    out["runs"].append(row)
    ks = np.unique(sec); ks = ks[(ks >= 0)]
    out["series"][run] = dict(t=ks.tolist(), med=[float(np.median(off[sec == k])) for k in ks], p10=[float(np.percentile(off[sec == k], 10)) for k in ks], p90=[float(np.percentile(off[sec == k], 90)) for k in ks])
    out["cdf"][run] = np.sort(dev)[:: max(1, len(dev) // 4000)].tolist()
    if run in ("t11_nrw4", "t12_fsync", "t16_tiles", "t18_zc80_g3"):
        cf = {}
        for dn, rx in (("A->B", "B"), ("B->A", "A")):
            z = Z[rx]; g = (~z["acq"]) & (z["q"] >= 20); c = burst_cfo(z["h"][g].astype(complex), int(z["pulse_spacing"]) / rate); u = z["unix"][g] - t0
            sc = np.floor(u); kk = np.unique(sc); kk = kk[kk >= 0]
            cf[dn] = dict(t=kk.tolist(), cfo=[float(np.median(c[sc == k])) for k in kk], median=float(np.median(c)), std_burst=float(np.std(c)))
        out["cfo"][run] = cf
    print(f"{run:13s} {desc:30s} pairs {len(off):6d} | sigma {whole['std']:6.1f} fast {whole['fast']:4.1f} slow {whole['slow']:5.1f} p99 {whole['p99']:6.1f} max {whole['maxdev']:6.1f}"
          + (f" | empty sigma {empty['std']:5.1f} slow {empty['slow']:4.1f}" if empty else "") + f" | dev max {dev.max():6.1f} >100ns {int((dev>100).sum())}"
          + f" | skipped {100*late/armed:4.2f}% after30s {100*row['skipped_after30']:4.2f}% | numb {numb}")
for run, cf in out["cfo"].items(): print("CFO", run, {dn: (round(v["median"], 1), round(v["std_burst"], 1)) for dn, v in cf.items()})
json.dump(out, open("sec7_stats.json", "w"))
