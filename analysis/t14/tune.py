import json, numpy as np, sys
sys.path.insert(0, "."); from t9c import tile_xy
exec(open("better.py").read().split("# ---- calibration on t13")[0])   # helpers only
B = json.load(open("better_est.json")); E = np.array(B["E"])
src = open("better.py").read(); ks = src.index("def kalman"); ke = src.index("filt, smooth = kalman(E)")
exec(src[ks:ke])
path=[(1,2),(1,3),(1,4),(1,5),(2,5),(3,5),(4,5),(4,4),(4,3),(3,3),(2,3),(2,2)]
def truth(t):
    k=(t-20)/2.0; i=int(np.floor(k)); f=k-i; p0=tile_xy(*path[i%12]); p1=tile_xy(*path[(i+1)%12]); return p0+f*(p1-p0)
def ev(P, L=-2.5):
    m=(E[:,0]+L>=20)&(E[:,0]+L<=164); e=np.array([np.linalg.norm(P[i]-truth(E[i,0]+L)) for i in np.flatnonzero(m)])
    step=np.median(np.linalg.norm(np.diff(P[m],axis=0),axis=1))*100
    tstep=np.median(np.linalg.norm(np.diff(np.array([truth(t+L) for t in E[m,0]]),axis=0),axis=1))*100
    return np.median(e), np.mean(e<=0.3), step, tstep
cands = {}
cands["mediana 5 (1,25 s)"] = np.array([np.median(E[max(0,i-2):i+3,1:3],0) for i in range(len(E))])
cands["mediana 9 (2,25 s)"] = np.array([np.median(E[max(0,i-4):i+5,1:3],0) for i in range(len(E))])
for sm_, q in ((0.25,0.6),(0.4,0.3),(0.5,0.15),(0.6,0.08)):
    f, s = kalman(E, sig_meas=sm_, q=q)
    cands[f"Kalman causal σ={sm_} q={q}"] = f; cands[f"Kalman+RTS σ={sm_} q={q}"] = s
# median-5 then causal Kalman
m5 = cands["mediana 5 (1,25 s)"]; E2 = E.copy(); E2[:,1:3] = m5
exec("E_backup=E"); 
for name, P in cands.items():
    md, f30, st, ts = ev(P); print(f"{name:28s} error mediano {md:.3f} m | ≤30 cm {100*f30:3.0f} % | salto mediano entre muestras {st:4.1f} cm (verdad {ts:.1f} cm)")
json.dump({k: v.tolist() for k, v in cands.items()}, open("cands.json", "w"))
