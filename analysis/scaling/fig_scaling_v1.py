import json, numpy as np
import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
R = json.load(open("scaling_results.json"))
c = lambda v, d=0: f"{v:.{d}f}".replace(".", ",")
col = {"S5G": "#1f77b4", "B1": "#d62728", "B2": "#2ca02c", "B3": "#9467bd", "B3r": "#8c564b"}
fig, ax = plt.subplots(2, 3, figsize=(17, 9.5))
# (a) airtime and communications time vs N
A = R["airtime"]; N = [r["N"] for r in A]; a = ax[0, 0]
a.plot(N, [100 * min(1.2, r["S5G_sensing_airtime"]) for r in A], "-o", color=col["S5G"], label="S5G (y B2 mientras todos sensan): solo señal")
a.plot(N, [100 * min(1.2, r["B3_sensing_airtime"]) for r in A], "-s", color=col["B3"], label="802.11bf TB: señal + control")
a.plot(N, [100 * min(1.2, r["B3r_sensing_airtime"]) for r in A], "-^", color=col["B3r"], label="802.11bf TB + informes mínimos")
a.axhline(100, color="0.4", ls="--", lw=1); a.set_ylim(0, 120); a.set_xlabel("número de AP"); a.set_ylabel("tiempo de aire ocupado por el sensing (%)")
a.set_title("(a) Tiempo de aire a 200 Hz por AP\n(lo que queda por debajo del 100 % es para comunicaciones)", fontsize=10); a.legend(fontsize=8); a.grid(alpha=.3)
# (b) max N vs sensing rate
mN = R["maxN"]; rates = [50, 100, 200]; x = np.arange(3); w = .27; a = ax[0, 1]
for k, (lab, key, cc) in enumerate((("S5G", "S5G", col["S5G"]), ("802.11bf TB", "B3", col["B3"]), ("802.11bf TB + informes", "B3reports", col["B3r"]))):
    v = [mN[f"{key}@{r}Hz"] for r in rates]; a.bar(x + (k - 1) * w, v, w, color=cc, label=lab)
    for i, vv in enumerate(v): a.text(x[i] + (k - 1) * w, vv + .5, str(vv), ha="center", fontsize=8)
a.set_xticks(x); a.set_xticklabels([f"{r} Hz" for r in rates]); a.set_ylabel("AP máximos con el canal al 100 %"); a.set_title("(b) Número máximo de AP según la tasa de sensing", fontsize=10); a.legend(fontsize=8); a.grid(alpha=.3, axis="y")
# (c) B1 collisions
Bc = R["b1_collisions"]; a = ax[0, 2]
a.plot([r["N"] for r in Bc], [100 * r["b1_lost_fraction"] for r in Bc], "-o", color=col["B1"], label="B1: relojes libres, busca y sigue")
a.plot([r["N"] for r in Bc], [0 if r["s5g_lost_fraction"] is not None else np.nan for r in Bc], "-o", color=col["S5G"], label="S5G: turnos sin solape (hasta 10 AP a 200 Hz)")
a.set_ylim(-3, 100); a.set_xlabel("número de AP"); a.set_ylabel("bursts perdidos por colisión (%)"); a.set_title("(c) Colisiones sin coordinación\n(medido con 2 AP: 18 % de fases ciegas, apagones de ~45 s)", fontsize=10); a.legend(fontsize=8); a.grid(alpha=.3)
# (d) sync error vs hops
S = [r for r in R["sync_vs_hops"] if r["hops"] >= 1]; h = [r["hops"] for r in S]; a = ax[1, 0]
a.plot(h, [r["b2_calibrated_ns"] for r in S], "-o", color=col["B2"], label="B2 por saltos, calibrado en los dos sentidos (4,9 ns/salto)")
a.plot(h, [r["b2_uncalibrated_ns"] for r in S], "--o", color=col["B2"], alpha=.6, label="B2 por saltos, sin calibrar latencias (±175 ns/salto)")
a.axhline(S[0]["s5g_ns"], color=col["S5G"], label="S5G, ruido p95 (σ = 6,0 ns medido en t16; no depende de N)")
a.axhline(R["s5g_gnb_bias"]["p95_ns"], color=col["S5G"], ls="--", alpha=.6, label=f"S5G, sesgo fijo por distancia al gNB (p95 {c(R['s5g_gnb_bias']['p95_ns'])} ns, calibrable)")
a.set_xlim(.5, 8.5); a.set_yscale("log"); a.set_xlabel("saltos hasta el líder (B2)"); a.set_ylabel("error de sincronización, p95 (ns)"); a.set_title("(d) Error temporal frente a saltos", fontsize=10); a.legend(fontsize=7.5); a.grid(alpha=.3, which="both")
# (e) interfering pairs that B2 cannot coordinate
C82, C95 = R["conflicts_m82"], R["conflicts_m95"]; a = ax[1, 1]
a.plot([r["N"] for r in C82], [100 * r["deployments_with_conflict"] for r in C82], "-o", color=col["B2"], label="B2 con tramas WiFi estándar (−82 dBm)")
a.plot([r["N"] for r in C95], [100 * r["deployments_with_conflict"] for r in C95], "--o", color=col["B2"], alpha=.6, label="B2 con detección por correlación (−95 dBm)")
a.plot([r["N"] for r in C82], [0] * len(C82), "-o", color=col["S5G"], label="S5G (basta la cobertura de la celda)")
a.set_ylim(-3, 60); a.set_xlabel("número de AP en una planta de 60 × 30 m con paredes de 7 dB"); a.set_ylabel("despliegues con AP que se interfieren\nsin poder coordinarse (%)"); a.set_title("(e) AP que se molestan pero no se oyen lo bastante", fontsize=10); a.legend(fontsize=8); a.grid(alpha=.3)
# (f) coordination frames and leader failure
F = R["frames"]; a = ax[1, 2]
a.plot([r["N"] for r in F], [r["b3_frames_per_s"] for r in F], "-s", color=col["B3"], label="802.11bf TB: tramas de control por segundo")
a.plot([r["N"] for r in F], [r["b2_setup_frames"] for r in F], "-o", color=col["B2"], label="B2/802.11bn: tramas de negociación al montar la red")
a.plot([r["N"] for r in F], [0] * len(F), "-o", color=col["S5G"], label="S5G: 0 (turno por regla sobre el SFN)")
a.set_yscale("symlog", linthresh=10); a.set_xlabel("número de AP"); a.set_ylabel("tramas de coordinación"); a.grid(alpha=.3, which="both")
lf = R["leader_failure"]["b2_outage_s"]
a.legend(fontsize=8, loc="center right", bbox_to_anchor=(1, .3)); a.set_title(f"(f) Coordinación y caída del líder\nB2 queda ≥ {c(lf['total_min'], 2)} s sin sincronización por cada caída; S5G no tiene líder", fontsize=10)
fig.suptitle("Modelo de escalado con N AP · S5G frente a B1 (libres), B2 (seguidor WiFi) y 802.11bf TB · entradas: mejores valores medidos en el banco X410", fontsize=12)
fig.tight_layout(rect=(0, 0, 1, .96)); fig.savefig("scaling_model.png", dpi=100)
