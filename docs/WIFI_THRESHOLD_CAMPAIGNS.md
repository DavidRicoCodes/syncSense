# WiFi detector threshold campaigns

## Scope

`tools/run_wifi_threshold_campaign.py` uses the validated `wifi_link_smoke`
workflow and accepts the detector threshold through the required
`--threshold` argument.

Their default cadence reproduces the 2026-07-16 campaign:

- 590 transmitted beacons per run;
- 3 cycles;
- 4 active hours per cycle;
- 10 runs per active hour;
- 1 hour between cycles;
- 120 runs in total.

The runner accepts optional cadence arguments such as `--cycles`,
`--active-hours`, `--runs-per-hour`, `--num-beacons`, `--seed`, and
`--cycle-gap-minutes`. Use `--dry-run` to print the complete plan without
creating a campaign or touching hardware.

## Recommended launch commands

Run these commands on Spark (`/home/nextnet/sync`). They start detached tmux
sessions so the campaign survives an SSH disconnection.

```bash
tmux new-session -d -s wifi-thr085-night \
  'cd /home/nextnet/sync && python3 tools/run_wifi_threshold_campaign.py --threshold 0.85'
```

After that session has finished:

```bash
tmux new-session -d -s wifi-thr080 \
  'cd /home/nextnet/sync && python3 tools/run_wifi_threshold_campaign.py --threshold 0.80'
```

After the second session has finished:

```bash
tmux new-session -d -s wifi-thr090 \
  'cd /home/nextnet/sync && python3 tools/run_wifi_threshold_campaign.py --threshold 0.90'
```

Do not run two campaigns at once. A global file lock also rejects concurrent
WiFi hardware campaigns.

Check progress with:

```bash
tmux ls
tmux attach -t wifi-thr085-night
ls -td /srv/sync-experiments/campaigns/wifi590_thr* | head
```

## Outputs

Every run retains the existing features, CSI, process logs, state, receipts,
checksums, Git provenance, manifests, and dummy inference output. It also adds:

- `rx_wifi/frame-timings.jsonl`: one timing record per accepted beacon;
- `rx_wifi/block-timings.jsonl`: one timing record per processed IQ block.

The frame trace includes queue wait, block DSP time, JSON write, CF32 write,
combined local output time, block-to-output latency, and estimated
packet-start/packet-end-to-output latency.

The packet start/end estimates are operational host measurements. They use the
sample offset relative to the completed UHD block and therefore include
unmeasured USB/host delivery uncertainty; they are not RF-hardware timestamps.

At successful campaign completion,
`wifi_threshold_runs.csv` is generated in the campaign directory. It contains
the existing run-level results plus mean, median, p95, p99, and maximum timing
values.
