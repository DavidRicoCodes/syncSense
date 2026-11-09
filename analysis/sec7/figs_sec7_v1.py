import json, numpy as np, matplotlib
matplotlib.use("Agg"); import matplotlib.pyplot as plt
plt.rcParams.update({"font.size": 8, "font.family": "serif", "axes.linewidth": .6, "lines.linewidth": 1.0, "legend.fontsize": 7, "xtick.labelsize": 7, "ytick.labelsize": 7})
D = json.load(open("sec7_stats.json")); OUT = "/home/nextnet/overleaf_paper/figures/"
# --- Fig. 3: two-way inter-AP offset, 1-s medians and p10-p90
lab = {"t18_zc80_g3": "(a) Empty room, 80 µs burst, G = 3 µs (t18)", "t11_nrw4": "(b) Empty room, 400 µs burst (t11)", "t16_tiles": "(c) Person on 16 tiles, another day (t16)"}
fig, ax = plt.subplots(3, 1, figsize=(3.5, 3.6), sharey=True)
for a, run in zip(ax, ("t18_zc80_g3", "t11_nrw4", "t16_tiles")):
    s = D["series"][run]; t = np.array(s["t"]); m = np.array(s["med"]); lo = np.array(s["p10"]); hi = np.array(s["p90"])
    m0 = np.median(m); a.fill_between(t, lo - m0, hi - m0, color="#9ecae1", lw=0, label="p10–p90 per second"); a.plot(t, m - m0, color="#08519c", lw=.9, label="median per second")
    a.set_xlim(0, t.max()); a.set_ylim(-60, 60); a.grid(alpha=.3, lw=.4); a.set_title(lab[run], fontsize=7.5, pad=2)
    if run == "t16_tiles":
        for i in range(16): a.axvspan(25 + 25 * i, 45 + 25 * i, color="0.85", lw=0, zorder=0)
ax[1].set_ylabel("Inter-AP offset $\\hat e_{AB}$ (ns, median removed)"); ax[2].set_xlabel("Time (s)"); ax[0].legend(loc="upper right", ncol=2, frameon=False, fontsize=6)
fig.tight_layout(pad=.3, h_pad=.4); fig.savefig(OUT + "sync_timeseries.pdf"); fig.savefig("sync_timeseries.png", dpi=160)
# --- Fig. 4: CCDF of the per-burst timing deviation, all runs
fig, a = plt.subplots(figsize=(3.5, 2.3))
sty = {"t10_tiles": ("5G antenna of B degraded (t10)", "#d62728", "--")}
cols = plt.cm.viridis(np.linspace(0, .85, 7)); k = 0
for run in ("t11_nrw4", "t12_fsync", "t13_tiles_fs", "t14_track", "t15c_check", "t16_tiles", "t18_zc80_g3", "t10_tiles"):
    x = np.sort(np.array(D["cdf"][run])); p = 1 - np.arange(len(x)) / len(x)
    if run in sty: l, c, ls = sty[run]
    else: l, c, ls = run.split("_")[0], cols[k], "-"; k += 1
    a.loglog(np.maximum(x, .1), p, ls, color=c, label=l, lw=.9)
for v, t_ in ((100, "0.1 µs"), (3000, "$G$ = 3 µs")):
    a.axvline(v, color="0.3", lw=.6, ls=":"); a.text(v * 1.1, 2e-4, t_, fontsize=6.5, rotation=90, va="bottom")
a.set_xlim(.5, 1e4); a.set_ylim(1e-5, 1.2); a.set_xlabel("Per-burst timing deviation (ns)"); a.set_ylabel("CCDF"); a.grid(alpha=.3, which="both", lw=.3)
a.legend(ncol=2, frameon=False, fontsize=6, loc="lower left"); fig.tight_layout(pad=.3); fig.savefig(OUT + "sync_ccdf.pdf"); fig.savefig("sync_ccdf.png", dpi=160)
# --- Fig. 5: inter-AP CFO per second, without (t11) and with (t12) 5G frequency synchronization
fig, a = plt.subplots(figsize=(3.5, 2.1))
for run, ls, nm in (("t11_nrw4", "-", "no f-sync (t11)"), ("t12_fsync", "-", "5G f-sync (t12)")):
    for dn, c in (("A->B", "#08519c"), ("B->A", "#d95f0e")):
        s = D["cfo"][run][dn]; a.plot(s["t"], s["cfo"], ls, color=c, alpha=1 if run == "t12_fsync" else .55, lw=.8, label=f"{dn.replace('->', '→')}, {nm}")
a.axhline(0, color="0.4", lw=.5); a.set_xlim(0, 180); a.set_ylim(-420, 420); a.set_xlabel("Time (s)"); a.set_ylabel("Inter-AP CFO (Hz)"); a.grid(alpha=.3, lw=.4)
a.legend(ncol=2, frameon=False, fontsize=6, loc="center", bbox_to_anchor=(0.5, 0.28)); fig.tight_layout(pad=.3); fig.savefig(OUT + "cfo_fsync.pdf"); fig.savefig("cfo_fsync.png", dpi=160)
print("ok")
