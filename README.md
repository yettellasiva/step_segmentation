# TB_CPA_Segmentation_v2

Finds tests in battery cycler data, based on the step-features files written by TB_CPA_Evaluate.
Each step is given a label. Steps that the cycler split are merged, labelled again, and the label
sequences are then searched in three more levels:

| Level | What it is | Example | Function |
|---|---|---|---|
| **Label** | name for one step, from that step's features | `CC_CHA_C3` = full C/3 charge | `label_steps` |
| **Merge** | joins split steps (rest split in two, CC phase interrupted by a spike) | `REST, CURRENT_SPIKE, REST` → one `REST` | `merge_steps` |
| **Pattern** | fixed sequence of labels; an item may add conditions, e.g. a rest of 15 min | `std_cycle` = `CC_CHA_C3, REST, CC_DCH_C3, REST` | `find_patterns` |
| **Segment** | a test built from patterns and labels (items may carry conditions), with variants | `RPT_01` = `std_cycle*3, CC_CHA_C3, REST, pulse*4` | `find_segments` |
| **Chain** | ordered segments, with allowed labels between them | `INIT_RPT` → storage → `RPT` | `find_chains` |

```
<base_path>/04_Evaluated_Data/<cell_id>/<cell_id>_step_features.csv      (from TB_CPA_Evaluate)
        │  label_steps     pass 1: every step gets one label (finds spikes and split rests)
        │  merge_steps     joins split steps into one row
        │  label_steps     pass 2: the same labels on the merged table
        │  find_patterns   every pattern, searched on its own
        │  find_segments   every segment, matched on the labels (patterns = building blocks)
        │  find_chains     every chain, matched on the segments found
        ▼
<base_path>/04_Evaluated_Data/<cell_id>/<cell_id>_segments.csv           (this tool)
```

What is defined (labels, patterns, …) can differ per sample: each sample in `samples.yaml` names its own definition folder.
KPIs are not part of v2 yet. The output gives step and raw-data row ranges for every detection, so KPIs can be added later.

---

## 1. Quick start

```
cd TB_CPA_Segmentation_v2
python run_segmentation.py
```

Settings at the top of `run_segmentation.py`:

| Setting | Meaning | Default |
|---|---|---|
| `SAMPLES` | `None` = all samples in `samples.yaml`, or a list like `["C48T1_C1-2-03S"]` | `None` |
| `CELL_IDS` | `[]` = all cells, or a list like `["FCA08J0011"]` (applies to every selected sample) | `[]` |
| `SKIP_RERUN` | Skip cells whose result cannot have changed (see §10) | `True` |
| `EXPORT_LABELS` | Also write `<cell_id>_steps_labeled.csv` (merged step table + label), useful to check labels and merges | `False` |
| `EXPORT_PLOT` | Also write `<cell_id>_segments_plot.html`: current coloured by label + detected patterns / segments / chains (§9) | `False` |
| `LOG_FOLDER` | `None` = `<base_path>/06_Logs/debug_logs` of each sample, or a folder for all samples | `None` |
| `CONSOLE_LEVEL` | `logging.INFO`; `logging.DEBUG` also shows counts per label, merge, pattern, segment and chain | `INFO` |
| `SUMMARY_CSV` | Summary of the last run, one row per cell | `last_run_summary.csv` |

**Requirements:** Python with `pandas`, `numpy` and `pyyaml`; `plotly` only for `EXPORT_PLOT`. The step files must come from TB_CPA_Evaluate
`eval_steps` 2.0 or later; older files are rejected with a message to rerun Evaluate.

**Typical first run for a new definition:** one sample, `EXPORT_LABELS = True`, `SKIP_RERUN = False`. Open
`<cell_id>_steps_labeled.csv` and check the labels and merged rows (`n_merged`, `n_bridged`), then check
`<cell_id>_segments.csv`.

---

## 2. Samples (`samples.yaml`)

One entry per sample:

```yaml
C48T1_B3: {base_path: //server/.../B3_sample,
           nominal_capacity: 213.2, nominal_energy: 701.8, max_volt: 3.8, min_volt: 2.5, vbc_volt: 3.5,
           std_temperature: 25, temp_threshold: 3, definition: custom/c48t1_definitions}
```

