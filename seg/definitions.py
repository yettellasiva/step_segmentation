"""
definitions.py
--------------
Read and check one definition folder.
    definition = load_definition("custom/c48t1_definitions", sample_config)
A definition folder (any depth below definitions/, e.g. definitions/custom/c48t1_definitions/) holds
numbered YAML files, one per level:
    00_settings.yaml   settings:   merge, default_label
    01_labels.yaml     labels:     step-feature conditions per label
    02_patterns.yaml   patterns:   label sequences (items may carry conditions)
    03_segments.yaml   segments:   tests built from patterns + labels, with variants
    04_chains.yaml     chains:     ordered segments
A numbered file may only contain its own section (plus `include:`); files without a number
may contain any section. `include: [common/TA_T/label_rest.yaml]` adds the items of another file
(path relative to the definitions/ folder) as if they were written here.
All problems are collected and raised together as one DefinitionError, so a typo never
silently changes a result.
"""
from __future__ import annotations
import ast
import copy
import hashlib
import json
import logging
import re
from pathlib import Path
import numpy as np
import yaml
logger = logging.getLogger(__name__)
DEFINITIONS_ROOT = Path(__file__).resolve().parents[1] / "definitions"
SECTIONS = ("labels", "patterns", "segments", "chains")
FILE_KEYS = {"include", "settings", *SECTIONS}
FILE_NUMBER_SECTION = {"00": "settings", "01": "labels", "02": "patterns", "03": "segments", "04": "chains"}
SETTINGS_DEFAULT = {"default_label": "NO_LABEL", "merge": {}}
MERGE_KEYS = {"join_adjacent", "bridges"}
PATTERN_KEYS = {"description", "sequence"}
SEGMENT_KEYS = {"description", "variants"}
CHAIN_KEYS = {"description", "variants", "between", "between_pairs"}
NEIGHBOURS = ("prev", "next")
FORMULA_FUNCTIONS = {"abs": np.abs, "min": np.minimum, "max": np.maximum}
FORMULA_ARG_COUNT = {"abs": 1, "min": 2, "max": 2}
_TERM = re.compile(r"^\s*([^\s*]+)\s*(?:\*\s*(\d+))?\s*$")
class DefinitionError(ValueError):
    """Raised by load_definition; the message lists every problem found in the definition."""
class _StrictLoader(yaml.SafeLoader):
    pass
def _mapping_without_duplicates(loader, node, deep=False):
    seen_keys = set()
    for key_node, _ in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in seen_keys:
            raise DefinitionError(f"{node.start_mark.name}, line {key_node.start_mark.line + 1}: "
                                  f"'{key}' is defined twice")
        seen_keys.add(key)
    return loader.construct_mapping(node, deep)
_StrictLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping_without_duplicates)
def read_yaml(path: Path | str) -> dict:
    """Read one YAML file; duplicate keys raise DefinitionError.
    Parameters
    ----------
    path : YAML file
    Returns
    -------
    dict with the file content ({} for an empty file)
    """
    path = Path(path)
    with open(path, encoding="utf-8") as f:
        content = yaml.load(f, Loader=_StrictLoader) or {}
    if not isinstance(content, dict):
        raise DefinitionError(f"{path}: top level must be a mapping (labels:, patterns:, ...)")
    return content
def formula_names(sample_config: dict) -> dict:
    """Scalar names usable in formulas, taken from one sample's entry in samples.yaml.
    Parameters
    ----------
    sample_config : needs nominal_capacity, max_volt, min_volt, vbc_volt;
                    optional std_temperature (25), temp_threshold (3), nominal_energy
    Returns
    -------
    {"cap", "vmax", "vmin", "vbc", "std_temp", "temp_tol", "e_nom", "inf"} → float
    """
    return dict(cap=sample_config["nominal_capacity"], vmax=sample_config["max_volt"],
                vmin=sample_config["min_volt"], vbc=sample_config["vbc_volt"],
                std_temp=sample_config.get("std_temperature", 25),
                temp_tol=sample_config.get("temp_threshold", 3),
                e_nom=sample_config.get("nominal_energy", np.nan), inf=np.inf)
