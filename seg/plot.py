"""
plot.py
-------
One interactive HTML per cell, to check labels and detections by eye.

    plot_cell(steps, labeled_steps, detections, "FCA08J0011_segments_plot.html", title="FCA08J0011")

Row 1: current vs datetime, rebuilt from the step features only (no raw data). Support points per
       step: start, every Current_A_at_<X>s that lies inside the step, the CC→CV switch
       (cc_duration_s / cc_current_A) and the end. Each step is coloured by its pass-2 label.
       A thin grey line joins all points in time order as a guide to the eye; it breaks at Gap rows.
Row 2: detections ('(whole)' rows of <cell_id>_segments.csv), one band per level (chain, segment,
       pattern). Detections that overlap in time inside a level go to the next lane of that band,
       so the number of rows depends on overlaps, not on the number of names.
       Colour = name; a click on a name in the legend hides it.

Needs plotly (only imported when a plot is made). time_start_s is Unix seconds.
"""
from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pandas as pd

AT_PATTERN = re.compile(r"^Current_A_at_(\d+(?:\.\d+)?)s$")
LEVELS = ("chain", "segment", "pattern")
GUIDE_COLOUR = "#c8c8c8"
NO_LABEL_COLOUR = "#9aa0a6"
LABEL_COLOURS = ["#4269d0", "#efb118", "#ff725c", "#6cc5b0", "#3ca951", "#ff8ab7", "#a463f2",
                 "#97bbf5", "#9c6b4e", "#e45756", "#72b7b2", "#b279a2"]
NAME_COLOURS = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd", "#8c564b", "#e377c2",
                "#7f7f7f", "#bcbd22", "#17becf"]


def _num(steps: pd.DataFrame, column: str) -> np.ndarray:
    if column not in steps.columns:
        return np.full(len(steps), np.nan)
    return pd.to_numeric(steps[column], errors="coerce").to_numpy(float)


def _is_gap(steps: pd.DataFrame) -> np.ndarray:
    return (steps["mode"].astype(str) == "GAP").to_numpy() if "mode" in steps.columns else np.zeros(len(steps), bool)


def current_points(steps: pd.DataFrame) -> pd.DataFrame:
    """Support points of the rebuilt current, from the step features only.

    Parameters
    ----------
    steps : original step-features table (time_start_s, time_duration_s, Current_A_start,
            Current_A_at_<X>s, cc_duration_s, cc_current_A, Current_A_end; missing columns are skipped)

    Returns
    -------
    DataFrame pos (row of `steps`), t (Unix s), current [A], sorted by pos and time.
    Gap rows and points without a value give no point; an at_<X>s point only if X < step duration.

    Example
    -------
        points = current_points(steps)
        points.groupby("pos").size()          # points per step
    """
    n = len(steps)
    t0, duration = _num(steps, "time_start_s"), np.nan_to_num(_num(steps, "time_duration_s"))
    blocks = [(np.zeros(n), _num(steps, "Current_A_start"), np.ones(n, bool))]
    for column in steps.columns:
        if match := AT_PATTERN.match(column):
            x = float(match.group(1))
            blocks.append((np.full(n, x), _num(steps, column), x < duration))
    cc_duration = _num(steps, "cc_duration_s")
    with np.errstate(invalid="ignore"):
        blocks.append((cc_duration, _num(steps, "cc_current_A"), (cc_duration > 0) & (cc_duration < duration)))
    blocks.append((duration, _num(steps, "Current_A_end"), duration > 0))

    points = pd.DataFrame({"pos": np.tile(np.arange(n), len(blocks)),
                           "dt": np.concatenate([b[0] for b in blocks]),
                           "current": np.concatenate([b[1] for b in blocks]),
                           "keep": np.concatenate([b[2] for b in blocks])})
    pos = points["pos"].to_numpy()
    points = points[points["keep"].to_numpy() & points["current"].notna().to_numpy()
                    & points["dt"].notna().to_numpy() & ~_is_gap(steps)[pos] & np.isfinite(t0[pos])]
    points = points.sort_values(["pos", "dt"], kind="stable").drop_duplicates(["pos", "dt"], keep="last")
    points["t"] = t0[points["pos"].to_numpy()] + points["dt"].to_numpy()
    return points[["pos", "t", "current"]].reset_index(drop=True)