| Key | Meaning | Name in formulas |
|---|---|---|
| `base_path` | sample root; the step files are read from `<base_path>/04_Evaluated_Data/<cell_id>/` | – |
| `nominal_capacity` | Ah (required) | `cap` |
| `max_volt`, `min_volt`, `vbc_volt` | V (required) | `vmax`, `vmin`, `vbc` |
| `nominal_energy` | Wh (optional; NaN if missing, so a formula using it never matches) | `e_nom` |
| `std_temperature` | °C, optional, default 25 | `std_temp` |
| `temp_threshold` | ± °C around std_temperature, optional, default 3 | `temp_tol` |
| `definition` | definition folder below `definitions/`, e.g. `custom/c48t1_definitions` (required) | – |

- **Cell IDs:** every subfolder of `04_Evaluated_Data` that contains a file matching `*step*feature*.csv` is one cell,
  and the folder name is the cell ID. If a folder has several matching files, the last one in alphabetical order is
  used and a warning is logged.
- Formulas can also use `inf`.

---

## 3. Definitions

### 3.1 Folders and files

A definition is a folder anywhere below `definitions/`. Shared label files live in `common/`; each project
definition includes what it needs:

```
definitions/
├── common/
│   └── TA_T/                            shared label sets
│       ├── label_C3_cha_dch.yaml        C/3 charge / discharge
│       ├── label_rest.yaml              REST, REST_SPIKE, REST_TRANSITION
│       ├── label_spike.yaml             CURRENT_SPIKE
│       └── label_storage.yaml           STORAGE_*, SHORT_GAP_1H, GAP
└── custom/
    └── c48t1_definitions/               definition "custom/c48t1_definitions"
        ├── 00_settings.yaml
        ├── 01_labels.yaml
        ├── 02_patterns.yaml
        ├── 03_segments.yaml
        └── 04_chains.yaml
docs/
└── labelling_guideline.md               how to write labels (rules, naming, checklist)
```

| File | Contains |
|---|---|
| `00_settings.yaml` | `settings:` |
| `01_labels.yaml` | `labels:` |
| `02_patterns.yaml` | `patterns:` |
| `03_segments.yaml` | `segments:` |
| `04_chains.yaml` | `chains:` |

- All `.yaml` / `.yml` files directly in the definition folder are read. Subfolders are separate definitions and are not read.
- A numbered file (`NN_…`) may only contain its own section, plus `include:`. A file without a number may contain any section.
- Each file is optional, but the folder needs at least one. Without `04_chains.yaml` there are simply no chains.
  Without `00_settings.yaml` the defaults apply (no merging).
- Item names (labels, patterns, segments, chains) must be unique across all levels. `RPT` cannot be both a pattern and a segment.

### 3.2 `include:` (reusing shared files)

```yaml
# custom/c48t1_definitions/01_labels.yaml
include: [common/TA_T/label_C3_cha_dch.yaml,
          common/TA_T/label_rest.yaml,
          common/TA_T/label_storage.yaml,
          common/TA_T/label_spike.yaml]
labels:
  REST: {...}        # replaces the REST from common/TA_T/label_rest.yaml
```

- `include:` means: treat the items of that file as if they were written here. A limit changed in a shared file
  therefore applies to every definition that includes it, and those cells rerun (§10).
- `include:` takes one path or a list (block or inline form). No wildcards, no folders.
- **Paths are relative to the `definitions/` folder**, wherever the including file is. Absolute paths also work.
  A path written relative to the file (`../../common/…`) gives a "not found" problem.
- An included file may include further files. Each file is read at most once.
- **Overriding:** an item in the definition's own files replaces an included item of the same name. Any other
  duplicate is a problem: the same name twice in the definition's own files, in two included files, or twice in one file.
- **Priority of included labels:** own labels first, then the directly included files in the order listed, then the
  files those include (one level after the other). An own label that replaces an included one keeps its own position.
  Priority only matters for labels; patterns, segments and chains are each searched on their own.
- Included files follow the same numbered-file rule.

### 3.3 Checks before anything runs

`load_definition` checks the whole definition, including all included files, and reports **all** problems at once:

- unknown names in patterns, segments, chains, `between` and `merge`
- misspelled keys (e.g. `sequense:`, unknown settings or merge keys; an old `collapse:` key is reported as unknown)
- formula errors: syntax, unknown names, anything that is not arithmetic
- ranges that are not exactly `[low, high]`; `or.` groups that are not a list of non-empty blocks
- pattern / segment items with conditions: no `label:`, a `label:` that is not a label, `repeat` not a whole number ≥ 1,
  formula errors in the limits; conditions on chain items
