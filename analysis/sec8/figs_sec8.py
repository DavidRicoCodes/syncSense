import json, os, sys, numpy as np, matplotlib
matplotlib.use("Agg"); import matplotlib.pyplot as plt
from matplotlib.collections import LineCollection
plt.rcParams.update({"font.size": 8, "font.family": "serif", "axes.linewidth": .6, "legend.fontsize": 6.5, "xtick.labelsize": 7, "ytick.labelsize": 7})
D = json.load(open("sec8_stats.json")); OUT = "/home/nextnet/overleaf_paper/figures/"
A, B = np.array(D["geom"]["A"]), np.array(D["geom"]["B"])
def floor(a):
    for k in range(6): a.axvline(k * .6, color="0.9", lw=.5, zorder=0); a.axhline(k * .6, color="0.9", lw=.5, zorder=0)
    a.plot(*A, "ks", ms=6); a.plot(*B, "ks", ms=6); a.text(A[0] + .15, A[1] + .18, "AP A", va="center", fontsize=6.5); a.text(B[0] + .1, B[1], "AP B", va="center", fontsize=6.5)
    a.set_xlim(-.15, 3.45); a.set_ylim(-.35, 3.45); a.set_aspect("equal"); a.set_xlabel("x (m)")
# --- localization, t10 (pre-registered) and t16 (another day), both directions
fig, ax = plt.subplots(1, 2, figsize=(3.5, 2.05), sharey=True)
for a, (run, ttl) in zip(ax, (("t10", "(a) t10, both directions"), ("t16", "(b) t16 (another day)"))):
    floor(a); rows = D["loc"][f"{run}|both"]; e = np.array([r["err"] for r in rows])
    for r in rows:
        t, p = np.array(r["true"]), np.array(r["est"]); a.plot([t[0], p[0]], [t[1], p[1]], "-", color="#d95f0e", lw=.7, zorder=2)
    a.plot([r["true"][0] for r in rows], [r["true"][1] for r in rows], "o", mfc="none", mec="k", ms=4, mew=.6, zorder=3, label="true tile")
    a.plot([r["est"][0] for r in rows], [r["est"][1] for r in rows], ".", color="#d95f0e", ms=4, zorder=4, label="estimate")
    a.set_title(f"{ttl}\nmedian {np.median(e):.2f} m, max {e.max():.2f} m", fontsize=7, pad=2)
ax[0].set_ylabel("y (m)"); ax[0].legend(loc="upper left", frameon=False, fontsize=5.6, handletextpad=.2, borderaxespad=.05, ncol=2, columnspacing=.6)
fig.tight_layout(pad=.35, w_pad=.3); fig.savefig(OUT + "loc_tiles.pdf"); fig.savefig("loc_tiles.png", dpi=170)
# --- error vs observation window
sw = D["shortwin"]; fig, ax = plt.subplots(1, 2, figsize=(3.5, 1.75))
cols = {"t10": "#08519c", "t9": "#6baed6", "t16": "#006d2c"}; labs = {"t10": "t10", "t9": "t9", "t16": "t16 (other day)"}
for run in ("t10", "t9", "t16"):
    r = sorted([x for x in sw if x["run"] == run and x["mode"] == "both" and x["window_s"] <= 5], key=lambda x: x["window_s"]); w = [x["window_s"] for x in r]
    ax[0].semilogx(w, [x["median"] for x in r], "-o", ms=2.5, color=cols[run], lw=.9, label=labs[run]); ax[1].semilogx(w, [100 * x["le30"] for x in r], "-o", ms=2.5, color=cols[run], lw=.9)
ax[0].set_ylabel("Median error (m)"); ax[0].set_ylim(0, .25); ax[1].set_ylabel("Within 0.3 m (%)"); ax[1].set_ylim(70, 101)
for a in ax: a.set_xlabel("Window (s)"); a.grid(alpha=.3, which="both", lw=.3); a.set_xticks([.5, 1, 2, 5]); a.set_xticklabels(["0.5", "1", "2", "5"])
ax[0].legend(frameon=False, fontsize=5.8, loc="lower right"); fig.tight_layout(pad=.2, w_pad=.6); fig.savefig(OUT + "loc_window.pdf"); fig.savefig("loc_window.png", dpi=170)
# --- bistatic range
fig, a = plt.subplots(figsize=(3.5, 2.1))
for run, mk in (("t10", "o"), ("t16", "s")):
    P = D["range"][run]["points"]; real = [p["real"] for p in P]
    a.plot(real, [p["ab"] for p in P], mk, ms=3.2, mfc="#08519c" if run == "t10" else "none", mec="#08519c", mew=.6, label=f"A→B, {run}")
    a.plot(real, [p["ba"] for p in P], mk, ms=3.2, mfc="#d95f0e" if run == "t10" else "none", mec="#d95f0e", mew=.6, label=f"B→A, {run}")
a.plot([0, 4.2], [0, 4.2], "k--", lw=.6); a.set_xlim(0, 4.2); a.set_ylim(0, 4.2); a.set_xlabel("True excess bistatic path (m)"); a.set_ylabel("Estimated (m)"); a.grid(alpha=.3, lw=.3)
a.legend(frameon=False, fontsize=6, ncol=2, loc="upper left"); fig.tight_layout(pad=.2); fig.savefig(OUT + "range_bistatic.pdf"); fig.savefig("range_bistatic.png", dpi=170)
# --- walking target (t14), median-filtered 0.5 s windows
T14 = os.path.expanduser("~/sync_backups/x410_two_ap_results_20261014/analysis/t14"); sys.path.insert(0, T14); sys.modules.pop("t9c", None)
from t9c import tile_xy
Bt = json.load(open(f"{T14}/better_est.json")); E = np.array(Bt["E"]); F = np.array(json.load(open(f"{T14}/median5.json"))["med"])
path = [(1, 2), (1, 3), (1, 4), (1, 5), (2, 5), (3, 5), (4, 5), (4, 4), (4, 3), (3, 3), (2, 3), (2, 2)]
def truth(t):
    k = (t - 20) / 2.0; i = int(np.floor(k)); f = k - i; p0 = tile_xy(*path[i % 12]); p1 = tile_xy(*path[(i + 1) % 12]); return p0 + f * (p1 - p0)
