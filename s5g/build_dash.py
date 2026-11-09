#!/usr/bin/env python3
"""Builds the dashboard datasets (JSON row files) from the saved runs."""
import json, sys, datetime as dt
from pathlib import Path
import numpy as np

sys.argv = ["x"]
import sense_doppler as sd

C = 299792458.0
OUT = Path("dash"); OUT.mkdir(exist_ok=True)
A_ = sd.parse_args()

def save(name, rows):
    (OUT / f"{name}.json").write_text(json.dumps(rows, separators=(",", ":")))
    print(name, len(rows), "rows", (OUT / f"{name}.json").stat().st_size // 1024, "KB")

def r(x, n=2):
    return None if x is None or (isinstance(x, float) and not np.isfinite(x)) else round(float(x), n)

def summ(d): return json.loads((Path(d) / "summary.json").read_text())

RUNS = [  # key, label, tanda
    ("sense_L_empty", "Tanda 0 · vacía 2,45 GHz", "0"),
    ("sense_L_walk", "Tanda 0 · andando 2,45 GHz", "0"),
    ("sense_L_cables", "Tanda 1 · cables pegados", "1"),
    ("t2_check3", "Tanda 3 · comprobación 3 min", "3"),
    ("t3_empty", "Tanda 3 · vacía", "3"),
    ("t3_walk", "Tanda 3 · andando", "3"),
]
DIRS = [("A→B", "B", "A"), ("B→A", "A", "B")]   # name, rx folder suffix, tx suffix

def clean_h(z):
    """CFO-corrected, pulse-averaged, common-phase aligned snapshots + mask of good bursts."""
    h = z["h"].astype(np.complex128)
    dt_p = int(z["pulse_spacing"]) / float(z["rate"])
    cfo_b = sd.burst_cfo(h, dt_p)
    K = h.shape[1]
    Hb = np.einsum("bkrc,bk->brc", h, np.exp(-2j * np.pi * cfo_b[:, None] * np.arange(K)[None] * dt_p)) / K
    q = z["q"]; good = q >= np.median(q) / 4
    if "acq" in z.files: good &= ~z["acq"]
    ref = Hb[good].mean(0)
    for _ in range(2):
        phi = np.angle(np.einsum("brc,rc->b", Hb, np.conj(ref)))
        Hb = Hb * np.exp(-1j * phi)[:, None, None]
        ref = Hb[good].mean(0)
    return Hb, good, cfo_b

def snr_pulse(h):
    d = h[:, 1:] * np.conj(h[:, :-1]); ph = np.angle(d.sum((1, 2, 3)))
    hc = h * np.exp(-1j * np.outer(ph, np.arange(h.shape[1])))[:, :, None, None]
    m = hc.mean(1, keepdims=True); e = hc - m
    return 10 * np.log10((np.abs(m) ** 2).mean() / (np.abs(e) ** 2).mean() * (h.shape[1] - 1) / h.shape[1])

def one_hz_db(z):
    h = z["h"]; t = z["unix"] - z["unix"][0]
    a = np.abs(h).mean(1); x = a / np.median(a, 0) - 1
    fs = 1 / np.median(np.diff(t))
    X = np.abs(np.fft.rfft(x * np.hanning(len(x))[:, None, None], axis=0)) ** 2
    f = np.fft.rfftfreq(len(x), 1 / fs); P = X.mean((1, 2))
    band = lambda lo, hi: P[(f >= lo) & (f < hi)].mean()
    return 10 * np.log10(band(0.95, 1.15) / np.mean([band(0.6, 0.9), band(1.3, 1.8)])), f, P

# ---------------------------------------------------------------- runs table
runs, sync_rows, amp_rows = [], [], []
for key, label, tanda in RUNS:
    for dname, rx, tx in DIRS:
        d = f"{key}_{rx}"
        if not (Path(d) / "sense_rx.npz").exists():
            continue
        z = np.load(Path(d) / "sense_rx.npz"); s = summ(d); st = summ(f"{key}_{tx}")
        rate = float(z["rate"]); t0 = s["args"]["tx_start_unix"]
        lag = z["lag"] / rate * 1e9
        Hb, good, cfo_b = clean_h(z)
        lg = lag[good]; lg = lg[np.abs(lg - np.median(lg)) < 200]
        zt = np.load(Path(f"{key}_{tx}") / "sense_rx.npz")
        f0 = float(z["wifi_freq"])
        pred = f0 * (np.interp(z["unix"], zt["unix"], zt["ppm"]) - z["ppm"]) * 1e-6
        oh, f, P = one_hz_db(z)
        lat = s["results"].get("latency", {}).get("latency_ns") if "latency" in s["results"] else s["args"]["latency_ns"]
        ctx = st["results"]["counts"]
        tx_events = st["results"].get("async_other", {})
        runs.append(dict(
            run=key, label=label, tanda=tanda, dir=dname,
            started=dt.datetime.fromtimestamp(t0).strftime("%Y-%m-%d %H:%M"),
            band_ghz=r(f0 / 1e9, 2), wifi_msps=r(rate / 1e6, 2),
            burst_hz=r(1 / np.median(np.diff(z["unix"])), 0), bursts=int(len(lag)),
            usable_pct=r(100 * good.mean(), 2), snr_db=r(snr_pulse(z["h"]), 1),
            sync_std_ns=r(lg.std(), 1), sync_p99_ns=r(np.percentile(np.abs(lg - lg.mean()), 99), 1),
            lag_mean_ns=r(lg.mean(), 1), latency_ns=r(lat, 0),
            cfo_meas_hz=r(np.median(cfo_b), 1), cfo_pred_hz=r(np.median(pred), 1),
            cfo_diff_hz=r(np.median(cfo_b) - np.median(pred), 1),
            one_hz_db=r(oh, 1),
            tx_late_pct=r(100 * ctx["tx_late"] / max(1, ctx["tx_late"] + ctx["tx_armed"]), 2),
            tx_late_events=int(sum(v for k, v in tx_events.items() if k in ("time_error", "underflow"))),
            extraction=("fraccional" if rate > 20e6 else "muestra entera")))
        if key in ("sense_L_cables", "t3_empty"):
            m = (f > 0.2) & (f < 4)
            ref = np.median(P[m])
            for fi, pi in zip(f[m][::2], P[m][::2]):
                amp_rows.append(dict(run=label, dir=dname, f_hz=r(fi, 3), rel_db=r(10 * np.log10(pi / ref), 2)))
save("runs", runs)
save("amp_spectrum", amp_rows)

# ------------------------------------------------------------- sync history (two-way)
hist = []
def two_ap_side(d):
    links = summ(d)["results"]["links"]
    v = [l["sync_err_ns"] for l in links.values() if l.get("sync_err_ns")]
    return np.mean([x["mean"] for x in v]), np.mean([x["std"] for x in v])
for key, label, rate in (("two_ap_run1", "Coordinación · enfrentados", 15.36), ("two_ap_L_run1", "Coordinación · en L", 15.36)):
    ma, sa = two_ap_side(f"{key}_A"); mb, sb = two_ap_side(f"{key}_B")
    hist.append(dict(run=label, wifi_msps=rate, method="correlación completa + sinc", std_at_A_ns=r(sa, 1), std_at_B_ns=r(sb, 1),
                     offset_ns=r((ma - mb) / 2, 1), path_ns=r((ma + mb) / 2, 0)))
by = {(x["run"], x["dir"]): x for x in runs}
for key, label, _ in RUNS:
    a, b = by.get((key, "B→A")), by.get((key, "A→B"))
    if not a or not b: continue
    da = a["latency_ns"] + a["lag_mean_ns"]; db = b["latency_ns"] + b["lag_mean_ns"]
    hist.append(dict(run=label, wifi_msps=a["wifi_msps"], method=("posición conocida, fraccional" if a["wifi_msps"] > 20 else "posición conocida, muestra entera"),
                     std_at_A_ns=a["sync_std_ns"], std_at_B_ns=b["sync_std_ns"], offset_ns=r((da - db) / 2, 1), path_ns=r((da + db) / 2, 0)))
save("sync_history", hist)

# ------------------------------------------------------------- sync timeline + histogram
tl, hg = [], []
for key, label in (("t3_empty", "Vacía"), ("t3_walk", "Andando")):
    za, zb = np.load(f"{key}_A/sense_rx.npz"), np.load(f"{key}_B/sense_rx.npz")
    t0 = summ(f"{key}_A")["args"]["tx_start_unix"]
    def per_s(z):
        lag = z["lag"] / float(z["rate"]) * 1e9; t = z["unix"] - t0
        q = z["q"]; g = (q >= np.median(q) / 4) & ~z["acq"]
        return t[g], lag[g]
    ta, la = per_s(za); tb, lb = per_s(zb)
    for s0 in np.arange(2, 120, 2.0):
        ma = (ta >= s0) & (ta < s0 + 2); mb = (tb >= s0) & (tb < s0 + 2)
        if ma.sum() < 50 or mb.sum() < 50: continue
        xa, xb = np.median(la[ma]), np.median(lb[mb])
        tl.append(dict(run=label, t_s=r(s0 + 1, 1), at_A_ns=r(xa, 2), at_B_ns=r(xb, 2),
                       offset_ns=r((xa - xb) / 2, 2), path_ns=r((xa + xb) / 2, 2)))
    if key == "t3_empty":
        for nm, l in (("Recibe A (B→A)", la), ("Recibe B (A→B)", lb)):
            dv = l - np.median(l)
            c, e = np.histogram(dv, bins=np.arange(-60, 61, 2))
            for ci, ei in zip(c, e[:-1]):
                hg.append(dict(series=nm, ns=r(ei + 1, 0), share=r(ci / len(dv), 5)))
z = np.load("sense_L_empty_B/sense_rx.npz")
l = z["lag"] / float(z["rate"]) * 1e9; dv = l - np.median(l)
c, e = np.histogram(dv, bins=np.arange(-60, 61, 2))
for ci, ei in zip(c, e[:-1]):
    hg.append(dict(series="Tanda 0, recibe B (muestra entera)", ns=r(ei + 1, 0), share=r(ci / len(dv), 5)))
save("sync_timeline", tl)
save("sync_hist", hg)

# ------------------------------------------------------------- beams
f0 = 5.18e9; lam = C / f0; d = 0.030; kd = 2 * np.pi * d / lam
th = np.arange(-90, 90.5, 1.0)
def af(n, steer_deg, ang):
    u = np.sin(np.deg2rad(ang)) - np.sin(np.deg2rad(steer_deg))
    v = np.abs(np.exp(1j * np.outer(u, np.arange(n)) * kd).sum(1)) / n
    return 20 * np.log10(np.maximum(v, 1e-4))
def hpbw(n, steer=0.0):
    g = af(n, steer, np.arange(-90, 90.01, 0.05)); a = np.arange(-90, 90.01, 0.05)
    m = a[g >= -3]; return m.max() - m.min()
def sectors(n, cross=-2.0, span=60):
    g = af(n, 0, np.arange(0, 90, 0.01)); a = np.arange(0, 90, 0.01)
    half_u = np.sin(np.deg2rad(a[np.argmax(g < cross)]))
    return int(np.floor(2 * np.sin(np.deg2rad(span)) / (2 * half_u))) + 1
partner = {"A": A_.a_partner_deg, "B": A_.b_partner_deg}
secs = {"A": A_.a_sectors, "B": A_.b_sectors}
beam_rows = []
for ap in "AB":
    for n, role in ((4, "TX"), (3, "RX")):
        for s in secs[ap]:
            for a, g in zip(th, af(n, s, th)):
                beam_rows.append(dict(ap=ap, role=role, beam=f"{role} {s:+.0f}°", deg=r(a, 0), gain_db=r(g, 2)))
# measured: calibration from t2_check3, scanned on the static channel of t3_walk (first 30 s)
cal_rows, metric_rows = [], []
def static_H(folder, t_lo=None, t_hi=None):
    z = np.load(Path(folder) / "sense_rx.npz"); Hb, good, _ = clean_h(z)
    t = z["unix"] - summ(folder)["args"]["tx_start_unix"]
    m = good if t_lo is None else good & (t >= t_lo) & (t < t_hi)
    return Hb[m].mean(0)
def steer(n, deg): return np.exp(1j * np.arange(n) * kd * np.sin(np.deg2rad(deg)))
for dname, rx, tx in DIRS:
    Hc = static_H(f"t3_empty_{rx}", 2, 30); Hn = static_H(f"t3_empty_{rx}", 90, 118)
    He = static_H(f"t3_walk_{rx}", 2, 30)
    for role, ap, n, vec_c, vec_n, vec_e in (("TX", tx, 4, Hc[1], Hn[1], He[1]), ("RX", rx, 3, Hc[:, 1], Hn[:, 1], He[:, 1])):
        p = partner[ap]
        cal = vec_c / steer(n, p); cal = cal / np.abs(cal)
        scan = []
        for a in th:
            w = np.conj(cal * steer(n, a)) / np.sqrt(n)
            scan.append(np.abs(w @ vec_n) ** 2 / (np.sum(np.abs(vec_n) ** 2)))
        scan = 10 * np.log10(np.maximum(np.array(scan), 1e-6))
        for a, g in zip(th, scan):
            cal_rows.append(dict(ap=ap, role=role, series="medido (calibrado en 0–30 s, probado en 90–118 s)", deg=r(a, 0), gain_db=r(g, 2)))
        for a, g in zip(th, af(n, p, th)):
            cal_rows.append(dict(ap=ap, role=role, series="ideal", deg=r(a, 0), gain_db=r(g, 2)))
        def loss(v):
            w = np.conj(cal * steer(n, p)) / np.sqrt(n)
            return 10 * np.log10(np.abs(w @ v) ** 2 / np.sum(np.abs(v) ** 2))
        cal_n = vec_n / steer(n, p)
        drift = np.angle(cal_n / cal_n[0] / (cal / cal[0]), deg=True)
        amp = 20 * np.log10(np.abs(vec_n) / np.abs(vec_n).mean())
        metric_rows.append(dict(array=f"AP {ap} · {role} ({n} antenas)", ap=ap, role=role, n=n,
                                d_over_lambda=r(d / lam, 3), hpbw_broadside_deg=r(hpbw(n), 1),
                                hpbw_31deg=r(hpbw(n, 31.0), 1),
                                sectors_2db_120deg=sectors(n), grating_free_deg=r(np.rad2deg(np.arcsin(min(1, lam / d - 1))), 0),
                                partner_deg=p, cal_loss_db_same_session=r(loss(vec_n), 2), cal_loss_db_next_session=r(loss(vec_e), 2),
                                phase_drift_max_deg=r(np.max(np.abs(drift)), 1), amp_spread_db=r(amp.max() - amp.min(), 1)))
save("beam_sectors", beam_rows)
save("beam_measured", cal_rows)
save("beam_metrics", metric_rows)

# ------------------------------------------------------------- geometry / zones (model)
T = 0.6
P_A, P_B, P_Z = np.array([4 * T, 8 * T]), np.array([0.8 * T, 5.8 * T]), np.array([4 * T, 5 * T])
def bearing(p, q): v = q - p; return np.rad2deg(np.arctan2(v[1], v[0]))
# steering angle conventions fitted to the partner/zone angles used in the analysis
# A: theta = broadside - bearing (B at +32, clockwise); B: theta = bearing - broadside (A at +30)
bsA = bearing(P_A, P_Z) - 22.0
def thA(p): return ((bsA - bearing(P_A, p) + 180) % 360) - 180
bsB = bearing(P_B, P_Z) + 18.0
def thB(p): return ((bearing(P_B, p) - bsB + 180) % 360) - 180
geo = dict(A=P_A, B=P_B, Z=P_Z)
pts = []
for nm, p in geo.items():
    pts.append(dict(name=nm, x=r(p[0], 2), y=r(p[1], 2), kind="nodo" if nm != "Z" else "centro zona",
                    theta_from_A=r(thA(p), 1) if nm != "A" else None, theta_from_B=r(thB(p), 1) if nm != "B" else None))
for i, (cx, cy) in enumerate([(2.5 * T, 3.5 * T), (5.5 * T, 3.5 * T), (5.5 * T, 6.5 * T), (2.5 * T, 6.5 * T)]):
    p = np.array([cx, cy])
    pts.append(dict(name=f"esquina {i+1}", x=r(cx, 2), y=r(cy, 2), kind="zona de paso", theta_from_A=r(thA(p), 1), theta_from_B=r(thB(p), 1)))
save("geometry", pts)
zone_rows = []
xs = np.arange(-0.3, 4.21, 0.15); ys = np.arange(1.2, 6.01, 0.15)
for x in xs:
    for y in ys:
        p = np.array([x, y])
        if np.linalg.norm(p - P_A) < 0.25 or np.linalg.norm(p - P_B) < 0.25: continue
        a, b = thA(p), thB(p)
        va, vb = (p - P_A), (p - P_B)
        beta = np.degrees(np.arccos(np.dot(va, vb) / np.linalg.norm(va) / np.linalg.norm(vb)))
        dop = np.cos(np.deg2rad(beta / 2))
        # behind the array (|theta|>90) a ULA sees the mirror angle: same gain (front/back ambiguity)
        fa = 180 - a if a > 90 else (-180 - a if a < -90 else a)
        fb = 180 - b if b > 90 else (-180 - b if b < -90 else b)
        for sa in A_.a_sectors:
            for sb in A_.b_sectors:
                g = af(4, sa, [fa])[0] + af(3, sb, [fb])[0]
                zone_rows.append(dict(pair=f"TX A {sa:+.0f}° × RX B {sb:+.0f}°", x=r(x, 2), y=r(y, 2), gain_db=r(max(g, -30), 1),
                                      doppler_factor=r(dop, 2), behind=bool(abs(a) > 90 or abs(b) > 90)))
save("zones_map", zone_rows)

# ------------------------------------------------------------- detection, energy, doppler
def proc(rx, tx, ap_tx, ap_rx):
    kd_ = 2 * np.pi * A_.spacing / (C / float(np.load(f"{rx}/sense_rx.npz")["wifi_freq"]))
    return sd.process(Path(rx), Path(tx), kd_, partner[ap_tx], partner[ap_rx], secs[ap_tx], secs[ap_rx], A_)
P = {}
for run in ("t3_empty", "t3_walk"):
    P[(run, "A→B")] = proc(f"{run}_B", f"{run}_A", "A", "B")
    P[(run, "B→A")] = proc(f"{run}_A", f"{run}_B", "B", "A")
def win(res, integ, edges):
    tc, e = res["tc"], res["e_all"]
    return np.array([np.median(e[(tc >= s) & (tc < s + integ)]) for s in edges])
roc, det = [], []
for integ in (0.5, 1.0, 2.0):
    edges = np.arange(2, 119 - integ, integ)
    ser = {}
    for dn in ("A→B", "B→A"):
        ser[dn] = (win(P[("t3_empty", dn)], integ, edges), win(P[("t3_walk", dn)], integ, edges))
    ser["Ambos sentidos"] = (np.minimum(ser["A→B"][0], ser["B→A"][0]), np.minimum(ser["A→B"][1], ser["B→A"][1]))
    walk = (edges >= 35) & (edges + integ <= 95); still = (edges >= 96) & (edges + integ <= 119); pre = edges + integ <= 30
    for nm, (eE, eW) in ser.items():
        for pc in np.concatenate([np.arange(50, 99, 2.0), np.arange(99, 100.01, 0.25)]):
            thr = np.percentile(eE, pc)
            roc.append(dict(window_s=integ, method=nm, fa=r(np.mean(eE > thr), 4), pd=r(np.mean(eW[walk] > thr), 4)))
        thr = np.percentile(eE, 99)
        det.append(dict(window_s=integ, method=nm, fa_pct=r(100 * np.mean(eE > thr), 1),
                        pd_walk_pct=r(100 * np.mean(eW[walk] > thr), 1), pd_still_pct=r(100 * np.mean(eW[still] > thr), 1),
                        empty_part_pct=r(100 * np.mean(eW[pre] > thr), 1),
                        margin_db=r(10 * np.log10(np.median(eW[walk]) / thr), 1)))
save("detection_roc", roc)
save("detection", det)
en, sec, cdf, spec = [], [], [], []
for (run, dn), res in P.items():
    tc, e = res["tc"], res["e_all"]
    for s0 in np.arange(1.5, 119.5, 0.5):
        m = (tc >= s0) & (tc < s0 + 0.5)
        if m.any(): en.append(dict(run="Andando" if run == "t3_walk" else "Vacía", dir=dn, t_s=r(s0 + 0.25, 2), energy_db=r(10 * np.log10(np.median(e[m])), 2)))
    if run == "t3_walk":
        Pe = P[("t3_empty", dn)]
        for (qq, ss), ew in res["energy"].items():
            ee = Pe["energy"][(qq, ss)]
            mw = (tc >= 35) & (tc < 95); me = Pe["tc"] > 0
            sec.append(dict(dir=dn, pair=f"RX {res['rx_sectors'][qq]:+.0f}° × TX {res['tx_sectors'][ss]:+.0f}°",
                            walk_db=r(10 * np.log10(np.median(ew[mw])), 1), empty_db=r(10 * np.log10(np.median(ee[me])), 1),
                            delta_db=r(10 * np.log10(np.median(ew[mw]) / np.median(ee[me])), 1),
                            peak_db=r(10 * np.log10(np.percentile(ew[mw], 95) / np.median(ee[me])), 1)))
        f = res["f"]; S = res["spec_all"]; mw = (tc >= 35) & (tc < 95)
        Sw = S[mw] - np.median(P[("t3_empty", dn)]["spec_all"], 0)[None]
        Sw = np.maximum(Sw, 0)
        af_ = np.abs(f); E = Sw.sum(0); mov = af_ > A_.static_hz
        for fmax in np.arange(2.5, 100.01, 2.5):
            cdf.append(dict(dir=dn, abs_hz=r(fmax, 1), speed_mps=r(fmax * res["lam"], 3),
                            share=r(E[mov & (af_ <= fmax)].sum() / E[mov].sum(), 4)))
        # spectrogram, downsampled: 0.3 s x 2.5 Hz, |f| <= 80
        fm = np.abs(f) <= 80
        fb = np.round(f[fm] / 2.5) * 2.5
        Sd = 10 * np.log10(S[:, fm] + 1e-30)
        top = np.percentile(Sd, 99.5)
        for i in range(0, len(tc), 3):
            row = S[i:i + 3, fm].mean(0)
            for fv in np.unique(fb):
                spec.append(dict(dir=dn, t_s=r(tc[i], 1), f_hz=r(fv, 1),
                                 db=r(max(10 * np.log10(row[fb == fv].mean() + 1e-30) - top, -40), 1)))
save("energy_timeline", en)
save("sector_energy", sec)
save("doppler_cdf", cdf)
save("spectrogram", spec)

# ------------------------------------------------------------- capabilities
t3 = [x for x in runs if x["run"] in ("t3_empty", "t3_walk")]
t3q = [x for x in runs if x["run"] in ("t3_empty", "t2_check3")]
fs = 200.0; win_s = A_.win_s; lam = C / 5.18e9
dj = {(x["window_s"], x["method"]): x for x in det}
cap = [
    dict(group="Sincronización", item="Residuo de sincronización entre AP (desviación)", value=r(np.mean([x["sync_std_ns"] for x in t3q]), 1), unit="ns", how="Desviación del retardo de cada burst respecto al esperado por el 5G; tanda 3 con la sala quieta, media de los dos sentidos (andando: 17,7 ns)"),
    dict(group="Sincronización", item="Desfase entre AP medido por ida y vuelta (vacía)", value=[h for h in hist if h["run"] == "Tanda 3 · vacía"][0]["offset_ns"], unit="ns", how="Mitad de la diferencia entre los retardos A→B y B→A"),
    dict(group="Sincronización", item="Error del CFO predicho por el 5G", value=r(np.mean([abs(x["cfo_diff_hz"]) for x in t3]), 1), unit="Hz", how="|CFO medido en los pulsos − (ppm_tx − ppm_rx)·f| sobre ~355 Hz"),
    dict(group="Doppler", item="Tasa de revisita por sentido", value=r(fs, 0), unit="Hz", how="Un burst cada 5 ms por AP"),
    dict(group="Doppler", item="Doppler máximo sin ambigüedad", value=r(fs / 2, 0), unit="± Hz", how="fs/2"),
    dict(group="Doppler", item="Velocidad de camino máxima", value=r(fs / 2 * lam, 2), unit="± m/s", how="fs/2 · λ (λ = 5,79 cm)"),
    dict(group="Doppler", item="Resolución Doppler", value=r(1 / win_s, 1), unit="Hz", how="1 / ventana de 0,5 s"),
    dict(group="Doppler", item="Resolución de velocidad", value=r(lam / win_s, 3), unit="m/s", how="λ / ventana"),
    dict(group="Doppler", item="Velocidad mínima detectable", value=r(A_.static_hz * lam, 2), unit="m/s", how="Umbral de 2,5 Hz que separa lo estático · λ"),
    dict(group="Distancia", item="Resolución en camino biestático", value=r(C / (0.8 * 61.44e6), 1), unit="m", how="c / B con B ≈ 49 MHz útiles (80 % de 61,44 Msps)"),
    dict(group="Ángulo", item="Ancho de haz TX (4 antenas, frente)", value=[m for m in metric_rows if m["role"] == "TX"][0]["hpbw_broadside_deg"], unit="°", how="−3 dB, d = 3,0 cm = 0,52 λ"),
    dict(group="Ángulo", item="Ancho de haz RX (3 antenas, frente)", value=[m for m in metric_rows if m["role"] == "RX"][0]["hpbw_broadside_deg"], unit="°", how="−3 dB"),
    dict(group="Ángulo", item="Sectores TX distinguibles en ±60°", value=[m for m in metric_rows if m["role"] == "TX"][0]["sectors_2db_120deg"], unit="", how="Haces que se cruzan a −2 dB"),
    dict(group="Ángulo", item="Sectores RX distinguibles en ±60°", value=[m for m in metric_rows if m["role"] == "RX"][0]["sectors_2db_120deg"], unit="", how="Haces que se cruzan a −2 dB"),
    dict(group="Detección", item="Persona andando, ventana 2 s, ambos sentidos", value=dj[(2.0, "Ambos sentidos")]["pd_walk_pct"], unit="%", how=f"Umbral al 99 % de la sala vacía (falsa alarma {dj[(2.0, 'Ambos sentidos')]['fa_pct']} %)"),
    dict(group="Detección", item="Persona andando, ventana 1 s, ambos sentidos", value=dj[(1.0, "Ambos sentidos")]["pd_walk_pct"], unit="%", how=f"Falsa alarma {dj[(1.0, 'Ambos sentidos')]['fa_pct']} %"),
    dict(group="Detección", item="Persona quieta de pie", value=dj[(2.0, "Ambos sentidos")]["pd_still_pct"], unit="%", how="El detector usa Doppler: quien no se mueve no se ve"),
]
save("capabilities", cap)