def check_formula(formula: str, allowed_names: set) -> tuple[list[str], set]:
    """Check a formula against a whitelist (numbers, + - * / ** % //, allowed names,
    prev.<column> / next.<column>, prev['column'], abs(), min(a, b), max(a, b)).
    Parameters
    ----------
    formula       : e.g. "cap*0.95/3" or "prev.Current_A_median*1.5"
    allowed_names : scalar names, e.g. set(formula_names(sample_config))
    Returns
    -------
    (problems, neighbour_columns) — problems is empty when the formula is fine;
    neighbour_columns = columns used via prev/next
    """
    try:
        tree = ast.parse(str(formula), mode="eval")
    except SyntaxError:
        return [f"formula '{formula}' has a syntax error"], set()
    problems, neighbour_columns = [], set()
    allowed_operators = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow, ast.Mod, ast.FloorDiv,
                         ast.USub, ast.UAdd)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Load, *allowed_operators)):
            continue
        if isinstance(node, ast.Constant):
            if not isinstance(node.value, (int, float, str)):
                problems.append(f"formula '{formula}': constant {node.value!r} not allowed")
        elif isinstance(node, ast.Name):
            if node.id not in allowed_names and node.id not in NEIGHBOURS and node.id not in FORMULA_FUNCTIONS:
                problems.append(f"formula '{formula}': unknown name '{node.id}' "
                                f"(allowed: {', '.join(sorted(allowed_names))}, prev.X, next.X, abs, min, max)")
        elif isinstance(node, ast.Attribute):
            if isinstance(node.value, ast.Name) and node.value.id in NEIGHBOURS:
                neighbour_columns.add(node.attr)
            else:
                problems.append(f"formula '{formula}': only prev.<column> / next.<column> may use '.'")
        elif isinstance(node, ast.Subscript):
            if (isinstance(node.value, ast.Name) and node.value.id in NEIGHBOURS
                    and isinstance(node.slice, ast.Constant) and isinstance(node.slice.value, str)):
                neighbour_columns.add(node.slice.value)
            else:
                problems.append(f"formula '{formula}': only prev['column'] / next['column'] may use [ ]")
        elif isinstance(node, ast.Call):
            if not (isinstance(node.func, ast.Name) and node.func.id in FORMULA_FUNCTIONS) or node.keywords:
                problems.append(f"formula '{formula}': only abs(), min(a, b), max(a, b) may be called")
            elif len(node.args) != FORMULA_ARG_COUNT[node.func.id]:
                # np.minimum(a, b, c) would take c as the output array and fail at runtime
                problems.append(f"formula '{formula}': {node.func.id}() needs exactly "
                                f"{FORMULA_ARG_COUNT[node.func.id]} argument(s), got {len(node.args)}")
        else:
            problems.append(f"formula '{formula}': '{type(node).__name__}' not allowed")
    return problems, neighbour_columns
