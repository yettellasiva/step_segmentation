# CONTEXT: handoff for TB_CPA_Segmentation_v2

For an engineer or AI continuing this work. The **README** explains how to use the tool; this file explains
**why it is built this way, how the code is organised, what is settled, and what is still open**.
Read this first, then the README, then `docs/labelling_guideline.md`.

> Status: written 2026-11-23. Statements marked **[verified]** were run against the repo.
> **[assumed]** means inferred from conversation and not yet checked against real data. **[open]** means undecided.

---

## 1. Purpose and context

- **Owner:** Siva Yettella, PowerCo DE TA-T (battery cell testing, project families C48T1 / C25T1).
- **What it does:** takes the step-features CSV that **TB_CPA_Evaluate** writes per cell
  (`<base_path>/04_Evaluated_Data/<cell_id>/<cell_id>_step_features.csv`) and finds known test structures in it:
  RPT campaigns, C/3 capacity cycles, DCIR pulse blocks, storage breaks.
- **Output:** `<cell_id>_segments.csv`, one row per detected pattern / segment / chain and per part, with step
  ranges and raw-data row ranges (`row_start:row_end` into the processed parquet).
- **Why:** KPIs (capacity, DCIR, …) will be computed per detected segment. KPIs are **not** part of v2 yet.
- **Related repos:** TB_CPA_Evaluate (produces the step features), TB_CPA_KPIs (consumes segments, being
  reworked so that it stops recomputing what Evaluate already stores, e.g. `Q_std`, SOC, throughput).
- **Environment:** Windows corporate machines, PyCharm + venv, Databricks for storage elsewhere. This tool runs
  locally on network-drive data. Corporate SSL / proxy issues exist for pip and Git (not relevant to this code).

---

## 2. Architecture

### 2.1 Pipeline

```
step_features.csv
  │ check_step_table        needs step_id, row_start, row_end, eval_version >= 2.0
  │ label_steps   (pass 1)  every step gets ONE label; first matching label wins
  │ merge_steps             join split steps; bridges (spikes) are dropped from the values
  │ label_steps   (pass 2)  same labels on the merged table
  │ item_condition_masks    conditions of pattern/segment items, evaluated on the merged table
  │ find_patterns           every pattern, searched on its own
  │ find_segments           every segment (variants), matched on the label sequence
  │ find_chains             ordered segments with allowed `between` labels
  ▼ positions mapped back to the ORIGINAL step file (orig_pos_start / orig_pos_end)
<cell_id>_segments.csv
```

### 2.2 Modules (`seg/`)

| Module | Responsibility | Key entry points |
|---|---|---|
| `definitions.py` | Read, include, validate and compile the YAML definition. Reports **all** problems at once (`DefinitionError`) | `load_definition`, `formula_names`, `check_formula`, `_parse_block`, `_parse_item`, `_expand_sequence` |
| `labeling.py` | Evaluate label conditions on a step table | `label_steps`, `block_mask`, `condition_mask`, `item_condition_masks`, `_formula_scope` |
| `merge.py` | Join split steps before the final labelling | `merge_steps`, `merge_groups`, `COLUMN_RULES`, `ELAPSED_PATTERN` |
| `patterns.py` | Sequence search shared by patterns and segments | `find_patterns`, `match_variants` |
| `segments.py` | Segments with variants | `find_segments` |
| `chains.py` | Ordered segments with `between` labels | `find_chains` |
| `run.py` | Per-cell and per-sample orchestration, file output, skip/rerun fingerprint, logging | `segment_cell`, `process_sample`, `SEG_V2_VERSION` |

Entry script: `run_segmentation.py` (settings at the top). Config: `samples.yaml`. Definitions: `definitions/`.

### 2.3 Data shapes (compiled definition)

`load_definition(name, sample_config)` returns a dict:

