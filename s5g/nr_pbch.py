#!/usr/bin/env python3
"""
NR PBCH / BCH decoding (TS 38.211 7.3.3, TS 38.212 7.1) for the 240x4 SSB grid
produced by the modulos_rx_tx 5G pipeline. Pure numpy.

Chain (receive):  grid + channel estimate -> 432 PBCH data RE -> equalized QPSK
-> LLR (864) -> PBCH descrambling (c_init = NCellID, offset v*864, v = 3 LSB of
the SSB index for Lmax = 8) -> polar rate recovery -> CRC-aided SC-list decoder
-> CRC24C -> payload descrambling / deinterleaving -> MIB (24 bits), SFN (10
bits), half-frame bit, k_SSB MSB.

The polar tables come from nr_pbch_tables.py; the payload interleaver,
scramblers and bit layout follow TS 38.212 and are checked bit-exactly against
MATLAB nrBCH / nrPBCH test vectors (pbch_ref/).
"""
from dataclasses import dataclass

import numpy as np

from nr_pbch_tables import INFO_POS, RM_SRC

K, E, N, A = 56, 864, 512, 32
INFO_POS = np.asarray(INFO_POS)
RM_SRC = np.asarray(RM_SRC)
FROZEN = np.ones(N, dtype=bool)
FROZEN[INFO_POS] = False
# TS 38.212 Table 7.1.1-1, PBCH payload interleaving pattern G(j)
G_IL = (16, 23, 18, 17, 8, 30, 10, 6, 24, 7, 0, 5, 3, 2, 1, 4,
        9, 11, 12, 13, 14, 15, 19, 20, 21, 22, 25, 26, 27, 28, 29, 31)
CRC24C = 0x1B2B117


def gold(c_init, length):
    """TS 38.211 5.2.1 pseudo-random sequence."""
    nc = 1600
    x1 = np.zeros(length + nc + 31, dtype=np.uint8)
    x2 = np.zeros(length + nc + 31, dtype=np.uint8)
    x1[0] = 1
    for i in range(31):
        x2[i] = (c_init >> i) & 1
    for n in range(length + nc):
        x1[n + 31] = x1[n + 3] ^ x1[n]
        x2[n + 31] = x2[n + 3] ^ x2[n + 2] ^ x2[n + 1] ^ x2[n]
    return (x1[nc:nc + length] ^ x2[nc:nc + length]).astype(np.uint8)


_GOLD_CACHE = {}


def gold_cached(c_init, length):
    key = (c_init, length)
    if key not in _GOLD_CACHE:
        _GOLD_CACHE[key] = gold(c_init, length)
    return _GOLD_CACHE[key]


def crc24c(bits):
    reg = 0
    for b in bits:
        top = ((reg >> 23) & 1) ^ int(b)
        reg = (reg << 1) & 0xFFFFFF
        if top:
            reg ^= CRC24C & 0xFFFFFF
    return np.array([(reg >> (23 - i)) & 1 for i in range(24)], dtype=np.uint8)


# ----------------------------------------------------------------------------- payload
SFN_MIB_POS = range(1, 7)          # 6 MSBs of the SFN inside the MIB (bits 2..7)


def payload_layout(lmax=8):
    """For each payload bit i of a-bar: its kind and interleaved position G(j)."""
    kinds, dest = [], []
    j_sfn, j_hrf, j_ssb, j_other = 0, 10, 11, 14
    for i in range(A):
        if i in SFN_MIB_POS or 24 <= i <= 27:
            kinds.append(("sfn", j_sfn)); dest.append(G_IL[j_sfn]); j_sfn += 1
        elif i == 28:
            kinds.append(("hrf", 0)); dest.append(G_IL[j_hrf])
        elif 29 <= i <= 31:                    # last 3 bits always use j_SSB slots (G 11..13)
            kinds.append(("ssb", j_ssb)); dest.append(G_IL[j_ssb]); j_ssb += 1
        else:
            kinds.append(("other", j_other)); dest.append(G_IL[j_other]); j_other += 1
    return kinds, np.array(dest)