L = -2.5; m = (E[:, 0] + L >= 20) & (E[:, 0] + L <= 164); tt = E[m, 0]; S = F[m]; Tr = np.array([truth(x + L) for x in tt]); err = np.linalg.norm(S - Tr, axis=1)
fig, a = plt.subplots(figsize=(3.5, 2.6)); floor(a)
loop = np.array([tile_xy(*p) for p in path] + [tile_xy(*path[0])]); a.plot(loop[:, 0], loop[:, 1], "-", color="0.2", lw=2.5, alpha=.3, zorder=1, label="true path")
pts = S.reshape(-1, 1, 2); lc = LineCollection(np.concatenate([pts[:-1], pts[1:]], 1), cmap="viridis", norm=plt.Normalize(0, 144), lw=1.0); lc.set_array(tt[:-1] - 20); a.add_collection(lc)
cb = fig.colorbar(lc, ax=a, fraction=.04, pad=.02); cb.set_label("Time (s)", fontsize=7); cb.ax.tick_params(labelsize=6)
a.plot([], [], color="tab:green", lw=1, label="estimate"); a.legend(loc="upper right", frameon=False, fontsize=6); a.set_ylabel("y (m)")
fig.tight_layout(pad=.2); fig.savefig(OUT + "walk_t14.pdf"); fig.savefig("walk_t14.png", dpi=170)
json.dump(dict(median=float(np.median(err)), le30=float(np.mean(err <= .3)), le60=float(np.mean(err <= .6)), n=int(len(err))), open("walk_t14_stats.json", "w"))
print("walk median %.2f le30 %.0f%% le60 %.0f%% n %d" % (np.median(err), 100 * np.mean(err <= .3), 100 * np.mean(err <= .6), len(err)))
# --- walking target (t14), v4: 0.5 s windows (AP exclusion zone) + constant-velocity Kalman with light RTS smoothing (offline)
EX14 = f"{T14}/excl"; Bx = json.load(open(f"{EX14}/better_est_excl.json")); E4 = np.array(Bx["E"]); C4 = json.load(open(f"{EX14}/cands_excl.json"))
S4all = np.array(C4["Kalman+RTS σ=0.25 q=1.5"]); Kc = np.array(C4["Kalman causal σ=0.25 q=0.6"])
m4 = (E4[:, 0] + L >= 20) & (E4[:, 0] + L <= 164); t4 = E4[m4, 0] - 20; S4 = S4all[m4]; R4 = E4[m4, 1:3]; T4 = np.array([truth(x + L) for x in E4[m4, 0]])
e4 = np.linalg.norm(S4 - T4, axis=1); ec = np.linalg.norm(Kc[m4] - T4, axis=1); er = np.linalg.norm(R4 - T4, axis=1)
fig = plt.figure(figsize=(7.16, 2.45)); gs = fig.add_gridspec(2, 4, width_ratios=[1, .045, .36, 1.75], hspace=.12, wspace=.05)
a = fig.add_subplot(gs[:, 0]); floor(a); a.set_xlim(-.15, 3.45); a.set_ylim(-.35, 3.2)
a.plot(loop[:, 0], loop[:, 1], "-", color="0.2", lw=2.5, alpha=.3, zorder=1, label="true path")
a.scatter(R4[:, 0], R4[:, 1], s=2, color="tab:green", alpha=.25, lw=0, zorder=2, label="0.5 s window")
pts = S4.reshape(-1, 1, 2); lc = LineCollection(np.concatenate([pts[:-1], pts[1:]], 1), cmap="viridis", norm=plt.Normalize(0, 144), lw=1.0); lc.set_array(t4[:-1]); a.add_collection(lc)
cb = fig.colorbar(lc, cax=fig.add_subplot(gs[:, 1])); cb.set_label("Time (s)", fontsize=7); cb.ax.tick_params(labelsize=6)
a.plot([], [], color="tab:green", lw=1, label="tracked"); a.legend(loc="upper right", frameon=False, fontsize=5.6, handlelength=1.2); a.set_ylabel("y (m)")
for k, lab in ((0, "x (m)"), (1, "y (m)")):
    b = fig.add_subplot(gs[k, 3]); b.plot(t4, T4[:, k], color="0.2", lw=2, alpha=.4); b.scatter(t4, R4[:, k], s=1.5, color="tab:green", alpha=.3, lw=0); b.plot(t4, S4[:, k], color="tab:green", lw=.8)
    b.set_ylabel(lab); b.set_xlim(0, 144); b.grid(alpha=.3, lw=.3)
    if k == 0: b.tick_params(labelbottom=False)
    else: b.set_xlabel("Time (s)")
fig.subplots_adjust(left=.065, right=.99, bottom=.16, top=.97); fig.savefig(OUT + "walk_t14_v4.pdf"); fig.savefig("walk_t14_v4.png", dpi=170)
w4 = dict(median=float(np.median(e4)), le30=float(np.mean(e4 <= .3)), le60=float(np.mean(e4 <= .6)), causal_median=float(np.median(ec)), raw_median=float(np.median(er)), n=int(len(e4)))
json.dump(w4, open("walk_t14_v4_stats.json", "w")); print("walk v4", {k: round(v, 3) for k, v in w4.items()})