```
{"name", "labels", "patterns", "segments", "chains", "settings", "item_conditions", "formula_names", "hash", ...}

labels[name]          = {"description", "blocks": [ [ (column, neighbour, condition), ... ], ... ]}
                        blocks are OR-ed; conditions inside a block are AND-ed
condition             = ("range", low, high) | ("equal", value | [values]) | ("any", [block, ...])   # or.<name> group
neighbour             = None | "prev" | "next" | "or"
patterns[name]        = {"description", "labels": [token, ...]}        # nested patterns already expanded
segments[name]        = {"description", "variants": {variant: [ {"name", "kind", "labels": [token, ...]}, ... ]}}
chains[name]          = {"description", "variants": {variant: [segment, ...]}, "between": set, "between_pairs": {(a, b): set}}
settings["merge"]     = {"join_adjacent": set, "bridges": {label: max_per_merged_row | None}}
item_conditions[key]  = {"labels": [...], "block": [...], "display": "REST"}
```

A **token** in a pattern / segment is either a plain label name or an **item key** (a string
`"<labels> <json of the raw conditions>"`) that points into `item_conditions`. `match_variants` checks a plain token
by label equality and an item key by a precomputed boolean mask from `item_condition_masks`.

### 2.4 Positions

Matching runs on the **merged** table. Before writing output, `segment_cell` translates `step_pos_*` through
`orig_pos_start` / `orig_pos_end` into the **original** step file and looks up `step_id_*` and `row_*` there.
So all positions in `segments.csv` refer to the original file, while `_steps_labeled.csv` holds the merged table.

---

## 3. Decision log

Each entry: decision, reason, and what was rejected. Do not reverse these without a new reason.

| # | Decision | Why | Rejected alternatives |
|---|---|---|---|
| D1 | **One label per step, first match wins**, order = priority | Simple, deterministic, easy to explain and debug | Multi-label steps (changes the core model, list-valued output, ambiguity with merging) |
| D2 | **Label → merge → relabel** (two passes) | A CC step split by a spike has half the capacity on each side. Labelled separately it becomes `CC_DCH_C3_PARTIAL` and joining the partials cannot recover `CC_DCH_C3`. Merging first lets pass 2 see full duration and capacity | Merging only in the pattern stage (`collapse`); merging in Evaluate (needs raw data, bigger change) |
| D3 | **`collapse` removed** | Fully replaced by merge; keeping both is confusing. `collapse:` is now reported as an unknown settings key | Keeping it alongside merge |
| D4 | **Merge rules:** `join_adjacent` (same label, only listed labels, currently `REST`) and `bridges` (spike dropped if both neighbours have the same `mode`) | Rest split by the cycler and spikes inside rests / CC phases are the real edge cases. Charge/discharge steps must not be joined blindly: CC followed by CV, or discharge followed by a pulse, are separate steps | Joining all adjacent same-mode steps |
| D5 | **Bridge limit per merged row** (`REST_SPIKE: 3`, `CURRENT_SPIKE: 5`, `null` = none) | A rest with many spikes is probably not a clean rest. Above the limit the bridge stays its own step | Global limit; no limit |
| D6 | **Bridges are left out of the merged values** and only counted (`n_bridged`, `bridged_s`) | "Ignore the spike" was the requirement. The spike time is not in `time_duration_s` | Counting the spike in the duration (can be done with a formula on `bridged_s` if wanted) |
| D7 | **Column combine rules** by name pattern (`COLUMN_RULES`): durations and `delta_*` summed, `*_start` first, `*_end` last, medians/means duration-weighted, `*_std` max. Unknown columns take the first part and warn | Cheap, transparent, extendable. Merged medians/std are approximations, acceptable for labelling and **not for KPIs** | Recomputing from raw data (exact but expensive, needs parquet access in Segment) |
| D8 | **Time-based columns `*_at_<t>s`: value from the first part if it lasts ≥ t s, else NaN** | The time point lies in the first part only then. A later point was never computed, so NaN is honest and a label using it fails visibly. Negative times such as `-18s` were a misuse and are not supported | Guessing or interpolating |
| D9 | **Conditions on pattern/segment items** (`{label: REST, time_duration_s: [..]}`), evaluated on the merged table | Avoids a label per duration (`REST_15MIN`, `REST_1H`, …) and works on merged rests. Same condition syntax as labels | Label families (`REST.15MIN`), multi-label steps, post-match checks (still an option for reporting deviations) |
| D10 | **Conditions only on labels, not on patterns; none on chain items** | Keeps matching a simple sequence search with per-step masks | Conditions on whole patterns |
| D11 | **Labels used by merge must be labels, not item conditions** | Merge runs before pass 2 and only sees pass-1 labels. `REST_SPIKE` and `CURRENT_SPIKE` stay labels | – |
| D12 | **`or.<name>` OR-groups inside a label** | 2 temperature options × 2 voltage options would otherwise need 4 copies of the label. Single-option groups are pointless; write plain rows | Writing every combination; `A \| B` between ranges (not valid YAML/semantics) |
| D13 | **Closed intervals** `low <= x <= high`; no strict `<`/`>` | Simple; use a small offset where needed. Edge values shared by two labels go to the earlier label | Strict-bound syntax |
| D14 | **Missing values never match** (NaN, `prev` of first step, `next` of last, a missing column) | Silent false matches are worse than non-matches. Missing columns only warn, so a typo disables a condition (see risk R3) | Raising on missing columns (breaks the `or.` fallback idea) |
| D15 | **Formulas in range limits only**, restricted AST (arithmetic, `abs/min/max`, allowed names) | Safe and checkable at load time | `eval` |
| D16 | **Definitions are YAML, `include:` for shared label files, one label defined in exactly one included file** | Shared label sets across C48T1 / C25T1; own labels override included ones | One big file per project |
| D17 | **Skip/rerun fingerprint:** step file name + mtime, definition hash (including includes, merge settings, item conditions), sample hash (except `base_path`), `SEG_V2_VERSION` | Thousands of cells; must be resumable and must rerun when logic changes | Always rerun |
| D18 | **Rename `tol` → `temp_tol`** (formula name) | Clarity. `samples.yaml` key stays `temp_threshold` | – |
| D19 | **Naming scheme** `<MODE>_<DIRECTION>_<RATE>[_<VARIANT>]` (`CC_DCH_C3`, `CC_CHA_C3_PARTIAL`, `INIT_CC_DCH_C3`) | Consistency across files | Old names (`DCH_C3_CC`, `CHA_C3_CC`, `RELAX`) |
| D20 | **Patterns and segments are searched independently**, so results overlap by design (`RPT` inside `INIT_RPT`) | Filter by `name` in the output | Exclusive assignment |

