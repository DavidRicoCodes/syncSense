# Method: OTA coordination and SFN numbering

Implementation: `s5g/nr_pbch.py`, `s5g/x410_pss_timed_tx.py`, `s5g/x410_two_ap.py`,
`s5g/x410_two_ap_sense.py`. The detailed lab version (Spanish) is in
`docs/LAB_LOG.md`, §5.2.

## Problem

Each AP has its own X410 with an independent oscillator and time epoch, no common
reference and no backhaul. To run a complementary agenda (A transmits while B
listens, and vice versa) both APs must agree, using only the 5G cell, on:

1. the fine instant of each turn (nanoseconds) — from the PSS;
2. the identity of the turn (which SSB is turn 1 of the cycle) — from the SFN.

Counting SSBs from boot fails (different start times). Rounding host time to the
20 ms grid depends on NTP; the two hosts' NTP clocks were measured 8.3 ms apart,
almost half the grid.

## Cell and capture

| Parameter | Value |
|---|---|
| Band / SSB frequency | n78 TDD, 3541.44 MHz |
| SSB SCS | 30 kHz (case C), Lmax = 8 |
| Cell | NCellID 0, SSB index 0, measured 20 ms periodicity |
| Capture | X410 ch2 `RX1`, 15.36 Msps (FFT 512), 20 ms blocks with 1 ms overlap, continuous |
| Ticks | 245.76 MHz master clock (16 ticks per 5G sample) |

## Fine timing: PSS and tracker

1. The unchanged 5G pipeline of `modulos_rx_tx` detects PSS/SSS on each block,
   corrects the warm-up CFO and extracts the SSB grid (240 × 4).
2. PSS timestamp in device ticks:
   `T = tick(block time_spec) + 16 · detector offset (samples)`.
   The time at which Python receives the result is never used.
3. Tracker: linear fit `T_k = a + b·k` over the last 50 accepted PSS. It gives the
   AP's period against the gNB (ppm) and predicts future SSBs. Lock with 20 points,
   residual < 300 ns, 200 ms holdover, PSS more than 1 µs off the prediction rejected.
   Typical residual 17–19 ns rms.

## Identity: PBCH/BCH decoding

On the same SSB grid (TS 38.211 §7.3.3, TS 38.212 §7.1):

1. Channel estimate (`modulos_rx_tx`): LS on PSS, SSS and the 144 PBCH DMRS,
   per-symbol frequency interpolation and smoothing; residual power as noise.
2. 432 PBCH data REs (symbols 1 and 3, edges of 2, without DMRS positions).
3. Equalization `s = y·H*/|H|²`, `σ² = P_res/|H|²`, QPSK LLRs (864).
4. PBCH descrambling: Gold sequence, `c_init = NCellID`, offset `v·864`
   (v = 3 LSB of the SSB index).
5. Polar rate recovery 864 → 512 (sum of repeated LLRs).
6. SC-list polar decoder, L = 8, CRC24C; K = 56 (32 payload + 24 CRC), nMax = 9.
7. Payload: read v from the two unscrambled SFN bits, descramble (M = 29),
   deinterleave G(j), extract MIB (24 bits), SFN LSBs, half-frame bit, k_SSB MSB.
8. SFN = 6 MSB of the MIB · 16 + 4 LSB of the payload.

The fixed polar tables were derived numerically from the public MATLAB 5G Toolbox
functions `nrPolarEncode` and `nrRateMatchPolar` (`tools/pbch_ref/pbch_ref.m`).
The Python encoder reproduces `nrBCH`/`nrPBCH` bit-exactly on 300 random cases.
AWGN: 100 % correct down to −7 dB per RE, 87 % at −9 dB, no false CRC passes.
Real cell, 60 s RX-only on both APs: 2762/2762 and 2341/2341 SSBs decoded.

## From SFN to the common SSB number

- `n = ((2·SFN + h)/4) mod 512`; `K0 = (n − k) mod 512` with k the local tracker index.
- Acquisition: decode every SSB until the last 3 decodes agree on K0.
- Revalidation every 25 SSB (0.5 s); mismatches are counted (no automatic re-acquisition).
- Agenda: `slot = (n mod 512) mod 5`, `cycle = (n mod 512) div 5`. 512 is not a multiple
  of 5, so cycle 102 has two SSBs; both APs see the same jump.
- Scheduling: each AP predicts `T(k)` with its tracker, adds the turn offset in its own
  ticks and arms the UHD timed command about 6 SSB ahead; with less than 15–20 ms of
  margin the turn is dropped and the AP stays silent.

Host time is only used to start and stop a run and to annotate results.

## Verification

| Check | Shows |
|---|---|
| Encoder vs MATLAB (300/300) | bit-exact PBCH/BCH chain |
| AWGN and MATLAB SSB waveform through the real pipeline | decoder robustness and integration |
| RX-only, real cell, both APs | 100 % decoded, 0 CRC failures, SFN consistent with the tracker, constant MIB |
| Revalidations during coordination (`two_ap_sfn_*`) | 974 checks, 0 mismatches |
| Sentinels in non-partner slots | 0 hits in 14 567 windows (agreement mod 5) |
| Epoch of common SSB 0 via device↔host anchor | full-number agreement (8.3 ms apart vs 20 ms per SSB) |
