import json, re, sys, numpy as np
sys.argv = ["x"]
out = {}
# ---------- tiles: localization per tile ----------
rows = []
for run, meth, dirs in (("t9", "t9 · A→B", ("A→B",)), ("t10", "t10 · A→B (fijado de antemano)", ("A→B",)), ("t10", "t10 · ambos sentidos", ("A→B", "B→A"))):
    import os
    g = {}
    base = os.getcwd(); os.chdir(run); sys.path.insert(0, os.getcwd()); sys.modules.pop("t9c", None)
    src = open("pos9.py").read().split('rng=np.random')[0]
    exec(src, g)
    for nm, tl, p, e in g["errs"]({"A": 1.12, "B": 1.12}, dirs):
        tr = g["tile_xy"](*tl)
        rows.append(dict(method=meth, run=run, order=int(nm.split(":")[0]), tile=f"({tl[0]},{tl[1]})",
                         truth_x=round(float(tr[0]), 2), truth_y=round(float(tr[1]), 2),
                         est_x=round(float(p[0]), 2), est_y=round(float(p[1]), 2), error_m=round(float(e), 3)))
    os.chdir(base); sys.path.pop(0)
out["tiles"] = rows
# ---------- range ----------
rows = []
for run, path in (("t9", "t9/range9b_out.txt"), ("t10", "t10/range.out")):
    on = False
    for line in open(path):
        if "restando" in line: on = True
        m = re.match(r"\s*(\d+):\((\d), (\d)\)\s+([\d.]+) \| A→B\s+([-\d.]+) m.*\| B→A\s+([-\d.]+) m", line)
        if on and m:
            rows.append(dict(run=run, order=int(m.group(1)), tile=f"({m.group(2)},{m.group(3)})", real_m=float(m.group(4)),
                             est_ab_m=float(m.group(5)), est_ba_m=float(m.group(6))))
out["range"] = rows
# ---------- short windows ----------
rows = []
for run in ("t9", "t10"):
    for k, v in json.load(open(f"dash/shortwin_{run}.json")).items():
        w, mode = k.split("|")
        rows.append(dict(run=run, window_s=float(w), mode="A→B" if mode == "A→B" else "ambos sentidos",
                         windows=v["n"], median_m=round(v["median"], 3), p80_m=round(v["p80"], 3), p95_m=round(v["p95"], 3),
                         share_le_30cm=round(v["le30"], 3)))
out["shortwin"] = rows
json.dump(out, open("dash/tmp_part1.json", "w"))
print({k: len(v) for k, v in out.items()})