- a name used twice, an include that is not found, a section in the wrong numbered file
- a label with no conditions, or `default_label` that is also defined as a label
- merge settings: a label in both `join_adjacent` and `bridges`, a bridge limit that is not a whole number ≥ 1 or `null`

**Not checked at load time: step-feature column names**, because they are only known once a step file is read.
This includes columns used in conditions of pattern / segment items. A misspelled column (`Curent_A_median`) is reported when the first cell runs: the console and log show a warning,
the run summary lists it under `message`, and the condition never matches. So "label never appears" plus a warning
means a typo in a column name.

A definition with problems stops only its own sample; the other samples still run.

### 3.4 YAML pitfalls

| Write | Not | Why |
|---|---|---|
| `[null, "prev['SOC_eval_start_%']*2"]` | `[null, prev['SOC_eval_start_%']*2]` | `[ ]` inside `[ ]` breaks YAML; quote the formula |
| `description: "Rest: long"` | `{description: Rest: long}` | a `:` inside `{ }` needs quotes |
| `mode: "on"` | `mode: on` | YAML reads `on`, `off`, `yes`, `no`, `true`, `false` as true/false; quote such values |
| `[600, null]` or `[600, ~]` | `[600, ]` / `[600, None]` | `null`/`~` = open side; `None` is read as a formula and fails |
| 2-space indentation | tabs | YAML rejects tabs |

Numbers like `1e-6`, `-1e-6` and `3600*24` work as written.

---

## 4. Labels (`01_labels.yaml`)

Full rules, naming and a checklist: **`docs/labelling_guideline.md`**.

```yaml
labels:
  CC_DCH_C3:                                   # every row must hold (AND)
    description:            Full C/3 constant current discharge at 25°C
    mode:                   DISCHARGE          # equality
    delta_Capacity_step_Ah: [ -cap*1.1,   -cap*0.6 ]
    time_duration_s:        [ 0.5*3*3600, 1.2*3*3600 ]
    Voltage_V_end:          [ vmin-0.05,  vmin+0.005 ]
    or.current_check:                          # OR group: one alternative must hold
      - { cc_current_A:     [ -cap*1.03/3, -cap*0.97/3 ],
          phase_coverage:   [ 0.99, null ] }
      - { Current_A_median: [ -cap*1.03/3, -cap*0.97/3 ],
          Current_A_std:    [ 0, cap*0.02/3 ] }
    or.temp_check:
      - { T_Chamber_degC_mean: [ std_temp-temp_tol, std_temp+temp_tol ] }
      - { T_Cell_degC_mean:    [ std_temp-temp_tol, std_temp+temp_tol*2 ] }

  REST_SPIKE:                                  # neighbour steps
    description:            Short rest (≤ 1 s) between two steps of the same mode
    mode:                   REST
    time_duration_s:        [ 0, 1 ]
    or.neighbour_modes:
      - { prev.mode: DISCHARGE, next.mode: DISCHARGE }
      - { prev.mode: CHARGE,    next.mode: CHARGE }
```

**Conditions**

| Form | Meaning |
|---|---|
| `column: [low, high]` | `low <= value <= high`, limits included. Low and high may be swapped. `null` = open side |
| `column: value` | equality. Text is compared exactly (case-sensitive, surrounding spaces ignored); numbers as numbers (`5` = `5.0`) |
| `column: A\|B\|C` | any of the values |
| `prev.column: …` / `next.column: …` | the same condition, on the previous / next row of the step table |
| `prev2.column: …` / `next3.column: …` | the same condition, on the row 2 before / 3 after (any number; `prev1` = `prev`) |
| `this.column: …` | same as `column: …` |
| `or.<name>: [{…}, {…}]` | OR group: at least one block must hold; ANDed with the other rows. `<name>` is free text, so several groups per label are possible. Groups may be nested |
| `- {…}` `- {…}` | OR of whole blocks (label level); inside a block all conditions must hold |

- A two-item list is always a range. For "any of" use `A|B`, not `[A, B]`.
- There is no strict `<` / `>`; use a small offset (`[0.001, …]`).
- Float equality (e.g. `SOC_eval_end_%: 0`) almost never matches; use a range.
- **Formulas** are allowed in range limits only, not in equality values. They may use `cap, e_nom, vmax, vmin, vbc,
  std_temp, temp_tol, inf`, numbers, `+ - * / ** % //`, `abs(x)`, `min(a, b)`, `max(a, b)`, and neighbour values
  `prev.<column>` / `next.<column>`, further neighbours `prev2.<column>` / `next3.<column>` (any number), and this
  step's own columns `this.<column>`. For column names with special characters use `prev['SOC_eval_start_%']`,
  quoted in YAML (§3.4).
