"""
labeling.py
-----------
Give every step one label.

    labeled_steps = label_steps(steps, definition)       # adds column 'label'
    item_masks    = item_condition_masks(labeled_steps, definition)   # for pattern / segment items

Labels are checked in definition order; the first label whose conditions hold wins.
Steps that match no label get settings.default_label (NO_LABEL).
label_steps runs twice per cell: on the original steps (pass 1) and on the merged table (pass 2).

Conditions (from 01_labels.yaml, also used by pattern / segment items):
    column: [low, high]          range, limits included, null = open side, the order of low/high does not matter
    column: value                equality;  'A|B' = any of
    prev.column / next.column    the same condition on the previous / next step of the table
    prev2.column / next3.column  ... on the step 2 before / 3 after (any number); this.column = column
    or.<name>: [{...}, {...}]    OR group: one block must hold; ANDed with the other rows
    - {...}  - {...}             list of blocks = OR for the whole label
    limits may be formulas:      cap*0.95/3,  prev.Current_A_median*1.5,  prev['SOC_eval_start_%'],
                                 this.Current_A_median*0.9,  prev2.Current_A_median

A missing value never matches (NaN, prev of the first step, next of the last step, next3 of
the last 3 steps, ...).
A column missing in the step file makes its condition never match (logged as a warning).
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from .definitions import FORMULA_FUNCTIONS, neighbour_shift

logger = logging.getLogger(__name__)


class _NeighbourStep:
    """`this` / `prev` / `next` / `prevN` / `nextN` inside formulas: prev.Current_A_median,
    prev2['SOC_eval_start_%'], this.Current_A_median.

    Returns the column shifted by `shift` steps (1: the step before, -1: the step after, 0: this step);
    rows without such a neighbour get NaN."""

    def __init__(self, steps: pd.DataFrame, shift: int, missing_columns: set):
        self._steps, self._shift, self._missing_columns = steps, shift, missing_columns

    def __getitem__(self, column):
        if column not in self._steps.columns:
            self._missing_columns.add(column)
            return pd.Series(np.nan, index=self._steps.index)
        return pd.to_numeric(self._steps[column], errors="coerce").shift(self._shift)

    __getattr__ = __getitem__

def _formula_scope(steps: pd.DataFrame, definition: dict, missing_columns: set) -> dict:
    """Names a formula may use: formula_names (cap, vmax, …), abs/min/max, and this / prev / next /
    prevN / nextN for `steps` (a neighbour name is resolved when the formula first uses it)."""
    return _FormulaScope({**definition["formula_names"], **FORMULA_FUNCTIONS}, steps, missing_columns)


class _FormulaScope(dict):
    """Formula names; this / prevN / nextN are created on first lookup (eval() uses __missing__)."""

    def __init__(self, names: dict, steps: pd.DataFrame, missing_columns: set):
        super().__init__(names)
        self._steps, self._missing_columns = steps, missing_columns

    def __missing__(self, name):
        shift = neighbour_shift(name)
        if shift is None:
            raise KeyError(name)
        self[name] = _NeighbourStep(self._steps, shift, self._missing_columns)
        return self[name]


def item_condition_masks(labeled_steps: pd.DataFrame, definition: dict,
                         missing_columns: set | None = None) -> dict:
    """Boolean mask per pattern / segment item with conditions.

    Parameters
    ----------
    labeled_steps   : step table with column 'label' (pass 2, i.e. the merged table)
    definition      : result of load_definition (uses 'item_conditions', 'formula_names')
    missing_columns : optional set; columns used by item conditions but not in the table are added to it

    Returns
    -------
    {item key: bool array of length len(labeled_steps)}, True where the step has one of the item's
    labels AND all its conditions hold; {} if the definition has no such items.
    Missing columns are logged as a warning; their conditions never match.

    Example
    -------
        item_masks = item_condition_masks(labeled_steps, definition)
        match_variants(list(labeled_steps["label"]), variants, item_masks)
    """
    item_conditions = definition.get("item_conditions") or {}
    if not item_conditions:
        return {}
    missing_here: set = set()
    names = _formula_scope(labeled_steps, definition, missing_here)
    labels = labeled_steps["label"].to_numpy(object)
    masks = {key: np.isin(labels, item["labels"]) & block_mask(labeled_steps, item["block"], names, missing_here)
             for key, item in item_conditions.items()}
    if missing_here:
        logger.warning(f"pattern item conditions: no column(s) {sorted(missing_here)}; they never match")
        if missing_columns is not None:
            missing_columns |= missing_here
    return masks

def evaluate_limit(limit, names: dict, n_steps: int) -> np.ndarray:
    """Turn one range limit into a float array with one value per step.

    Parameters
    ----------
    limit   : number, formula string (checked by definitions.check_formula) or None (= open, NaN)
    names   : formula names (cap, vmax, …, this, prev, next, prevN, nextN, abs, min, max)
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
    neighbour       : None or "this" (this step), "prev" / "next", "prev2" / "next3" / ...
    condition       : ("range", low, high) or ("equal", value | [values]);
                      ("any", …) groups are handled by block_mask
    names           : formula scope (see _formula_scope)
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
    shift = neighbour_shift(neighbour) if neighbour else 0
    if shift:
        values = values.shift(shift)          # rows without such a neighbour get NaN → no match

    if condition[0] == "equal":
        wanted = condition[1] if isinstance(condition[1], list) else [condition[1]]
        if all(isinstance(v, (int, float)) for v in wanted):
            return pd.to_numeric(values, errors="coerce").isin(wanted).to_numpy()
        return values.astype(str).str.strip().isin([str(v) for v in wanted]).to_numpy() & values.notna().to_numpy()

    _, low_limit, high_limit = condition
    numbers = pd.to_numeric(values, errors="coerce").to_numpy(float)
    low, high = evaluate_limit(low_limit, names, n_steps), evaluate_limit(high_limit, names, n_steps)
    # null = open side, but NaN coming from a formula (e.g. prev of the first step, next3 near the end) = no match
    formula_has_no_value = np.zeros(n_steps, bool)
    for raw_limit, limit_values in ((low_limit, low), (high_limit, high)):
        if isinstance(raw_limit, str):
            formula_has_no_value |= np.isnan(limit_values)
    low = np.where(np.isnan(low), -np.inf, low)
    high = np.where(np.isnan(high), np.inf, high)
    low, high = np.minimum(low, high), np.maximum(low, high)
    with np.errstate(invalid="ignore"):
        return (numbers >= low) & (numbers <= high) & ~np.isnan(numbers) & ~formula_has_no_value

