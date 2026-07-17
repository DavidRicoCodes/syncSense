#!/usr/bin/env bash
set -euo pipefail
repo_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
exec python3 "$repo_dir/tools/run_wifi_threshold_campaign.py" --threshold 0.80 "$@"
