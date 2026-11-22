# TB_CPA_Segmentation_v2

Finds tests in battery cycler data, based on the step-features files written by TB_CPA_Evaluate.
Each step is given a label. Label sequences are then searched in four levels:

| Level | What it is | Example | Function |
|---|---|---|---|
| **Label** | name for one step, from that step's features | `CHA_C3_CC` = full C/3 charge | `label_steps` |
| **Pattern** | fixed sequence of labels | `std_cycle` = `CHA_C3_CC, RELAX, DCH_C3_CC, RELAX` | `find_patterns` |
| **Segment** | a test built from patterns and labels, with variants | `RPT_01` = `std_cycle*3, CHA_C3_CC, RELAX, pulse*4` | `find_segments` |
| **Chain** | ordered segments, with allowed labels between them | `INIT_RPT` → storage → `RPT` | `find_chains` |

```
<base_path>/04_Evaluated_Data/<cell_id>/<cell_id>_step_features.csv      (from TB_CPA_Evaluate)
        │  label_steps     every step gets one label
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
| `SKIP_RERUN` | Skip cells whose result cannot have changed (see §9) | `True` |
| `EXPORT_LABELS` | Also write `<cell_id>_steps_labeled.csv` (step table + label), useful to check labels | `False` |
| `LOG_FOLDER` | `None` = `<base_path>/06_Logs/debug_logs` of each sample, or a folder for all samples | `None` |
| `CONSOLE_LEVEL` | `logging.INFO`; `logging.DEBUG` also shows counts per label, pattern, segment and chain | `INFO` |
| `SUMMARY_CSV` | Summary of the last run, one row per cell | `last_run_summary.csv` |

**Requirements:** Python with `pandas`, `numpy` and `pyyaml`. The step files must come from TB_CPA_Evaluate
`eval_steps` 2.0 or later; older files are rejected with a message to rerun Evaluate.

**Typical first run for a new definition:** one sample, `EXPORT_LABELS = True`, `SKIP_RERUN = False`. Open
`<cell_id>_steps_labeled.csv` and check the labels, then check `<cell_id>_segments.csv`.

---

## 2. Samples (`samples.yaml`)

One entry per sample:

```yaml
C48T1_B3: {base_path: //server/.../B3_sample,
           nominal_capacity: 213.2, nominal_energy: 701.8, max_volt: 3.8, min_volt: 2.5, vbc_volt: 3.5,
           definition: cpa/rpt_standard}
```

| Key | Meaning | Name in formulas |
|---|---|---|
| `base_path` | sample root; the step files are read from `<base_path>/04_Evaluated_Data/<cell_id>/` | – |
| `nominal_capacity` | Ah (required) | `cap` |
| `max_volt`, `min_volt`, `vbc_volt` | V (required) | `vmax`, `vmin`, `vbc` |
| `nominal_energy` | Wh (optional; NaN if missing, so a formula using it never matches) | `e_nom` |
| `std_temperature` | °C, optional, default 25 | `std_temp` |
| `temp_threshold` | ± °C around std_temperature, optional, default 3 | `tol` |
| `definition` | definition folder below `definitions/`, e.g. `cpa/rpt_standard` (required) | – |

- **Cell IDs:** every subfolder of `04_Evaluated_Data` that contains a file matching `*step*feature*.csv` is one cell,
  and the folder name is the cell ID. If a folder has several matching files, the last one in alphabetical order is
  used and a warning is logged.
- Formulas can also use `inf`.

---

## 3. Definitions

### 3.1 Folders and files

A definition is a folder anywhere below `definitions/`. A project can hold several definitions, and each sample picks one:

```
definitions/
├── common/                          shared by all projects
│   └── 01_labels_c3.yaml
└── cpa/                             project
    ├── 01_labels_cpa.yaml           (optional) shared by the definitions of this project
    ├── rpt_standard/                definition "cpa/rpt_standard"
    │   ├── 00_settings.yaml
    │   ├── 01_labels.yaml
    │   ├── 02_patterns.yaml
    │   ├── 03_segments.yaml
    │   └── 04_chains.yaml
    └── rpt_short/                   another definition, e.g. for samples with a shorter RPT plan
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
  Without `00_settings.yaml` the defaults apply.
- Item names (labels, patterns, segments, chains) must be unique across all levels. `RPT` cannot be both a pattern and a segment.

