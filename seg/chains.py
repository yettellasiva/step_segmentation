"""
chains.py
---------
Find chains: ordered segments (CH_01: INIT_RPT + RPT*3).

    chain_rows = find_chains(labeled_steps, segment_rows, definition)

Between two consecutive segments only the labels in `between` may appear (none listed =
the segments must be directly adjacent). `between_pairs: {INIT_RPT>RPT: [RELAX]}` replaces
the list for that one pair. Variants are tried per location (the first that fits wins);
matches of one chain never overlap each other.

Each instance gives one '(whole)' row plus one row per segment part; part_variant /
part_instance point to the matching segment row of find_segments.
"""
from __future__ import annotations

import logging

import pandas as pd

logger = logging.getLogger(__name__)


def find_chains(labeled_steps: pd.DataFrame, segment_rows: list[dict], definition: dict) -> list[dict]:
    """Find all instances of every chain of the definition.

    Parameters
    ----------
    labeled_steps : step table with column 'label' (result of label_steps)
    segment_rows  : result of find_segments (only the '(whole)' rows are used)
    definition    : result of load_definition (uses 'chains')

    Returns
    -------
    Dicts with level="chain", name, variant, instance, part, part_index, part_kind,
    part_variant, part_instance, step_pos_start, step_pos_end:
      one row with part="(whole)" per instance, then one row per segment
      (part = segment name, part_variant / part_instance = that segment's variant / instance).

    Example
    -------
        segment_rows = find_segments(labeled_steps, definition)
        chain_rows = find_chains(labeled_steps, segment_rows, definition)
    """
    labels = list(labeled_steps["label"])
    segment_instances = sorted(
        ({"name": r["name"], "variant": r["variant"], "instance": r["instance"],
          "start": r["step_pos_start"], "end": r["step_pos_end"]}
         for r in segment_rows if r["part"] == "(whole)"),
        key=lambda s: (s["start"], s["end"]))

    def build_chain(segment_sequence, first_segment, between, between_pairs):
        """Greedy: each next segment = first instance of that name after the previous one,
        with only allowed labels in the gap. None if the sequence cannot be completed."""
        chain = [first_segment]
        for segment_name in segment_sequence[1:]:
            previous = chain[-1]
            following = next((s for s in segment_instances
                              if s["name"] == segment_name and s["start"] > previous["end"]), None)
            if following is None:
                return None
            allowed = between_pairs.get((previous["name"], segment_name), between)
            labels_in_gap = set(labels[previous["end"] + 1:following["start"]])
            if not labels_in_gap <= allowed:
                return None
            chain.append(following)
        return chain

    rows = []
    for chain_name, chain_definition in definition["chains"].items():
        last_end, instance = -1, 0
        for first_segment in segment_instances:
            if first_segment["start"] <= last_end:
                continue
            for variant, segment_sequence in chain_definition["variants"].items():
                if segment_sequence[0] != first_segment["name"]:
                    continue
                chain = build_chain(segment_sequence, first_segment,
                                    chain_definition["between"], chain_definition["between_pairs"])
                if chain is None:
                    continue
                common = dict(level="chain", name=chain_name, variant=variant, instance=instance)
                rows.append({**common, "part": "(whole)", "part_index": -1, "part_kind": "chain",
                             "step_pos_start": chain[0]["start"], "step_pos_end": chain[-1]["end"]})
                rows += [{**common, "part": s["name"], "part_index": part_index, "part_kind": "segment",
                          "part_variant": s["variant"], "part_instance": s["instance"],
                          "step_pos_start": s["start"], "step_pos_end": s["end"]}
                         for part_index, s in enumerate(chain)]
                last_end, instance = chain[-1]["end"], instance + 1
                break
        logger.debug(f"chain {chain_name}: {instance} instance(s)")
    return rows
