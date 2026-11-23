# Step-features columns

Columns of `<cell_id>_step_features.csv` (TB_CPA_Evaluate, eval_steps >= 2.0) that labels and pattern items
can use. The definition agent (`docs/segment_definitions_agent.md`) uses **only** columns from this list.

> **TO DO (owner):** this list was inferred from the existing labels and is **not checked against a real file**.
> Paste the real header below (§3), then correct every row marked `?` and add the missing columns.

## 1. Conventions

| Topic | Convention |
|---|---|
| Sign | Charge current / capacity **positive**, discharge **negative** ? |
| Time | `time_start_s` = Unix seconds; durations in s |
| Gap rows | Virtual rows for pauses > 30 min ?, `mode = GAP`, step_id `x.5`, no current / voltage values |
| Missing value | empty / NaN → a condition on it never matches |

## 2. Columns

| Column | Unit | Meaning | Values / typical range | Checked |
|---|---|---|---|---|
| `step_id` | – | step number; Gap rows `x.5` | 0, 1, 2, … | ? |
| `row_start`, `row_end` | – | raw-data rows `iloc[row_start:row_end]` | | ? |
| `eval_version` | – | TB_CPA_Evaluate version | `2.0` … | ? |
| `mode` | – | step type | `CHARGE`, `DISCHARGE`, `REST`, `GAP` (exact spelling?) | ? |
| `time_start_s` | s | step start, Unix time | | yes |
| `time_duration_s` | s | step duration | | ? |
| `Current_A_start`, `Current_A_end` | A | current at step start / end | | ? |
| `Current_A_median`, `Current_A_mean` | A | median / mean current of the step | | ? |
| `Current_A_std` | A | standard deviation of the current | | ? |
| `Current_A_at_<X>s` | A | current X s after the step start (X = 1, 10, 18, 180, 1800, 3600 …?) | | ? |
| `Voltage_V_start`, `Voltage_V_end` | V | voltage at step start / end | | ? |
| `Voltage_V_at_<X>s` | V | voltage X s after the step start | | ? |
| `cc_current_A` | A | current of the CC phase | | ? |
| `cc_duration_s` | s | duration of the CC phase (CC→CV switch) | | ? |
| `cc_end_V` | V | voltage at the end of the CC phase | | ? |
| `phase_coverage` | 0–1 | share of the step that is CC ? | | ? |
| `delta_Capacity_step_Ah` | Ah | capacity charged (+) / discharged (−) in the step | | ? |
| `SOC_eval_start_%`, `SOC_eval_end_%` | % | SOC at step start / end (scale 0–100?) | | ? |
| `T_Cell_degC_start`, `T_Cell_degC_mean` | °C | cell temperature | | ? |
| `T_Chamber_degC_mean` | °C | chamber temperature | | ? |

## 3. Real header (paste here)

```
<paste the first line of a real <cell_id>_step_features.csv>
```
