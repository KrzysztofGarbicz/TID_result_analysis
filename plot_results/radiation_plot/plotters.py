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

from . import config as _cfg
from . import data_loader as dl


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# series_by helpers
# ---------------------------------------------------------------------------


def _normalize_series_by(spec: dict[str, Any]) -> list[str]:
    """Return ``series_by`` as a list (accepts string or list in YAML)."""
    sb = spec.get("series_by")
    if not sb:
        return []
    if isinstance(sb, str):
        return [sb]
    return list(sb)


def _series_combos(
    df: pd.DataFrame,
    series_by: list[str],
) -> list[dict[str, str]]:
    """Build the cartesian product of values present in ``df`` for each
    dimension in ``series_by``. Returns a list of dicts mapping dim -> value.

    Returns ``[{}]`` (one empty combo) when ``series_by`` is empty so callers
    can iterate uniformly regardless of whether series splitting is active.
    """
    if not series_by:
        return [{}]
    combos: list[dict[str, str]] = [{}]
    for dim in series_by:
        if dim not in df.columns:
            continue
        values = sorted(v for v in df[dim].dropna().unique() if str(v) != "")
        if not values:
            continue
        combos = [{**c, dim: v} for c in combos for v in values]
    return combos or [{}]


def _filter_to_combo(df: pd.DataFrame, combo: dict[str, str]) -> pd.DataFrame:
    """Return rows of ``df`` matching every key=value pair in ``combo``."""
    if not combo:
        return df
    mask = pd.Series(True, index=df.index)
    for dim, value in combo.items():
        if dim in df.columns:
            mask &= df[dim].astype(str) == str(value)
    return df[mask]


def _combo_label(combo: dict[str, str]) -> str:
    """Short human-readable label for a series_by combo (e.g. ``"LOT A"``)."""
    parts: list[str] = []
    for dim, value in combo.items():
        if dim == "lot":
            parts.append(f"LOT {value}")
        elif dim == "bias":
            parts.append(str(value))
        else:
            parts.append(f"{dim}={value}")
    return " / ".join(parts)


def _series_color(
    metric_color: Any,
    combo_index: int,
    n_combos: int,
) -> Any:
    """Pick a colour for one series within a (metric, combo) family.

    When ``series_by`` is unused (n_combos == 1), the metric colour is
    returned unchanged. With multiple combos, we shift the colour around
    the tab10 palette so each combo for the same metric stays visually
    related but distinct.
    """
    if n_combos <= 1:
        return metric_color
    cmap = plt.get_cmap("tab10")
    return cmap(combo_index % 10)


# ---------------------------------------------------------------------------
# Subplot helpers
# ---------------------------------------------------------------------------


def _resolve_subplot_specs(plot_spec: dict[str, Any]) -> list[dict[str, Any]]:
    """Build per-subplot merged specs.

    Each subplot inherits every key from the parent spec *except* ``title``
    (the parent's title becomes the figure suptitle) and ``subplots``
    itself. Subplot-specific overrides win on conflict.
    """
    subplots = plot_spec.get("subplots") or []
    if not subplots:
        return []
    parent_inherited = {
        k: v for k, v in plot_spec.items() if k not in ("subplots", "title")
    }
    resolved: list[dict[str, Any]] = []
    for sub in subplots:
        if not isinstance(sub, dict):
            raise ValueError(
                f"plot '{plot_spec.get('name')}': each subplot must be a "
                f"mapping, got {type(sub).__name__}"
            )
        resolved.append(_cfg._deep_merge(parent_inherited, sub))
    return resolved


def _subplot_grid(n_panels: int, layout: str) -> tuple[int, int]:
    """Return ``(nrows, ncols)`` for a subplot grid.

    ``layout`` is ``"rows"`` (stacked vertically, default), ``"cols"``
    (side-by-side), or ``"grid"`` (a roughly square grid).
    """
    import math

    if n_panels <= 0:
        return (1, 1)
    if layout == "cols":
        return (1, n_panels)
    if layout == "grid":
        cols = math.ceil(math.sqrt(n_panels))
        rows = math.ceil(n_panels / cols)
        return (rows, cols)
    return (n_panels, 1)


