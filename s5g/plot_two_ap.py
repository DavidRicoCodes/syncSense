#!/usr/bin/env python3
"""
Joint analysis of a two-AP run (x410_two_ap.py on AP A and AP B).

  e_AB = arrival of A's burst at B, in B's clock, minus (B's own PSS + offset)
  e_BA = arrival of B's burst at A, in A's clock, minus (A's own PSS + offset)

Two-way time transfer, per cycle (A's slot and B's slot are in the same 100 ms):
  (e_AB + e_BA) / 2 = mean hardware + air delay (TX pipeline + air + RX pipeline)
  (e_AB - e_BA) / 2 = offset between the time references of A and B, i.e. how
                      far apart the two APs really fire: mainly the difference
                      of the gNB->AP propagation and of the 5G receive chains
                      (plus any TX/RX hardware asymmetry between the two X410)

Example: python3 plot_two_ap.py two_ap_run1_A two_ap_run1_B
"""
import argparse
import json
import warnings
from pathlib import Path

import numpy as np

warnings.filterwarnings("ignore", message="Unable to import Axes3D")
import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.gridspec import GridSpec  # noqa: E402

CYCLE = 5


def load(d):
    s = json.loads((d / "summary.json").read_text())
    recs = [json.loads(line) for line in (d / "records.jsonl").open()]
    return s, recs


def per_cycle(recs, rx0, out_ns=1000.0):
    """cycle -> median sync error over the links of the first RX chain (robust)."""
    out = {}
    for r in recs:
        if r.get("role") != "RX" or r.get("phase") != "B_tx_window" or "obs" not in r:
            continue
        v = [d["sync_err_ns"] for d in r["obs"].get(str(rx0), []) if d.get("found") and "sync_err_ns" in d]
        if v:
            out[r["cycle"]] = (float(np.median(v)), r["unix_slot"])
    if out:
        med = np.median([v for v, _ in out.values()])
        out = {c: v for c, v in out.items() if abs(v[0] - med) <= out_ns}
    return out