- **Priority:** labels are checked in order. The first label whose conditions hold wins, and a step has exactly one label.
  Put specific labels before general ones (`INIT_CC_DCH_C3` and `CC_DCH_C3` before `CC_DCH_C3_PARTIAL`).
- Steps that fit no label get `settings.default_label` (`NO_LABEL`). That name can be used in patterns, segments and `between` lists.
- **Labels run twice** (pass 1 on the original steps, pass 2 on the merged table, §5). Write labels so they are
  correct on both: in pass 2 a merged rest has its full duration and a merged CC discharge its full capacity.

**Missing values never match**

- A NaN in the column fails the condition. Gap rows, for example, have no current.
- `prev` of the first step and `next` of the last step have no value, so `prev.` / `next.` conditions fail there
  (likewise `prev2` on the first 2 steps, `next3` on the last 3, …).
- A formula without a value (e.g. `prev.Current_A_median*1.5` on the first step) fails, even when the other limit is `null`.
- A column missing from the step file fails (warning, see §3.3). In an `or.` group this only disables that alternative.

**Neighbours are rows of the step table, including Gap rows.** TB_CPA_Evaluate inserts a virtual Gap row for every
pause > 30 min. For the step right after a pause, `prev` is that Gap row. In pass 2, neighbours are the merged rows.

---

## 5. Settings and merging (`00_settings.yaml`)

```yaml
settings:
  default_label: NO_LABEL              # default NO_LABEL
  merge:
    join_adjacent: [ REST ]            # consecutive steps with this label → one row
    bridges:                           # A, BRIDGE, B → one row when A and B have the same mode
      REST_SPIKE:    3                 # max. bridges of this label per merged row; null = no limit
      CURRENT_SPIKE: 5
```

Without `merge:` nothing is merged.

**Why:** cyclers often split one step. A rest is split into two steps, or a short current spike interrupts a rest,
or a short rest interrupts a CC discharge. Each half alone has too little duration or capacity, so it would get
the wrong label (e.g. `CC_DCH_C3_PARTIAL` instead of `CC_DCH_C3`) and patterns would not fit.

**How:**

1. **Pass 1:** `label_steps` on the original steps. Only the labels are used, to find what can be joined.
2. **`merge_steps`** builds groups:
   - `join_adjacent`: consecutive steps with the **same** label in the list become one row.
   - `bridges`: a step with a bridge label is dropped when the steps before and after it have the same `mode`;
     both neighbours become one row. Chains like `A, spike, B, spike, C` become one row.
   - A bridge limit counts the bridges of that label inside one merged row. Above the limit the bridge stays a step
     of its own and a new row starts after it.
3. **Pass 2:** `label_steps` on the merged table. Patterns, segments and chains use these labels.

| Data (pass-1 labels / `mode`) | Result |
|---|---|
| `REST, REST` | one `REST`, durations summed |
| `REST, CURRENT_SPIKE, REST` | one rest, spike left out of the sums |
| `CC_DCH_C3_PARTIAL, REST_SPIKE, CC_DCH_C3_PARTIAL` (DISCHARGE both sides) | one discharge, capacity summed; pass 2 can label it `CC_DCH_C3` |
| `CC_DCH_C3, CC_DCH_C3` without a spike | not merged (only labels in `join_adjacent` are joined directly) |
| `REST, GAP, REST` | not merged |
| two bridges next to each other | not merged |

**How columns are combined** (`COLUMN_RULES` in `seg/merge.py`, first matching rule wins; bridges are left out):

| Column | Merged value |
|---|---|
| `*_at_<t>s` (value t s after step start) | from the first part if it lasts ≥ t s, else NaN |
| `step_id`, `row_start` | first part |
| `row_end` | last part |
| `time_duration_s`, `delta_*` | sum |
| `*_start*` | first part |
| `*_end*` | last part |
| `*_std` | maximum (conservative) |
| `*_min` / `*_max` | min / max |
| `*_median`, `*_mean`, `cc_current_A`, `phase_coverage` | duration-weighted mean |
| anything else | first part's value; a warning lists such columns if their values differ inside a merged row |

- Medians and std of merged rows are **approximations**. They are good enough for labelling; do not use them as KPIs.
- A time-based value that falls into a later part was never computed by Evaluate, so it is NaN; a label using it does
  not match. The exact value would need the raw rows (`row_start:row_end`).
