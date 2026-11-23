"""
merge.py
--------
Join steps that the cycler split, before the final labelling.

    merged_steps = merge_steps(labeled_steps, definition["settings"]["merge"])

Rules (00_settings.yaml → settings.merge), applied on the labels of a first labelling pass:

    join_adjacent: [REST]                        consecutive steps with the same label → one row
    bridges:                                     A, BRIDGE, B → one row when A and B have the same mode
      REST_SPIKE:    3                           max. bridges of this label inside one merged row
      CURRENT_SPIKE: 5                           (null = no limit); above the limit the bridge stays a step

A bridge is left out of the merged values (sums, means, …); it only counts in n_bridged / bridged_s.
GAP rows are never joined unless their label is listed in join_adjacent.

How columns are combined: COLUMN_RULES (first matching rule wins). A numeric column without a rule
takes the first part's value; it is logged once if its values differ inside a merged row.

Added columns:
    orig_pos_start, orig_pos_end   first / last row of the original step table (inclusive)
    n_merged                       original rows in this row (bridges included)
    n_bridged, bridged_s           bridges dropped and their total duration
    merged_step_ids                'id|id|…' of the original steps ('' if not merged)
"""
from __future__ import annotations

import logging
import re

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

MODE_COLUMN = "mode"
DURATION_COLUMN = "time_duration_s"
MERGE_COLUMNS = ["orig_pos_start", "orig_pos_end", "n_merged", "n_bridged", "bridged_s", "merged_step_ids"]

# Time-based columns: <name>_at_<t>s = value t s after the step start (e.g. Voltage_V_at_18s).
# Negative times (_at_-18s) are not supported and fall through to the other rules.
# Adapt this pattern if your step features use another naming.
ELAPSED_PATTERN = r"_at_(\d+(?:\.\d+)?)s$"        # value t s after the step start

# (regex on the column name, rule) — first match wins. Edit here when step-feature columns change.
COLUMN_RULES = [
    (ELAPSED_PATTERN,                                         "elapsed"),   # value at t s after start
    (r"^(row_start|step_id)$",                                "first"),
    (r"^row_end$",                                            "last"),
    (r"^time_duration_s$|^delta_",                            "sum"),
    (r"_start(_|$)",                                          "first"),
    (r"_end(_|$)",                                            "last"),
    (r"_std(_|$)",                                            "max"),     # conservative
    (r"_min(_|$)",                                            "min"),
    (r"_max(_|$)",                                            "max"),
    (r"_(median|mean)(_|$)|^cc_current_A$|^phase_coverage$",  "weighted_mean"),   # weight = duration
]


def column_rule(column: str) -> str | None:
    """Rule for one column name (see COLUMN_RULES); None = no rule (first part's value)."""
    return next((rule for pattern, rule in COLUMN_RULES if re.search(pattern, column)), None)


def merge_groups(labels, modes, join_adjacent: set, bridges: dict) -> tuple[np.ndarray, np.ndarray]:
    """Group number per step (same number = one merged row) and which steps are dropped bridges.

    Parameters
    ----------
    labels        : first-pass label of every step
    modes         : value of the mode column of every step
    join_adjacent : labels whose direct repeats are joined
    bridges       : {label: max per merged row or None}

    Returns
    -------
    (group, is_bridge): int array (group number per step, 0, 1, … in order) and bool array
    (True for a bridge that is dropped from the merged values)

    Example
    -------
        merge_groups(["REST", "CURRENT_SPIKE", "REST", "REST", "CC_CHA_C3"],
                     ["REST", "CHARGE", "REST", "REST", "CHARGE"], {"REST"}, {"CURRENT_SPIKE": 5})
        → groups [0, 0, 0, 0, 1], bridges [F, T, F, F, F]
    """
    n = len(labels)
    group, is_bridge = np.zeros(n, int), np.zeros(n, bool)
    current, last_label, bridge_count = 0, labels[0] if n else None, {}
    i = 1
    while i < n:
        label = labels[i]
        limit = bridges.get(label, 0)
        if (label in bridges and i + 1 < n
                and labels[i - 1] not in bridges and labels[i + 1] not in bridges
                and pd.notna(modes[i - 1]) and modes[i - 1] == modes[i + 1]
                and (limit is None or bridge_count.get(label, 0) < limit)):
            group[i] = group[i + 1] = current
            is_bridge[i] = True
            bridge_count[label] = bridge_count.get(label, 0) + 1
            last_label = labels[i + 1]
            i += 2
            continue
        if not (label in join_adjacent and label == last_label):
            current += 1
            bridge_count = {}
        group[i] = current
        last_label = label
        i += 1
    return group, is_bridge


