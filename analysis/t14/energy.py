import numpy as np, sys
from t9c import *
def doppler_energy(d, nw=100, fmin=2.5, fmax=80, maxgap=10):
    H,t,v=d["H"],d["t"],d["valid"]; Ps=np.sum(abs(d["ref"])**2)
    f=np.fft.fftfreq(nw,1/200); mov=(abs(f)>fmin)&(abs(f)<fmax); w=np.hanning(nw)[:,None,None]
    E=[];T=[]
    for s0 in range(0,len(t)-nw,nw//2):
        vv=v[s0:s0+nw]
        # longest gap
        g=0;mg=0
        for x in vv: g=0 if x else g+1; mg=max(mg,g)
        if mg>maxgap: continue
        seg=H[s0:s0+nw]; X=np.fft.fft((seg-seg.mean(0))*w,axis=0)[mov]
        E.append(np.sum(abs(X)**2)/(np.sum(w**2)*Ps)); T.append(t[s0+nw//2])
    return np.array(T),np.array(E)
if __name__=="__main__":
    D={"A→B":load("t14_track_B"),"B→A":load("t14_track_A")}
    res={dn:doppler_energy(d) for dn,d in D.items()}
    print(f"{'tramo':14s} {'A→B dB':>8s} {'B→A dB':>8s}  ventanas usadas")
    seg_e={}
    for nm,t0,t1,_ in segments(3):
        row=[];nn=[]
        for dn in D:
            T,E=res[dn]; m=(T>=t0)&(T<=t1); row.append(10*np.log10(np.median(E[m])) if m.any() else np.nan); nn.append(int(m.sum()))
        seg_e[nm]=row; print(f"{nm:14s} {row[0]:8.1f} {row[1]:8.1f}  {nn}")
    v=np.nanmean([seg_e["vacío"],seg_e["vacío fin"]],0)
    print("\nvacío medio",np.round(v,1))
    for nm in seg_e: seg_e[nm]=np.array(seg_e[nm])
    print("Rechazo trasero A: (3,2) - detrás A =",np.round(seg_e["12:(3, 2)"]-seg_e["detrás A"],1)," | sobre vacío: delante",np.round(seg_e["12:(3, 2)"]-v,1),"detrás",np.round(seg_e["detrás A"]-v,1))
    print("Rechazo trasero B: (4,3) - detrás B =",np.round(seg_e["13:(4, 3)"]-seg_e["detrás B"],1)," | sobre vacío: delante",np.round(seg_e["13:(4, 3)"]-v,1),"detrás",np.round(seg_e["detrás B"]-v,1))
    np.savez("energy_t9.npz",**{k.replace(":","_"):v_ for k,v_ in seg_e.items()})