def _default_figsize_for_subplots(
    plot_spec: dict[str, Any],
    n_panels: int,
    layout: str,
) -> list[float]:
    """Pick a sensible default figsize when the user didn't set one."""
    if "figsize" in plot_spec:
        return list(plot_spec["figsize"])
    if layout == "cols":
        return [max(6 * n_panels, 8), 6]
    if layout == "grid":
        nrows, ncols = _subplot_grid(n_panels, "grid")
        return [max(5 * ncols, 8), max(4 * nrows, 5)]
    return [10, max(4 * n_panels, 5)]


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


def _apply_dose_ticks(ax: plt.Axes) -> None:
    """Set standard dose ticks for log scale (1, 2, 5, 10, 15, 25, 40 kRad)."""
    standard_ticks = [1, 2, 5, 10, 15, 25, 40]
    ax.set_xticks(standard_ticks)
    ax.set_xticklabels([str(t) for t in standard_ticks])


def _draw_reference_lines(ax: plt.Axes, plot_spec: dict[str, Any]) -> None:
    """Draw reference / limit lines defined in ``reference_lines:``.

    Each entry is a mapping with exactly one of ``y`` (horizontal) or
    ``x`` (vertical), plus optional ``label`` (legend entry), ``color``
    (default ``red``), ``linestyle`` (default ``"--"``), ``linewidth``
    (default 1.2) and ``alpha`` (default 0.8). Use it for spec limits
    such as the ±8 % I_lim accuracy window.

    Lines are drawn behind data (``zorder=1.5``) so they read as a
    background reference rather than competing with the points.
    """
    lines = plot_spec.get("reference_lines") or []
    for entry in lines:
        if not isinstance(entry, dict):
            continue
        kwargs: dict[str, Any] = {
            "color": entry.get("color", "red"),
            "linestyle": entry.get("linestyle", "--"),
            "linewidth": entry.get("linewidth", 1.2),
            "alpha": entry.get("alpha", 0.8),
            "zorder": 1.5,
        }
        label = entry.get("label")
        if label:
            kwargs["label"] = label
        if "y" in entry:
            ax.axhline(float(entry["y"]), **kwargs)
        elif "x" in entry:
            ax.axvline(float(entry["x"]), **kwargs)


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
            _apply_dose_ticks(ax)
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


def _get_metric_colors(metrics: list[str]) -> dict[str, str]:
    """Assign distinct colors to each metric for multi-metric plots.

    Uses tab10 colormap for up to 10 metrics; for more, cycles through.
    """
    if len(metrics) == 1:
        return {metrics[0]: "#1f77b4"}  # Default matplotlib blue

    cmap = plt.get_cmap("tab10")
    colors = {}
    for i, metric_name in enumerate(metrics):
        color_idx = i % 10
        colors[metric_name] = cmap(color_idx)
    return colors


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


