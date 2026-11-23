"""Segmentation v2: labels → merge → labels → patterns → segments → chains on TB_CPA_Evaluate step features.

Use the levels one by one:

    from seg import read_samples, load_definition, label_steps, find_patterns, find_segments, find_chains
    from seg.merge import merge_steps
    sample_config = read_samples()["C48T1_B3"]
    definition    = load_definition(sample_config["definition"], sample_config)
    first_pass    = label_steps(steps, definition)                              # pass 1
    merged        = merge_steps(first_pass, definition["settings"]["merge"])    # join split steps
    labeled_steps = label_steps(merged, definition)                             # pass 2
    pattern_rows  = find_patterns(labeled_steps, definition)    # positions in the MERGED table
    segment_rows  = find_segments(labeled_steps, definition)
    chain_rows    = find_chains(labeled_steps, segment_rows, definition)

or all at once: segment_cell(steps, definition, cell_id) / process_sample(sample_name, sample_config);
these also translate the positions to the original step file.
"""
from .definitions import DefinitionError, load_definition
from .labeling import label_steps
from .patterns import find_patterns
from .segments import find_segments
from .chains import find_chains
from .run import (DETECTION_COLUMNS, SEG_V2_VERSION, process_sample, read_samples, segment_cell,
                  setup_logging)

__all__ = ["DefinitionError", "load_definition", "label_steps", "find_patterns", "find_segments",
           "find_chains", "segment_cell", "process_sample", "read_samples", "setup_logging",
           "DETECTION_COLUMNS", "SEG_V2_VERSION"]
