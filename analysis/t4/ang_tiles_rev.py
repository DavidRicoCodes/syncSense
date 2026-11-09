import sys, numpy as np
sys.argv=["x"]; import loc_tiles_rev as L
th=np.arange(-89,90,1.0)
def steer_ang(n_idx, ap, s=1):
    r=((n_idx-1.5)*L.DSP)                         # positions along U (m)
    return np.exp(1j*s*L.K*np.outer(np.sin(np.radians(th)), r))
def run(folder, tx, rx):
    H,t,Hbar=L.load(folder)
    R,T=H.shape[1],H.shape[2]
    Uu,sv,Vh=np.linalg.svd(Hbar); u,v=Uu[:,0],Vh[0]
    thp_rx=L.theta(rx,L.CEN[tx]); thp_tx=L.theta(tx,L.CEN[rx])
    ar=steer_ang(L.IDX_RX,rx); at=steer_ang(L.IDX_TX,tx)
    i_r=np.argmin(abs(th-round(thp_rx))); i_t=np.argmin(abs(th-round(thp_tx)))
    g_rx=u/ar[i_r]; g_tx=v/at[i_t]; g_rx/=abs(g_rx); g_tx/=abs(g_tx)
    Hc=H/g_rx[None,:,None]/g_tx[None,None,:]; Hs=Hbar/g_rx[:,None]/g_tx[None,:]
    Us,ss,Vs=np.linalg.svd(Hs); b0=np.outer(Us[:,0],Vs[0]).ravel(); Pp=np.eye(R*T)-np.outer(b0,b0.conj())/np.vdot(b0,b0)
    B=(ar[:,None,:,None]*at[None,:,None,:]).reshape(len(th)**2,R*T); BP=np.conj(B)@Pp
    fs=1/np.median(np.diff(t)); nw=int(0.5*fs); hop=int(0.25*fs); f=np.fft.fftfreq(nw,1/fs); mov=(abs(f)>2.5)&(abs(f)<80); w=np.hanning(nw)[:,None,None]
    out=[]
    for s0 in range(0,len(t)-nw,hop):
        seg=Hc[s0:s0+nw]; X=np.fft.fft((seg-seg.mean(0))*w,axis=0)[mov].reshape(int(mov.sum()),-1).T
        P=np.sum(abs(BP@X)**2,1).reshape(len(th),len(th)); out.append((t[s0+nw//2],P/P.max()))
    return out
res={"A→B":run("t4_tiles_B","A","B"),"B→A":run("t4_tiles_A","B","A")}
print("tile   true θA  true θB | A→B: θA(tx) θB(rx) | B→A: θB(tx) θA(rx)")
rows=[]
for k,tl in enumerate(L.TILES):
    t0,t1=25+25*k+3,45+25*k-3; tr=L.tile_xy(*tl); ta,tb=L.theta("A",tr),L.theta("B",tr)
    est=[]
    for dn in ("A→B","B→A"):
        P=np.mean([P for tt,P in res[dn] if t0<=tt<=t1],0); i,j=np.unravel_index(np.argmax(P),P.shape)
        est.append((th[j],th[i]))   # (tx angle, rx angle)
    (aA,bB),(bB2,aA2)=est
    rows.append((ta,tb,aA,bB,bB2,aA2))
    print(f"{str(tl):6s} {ta:+6.0f} {tb:+7.0f} | {aA:+6.0f} {bB:+6.0f}     | {bB2:+6.0f} {aA2:+6.0f}")
r=np.array(rows)
for nm,tc,ec in (("A (AoD A→B)",0,2),("A (AoA B→A)",0,5),("B (AoA A→B)",1,3),("B (AoD B→A)",1,4)):
    x,y=r[:,tc],r[:,ec]; b,a=np.polyfit(x,y,1); print(f"{nm}: est = {a:+.1f} + {b:.2f}·true | mean bias {np.mean(y-x):+.1f}° | rms err {np.sqrt(np.mean((y-x)**2)):.1f}° | corr {np.corrcoef(x,y)[0,1]:.2f}")
