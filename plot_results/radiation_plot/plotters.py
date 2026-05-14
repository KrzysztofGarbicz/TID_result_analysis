"""Plot renderers.

One public function per plot type:

* :func:`plot_absolute` - raw value vs dose, one series per stage.
* :func:`plot_delta`    - per-SN change between two stages, vs dose.
* :func:`plot_annealing` - main subplot: trend across stages for each
  dose group; optional right subplot for reference samples.

All three return a dict describing what was rendered (count of points,
warnings, output path) so that ``plot_radiation.py`` can write a
``_plot_index.csv``.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from . import data_loader as dl


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _apply_axis_limits(ax: plt.Axes, plot_spec: dict[str, Any]) -> None:
    """Respect optional ``x_lim`` / ``y_lim`` from the config."""
    x_lim = plot_spec.get("x_lim")
    y_lim = plot_spec.get("y_lim")
    if x_lim and len(x_lim) == 2:
        ax.set_xlim(x_lim[0], x_lim[1])
    if y_lim and len(y_lim) == 2:
        ax.set_ylim(y_lim[0], y_lim[1])


def _apply_axis_scales(ax: plt.Axes, plot_spec: dict[str, Any]) -> None:
    """Apply optional ``x_scale`` / ``y_scale`` (``"linear"`` / ``"log"``).

    ``log`` on an axis that contains non-positive values would normally
    raise; we mask with ``nonpositive="clip"`` so matplotlib silently
    drops them rather than aborting the whole plot.
    """
    x_scale = plot_spec.get("x_scale")
    y_scale = plot_spec.get("y_scale")
    if x_scale:
        if x_scale == "log":
            ax.set_xscale("log", nonpositive="clip")
        else:
            ax.set_xscale(x_scale)
    if y_scale:
        if y_scale == "log":
            ax.set_yscale("log", nonpositive="clip")
        else:
            ax.set_yscale(y_scale)


def _style_axes(
    ax: plt.Axes,
    plot_spec: dict[str, Any],
    *,
    default_x_label: str = "Dose [kRad]",
    default_y_label: str = "Value",
) -> None:
    """Apply title, axis labels, grid, legend visibility."""
    if plot_spec.get("title"):
        ax.set_title(plot_spec["title"])
    ax.set_xlabel(plot_spec.get("x_label", default_x_label))
    ax.set_ylabel(plot_spec.get("y_label", default_y_label))
    if plot_spec.get("grid", True):
        ax.grid(True, linestyle="--", alpha=0.4)
    if plot_spec.get("legend", True) and ax.get_legend_handles_labels()[0]:
        ax.legend(loc="best", fontsize=9, framealpha=0.9)


def _stat_line_styles(stat: str) -> dict[str, Any]:
    """Visual style per statistic line kind. Keeps legends readable."""
    return {
        "mean":   {"linestyle": "-",  "linewidth": 1.8},
        "median": {"linestyle": "-.", "linewidth": 1.6},
        "min":    {"linestyle": ":",  "linewidth": 1.4},
        "max":    {"linestyle": ":",  "linewidth": 1.4},
    }[stat]


def _save_figure(
    fig: plt.Figure,
    plot_spec: dict[str, Any],
    output_dir: Path,
) -> Path:
    """Save ``fig`` to ``output_dir/<output_name>.<format>`` and close it."""
    fmt = plot_spec.get("format", "png")
    name = plot_spec["output_name"]
    out_path = output_dir / f"{name}.{fmt}"
    output_dir.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(out_path, dpi=plot_spec.get("dpi", 150))
    plt.close(fig)
    return out_path


# ---------------------------------------------------------------------------
# Plot type 1: ABSOLUTE
# ---------------------------------------------------------------------------


def plot_absolute(
    master_df: pd.DataFrame,
    plot_spec: dict[str, Any],
    *,
    output_dir: Path,
    reference_sns: list[str],
) -> dict[str, Any]:
    """Raw measurement value vs dose, one coloured series per stage."""

    stages: list[str] = plot_spec["stages"]
    stage_styles = plot_spec.get("stage_styles", {})
    stage_labels = plot_spec.get("stage_labels", {})

    df = dl.filter_for_plot(
        master_df,
        lcl_name=plot_spec["lcl_name"],
        measurement_type=plot_spec["measurement_type"],
        metric=plot_spec["metric"],
        context_key=plot_spec.get("context_key"),
        stages=stages,
        exclude_sn=plot_spec.get("exclude_sn"),
        include_doses=plot_spec.get("include_doses"),
        exclude_doses=plot_spec.get("exclude_doses"),
        exclude_reference=reference_sns,  # absolute plots exclude refs
        lot=plot_spec.get("lot"),
        bias=plot_spec.get("bias"),
    )

    if df.empty:
        logger.warning(
            "Plot '%s': no data after filtering - skipped.",
            plot_spec["output_name"],
        )
        return {
            "output_name": plot_spec["output_name"],
            "output_path": None,
            "n_points": 0,
            "n_series": 0,
            "skipped": True,
            "reason": "no data after filtering",
        }

    figsize = plot_spec.get("figsize", [10, 6])
    fig, ax = plt.subplots(figsize=figsize)

    show_points = plot_spec.get("show_points", True)
    show_lines: list[str] = plot_spec.get("show_lines", [])

    total_points = 0
    n_series_drawn = 0

    for stage in stages:
        stage_df = df[df["irradiation_stage"] == stage]
        if stage_df.empty:
            continue

        style = stage_styles.get(stage, {})
        color = style.get("color")
        marker = style.get("marker", "o")
        label_base = stage_labels.get(stage, stage)

        if show_points:
            ax.scatter(
                stage_df["dose_krad"],
                stage_df["value_num"],
                s=plot_spec.get("marker_size", 40),
                alpha=plot_spec.get("alpha_points", 0.65),
                color=color,
                marker=marker,
                label=f"{label_base} (points)" if not show_lines else label_base,
                edgecolors="black",
                linewidths=0.4,
                zorder=2,
            )
            total_points += len(stage_df)

        if show_lines:
            stats = dl.compute_stats_by_dose(stage_df)
            if not stats.empty:
                for stat in show_lines:
                    if stat not in stats.columns:
                        continue
                    style_kwargs = _stat_line_styles(stat)
                    ax.plot(
                        stats["dose_krad"],
                        stats[stat],
                        color=color,
                        marker="",
                        label=f"{label_base} {stat}",
                        zorder=3,
                        **style_kwargs,
                    )
            n_series_drawn += 1

        if not show_lines:
            n_series_drawn += 1

    _style_axes(
        ax,
        plot_spec,
        default_y_label=plot_spec.get("y_label", "Value"),
    )
    _apply_axis_scales(ax, plot_spec)
    _apply_axis_limits(ax, plot_spec)

    out_path = _save_figure(fig, plot_spec, output_dir)
    logger.info(
        "Saved '%s' (%d points across %d stages)",
        out_path.name,
        total_points,
        n_series_drawn,
    )
    return {
        "output_name": plot_spec["output_name"],
        "output_path": str(out_path),
        "n_points": total_points,
        "n_series": n_series_drawn,
        "skipped": False,
        "reason": "",
    }


# ---------------------------------------------------------------------------
# Plot type 2: DELTA
# ---------------------------------------------------------------------------


def plot_delta(
    master_df: pd.DataFrame,
    plot_spec: dict[str, Any],
    *,
    output_dir: Path,
    reference_sns: list[str],
) -> dict[str, Any]:
    """Per-SN delta between two stages, plotted against dose."""

    delta_from = plot_spec["delta_from"]
    delta_to = plot_spec["delta_to"]
    # Delta plots are always rendered as relative percent change, by
    # project convention. Any `delta_mode` in the config is ignored.
    mode = "relative_percent"

    df = dl.filter_for_plot(
        master_df,
        lcl_name=plot_spec["lcl_name"],
        measurement_type=plot_spec["measurement_type"],
        metric=plot_spec["metric"],
        context_key=plot_spec.get("context_key"),
        stages=[delta_from, delta_to],
        exclude_sn=plot_spec.get("exclude_sn"),
        include_doses=plot_spec.get("include_doses"),
        exclude_doses=plot_spec.get("exclude_doses"),
        exclude_reference=reference_sns,
        lot=plot_spec.get("lot"),
        bias=plot_spec.get("bias"),
    )

    deltas = dl.compute_deltas(df, delta_from=delta_from, delta_to=delta_to, mode=mode)

    if deltas.empty:
        logger.warning(
            "Plot '%s': no SN has both '%s' and '%s' measurements - skipped.",
            plot_spec["output_name"],
            delta_from,
            delta_to,
        )
        return {
            "output_name": plot_spec["output_name"],
            "output_path": None,
            "n_points": 0,
            "n_series": 0,
            "skipped": True,
            "reason": f"no SN with both {delta_from} and {delta_to}",
        }

    figsize = plot_spec.get("figsize", [10, 6])
    fig, ax = plt.subplots(figsize=figsize)

    show_points = plot_spec.get("show_points", True)
    show_lines: list[str] = plot_spec.get("show_lines", [])

    color = "#444444"
    if show_points:
        ax.scatter(
            deltas["dose_krad"],
            deltas["delta"],
            s=plot_spec.get("marker_size", 40),
            alpha=plot_spec.get("alpha_points", 0.65),
            color="#1f77b4",
            label="Per-SN delta",
            edgecolors="black",
            linewidths=0.4,
            zorder=2,
        )

    if show_lines:
        # Build a tiny frame compatible with compute_stats_by_dose
        renamed = deltas.rename(columns={"delta": "value_num"})
        stats = dl.compute_stats_by_dose(renamed)
        if not stats.empty:
            for stat in show_lines:
                if stat not in stats.columns:
                    continue
                style_kwargs = _stat_line_styles(stat)
                ax.plot(
                    stats["dose_krad"],
                    stats[stat],
                    color=color,
                    marker="",
                    label=stat,
                    zorder=3,
                    **style_kwargs,
                )

    # Reference line at zero - very useful for delta plots.
    ax.axhline(0, color="black", linewidth=0.8, alpha=0.6, zorder=1)

    # Always-percent delta plots get a "[%]" axis label by default.
    default_y = "delta value [%]"
    if not plot_spec.get("y_label"):
        plot_spec = {**plot_spec, "y_label": default_y}

    _style_axes(ax, plot_spec, default_y_label=default_y)
    _apply_axis_scales(ax, plot_spec)
    _apply_axis_limits(ax, plot_spec)

    out_path = _save_figure(fig, plot_spec, output_dir)
    logger.info(
        "Saved '%s' (%d deltas)",
        out_path.name,
        len(deltas),
    )
    return {
        "output_name": plot_spec["output_name"],
        "output_path": str(out_path),
        "n_points": int(len(deltas)),
        "n_series": 1,
        "skipped": False,
        "reason": "",
    }


# ---------------------------------------------------------------------------
# Plot type 3: ANNEALING
# ---------------------------------------------------------------------------


def _dose_colour(dose: float, all_doses: list[float]) -> str:
    """Pick a colour from a sequential colormap based on dose rank.

    Higher dose -> darker / more saturated. Plays nicely with the
    "before -> after -> annealing" trend lines.
    """
    if not all_doses:
        return "#444444"
    sorted_doses = sorted(set(all_doses))
    if len(sorted_doses) == 1:
        return "#1f77b4"
    idx = sorted_doses.index(dose)
    # Use viridis - perceptually uniform, colour-blind friendly.
    cmap = plt.get_cmap("viridis")
    return cmap(0.15 + 0.75 * idx / (len(sorted_doses) - 1))


def _plot_annealing_on_axes(
    ax: plt.Axes,
    df: pd.DataFrame,
    stages_order: list[str],
    stage_labels: dict[str, str],
    *,
    show_points: bool,
    show_lines: list[str],
    marker_size: int,
    alpha_points: float,
    group_by: str,  # "dose" or "sn"
    all_doses_for_color: list[float],
) -> int:
    """Render an annealing trend on the given axes.

    Returns the number of points drawn (for stats / index).
    """
    stage_to_x = {s: i for i, s in enumerate(stages_order)}
    ax.set_xticks(list(stage_to_x.values()))
    ax.set_xticklabels(
        [stage_labels.get(s, s) for s in stages_order],
        rotation=20,
        ha="right",
    )

    df = df[df["irradiation_stage"].isin(stages_order)].copy()
    df["x_pos"] = df["irradiation_stage"].map(stage_to_x)

    if df.empty:
        return 0

    n_points_drawn = 0

    if group_by == "dose":
        # One line per dose, mean over SNs at each stage; raw points scattered
        # around the integer x position for visibility.
        for dose, sub in df.groupby("dose_krad", sort=True):
            color = _dose_colour(dose, all_doses_for_color)
            label = f"{dose:g} kRad"

            # Aggregate per stage: mean for the line
            agg = (
                sub.groupby("x_pos", sort=True)["value_num"]
                .agg(["mean", "min", "max", "median"])
                .reset_index()
            )

            if show_lines:
                # The "main" trend - mean unless the user said otherwise.
                primary = "mean" if "mean" in show_lines else show_lines[0]
                if primary in agg.columns:
                    ax.plot(
                        agg["x_pos"],
                        agg[primary],
                        color=color,
                        marker="o",
                        linewidth=1.8,
                        label=label,
                        zorder=3,
                    )
                # Optionally add min/max envelope as dotted lines without
                # adding extra legend entries.
                for stat in show_lines:
                    if stat in ("mean", primary):
                        continue
                    if stat not in agg.columns:
                        continue
                    style_kwargs = _stat_line_styles(stat)
                    ax.plot(
                        agg["x_pos"],
                        agg[stat],
                        color=color,
                        alpha=0.5,
                        zorder=2,
                        **style_kwargs,
                    )

            if show_points:
                # Small horizontal jitter so overlapping points are visible.
                rng = np.random.default_rng(int(abs(dose) * 1000) or 1)
                jitter = rng.uniform(-0.12, 0.12, size=len(sub))
                ax.scatter(
                    sub["x_pos"] + jitter,
                    sub["value_num"],
                    s=marker_size,
                    color=color,
                    alpha=alpha_points,
                    edgecolors="black",
                    linewidths=0.3,
                    label=None if show_lines else label,
                    zorder=2,
                )
                n_points_drawn += len(sub)

            if not show_points:
                n_points_drawn += len(sub)
    else:
        # group_by == "sn" - dense fallback, currently not exposed in YAML.
        for sn, sub in df.groupby("lcl_serial_number"):
            dose = sub["dose_krad"].iloc[0]
            color = _dose_colour(dose, all_doses_for_color)
            sub = sub.sort_values("x_pos")
            ax.plot(
                sub["x_pos"],
                sub["value_num"],
                color=color,
                marker="o",
                alpha=0.8,
                linewidth=1.2,
                label=f"{sn} ({dose:g} kRad)",
            )
            n_points_drawn += len(sub)

    # A faint vertical line between "after" and the first annealing helps
    # the eye separate "damage" from "recovery".
    after_idx = None
    for s in ("after_irradiate",):
        if s in stage_to_x:
            after_idx = stage_to_x[s]
            break
    if after_idx is not None and after_idx + 1 < len(stages_order):
        ax.axvline(after_idx + 0.5, color="black", linewidth=0.6, alpha=0.3, linestyle="--")

    return n_points_drawn


def plot_annealing(
    master_df: pd.DataFrame,
    plot_spec: dict[str, Any],
    *,
    output_dir: Path,
    reference_sns: list[str],
) -> dict[str, Any]:
    """Recovery across annealing stages.

    Main subplot: irradiated samples, one line per dose, X axis = stage.
    Right subplot (created only if at least one reference SN has data):
    same metric for the reference samples, shown in grey.
    """

    stages_order: list[str] = plot_spec["stages_order"]
    stage_labels = plot_spec.get("stage_labels", {})
    group_by = plot_spec.get("group_by", "dose")

    # Irradiated samples (no references). Use exclude_reference to drop them.
    irr_df = dl.filter_for_plot(
        master_df,
        lcl_name=plot_spec["lcl_name"],
        measurement_type=plot_spec["measurement_type"],
        metric=plot_spec["metric"],
        context_key=plot_spec.get("context_key"),
        stages=stages_order,
        exclude_sn=plot_spec.get("exclude_sn"),
        include_doses=plot_spec.get("include_doses"),
        exclude_doses=plot_spec.get("exclude_doses"),
        exclude_reference=reference_sns,
        lot=plot_spec.get("lot"),
        bias=plot_spec.get("bias"),
    )

    # Reference samples (controls) - separate subplot if any data.
    # Lot / bias filters apply here too so e.g. the LOT-A panel only
    # shows LOT-A reference samples.
    ref_df = master_df[
        (master_df["lcl_name"] == plot_spec["lcl_name"])
        & (master_df["measurement_type"] == plot_spec["measurement_type"])
        & (master_df["metric"] == plot_spec["metric"])
        & (master_df["lcl_serial_number"].isin(reference_sns))
        & (master_df["irradiation_stage"].isin(stages_order))
    ].copy()
    if plot_spec.get("context_key") is not None:
        ref_df = ref_df[ref_df["context_key"] == plot_spec["context_key"]]
    if plot_spec.get("lot") is not None and "lot" in ref_df.columns:
        ref_df = ref_df[ref_df["lot"].astype(str) == str(plot_spec["lot"])]
    if plot_spec.get("bias") is not None and "bias" in ref_df.columns:
        ref_df = ref_df[ref_df["bias"].astype(str) == str(plot_spec["bias"])]

    has_ref_data = not ref_df.empty

    if irr_df.empty and not has_ref_data:
        logger.warning(
            "Plot '%s': no data (irradiated or reference) - skipped.",
            plot_spec["output_name"],
        )
        return {
            "output_name": plot_spec["output_name"],
            "output_path": None,
            "n_points": 0,
            "n_series": 0,
            "skipped": True,
            "reason": "no data after filtering",
        }

    figsize = plot_spec.get("figsize", [12, 6])

    if has_ref_data:
        # Side-by-side - reference panel narrower (typically 1-2 SNs).
        fig, (ax_main, ax_ref) = plt.subplots(
            1, 2, figsize=figsize, gridspec_kw={"width_ratios": [4, 1]}, sharey=True
        )
    else:
        fig, ax_main = plt.subplots(figsize=figsize)
        ax_ref = None

    all_doses = sorted(irr_df["dose_krad"].dropna().unique().tolist())

    n_points = _plot_annealing_on_axes(
        ax_main,
        irr_df,
        stages_order=stages_order,
        stage_labels=stage_labels,
        show_points=plot_spec.get("show_points", True),
        show_lines=plot_spec.get("show_lines", ["mean"]),
        marker_size=plot_spec.get("marker_size", 40),
        alpha_points=plot_spec.get("alpha_points", 0.65),
        group_by=group_by,
        all_doses_for_color=all_doses,
    )

    _style_axes(
        ax_main,
        plot_spec,
        default_x_label="Stage",
        default_y_label=plot_spec.get("y_label", "Value"),
    )
    # Annealing uses categorical positions on the X axis (0..N-1) so an X
    # log scale doesn't make sense; respect Y log only.
    if plot_spec.get("y_scale"):
        _apply_axis_scales(ax_main, {"y_scale": plot_spec["y_scale"]})
    _apply_axis_limits(ax_main, plot_spec)

    n_ref_points = 0
    if ax_ref is not None:
        stage_to_x = {s: i for i, s in enumerate(stages_order)}
        ax_ref.set_xticks(list(stage_to_x.values()))
        ax_ref.set_xticklabels(
            [stage_labels.get(s, s) for s in stages_order],
            rotation=20,
            ha="right",
        )
        for sn, sub in ref_df.groupby("lcl_serial_number"):
            sub = sub.assign(x_pos=sub["irradiation_stage"].map(stage_to_x))
            sub = sub.dropna(subset=["x_pos"]).sort_values("x_pos")
            ax_ref.plot(
                sub["x_pos"],
                sub["value_num"],
                marker="o",
                linewidth=1.4,
                label=f"{sn} (ref)",
                color="#7f7f7f",
            )
            n_ref_points += len(sub)
        if plot_spec.get("y_scale"):
            _apply_axis_scales(ax_ref, {"y_scale": plot_spec["y_scale"]})
        ax_ref.set_title("Reference")
        ax_ref.grid(True, linestyle="--", alpha=0.4)
        if ax_ref.get_legend_handles_labels()[0]:
            ax_ref.legend(loc="best", fontsize=8, framealpha=0.9)

    out_path = _save_figure(fig, plot_spec, output_dir)
    logger.info(
        "Saved '%s' (%d irradiated points + %d reference points)",
        out_path.name,
        n_points,
        n_ref_points,
    )

    return {
        "output_name": plot_spec["output_name"],
        "output_path": str(out_path),
        "n_points": n_points + n_ref_points,
        "n_series": len(all_doses) + (1 if has_ref_data else 0),
        "skipped": False,
        "reason": "",
    }


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


def render_plot(
    master_df: pd.DataFrame,
    plot_spec: dict[str, Any],
    *,
    output_dir: Path,
    reference_sns: list[str],
) -> dict[str, Any]:
    """Dispatch to the right plotter based on ``plot_spec['type']``.

    Wraps each call in a try/except so a single failing plot does not
    abort a long batch. Returns a result dict either way.
    """
    ptype = plot_spec["type"]
    try:
        if ptype == "absolute":
            return plot_absolute(
                master_df, plot_spec, output_dir=output_dir, reference_sns=reference_sns
            )
        if ptype == "delta":
            return plot_delta(
                master_df, plot_spec, output_dir=output_dir, reference_sns=reference_sns
            )
        if ptype == "annealing":
            return plot_annealing(
                master_df, plot_spec, output_dir=output_dir, reference_sns=reference_sns
            )
        raise ValueError(f"Unsupported plot type: {ptype!r}")
    except Exception as exc:  # noqa: BLE001 - we want to keep going
        logger.exception(
            "Plot '%s' failed with %s: %s",
            plot_spec.get("output_name", "<unknown>"),
            type(exc).__name__,
            exc,
        )
        return {
            "output_name": plot_spec.get("output_name", "<unknown>"),
            "output_path": None,
            "n_points": 0,
            "n_series": 0,
            "skipped": True,
            "reason": f"{type(exc).__name__}: {exc}",
        }
