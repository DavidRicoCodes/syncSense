import numpy as np
from t9c import *
z=np.load("angmaps_t10.npz"); th=z["th"]
S=[s for s in segments(3) if isinstance(s[3],tuple)]
xs=np.arange(0.0,3.001,0.05); ys=np.arange(0.0,3.001,0.05); GX,GY=np.meshgrid(xs,ys); G=np.column_stack([GX.ravel(),GY.ravel()])
G=G[((G-CEN["A"])@NRM["A"]>0.1)&((G-CEN["B"])@NRM["B"]>0.1)]
def sin_true(ap,P):
    d=(P-CEN[ap]); d/=np.linalg.norm(d,axis=1,keepdims=True); return d@U[ap]
SP={ap:np.sin(np.radians(theta(ap,CEN["B" if ap=="A" else "A"]))) for ap in "AB"}
def meas_angle(ap,P,k):
    s=SP[ap]+k[ap]*(sin_true(ap,P)-SP[ap]); return np.degrees(np.arcsin(np.clip(s,-1,1)))
def ix(a): return np.clip(np.round(a-th[0]).astype(int),0,len(th)-1)
def score(nm,k,dirs=("A→B","B→A")):
    tot=0
    for dn in dirs:
        P=z[f"{nm}|{dn}"]; P=P/P.max(); tx,rx=dn[0],dn[2]
        tot=tot+P[ix(meas_angle(rx,G,k)),ix(meas_angle(tx,G,k))]
    return tot
def locate(nm,k,dirs=("A→B","B→A")): return G[np.argmax(score(nm,k,dirs))]
def errs(k,dirs=("A→B","B→A"),only=None):
    out=[]
    for nm,_,_,tl in S:
        if only and nm not in only: continue
        out.append((nm,tl,locate(nm,k,dirs),np.linalg.norm(locate(nm,k,dirs)-tile_xy(*tl))))
    return out
def fit_k(names,dirs=("A→B","B→A")):
    best=None
    for ka in np.arange(0.9,1.31,0.02):
        for kb in np.arange(0.9,1.31,0.02):
            k={"A":ka,"B":kb}; e=np.mean([np.linalg.norm(locate(nm,k,dirs)-tile_xy(*tl)) for nm,_,_,tl in S if nm in names])
            if best is None or e<best[0]-1e-9: best=(e,k)
    return best[1]
rng=np.random.default_rng(0); chance=np.median([np.linalg.norm(G[rng.integers(len(G))]-tile_xy(*S[rng.integers(len(S))][3])) for _ in range(20000)])
print(f"azar (punto aleatorio de la zona): mediana {chance:.2f} m")
k1={"A":1.0,"B":1.0}
for dirs in (("A→B","B→A"),("A→B",),("B→A",)):
    print(f"\n=== sentidos {'+'.join(dirs)} ===")
    kc=fit_k({"1:(3, 3)"},dirs)
    rows={}
    for lab,k in (("geometría (3,0 cm, solo compañero)",k1),(f"calibrado con baldosa 1 (3,3): kA={kc['A']:.2f} kB={kc['B']:.2f}",kc)):
        e=errs(k,dirs); rows[lab]=e
        ev=[x[3] for x in e if x[0]!="1:(3, 3)"]
        print(f"{lab}: mediana {np.median(ev):.2f} m, media {np.mean(ev):.2f} m, <=0,6 m en {sum(np.array(ev)<=0.6)}/{len(ev)} (sin contar la baldosa 1)")
    # leave-one-out global k
    loo=[]
    for nm,_,_,tl in S:
        kk=fit_k({n for n,_,_,_ in S if n!=nm},dirs); loo.append(np.linalg.norm(locate(nm,kk,dirs)-tile_xy(*tl)))
    print(f"dejando una fuera (k ajustado con las otras 15): mediana {np.median(loo):.2f} m, media {np.mean(loo):.2f} m, <=0,6 m en {sum(np.array(loo)<=0.6)}/{len(loo)}")
    if dirs==("A→B","B→A") or dirs==("A→B",):
        print("  detalle (geometría | calibrado 1 | dejando una fuera):")
        for (nm,tl,p0,e0),(_,_,p1,e1),e2 in zip(rows[list(rows)[0]],rows[list(rows)[1]],loo):
            print(f"   {nm:10s} verdad ({tile_xy(*tl)[0]:.2f},{tile_xy(*tl)[1]:.2f}) | ({p0[0]:.2f},{p0[1]:.2f}) {e0:.2f} | ({p1[0]:.2f},{p1[1]:.2f}) {e1:.2f} | {e2:.2f}")
