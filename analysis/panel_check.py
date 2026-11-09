import numpy as np, sys
runs={"t5ps50 chapas grandes":"t5/t5_empty122ps50_","t6 sin chapas":"t6/t6_nopanel_","t7 chapas pequeñas":"t7/t7_smallpanel_"}
# sync
for nm,pre in runs.items():
    L={}; c=None
    for ap in "AB":
        z=np.load(pre+ap+"/sense_rx.npz"); q=z["q"]; g=(~z["acq"])&(q>np.median(q)/4)
        n=z["n"][g].astype(int)%512; u=z["unix"][g]; x=u-n*0.02
        if c is None: c=np.median(x%10.24)
        ep=np.round((x-c)/10.24).astype(int)
        L[ap]={(a,b,e):(l/z["rate"]*1e9,t) for a,b,e,l,t in zip(n,z["sub"][g].astype(int),ep,z["lag"][g],u)}
    com=sorted(set(L["A"])&set(L["B"]),key=lambda k:L["A"][k][1])
    la=np.array([L["A"][k][0] for k in com]); lb=np.array([L["B"][k][0] for k in com]); t=np.array([L["A"][k][1] for k in com])
    off=(la-lb)/2; path=(la+lb)/2; sec=np.floor(t-t[0])
    om=np.array([np.median(off[sec==s]) for s in np.unique(sec)])
    print(f"SYNC {nm:22s} pares {len(com):6d} | offset std {np.std(off):5.1f} rápido {np.std(np.diff(off))/np.sqrt(2):4.1f} lento(1s) {np.std(om):5.1f} rango {np.ptp(om):5.1f} | camino std {np.std(path):4.1f}")
print()
for nm,pre in runs.items():
    for ap in "AB":
        z=np.load(pre+ap+"/sense_rx.npz"); q=z["q"]; g=(~z["acq"])&(q>np.median(q)/4)
        h=z["h"][g].mean(1)                       # (N,3,4)
        r=h/np.exp(1j*np.angle(h[:,:1,:1]))
        Hb=r.mean(0)
        p=np.angle(np.sum(h*np.conj(Hb),axis=(1,2)))
        r=h*np.exp(-1j*p)[:,None,None]; Hb=r.mean(0)
        stat=np.sum(np.abs(Hb)**2)/np.mean(np.sum(np.abs(r)**2,axis=(1,2)))
        s=np.linalg.svd(Hb,compute_uv=False); rk=s[0]**2/np.sum(s**2)
        # delay profile tail
        pr=z["profile"][g]; P=np.mean(np.abs(pr)**2,axis=(0,1,2)); pk=np.argmax(P)
        tail=10*np.log10(np.sum(P[pk+3:])/P[pk]); pre_=10*np.log10(np.sum(P[:max(pk-2,0)])/P[pk])
        # Doppler: uniform grid at 200 Hz
        k=(z["n"][g].astype(int)*4+z["sub"][g].astype(int)); k-=k.min()
        X=np.tile(Hb,(k.max()+1,1,1)).astype(complex); X[k]=r; X=X.reshape(len(X),-1)
        W=200; f=np.fft.fftfreq(W,1/200); band=(np.abs(f)>=2.5)&(np.abs(f)<=80)
        e=[]
        for i in range(0,len(X)-W,W):
            F=np.fft.fft(X[i:i+W]-X[i:i+W].mean(0),axis=0); e.append(np.sum(np.abs(F[band])**2)/(W*W*np.sum(np.abs(Hb)**2)))
        e=10*np.log10(np.array(e))
        print(f"{nm:22s} {ap} | estático {100*stat:5.1f} % | rango1 {100*rk:5.1f} % | cola retardo(>+3) {tail:6.1f} dB | antes {pre_:6.1f} dB | Doppler 2.5-80Hz med {np.median(e):6.1f} p90 {np.percentile(e,90):6.1f} max {e.max():6.1f} dB")