def block_mask(steps: pd.DataFrame, block: list, names: dict, missing_columns: set) -> np.ndarray:
    """True where all conditions of one block hold. An ("any", [block, ...]) condition
    (or.<name> group) holds where at least one of its blocks holds."""
    mask = np.ones(len(steps), bool)
    for column, neighbour, condition in block:
        if condition[0] == "any":
            mask &= np.logical_or.reduce([block_mask(steps, sub, names, missing_columns)
                                          for sub in condition[1]])
        else:
            mask &= condition_mask(steps, column, neighbour, condition, names, missing_columns)
    return mask

def label_steps(steps: pd.DataFrame, definition: dict) -> pd.DataFrame:
    """Give every step one label (first matching label wins).

    Parameters
    ----------
    steps      : step-features table from TB_CPA_Evaluate (one row per step), or the merged
                 table from merge_steps (pass 2)
    definition : result of load_definition (uses 'labels', 'settings', 'formula_names')

    Returns
    -------
    Copy of `steps` with index 0..n-1 and a new column 'label'.
    Columns that a label needs but the table lacks are listed in
    result.attrs["missing_columns"] and logged once as a warning.

    Example
    -------
        definition = load_definition("custom/c48t1_definitions", sample_config)
        labeled_steps = label_steps(pd.read_csv(step_file), definition)
        labeled_steps["label"].value_counts()
    """
    labeled_steps = steps.reset_index(drop=True).copy()
    n_steps = len(labeled_steps)
    missing_columns: set = set()
    names = _formula_scope(labeled_steps, definition, missing_columns)

    labels = np.full(n_steps, definition["settings"]["default_label"], dtype=object)
    still_unlabeled = np.ones(n_steps, bool)
    for label_name, blocks in definition["labels"].items():
        matches_label = np.zeros(n_steps, bool)
        for block in blocks:
            matches_label |= still_unlabeled & block_mask(labeled_steps, block, names, missing_columns)
        labels[matches_label] = label_name
        still_unlabeled &= ~matches_label

    if missing_columns:
        logger.warning(f"step table has no column(s) {sorted(missing_columns)}; conditions on them never match")
    labeled_steps["label"] = labels
    labeled_steps.attrs["missing_columns"] = sorted(missing_columns)
    logger.debug(f"labeled {n_steps} steps: "
                 f"{pd.Series(labels).value_counts().to_dict()}")
    return labeled_steps
