# Raw data (not in git)

All copies are on PC5 unless stated otherwise. Verify with the SHA256 files
before relying on a copy.

| Location | Contents | Size | Integrity |
|---|---|---|---|
| `~/sync_backups/x410_two_ap_results_20261014/` | curated backup: runs t4–t18 and c1–c3 (`runs/`), analysis with intermediate files (`analysis/`), figures, code snapshot (`code/current_2026-10-21` = version of t18), status snapshot | 5.2 GB | `SHA256SUMS` |
| `~/sync_backups/x410_testmultiband_full_20261109/` | full mirror of `~/syncSense/testmultiband` of both APs (`wifitx/`, `wifirx/`): every run including the early ones (two_ap_*, pbch_check_*, sense_L_*, t2_*, t3_*, aborted runs), logs, scans, MATLAB references | 5.6 GB | `*/SHA256SUMS_pc5.txt` (identical to the server-side sums) |
| `~/sync_backups/claude_scratchpads_20261109/` | copy of `/tmp/claude-1000` (Claude Code scratchpads). Several analysis scripts read/write absolute paths there (Sec. VIII and its exclusion variants, t14, dashboards) | 6.4 GB | `SHA256SUMS_backup.txt` |
| `wifitx:~/x410_backup/20261014`, `20261015`; same on `wifirx` | server-side copies of the runs | 2.6 GB each | — |
| `wifitx`/`wifirx:~/syncSense/testmultiband/` | live working folders on the APs | 2.8 GB each | — |

If `/tmp` is wiped (reboot), restore the scratchpads to their original paths so
the analysis scripts keep working unchanged:

```bash
rsync -a ~/sync_backups/claude_scratchpads_20261109/claude-1000/ /tmp/claude-1000/
```

Each run folder holds `summary.json` (all arguments and results), `sense_rx.npz`
(per-burst channels), `tx_log.jsonl` and `nr_events.jsonl`; the AP A folder ends in
`_A`, AP B in `_B`.