def _draw_absolute_on_ax(
    ax: plt.Axes,
    master_df: pd.DataFrame,
    plot_spec: dict[str, Any],
    reference_sns: list[str],
) -> tuple[int, int, bool]:
    """Render absolute plot content onto a single Axes.

    Returns ``(n_points, n_series, has_data)``. Does NOT touch the figure
    (no save, no suptitle, no note) - the caller is responsible for that.
    """
    stages: list[str] = plot_spec["stages"]
    stage_styles = plot_spec.get("stage_styles", {})
    stage_labels = plot_spec.get("stage_labels", {})

    metric_param = plot_spec["metric"]
    metrics_to_plot = metric_param if isinstance(metric_param, list) else [metric_param]

    df = dl.filter_for_plot(
        master_df,
        lcl_name=plot_spec["lcl_name"],
        measurement_type=plot_spec["measurement_type"],
        metric=metric_param,
        context_key=plot_spec.get("context_key"),
        stages=stages,
        exclude_sn=plot_spec.get("exclude_sn"),
        include_doses=plot_spec.get("include_doses"),
        exclude_doses=plot_spec.get("exclude_doses"),
        exclude_reference=reference_sns,
        lot=plot_spec.get("lot"),
        bias=plot_spec.get("bias"),
    )

    if df.empty:
        return 0, 0, False

    show_points = plot_spec.get("show_points", True)
    show_lines: list[str] = plot_spec.get("show_lines", [])
    series_by = _normalize_series_by(plot_spec)

    metric_colors = _get_metric_colors(metrics_to_plot)
    combos = _series_combos(df, series_by)

    total_points = 0
    n_series_drawn = 0

    for metric_name in metrics_to_plot:
        metric_df = df[df["metric"] == metric_name]
        if metric_df.empty:
            continue

        base_metric_color = metric_colors[metric_name]
        metric_label = metric_name if len(metrics_to_plot) > 1 else None

        for combo_idx, combo in enumerate(combos):
            combo_df = _filter_to_combo(metric_df, combo)
            if combo_df.empty:
                continue

            series_color = _series_color(
                base_metric_color, combo_idx, len(combos)
            )
            combo_lbl = _combo_label(combo)

            for stage in stages:
                stage_df = combo_df[combo_df["irradiation_stage"] == stage]
                if stage_df.empty:
                    continue

                style = stage_styles.get(stage, {})
                marker = style.get("marker", "o")
                label_base = stage_labels.get(stage, stage)

                label_parts = [metric_label, label_base, combo_lbl]
                full_label = " - ".join([p for p in label_parts if p])

                if show_points:
                    ax.scatter(
                        stage_df["dose_krad"],
                        stage_df["value_num"],
                        s=plot_spec.get("marker_size", 40),
                        alpha=plot_spec.get("alpha_points", 0.65),
                        color=series_color,
                        marker=marker,
                        label=(
                            f"{full_label} (points)" if not show_lines else full_label
                        ),
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
                                color=series_color,
                                marker="",
                                label=f"{full_label} {stat}",
                                zorder=3,
                                **style_kwargs,
                            )
                    n_series_drawn += 1

                if not show_lines:
                    n_series_drawn += 1

    show_baseline = plot_spec.get(
        "show_before_at_zero", "before_irradiate" in stages
    )
    if show_baseline and "before_irradiate" in stages:
        baseline_style = stage_styles.get("before_irradiate", {})
        baseline_marker = baseline_style.get("marker", "o")
        for metric_name in metrics_to_plot:
            metric_df = df[
                (df["metric"] == metric_name)
                & (df["irradiation_stage"] == "before_irradiate")
            ]
            if metric_df.empty:
                continue
            base_metric_color = metric_colors[metric_name]
            for combo_idx, combo in enumerate(combos):
                combo_df = _filter_to_combo(metric_df, combo)
                if combo_df.empty:
                    continue
                series_color = _series_color(
                    base_metric_color, combo_idx, len(combos)
                )
                ax.scatter(
                    [0] * len(combo_df),
                    combo_df["value_num"],
                    s=plot_spec.get("marker_size", 40),
                    alpha=plot_spec.get("alpha_points", 0.65),
                    color=series_color,
                    marker=baseline_marker,
                    edgecolors="black",
                    linewidths=0.4,
                    zorder=2,
                    label=None,
                )

    _draw_reference_lines(ax, plot_spec)

    # Append " - by LOT / bias" to title when series_by is in effect.
    styled_spec = plot_spec
    if series_by:
        styled_spec = {
            **plot_spec,
            "title": (plot_spec.get("title") or "")
            + _cfg.title_suffix_for_series_by(series_by),
        }

    _style_axes(
        ax,
        styled_spec,
        default_y_label=styled_spec.get("y_label", "Value"),
    )
    _apply_axis_scales(ax, styled_spec)
    _apply_axis_limits(ax, styled_spec)

    return total_points, n_series_drawn, True


