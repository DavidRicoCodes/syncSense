# syncSense — S5G: multi-AP Wi-Fi sensing coordinated by a commercial 5G cell

Two Wi-Fi access points built on independent USRP X410s, with no backhaul and no
shared clock, coordinate a collision-free sensing schedule using only the SSB of
the same commercial 5G cell:

- **time** from the PSS (per-AP linear tracker over the last 50 PSS, X410 ticks);
- **identity** from the SFN decoded from PBCH/MIB (common SSB number
  `n = floor((2·SFN + h)/4) mod 512`);
- **frequency** from each AP's offset against the gNB (predicts the inter-AP CFO);
- a provisioned complementary agenda, executed with UHD timed commands armed
  about 6 SSB (≈120 ms) ahead; a late turn stays silent.

On top of that schedule the APs run bistatic 4 TX × 3 RX MIMO sensing at 5.18 GHz
(Doppler, presence, localization by crossing AoD/AoA).

This branch is the code and analysis behind the S5G journal paper (IEEE TWC
draft, Overleaf). The previous history of this repository (orchestration
framework `syncctl`, `nosync_passive`, HE NDP-like 20/40 MHz) is kept in the
`legacy/*` branches.

## Layout

| Path | Contents |
|---|---|
| `s5g/` | testbed code, exact copies of the scripts used on both APs (version of run t18, 2026-10-21) |
| `s5g/archive/` | earlier variants and older X410 beamforming experiments, kept for traceability |
| `modulos_rx_tx/` | submodule pinned at `2ac9ee9`: the 5G SSB DSP (PSS/SSS, grid, channel estimate) imported by `s5g/x410_2x2_5g.py` |
| `tools/pbch_ref/` | MATLAB 5G Toolbox reference scripts used to derive and validate the PBCH decoder |
| `analysis/` | analysis scripts behind the paper sections, same layout as in the data backup |
| `docs/METHOD.md` | the coordination and SFN numbering method |
| `docs/RESULTS.md` | paper numbers and figures → script → runs → output |
| `docs/DATA.md` | where the raw data lives (not in git) and how to check it |
| `docs/LAB_LOG.md` | full lab log of the testbed (Spanish), including every run and decision |

## Main scripts (`s5g/`)

| Script | Purpose |
|---|---|
| `x410_pss_timed_tx.py` | core: UHD radio (separate tick/5G/Wi-Fi rates), PSS tracker, ZC bursts, PBCH hook, simulator |
| `x410_two_ap.py` | two-AP coordination at 10 Hz with sentinels and SFN numbering |
| `x410_two_ap_sense.py` | S5G sensing at 200 Hz per direction (fractional extraction, delay profiles, latency acquisition) |
| `x410_two_ap_baseline.py` | baselines B0/B1/B2 without 5G coordination |
| `nr_pbch.py`, `nr_pbch_tables.py` | PBCH/BCH decoder (SC-list polar + CRC24C), bit-exact against MATLAB |
| `x410_pbch_check.py`, `compare_pbch.py` | RX-only PBCH check and two-AP numbering comparison |
| `sense_doppler.py` | Doppler per sector, CFO handling, spectrograms |
| `x410_2x2_5g.py` | loader of the 5G DSP from `modulos_rx_tx` |

All scripts accept `--sim` for a software-only run.

## Running on the APs

The two APs are the servers reached as `wifitx` (AP A) and `wifirx` (AP B), each
with an X410 on two 10 GbE links (MTU 9000). Same command on both, same real
start time:

```bash
PYTHONPATH=/usr/local/lib/python3.10/site-packages ~/syncSense/modulos_rx_tx/.venv_uhd/bin/python -u \
  s5g/x410_two_ap_sense.py --ap A --tx-start 12:00:00 --duration-s 120 \
  --args addr=192.168.10.2,second_addr=192.168.11.2 --wifi-rate 122.88e6 --tx-gain 40 --rx-gain 40
```

Exact arguments of every run are stored in each run's `summary.json`
(see `docs/DATA.md`).

## Getting the submodule

```bash
git clone --recurse-submodules https://github.com/DavidRicoCodes/syncSense.git
```
