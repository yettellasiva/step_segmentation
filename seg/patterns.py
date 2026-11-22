"""
patterns.py
-----------
Find patterns: fixed label sequences (std_cycle = CHA_C3_CC, RELAX, DCH_C3_CC, RELAX).

    pattern_rows = find_patterns(labeled_steps, definition)

Every pattern is searched on its own. Matches of one pattern never overlap each other;
matches of different patterns may overlap.

Collapse: consecutive repeats of the same collapse label count as one before matching
(RELAX, RELAX → RELAX); the match still covers all of them. Each pattern uses its own
`collapse:` list if it has one, otherwise settings.collapse (default [RELAX]).

`collapse_labels` and `match_variants` are shared with segments.py.
"""
from __future__ import annotations

import logging

import pandas as pd

logger = logging.getLogger(__name__)


def collapse_labels(labels: list, collapse_set: set) -> tuple[list, list, list]:
    """Merge consecutive repeats of the same label if it is in `collapse_set`.

    Parameters
    ----------
    labels       : label of every step, in order
    collapse_set : labels whose repeats count as one, e.g. {"RELAX"}

    Returns
    -------
    (collapsed_labels, first_positions, last_positions)
      first/last_positions[i] = first and last step position covered by collapsed_labels[i]

    Example
    -------
        collapse_labels(["CHA", "RELAX", "RELAX", "DCH"], {"RELAX"})
        → (["CHA", "RELAX", "DCH"], [0, 1, 3], [0, 2, 3])
    """
    collapsed_labels, first_positions, last_positions = [], [], []
    for position, label in enumerate(labels):
        if collapsed_labels and label in collapse_set and label == collapsed_labels[-1]:
            last_positions[-1] = position
        else:
            collapsed_labels.append(label)
            first_positions.append(position)
            last_positions.append(position)
    return collapsed_labels, first_positions, last_positions


def match_variants(labels: list, variants: dict, collapse_set: set) -> list[dict]:
    """Search the label sequence left to right; at each position try the variants in order,
    keep the first that fits and continue after it (matches never overlap).

    Parameters
    ----------
    labels       : label of every step, in order
    variants     : {variant_name: [part, ...]}, part = {"name", "kind", "labels": [label, ...]}
    collapse_set : see collapse_labels

    Returns
    -------
    [{"variant", "step_pos_start", "step_pos_end",
      "parts": [(part_index, part_name, part_kind, step_pos_start, step_pos_end), ...]}]
    Positions are rows of the step table, inclusive.
    """
    collapsed, first_positions, last_positions = collapse_labels(labels, collapse_set)
    variant_sequences = {variant: [label for part in parts for label in part["labels"]]
                         for variant, parts in variants.items()}
    matches, i = [], 0
    while i < len(collapsed):
        for variant, sequence in variant_sequences.items():
            length = len(sequence)
            if length and collapsed[i:i + length] == sequence:
                parts, offset = [], i
                for part_index, part in enumerate(variants[variant]):
                    part_length = len(part["labels"])
                    parts.append((part_index, part["name"], part["kind"],
                                  first_positions[offset], last_positions[offset + part_length - 1]))
                    offset += part_length
                matches.append({"variant": variant, "step_pos_start": first_positions[i],
                                "step_pos_end": last_positions[i + length - 1], "parts": parts})
                i += length
                break
        else:
            i += 1
    return matches


def find_patterns(labeled_steps: pd.DataFrame, definition: dict) -> list[dict]:
    """Find all instances of every pattern of the definition.

    Parameters
    ----------
    labeled_steps : step table with column 'label' (result of label_steps)
    definition    : result of load_definition (uses 'patterns': labels + collapse of each pattern)

    Returns
    -------
    One dict per instance: {level="pattern", name, variant="", instance, part="(whole)",
    part_index=-1, part_kind="pattern", step_pos_start, step_pos_end}

    Example
    -------
        rows = find_patterns(labeled_steps, definition)
        pd.DataFrame(rows).groupby("name").size()
    """
    labels = list(labeled_steps["label"])
    rows = []
    for pattern_name, pattern in definition["patterns"].items():
        matches = match_variants(labels, {pattern_name: [{"name": pattern_name, "kind": "pattern",
                                                          "labels": pattern["labels"]}]},
                                 pattern["collapse"])
        rows += [dict(level="pattern", name=pattern_name, variant="", instance=instance, part="(whole)",
                      part_index=-1, part_kind="pattern",
                      step_pos_start=m["step_pos_start"], step_pos_end=m["step_pos_end"])
                 for instance, m in enumerate(matches)]
        logger.debug(f"pattern {pattern_name}: {len(matches)} instance(s)")
    return rows
