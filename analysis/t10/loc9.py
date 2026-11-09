import numpy as np, json, sys
from t9c import *
from energy import doppler_energy
th=np.arange(-89,90,1.0)
def sv(idx):  # (len(th), n) steering vs angle, s=+1
    r=(idx-1.5)*DSP; return np.exp(1j*K*np.outer(np.sin(np.radians(th)),r))
def maps(d, tx, rx, nw=100, maxgap=10, null=1):
    H,t,v,ref=d["H"],d["t"],d["valid"],d["ref"]; R,T=H.shape[1:]
    Uu,s_,Vh=np.linalg.svd(ref); u,vv=Uu[:,0],Vh[0]
    ar,at=sv(IDX_RX),sv(IDX_TX)
    i_r=np.argmin(abs(th-theta(rx,CEN[tx]))); i_t=np.argmin(abs(th-theta(tx,CEN[rx])))
    g_rx=u/ar[i_r]; g_tx=vv/at[i_t]; g_rx/=abs(g_rx); g_tx/=abs(g_tx)
    Hc=H/g_rx[None,:,None]/g_tx[None,None,:]; Hs=ref/g_rx[:,None]/g_tx[None,:]
    Us,ss,Vs=np.linalg.svd(Hs)
    Q,_=np.linalg.qr(np.stack([np.outer(Us[:,i],Vs[i]).ravel() for i in range(null)],1)); Pp=np.eye(R*T)-Q@Q.conj().T
    B=(ar[:,None,:,None]*at[None,:,None,:]).reshape(len(th)**2,R*T); BP=np.conj(B)@Pp   # index [i_rx, i_tx]
    f=np.fft.fftfreq(nw,1/200); mov=(abs(f)>2.5)&(abs(f)<80); w=np.hanning(nw)[:,None,None]
    out=[]
    for s0 in range(0,len(t)-nw,nw//2):
        vv_=v[s0:s0+nw]
        if np.max(np.diff(np.flatnonzero(np.r_[True,vv_,True])))-1>maxgap: continue
        seg=Hc[s0:s0+nw]; X=np.fft.fft((seg-seg.mean(0))*w,axis=0)[mov].reshape(int(mov.sum()),-1).T
        P=np.sum(abs(BP@X)**2,1).reshape(len(th),len(th)); out.append((t[s0+nw//2],P))
    return out, float(s_[0]**2/np.sum(s_**2))
if __name__=="__main__":
    D={"A→B":(load("t10_tiles_B"),"A","B"),"B→A":(load("t10_tiles_A"),"B","A")}
    M={}; r1={}
    for dn,(d,tx,rx) in D.items(): M[dn],r1[dn]=maps(d,tx,rx)
    seg={}
    for nm,t0,t1,tl in segments(3):
        seg[nm]={dn:np.mean([P for tt,P in M[dn] if t0<=tt<=t1],0) for dn in M}
    np.savez_compressed("angmaps_t10.npz", th=th, **{f"{nm}|{dn}":seg[nm][dn] for nm in seg for dn in seg[nm]})
    print("rank1",r1, "partner angles: A sees B",theta("A",CEN["B"]),"B sees A",theta("B",CEN["A"]))
    print(f"{'tramo':12s} {'θA real':>7s} {'θB real':>7s} | A→B: θA(tx) θB(rx) | B→A: θB(tx) θA(rx)")
    for nm,t0,t1,tl in segments(3):
        if not isinstance(tl,tuple): continue
        p=tile_xy(*tl); ta,tb=theta("A",p),theta("B",p); est=[]
        for dn in ("A→B","B→A"):
            P=seg[nm][dn]; i,j=np.unravel_index(np.argmax(P),P.shape); est.append((th[j],th[i]))  # (tx, rx)
        print(f"{nm:12s} {ta:+7.0f} {tb:+7.0f} | {est[0][0]:+6.0f} {est[0][1]:+6.0f}     | {est[1][0]:+6.0f} {est[1][1]:+6.0f}")
