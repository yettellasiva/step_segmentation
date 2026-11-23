"""
patterns.py
-----------
Find patterns: fixed label sequences (std_cycle = CC_CHA_C3, REST, CC_DCH_C3, REST).

    pattern_rows = find_patterns(labeled_steps, definition)

Every pattern is searched on its own. Matches of one pattern never overlap each other;
matches of different patterns may overlap. Items with conditions are checked with
item_condition_masks (labeling.py) on the same table.

`match_variants` is shared with segments.py.
"""
from __future__ import annotations

import logging

import pandas as pd
from .labeling import item_condition_masks

logger = logging.getLogger(__name__)

def match_variants(labels: list, variants: dict, item_masks: dict | None = None) -> list[dict]:
    """Search the label sequence left to right; at each position try the variants in order,
    keep the first that fits and continue after it (matches never overlap).

    Parameters
    ----------
    labels     : label of every step (pass 2)
    variants   : {variant: [part, ...]}, part = {"name", "kind", "labels": [token, ...]}
                 (as in definition["segments"][name]["variants"])
    item_masks : {item key: bool array} for items with conditions (item_condition_masks);
                 a token without a mask is compared with the label

    Returns
    -------
    One dict per match: {"variant", "step_pos_start", "step_pos_end",
                         "parts": [(part_index, name, kind, pos_start, pos_end), ...]}
    positions are rows of the table the labels come from, end inclusive.

    Example
    -------
        match_variants(["A", "B", "A", "B"], {"v": [{"name": "A", "kind": "label", "labels": ["A"]},
                                                    {"name": "B", "kind": "label", "labels": ["B"]}]})
        → 2 matches: step_pos 0–1 and 2–3
    """
    item_masks = item_masks or {}
    n = len(labels)

    def fits(sequence, i):
        if not sequence or i + len(sequence) > n:
            return False
        for k, token in enumerate(sequence):
            mask = item_masks.get(token)
            if not (mask[i + k] if mask is not None else labels[i + k] == token):
                return False
        return True

    variant_sequences = {variant: [label for part in parts for label in part["labels"]]
                         for variant, parts in variants.items()}
    matches, i = [], 0
    while i < n:
        for variant, sequence in variant_sequences.items():
            if fits(sequence, i):
                parts, offset = [], i
                for part_index, part in enumerate(variants[variant]):
                    part_length = len(part["labels"])
                    parts.append((part_index, part["name"], part["kind"], offset, offset + part_length - 1))
                    offset += part_length
                matches.append({"variant": variant, "step_pos_start": i,
                                "step_pos_end": i + len(sequence) - 1, "parts": parts})
                i += len(sequence)
                break
        else:
            i += 1
    return matches


def find_patterns(labeled_steps: pd.DataFrame, definition: dict, item_masks: dict | None = None) -> list[dict]:
    """Find all instances of every pattern of the definition.

    Parameters
    ----------
    labeled_steps : step table with column 'label' (result of label_steps)
    definition    : result of load_definition (uses 'patterns', 'item_conditions')
    item_masks    : result of item_condition_masks for labeled_steps; None = computed here

    Returns
    -------
    One dict per instance: {level="pattern", name, variant="", instance, part="(whole)",
    part_index=-1, part_kind="pattern", step_pos_start, step_pos_end}
    Positions are rows of `labeled_steps` (the merged table), end inclusive.

    Example
    -------
        rows = find_patterns(labeled_steps, definition)
        pd.DataFrame(rows).groupby("name").size()
    """
    labels = list(labeled_steps["label"])
    if item_masks is None:
        item_masks = item_condition_masks(labeled_steps, definition)
    rows = []
    for pattern_name, pattern in definition["patterns"].items():
        matches = match_variants(labels, {pattern_name: [{"name": pattern_name, "kind": "pattern",
                                                          "labels": pattern["labels"]}]}, item_masks)
        rows += [dict(level="pattern", name=pattern_name, variant="", instance=instance, part="(whole)",
                      part_index=-1, part_kind="pattern",
                      step_pos_start=m["step_pos_start"], step_pos_end=m["step_pos_end"])
                 for instance, m in enumerate(matches)]
        logger.debug(f"pattern {pattern_name}: {len(matches)} instance(s)")
    return rows
