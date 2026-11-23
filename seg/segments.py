"""
segments.py
-----------
Find segments: tests made of patterns and labels, with named variants
(RPT_01: std_cycle*3 + CC_CHA_C3 + REST + pulse*4).

    segment_rows = find_segments(labeled_steps, definition)

Variants are tried per location: at each position the first variant that fits wins, so one
cell can contain RPT_01 at one place and RPT_03 at another. Matches of one segment never
overlap each other; different segments may overlap (RPT is also found inside INIT_RPT).

Each instance gives one '(whole)' row plus one row per part (each pattern / label of the
variant, in order, part_index = 0, 1, …) so KPIs can later address e.g. the 2nd std_cycle.
"""
from __future__ import annotations

import logging

import pandas as pd

from .patterns import match_variants
from .labeling import item_condition_masks

logger = logging.getLogger(__name__)


def find_segments(labeled_steps: pd.DataFrame, definition: dict, item_masks: dict | None = None) -> list[dict]:
    """Find all instances of every segment of the definition.

    Parameters
    ----------
    labeled_steps : step table with column 'label' (result of label_steps)
    definition    : result of load_definition (uses 'segments', 'item_conditions')
    item_masks    : result of item_condition_masks for labeled_steps; None = computed here

    Returns
    -------
    Dicts with level="segment", name, variant, instance, part, part_index, part_kind,
    step_pos_start, step_pos_end:
      one row with part="(whole)", part_index=-1, part_kind="segment" per instance,
      then one row per part (part = pattern or label name, part_kind = "pattern" | "label";
      an item with conditions appears under its label, e.g. "REST" or "REST|GAP").
    Positions are rows of `labeled_steps` (the merged table), end inclusive.

    Example
    -------
        rows = pd.DataFrame(find_segments(labeled_steps, definition))
        rows[(rows.name == "RPT") & (rows.part == "std_cycle")]     # every std_cycle of every RPT
    """
    labels = list(labeled_steps["label"])
    if item_masks is None:
        item_masks = item_condition_masks(labeled_steps, definition)
    rows = []
    for segment_name, segment in definition["segments"].items():
        matches = match_variants(labels, segment["variants"], item_masks)
        for instance, match in enumerate(matches):
            common = dict(level="segment", name=segment_name, variant=match["variant"], instance=instance)
            rows.append({**common, "part": "(whole)", "part_index": -1, "part_kind": "segment",
                         "step_pos_start": match["step_pos_start"], "step_pos_end": match["step_pos_end"]})
            rows += [{**common, "part": part_name, "part_index": part_index, "part_kind": part_kind,
                      "step_pos_start": pos_start, "step_pos_end": pos_end}
                     for part_index, part_name, part_kind, pos_start, pos_end in match["parts"]]
        logger.debug(f"segment {segment_name}: {len(matches)} instance(s) "
                     f"{sorted({m['variant'] for m in matches})}")
    return rows