- When step-feature columns change, check the warning after the first run and add rules in `COLUMN_RULES`.

**Columns added to the merged table:**

| Column | Meaning |
|---|---|
| `orig_pos_start`, `orig_pos_end` | first / last row of the original step table (inclusive, bridges included) |
| `n_merged` | original rows in this row (bridges included) |
| `n_bridged`, `bridged_s` | bridges dropped and their total duration |
| `merged_step_ids` | `id\|id\|…` of the original steps (`''` if not merged) |

**Things to keep in mind**

- A bridge label should be strict. `REST_SPIKE` only checks the modes of its neighbours; to avoid joining two
  different steps that happen to have the same mode, also require the same current on both sides, e.g.
  `next.Current_A_median: [prev.Current_A_median*0.95, prev.Current_A_median*1.05]`.
- Edge values: `REST` (`[1, …]`) and `REST_SPIKE` (`[0, 1]`) share 1.0 s; the earlier label wins.
- Patterns no longer need `REST, REST` handling: a split rest is one row after merging. Write one `REST`.

---

## 6. Patterns (`02_patterns.yaml`)

```yaml
patterns:
  std_cycle: [CC_CHA_C3, REST, CC_DCH_C3, REST]               # short form
  two_cycles: [std_cycle*2]                                    # patterns may use patterns
  init_dch:                                                    # long form
    description: Incoming discharge at the start of the file
    sequence: [INIT_CC_DCH_C3, REST]
  cha_rest15_dch:                                              # item with a condition
    - CC_CHA_C3
    - { label: REST, time_duration_s: [ 14*60, 16*60 ] }       # a REST of 14-16 min
    - CC_DCH_C3
```

- Items are labels or other patterns. `NAME*n` repeats an item n times. A string `"std_cycle*2 + CC_CHA_C3"`
  works as well as a list. A pattern may not refer to itself, directly or through others.
- **Every pattern is searched on its own**, whether or not a segment uses it, and every match gives one `pattern` row.
- **Search:** left to right over the pass-2 labels. When the pattern fits, the match is recorded and the search
  continues after its last step, so matches of one pattern never overlap. Matches of different patterns may overlap
  (e.g. `std_cycle` and `two_cycles`).

### 6.1 Items with conditions

A plain item only checks the label name. An item written as a mapping also checks the step's features, so
"a rest of 15 min" needs no label of its own:

```yaml
- { label: REST, time_duration_s: [ 14*60, 16*60 ] }                  # REST lasting 14-16 min
- { label: "REST|GAP", time_duration_s: [ 0, 700 ] }                  # REST or GAP, up to 700 s
- { label: CC_CHA_C3, T_Cell_degC_start: [ 20, std_temp ], repeat: 2 }  # two such steps in a row
```

| Key | Meaning |
|---|---|
| `label` | required. A label, or `A\|B` for any of several labels. Only labels, not patterns |
| any other key | a condition, written as in labels (§4): range, equality, formula limits, `prev.` / `next.`, `or.<name>` groups. All of them must hold |
| `repeat` | optional, the item n times in a row (like `NAME*n`), default 1 |

- **An item matches a step** if the step has one of its labels **and** all of its conditions hold.
- **Conditions are evaluated on the merged table** (pass 2). A rest that was split into 10 min + spike + 5 min
  is one `REST` of 15 min there, so the condition sees the full duration.
- **Missing values never match**, as in labels (§4). A column that is not in the step file gives a warning once
  per cell, and the item never matches.
- `prev.` / `next.` mean the neighbouring rows of the merged step table, not the neighbouring items of the pattern.
- Items with conditions can be used in patterns and in segment variants. A pattern that is used in a segment brings
  its conditions along. Chains link segments and take no conditions.
- The string form (`"a + b"`) and `NAME*n` cannot carry conditions; write a list with `{...}` items and `repeat:`.

**Label or item condition?**

- Use a **label** when the meaning is the same everywhere, or when merging must know about it. Merging (§5) only
  sees the pass-1 labels, so `REST_SPIKE` and `CURRENT_SPIKE` have to be labels.
- Use an **item condition** when the condition matters only in a specific pattern or segment, like the length of
  one particular rest. This avoids a label for every duration.
- Repeating a label from `join_adjacent` is pointless: `{label: REST, ..., repeat: 2}` can never match, because
  two neighbouring rests are one row after merging.

---

## 7. Segments (`03_segments.yaml`)

