"""
run.py
------
Run label → merge → label → patterns → segments → chains for one cell or a whole sample.

    labeled_steps, detections = segment_cell(steps, definition, cell_id)   # one cell, in memory
    summary = process_sample(sample_name, sample_config)                   # all cells → files

Files per cell, next to the step features (<base_path>/04_Evaluated_Data/<cell_id>/):
    <cell_id>_segments.csv        one row per detection and per part (DETECTION_COLUMNS),
                                  positions refer to the original step file
    <cell_id>_steps_labeled.csv   optional: merged step table + pass-2 label + merge columns
    <cell_id>_segments_plot.html  optional: current coloured by label + detections (seg/plot.py)
    <cell_id>_segments_meta.json  fingerprint for skip/rerun
Log per sample (like TB_CPA_Evaluate): <base_path>/06_Logs/debug_logs/segmentation_debug_<host>.log
"""
from __future__ import annotations

import hashlib
import json
import logging
import socket
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
import yaml

from .chains import find_chains
from .definitions import load_definition
from .labeling import item_condition_masks, label_steps
from .patterns import find_patterns
from .segments import find_segments
from .merge import merge_steps

logger = logging.getLogger(__name__)
PACKAGE_LOGGER = logging.getLogger("seg")
LOG_FORMAT = "%(asctime)s  %(levelname)-8s  %(message)s"

SEG_V2_VERSION = "2.1"   # bump on any labeling/merge/pattern/segment/chain logic change → all cells rerun
SAMPLES_FILE = Path(__file__).resolve().parents[1] / "samples.yaml"
STEP_FILE_GLOB = "*step*feature*.csv"
REQUIRED_STEP_COLUMNS = ["step_id", "row_start", "row_end", "eval_version"]
DETECTION_COLUMNS = ["cell_id", "level", "name", "variant", "instance", "part", "part_index", "part_kind",
                     "part_variant", "part_instance", "step_id_start", "step_id_end",
                     "row_start", "row_end", "n_steps", "step_pos_start", "step_pos_end"]


# ---- logging ------------------------------------------------------------------------------------------
def setup_logging(console_level: int = logging.INFO) -> None:
    """Log the 'seg' package to the console (call once, e.g. in run_segmentation.py).

    Parameters
    ----------
    console_level : logging.INFO (default) or logging.DEBUG for per-pattern details

    The per-sample log file is added by process_sample (always at DEBUG level).
    """
    PACKAGE_LOGGER.setLevel(logging.DEBUG)
    PACKAGE_LOGGER.propagate = False
    if not any(isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
               for h in PACKAGE_LOGGER.handlers):
        console = logging.StreamHandler()
        console.setLevel(console_level)
        console.setFormatter(logging.Formatter(LOG_FORMAT))
        PACKAGE_LOGGER.addHandler(console)


def _open_log_file(log_folder: Path) -> logging.FileHandler | None:
    """Add a DEBUG file handler to the 'seg' logger; None (with a warning) if the folder is not writable."""
    try:
        log_folder.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(log_folder / f"segmentation_debug_{socket.gethostname()}.log", encoding="utf-8")
    except OSError as exc:
        logger.warning(f"cannot write log file in {log_folder}: {exc}")
        return None
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    PACKAGE_LOGGER.addHandler(handler)
    if PACKAGE_LOGGER.level == logging.NOTSET or PACKAGE_LOGGER.level > logging.DEBUG:
        PACKAGE_LOGGER.setLevel(logging.DEBUG)
    return handler