Superseded: the idea of making `join_adjacent` join **different** rest-duration labels (`REST`, `REST_15MIN`) was
dropped once D9 made duration labels unnecessary. `join_adjacent` still joins only identical labels.

---

## 4. Current state of the repo

### Works [verified]
- `load_definition`, both label passes, merge, item conditions, patterns, segments and chains run end to end on a
  synthetic ten-step cell, including a rest split by a current spike (600 s + spike + 300 s → one 900 s `REST`
  matching `{label: REST, time_duration_s: [14*60, 16*60]}`).
- Load-time error messages for item conditions (unknown label, missing `label:`, bad formula, `repeat: 0`,
  conditions in a chain) and the definition hash reacting to changed item limits.

### Broken or inconsistent right now
1. **`seg/definitions.py` in the user's repo had three bugs** that stop it from loading
   (`TypeError: unhashable type: 'dict'`): `resolve_pattern` did not pass `parse_item`; the segment loop used the
   pattern's sequence instead of the segment's `items`; the chain loop contained pasted segment code and lost its
   variant loop. A corrected `definitions.py` was delivered; **it must be applied** and re-tested. The chain-loop
   rewrite was reconstructed from the sample code that was visible, so compare it with the original.
2. **`02_patterns.yaml`, `03_segments.yaml`, `04_chains.yaml` still use the old names:**
   `INIT_DCH`, `RELAX`, `CHA_C3_CC`, `DCH_C3_CC`, `DCH_C3_CC_PARTIAL`, `DCH_Pulse_18s_1.5C`, `CHA_Pulse_18s_1.5C`.
   Loading reports 18 problems. Map: `INIT_DCH → INIT_CC_DCH_C3`, `RELAX → REST`,
   `CHA_C3_CC → CC_CHA_C3`, `DCH_C3_CC → CC_DCH_C3`, `DCH_C3_CC_PARTIAL → CC_DCH_C3_PARTIAL`.
3. **No pulse labels exist.** The `pulse` pattern needs `DCH_Pulse_18s_1.5C` / `CHA_Pulse_18s_1.5C` equivalents.
4. **`custom/c25t1_definitions` does not exist**, but two samples in `samples.yaml` point to it.
5. **Stale text:** header comments in `02_patterns.yaml` and `03_segments.yaml` still describe `collapse`;
   `docs/labelling_guideline.md` §2 folder tree is outdated (`common/TA_T/…`, `label_spike.yaml`) and does not
   yet cover merge or item conditions.
