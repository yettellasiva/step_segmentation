"""
run_segmentation.py  —  TB_CPA_Segmentation_v2
-----------------------------------------------
For every selected sample in samples.yaml:
    step features (TB_CPA_Evaluate) → labels → merge → labels → patterns → segments → chains
    → <base_path>/04_Evaluated_Data/<cell_id>/<cell_id>_segments.csv

Each sample picks its definition folder with `definition: <folder>` (e.g. custom/c48t1_definitions).
Log: console + <base_path>/06_Logs/debug_logs/segmentation_debug_<host>.log per sample.
"""
import logging
from pathlib import Path

import pandas as pd

from seg import DefinitionError, process_sample, read_samples, setup_logging

# ---------------------------------------------------------------------------
# USER CONFIGURATION
# ---------------------------------------------------------------------------
SAMPLES       = ["C48T1_B1"]      # None = all samples in samples.yaml, or e.g. ["C48T1_C1-2-03S"]
CELL_IDS      = ["FCA08J0011"]        # [] = all cells of each sample, or e.g. ["FCA08J0011"]
SKIP_RERUN    = False      # skip cells whose step file, definition and sample config are unchanged
EXPORT_LABELS = True     # also write <cell_id>_steps_labeled.csv (merged step table + label)
EXPORT_PLOT   = True     # also write <cell_id>_segments_plot.html (current by label + detections, needs plotly)
LOG_FOLDER    = None      # None = <base_path>/06_Logs/debug_logs of each sample
CONSOLE_LEVEL = logging.INFO   # logging.DEBUG shows per-pattern / per-segment counts
SUMMARY_CSV   = Path(__file__).parent / "last_run_summary.csv"

# ---------------------------------------------------------------------------
logger = logging.getLogger("seg.run_segmentation")

if __name__ == "__main__":
    setup_logging(CONSOLE_LEVEL)
    samples = read_samples()
    sample_names = SAMPLES or list(samples)
    if unknown := [s for s in sample_names if s not in samples]:
        raise SystemExit(f"Not in samples.yaml: {unknown}")

    summaries = []
    for name in sample_names:
        try:
            summaries.append(process_sample(name, samples[name], CELL_IDS or None, SKIP_RERUN, EXPORT_LABELS,
                                            log_folder=LOG_FOLDER, export_plot=EXPORT_PLOT))
        except (DefinitionError, ValueError) as exc:     # bad definition / config: skip this sample only
            logger.error(f"{name}: not run\n{exc}")
            summaries.append(pd.DataFrame([{"sample": name, "status": "failed", "message": str(exc)}]))
    summary = pd.concat(summaries, ignore_index=True) if summaries else pd.DataFrame()
    summary.to_csv(SUMMARY_CSV, index=False)
    if not summary.empty:
        logger.info(f"All samples: {summary['status'].value_counts().to_dict()}  → {SUMMARY_CSV}")
