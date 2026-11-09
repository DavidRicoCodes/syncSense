import sys, os, json, numpy as np, re
base = os.getcwd()
out = {"loc": [], "angles": [], "range": [], "energy": [], "loc_summary": []}
modes = {"A→B": ("A→B",), "B→A": ("B→A",), "ambos": ("A→B", "B→A")}
for run, d, pre in (("t9", "t9", "t9_tiles"), ("t10", "t10", "t10_tiles"), ("t13", "t13", "t13_tiles_fs"), ("t16", "t16", "t16_tiles")):
    os.chdir(os.path.join(base, d)); sys.path.insert(0, os.getcwd()); sys.modules.pop("t9c", None)
    g = {}; exec(open("pos9.py").read().split('rng=np.random')[0], g)
    for mname, dirs in modes.items():
        e = []
        for nm, tl, p, er in g["errs"]({"A": 1.12, "B": 1.12}, dirs):
            tr = g["tile_xy"](*tl); e.append(er)
            out["loc"].append(dict(run=run, mode=mname, order=int(nm.split(":")[0]), tile=f"({tl[0]},{tl[1]})", truth_x=round(float(tr[0]), 2), truth_y=round(float(tr[1]), 2),
                                   est_x=round(float(p[0]), 2), est_y=round(float(p[1]), 2), error_m=round(float(er), 3)))
        e = np.array(e)
        out["loc_summary"].append(dict(run=run, mode=mname, median_m=round(float(np.median(e)), 3), mean_m=round(float(e.mean()), 3), max_m=round(float(e.max()), 3),
                                       le30=int(np.sum(e <= 0.3)), le60=int(np.sum(e <= 0.6)), tiles=int(len(e))))
    z = g["z"]; th = z["th"]
    for nm, _, _, tl in g["S"]:
        tr = g["tile_xy"](*tl); row = dict(run=run, order=int(nm.split(":")[0]), tile=f"({tl[0]},{tl[1]})", true_thA=round(g["theta"]("A", tr), 1), true_thB=round(g["theta"]("B", tr), 1))
        for dn in ("A→B", "B→A"):
            P = z[f"{nm}|{dn}"]; i, j = np.unravel_index(np.argmax(P), P.shape)   # [rx, tx]
            tx, rx = dn[0], dn[2]
            row[f"{dn}_tx_{tx}"] = float(th[j]); row[f"{dn}_rx_{rx}"] = float(th[i])
        out["angles"].append({"run": row["run"], "order": row["order"], "tile": row["tile"], "true_thA": row["true_thA"], "true_thB": row["true_thB"],
                              "ab_thA_tx": row["A→B_tx_A"], "ab_thB_rx": row["A→B_rx_B"], "ba_thB_tx": row["B→A_tx_B"], "ba_thA_rx": row["B→A_rx_A"]})
    rf = {"t9": "range9b_out.txt"}.get(run, "range.out"); on = False
    for line in open(rf):
        if "restando" in line: on = True
        m = re.match(r"\s*(\d+):\((\d), (\d)\)\s+([\d.]+) \| A→B\s+([-\d.]+) m \(movimiento\s+([-\d.]+) dB.*\| B→A\s+([-\d.]+) m \(movimiento\s+([-\d.]+) dB", line)
        if on and m:
            out["range"].append(dict(run=run, order=int(m.group(1)), tile=f"({m.group(2)},{m.group(3)})", real_m=float(m.group(4)), est_ab_m=float(m.group(5)),
                                     est_ba_m=float(m.group(7)), motion_ab_db=float(m.group(6)), motion_ba_db=float(m.group(8))))
    ef = {"t9": "energy_out.txt"}.get(run, "energy.out")
    for line in open(ef):
        m = re.match(r"(\S.*?)\s{2,}([-\d.]+)\s+([-\d.]+)\s+\[", line)
        if m: out["energy"].append(dict(run=run, segment=m.group(1).strip(), ab_db=float(m.group(2)), ba_db=float(m.group(3))))
    sys.path.pop(0)
os.chdir(base); json.dump(out, open("dash2/part_loc.json", "w")); print({k: len(v) for k, v in out.items()})
for r in out["loc_summary"]: print(r)