```yaml
segments:
  RPT:
    description: RPT - capacity check + DCIR pulses
    variants:                          # tried in this order at every position
      RPT_01: [std_cycle*3, CC_CHA_C3, REST, pulse*4]
      RPT_02: [std_cycle*3, CC_CHA_C3, REST, pulse*3]
      RPT_03: [std_cycle*2, CC_CHA_C3, REST, pulse*4]
      RPT_04: [std_cycle*2, CC_CHA_C3, REST, pulse*3]
  STD_CAP_CHECK: [std_cycle*3]         # short form: one variant, named like the segment
```

- A variant is a list of patterns and labels (`NAME*n` allowed). Label items can carry conditions (§6.1), e.g.
  `{label: REST, time_duration_s: [3600, null]}` for a rest of at least 1 h. Patterns are expanded into their
  labels and conditions, and the whole variant is matched as one sequence. The segment does not use the
  `pattern` rows found in §6.
- **Variants are chosen per location.** The search goes left to right. At each position the variants are tried in the
  order written, and the first that fits is recorded. The search then continues after it. One cell can contain
  `RPT_01` at one place and `RPT_04` at another.
- **Order the variants longest/most complete first.** If `RPT_02` (3 pulse blocks) were listed before `RPT_01`
  (4 pulse blocks), `RPT_02` would always win at the start of a 4-pulse RPT and the 4th pulse block would be left over.
- Matches of one segment never overlap. Different segments are searched independently and may overlap:
  `RPT` (no initial discharge) is also found inside every `INIT_RPT`, and `STD_CAP_CHECK` inside every `RPT`.
  This is intended. Filter by `name` in the output.
- Each match gives one `(whole)` row plus one row per part, in order (`part_index` 0, 1, …). A part is a pattern or
  a label of the variant, and repeats count separately (`std_cycle*3` = parts 0, 1, 2).

---

## 8. Chains (`04_chains.yaml`)

```yaml
chains:
  INIT_RPT_STORAGE_RPT:
    description: Initial RPT, storage break, RPT
    variants:
      CH_01: [INIT_RPT, RPT]                    # segments, NAME*n allowed (RPT*3)
    between: [REST, GAP, STORAGE_28D]           # labels allowed between two segments
    between_pairs:
      INIT_RPT>RPT: [REST, STORAGE_28D]         # replaces `between` for this pair only
```

How a chain is matched:

1. Take the segment matches in time order. A chain variant can start at any match of its first segment.
2. For each following segment of the variant, take the **first** match of that segment that starts after the
   previous one ends.
3. Every step between the two segments must have a label from `between`, or from `between_pairs` for that pair. An empty
   or missing `between` means the segments must follow each other directly. If one step does not fit, this
   variant fails at this start. It does not try a later match of the same segment.
4. Variants are tried in the order written. The first that completes is recorded, and the search continues after its last step,
   so chains of one name never overlap.

- `between_pairs` keys are `SEGMENT_A>SEGMENT_B`. Both must be segments of this chain.
- Bridges that were not merged keep their label (e.g. `REST_SPIKE`); add them to `between` if they may appear there.
- Each match gives one `(whole)` row plus one row per segment. `part_variant` and `part_instance` say which
  segment match it is, so it can be found among the `segment` rows (same `name` = part, same `instance`).

---

## 9. Output

### `<cell_id>_segments.csv` (in `04_Evaluated_Data/<cell_id>/`)

One row per detected pattern, segment or chain (`part = (whole)`), plus one row per part of each segment and chain.
Matching runs on the merged table, but **all positions and ranges in this file refer to the original step file**.

| Column | Meaning |
|---|---|
| `cell_id` | cell (folder name) |
| `level` | `pattern`, `segment` or `chain` |
| `name` | pattern / segment / chain name |
| `variant` | variant that matched (empty for patterns) |
| `instance` | 0, 1, 2, … per `name` in time order |
| `part` | `(whole)`, or the part's name (pattern / label for segments, segment for chains). An item with conditions appears under its label, e.g. `REST` or `REST\|GAP` |
| `part_index` | -1 for `(whole)`, else 0, 1, … in order |
| `part_kind` | `pattern`, `segment`, `chain` for `(whole)` rows; `pattern` / `label` / `segment` for parts |
| `part_variant`, `part_instance` | chain parts only: variant and instance of that segment |
| `step_id_start`, `step_id_end` | `step_id` of the first and last original step (Gap rows have `x.5` ids) |
| `row_start`, `row_end` | raw data rows: `processed_data.parquet.iloc[row_start:row_end]` (`row_end` exclusive); merged bridges are inside this range |
| `n_steps` | number of original step rows, Gap rows and bridges included |
| `step_pos_start`, `step_pos_end` | positions in the original step file: `steps.iloc[step_pos_start : step_pos_end + 1]` (end inclusive) |