# ---- one cell -----------------------------------------------------------------------------------------
def read_samples(samples_file: Path | str = SAMPLES_FILE) -> dict:
    """Read samples.yaml.

    Returns
    -------
    {sample_name: {base_path, nominal_capacity, nominal_energy, max_volt, min_volt, vbc_volt, definition, ...}}
    """
    with open(samples_file, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def check_step_table(steps: pd.DataFrame) -> str:
    """Check that the step table comes from TB_CPA_Evaluate eval_steps >= 2.0.

    Returns
    -------
    eval_version of the file (e.g. "3.0")

    Raises
    ------
    ValueError if required columns are missing or eval_version < 2.0
    """
    if missing := [c for c in REQUIRED_STEP_COLUMNS if c not in steps.columns]:
        raise ValueError(f"step table misses {missing}; rerun TB_CPA_Evaluate (eval_steps >= 2.0)")
    versions = steps["eval_version"].dropna()
    eval_version = str(versions.iloc[0]) if len(versions) else ""
    try:
        major = float(eval_version.split(".")[0])
    except ValueError:
        major = 0
    if major < 2:
        raise ValueError(f"step file has eval_version '{eval_version}', needs >= 2.0; rerun TB_CPA_Evaluate")
    return eval_version


def segment_cell(steps: pd.DataFrame, definition: dict, cell_id: str = "") -> tuple[pd.DataFrame, pd.DataFrame]:
    """Label (pass 1) → merge → label (pass 2) → patterns → segments → chains for one cell.

    Parameters
    ----------
    steps      : step-features table of one cell (TB_CPA_Evaluate, eval_steps >= 2.0)
    definition : result of load_definition
    cell_id    : written into the 'cell_id' column

    Returns
    -------
    (labeled_steps, detections)
      labeled_steps : the MERGED step table (index 0..m-1) with pass-2 'label' and the merge columns
                      (orig_pos_start, orig_pos_end, n_merged, n_bridged, bridged_s, merged_step_ids)
      detections    : DataFrame with DETECTION_COLUMNS, one row per detection ('(whole)') and per part.
                      Positions refer to the ORIGINAL step file:
                      Steps of a row:    steps.reset_index(drop=True).iloc[step_pos_start : step_pos_end + 1]
                      Raw data of a row: processed_data parquet .iloc[row_start : row_end]

    Raises
    ------
    ValueError from check_step_table if the step table is too old or misses columns

    Example
    -------
        definition = load_definition("custom/c48t1_definitions", read_samples()["C48T1_B3"])
        labeled_steps, detections = segment_cell(pd.read_csv(step_file), definition, "FCA08J0011")
        detections[detections.part == "(whole)"]
    """
    check_step_table(steps)
    original = steps.reset_index(drop=True)
    first_pass = label_steps(original, definition)                       # finds the bridges
    labeled_steps = label_steps(merge_steps(first_pass, definition["settings"]["merge"]), definition)
    # item masks once per cell; their missing columns go into the same list as the label ones (run summary)
    missing_columns = set(labeled_steps.attrs.get("missing_columns") or [])
    item_masks = item_condition_masks(labeled_steps, definition, missing_columns)
    labeled_steps.attrs["missing_columns"] = sorted(missing_columns)
    rows = find_patterns(labeled_steps, definition, item_masks)
    segment_rows = find_segments(labeled_steps, definition, item_masks)
    rows += segment_rows + find_chains(labeled_steps, segment_rows, definition)

    # positions above refer to the merged table → translate to the original step file
    step_ids = original["step_id"].to_numpy(float)
    row_starts, row_ends = original["row_start"].to_numpy(), original["row_end"].to_numpy()
    orig_start, orig_end = labeled_steps["orig_pos_start"].to_numpy(), labeled_steps["orig_pos_end"].to_numpy()
    for row in rows:
        first, last = int(orig_start[row["step_pos_start"]]), int(orig_end[row["step_pos_end"]])
        row.update(cell_id=cell_id, step_id_start=step_ids[first], step_id_end=step_ids[last],
                   row_start=int(row_starts[first]), row_end=int(row_ends[last]), n_steps=last - first + 1,
                   step_pos_start=first, step_pos_end=last)

    detections = pd.DataFrame(rows, columns=DETECTION_COLUMNS)
    detections["part_instance"] = detections["part_instance"].astype("Int64")
    detections["part_variant"] = detections["part_variant"].fillna("")
    return labeled_steps, detections


# ---- a whole sample ------------------------------------------------------------------------------------
def _md5(obj) -> str:
    return hashlib.md5(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()


def cell_fingerprint(step_file: Path, definition: dict, sample_config: dict) -> dict:
    """Everything that decides a cell's result; a cell is skipped when this is unchanged.

    Returns
    -------
    {step_file, step_file_mtime, definition, definition_hash, sample_hash, seg_v2_version}
    """
    return {"step_file": step_file.name,
            "step_file_mtime": step_file.stat().st_mtime,
            "definition": definition["name"], "definition_hash": definition["hash"],
            "sample_hash": _md5({k: v for k, v in sample_config.items() if k != "base_path"}),
            "seg_v2_version": SEG_V2_VERSION}


def find_step_files(base_path: Path | str, cell_ids: list | None = None) -> dict:
    """Step-features files of a sample: {cell_id: path} from <base_path>/04_Evaluated_Data/<cell_id>/.

    Parameters
    ----------
    base_path : sample base path
    cell_ids  : None / [] = all cells, else only these (missing ones are logged)

    Returns
    -------
    {cell_id: Path}; with several matching files in one folder the last in alphabetical order wins (warning)
    """
    evaluated_folder = Path(base_path) / "04_Evaluated_Data"
    step_files = {}
    for path in sorted(evaluated_folder.glob(f"*/{STEP_FILE_GLOB}")):
        if path.parent.name in step_files:
            logger.warning(f"{path.parent.name}: several step-features files, using {path.name} "
                           f"(not {step_files[path.parent.name].name})")
        step_files[path.parent.name] = path
    if cell_ids:
        if missing := sorted(set(cell_ids) - set(step_files)):
            logger.warning(f"no step-features file for {missing} in {evaluated_folder}")
        step_files = {c: p for c, p in step_files.items() if c in cell_ids}
    if not step_files:
        logger.warning(f"no step-features files found under {evaluated_folder}")
    return step_files


def process_sample(sample_name: str, sample_config: dict, cell_ids: list | None = None,
                   skip_rerun: bool = True, export_labels: bool = False,
                   definition_name: str | None = None, log_folder: Path | str | None = None,
                   export_plot: bool = False) -> pd.DataFrame:
    """Run all cells of one sample (or only `cell_ids`) and write the files.

    Parameters
    ----------
    sample_name     : key in samples.yaml (only used for messages)
    sample_config   : the sample's entry in samples.yaml (base_path, nominal_capacity, …, definition)
    cell_ids        : None / [] = all cells
    skip_rerun      : skip cells whose fingerprint (cell_fingerprint) is unchanged
    export_labels   : also write <cell_id>_steps_labeled.csv
    definition_name : use this definition instead of sample_config['definition']
    log_folder      : log file folder; None = <base_path>/06_Logs/debug_logs
    export_plot     : also write <cell_id>_segments_plot.html (needs plotly)

    Returns
    -------
    Summary, one row per cell: sample, cell_id, status (done / skipped / failed),
    patterns, segments, chains (instance counts), seconds, message

    Raises
    ------
    DefinitionError if the definition has problems (before any cell is processed)
    ValueError if the sample has no 'definition' or no 'base_path'
    """
    definition_name = definition_name or sample_config.get("definition")
    if not definition_name:
        raise ValueError(f"sample '{sample_name}' has no 'definition:' in samples.yaml")
    if not sample_config.get("base_path"):
        raise ValueError(f"sample '{sample_name}' has no 'base_path:' in samples.yaml")
    log_file = _open_log_file(Path(log_folder) if log_folder else
                              Path(sample_config["base_path"]) / "06_Logs" / "debug_logs")
    try:
        logger.info(f">>>>>>>>>> segmentation v2  {sample_name}  (definition {definition_name}) "
                    f"started {datetime.now():%Y-%m-%d %H:%M:%S}")
        definition = load_definition(definition_name, sample_config)
        step_files = find_step_files(sample_config["base_path"], cell_ids)
        logger.info(f"{len(step_files)} cell(s) to consider")
        summary = [_process_cell(sample_name, cell_id, step_file, definition, sample_config,
                                 skip_rerun, export_labels, export_plot)
                   for cell_id, step_file in step_files.items()]
        summary = pd.DataFrame(summary)
        if not summary.empty:
            logger.info(f"[Done] {sample_name}: {summary['status'].value_counts().to_dict()}")
        return summary
    finally:
        if log_file is not None:
            PACKAGE_LOGGER.removeHandler(log_file)
            log_file.close()


def _process_cell(sample_name, cell_id, step_file, definition, sample_config, skip_rerun, export_labels,
                  export_plot=False) -> dict:
    """One cell of process_sample: skip check, segment_cell, write files. Never raises."""
    output_folder = step_file.parent
    segments_csv = output_folder / f"{cell_id}_segments.csv"
    meta_json = output_folder / f"{cell_id}_segments_meta.json"
    labeled_csv = output_folder / f"{cell_id}_steps_labeled.csv"
    plot_html = output_folder / f"{cell_id}_segments_plot.html"
    result = {"sample": sample_name, "cell_id": cell_id, "status": "", "patterns": 0, "segments": 0,
              "chains": 0, "seconds": 0.0, "message": ""}
    started = time.perf_counter()
    try:
        fingerprint = cell_fingerprint(step_file, definition, sample_config)
        outputs_exist = (segments_csv.exists() and meta_json.exists() and (labeled_csv.exists() or not export_labels)
                         and (plot_html.exists() or not export_plot))
        if skip_rerun and outputs_exist:
            stored = json.loads(meta_json.read_text(encoding="utf-8"))
            if all(stored.get(k) == v for k, v in fingerprint.items()):
                logger.info(f"  SKIP  {cell_id}  (step file, definition and sample config unchanged)")
                return {**result, "status": "skipped"}

        logger.info(f"  RUN   {cell_id}")
        steps = pd.read_csv(step_file, encoding="utf-8-sig", low_memory=False)
        labeled_steps, detections = segment_cell(steps, definition, cell_id)
        detections.to_csv(segments_csv, index=False)
        if export_labels:
            labeled_steps.to_csv(labeled_csv, index=False)
        plot_problem = ""
        if export_plot:
            try:                                           # a failed plot must not fail the cell
                from .plot import plot_cell                # plotly only needed when plots are wanted
                plot_cell(steps, labeled_steps, detections, plot_html,
                          title=f"{cell_id} · {sample_name} · {definition['name']}",
                          default_label=definition["settings"]["default_label"])
            except Exception as exc:  # noqa: BLE001
                plot_problem = f"plot failed: {type(exc).__name__}: {exc}"
                logger.warning(f"  {cell_id}  {plot_problem}")
                plot_html.unlink(missing_ok=True)          # so the next run with SKIP_RERUN tries again
        meta_json.write_text(json.dumps({**fingerprint, "eval_version": check_step_table(steps),
                                         "written_at": datetime.now().isoformat(timespec="seconds")},
                                        indent=2), encoding="utf-8")
        counts = detections.loc[detections["part"] == "(whole)", "level"].value_counts()
        missing = labeled_steps.attrs.get("missing_columns") or []
        seconds = round(time.perf_counter() - started, 2)
        logger.info(f"  OK    {cell_id}  patterns={counts.get('pattern', 0)}  segments={counts.get('segment', 0)}  "
                    f"chains={counts.get('chain', 0)}  [{seconds}s]")
        return {**result, "status": "done", "patterns": int(counts.get("pattern", 0)),
                "segments": int(counts.get("segment", 0)), "chains": int(counts.get("chain", 0)),
                "seconds": seconds,
                "message": "; ".join(m for m in (f"missing columns: {missing}" if missing else "", plot_problem) if m)}
    except Exception as exc:  # noqa: BLE001 - one bad cell must not stop the sample
        logger.error(f"  FAIL  {cell_id}  {type(exc).__name__}: {exc}")
        logger.debug("traceback", exc_info=True)
        return {**result, "status": "failed", "seconds": round(time.perf_counter() - started, 2),
                "message": f"{type(exc).__name__}: {exc}"}
