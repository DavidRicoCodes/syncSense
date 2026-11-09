# Paper results → scripts → runs → outputs

Analysis scripts are stored here exactly as used. They run **in place inside the
data backup** (`~/sync_backups/x410_two_ap_results_20261014/analysis/...` on PC5),
because they use relative and absolute paths to the runs, to intermediate files
and to the Overleaf figures folder. Some also use files under `/tmp/claude-1000/...`;
those are backed up (see `docs/DATA.md`).

| Paper item | Script(s) | Runs | Output |
|---|---|---|---|
| Sec. VII, synchronization statistics | `analysis/sec7/sec7_stats.py`, `verify.py` | t9_tiles, t10_tiles, t11_nrw4, t12_fsync, t13_tiles_fs, t14_track, t15c_check, t16_tiles, t18_zc80_g3 | `sec7_stats.json` |
| Sec. VII, figures | `analysis/sec7/figs_sec7.py` | (from `sec7_stats.json`) | `sync_timeseries.pdf`, `sync_ccdf.pdf` (Overleaf `figures/`) |
| Sec. VIII, localization, range, energy, short windows | `analysis/sec8/sec8_stats.py` (+ `sec8_stats_noexcl.py`) | t9, t10, t13, t16 (per-run `analysis/t*/pos9.py`, `range9b.py`, `energy.py`, `t9c.py`) | `sec8_stats.json` |
| Sec. VIII, AP exclusion radius variants | `analysis/sec8/excl/*.py` | t9, t10, t13, t16 | `excl/*.json` |
| Sec. VIII, tracking | `analysis/t14/*.py` | t13 (calibration), t14_track | `t14/*.json` |
| Sec. VIII, figures | `analysis/sec8/figs_sec8.py` | (from `sec8_stats.json`) | `loc_tiles.pdf`, `loc_window.pdf` |
| Airtime and scaling with N APs | `analysis/scaling/airtime_v3.py`, `scaling_model.py`, `fig_scaling.py` | measured inputs from c1–c3, t9–t16 | `airtime_v3.json`, `scaling_results.json` |
| Guard G = 3 µs | `analysis/guard/phase0.py` | t10–t16, c3_b2 | `guard_phase0.json` |
| Short sounding (1–2 pulses) | `analysis/pulse_emu/*.py` | t9, t10, t16 | `results.jsonl` |
| Trace-driven emulation of baselines | `analysis/emulation/emu.py` | t9, t10 | `emu_t9.json`, `emu_t10.json` |
| Method (SFN numbering) | `s5g/x410_pbch_check.py`, `s5g/compare_pbch.py`, `tools/pbch_ref/` | pbch_check_A/B, two_ap_sfn_A/B | see `docs/METHOD.md` |

## Headline numbers (read from the JSON files on 2026-11-09)

| Item | Value | Source |
|---|---|---|
| Two-way offset std, t18 (empty room, 80 µs burst, G = 3 µs) | 5.64 ns | `sec7_stats.json`, run t18_zc80_g3, `sigma` |
| TDEV at 30 s, t18 | 0.21 ns | idem, `tdev30` |
| Localization median A→B, t9 (in-sample, k = 1.12 fitted on t9) | 0.10 m | `sec8_stats.json`, `loc_summary` |
| Localization median A→B, t10 (rule fixed before t10) | 0.146 m | idem |
| Airtime at 100 Hz, N = 8, HE NDP 80 µs: S5G / ideal negotiated / TB | 6.88 % / 10.2 % / 26.6 % | `airtime_v3.json`, `airtime` |
| Max APs at 200 Hz, HE NDP: S5G | 58 | `airtime_v3.json`, `maxN` |
