import json, numpy as np
import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
R = json.load(open("scaling_results.json"))
c = lambda v, d=0: f"{v:.{d}f}".replace(".", ",")
col = {"S5G": "#1f77b4", "B1": "#d62728", "B2": "#2ca02c", "B3": "#9467bd", "B3r": "#8c564b"}
fig, ax = plt.subplots(2, 3, figsize=(17, 9.5))
V3 = json.load(open("airtime_v3.json"))
col.update({"IDEAL": "#ff7f0e"})
# (a) airtime, HE NDP at 100 Hz (model v3, fair 802.11bf assumptions)
A3 = [r for r in V3["airtime"] if r["waveform"] == "he_ndp_80us" and r["rate_hz"] == 100]; N = [r["N"] for r in A3]; a = ax[0, 0]
for key, lab, cc, ls in (("S5G", "S5G, guarda de 3 µs", col["S5G"], "-"), ("S5G_g50", "S5G con la ventana del banco (50 µs)", col["S5G"], "--"),
                          ("IDEAL", "ideal negociado (una TXOP por periodo)", col["IDEAL"], "-"), ("TB", "802.11bf TB (control a 24 Mb/s)", col["B3"], "-"),
                          ("TBrep", "TB + informes CSI por aire", col["B3r"], "-")):
    a.plot(N, [100 * min(1.2, r["used_" + key]) for r in A3], ls, marker="o", ms=3, color=cc, label=lab)
a.axhline(100, color="0.4", ls="--", lw=1); a.set_ylim(0, 120); a.set_xlabel("número de AP"); a.set_ylabel("tiempo de aire ocupado por el sensing (%)")
a.set_title("(a) Tiempo de aire, NDP HE a 100 Hz por AP\n(lo que queda bajo el 100 % es para comunicaciones)", fontsize=10); a.legend(fontsize=7.5); a.grid(alpha=.3)
# (b) max N vs sensing rate (HE NDP)
mN = {r["rate_hz"]: r for r in V3["maxN"] if r["waveform"] == "he_ndp_80us"}; rates = [50, 100, 200]; x = np.arange(3); w = .2; a = ax[0, 1]
for k, (lab, key, cc) in enumerate((("S5G (3 µs)", "S5G", col["S5G"]), ("ideal negociado", "IDEAL", col["IDEAL"]), ("802.11bf TB", "TB", col["B3"]), ("TB + informes", "TBrep", col["B3r"]))):
    v = [mN[r][key] for r in rates]; a.bar(x + (k - 1.5) * w, v, w, color=cc, label=lab)
    for i, vv in enumerate(v): a.text(x[i] + (k - 1.5) * w, vv + 2, str(vv), ha="center", fontsize=7)
a.set_xticks(x); a.set_xticklabels([f"{r} Hz" for r in rates]); a.set_ylabel("AP máximos con el canal al 100 %"); a.set_title("(b) AP máximos por tasa de sensing (NDP HE)", fontsize=10); a.legend(fontsize=8); a.grid(alpha=.3, axis="y")
# (c) B1 collisions
a = ax[0, 2]
for wv, lab, ls in (("zc_0.4ms", "B1, burst ZC de 0,4 ms (18 % medido con 2 AP)", "-"), ("he_ndp_80us", "B1, NDP HE de 80 µs", "--")):
    rr = [r for r in V3["b1"] if r["waveform"] == wv]; a.plot([r["N"] for r in rr], [100 * r["b1_lost"] for r in rr], ls, marker="o", ms=3, color=col["B1"], label=lab)
a.plot(range(2, 17), [0] * 15, "-o", ms=3, color=col["S5G"], label="S5G: turnos sin solape")
a.set_ylim(-3, 100); a.set_xlabel("número de AP"); a.set_ylabel("bursts perdidos por colisión (%)"); a.set_title("(c) Colisiones sin coordinación", fontsize=10); a.legend(fontsize=8); a.grid(alpha=.3)
# (d) sync error vs hops
S = [r for r in R["sync_vs_hops"] if r["hops"] >= 1]; h = [r["hops"] for r in S]; a = ax[1, 0]
a.plot(h, [r["b2_calibrated_ns"] for r in S], "-o", color=col["B2"], label="B2 por saltos, calibrado en los dos sentidos (4,9 ns/salto)")
a.plot(h, [r["b2_uncalibrated_ns"] for r in S], "--o", color=col["B2"], alpha=.6, label="B2 por saltos, sin calibrar latencias (±175 ns/salto)")
a.axhline(S[0]["s5g_ns"], color=col["S5G"], label="S5G, ruido p95 (σ = 6,0 ns medido en t16; no depende de N)")
gb = R["s5g_gnb_bias"]
a.axhline(gb["interfering_pairs"]["p95_ns"], color=col["S5G"], ls="--", alpha=.7, label=f"S5G, sesgo fijo por distancia al gNB, pares que se interfieren (p95 {c(gb['interfering_pairs']['p95_ns'])} ns)")
a.axhline(gb["pairs_lt15m"]["p95_ns"], color=col["S5G"], ls=":", alpha=.9, label=f"S5G, mismo sesgo, pares a < 15 m (p95 {c(gb['pairs_lt15m']['p95_ns'])} ns)")
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
fig.suptitle("Modelo de escalado v3 · S5G frente a B1, B2, 802.11bf TB y el ideal negociado · mismo sondeo para todos, supuestos justos, mejores valores medidos", fontsize=12)
fig.tight_layout(rect=(0, 0, 1, .96)); fig.savefig("scaling_model.png", dpi=100)