def merge_steps(labeled_steps: pd.DataFrame, merge_settings: dict) -> pd.DataFrame:
    """Join split steps (see module docstring).

    Parameters
    ----------
    labeled_steps  : step table with column 'label' from a first label_steps pass
    merge_settings : {"join_adjacent": set of labels, "bridges": {label: max or None}}

    Returns
    -------
    New step table (index 0..m-1) without 'label', plus MERGE_COLUMNS. Without merge settings
    every row stays as it is (orig_pos_start == orig_pos_end).

    Example
    -------
        first_pass = label_steps(steps, definition)
        merged     = merge_steps(first_pass, definition["settings"]["merge"])
        merged[merged["n_merged"] > 1][["orig_pos_start", "orig_pos_end", "n_bridged", "merged_step_ids"]]
    """
    steps = labeled_steps.reset_index(drop=True)
    n = len(steps)
    join_adjacent = set(merge_settings.get("join_adjacent") or ())
    bridges = dict(merge_settings.get("bridges") or {})
    labels = steps["label"].to_numpy(object)
    if MODE_COLUMN in steps.columns:
        modes = steps[MODE_COLUMN].to_numpy(object)
    else:
        if bridges:
            logger.warning(f"step table has no '{MODE_COLUMN}' column; bridges are not merged")
        modes, bridges = np.full(n, np.nan, object), {}

    group, is_bridge = merge_groups(labels, modes, join_adjacent, bridges)
    positions = np.arange(n)
    duration = (pd.to_numeric(steps[DURATION_COLUMN], errors="coerce").to_numpy(float)
                if DURATION_COLUMN in steps.columns else np.full(n, np.nan))

    keep = ~is_bridge                                         # parts whose values are combined
    parts = steps[keep]
    part_group = group[keep]
    first_pos = pd.Series(positions[keep]).groupby(part_group).min().to_numpy()
    last_pos = pd.Series(positions[keep]).groupby(part_group).max().to_numpy()
    group_size = np.bincount(group)
    merged_groups = np.flatnonzero(group_size > 1)

    merged, unruled_differs = {}, []
    for column in steps.columns:
        if column == "label":
            continue
        rule = column_rule(column)
        values = steps[column]
        numeric = pd.api.types.is_numeric_dtype(values)
        if rule == "elapsed":
            # t s after the merged start lies in the first part only if that part lasts >= t s;
            # otherwise the value was never computed → NaN
            t = float(re.search(ELAPSED_PATTERN, column).group(1))
            value = pd.to_numeric(values, errors="coerce").to_numpy(float)[first_pos]
            with np.errstate(invalid="ignore"):
                long_enough = duration[first_pos] >= t  # NaN duration → not long enough
            merged[column] = np.where((group_size > 1) & ~long_enough, np.nan, value)
            continue
        if rule is None or not numeric or rule in ("first", "last"):
            source = last_pos if rule == "last" else first_pos
            merged[column] = values.to_numpy()[source]
            if rule is None and numeric and len(merged_groups):
                spread = parts[column].groupby(part_group).nunique(dropna=False)
                if (spread.reindex(merged_groups).fillna(1) > 1).any():
                    unruled_differs.append(column)
            continue
        part_values = pd.to_numeric(parts[column], errors="coerce")
        by_group = part_values.groupby(part_group)
        if rule == "sum":
            merged[column] = by_group.sum(min_count=1).to_numpy()
        elif rule == "min":
            merged[column] = by_group.min().to_numpy()
        elif rule == "max":
            merged[column] = by_group.max().to_numpy()
        else:                                                 # weighted_mean
            weight = pd.Series(duration[keep], index=parts.index)
            valid = part_values.notna() & weight.notna() & (weight > 0)
            weighted = (part_values * weight).where(valid).groupby(part_group).sum(min_count=1)
            weight_sum = weight.where(valid).groupby(part_group).sum(min_count=1)
            plain_mean = by_group.mean()
            merged[column] = (weighted / weight_sum).fillna(plain_mean).to_numpy()

    merged_steps = pd.DataFrame(merged)
    all_by_group = pd.Series(positions).groupby(group)
    merged_steps["orig_pos_start"] = all_by_group.min().to_numpy()
    merged_steps["orig_pos_end"] = all_by_group.max().to_numpy()
    merged_steps["n_merged"] = group_size
    merged_steps["n_bridged"] = np.bincount(group, weights=is_bridge).astype(int)
    merged_steps["bridged_s"] = np.bincount(group, weights=np.where(is_bridge, np.nan_to_num(duration), 0.0))
    step_ids = steps["step_id"].astype(str).to_numpy() if "step_id" in steps.columns else positions.astype(str)
    merged_steps["merged_step_ids"] = [("|".join(step_ids[group == g]) if size > 1 else "")
                                       for g, size in enumerate(group_size)]

    if unruled_differs:
        logger.warning(f"merge: no combine rule for {unruled_differs}; the first part's value is used "
                       f"(add a rule in seg/merge.py COLUMN_RULES)")
    logger.debug(f"merge: {n} steps → {len(merged_steps)} "
                 f"({int(is_bridge.sum())} bridge(s) dropped, {len(merged_groups)} merged row(s))")
    return merged_steps
