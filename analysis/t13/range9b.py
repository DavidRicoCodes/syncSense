import numpy as np
from t9c import *
D={"A→B":load("t13_tiles_fs_B",profile=True),"B→A":load("t13_tiles_fs_A",profile=True)}
nw=100; f=np.fft.fftfreq(nw,1/200); mov=(abs(f)>2.5)&(abs(f)<80); w=np.hanning(nw)[:,None,None]
base=np.linalg.norm(CEN["A"]-CEN["B"])
def peak_frac(y):
    i=int(np.argmax(y))
    if 0<i<len(y)-1:
        a,b,c=np.log(y[i-1:i+2]); return i+0.5*(a-c)/(a-2*b+c)
    return float(i)
res={}
for dn,d in D.items():
    P=d["prof"]; t=d["t"]; v=d["valid"]; N,R,T,Lg=P.shape; first=d["pfirst"]; rate=d["rate"]
    Pm=P[v].mean(0)                                   # (R,T,L) static
    stat=np.sum(np.abs(Pm)**2,axis=(0,1))
    # static spatial subspace from lags around the peak (rank r)
    M=np.stack([Pm[:,:,l].ravel() for l in range(Lg)],1)   # (RT, L)
    Uq,sq,_=np.linalg.svd(M); r=2; Q=Uq[:,:r]; Pp=np.eye(R*T)-Q@Q.conj().T
    Y=(P.reshape(N,R*T,Lg).transpose(0,2,1)@Pp.T)          # (N,L,RT)
    out=[]
    for s0 in range(0,N-nw,nw//2):
        vv=v[s0:s0+nw]
        if np.max(np.diff(np.flatnonzero(np.r_[True,vv,True])))-1>10: continue
        seg=Y[s0:s0+nw]; X=np.fft.fft((seg-seg.mean(0))*w,axis=0)[mov]
        out.append((t[s0+nw//2],np.sum(np.abs(X)**2,axis=(0,2))))
    res[dn]=(out,stat,first,rate)
    print(dn,"sv static spatial:",np.round(sq[:4]**2/np.sum(sq**2),3))
print(f"{'tramo':12s} {'exceso real':>11s} | A→B: exceso est (m), forma lags -1..+5 (dB) | B→A: ídem")
rows=[]
for nm,t0,t1,tl in segments(3):
    ex=np.nan
    if isinstance(tl,tuple):
        p=tile_xy(*tl); ex=np.linalg.norm(p-CEN["A"])+np.linalg.norm(p-CEN["B"])-base
    s=f"{nm:12s} {ex:11.2f} | "; row=[ex]
    for dn,(out,stat,first,rate) in res.items():
        E=np.mean([e for tt,e in out if t0<=tt<=t1],0); p0=peak_frac(stat); pm=peak_frac(E); dl=(pm-p0)*C/rate
        i0=int(np.argmax(stat)); sh=10*np.log10(E[i0-1:i0+6]/E.max()); row.append(dl)
        s+=f"{dl:6.2f} [{' '.join(f'{x:5.1f}' for x in sh)}] | "
    rows.append(row); print(s)
r=np.array([x for x in rows if not np.isnan(x[0])])
for j,dn in ((1,"A→B"),(2,"B→A")):
    print(dn,"corr(real, est) =",np.round(np.corrcoef(r[:,0],r[:,j])[0,1],2),"| ajuste est = %.2f + %.2f·real"%tuple(np.polyfit(r[:,0],r[:,j],1)[::-1]))

print("\n--- restando el suelo de la sala vacía (0-20 s) ---")
rows=[]
for nm,t0,t1,tl in segments(3):
    if not isinstance(tl,tuple): continue
    p=tile_xy(*tl); ex=np.linalg.norm(p-CEN["A"])+np.linalg.norm(p-CEN["B"])-base; row=[ex]; s=f"{nm:12s} {ex:5.2f} |"
    for dn,(out,stat,first,rate) in res.items():
        E=np.mean([e for tt,e in out if t0<=tt<=t1],0); E0=np.mean([e for tt,e in out if 3<=tt<=17],0)
        snr=10*np.log10(E.sum()/E0.sum()); Ed=np.maximum(E-E0,1e-6*E.max())
        dl=(peak_frac(Ed)-peak_frac(stat))*C/rate; row+= [dl,snr]; s+=f" {dn} {dl:5.2f} m (movimiento {snr:4.1f} dB sobre vacío) |"
    rows.append(row); print(s)
r=np.array(rows)
for j,dn in ((1,"A→B"),(3,"B→A")):
    good=(r[:,j+1]>3)
    e=r[:,j]-r[:,0]
    print(dn,"todas: error mediana |%.2f| m, corr %.2f"%(np.median(abs(e)),np.corrcoef(r[:,0],r[:,j])[0,1]),"| con movimiento >3 dB (%d baldosas): error mediana %.2f m, corr %.2f"%(good.sum(),np.median(abs(e[good])),np.corrcoef(r[good,0],r[good,j])[0,1]))