6. `label_rest.yaml` uses the group name `or.current_adjacent`; the preferred name is `or.neighbour_modes`.
   Cosmetic.

### Version
`SEG_V2_VERSION = "2.1"` in `seg/run.py`. Bump it after every change to matching or merge logic, otherwise cells
with `SKIP_RERUN = True` are skipped.

---

## 5. Open questions and unverified assumptions

| Id | Item | Status | What to check |
|---|---|---|---|
| A1 | **`mode` column and values** (`REST`, `GAP`, `CHARGE`, `DISCHARGE`) exist in the step features with exactly this spelling | **[assumed]** | Header and values of a real step features CSV. If wrong, almost everything becomes `NO_LABEL` (plain `mode:` rows) and merge does nothing |
| A2 | **Step-features column list.** Only the time-series columns were seen (`Voltage_V`, `Current_A`, `Step_id_calc`, `Mode_calc`, …). Columns used by labels (`Current_A_median`, `Current_A_std`, `Voltage_V_end`, `T_Chamber_degC_mean`, `T_Cell_degC_mean`, `T_Cell_degC_start`, `time_duration_s`, `delta_Capacity_step_Ah`, `SOC_eval_end_%`, `cc_current_A`, `phase_coverage`) are **inferred** | **[assumed]** | Compare with the real header; the label-review agent also needs it |
| A3 | **`*_at_<t>s` naming** and the regex `ELAPSED_PATTERN` | **[assumed]** | Real names of time-based columns (`Voltage_V_at_18s`?) |
| A4 | **`SOC_eval_end_%` ranges** (`[-1, 1]` for empty, `[99, 101]` for full) | **[assumed]** | Real SOC scale |
| A5 | **Gap threshold:** Evaluate inserts virtual Gap rows for pauses > 30 min, so `SHORT_GAP_1H` (900–3600 s) can only match 30–60 min | **[assumed]** | Evaluate's actual threshold |
| A6 | `REST_SPIKE` only checks neighbour **modes**, so it can bridge two different discharges separated by a short rest | **[open]** | Add the same-current condition `next.Current_A_median: [prev.Current_A_median*0.95, prev.Current_A_median*1.05]` |
| A7 | `REST` and `REST_SPIKE` / `REST_TRANSITION` share 1.0 s (earlier label wins). A rest under 1 s with other neighbours is `NO_LABEL` | **[open]** | Whether a catch-all short-rest label is wanted |
| A8 | **Overlap of `INIT_CC_CHA_C3` and `CC_CHA_C3`** (capacity 0.3–0.9 vs 0.65–1.1, INIT listed first) | **[open]** | Lower INIT's upper limit to `cap*0.65` or reorder |
| A9 | **Partial labels leave a gap above 0.98 × cap** | **[open]** | Intended as "flag as NO_LABEL"? |
| A10 | **Cell temperature window is asymmetric** (`std_temp+temp_tol*2`) in all labels, without a comment | **[open]** | Confirm intent, add a comment |
| A11 | **Merged medians / std are approximations**; time values after the first part are NaN | known limit | If it hurts, recompute from raw rows `row_start:row_end` |
| A12 | **No automated tests** in the repo. Checks so far were ad hoc scripts | **[open]** | Add `tests/` with the synthetic cell from §4 as a fixture |
| A13 | **KPIs** per pattern / segment / chain | **[open]** | Planned; TB_CPA_KPIs rework is in progress |

### Known risks
- **R1:** `load_definition` cannot check step-feature column names (they are only known at runtime), so a typo
  disables a condition with only a warning. Check warnings after the first run of any new label.
- **R2:** Merging depends on pass-1 labels. If `REST_SPIKE` / `CURRENT_SPIKE` fail to match (wrong `mode` spelling,
  missing column), nothing is merged and split steps get partial labels.
- **R3:** Labels run twice and must be correct on **both** the original and the merged table.
- **R4:** Two steps with the same mode can be merged by mistake if a bridge label is too loose (A6).

---