def _read_definition_file(path: Path, collected: dict, already_read: set, problems: list,
                          is_project_file: bool, definitions_root: Path) -> list[Path]:
    """Add one file's items to `collected`; returns the files it includes (read later).
    A project file may replace an included item of the same name; any other duplicate is a problem."""
    path = path.resolve()
    if path in already_read:
        return []
    already_read.add(path)
    try:
        content = read_yaml(path)
    except DefinitionError as exc:
        problems.append(str(exc)); return []
    except (OSError, yaml.YAMLError) as exc:
        problems.append(f"{path}: {exc}"); return []
    if unknown := set(content) - FILE_KEYS:
        problems.append(f"{path.name}: unknown top-level key(s) {sorted(unknown)} (allowed: {sorted(FILE_KEYS)})")
    file_number = path.name[:2] if path.name[:2].isdigit() and path.name[2:3] == "_" else None
    if file_number is not None:
        expected = FILE_NUMBER_SECTION.get(file_number)
        if expected is None:
            problems.append(f"{path.name}: unknown file number {file_number} "
                            f"(use {', '.join(f'{n}_{s}' for n, s in FILE_NUMBER_SECTION.items())})")
        elif wrong := sorted(set(content) - {expected, "include"}):
            problems.append(f"{path.name}: a {file_number}_ file may only contain '{expected}:' "
                            f"(found {wrong}); move them to the file with their number")
    for section in (*SECTIONS, "settings"):
        items = content.get(section) or {}
        if not isinstance(items, dict):
            problems.append(f"{path.name}: '{section}' must be a mapping of names"); continue
        for item_name, item_value in items.items():
            item_name = str(item_name)
            if item_name in collected[section]:
                first_file, first_is_project = collected["_source"][section][item_name]
                if is_project_file or not first_is_project:
                    kind = "setting" if section == "settings" else section[:-1]
                    problems.append(f"{kind} '{item_name}' is defined in {first_file.name} and in {path.name}")
                continue
            collected[section][item_name] = item_value
            collected["_source"][section][item_name] = (path, is_project_file)
    includes = content.get("include") or []
    found = []
    for include in [includes] if isinstance(includes, str) else includes:
        include_path = Path(definitions_root) / include
        if include_path.is_file():
            found.append(include_path)
        else:
            problems.append(f"{path.name}: include '{include}' not found ({include_path}); "
                            f"paths are relative to the definitions folder, e.g. common/TA_T/label_rest.yaml")
    return found
def _expand_sequence(items, is_known, where: str, problems: list, parse_item=None) -> list[str]:
    """Expand NAME*n and the string form into a flat list of names.

    ['std_cycle*3', 'CC_CHA_C3'] (or 'std_cycle*3 + CC_CHA_C3') → ['std_cycle', 'std_cycle', 'std_cycle', 'CC_CHA_C3']
    A {label: ..., <conditions>} item is handed to parse_item, which returns item keys (see _parse_item);
    without parse_item (chains) such an item is a problem."""
    if isinstance(items, str):
        items = items.split("+")
    if not isinstance(items, list) or not items:
        problems.append(f"{where}: must be a non-empty list"); return []
    names = []
    for item in items:
        if isinstance(item, dict):
            if parse_item is None:
                problems.append(f"{where}: conditions on items are only allowed in patterns and segments");
                continue
            names += parse_item(item, where);
            continue
        match = _TERM.match(str(item))
        if not match:
            problems.append(f"{where}: cannot read '{item}' (use NAME or NAME*n)"); continue
        name, repeat = match.group(1), int(match.group(2) or 1)
        if not is_known(name):
            problems.append(f"{where}: unknown name '{name}'"); continue
        names += [name] * repeat
    return names
def _parse_equality(value):
    """'Gap' → 'Gap';  'A|B' → ['A', 'B'];  numbers stay numbers ('5' → 5.0)."""
    if isinstance(value, (int, float)):
        return value
    def as_number_if_possible(text):
        try:
            return float(text)
        except ValueError:
            return text
    values = [as_number_if_possible(v.strip()) for v in str(value).split("|")]
    return values if len(values) > 1 else values[0]
def _for_hash(obj):
    """JSON-safe copy for hashing: tuple keys → 'a>b', sets sorted, item order kept."""
    if isinstance(obj, dict):
        return [[(">".join(k) if isinstance(k, tuple) else str(k)), _for_hash(v)] for k, v in obj.items()]
    if isinstance(obj, (set, frozenset)):
        return sorted(map(str, obj))
    if isinstance(obj, (list, tuple)):
        return [_for_hash(x) for x in obj]
    return obj if isinstance(obj, (int, float, str, type(None))) else str(obj)
