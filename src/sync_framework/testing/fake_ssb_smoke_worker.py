"""Harmless local stand-in for the continuous 5G SSB receiver."""

from __future__ import annotations

import argparse
import json
import signal
import time
from datetime import datetime, timezone
from pathlib import Path


def _row(iteration: int) -> dict:
    timestamp_ns = time.time_ns()
    return {
        "protocol_version": 1,
        "schema": "5g_ssb_rxgrid_jsonl_v1",
        "waveform_type": "5g_ssb",
        "profile_id": "n78_ssb_30khz",
        "iteration": iteration,
        "valid": True,
        "error": "",
        "rx_timestamp_ns": timestamp_ns,
        "timestamp_unix": timestamp_ns / 1e9,
        "timestamp_utc": datetime.fromtimestamp(timestamp_ns / 1e9, timezone.utc).isoformat(),
        "timestamp_semantics": "host_serialization_time_operational_only",
        "usrp": {"serial": "FAKE", "channel": 0, "gain_db": 60.0},
        "center_frequency_hz": 3541.44e6,
        "sample_rate_hz": 15.36e6,
        "cfo_hz": 100.0,
        "cfo_correction_enabled": True,
        "feature_name": "rxGridSSB",
        "feature_dtype": "complex64",
        "feature_shape": [240, 4],
        "feature_flatten_order": "C",
        "feature_count": 960,
        "complex_features": [{"real": 0.0, "imag": 0.0}] * 960,
        "numeric_metadata": {
            "nid2": 0,
            "pss_metric": 1.0,
            "timing_offset_samples": 0,
            "timing_offset_ms": 0.0,
            "n_symbols_extracted": 6,
            "rxgrid_mean_abs": 0.0,
            "rxgrid_median_abs": 0.0,
            "rxgrid_std_abs": 0.0,
            "rxgrid_max_abs": 0.0,
            "rxgrid_mean_power_db": -100.0,
            "capture_time_ms": 20.0,
            "pss_time_ms": 1.0,
            "ofdm_time_ms": 1.0,
            "dsp_time_ms": 2.0,
            "loop_time_ms": 20.0,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    output = Path(args.output_dir)
    (output / "runtime").mkdir(parents=True, exist_ok=True)
    stopping = False

    def stop(_signum, _frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    (output / "runtime" / "status.json").write_text(
        json.dumps({"status": "ready"}) + "\n", encoding="utf-8"
    )
    rows = 0
    with (output / "rxgridssb.jsonl").open("w", encoding="utf-8", buffering=1) as handle:
        while not stopping:
            handle.write(json.dumps(_row(rows), separators=(",", ":")) + "\n")
            rows += 1
            time.sleep(0.02)
    (output / "process.log").write_text(
        "=== Final statistics ===\n"
        f"iterations:         {rows}\n"
        f"valid grids:        {rows}\n"
        "invalid grids:      0\n"
        f"JSONL lines written:{rows}\n"
        f"output:             {output / 'rxgridssb.jsonl'}\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
