import json,numpy as np,sys
sys.path.insert(0,"."); from t9c import tile_xy, CEN
import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
from matplotlib.collections import LineCollection
B=json.load(open("better_est.json")); E=np.array(B["E"]); F=np.array(json.load(open("median5.json"))["med"])
path=[(1,2),(1,3),(1,4),(1,5),(2,5),(3,5),(4,5),(4,4),(4,3),(3,3),(2,3),(2,2)]
def truth(t):
    k=(t-20)/2.0; i=int(np.floor(k)); f=k-i; p0=tile_xy(*path[i%12]); p1=tile_xy(*path[(i+1)%12]); return p0+f*(p1-p0)
L=-2.5; m=(E[:,0]+L>=20)&(E[:,0]+L<=164); tt=E[m,0]; S=F[m]; T=np.array([truth(x+L) for x in tt]); err=np.linalg.norm(S-T,axis=1)
c=lambda v,d=2: f"{v:.{d}f}".replace(".",",")
rows=[dict(t_s=round(float(t-20),3), true_x=round(float(a[0]),3), true_y=round(float(a[1]),3), est_x=round(float(b[0]),3), est_y=round(float(b[1]),3), error_m=round(float(e),3)) for t,a,b,e in zip(tt,T,S,err)]

summ=[dict(method="sin filtro",median_m=round(float(np.median(err)),3),p80_m=round(float(np.percentile(err,80)),3),share_le_30cm=round(float(np.mean(err<=0.3)),3),share_le_60cm=round(float(np.mean(err<=0.6)),3),windows=int(len(err)),truth_shift_s=L)]
print(summ)
fig=plt.figure(figsize=(15,6.6)); gs=fig.add_gridspec(2,2,width_ratios=[1,1.35])
ax=fig.add_subplot(gs[:,0])
for k in range(6): ax.axvline(k*0.6,color="0.88",lw=0.8); ax.axhline(k*0.6,color="0.88",lw=0.8)
loop=np.array([tile_xy(*p) for p in path]+[tile_xy(*path[0])]); ax.plot(loop[:,0],loop[:,1],"-",color="0.2",lw=3,alpha=0.35,zorder=1)
Rw=E[m,1:3]
pts=S.reshape(-1,1,2); segs=np.concatenate([pts[:-1],pts[1:]],axis=1)
lc=LineCollection(segs,cmap="viridis",norm=plt.Normalize(0,144),lw=1.6,alpha=0.9); lc.set_array(tt[:-1]-20); ax.add_collection(lc)
cb=fig.colorbar(lc,ax=ax,fraction=0.04,pad=0.02); cb.set_label("tiempo (s)")
ax.plot(*CEN["A"],"ks",ms=11); ax.plot(*CEN["B"],"ks",ms=11); ax.text(1.5,-0.22,"AP A",ha="center"); ax.text(3.08,1.5,"AP B",va="center")
ax.set_xlim(-0.2,3.35); ax.set_ylim(-0.35,3.15); ax.set_aspect("equal"); ax.set_xlabel("x (m)"); ax.set_ylabel("y (m)")
ax.plot([],[],color="0.2",lw=3,alpha=0.35,label="recorrido real"); ax.plot([],[],color="tab:green",lw=1.6,label="estimado (color = tiempo)"); ax.legend(loc="upper right",fontsize=8.5,framealpha=0.9)
ax.set_title("Persona caminando: recorrido real y estimado",fontsize=11)
for k,lab in ((0,"x"),(1,"y")):
    a=fig.add_subplot(gs[k,1]); a.plot(tt-20,T[:,k],color="0.2",lw=2.5,alpha=0.5,label="real"); a.plot(tt-20,S[:,k],color="tab:green",lw=1.3,label="estimado")
    a.set_ylabel(f"{lab} (m)"); a.set_xlim(0,144); a.grid(alpha=0.3)
    if k==0: a.set_ylim(-0.2,2.85)
    if k==0: a.legend(loc="upper right",fontsize=9,ncol=2); a.set_title(f"Coordenadas frente al tiempo · error mediano {c(np.median(err))} m · {100*np.mean(err<=0.3):.0f} % a ≤ 0,3 m · {100*np.mean(err<=0.6):.0f} % a ≤ 0,6 m",fontsize=10)
    else: a.set_xlabel("tiempo desde el inicio del recorrido (s)")
fig.suptitle("Dos AP WiFi de 100 MHz sincronizados solo por la celda 5G · seguimiento de una persona caminando (6 vueltas, ~0,3 m/s)",fontsize=12)
fig.text(0.5,0.006,"Ventanas de 0,5 s (avance de 0,25 s) con filtro de mediana de 5 ventanas (1,25 s), sin Kalman.\nCalibración angular con las 16 baldosas de t13 (misma sesión, prueba distinta). Verdad: recorrido marcado con avisador (una baldosa cada 2 s), desplazada 2,5 s porque la persona\nllegaba a cada baldosa un paso después del pitido (desfase ajustado a posteriori). Prueba t14, 14-10-2026.",ha="center",fontsize=8,color="0.35")
fig.tight_layout(rect=(0,0.07,1,0.95)); fig.savefig("t14_trayectoria_mediana.png",dpi=110)