def _parse_block(raw_block, where: str, allowed_names: set, problems: list) -> list:
    """Parse one block {column: condition, ...} into [(column, neighbour, condition), ...].
    neighbour = None | "prev" | "next"; condition = ("range", low, high) | ("equal", value | [values]).
    'or.<name>: [{...}, {...}]' = OR group; each item is a block (AND inside). It is stored as
    ("or.<name>", "or", ("any", [block, ...])). 'description' keys are ignored."""
    conditions = []
    for column, condition in raw_block.items():
        column = str(column)
        if column == "description":
            continue
        prefix, _, rest = column.partition(".")
        if prefix == "or" and rest:
            if not isinstance(condition, list) or not condition or \
                    not all(isinstance(item, dict) and item for item in condition):
                problems.append(f"{where}, {column}: must be a list of blocks  - {{...}}  - {{...}}"); continue
            group = [_parse_block(item, f"{where}, {column}", allowed_names, problems) for item in condition]
            conditions.append((column, "or", ("any", [g for g in group if g])))
            continue
        neighbour, base_column = (prefix, rest) if prefix in NEIGHBOURS and rest else (None, column)
        if isinstance(condition, list):
            if len(condition) != 2:
                problems.append(f"{where}, {column}: a range needs exactly [low, high] "
                                f"(use 'A|B' for 'any of')"); continue
            for limit in condition:
                if isinstance(limit, str):
                    formula_problems, _ = check_formula(limit, allowed_names)
                    problems += [f"{where}, {column}: {p}" for p in formula_problems]
                elif limit is not None and not isinstance(limit, (int, float)):
                    problems.append(f"{where}, {column}: limit {limit!r} is not a number or formula")
            conditions.append((base_column, neighbour, ("range", condition[0], condition[1])))
        elif condition is None or isinstance(condition, (dict, bool)):
            problems.append(f"{where}, {column}: condition must be [low, high] or a value")
        else:
            conditions.append((base_column, neighbour, ("equal", _parse_equality(condition))))
    return conditions
ITEM_KEYS = {"label", "repeat"}
def _parse_item(item: dict, is_label, where: str, allowed_names: set, problems: list,
                item_conditions: dict) -> list[str]:
    """Pattern / segment item with conditions:  {label: REST, time_duration_s: [840, 960], repeat: 1}.
    Registers the item in item_conditions and returns its key, `repeat` times.
    Key = "<labels> <json of the raw conditions>", e.g. 'REST [["time_duration_s", [840, 960]]]'.
    A plain {label: X} (no conditions, one label) → ['X'] * repeat; {label: "A|B"} without conditions
    is still registered as an item (any of the labels)."""
    raw = {str(k): v for k, v in item.items()}
    label_value, repeat = raw.pop("label", None), raw.pop("repeat", 1)
    if label_value is None:
        problems.append(f"{where}: item {item} needs 'label:'"); return []
    labels = [v.strip() for v in str(label_value).split("|")]
    if unknown := [name for name in labels if not is_label(name)]:
        problems.append(f"{where}: {unknown} are not labels (only labels can have conditions)"); return []
    if isinstance(repeat, bool) or not isinstance(repeat, int) or repeat < 1:
        problems.append(f"{where}: repeat must be a whole number >= 1"); return []
    if not raw and len(labels) == 1:
        return [labels[0]] * repeat
    display = "|".join(labels)
    block = _parse_block(raw, f"{where}, item {display}", allowed_names, problems)
    key = f"{display} {json.dumps(_for_hash(raw), default=str)}"
    item_conditions[key] = {"labels": labels, "block": block, "display": display}
    return [key] * repeat