### 3.2 `include:` (reusing shared files)

```yaml
# cpa/rpt_standard/01_labels.yaml
include: [common/01_labels_c3.yaml]
labels:
  RELAX: {...}        # replaces the RELAX from common/01_labels_c3.yaml
```

- `include:` means: treat the items of that file as if they were written here. A limit changed in a shared file
  therefore applies to every definition that includes it, and those cells rerun (§9).
- **Paths are relative to the `definitions/` folder**, wherever the including file is: `common/01_labels_c3.yaml`
  or `cpa/01_labels_cpa.yaml`. Absolute paths also work. A path written relative to the file (`../../common/…`) gives a
  "not found" problem.
- An included file may include further files. Each file is read at most once.
- **Overriding:** an item in the definition's own files replaces an included item of the same name. Any other
  duplicate is a problem: the same name twice in the definition's own files, in two included files, or twice in one file.
- Included files follow the same numbered-file rule.

### 3.3 Checks before anything runs

`load_definition` checks the whole definition, including all included files, and reports **all** problems at once:

- unknown names in patterns, segments, chains, `collapse` and `between`
- misspelled keys (e.g. `sequense:`, `colapse:`, unknown settings)
- formula errors: syntax, unknown names, anything that is not arithmetic
- ranges that are not exactly `[low, high]`
- a name used twice, an include that is not found, a section in the wrong numbered file
- a label with no conditions, or `default_label` that is also defined as a label

**Not checked at load time: step-feature column names**, because they are only known once a step file is read.
A misspelled column (`Curent_A_median`) is reported when the first cell runs: the console and log show a warning,
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

Numbers like `1e-6`, `-1e-6` and `3600*24` work as written.

---

## 4. Labels (`01_labels.yaml`)

```yaml
labels:
  CHA_C3_CC:                                   # every condition must hold (AND)
    description: Full C/3 charge               # optional, ignored by the matching
    Current_A_median:       [cap*0.95/3, cap*1.05/3]
    delta_Capacity_step_Ah: [cap*0.65, cap*1.1]
    Voltage_V_end:          [vbc+0.01, vmax+0.01]

  RELAX:                                       # list of blocks = OR (any block may hold)
    - {Current_A_median: [-1e-6, 1e-6], time_duration_s: [1, 3600*24]}
    - {time_duration_s: [0, 1e-4]}

  GAP:
    step_name: Gap                             # equality
    time_duration_s: [600, null]               # null = open side (here: >= 600 s)

  STEP_1_OR_2:
    step_id: 1|2                               # any of

  DCH_SPIKE:                                   # neighbour steps
    prev.Current_A_median: [-cap*1.05/3, -cap*0.95/3]          # previous step was a C/3 discharge
    Current_A_median:      [null, prev.Current_A_median*1.5]   # and this step is >= 1.5x stronger
    time_duration_s:       [0, 30]
```

**Conditions**

| Form | Meaning |
|---|---|
| `column: [low, high]` | `low <= value <= high`, limits included. Low and high may be swapped. `null` = open side |
| `column: value` | equality. Text is compared exactly (case-sensitive, surrounding spaces ignored); numbers as numbers (`5` = `5.0`) |
| `column: A\|B\|C` | any of the values |
| `prev.column: …` / `next.column: …` | the same condition, on the previous / next row of the step table |
| `- {…}` `- {…}` | OR of blocks; inside a block all conditions must hold |

- A two-item list is always a range. For "any of" use `A|B`, not `[A, B]`.
- **Formulas** are allowed in range limits only, not in equality values. They may use `cap, e_nom, vmax, vmin, vbc,
  std_temp, tol, inf`, numbers, `+ - * / ** % //`, `abs(x)`, `min(a, b)`, `max(a, b)`, and neighbour values
  `prev.<column>` / `next.<column>`. For column names with special characters use `prev['SOC_eval_start_%']`,
  quoted in YAML (§3.4).
- **Priority:** labels are checked in order. The first label whose conditions hold wins, and a step has exactly one label.
  Put specific labels before general ones (in the RPT definition, `INIT_DCH` comes before `DCH_C3_CC_PARTIAL`).
  The definition's own labels are checked before included labels, in file order.