## 6. Where to change what

| Task | Where |
|---|---|
| Add / rename a formula name (`cap`, `temp_tol`, …) | `formula_names()` in `seg/definitions.py`, then every YAML using it, README §2 / §4, guideline |
| Add a formula function | `FORMULA_FUNCTIONS` in `seg/definitions.py` |
| Change how merged columns are combined, or add a rule | `COLUMN_RULES` in `seg/merge.py` (first matching rule wins). Watch the warning `merge: no combine rule for [...]` after a run |
| Change the time-based column naming | `ELAPSED_PATTERN` in `seg/merge.py` |
| Change `mode` column name or duration column | `MODE_COLUMN`, `DURATION_COLUMN` in `seg/merge.py` |
| Change what a step table must contain | `REQUIRED_STEP_COLUMNS`, `check_step_table` in `seg/run.py` |
| Add a settings or merge key | `SETTINGS_DEFAULT`, `MERGE_KEYS` and the settings block in `load_definition` |
| Add a condition type | `_parse_block` (compile) and `condition_mask` (evaluate); `("any", …)` shows how a composite is handled |
| Change pattern / segment matching | `match_variants` in `seg/patterns.py` (shared by patterns and segments) |
| Change chain matching | `seg/chains.py` |
| Change output columns | `DETECTION_COLUMNS` and `segment_cell` in `seg/run.py`; README §9 |
| Add a new label | `definitions/common/TA_T/…` (shared) or the project's `01_labels.yaml`; follow `docs/labelling_guideline.md`; update patterns |

After any logic change: bump `SEG_V2_VERSION`, run `load_definition` for every definition folder, run one cell with
`EXPORT_LABELS = True`, `SKIP_RERUN = False`, then check `NO_LABEL` share, merge columns and warnings.

---

## 7. Related material

| File | Purpose |
|---|---|
| `README.md` | User documentation: usage, YAML syntax, output files, troubleshooting |
| `docs/labelling_guideline.md` | How to write labels: syntax, design rules, naming, checklist. **Needs updating** for merge, item conditions and the new folder layout |
| Label-review agent instructions | A drafted agent prompt (not stored in the repo) that reviews and drafts labels using the guideline and the step-features column list. It relies on the guideline being current and on the real step-features header being pasted in (A2) |

---

## 8. Working conventions (user preferences)

- **Stay in scope.** Do what was asked; mention other problems in one line instead of fixing them. No redesigns
  without being asked.
- **Minimal, close-to-existing changes.** Reuse existing columns and structure; reduce duplicated work.
- **Give exact edits:** which file, which function, the replacement code. Prefer whole replacement blocks over
  vague instructions.
- **Docstrings** in the repo style: description, `Parameters`, `Returns`, short example. Comments in plain, natural
  wording, not "AI-like" phrasing.
- **Root cause first** when troubleshooting, then the minimal safe fix.
- **Say what was not run.** State plainly whether code was tested, and list assumptions instead of presenting them
  as facts.
- **Iterate.** The user refines step by step and often narrows the scope ("only change X"). Keep earlier decisions
  (§3) consistent across modules and docs.
- **YAML layout:** 2-space indentation, aligned values, comments for limits, block style for `or.` alternatives.
  PyCharm: Ctrl+Alt+L reformats, but check the alignment afterwards.
- Technical vocabulary is fine; give shorter answers to simple questions and step-by-step detail for complex ones.

---

## 9. Suggested next steps

1. Apply the corrected `seg/definitions.py`; run `load_definition` for `custom/c48t1_definitions`.
2. Paste the real step-features header into the review-agent prompt and check A1–A3 against it. Do this first,
   because everything else depends on `mode` and the column names.
3. Update `02_patterns.yaml`, `03_segments.yaml`, `04_chains.yaml` to the new label names; add pulse labels.
4. Create `custom/c25t1_definitions` or point those samples to an existing definition.
5. Run one real cell with `EXPORT_LABELS = True`; read `_steps_labeled.csv` (`label`, `n_merged`, `n_bridged`) and the
   warnings; decide A6–A10.
6. Update `docs/labelling_guideline.md` (merge, item conditions, folder tree).
7. Add `tests/` with the synthetic cell as a regression fixture (A12).
8. Then: KPIs per detected segment.