def load_definition(definition_name: str, sample_config: dict,
                    definitions_root: Path | str = DEFINITIONS_ROOT) -> dict:
    """Read, check and resolve one definition folder for one sample.
    Parameters
    ----------
    definition_name  : folder below definitions_root, subfolders allowed ("custom/c48t1_definitions")
    sample_config    : the sample's entry in samples.yaml (gives cap, vmax, … for the formulas)
    definitions_root : default <repo>/definitions
    Returns
    -------
    dict with
      name       : definition_name
      labels     : {label: [block, ...]} in priority order (first match wins); label descriptions are dropped.
                   block = [(column, neighbour, condition)], neighbour = None | "prev" | "next" | "or",
                   condition = ("range", low, high), ("equal", value | [values])
                                 or ("any", [block, ...])  for an or.<name> group (neighbour "or")
      patterns   : {pattern: {"description", "labels": [token, ...]}}
                   nested patterns expanded; token = label name or item key (see item_conditions)
      segments   : {segment: {"description", "variants": {variant: [part, ...]}}}
                   part = {"name", "kind": "pattern" | "label", "labels": [token, ...]}
      chains     : {chain: {"description", "variants": {variant: [segment, ...]},
                            "between": set, "between_pairs": {(segment_a, segment_b): set}}}
      settings   : {"default_label": str,
                    "merge": {"join_adjacent": set of labels, "bridges": {label: max per merged row | None}}}
      item_conditions : {item key: {"labels": [label, ...], "block": block, "display": "REST|GAP"}}
                   for pattern / segment items with conditions
      formula_names : scalars for the formulas (from sample_config)
      hash       : md5 of everything above except formula_names (for skip/rerun)
    Raises
    ------
    DefinitionError listing every problem (unknown names, typos in keys, formula errors,
    duplicate names, malformed ranges, sections in the wrong numbered file, ...)
    Example
    -------
        definition = load_definition("custom/c48t1_definitions", read_samples()["C48T1_B3"])
        list(definition["labels"])        # ['INIT_CC_DCH_C3', 'CC_DCH_C3', ...]
    """
    folder = Path(definitions_root) / definition_name
    if not folder.is_dir():
        raise DefinitionError(f"Definition folder not found: {folder}")
    project_files = sorted(folder.glob("*.yaml")) + sorted(folder.glob("*.yml"))
    if not project_files:
        raise DefinitionError(f"No .yaml files in {folder}")
    problems: list[str] = []
    collected = {section: {} for section in (*SECTIONS, "settings")}
    collected["_source"] = {section: {} for section in (*SECTIONS, "settings")}
    already_read: set = set()
    pending = [inc for f in project_files
               for inc in _read_definition_file(f, collected, already_read, problems, is_project_file=True,
                                                    definitions_root=definitions_root)]
    while pending:
        pending = [inc for f in pending
                   for inc in _read_definition_file(f, collected, already_read, problems, is_project_file=False,
                                                    definitions_root=definitions_root)]
    logger.debug(f"definition {definition_name}: read {len(already_read)} file(s)")
    try:
        names_for_formulas = formula_names(sample_config)
    except KeyError as exc:
        raise DefinitionError(f"Sample config misses {exc} (needs nominal_capacity, max_volt, min_volt, vbc_volt)")
    allowed_names = set(names_for_formulas)
    settings = dict(SETTINGS_DEFAULT)
    if unknown := set(collected["settings"]) - set(SETTINGS_DEFAULT):
        problems.append(f"settings: unknown key(s) {sorted(unknown)} (allowed: {sorted(SETTINGS_DEFAULT)})")
    settings.update({k: v for k, v in collected["settings"].items() if k in SETTINGS_DEFAULT})
    default_label = settings["default_label"] = str(settings["default_label"])
    for i, level_a in enumerate(SECTIONS):
        for level_b in SECTIONS[i + 1:]:
            for name in sorted(set(collected[level_a]) & set(collected[level_b])):
                problems.append(f"name '{name}' is used as {level_a[:-1]} and as {level_b[:-1]}")
    if default_label in collected["labels"]:
        problems.append(f"default_label '{default_label}' must not also be a defined label")
    labels = {}
    for label_name, label_value in collected["labels"].items():
        raw_blocks = label_value if isinstance(label_value, list) else [label_value]
        blocks = []
        for block_number, raw_block in enumerate(raw_blocks, start=1):
            where = f"label {label_name}" + (f" (block {block_number})" if len(raw_blocks) > 1 else "")
            if not isinstance(raw_block, dict) or not raw_block:
                problems.append(f"{where}: must be 'column: condition' lines"); continue
            conditions = _parse_block(raw_block, where, allowed_names, problems)
            if conditions:
                blocks.append(conditions)
        if blocks:
            labels[label_name] = blocks
        else:
            problems.append(f"label {label_name}: has no conditions")
    known_labels = set(collected["labels"]) | {default_label}
    merge = settings["merge"] or {}
    item_conditions: dict = {}
    def parse_item(item, where):
        return _parse_item(item, lambda n: n in known_labels, where, allowed_names, problems, item_conditions)
    if not isinstance(merge, dict):
        problems.append("settings: merge must be a mapping (join_adjacent:, bridges:)"); merge = {}
    if unknown := set(merge) - MERGE_KEYS:
        problems.append(f"settings merge: unknown key(s) {sorted(unknown)} (allowed: {sorted(MERGE_KEYS)})")
    join_adjacent = merge.get("join_adjacent") or []
    if not isinstance(join_adjacent, (list, str)):
        problems.append("settings merge join_adjacent: must be a label or a list of labels"); join_adjacent = []
    join_adjacent = {join_adjacent} if isinstance(join_adjacent, str) else set(map(str, join_adjacent))
    bridges = merge.get("bridges") or {}
    if isinstance(bridges, (list, str)):
        bridges = {b: None for b in ([bridges] if isinstance(bridges, str) else bridges)}
    if not isinstance(bridges, dict):
        problems.append("settings merge bridges: must be a mapping {LABEL: limit} or a list of labels"); bridges = {}
    parsed_bridges = {}
    for bridge, limit in bridges.items():
        if limit is not None and (isinstance(limit, bool) or not isinstance(limit, int) or limit < 1):
            problems.append(f"settings merge bridges {bridge}: limit must be a whole number >= 1 or null"); continue
        parsed_bridges[str(bridge)] = limit
    for label_name in sorted((join_adjacent | set(parsed_bridges)) - set(collected["labels"])):
        problems.append(f"settings merge: label '{label_name}' is not defined")
    for label_name in sorted(join_adjacent & set(parsed_bridges)):
        problems.append(f"settings merge: '{label_name}' cannot be in join_adjacent and bridges")
    settings["merge"] = {"join_adjacent": join_adjacent, "bridges": parsed_bridges}
    pattern_values = {}
    for pattern_name, pattern_value in collected["patterns"].items():
        if isinstance(pattern_value, (list, str)):
            pattern_value = {"sequence": pattern_value}
        if not isinstance(pattern_value, dict):
            problems.append(f"pattern {pattern_name}: must be a list or have 'sequence:'")
            pattern_value = {"sequence": []}
        if unknown := set(pattern_value) - PATTERN_KEYS:
            problems.append(f"pattern {pattern_name}: unknown key(s) {sorted(unknown)} (allowed: {sorted(PATTERN_KEYS)})")
        if "sequence" not in pattern_value:
            problems.append(f"pattern {pattern_name}: needs 'sequence:'")
        pattern_values[pattern_name] = pattern_value
    pattern_labels: dict[str, list] = {}
    def is_label_or_pattern(name):
        return name in known_labels or name in collected["patterns"]
    def resolve_pattern(pattern_name, path=()):
        """Labels of a pattern, with nested patterns expanded."""
        if pattern_name in pattern_labels:
            return pattern_labels[pattern_name]
        if pattern_name in path:
            problems.append(f"pattern {pattern_name}: refers to itself ({' > '.join(path + (pattern_name,))})")
            return []
        names = _expand_sequence(pattern_values[pattern_name].get("sequence"), is_label_or_pattern,
                                 f"pattern {pattern_name}", problems, parse_item)
        resolved = []
        for name in names:
            resolved += resolve_pattern(name, path + (pattern_name,)) if name in collected["patterns"] else [name]
        pattern_labels[pattern_name] = resolved
        return resolved
    patterns = {}
    for pattern_name, pattern_value in pattern_values.items():
        patterns[pattern_name] = {"description": pattern_value.get("description", ""),
                                  "labels": resolve_pattern(pattern_name)}
    segments = {}
    for segment_name, segment_value in collected["segments"].items():
        if isinstance(segment_value, (list, str)):
            segment_value = {"variants": {segment_name: segment_value}}
        if not isinstance(segment_value, dict):
            problems.append(f"segment {segment_name}: must be a list or have 'variants:'"); continue
        if unknown := set(segment_value) - SEGMENT_KEYS:
            problems.append(f"segment {segment_name}: unknown key(s) {sorted(unknown)} (allowed: {sorted(SEGMENT_KEYS)})")
        variants = segment_value.get("variants")
        if isinstance(variants, (list, str)):
            variants = {segment_name: variants}
        if not isinstance(variants, dict) or not variants:
            problems.append(f"segment {segment_name}: needs 'variants:'"); continue
        resolved_variants = {}
        for variant_name, items in variants.items():
            names = _expand_sequence(items, is_label_or_pattern, f"segment {segment_name} / {variant_name}",
                                     problems, parse_item)
            resolved_variants[str(variant_name)] = [
                {"name": item_conditions[name]["display"] if name in item_conditions else name,
                 "kind": "pattern" if name in collected["patterns"] else "label",
                 "labels": pattern_labels.get(name, []) if name in collected["patterns"] else [name]}
                for name in names]
        segments[segment_name] = {"description": segment_value.get("description", ""),
                                  "variants": resolved_variants}
    chains = {}
    for chain_name, chain_value in collected["chains"].items():
        if not isinstance(chain_value, dict):
            problems.append(f"chain {chain_name}: must have 'variants:' (and optional 'between:')"); continue
        if unknown := set(chain_value) - CHAIN_KEYS:
            problems.append(f"chain {chain_name}: unknown key(s) {sorted(unknown)} (allowed: {sorted(CHAIN_KEYS)})")
        variants = chain_value.get("variants")
        if isinstance(variants, (list, str)):
            variants = {chain_name: variants}
        if not isinstance(variants, dict) or not variants:
            problems.append(f"chain {chain_name}: needs 'variants:'"); continue
        resolved_variants = {}
        for variant_name, items in variants.items():
            resolved_variants[str(variant_name)] = _expand_sequence(
                items, lambda name: name in collected["segments"], f"chain {chain_name} / {variant_name}", problems)
        segments_in_chain = {s for seq in resolved_variants.values() for s in seq}
        def label_set(value, where):
            value = [value] if isinstance(value, str) else (value or [])
            for label_name in value:
                if label_name not in known_labels:
                    problems.append(f"{where}: label '{label_name}' is not defined")
            return set(value)
        between = label_set(chain_value.get("between"), f"chain {chain_name} between")
        between_pairs = {}
        for pair, pair_labels in (chain_value.get("between_pairs") or {}).items():
            segment_a, _, segment_b = str(pair).partition(">")
            segment_a, segment_b = segment_a.strip(), segment_b.strip()
            if not segment_b or segment_a not in segments_in_chain or segment_b not in segments_in_chain:
                problems.append(f"chain {chain_name} between_pairs '{pair}': use 'SEGMENT_A>SEGMENT_B' "
                                f"with segments of this chain ({sorted(segments_in_chain)})")
            between_pairs[(segment_a, segment_b)] = label_set(pair_labels, f"chain {chain_name} between_pairs {pair}")
        chains[chain_name] = {"description": chain_value.get("description", ""), "variants": resolved_variants,
                              "between": between, "between_pairs": between_pairs}
    if problems:
        raise DefinitionError(f"Definition '{definition_name}' has {len(problems)} problem(s):\n  - "
                              + "\n  - ".join(problems))
    definition = {"name": definition_name, "labels": labels, "patterns": patterns, "segments": segments,
                  "chains": chains, "settings": settings, "item_conditions": item_conditions,
                  "formula_names": names_for_formulas}
    definition["hash"] = hashlib.md5(json.dumps(
        _for_hash({k: v for k, v in definition.items() if k != "formula_names"})).encode()).hexdigest()
    logger.debug(f"definition {definition_name}: {len(labels)} labels, {len(patterns)} patterns, "
                 f"{len(segments)} segments, {len(chains)} chains")
    return copy.deepcopy(definition)