def _with_breaks(points: pd.DataFrame, break_after: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """x (datetime), y and pos arrays with an empty point after every True in break_after (line break)."""
    x = pd.to_datetime(points["t"], unit="s").to_numpy(object)
    y = points["current"].to_numpy(object)
    pos = points["pos"].to_numpy(object)
    where = np.flatnonzero(break_after) + 1
    return np.insert(x, where, None), np.insert(y, where, None), np.insert(pos, where, None)


def _lanes(starts: np.ndarray, ends: np.ndarray) -> np.ndarray:
    """Lane per bar: in start order, the first lane whose last bar ended before this one starts."""
    lane_end, lanes = [], np.zeros(len(starts), int)
    for i in np.argsort(starts, kind="stable"):
        lane = next((k for k, end in enumerate(lane_end) if end < starts[i]), len(lane_end))
        if lane == len(lane_end):
            lane_end.append(ends[i])
        else:
            lane_end[lane] = ends[i]
        lanes[i] = lane
    return lanes


def plot_cell(steps: pd.DataFrame, labeled_steps: pd.DataFrame, detections: pd.DataFrame,
              out_html: Path | str | None = None, title: str = "", default_label: str = "NO_LABEL"):
    """Current vs datetime coloured by label, detections below (see module docstring).

    Parameters
    ----------
    steps         : original step-features table; positions in `detections` refer to it
    labeled_steps : merged table from segment_cell (pass-2 'label', orig_pos_start, orig_pos_end);
                    a table without orig_pos_* must have one row per row of `steps`
    detections    : result of segment_cell / content of <cell_id>_segments.csv ('(whole)' rows are drawn)
    out_html      : write the figure to this HTML file (plotly.js loaded from the internet); None = do not write
    title         : figure title, e.g. the cell ID
    default_label : label drawn in grey (settings.default_label)

    Returns
    -------
    plotly Figure (fig.show() opens it in the browser)

    Example
    -------
        labeled_steps, detections = segment_cell(steps, definition, "FCA08J0011")
        plot_cell(steps, labeled_steps, detections, "FCA08J0011_segments_plot.html", title="FCA08J0011")
    """
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    steps = steps.reset_index(drop=True)
    # pass-2 label of every original step; merged parts and bridges take the label of their merged row
    if "orig_pos_start" in labeled_steps.columns:
        label = np.full(len(steps), default_label, dtype=object)
        for lab, first, last in zip(labeled_steps["label"], labeled_steps["orig_pos_start"],
                                    labeled_steps["orig_pos_end"]):
            label[int(first):int(last) + 1] = lab
    else:
        label = labeled_steps["label"].to_numpy(object)

    points = current_points(steps)
    t_start = _num(steps, "time_start_s")
    t_end = t_start + np.nan_to_num(_num(steps, "time_duration_s"))
    step_ids = steps["step_id"].to_numpy() if "step_id" in steps.columns else np.arange(len(steps))

    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.65, 0.35], vertical_spacing=0.05)

    # guide to the eye: all points in time order, broken where a Gap row lies between two points
    gaps_before = np.cumsum(_is_gap(steps))                     # Gap rows up to and including each position
    pos = points["pos"].to_numpy()
    next_pos = np.append(pos[1:], pos[-1] if len(pos) else 0)
    gap_between = gaps_before[np.maximum(next_pos - 1, 0)] > gaps_before[pos] if len(pos) else np.zeros(0, bool)
    x, y, _ = _with_breaks(points, gap_between)
    fig.add_trace(go.Scattergl(x=x, y=y, mode="lines", line=dict(color=GUIDE_COLOUR, width=1),
                               hoverinfo="skip", showlegend=False), row=1, col=1)

    # one trace per label, broken between steps
    order = [l for l in dict.fromkeys(label) if l != default_label] + [default_label]
    for i, lab in enumerate(order):
        p = points[label[points["pos"].to_numpy()] == lab]
        if p.empty:
            continue
        x, y, p_pos = _with_breaks(p, (p["pos"].shift(-1) != p["pos"]).to_numpy())
        hover_ids = [step_ids[q] if q is not None else None for q in p_pos]
        colour = NO_LABEL_COLOUR if lab == default_label else LABEL_COLOURS[i % len(LABEL_COLOURS)]
        fig.add_trace(go.Scattergl(x=x, y=y, customdata=hover_ids, mode="lines", name=lab,
                                   line=dict(color=colour, width=2),
                                   legendgroup="labels", legendgrouptitle_text="Labels",
                                   hovertemplate=f"{lab}<br>step %{{customdata}}<br>%{{x}}<br>"
                                                 f"%{{y:.2f}} A<extra></extra>"), row=1, col=1)

    # detections: one band per level, a new lane only where detections of that level overlap
    whole = detections[detections["part"] == "(whole)"].copy()
    whole["t0"] = t_start[whole["step_pos_start"].astype(int).to_numpy()]
    whole["t1"] = t_end[whole["step_pos_end"].astype(int).to_numpy()]
    tick_values, tick_text, y_next, name_colour = [], [], 0.0, {}
    for level in LEVELS:
        level_rows = whole[whole["level"] == level]
        if level_rows.empty:
            continue
        lanes = _lanes(level_rows["t0"].to_numpy(), level_rows["t1"].to_numpy())
        level_rows = level_rows.assign(y=y_next + lanes)
        tick_values.append(y_next + lanes.max() / 2)
        tick_text.append(level)
        for name, g in level_rows.groupby("name", sort=False):
            colour = name_colour.setdefault(name, NAME_COLOURS[len(name_colour) % len(NAME_COLOURS)])
            variants = g["variant"].fillna("").astype(str)
            text = [f"{name}{' ' + v if v and v != name else ''} #{i}" for v, i in zip(variants, g["instance"])]
            hover = [f"{level} {name} {v} #{i}<br>steps {a:g}–{b:g}<br>{n} step rows"
                     for v, i, a, b, n in zip(variants, g["instance"], g["step_id_start"], g["step_id_end"],
                                              g["n_steps"])]
            fig.add_trace(go.Bar(orientation="h", y=g["y"], base=pd.to_datetime(g["t0"], unit="s"),
                                 x=(g["t1"] - g["t0"]).to_numpy() * 1000, width=0.8,
                                 marker=dict(color=colour, line=dict(color="white", width=1)),
                                 text=text, textposition="inside", insidetextanchor="middle",
                                 textfont=dict(size=10), hovertext=hover, hoverinfo="text", name=name,
                                 legendgroup=level, legendgrouptitle_text=level.capitalize() + "s"),
                          row=2, col=1)
        y_next += lanes.max() + 1.6                                 # small space between the bands

    fig.update_yaxes(title_text="Current [A]", row=1, col=1)
    fig.update_yaxes(tickvals=tick_values, ticktext=tick_text, autorange="reversed", showgrid=False,
                     zeroline=False, row=2, col=1)
    fig.update_xaxes(type="date", row=2, col=1)
    fig.update_layout(title=title, template="plotly_white", height=760, barmode="overlay",
                      hovermode="closest", legend=dict(groupclick="toggleitem"),
                      margin=dict(l=70, r=20, t=60, b=40))
    if out_html is not None:
        fig.write_html(out_html, include_plotlyjs="cdn")
    return fig