Example:

```
level    name       variant  instance  part       part_index  part_kind  step_id_start  step_id_end  row_start  row_end
segment  RPT        RPT_01   1         (whole)    -1          segment    43             81           2100       4050
segment  RPT        RPT_01   1         std_cycle  0           pattern    43             47           2100       2350
segment  RPT        RPT_01   1         std_cycle  1           pattern    48             51           2350       2550
segment  RPT        RPT_01   1         CC_CHA_C3  3           label      56             56           2750       2800
```

Gap rows have no raw data (`row_start == row_end`), so a range that ends in a Gap row has the same `row_end` as the step before it.

### Other files

| File | Content |
|---|---|
| `<cell_id>_steps_labeled.csv` | only with `EXPORT_LABELS`: the **merged** step table + pass-2 `label` + merge columns (§5). Use `orig_pos_*` / `merged_step_ids` to trace back |
| `<cell_id>_segments_plot.html` | only with `EXPORT_PLOT`: interactive plot to check labels and detections by eye (see below) |
| `<cell_id>_segments_meta.json` | fingerprint for skip/rerun; also the `eval_version` and the time it was written |
| `last_run_summary.csv` | per cell: `sample, cell_id, status` (`done` / `skipped` / `failed`), counts of patterns / segments / chains, `seconds`, `message` (missing columns or the error) |
| `06_Logs/debug_logs/segmentation_debug_<host>.log` | full log per sample, DEBUG level, appended on every run; tracebacks of failed cells |

All files are overwritten on each run of a cell. If a cell **fails**, its files from an earlier run are **not** deleted.
Check `status` in the summary or the log.

### The plot (`seg/plot.py`)

- **Top:** current vs date/time, rebuilt from the step features only (no raw data): per step the start,
  every `Current_A_at_<X>s` inside the step, the CC→CV switch (`cc_duration_s`) and the end, joined by straight lines.
  Each step is coloured by its pass-2 label (`NO_LABEL` grey); a merged spike takes the colour of its merged row.
  A thin grey line joins all points as a guide to the eye and breaks at Gap rows. `time_start_s` must be Unix seconds.
- **Bottom:** the `(whole)` detections, one band per level (chain, segment, pattern). Detections that overlap
  inside a level (e.g. `RPT` inside `INIT_RPT`) get another lane, so many names do not mean many rows. Colour = name.
- Click a name in the legend to hide it; zoom in one row zooms both. Short steps (18 s pulses) only show when zoomed in.
- The HTML loads plotly.js from the internet. If the plot fails, the cell still counts as `done`; the reason is
  in `message` and the log.
- On its own: `plot_cell(steps, labeled_steps, detections, "out.html", title="FCA08J0011")` with the results of
  `segment_cell`.

---

## 10. Skip / rerun (`SKIP_RERUN = True`)

A cell is skipped only if `<cell_id>_segments.csv` and the meta file exist (and the labeled file, if `EXPORT_LABELS`,
and the plot, if `EXPORT_PLOT`),
and all of these are unchanged:

| Fingerprint | Changes when |
|---|---|
| step file name and modification time | TB_CPA_Evaluate rewrote the step file |
| definition name and hash | anything that affects matching changed, **including in included files**, the merge settings and the conditions of pattern / segment items, and also descriptions of patterns, segments and chains (label descriptions do not count) |
| sample hash | any value of the sample in `samples.yaml` except `base_path` |
| `SEG_V2_VERSION` (`seg/run.py`) | the code logic changed (bump it by hand), e.g. new rules in `COLUMN_RULES` |

Log level, `LOG_FOLDER` and `CELL_IDS` do not trigger a rerun. Set `SKIP_RERUN = False` to force a rerun.

---

## 11. Using the functions on their own

Every function has a docstring with parameters, return value and an example (`help(seg.find_segments)`).

