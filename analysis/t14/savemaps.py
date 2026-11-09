import sys, numpy as np
sys.path.insert(0, "."); from t9c import *
import loc9
D = {"A→B": (load("t14_track_B"), "A", "B"), "B→A": (load("t14_track_A"), "B", "A")}
out = {}
for dn, (d, tx, rx) in D.items():
    o, _ = loc9.maps(d, tx, rx)
    out[f"t|{dn}"] = np.array([x[0] for x in o]); out[f"P|{dn}"] = np.stack([x[1].astype(np.float32) for x in o])
np.savez_compressed("winmaps_t14.npz", th=loc9.th, **out)
