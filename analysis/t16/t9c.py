"""Common loader / geometry for t16_tiles (2026-10-14)."""
import json
from pathlib import Path
import numpy as np
C = 299792458.0; F0 = 5.18e9; K = 2*np.pi*F0/C; DSP = 0.030
CEN = {"A": np.array([1.50, 0.0]), "B": np.array([3.0, 1.50])}
U = {"A": np.array([-1.0, 0.0]), "B": np.array([0.0, -1.0])}     # antenna 1 -> antenna 3
NRM = {"A": np.array([0.0, 1.0]), "B": np.array([-1.0, 0.0])}
IDX_TX = np.array([0, 1, 3, 2]); IDX_RX = np.array([0, 1, 2])
TILES = [(3,3),(3,4),(3,5),(2,5),(1,5),(1,4),(2,4),(2,3),(1,3),(1,2),(2,2),(3,2),(4,3),(4,4),(4,5),(3,3)]
def tile_xy(c, r): return np.array([(c-0.5)*0.6, (r-0.5)*0.6])
def segments(trim=3.0):
    S = [("vacío", 0+trim, 20-trim, None)]
    for i, t in enumerate(TILES):
        S.append((f"{i+1}:{t}", 25+25*i+trim, 45+25*i-trim, t))
    S += [("detrás A", 430+trim, 450-trim, "behindA"), ("detrás B", 460+trim, 480-trim, "behindB"), ("vacío fin", 485+trim, 510-trim, None)]
    return S
def theta(ap, p):
    d = (p-CEN[ap])/np.linalg.norm(p-CEN[ap]); return float(np.degrees(np.arcsin(np.clip(U[ap]@d, -1, 1))))
def burst_cfo(h, dt_p):
    zp = np.einsum("bkrc,brc->bk", h, np.conj(h[:, 0])); ph = np.unwrap(np.angle(zp), axis=1)
    return np.polyfit(np.arange(h.shape[1])*dt_p, ph.T, 1)[0]/(2*np.pi)
def load(folder, profile=False):
    """Uniform 200 Hz grid. Returns dict with Hb (N,R,T) CFO-compensated and common-phase aligned,
    t (s from tx_start), valid mask, ref, and optionally profile (N,R,T,L) aligned the same way."""
    folder = Path(folder); z = np.load(folder/"sense_rx.npz"); s = json.loads((folder/"summary.json").read_text())
    q = z["q"]; good = (q >= np.median(q)/4) & ~z["acq"]
    h = z["h"][good].astype(np.complex128)
    dt_p = int(z["pulse_spacing"])/float(z["rate"])
    cfo = burst_cfo(h, dt_p); Kp = h.shape[1]
    rot = np.exp(-2j*np.pi*cfo[:, None]*np.arange(Kp)[None]*dt_p)
    Hb = np.einsum("bkrc,bk->brc", h, rot)/Kp
    k = z["n"][good].astype(np.int64)*4 + z["sub"][good].astype(np.int64)
    k0 = k.min(); N = int(k.max()-k0+1); idx = k-k0
    t_obs = z["unix"][good]-s["args"]["tx_start_unix"]
    tg = np.interp(np.arange(N), idx, t_obs)
    valid = np.zeros(N, bool); valid[idx] = True
    ref = Hb.mean(0); phi_obs = np.zeros(len(Hb))
    for _ in range(3):                                   # align common phase of observed bursts first
        phi = np.angle(np.einsum("brc,rc->b", Hb, np.conj(ref))); Hb = Hb*np.exp(-1j*phi)[:, None, None]; phi_obs += phi; ref = Hb.mean(0)
    G = np.zeros((N,)+Hb.shape[1:], complex); G[idx] = Hb
    flat = G.reshape(N, -1); ii = np.arange(N); hf = Hb.reshape(len(idx), -1)
    for j in range(flat.shape[1]):                       # then fill gaps (phase-coherent now)
        flat[~valid, j] = np.interp(ii[~valid], idx, hf[:, j].real) + 1j*np.interp(ii[~valid], idx, hf[:, j].imag)
    G = flat.reshape(G.shape)
    out = dict(H=G, t=tg, valid=valid, ref=ref, lag=np.full(N, np.nan), rate=float(z["rate"]))
    out["lag"][idx] = z["lag"][good]
    if profile:
        P = z["profile"][good].astype(np.complex64)                     # (n,R,T,L), already pulse-aligned
        Pg = np.zeros((N,)+P.shape[1:], np.complex64)
        P *= np.exp(-1j*phi_obs)[:, None, None, None].astype(np.complex64); Pg[idx] = P
        out["prof"] = Pg; out["pdelta"] = np.full(N, np.nan); out["pdelta"][idx] = z["profile_delta"][good]
        out["pfirst"] = int(z["profile_first_lag"])
    return out
