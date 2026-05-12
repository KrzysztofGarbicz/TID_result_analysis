#!/usr/bin/env python3
"""Generate radiation-test plots from aggregated flat CSV files.

Usage:

    python plot_radiation.py --config examples/plot_config.yaml

Useful flags:

    --dry-run          Validate config and report how many rows each plot
                       would receive, without rendering.
    --only NAME[,...]  Render only the named plots (matches `name` from
                       YAML; if a plot has variants, all variants of that
                       name are kept).
    --verbose          Debug-level logging.

The script writes:

    <output_dir>/<name>.png       (one per plot / variant)
    <output_dir>/_plot_index.csv  (summary of what was rendered)
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import pandas as pd

from radiation_plot import config as cfg
from radiation_plot import data_loader as dl
from radiation_plot import plotters


PLOT_INDEX_COLUMNS = [
    "output_name",
    "type",
    "lcl_name",
    "measurement_type",
    "metric",
    "context_key",
    "output_path",
    "n_points",
    "n_series",
    "skipped",
    "reason",
]


def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Generate radiation-test plots from flat CSV files."
    )
    p.add_argument(
        "--config",
        required=True,
        type=Path,
        help="Path to plot_config.yaml",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate config and report row counts without rendering",
    )
    p.add_argument(
        "--only",
        type=str,
        default=None,
        help="Comma-separated list of plot names to render (others are skipped)",
    )
    p.add_argument(
        "--verbose",
        action="store_true",
        help="Enable debug logging",
    )
    return p.parse_args()


def _filter_specs_by_only(
    specs: list[dict],
    only_arg: str | None,
) -> list[dict]:
    if not only_arg:
        return specs
    wanted = {n.strip() for n in only_arg.split(",") if n.strip()}
    kept = [s for s in specs if s["name"] in wanted or s["output_name"] in wanted]
    if not kept:
        logging.warning(
            "--only=%s matched no plot specs (names available: %s)",
            only_arg,
            sorted({s["name"] for s in specs}),
        )
    return kept


def _resolve_paths(data_section: dict, config_path: Path) -> tuple[Path, Path, Path]:
    """Resolve paths from the config relative to the config file location.

    This lets the user keep relative paths in the YAML (``./flat_by_device``)
    without depending on the directory the script is invoked from.
    """
    base = config_path.parent.resolve()

    def _resolve(value: str) -> Path:
        p = Path(value)
        return p if p.is_absolute() else (base / p).resolve()

    flat_dir = _resolve(data_section["flat_files_dir"])
    dose_map_path = _resolve(data_section["dose_map"])
    output_dir = _resolve(data_section["output_dir"])
    return flat_dir, dose_map_path, output_dir


def _dry_run_report(
    master_df: pd.DataFrame,
    plot_specs: list[dict],
    reference_sns: list[str],
) -> int:
    """Print row counts for each plot spec. Returns process exit code."""
    print()
    print(f"{'Plot':40s} {'Type':10s} {'Rows':>8s}  Notes")
    print("-" * 90)
    issues = 0
    for spec in plot_specs:
        filtered = dl.filter_for_plot(
            master_df,
            lcl_name=spec["lcl_name"],
            measurement_type=spec["measurement_type"],
            metric=spec["metric"],
            context_key=spec.get("context_key"),
            stages=(
                spec.get("stages")
                or spec.get("stages_order")
                or [spec.get("delta_from"), spec.get("delta_to")]
            ),
            exclude_sn=spec.get("exclude_sn"),
            include_doses=spec.get("include_doses"),
            exclude_doses=spec.get("exclude_doses"),
            exclude_reference=(
                reference_sns if spec["type"] != "annealing" else None
            ),
            lot=spec.get("lot"),
            bias=spec.get("bias"),
        )
        n = len(filtered)
        note = ""
        if n == 0:
            note = "EMPTY - would be skipped"
            issues += 1
        print(f"{spec['output_name']:40s} {spec['type']:10s} {n:>8d}  {note}")
    print()
    if issues:
        print(f"WARNING: {issues} plot(s) would produce empty output.")
    return 0


def _write_plot_index(
    results: list[dict],
    plot_specs: list[dict],
    output_dir: Path,
) -> None:
    """Write a CSV summarising every plot attempt.

    Joins each result with its spec by ``output_name`` so we can include
    the plot's source identification (device, metric, ...).
    """
    spec_by_name = {s["output_name"]: s for s in plot_specs}
    rows = []
    for r in results:
        spec = spec_by_name.get(r["output_name"], {})
        rows.append(
            {
                "output_name": r["output_name"],
                "type": spec.get("type", ""),
                "lcl_name": spec.get("lcl_name", ""),
                "measurement_type": spec.get("measurement_type", ""),
                "metric": spec.get("metric", ""),
                "context_key": spec.get("context_key", ""),
                "output_path": r.get("output_path") or "",
                "n_points": r.get("n_points", 0),
                "n_series": r.get("n_series", 0),
                "skipped": bool(r.get("skipped", False)),
                "reason": r.get("reason", ""),
            }
        )
    df = pd.DataFrame(rows, columns=PLOT_INDEX_COLUMNS)
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / "_plot_index.csv"
    df.to_csv(out_path, index=False, encoding="utf-8-sig")
    logging.info("Wrote plot index: %s", out_path)


def main() -> int:
    args = _parse_args()
    _setup_logging(args.verbose)

    try:
        plot_config = cfg.load_plot_config(args.config)
    except cfg.ConfigError as exc:
        logging.error("Plot config error: %s", exc)
        return 2

    flat_dir, dose_map_path, output_dir = _resolve_paths(
        plot_config["data"], args.config
    )
    logging.info("flat_files_dir : %s", flat_dir)
    logging.info("dose_map       : %s", dose_map_path)
    logging.info("output_dir     : %s", output_dir)

    try:
        dose_map, reference_sns, lot_by_sn, bias_by_sn = cfg.load_dose_map(
            dose_map_path
        )
    except cfg.ConfigError as exc:
        logging.error("Dose map error: %s", exc)
        return 2

    try:
        master_df = dl.build_master_dataframe(
            flat_dir,
            dose_map,
            lot_by_sn=lot_by_sn,
            bias_by_sn=bias_by_sn,
        )
    except (FileNotFoundError, RuntimeError) as exc:
        logging.error("Data loading failed: %s", exc)
        return 2

    # Expand any `split_by:` entries now that we know which lot / bias
    # groups actually exist.
    try:
        expanded = cfg.expand_splits(
            plot_config["plots"],
            lot_values=sorted(set(lot_by_sn.values())),
            bias_values=sorted(set(bias_by_sn.values())),
        )
    except cfg.ConfigError as exc:
        logging.error("Plot config error during split expansion: %s", exc)
        return 2

    plot_specs = _filter_specs_by_only(expanded, args.only)
    if not plot_specs:
        logging.error("No plots to render after applying --only filter.")
        return 1

    if args.dry_run:
        return _dry_run_report(master_df, plot_specs, reference_sns)

    output_dir.mkdir(parents=True, exist_ok=True)
    results: list[dict] = []
    for spec in plot_specs:
        result = plotters.render_plot(
            master_df,
            spec,
            output_dir=output_dir,
            reference_sns=reference_sns,
        )
        results.append(result)

    n_ok = sum(1 for r in results if not r["skipped"])
    n_skipped = sum(1 for r in results if r["skipped"])
    logging.info(
        "Done: %d plots rendered, %d skipped (see _plot_index.csv).",
        n_ok,
        n_skipped,
    )

    _write_plot_index(results, plot_specs, output_dir)
    return 0 if n_skipped == 0 else 0  # don't error on skipped plots


if __name__ == "__main__":
    sys.exit(main())
