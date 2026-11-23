# Labelling guideline

How to write and maintain step labels (`label_*.yaml` / `01_labels.yaml`) for TB_CPA_Segment.

---

## 1. What a label does

Every row of the step table (one step of the test) gets **exactly one label**.
Labels are checked **in order**; the **first label whose conditions hold wins**.
Steps that match nothing get the default label (`NO_LABEL`).
Patterns (`02_patterns.yaml`) then search for sequences of labels.

```
step features CSV ──► labels (01) ──► patterns (02) ──► segments
```

---

## 2. Where labels live

```
definitions/
├── common/                      shared label sets, used by several projects
│   ├── label_C3_cha_dch.yaml    C/3 charge / discharge
│   ├── label_rest.yaml          pulses, RELAX, GAP
│   └── label_storage.yaml       STORAGE_*
├── custom/
│   └── <project>_definitions/
│       ├── 01_labels.yaml       include: common files + project-specific labels
│       └── 02_patterns.yaml
└── docs/
    └── labelling_guideline.md   this file
```

**Rules**

- Each label is defined in **one** file only. The same label in two included files is an error.
- A label in the project's own `01_labels.yaml` **overrides** an included label of the same name.
- Include paths are relative to `definitions/`; no wildcards, no folders.

```yaml
# custom/c48t1_definitions/01_labels.yaml
include:
  - common/label_C3_cha_dch.yaml
  - common/label_rest.yaml
  - common/label_storage.yaml
labels:
  CC_DCH_C3: {...}      # project-specific override
```

---

## 3. Anatomy of a label

```yaml
CC_DCH_C3:
  description:            Full C/3 constant current discharge at 25°C
  Current_A_median:       [-cap*1.03/3, -cap*0.97/3]
  delta_Capacity_step_Ah: [-cap*1.1,    -cap*0.6]
  time_duration_s:        [0.5*3*3600,  1.2*3*3600]
  or.temp:
    - {T_Chamber_degC_mean: [std_temp-temp_tol, std_temp+temp_tol]}
    - {T_Cell_degC_mean:    [std_temp-temp_tol, std_temp+temp_tol]}
```

- `description` is free text and is not evaluated. Always add one.
- All other rows are conditions. **All rows must hold (AND).**

---

## 4. Condition syntax

| Form | Meaning |
|---|---|
| `column: [low, high]` | `low <= x <= high` (limits included). `null` = open side. Low/high may be swapped |
| `column: value` | equality. Text is compared exactly; numbers as numbers (`5` = `5.0`) |
| `column: A\|B\|C` | any of the values |
| `prev.column: …` / `next.column: …` | same condition on the previous / next row of the step table |
| `or.<name>: [{…}, {…}]` | OR group: at least one block must hold; ANDed with the other rows. Several groups per label allowed; can be nested |
| `- {…}` `- {…}` | list of blocks = OR for the whole label |

**Choosing OR form**

- Only some conditions have alternatives → use `or.<name>`.
- The whole label has completely different definitions → use `- {…}` blocks.
- Never write all combinations by hand; 2 groups × 2 options = 2 `or.` groups, not 4 blocks.

---

## 5. Formulas in range limits

Limits can be numbers or arithmetic formulas. Formulas are **not** allowed in equality values.

| Name | Source (`samples.yaml`) | Meaning |
|---|---|---|
| `cap` | `nominal_capacity` | Nominal capacity [Ah] |
| `e_nom` | `nominal_energy` | Nominal energy [Wh]; NaN if missing → never matches |
| `vmax` / `vmin` | `max_volt` / `min_volt` | Voltage limits [V] |
| `vbc` | `vbc_volt` | `vbc_volt` value [V] |
| `std_temp` | `std_temperature` | Standard temperature [°C], default 25 |
| `temp_tol` | `temp_threshold` | ± tolerance [°C], default 3 |
| `inf` | built in | Infinity |
| `prev.X` / `next.X` | step table | Column X of the previous / next step |

- Functions: `abs(x)`, `min(a, b)`, `max(a, b)`. Operators: `+ - * / ** % //`.
- Columns with special characters: `"prev['SOC_eval_start_%']*2"` — quote the whole formula.
- Write rates readably: `cap/3` for C/3, `3*3600` for 3 h.

---

## 6. Design rules

1. **Order = priority.** Put specific labels before general ones
   (e.g. `CC_DCH_C3` before `CC_DCH_C3_PARTIAL`, pulses before `RELAX`).
2. **Use relative limits.** Prefer `cap`, `vmin`, `std_temp` over hard-coded numbers, so one label fits all samples.
3. **Mind the sign.** Discharge current and capacity are negative.
4. **Edges are included.** Adjacent ranges sharing a value (`[17, 19]` and `[19, 25]`) overlap at that value; the earlier label wins. For a strict `>`, use a small offset (`[0.001, …]`).
5. **Leave no unintended gaps.** E.g. `STORAGE_28D: [27 d, 32 d]` and `STORAGE_56D: [55 d, 60 d]` leave 33–54 d unlabelled. Make sure that is intended.
6. **Missing data never matches.** NaN values, `prev` on the first step, `next` on the last step, and `prev` after a virtual `Gap` row all fail. Use an `or.` group as a fallback if a sensor may be missing.
7. **Check that columns exist.** A column not in the step features CSV only logs a warning and the condition never matches.
8. **Keep tolerances explicit.** E.g. `0.97 … 1.03` for current, `0.6 … 1.1` of `cap` for capacity. Note the reason in `description` if a tolerance is unusual.
9. **One label, one meaning.** If two labels differ only in a parameter that patterns don't care about, merge them.

---

## 7. Naming

- Use one consistent scheme across all files, e.g. `<MODE>_<DIRECTION>_<RATE>[_<VARIANT>]`:
  `CC_DCH_C3`, `CC_CHA_C3`, `CC_DCH_C3_PARTIAL`, `INIT_CC_DCH_C3`.
- Upper case, underscores, no spaces.
- Names are referenced in `02_patterns.yaml`. **Renaming a label means updating all patterns** that use it.

---

## 8. Workflow: adding or changing a label

1. Edit the label in the right file (common vs. project, see §2).
2. Update `02_patterns.yaml` if names changed.
3. Load the definition once – it reports all problems in one list:
   ```python
   load_definition("custom/c48t1_definitions", sample_config)
   ```
4. Run on **one cell** and check the `label` column: count of `NO_LABEL`, unexpected labels, boundary steps.
5. Run all cells. A changed definition triggers a rerun via the definition hash;
   if code changed, bump `SEG_V2_VERSION` in `seg/run.py`.

---

## 9. Checklist before committing

- [ ] `description` filled in
- [ ] Labels are not defined twice/duplicated
- [ ] Order checked (specific before general)
- [ ] Signs correct for discharge
- [ ] Formulas use only allowed names
- [ ] All columns exist in the step features CSV
- [ ] Patterns updated for new / renamed labels
- [ ] `load_definition` runs without errors
- [ ] Tested on at least one cell

---

## 10. Troubleshooting

| Symptom | Likely cause |
|---|---|
| `include … not found` | Wrong path; must be relative to `definitions/` |
| `defined in … and in …` | Same label in two included files |
| `unknown name '…'` in formula | Typo or old name (e.g. `tol`) |
| `unknown name` in patterns | Label renamed but pattern not updated |
| Label never matches, warning in log | Column missing in step features CSV |
| Label never matches, no warning | NaN values, wrong sign, `prev`/`next` at table end or after `Gap` |
| Wrong label wins | Order: a more general label is listed earlier |
| Many `NO_LABEL` steps | Gaps between ranges, tolerances too tight |