```python
import pandas as pd
from seg import (read_samples, load_definition, label_steps, find_patterns, find_segments,
                 find_chains, segment_cell, process_sample, setup_logging)
from seg.merge import merge_steps

setup_logging()                                              # optional: log to the console
sample_config = read_samples()["C48T1_B3"]
definition    = load_definition(sample_config["definition"], sample_config)    # raises DefinitionError
steps         = pd.read_csv(step_file, encoding="utf-8-sig")

first_pass    = label_steps(steps, definition)                                  # pass 1
merged        = merge_steps(first_pass, definition["settings"]["merge"])        # join split steps
labeled_steps = label_steps(merged, definition)                                 # pass 2
pattern_rows  = find_patterns(labeled_steps, definition)     # positions in the MERGED table
segment_rows  = find_segments(labeled_steps, definition)
chain_rows    = find_chains(labeled_steps, segment_rows, definition)

labeled_steps, detections = segment_cell(steps, definition, "FCA08J0011")   # all of the above + original ids / row ranges
from seg.plot import plot_cell
plot_cell(steps, labeled_steps, detections, "FCA08J0011_segments_plot.html", title="FCA08J0011")
summary = process_sample("C48T1_B3", sample_config, cell_ids=["FCA08J0011"], skip_rerun=False)
# try another definition on a sample without editing samples.yaml
summary = process_sample("C48T1_B3", sample_config, definition_name="custom/other_definition")
```

`find_*` return positions (`step_pos_start`, `step_pos_end`) in the table they are given, i.e. the merged table.
Conditions of pattern / segment items are evaluated on that same table (`item_condition_masks` in `seg/labeling.py`).
`segment_cell` translates them to the original step file (via `orig_pos_start` / `orig_pos_end`) and adds the
cell ID, step IDs and raw row ranges.

---

## 12. Troubleshooting

| Symptom | Likely cause |
|---|---|
| A label never appears; a warning about missing columns | column name misspelled, or not in this step file |
| A label never appears, no warning | limits too tight; an earlier label already takes these steps (priority); NaN in a column; wrong sign; check `_steps_labeled.csv` |
| Almost everything is `NO_LABEL` | a plain row on a missing column (e.g. `mode`), or `mode` values spelled differently in the data |
| A full CC step is labelled `…_PARTIAL` | it was split and not merged: check that the spike got a bridge label in pass 1 and that the bridge limit was not reached |
| Two different steps were merged | the bridge label is too loose (only checks `mode`); add a current condition (§5) |
| A merged row has NaN in a `*_at_<t>s` column | the first part is shorter than t s (§5) |
| Warning `merge: no combine rule for [...]` | add a rule for those columns in `COLUMN_RULES` (`seg/merge.py`) |
| `settings: unknown key(s) ['collapse']` | collapse was replaced by `merge` (§5); remove it |
| A pattern item with conditions never matches | the label does not fit (check `_steps_labeled.csv`); a condition column is missing (warning) or NaN; the duration is checked on the merged row, not on the original parts (§6.1) |
| `item … needs 'label:'` | a `{...}` item in a pattern / segment has no `label:` key |
| `[...] are not labels (only labels can have conditions)` | `label:` names a pattern or an unknown name; conditions only work on labels |
| `conditions on items are only allowed in patterns and segments` | a `{...}` item in a chain; chains take no conditions |
| A segment is found as a shorter variant than expected | variant order: list the longest variants first (§7) |
| `RPT` rows inside `INIT_RPT` | intended: every segment is searched independently (§7) |
| A chain is not found | a step between the segments has a label that is not in `between` (e.g. an unmerged `REST_SPIKE`); or another match of the segment comes first (§8) |
| `prev.` condition fails right after a pause | `prev` is the Gap row (§4) |
| A cell is `skipped` although you changed something | the change is not in the fingerprint (e.g. code without bumping `SEG_V2_VERSION`); use `SKIP_RERUN = False` |
| `step table misses ['eval_version']` / `eval_version … needs >= 2.0` | rerun TB_CPA_Evaluate for this cell |
| `include '…' not found` | path must be relative to `definitions/` (§3.2) |
| YAML `ParserError` | quoting or tabs (§3.4) |

---

## 13. Current limits

- One label per step; no conditions on a neighbour's **label** (only on its features).
- Pattern / segment items can check the step's own features (§6.1), but not the label of a neighbour, and not a property of several steps together (except what `merge` joins). Chains take no conditions.
- Merged medians / std are approximations; time-based values after the first part of a merged row are NaN.
- Chains only link segments; there is no level above chains.
- No KPIs yet (planned per pattern / segment / chain, from step features or raw rows via `row_start:row_end`).

After changing code logic in `seg/`, bump `SEG_V2_VERSION` in `seg/run.py` so that all cells rerun.