def _unscrambled_positions(lmax):
    """Interleaved positions not scrambled: HRF, SFN 2nd and 3rd LSB, SSB index (Lmax 64)."""
    pos = {G_IL[10], G_IL[7], G_IL[8]}      # SFN order: 6 MSBs (j 0..5), then bit3, bit2, bit1, bit0 (j 6..9)
    if lmax == 64:
        pos |= {G_IL[11], G_IL[12], G_IL[13]}
    return pos


def build_payload(mib24, sfn, hrf, kssb_msb, ncellid, lmax=8):
    """a-bar -> interleave -> scramble (TS 38.212 7.1.1-7.1.2). Returns 32 bits."""
    abar = np.zeros(A, dtype=np.uint8)
    abar[:24] = np.asarray(mib24, dtype=np.uint8)
    abar[24:28] = [(sfn >> 3) & 1, (sfn >> 2) & 1, (sfn >> 1) & 1, sfn & 1]
    abar[28] = hrf
    if lmax == 64:
        abar[29:32] = [(kssb_msb >> 5) & 1, (kssb_msb >> 4) & 1, (kssb_msb >> 3) & 1]
    else:
        abar[29] = kssb_msb
    _, dest = payload_layout(lmax)
    ap = np.zeros(A, dtype=np.uint8)
    ap[dest] = abar
    M = A - 3 if lmax in (4, 8) else A - 6
    v = 2 * ((sfn >> 2) & 1) + ((sfn >> 1) & 1)
    c = gold_cached(ncellid, M * 4 + 64)
    skip = _unscrambled_positions(lmax)
    out, j = ap.copy(), 0
    for i in range(A):
        if i in skip:
            continue
        out[i] ^= c[j + v * M]
        j += 1
    return out


def parse_payload(app, ncellid, lmax=8):
    """Inverse of build_payload: 32 scrambled+interleaved bits -> fields."""
    _, dest = payload_layout(lmax)
    # SFN 2nd/3rd LSB are not scrambled: read them first to get v
    b2 = int(app[G_IL[7]]); b1 = int(app[G_IL[8]])
    v = 2 * b2 + b1
    M = A - 3 if lmax in (4, 8) else A - 6
    c = gold_cached(ncellid, M * 4 + 64)
    skip = _unscrambled_positions(lmax)
    ap, j = np.array(app, dtype=np.uint8).copy(), 0
    for i in range(A):
        if i in skip:
            continue
        ap[i] ^= c[j + v * M]
        j += 1
    abar = ap[dest]
    mib = abar[:24]
    sfn_msb = int("".join(str(b) for b in mib[1:7]), 2)
    sfn = (sfn_msb << 4) | int("".join(str(b) for b in abar[24:28]), 2)
    return dict(mib=mib, sfn=sfn, hrf=int(abar[28]), kssb_msb=int(abar[29]), reserved=abar[30:32].tolist())


# ----------------------------------------------------------------------------- polar
def polar_transform(u):
    x = np.array(u, dtype=np.uint8).copy()
    n = 1
    while n < len(x):
        for s in range(0, len(x), 2 * n):
            x[s:s + n] ^= x[s + n:s + 2 * n]
        n *= 2
    return x


def bch_encode(mib24, sfn, hrf, kssb_msb, ncellid, lmax=8):
    """Payload -> CRC -> polar -> rate match: 864 coded bits (== MATLAB nrBCH)."""
    a = build_payload(mib24, sfn, hrf, kssb_msb, ncellid, lmax)
    c = np.concatenate([a, crc24c(a)])
    u = np.zeros(N, dtype=np.uint8)
    u[INFO_POS] = c
    x = polar_transform(u)
    return x[RM_SRC]


def pbch_scramble_seq(ncellid, v, lmax=8):
    return gold_cached(ncellid, E * lmax)[v * E:(v + 1) * E]


def qpsk(bits):
    b = np.asarray(bits, dtype=np.float64)
    return ((1 - 2 * b[0::2]) + 1j * (1 - 2 * b[1::2])) / np.sqrt(2)


def _f(a, b):
    return np.sign(a) * np.sign(b) * np.minimum(np.abs(a), np.abs(b))


