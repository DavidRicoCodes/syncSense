import numpy as np
from t9c import *
D={"A→B":load("t9_tiles_B",profile=True),"B→A":load("t9_tiles_A",profile=True)}
nw=100; f=np.fft.fftfreq(nw,1/200); mov=(abs(f)>2.5)&(abs(f)<80); w=np.hanning(nw)[:,None,None,None]
base=np.linalg.norm(CEN["A"]-CEN["B"])
res={}
for dn,d in D.items():
    P=d["prof"]; t=d["t"]; v=d["valid"]; Lg=P.shape[-1]; first=d["pfirst"]; rate=d["rate"]
    stat=np.mean(np.abs(P[v].mean(0))**2,axis=(0,1))       # static power per lag
    print(dn,"profile lags",first,"..",first+Lg-1,"| static peak at lag",first+np.argmax(stat),"| static profile dB rel peak:",np.round(10*np.log10(stat/stat.max()),1)[6:16])
    out=[]
    for s0 in range(0,len(t)-nw,nw//2):
        vv=v[s0:s0+nw]
        if np.max(np.diff(np.flatnonzero(np.r_[True,vv,True])))-1>10: continue
        seg=P[s0:s0+nw]; X=np.fft.fft((seg-seg.mean(0))*w,axis=0)[mov]
        out.append((t[s0+nw//2],np.sum(np.abs(X)**2,axis=(0,1,2))))
    res[dn]=(out,stat,first,rate)
np.save("range_res.npy",np.array([0]))
def peak_frac(y):
    i=int(np.argmax(y)); 
    if 0<i<len(y)-1:
        a,b,c=np.log(y[i-1:i+2]); return i+0.5*(a-c)/(a-2*b+c)
    return float(i)
print(f"\n{'tramo':12s} {'exceso real':>11s} | " + " | ".join(f"{dn}: exceso est (m)  forma(dB rel pico, lags -1..+4)" for dn in res))
for nm,t0,t1,tl in segments(3):
    if isinstance(tl,tuple):
        p=tile_xy(*tl); ex=np.linalg.norm(p-CEN["A"])+np.linalg.norm(p-CEN["B"])-base
    else: ex=np.nan
    s=f"{nm:12s} {ex:11.2f} | "
    for dn,(out,stat,first,rate) in res.items():
        E=np.mean([e for tt,e in out if t0<=tt<=t1],0)
        p0=peak_frac(stat); pm=peak_frac(E); dl=(pm-p0)*C/rate
        i0=int(np.argmax(stat)); sh=10*np.log10(E[i0-1:i0+5]/E.max())
        s+=f"{dl:6.2f}  [{' '.join(f'{x:5.1f}' for x in sh)}] | "
    print(s)
