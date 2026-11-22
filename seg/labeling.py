"""
labeling.py
-----------
Give every step one label.

    labeled_steps = label_steps(steps, definition)       # adds column 'label'

Labels are checked in definition order; the first label whose conditions hold wins.
Steps that match no label get settings.default_label (NO_LABEL).

Conditions (from 01_labels.yaml):
    column: [low, high]          range, null = open side, the order of low/high does not matter
    column: value                equality;  'A|B' = any of
    prev.column / next.column    the same condition on the previous / next step of the table
    limits may be formulas:      cap*0.95/3,  prev.Current_A_median*1.5,  prev['SOC_eval_start_%']

A missing value never matches (NaN, prev of the first step, next of the last step).
A column missing in the step file makes its condition never match (logged as a warning).
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from .definitions import FORMULA_FUNCTIONS

logger = logging.getLogger(__name__)


class _NeighbourStep:
    """`prev` / `next` inside formulas: prev.Current_A_median or prev['SOC_eval_start_%'].

    Returns the column shifted by one step (prev: value of the step before, next: the step after)."""

    def __init__(self, steps: pd.DataFrame, shift: int, missing_columns: set):
        self._steps, self._shift, self._missing_columns = steps, shift, missing_columns

    def __getitem__(self, column):
        if column not in self._steps.columns:
            self._missing_columns.add(column)
            return pd.Series(np.nan, index=self._steps.index)
        return pd.to_numeric(self._steps[column], errors="coerce").shift(self._shift)

    __getattr__ = __getitem__


def evaluate_limit(limit, names: dict, n_steps: int) -> np.ndarray:
    """Turn one range limit into a float array with one value per step.

    Parameters
    ----------
    limit   : number, formula string (checked by definitions.check_formula) or None (= open, NaN)
    names   : formula names (cap, vmax, …, prev, next, abs, min, max)
    n_steps : length of the step table

    Returns
    -------
    float array of length n_steps (NaN where the formula has no value)
    """
    if limit is None:
        return np.full(n_steps, np.nan)
    if isinstance(limit, str):
        limit = eval(compile(limit, "<formula>", "eval"), {"__builtins__": {}}, names)
    return np.broadcast_to(np.asarray(limit, dtype=float), (n_steps,))


def condition_mask(steps: pd.DataFrame, column: str, neighbour: str | None, condition: tuple,
                   names: dict, missing_columns: set) -> np.ndarray:
    """True where one condition holds.

    Parameters
    ----------
    steps           : step table (index 0..n-1)
    column          : step-feature column, e.g. "Current_A_median"
    neighbour       : None (this step), "prev" or "next"
    condition       : ("range", low, high) or ("equal", value | [values])
    names           : formula names (see label_steps)
    missing_columns : set that collects columns not found in `steps`

    Returns
    -------
    bool array of length len(steps)
    """
    n_steps = len(steps)
    if column not in steps.columns:
        missing_columns.add(column)
        return np.zeros(n_steps, bool)
    values = steps[column]
    if neighbour == "prev":
        values = values.shift(1)
    elif neighbour == "next":
        values = values.shift(-1)

    if condition[0] == "equal":
        wanted = condition[1] if isinstance(condition[1], list) else [condition[1]]
        if all(isinstance(v, (int, float)) for v in wanted):
            return pd.to_numeric(values, errors="coerce").isin(wanted).to_numpy()
        return values.astype(str).str.strip().isin([str(v) for v in wanted]).to_numpy() & values.notna().to_numpy()

    _, low_limit, high_limit = condition
    numbers = pd.to_numeric(values, errors="coerce").to_numpy(float)
    low, high = evaluate_limit(low_limit, names, n_steps), evaluate_limit(high_limit, names, n_steps)
    # null = open side, but NaN coming from a formula (e.g. prev of the first step) = no match
    formula_has_no_value = np.zeros(n_steps, bool)
    for raw_limit, limit_values in ((low_limit, low), (high_limit, high)):
        if isinstance(raw_limit, str):
            formula_has_no_value |= np.isnan(limit_values)
    low = np.where(np.isnan(low), -np.inf, low)
    high = np.where(np.isnan(high), np.inf, high)
    low, high = np.minimum(low, high), np.maximum(low, high)
    with np.errstate(invalid="ignore"):
        return (numbers >= low) & (numbers <= high) & ~np.isnan(numbers) & ~formula_has_no_value


def label_steps(steps: pd.DataFrame, definition: dict) -> pd.DataFrame:
    """Give every step one label (first matching label wins).

    Parameters
    ----------
    steps      : step-features table from TB_CPA_Evaluate (one row per step)
    definition : result of load_definition (uses 'labels', 'settings', 'formula_names')

    Returns
    -------
    Copy of `steps` with index 0..n-1 and a new column 'label'.
    Columns that a label needs but the table lacks are listed in
    result.attrs["missing_columns"] and logged once as a warning.

    Example
    -------
        definition = load_definition("cpa/rpt_standard", sample_config)
        labeled_steps = label_steps(pd.read_csv(step_file), definition)
        labeled_steps["label"].value_counts()
    """
    labeled_steps = steps.reset_index(drop=True).copy()
    n_steps = len(labeled_steps)
    missing_columns: set = set()
    names = {**definition["formula_names"], **FORMULA_FUNCTIONS,
             "prev": _NeighbourStep(labeled_steps, 1, missing_columns),
             "next": _NeighbourStep(labeled_steps, -1, missing_columns)}

    labels = np.full(n_steps, definition["settings"]["default_label"], dtype=object)
    still_unlabeled = np.ones(n_steps, bool)
    for label_name, blocks in definition["labels"].items():
        matches_label = np.zeros(n_steps, bool)
        for block in blocks:                              # blocks = OR
            matches_block = still_unlabeled.copy()
            for column, neighbour, condition in block:    # conditions in a block = AND
                matches_block &= condition_mask(labeled_steps, column, neighbour, condition, names, missing_columns)
            matches_label |= matches_block
        labels[matches_label] = label_name
        still_unlabeled &= ~matches_label

    if missing_columns:
        logger.warning(f"step table has no column(s) {sorted(missing_columns)}; conditions on them never match")
    labeled_steps["label"] = labels
    labeled_steps.attrs["missing_columns"] = sorted(missing_columns)
    logger.debug(f"labeled {n_steps} steps: "
                 f"{pd.Series(labels).value_counts().to_dict()}")
    return labeled_steps