- Steps that fit no label get `settings.default_label` (`NO_LABEL`). That name can be used in patterns, segments and `between` lists.

**Missing values never match**

- A NaN in the column fails the condition. Gap rows, for example, have no current.
- `prev` of the first step and `next` of the last step have no value, so `prev.` / `next.` conditions fail there.
- A formula without a value (e.g. `prev.Current_A_median*1.5` on the first step) fails, even when the other limit is `null`.
- A column missing from the step file fails (warning, see §3.3).

**Neighbours are rows of the step table, including Gap rows.** TB_CPA_Evaluate inserts a virtual `step_name = Gap`
row for every pause > 30 min. For the step right after a pause, `prev` is that Gap row, so `prev.Current_A_median`
is NaN there. To look past a pause, add a block or label that covers this case.

---

## 5. Settings and collapse (`00_settings.yaml`)

```yaml
settings:
  collapse: [RELAX]          # default [RELAX]
  default_label: NO_LABEL    # default NO_LABEL
```

**Collapse** solves one problem: cyclers often split one rest into several steps. A data sequence
`CHA_C3_CC, RELAX, RELAX, DCH_C3_CC, RELAX` would not fit `std_cycle = [CHA_C3_CC, RELAX, DCH_C3_CC, RELAX]`.
Before matching, consecutive repeats of the **same** label in the collapse list are counted as one:

```
data      : CHA_C3_CC  RELAX  RELAX  DCH_C3_CC  RELAX
collapsed : CHA_C3_CC  RELAX         DCH_C3_CC  RELAX      → std_cycle fits, the match covers all 5 steps
```

- Only labels in the list are merged. Two `DCH_Pulse_18s_1.5C` in a row stay two.
- Only identical repeats are merged. `RELAX, GAP, RELAX` stays three items.
- A merged run belongs completely to the match, also at the start and end of a match.
- **Consequence:** while `RELAX` is collapsed, a sequence written as `[…, RELAX, RELAX, …]` can never match,
  because the data never contains two RELAX in a row after merging. Write one `RELAX`.

**Which collapse list applies where**

| What is searched | Collapse list used |
|---|---|
| a pattern on its own (its `pattern` rows) | the pattern's own `collapse:` if set, else `settings.collapse` |
| a segment, including the patterns inside it | the segment's own `collapse:` if set, else `settings.collapse` |
| a chain | no collapse: chains match segments; the labels between segments are checked against `between` |

- An own `collapse:` **replaces** the global list; it does not add to it. `collapse: []` = no merging.
- A pattern's own `collapse:` has **no effect inside segments**. Inside a segment the pattern is expanded into its
  labels, and the segment's list applies to the whole sequence. To make the pulses inside RPT strict, set `collapse: []` on the segment.

---

## 6. Patterns (`02_patterns.yaml`)

```yaml
patterns:
  std_cycle: [CHA_C3_CC, RELAX, DCH_C3_CC, RELAX]           # short form
  two_cycles: [std_cycle*2]                                  # patterns may use patterns
  pulse:                                                     # long form
    description: DCIR pulse block
    sequence: [DCH_C3_CC_PARTIAL, RELAX, DCH_Pulse_18s_1.5C, RELAX, CHA_Pulse_18s_1.5C, RELAX]
    collapse: []                                             # optional, see §5
```

- Items are labels or other patterns. `NAME*n` repeats an item n times. A string `"std_cycle*2 + CHA_C3_CC"`
  works as well as a list. A pattern may not refer to itself, directly or through others.
- **Every pattern is searched on its own**, whether or not a segment uses it, and every match gives one `pattern` row.
- **Search:** left to right over the (collapsed) labels. When the pattern fits, the match is recorded and the search
  continues after its last step, so matches of one pattern never overlap. Matches of different patterns may overlap
  (e.g. `std_cycle` and `two_cycles`).

---

## 7. Segments (`03_segments.yaml`)

```yaml
segments:
  RPT:
    description: RPT - capacity check + DCIR pulses
    collapse: [RELAX]                  # optional, see §5
    variants:                          # tried in this order at every position
      RPT_01: [std_cycle*3, CHA_C3_CC, RELAX, pulse*4]
      RPT_02: [std_cycle*3, CHA_C3_CC, RELAX, pulse*3]
      RPT_03: [std_cycle*2, CHA_C3_CC, RELAX, pulse*4]
      RPT_04: [std_cycle*2, CHA_C3_CC, RELAX, pulse*3]
  STD_CAP_CHECK: [std_cycle*3]         # short form: one variant, named like the segment
```