def main():
    p = argparse.ArgumentParser()
    p.add_argument("dir_a", type=Path)
    p.add_argument("dir_b", type=Path)
    p.add_argument("--out", type=Path, default=None)
    a = p.parse_args()
    sA, rA = load(a.dir_a)
    sB, rB = load(a.dir_b)
    rx0 = sA["args"]["rx_chans"][0]
    eAB = per_cycle(rB, rx0)  # measured at B
    eBA = per_cycle(rA, rx0)  # measured at A
    common = sorted(set(eAB) & set(eBA))
    t0 = min([v[1] for v in eAB.values()] + [v[1] for v in eBA.values()])
    ab = np.array([eAB[c][0] for c in common])
    ba = np.array([eBA[c][0] for c in common])
    tc = np.array([eAB[c][1] - t0 for c in common])
    half_sum, half_diff = (ab + ba) / 2, (ab - ba) / 2

    resA, resB = sA["results"], sB["results"]
    numA, numB = resA["numbering"], resB["numbering"]
    txA = {r["n"] for r in rA if r["role"] == "TX" and r["state"] == "EXECUTED"}
    txB = {r["n"] for r in rB if r["role"] == "TX" and r["state"] == "EXECUTED"}
    heardB = {r["n"] for r in rB if r["role"] == "RX" and r.get("state") == "HEARD"}
    heardA = {r["n"] for r in rA if r["role"] == "RX" and r.get("state") == "HEARD"}
    coll = txA & txB

    lines = [
        f"Numbering (provisional, host time): PSS at {numA.get('pss_phase_in_grid_ms')} ms (A) / "
        f"{numB.get('pss_phase_in_grid_ms')} ms (B) of the 20 ms grid; mismatches A {numA.get('mismatches')} B {numB.get('mismatches')}",
        f"Clocks vs gNB: A {resA['tracker']['ppm']:+.3f} ppm, B {resB['tracker']['ppm']:+.3f} ppm "
        f"(difference {resA['tracker']['ppm'] - resB['tracker']['ppm']:+.3f} ppm)",
        f"A transmitted {len(txA)} bursts, B heard {len(txA & heardB)} ({100*len(txA & heardB)/max(len(txA),1):.2f} %) | "
        f"B transmitted {len(txB)}, A heard {len(txB & heardA)} ({100*len(txB & heardA)/max(len(txB),1):.2f} %)",
        f"Same SSB used by both (collisions): {len(coll)} | sentinel hits A {resA['sentinel']['n_hits']}, "
        f"B {resB['sentinel']['n_hits']} | late/time_error A {resA['tx']['states'].get('MISSED_LATE', 0)}+"
        f"{resA['async_events'].get('time_error', 0)}, B {resB['tx']['states'].get('MISSED_LATE', 0)}+"
        f"{resB['async_events'].get('time_error', 0)}",
        f"5G valid during TX: A {resA['nr_B_tx_window']['valid_pct']} %, B {resB['nr_B_tx_window']['valid_pct']} %",
    ]
    if len(common):
        lines += [
            f"e_AB (A->B, at B): mean {ab.mean():.1f} ns, std {ab.std():.1f} ns | e_BA (B->A, at A): mean "
            f"{ba.mean():.1f} ns, std {ba.std():.1f} ns   [{len(common)} cycles with both]",
            f"Two-way: hardware+air (half sum) {half_sum.mean():.1f} ± {half_sum.std():.1f} ns | "
            f"OFFSET BETWEEN APs (half difference) {half_diff.mean():+.1f} ± {half_diff.std():.1f} ns",
        ]
    print("\n".join(lines))

    plt.style.use("dark_background")
    fg, bg, panel, grid = "#e6edf3", "#0d1117", "#161b22", "#30363d"
    cA, cB, cS, cD = "#58a6ff", "#f778ba", "#3fb950", "#d29922"
    plt.rcParams.update({"axes.facecolor": panel, "figure.facecolor": bg, "axes.edgecolor": grid,
                         "grid.color": grid, "font.size": 9, "axes.titlesize": 10, "axes.titleweight": "bold"})
    fig = plt.figure(figsize=(18, 12.5))
    gs = GridSpec(3, 3, figure=fig, hspace=0.5, wspace=0.28, left=0.05, right=0.97, top=0.83, bottom=0.05)
    fig.suptitle("Two X410 APs, independent clocks, no backhaul, coordinated by the same 5G cell",
                 fontsize=15, fontweight="bold", x=0.05, ha="left")
    for i, ln in enumerate(lines):
        fig.text(0.05, 0.95 - i * 0.017, ln, fontsize=9, alpha=0.85,
                 color=cD if "OFFSET" in ln else fg)

    # agenda of one cycle
    ax = fig.add_subplot(gs[0, :])
    sa, sb = sA["args"]["slot_a"], sA["args"]["slot_b"]
    off = sA["args"]["slot_offset_ms"]
    blen = 1.85
    for s in range(CYCLE):
        x = s * 20
        ax.axvline(x, color=grid)
        ax.text(x, 1.05, f"SSB slot {s}", ha="center", fontsize=8)
        if s == sa:
            ax.add_patch(plt.Rectangle((x + off, 0.55), blen * 3, 0.35, color=cA))
            ax.text(x + off + 5, 0.72, "A TX", va="center", fontsize=9, color=cA)
            ax.add_patch(plt.Rectangle((x + off - 0.25, 0.1), blen * 3 + 0.5, 0.35, fill=False, ec=cB, ls="--"))
            ax.text(x + off + 5, 0.27, "B listens", va="center", fontsize=8, color=cB)
        elif s == sb:
            ax.add_patch(plt.Rectangle((x + off, 0.1), blen * 3, 0.35, color=cB))
            ax.text(x + off + 5, 0.27, "B TX", va="center", fontsize=9, color=cB)
            ax.add_patch(plt.Rectangle((x + off - 0.25, 0.55), blen * 3 + 0.5, 0.35, fill=False, ec=cA, ls="--"))
            ax.text(x + off + 5, 0.72, "A listens", va="center", fontsize=8, color=cA)
        else:
            ax.text(x + 8, 0.5, "sentinels", fontsize=7, alpha=0.5, ha="center")
    ax.set_xlim(-2, CYCLE * 20 + 2)
    ax.set_ylim(0, 1.2)
    ax.set_yticks([0.27, 0.72], ["AP B", "AP A"])
    ax.set_xlabel("time within the 100 ms cycle [ms] (bursts drawn 3x wider for visibility)")
    ax.set_title("Provisioned agenda: complementary slots, decided once, no negotiation")

    # time series
    ax = fig.add_subplot(gs[1, :2])
    if len(common):
        ax.plot(tc, ab - ab.mean(), ".", ms=3, color=cA, label=f"e_AB − mean ({ab.std():.1f} ns rms)")
        ax.plot(tc, ba - ba.mean(), ".", ms=3, color=cB, label=f"e_BA − mean ({ba.std():.1f} ns rms)")
        ax.plot(tc, half_diff - half_diff.mean(), "-", lw=0.8, color=cD,
                label=f"offset between APs − mean ({half_diff.std():.1f} ns rms)")
    ax.set_xlabel("time [s]")
    ax.set_ylabel("ns")
    ax.set_title("Inter-AP timing per cycle (each AP judging the other with its own 5G reference)")
    ax.grid(True, alpha=0.4)
    ax.legend(fontsize=8)

    ax = fig.add_subplot(gs[1, 2])
    if len(common):
        bins = np.linspace(-200, 200, 41)
        ax.hist(ab - ab.mean(), bins=bins, alpha=0.5, color=cA, label="e_AB")
        ax.hist(ba - ba.mean(), bins=bins, alpha=0.5, color=cB, label="e_BA")
        ax.hist(half_diff - half_diff.mean(), bins=bins, alpha=0.6, color=cD, label="offset A−B")
    ax.set_xlabel("deviation from mean [ns]")
    ax.set_title("Distributions")
    ax.grid(True, alpha=0.4)
    ax.legend(fontsize=8)

    # two-way bars
    ax = fig.add_subplot(gs[2, 0])
    if len(common):
        vals = [ab.mean(), ba.mean(), half_sum.mean()]
        ax.bar(["e_AB", "e_BA", "half sum\n(hw + air)"], vals, color=[cA, cB, cS])
        for i, v in enumerate(vals):
            ax.text(i, v, f"{v:.1f}", ha="center", va="bottom", fontsize=9)
        ax.set_ylim(min(vals) - 50, max(vals) + 50)
    ax.set_ylabel("ns")
    ax.set_title("Constant parts (hardware + air)")
    ax.grid(True, axis="y", alpha=0.4)

    ax = fig.add_subplot(gs[2, 1])
    if len(common):
        ax.bar(["offset between APs\n(half difference)"], [half_diff.mean()], yerr=[half_diff.std()],
               color=cD, capsize=8)
        ax.axhline(0, color=fg, lw=0.6)
        ax.text(0, half_diff.mean(), f"{half_diff.mean():+.1f} ± {half_diff.std():.1f} ns", ha="center",
                va="bottom", fontsize=11, fontweight="bold")
        ax.set_xlim(-1, 1)
    ax.set_ylabel("ns")
    ax.set_title("How far apart the two APs really fire\n(1 m of extra gNB distance ≈ 3.3 ns)")
    ax.grid(True, axis="y", alpha=0.4)

    # link levels
    ax = fig.add_subplot(gs[2, 2])
    txc, rxc = sA["args"]["tx_chans"], sA["args"]["rx_chans"]
    M = np.full((2 * len(rxc), len(txc)), np.nan)
    for blk, res in enumerate((resB, resA)):
        for i, r in enumerate(rxc):
            for j, t in enumerate(txc):
                v = res["links"].get(f"rx{r}_partner_tx{t}", {}).get("level_dbfs")
                if v is not None:
                    M[blk * len(rxc) + i, j] = v
    im = ax.imshow(M, cmap="viridis", aspect="auto")
    for i in range(M.shape[0]):
        for j in range(M.shape[1]):
            if np.isfinite(M[i, j]):
                ax.text(j, i, f"{M[i, j]:.0f}", ha="center", va="center", fontsize=8, color="w")
    ax.set_xticks(range(len(txc)), [f"TX ch{t}" for t in txc])
    ax.set_yticks(range(M.shape[0]), [f"B rx{r} ← A" for r in rxc] + [f"A rx{r} ← B" for r in rxc], fontsize=8)
    ax.axhline(len(rxc) - 0.5, color="w", lw=1)
    fig.colorbar(im, ax=ax, label="dBFS")
    ax.set_title("Received level per link (both directions)")

    out = a.out or a.dir_a.parent / f"two_ap_{a.dir_a.name}_{a.dir_b.name}.png"
    fig.savefig(out, dpi=100)
    print("Saved", out)


if __name__ == "__main__":
    main()
