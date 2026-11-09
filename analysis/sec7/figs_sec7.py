import json, numpy as np, matplotlib
matplotlib.use("Agg"); import matplotlib.pyplot as plt
plt.rcParams.update({"font.size": 8, "font.family": "serif", "axes.linewidth": .6, "lines.linewidth": 1.0, "legend.fontsize": 6.5, "xtick.labelsize": 7, "ytick.labelsize": 7})
D = json.load(open("sec7_stats.json")); OUT = "/home/nextnet/overleaf_paper/figures/"
COND = {"t18_zc80_g3": ("Empty, 80 µs burst (t18)", "#08306b", "-"), "t11_nrw4": ("Empty (t11)", "#2171b5", "-"), "t15c_check": ("Empty (t15c)", "#6baed6", "-"),
        "t12_fsync": ("Empty, then walking (t12)", "#41ab5d", "-"), "t14_track": ("Walking (t14)", "#238b45", "--"), "t9_tiles": ("Tiles (t9)", "#a1d99b", "-"),
        "t13_tiles_fs": ("Tiles (t13)", "#74c476", "-"), "t16_tiles": ("Tiles (t16)", "#006d2c", "-"), "t10_tiles": ("Degraded 5G antenna (t10)", "#d62728", "--")}
# --- time series of the two-way offset (no median removal)
lab = {"t18_zc80_g3": "(a) Empty room, 80 µs burst, G = 3 µs (t18)", "t11_nrw4": "(b) Empty room, 400 µs burst (t11)", "t16_tiles": "(c) Person on 16 tiles, another day (t16)"}
fig, ax = plt.subplots(3, 1, figsize=(3.5, 3.7), sharey=True)
for a, run in zip(ax, ("t18_zc80_g3", "t11_nrw4", "t16_tiles")):
    s = D["series"][run]; t = np.array(s["t"])
    if run == "t16_tiles":
        for i in range(16): a.axvspan(25 + 25 * i, 45 + 25 * i, color="0.88", lw=0, zorder=0)
        a.axvspan(430, 480, color="0.75", lw=0, zorder=0, hatch="////", fill=False)
    a.fill_between(t, s["p10"], s["p90"], color="#9ecae1", lw=0, label="p10–p90 per second"); a.plot(t, s["med"], color="#08519c", lw=.9, label="median per second")
    a.set_xlim(0, t.max()); a.set_ylim(-80, 120); a.grid(alpha=.3, lw=.4); a.set_title(lab[run], fontsize=7.5, pad=2)
ax[1].set_ylabel("Inter-AP offset $\\hat e_{AB}$ (ns)"); ax[2].set_xlabel("Time (s)"); ax[0].legend(loc="upper right", ncol=2, frameon=False, fontsize=6)
fig.tight_layout(pad=.3, h_pad=.4); fig.savefig(OUT + "sync_timeseries.pdf"); fig.savefig("sync_timeseries.png", dpi=160)
# --- CCDF of |l| (deviation from the calibrated expected arrival), full tail
fig, a = plt.subplots(figsize=(3.5, 2.4))
for run in ("t18_zc80_g3", "t11_nrw4", "t15c_check", "t12_fsync", "t14_track", "t9_tiles", "t13_tiles_fs", "t16_tiles", "t10_tiles"):
    c = D["ccdf"][run]; l, col, ls = COND[run]; a.loglog(np.maximum(c["x"], .1), c["p"], ls, color=col, label=l, lw=1.1 if run == "t18_zc80_g3" else .8)
for v, t_ in ((100, "0.1 µs"), (3000, "$G$ = 3 µs")):
    a.axvline(v, color="0.3", lw=.6, ls=":"); a.text(v * 1.12, 3e-3, t_, fontsize=6.5, rotation=90, va="bottom")
a.set_xlim(.5, 1e4); a.set_ylim(3e-7, 1.3); a.set_xlabel("Deviation from the expected arrival, $|\\ell|$ (ns)"); a.set_ylabel("CCDF"); a.grid(alpha=.3, which="both", lw=.3)
a.legend(ncol=2, frameon=False, fontsize=5.6, loc="lower left"); fig.tight_layout(pad=.3); fig.savefig(OUT + "sync_ccdf.pdf"); fig.savefig("sync_ccdf.png", dpi=160)
# --- time deviation
fig, a = plt.subplots(figsize=(3.5, 2.2))
for run in ("t18_zc80_g3", "t11_nrw4", "t15c_check", "t16_tiles", "t10_tiles"):
    td = D["tdev"][run]; x = np.array([float(k) for k in td]); y = np.array([td[k] for k in td]); m = np.isfinite(y)
    l, col, ls = COND[run]; a.loglog(x[m], y[m], ls, marker="o", ms=2.5, color=col, label=l, lw=.9)
a.set_xlabel("Averaging time $\\tau$ (s)"); a.set_ylabel("Time deviation (ns)"); a.grid(alpha=.3, which="both", lw=.3); a.legend(frameon=False, fontsize=5.8, ncol=2, loc="upper right")
a.set_ylim(.1, 400); fig.tight_layout(pad=.3); fig.savefig(OUT + "sync_tdev.pdf"); fig.savefig("sync_tdev.png", dpi=160)
# --- CFO: (a) without/with f-sync, (b) zoom on the inter-AP (antisymmetric) term
fig, ax = plt.subplots(2, 1, figsize=(3.5, 3.2), gridspec_kw=dict(height_ratios=[1.2, 1]))
a = ax[0]
for run, nm in (("t11_nrw4", "no f-sync (t11)"), ("t12_fsync", "5G f-sync (t12)")):
    for dn, c in (("A->B", "#08519c"), ("B->A", "#d95f0e")):
        s = D["cfo"][run][dn]; a.plot(s["t"], s["cfo"], color=c, alpha=1 if run == "t12_fsync" else .5, lw=.8, label=f"{dn.replace('->', '→')}, {nm}")
a.axhline(0, color="0.4", lw=.5); a.set_xlim(0, 180); a.set_ylim(-420, 420); a.set_ylabel("Inter-AP CFO (Hz)"); a.grid(alpha=.3, lw=.4)
a.set_title("(a) Measured CFO per direction", fontsize=7.5, pad=2); a.legend(ncol=2, frameon=False, fontsize=5.8, loc="center", bbox_to_anchor=(0.5, 0.3))
a = ax[1]
for run, col, nm in (("t12_fsync", "#08519c", "t12"), ("t16_tiles", "#006d2c", "t16 (another day)"), ("t18_zc80_g3", "#7a0177", "t18 (80 µs burst)")):
    s = D["cfo"][run]["anti"]; t = np.array(s["t"]); v = np.array(s["v"]); k = 10; vv = np.convolve(v, np.ones(k) / k, mode="valid")
    a.plot(t[k - 1:], vv, color=col, lw=.9, label=nm)
a.axhline(0, color="0.4", lw=.5); a.set_xlim(0, 520); a.set_ylim(-5, 25); a.set_xlabel("Time (s)"); a.set_ylabel("Inter-AP term (Hz)"); a.grid(alpha=.3, lw=.4)
a.set_title("(b) Inter-AP term with 5G f-sync (10 s mean)", fontsize=7.5, pad=2); a.legend(frameon=False, fontsize=6, ncol=3, loc="upper right")
fig.tight_layout(pad=.3, h_pad=.5); fig.savefig(OUT + "cfo_fsync.pdf"); fig.savefig("cfo_fsync.png", dpi=160)
print("ok")