- A variant is a list of patterns and labels (`NAME*n` allowed). Patterns are expanded into their labels, and the
  whole variant is matched as one label sequence. The segment does not use the `pattern` rows found in §6.
- **Variants are chosen per location.** The search goes left to right. At each position the variants are tried in the
  order written, and the first that fits is recorded. The search then continues after it. One cell can contain
  `RPT_01` at one place and `RPT_04` at another.
- **Order the variants longest/most complete first.** If `RPT_02` (3 pulse blocks) were listed before `RPT_01`
  (4 pulse blocks), `RPT_02` would always win at the start of a 4-pulse RPT and the 4th pulse block would be left over.
- Matches of one segment never overlap. Different segments are searched independently and may overlap:
  `RPT` (no initial discharge) is also found inside every `INIT_RPT`, and `STD_CAP_CHECK` inside every `RPT`.
  This is intended. Filter by `name` in the output.
- Each match gives one `(whole)` row plus one row per part, in order (`part_index` 0, 1, …). A part is a pattern or
  a label of the variant, and repeats count separately (`std_cycle*3` = parts 0, 1, 2). For example, "the 2nd std_cycle of
  this RPT" = `part == "std_cycle"` and the second such row.

---

## 8. Chains (`04_chains.yaml`)

```yaml
chains:
  INIT_RPT_STORAGE_RPT:
    description: Initial RPT, storage break, RPT
    variants:
      CH_01: [INIT_RPT, RPT]                    # segments, NAME*n allowed (RPT*3)
    between: [RELAX, GAP, STORAGE_28D]          # labels allowed between two segments
    between_pairs:
      INIT_RPT>RPT: [RELAX, STORAGE_28D]        # replaces `between` for this pair only
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
- Each match gives one `(whole)` row plus one row per segment. `part_variant` and `part_instance` say which
  segment match it is, so it can be found among the `segment` rows (same `name` = part, same `instance`).

---

## 9. Output

### `<cell_id>_segments.csv` (in `04_Evaluated_Data/<cell_id>/`)

One row per detected pattern, segment or chain (`part = (whole)`), plus one row per part of each segment and chain.

| Column | Meaning |
|---|---|
| `cell_id` | cell (folder name) |
| `level` | `pattern`, `segment` or `chain` |
| `name` | pattern / segment / chain name |
| `variant` | variant that matched (empty for patterns) |
| `instance` | 0, 1, 2, … per `name` in time order |
| `part` | `(whole)`, or the part's name (pattern / label for segments, segment for chains) |
| `part_index` | -1 for `(whole)`, else 0, 1, … in order |
| `part_kind` | `pattern`, `segment`, `chain` for `(whole)` rows; `pattern` / `label` / `segment` for parts |
| `part_variant`, `part_instance` | chain parts only: variant and instance of that segment |
| `step_id_start`, `step_id_end` | `step_id` of the first and last step (Gap rows have `x.5` ids) |
| `row_start`, `row_end` | raw data rows: `processed_data.parquet.iloc[row_start:row_end]` (`row_end` exclusive) |
| `n_steps` | number of step rows, Gap rows included |
| `step_pos_start`, `step_pos_end` | positions in the step file: `steps.iloc[step_pos_start : step_pos_end + 1]` (end inclusive) |

Example:

```
level    name       variant  instance  part       part_index  part_kind  step_id_start  step_id_end  row_start  row_end
segment  RPT        RPT_01   1         (whole)    -1          segment    43             81           2100       4050
segment  RPT        RPT_01   1         std_cycle  0           pattern    43             47           2100       2350
segment  RPT        RPT_01   1         std_cycle  1           pattern    48             51           2350       2550
segment  RPT        RPT_01   1         CHA_C3_CC  3           label      56             56           2750       2800
```

Gap rows have no raw data (`row_start == row_end`), so a range that ends in a Gap row has the same `row_end` as the step before it.

### Other files

| File | Content |
|---|---|
| `<cell_id>_steps_labeled.csv` | only with `EXPORT_LABELS`: the step file plus column `label` |
| `<cell_id>_segments_meta.json` | fingerprint for skip/rerun; also the `eval_version` and the time it was written |
| `last_run_summary.csv` | per cell: `sample, cell_id, status` (`done` / `skipped` / `failed`), counts of patterns / segments / chains, `seconds`, `message` (missing columns or the error) |
| `06_Logs/debug_logs/segmentation_debug_<host>.log` | full log per sample, DEBUG level, appended on every run; tracebacks of failed cells |

All files are overwritten on each run of a cell. If a cell **fails**, its files from an earlier run are **not** deleted.
Check `status` in the summary or the log.

### Skip / rerun (`SKIP_RERUN = True`)

A cell is skipped only if `<cell_id>_segments.csv` and the meta file exist (and the labeled file, if `EXPORT_LABELS`),
and all of these are unchanged:

| Fingerprint | Changes when |
|---|---|
| step file name and modification time | TB_CPA_Evaluate rewrote the step file |
| definition name and hash | anything that affects matching changed, **including in included files**, and also descriptions of patterns, segments and chains (label descriptions do not count) |
| sample hash | any value of the sample in `samples.yaml` except `base_path` |
| `SEG_V2_VERSION` (`seg/run.py`) | the code logic changed (bump it by hand) |

Log level, `LOG_FOLDER` and `CELL_IDS` do not trigger a rerun. Set `SKIP_RERUN = False` to force a rerun.

---

## 10. Using the functions on their own

Every function has a docstring with parameters, return value and an example (`help(seg.find_segments)`).

```python
import pandas as pd
from seg import (read_samples, load_definition, label_steps, find_patterns, find_segments,
                 find_chains, segment_cell, process_sample, setup_logging)

