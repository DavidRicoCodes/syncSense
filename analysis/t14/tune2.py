import json, numpy as np, sys
sys.path.insert(0, "."); from t9c import tile_xy
exec(open("better.py").read().split("# ---- calibration on t13")[0])
B = json.load(open("better_est.json")); E = np.array(B["E"])
src = open("better.py").read(); exec(src[src.index("def kalman"):src.index("filt, smooth = kalman(E)")])
exec(open("tune.py").read().split("def ev")[0].split("exec(src[ks:ke])")[1])   # path, truth
def ev(P, L=-2.5):
    m=(E[:,0]+L>=20)&(E[:,0]+L<=164); e=np.array([np.linalg.norm(P[i]-truth(E[i,0]+L)) for i in np.flatnonzero(m)])
    return np.median(e), np.mean(e<=0.3), np.median(np.linalg.norm(np.diff(P[m],axis=0),axis=1))*100
C = json.load(open("cands.json"))
for sm_, q in ((0.25,1.5),(0.25,3.0),(0.2,3.0),(0.3,2.0)):
    f, s = kalman(E, sig_meas=sm_, q=q); C[f"Kalman+RTS σ={sm_} q={q}"] = s.tolist()
    md, f30, st = ev(s); print(f"RTS σ={sm_} q={q}: error {md:.3f} m, ≤30 cm {100*f30:.0f} %, salto {st:.1f} cm")
json.dump(C, open("cands.json","w"))