def plot_absolute(
    master_df: pd.DataFrame,
    plot_spec: dict[str, Any],
    *,
    output_dir: Path,
    reference_sns: list[str],
) -> dict[str, Any]:
    """Raw measurement value vs dose, one coloured series per stage/metric.

    Supports single metric (string) or multiple metrics (list).
    When multiple metrics, each gets a different color; stages vary by marker.

    When ``subplots:`` is present on the spec, the figure is divided into
    one Axes per subplot entry, each inheriting from the parent spec but
    free to override filters, limits, ``series_by``, etc.
    """
    if plot_spec.get("subplots"):
        return _render_with_subplots(
            master_df,
            plot_spec,
            output_dir=output_dir,
            reference_sns=reference_sns,
            draw_on_ax=_draw_absolute_on_ax,
        )

    figsize = plot_spec.get("figsize", [10, 6])
    fig, ax = plt.subplots(figsize=figsize)

    total_points, n_series_drawn, has_data = _draw_absolute_on_ax(
        ax, master_df, plot_spec, reference_sns
    )

    if not has_data:
        plt.close(fig)
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

    if plot_spec.get("note"):
        fig.text(0.5, 0.02, plot_spec["note"], ha="center", fontsize=8,
                 style="italic", wrap=True, color="#666666")

    out_path = _save_figure(fig, plot_spec, output_dir)
    logger.info(
        "Saved '%s' (%d points across %d metrics/stages)",
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


def _render_with_subplots(
    master_df: pd.DataFrame,
    plot_spec: dict[str, Any],
    *,
    output_dir: Path,
    reference_sns: list[str],
    draw_on_ax: Any,
) -> dict[str, Any]:
    """Shared subplot dispatcher for absolute/delta plots.

    Builds a figure with one Axes per entry in ``plot_spec['subplots']``
    and delegates rendering to ``draw_on_ax(ax, master_df, sub_spec,
    reference_sns)``. The parent's ``title`` becomes the figure suptitle.
    """
    try:
        sub_specs = _resolve_subplot_specs(plot_spec)
    except ValueError as exc:
        logger.error("Plot '%s': %s", plot_spec.get("output_name"), exc)
        return {
            "output_name": plot_spec.get("output_name", "<unknown>"),
            "output_path": None,
            "n_points": 0,
            "n_series": 0,
            "skipped": True,
            "reason": str(exc),
        }
    if not sub_specs:
        return {
            "output_name": plot_spec["output_name"],
            "output_path": None,
            "n_points": 0,
            "n_series": 0,
            "skipped": True,
            "reason": "subplots list is empty",
        }

    n = len(sub_specs)
    layout = plot_spec.get("subplot_layout", "rows")
    nrows, ncols = _subplot_grid(n, layout)
    figsize = _default_figsize_for_subplots(plot_spec, n, layout)

    fig, axes = plt.subplots(
        nrows,
        ncols,
        figsize=figsize,
        sharex=plot_spec.get("share_x", True),
        sharey=plot_spec.get("share_y", False),
        squeeze=False,
    )
    axes_flat = list(axes.flatten())

    total_points = 0
    total_series = 0
    any_data = False
    for ax, sub_spec in zip(axes_flat, sub_specs):
        n_pts, n_ser, has = draw_on_ax(
            ax, master_df, sub_spec, reference_sns
        )
        total_points += n_pts
        total_series += n_ser
        any_data = any_data or has
        if not has:
            ax.text(
                0.5,
                0.5,
                "no data",
                ha="center",
                va="center",
                transform=ax.transAxes,
                color="#999999",
                fontsize=10,
                style="italic",
            )

    # Hide unused axes (when grid has more cells than panels).
    for ax in axes_flat[len(sub_specs):]:
        ax.set_visible(False)

    if not any_data:
        plt.close(fig)
        logger.warning(
            "Plot '%s': no data in any subplot - skipped.",
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

    if plot_spec.get("title"):
        fig.suptitle(plot_spec["title"], fontsize=12, fontweight="bold")
    if plot_spec.get("note"):
        fig.text(0.5, 0.02, plot_spec["note"], ha="center", fontsize=8,
                 style="italic", wrap=True, color="#666666")

    out_path = _save_figure(fig, plot_spec, output_dir)
    logger.info(
        "Saved '%s' (%d panels, %d points, %d series)",
        out_path.name,
        n,
        total_points,
        total_series,
    )
    return {
        "output_name": plot_spec["output_name"],
        "output_path": str(out_path),
        "n_points": total_points,
        "n_series": total_series,
        "skipped": False,
        "reason": "",
    }


# ---------------------------------------------------------------------------
# Plot type 2: DELTA
# ---------------------------------------------------------------------------


def _draw_delta_on_ax(
    ax: plt.Axes,
    master_df: pd.DataFrame,
    plot_spec: dict[str, Any],
    reference_sns: list[str],
) -> tuple[int, int, bool]:
    """Render delta plot content onto a single Axes.

    Returns ``(n_points, n_series, has_data)``.
    """
    delta_from = plot_spec["delta_from"]
    delta_to = plot_spec["delta_to"]
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
        return 0, 0, False

    show_points = plot_spec.get("show_points", True)
    show_lines: list[str] = plot_spec.get("show_lines", [])
    series_by = _normalize_series_by(plot_spec)

    if series_by:
        sn_attrs_cols = ["lcl_serial_number"] + [
            c for c in series_by if c in df.columns
        ]
        sn_attrs = (
            df[sn_attrs_cols].drop_duplicates(subset=["lcl_serial_number"])
        )
        deltas = deltas.merge(sn_attrs, on="lcl_serial_number", how="left")

    combos = _series_combos(deltas, series_by)
    total_points = 0
    n_series_drawn = 0

    for combo_idx, combo in enumerate(combos):
        combo_df = _filter_to_combo(deltas, combo)
        if combo_df.empty:
            continue
        series_color = _series_color("#1f77b4", combo_idx, len(combos))
        combo_lbl = _combo_label(combo)
        base_label = combo_lbl if combo_lbl else "Per-SN delta"

        if show_points:
            ax.scatter(
                combo_df["dose_krad"],
                combo_df["delta"],
                s=plot_spec.get("marker_size", 40),
                alpha=plot_spec.get("alpha_points", 0.65),
                color=series_color,
                label=base_label,
                edgecolors="black",
                linewidths=0.4,
                zorder=2,
            )
            total_points += len(combo_df)

        if show_lines:
            renamed = combo_df.rename(columns={"delta": "value_num"})
            stats = dl.compute_stats_by_dose(renamed)
            if not stats.empty:
                for stat in show_lines:
                    if stat not in stats.columns:
                        continue
                    style_kwargs = _stat_line_styles(stat)
                    line_label = (
                        f"{combo_lbl} {stat}" if combo_lbl else stat
                    )
                    ax.plot(
                        stats["dose_krad"],
                        stats[stat],
                        color=series_color,
                        marker="",
                        label=line_label,
                        zorder=3,
                        **style_kwargs,
                    )
        n_series_drawn += 1

    ax.axhline(0, color="black", linewidth=0.8, alpha=0.6, zorder=1)

    _draw_reference_lines(ax, plot_spec)

    default_y = "delta value [%]"
    styled_spec = plot_spec
    if not plot_spec.get("y_label"):
        styled_spec = {**styled_spec, "y_label": default_y}
    if series_by:
        styled_spec = {
            **styled_spec,
            "title": (styled_spec.get("title") or "")
            + _cfg.title_suffix_for_series_by(series_by),
        }

    _style_axes(ax, styled_spec, default_y_label=default_y)
    _apply_axis_scales(ax, styled_spec)
    _apply_axis_limits(ax, styled_spec)

    return total_points, max(n_series_drawn, 1), True


def plot_delta(
    master_df: pd.DataFrame,
    plot_spec: dict[str, Any],
    *,
    output_dir: Path,
    reference_sns: list[str],
) -> dict[str, Any]:
    """Per-SN delta between two stages, plotted against dose.

    Supports ``subplots:`` (one Axes per entry, each can override filters
    / limits / series_by).
    """
    if plot_spec.get("subplots"):
        return _render_with_subplots(
            master_df,
            plot_spec,
            output_dir=output_dir,
            reference_sns=reference_sns,
            draw_on_ax=_draw_delta_on_ax,
        )

    figsize = plot_spec.get("figsize", [10, 6])
    fig, ax = plt.subplots(figsize=figsize)

    total_points, n_series_drawn, has_data = _draw_delta_on_ax(
        ax, master_df, plot_spec, reference_sns
    )

    if not has_data:
        plt.close(fig)
        logger.warning(
            "Plot '%s': no SN has both stages - skipped.",
            plot_spec["output_name"],
        )
        return {
            "output_name": plot_spec["output_name"],
            "output_path": None,
            "n_points": 0,
            "n_series": 0,
            "skipped": True,
            "reason": (
                f"no SN with both {plot_spec['delta_from']} and "
                f"{plot_spec['delta_to']}"
            ),
        }

    if plot_spec.get("note"):
        fig.text(0.5, 0.02, plot_spec["note"], ha="center", fontsize=8,
                 style="italic", wrap=True, color="#666666")

    out_path = _save_figure(fig, plot_spec, output_dir)
    logger.info(
        "Saved '%s' (%d deltas, %d series)",
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

    _draw_reference_lines(ax_main, plot_spec)

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

    # Add note if present
    if plot_spec.get("note"):
        fig.text(0.5, 0.02, plot_spec["note"], ha="center", fontsize=8,
                 style="italic", wrap=True, color="#666666")

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