def scl_decode(llr, L=8):
    """CRC-aided SC-list decoding of u (N=512) from codeword LLRs (LLR>0 -> 0).
    Returns candidate u vectors sorted by path metric."""
    PM = np.zeros(1)

    def rec(lv, frozen, PM):
        n = lv.shape[1]
        if n == 1:
            l = lv[:, 0]
            if frozen[0]:
                PM = PM + np.where(l < 0, np.abs(l), 0.0)
                b = np.zeros((len(l), 1), dtype=np.uint8)
                return b, b, np.arange(len(l)), PM
            pm0 = PM + np.where(l < 0, np.abs(l), 0.0)
            pm1 = PM + np.where(l >= 0, np.abs(l), 0.0)
            allpm = np.concatenate([pm0, pm1])
            keep = np.argsort(allpm, kind="stable")[:min(L, len(allpm))]
            parent = keep % len(l)
            bits = (keep >= len(l)).astype(np.uint8)[:, None]
            return bits, bits, parent, allpm[keep]
        h = n // 2
        xl, ul, pl, PM = rec(_f(lv[:, :h], lv[:, h:]), frozen[:h], PM)
        lv = lv[pl]
        g = lv[:, h:] + (1 - 2 * xl.astype(np.float64)) * lv[:, :h]
        xr, ur, pr, PM = rec(g, frozen[h:], PM)
        return np.concatenate([xl[pr] ^ xr, xr], 1), np.concatenate([ul[pr], ur], 1), pl[pr], PM

    _, U, _, PM = rec(np.asarray(llr, dtype=np.float64)[None, :], FROZEN, PM)
    order = np.argsort(PM, kind="stable")
    return U[order], PM[order]


# ----------------------------------------------------------------------------- receive
def pbch_data_indices(ncellid):
    v = ncellid % 4
    k = np.arange(240)
    idx = []
    for sym in (1, 2, 3):
        for kk in k:
            if kk % 4 == v:
                continue
            if sym == 2 and 48 <= kk < 192:
                continue
            idx.append((kk, sym))
    return np.array(idx)


@dataclass
class PbchResult:
    ok: bool
    sfn: int = -1
    hrf: int = -1
    kssb_msb: int = -1
    mib: tuple = ()
    path: int = -1
    metric: float = float("nan")
    evm_db: float = float("nan")


def decode_llr(llr864, ncellid, ibar_ssb, lmax=8, L=8):
    c = pbch_scramble_seq(ncellid, ibar_ssb % lmax if lmax != 64 else ibar_ssb, lmax)
    l = np.asarray(llr864, dtype=np.float64) * (1 - 2 * c.astype(np.float64))
    lc = np.zeros(N)
    np.add.at(lc, RM_SRC, l)
    U, PM = scl_decode(lc, L)
    for p, u in enumerate(U):
        bits = u[INFO_POS]
        if np.array_equal(crc24c(bits[:A]), bits[A:]):
            f = parse_payload(bits[:A], ncellid, lmax)
            return PbchResult(True, f["sfn"], f["hrf"], f["kssb_msb"], tuple(int(b) for b in f["mib"]), p, float(PM[p]))
    return PbchResult(False)


def decode_grid(grid, channel, ncellid, ibar_ssb, noise_var=None, lmax=8, L=8):
    """grid, channel: 240x4 complex (SSB grid and its estimate). Returns PbchResult."""
    idx = pbch_data_indices(ncellid)
    y = grid[idx[:, 0], idx[:, 1]]
    h = channel[idx[:, 0], idx[:, 1]]
    s = y * np.conj(h) / np.maximum(np.abs(h) ** 2, 1e-30)
    if noise_var is None:
        hard = (np.sign(s.real) + 1j * np.sign(s.imag)) / np.sqrt(2)
        nv = max(np.mean(np.abs(s - hard) ** 2), 1e-6)
    else:
        nv = noise_var / np.maximum(np.abs(h) ** 2, 1e-30)
    llr = np.empty(E)
    llr[0::2] = 2 * np.sqrt(2) * s.real / nv
    llr[1::2] = 2 * np.sqrt(2) * s.imag / nv
    r = decode_llr(llr, ncellid, ibar_ssb, lmax, L)
    hard = (np.sign(s.real) + 1j * np.sign(s.imag)) / np.sqrt(2)
    r.evm_db = float(10 * np.log10(np.mean(np.abs(s - hard) ** 2) + 1e-12))
    return r