setup_logging()                                              # optional: log to the console
sample_config = read_samples()["C48T1_B3"]
definition    = load_definition(sample_config["definition"], sample_config)    # raises DefinitionError
steps         = pd.read_csv(step_file, encoding="utf-8-sig")

labeled_steps = label_steps(steps, definition)               # copy of steps + column 'label'
pattern_rows  = find_patterns(labeled_steps, definition)     # list of dicts
segment_rows  = find_segments(labeled_steps, definition)
chain_rows    = find_chains(labeled_steps, segment_rows, definition)

labeled_steps, detections = segment_cell(steps, definition, "FCA08J0011")   # all four + ids / row ranges
summary = process_sample("C48T1_B3", sample_config, cell_ids=["FCA08J0011"], skip_rerun=False)

# try another definition on a sample without editing samples.yaml
summary = process_sample("C48T1_B3", sample_config, definition_name="cpa/rpt_short")
```

`find_*` return positions (`step_pos_start`, `step_pos_end`) only. `segment_cell` adds the cell ID, step IDs and raw row ranges.

---

## 11. Troubleshooting

| Symptom | Likely cause |
|---|---|
| A label never appears; a warning about missing columns | column name misspelled, or not in this step file |
| A label never appears, no warning | limits too tight; or an earlier label already takes these steps (priority); or a NaN in a column; check `_steps_labeled.csv` |
| A pattern with `RELAX, RELAX` never matches | RELAX is collapsed, so write one `RELAX` (§5) |
| A segment is found as a shorter variant than expected | variant order: list the longest variants first (§7) |
| `RPT` rows inside `INIT_RPT` | intended: every segment is searched independently (§7) |
| A chain is not found | a step between the segments has a label that is not in `between`; or another match of the segment comes first (§8) |
| `prev.` condition fails right after a pause | `prev` is the Gap row (§4) |
| A cell is `skipped` although you changed something | the change is not in the fingerprint (e.g. code without bumping `SEG_V2_VERSION`); use `SKIP_RERUN = False` |
| `step table misses ['eval_version']` / `eval_version … needs >= 2.0` | rerun TB_CPA_Evaluate for this cell |
| `include '…' not found` | path must be relative to `definitions/` (§3.2) |
| YAML `ParserError` | quoting (§3.4) |

---

## 12. Current limits

- One label per step; no conditions on a neighbour's **label** (only on its features).
- Chains only link segments; there is no level above chains.
- No KPIs yet (planned per pattern / segment / chain, from step features or raw rows via `row_start:row_end`).

After changing code logic in `seg/`, bump `SEG_V2_VERSION` in `seg/run.py` so that all cells rerun.
