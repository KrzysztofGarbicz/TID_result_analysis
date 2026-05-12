#!/usr/bin/env python3
"""
review_flat_files_gui.py

Interactive reviewer for flat CSV measurement files.

Features:
- validates measurement completeness against a YAML definition,
- writes a shared missing-measurements TXT report,
- launches a Tkinter + matplotlib GUI for visual review,
- allows manual flagging of suspicious records,
- writes a shared flagged-points TXT report on exit.

Python 3.10+
"""

from __future__ import annotations

import argparse
import logging
import math
import re
import sys
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Any, Dict, Iterable, List, Optional, Tuple

import matplotlib
matplotlib.use("TkAgg")
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.figure import Figure
import pandas as pd
import yaml

REQUIRED_COLUMNS = [
    "timestamp",
    "run_id",
    "lcl_name",
    "lcl_serial_number",
    "measurement_type",
    "metric",
    "value",
    "unit",
    "context_json",
    "irradiation_stage",
    "source_dir_name",
    "context_key",
    "data_origin",
]

DEFAULT_FLAG_CATEGORIES = ["outlier", "suspicious", "bad_measurement"]


@dataclass
class LoadedFlatFile:
    """Container for a single loaded flat CSV file."""
    path: Path
    df: pd.DataFrame
    lcl_name: str
    lcl_serial_number: str


def setup_logging(verbose: bool) -> None:
    """Configure logging."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(description="Review flat CSV files with completeness checks and GUI.")
    parser.add_argument("--input-dir", required=True, help="Directory with *_flat.csv files.")
    parser.add_argument("--config-yaml", required=True, help="YAML file with expected measurements.")
    parser.add_argument("--output-dir", required=True, help="Output directory for TXT reports.")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose logging.")
    return parser.parse_args()


def normalize_text(value: Any) -> str:
    """Normalize arbitrary values into a stripped string."""
    if value is None:
        return ""
    if pd.isna(value):
        return ""
    return str(value).strip()


def normalize_context_key(value: Any) -> str:
    """Normalize context_key for comparison."""
    return normalize_text(value)


def safe_float(value: Any) -> Optional[float]:
    """Convert a value to float if possible, else return None."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    try:
        out = float(value)
        if math.isnan(out):
            return None
        return out
    except Exception:
        return None

DOSE_BY_SERIAL: Dict[str, str] = {
    # REF / control samples
    "SN36": "0 kRad",
    "SN37": "0 kRad",

    # Irradiated samples
    "SN01": "1 kRad",
    "SN02": "1 kRad",
    "SN03": "1 kRad",
    "SN04": "1 kRad",
    "SN25": "1 kRad",
    "SN26": "1 kRad",

    "SN05": "2 kRad",
    "SN06": "2 kRad",
    "SN07": "2 kRad",
    "SN08": "2 kRad",
    "SN27": "2 kRad",
    "SN28": "2 kRad",

    "SN09": "5 kRad",
    "SN10": "5 kRad",
    "SN11": "5 kRad",
    "SN12": "5 kRad",
    "SN29": "5 kRad",
    "SN30": "5 kRad",

    "SN13": "10 kRad",
    "SN14": "10 kRad",
    "SN15": "10 kRad",
    "SN16": "10 kRad",
    "SN31": "10 kRad",
    "SN32": "10 kRad",

    "SN17": "15 kRad",
    "SN18": "15 kRad",
    "SN19": "15 kRad",
    "SN20": "15 kRad",
    "SN33": "15 kRad",
    "SN34": "15 kRad",

    "SN21": "25 kRad",
    "SN22": "25 kRad",
    "SN35": "25 kRad",

    "SN23": "40 kRad",
    "SN24": "40 kRad",
}


def normalize_serial_number(value: Any) -> str:
    """Normalize serial number to SNxx form, e.g. SN4 -> SN04, 4 -> SN04."""
    text = normalize_text(value).upper().replace(" ", "")
    if not text:
        return ""

    match = re.fullmatch(r"(?:SN[:_-]?)?(\d+)", text)
    if match:
        return f"SN{int(match.group(1)):02d}"

    match = re.search(r"SN[:_-]?(\d+)", text)
    if match:
        return f"SN{int(match.group(1)):02d}"

    return text


def serial_to_dose(serial_number: Any) -> str:
    """Return dose group assigned to a serial number."""
    return DOSE_BY_SERIAL.get(normalize_serial_number(serial_number), "UNKNOWN")


def load_yaml_config(config_path: Path) -> Dict[str, Any]:
    """Load YAML configuration."""
    if not config_path.exists():
        raise FileNotFoundError(f"Config YAML not found: {config_path}")
    with config_path.open("r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    if not isinstance(cfg, dict):
        raise ValueError("YAML root must be a mapping.")
    cfg.setdefault("ignore_serial_numbers", [])
    cfg.setdefault("ignore_missing_rules", [])
    cfg.setdefault("expected_stages", [])
    cfg.setdefault("devices", {})
    return cfg


def should_ignore_serial(serial_number: str, config: Dict[str, Any]) -> bool:
    """Check whether a serial number should be ignored."""
    ignored = {normalize_text(x) for x in config.get("ignore_serial_numbers", [])}
    return normalize_text(serial_number) in ignored


def normalize_rule_value(value: Any) -> List[str]:
    """
    Normalize YAML rule value into a list of strings.

    Supports:
    - single scalar: "after_irradiate"
    - YAML list: ["after_irradiate", "before_irradiate"]
    """
    if value is None:
        return []

    if isinstance(value, (list, tuple, set)):
        return [normalize_text(v) for v in value if normalize_text(v)]

    single = normalize_text(value)
    return [single] if single else []


def rule_field_matches(rule_value: Any, item_value: Any) -> bool:
    """
    Match one rule field against one missing-item field.

    Rule semantics:
    - missing key in rule => wildcard
    - scalar value => exact match
    - list value => item must be one of listed values
    """
    normalized_rule_values = normalize_rule_value(rule_value)
    if not normalized_rule_values:
        return False
    return normalize_text(item_value) in normalized_rule_values


def should_ignore_missing_item(missing_item: Dict[str, Any], config: Dict[str, Any]) -> bool:
    """
    Check whether one detected missing item should be ignored based on YAML rules.

    Supported rule keys:
    - flat_file
    - lcl_name
    - lcl_serial_number
    - irradiation_stage
    - logical_measurement_name
    - source_measurement_type
    - missing_metric
    - missing_context_key
    """
    ignore_rules = config.get("ignore_missing_rules", []) or []
    if not isinstance(ignore_rules, list):
        logging.warning("ignore_missing_rules should be a list in YAML.")
        return False

    comparable_keys = [
        "flat_file",
        "lcl_name",
        "lcl_serial_number",
        "irradiation_stage",
        "logical_measurement_name",
        "source_measurement_type",
        "missing_metric",
        "missing_context_key",
    ]

    for idx, rule in enumerate(ignore_rules):
        if not isinstance(rule, dict):
            logging.warning("Ignoring malformed ignore_missing_rules[%d] - expected dict, got %r", idx, type(rule))
            continue

        matched = True
        for key in comparable_keys:
            if key not in rule:
                continue
            if not rule_field_matches(rule.get(key), missing_item.get(key, "")):
                matched = False
                break

        if matched:
            logging.debug(
                "Ignoring missing item due to rule %d: %s",
                idx,
                {k: rule[k] for k in rule.keys() if k != "reason"},
            )
            return True

    return False


def filter_missing_measurements(
    missing_items: List[Dict[str, str]],
    config: Dict[str, Any],
) -> List[Dict[str, str]]:
    """Filter detected missing measurements using ignore_missing_rules from YAML."""
    filtered: List[Dict[str, str]] = []
    for item in missing_items:
        if should_ignore_missing_item(item, config):
            continue
        filtered.append(item)
    return filtered


def validate_required_columns(df: pd.DataFrame, path: Path) -> None:
    """Ensure that all required columns exist."""
    missing = [col for col in REQUIRED_COLUMNS if col not in df.columns]
    if missing:
        raise ValueError(f"Missing required columns in {path.name}: {missing}")


def load_flat_files(input_dir: Path) -> Tuple[List[LoadedFlatFile], List[str]]:
    """Load all *_flat.csv files from a directory."""
    warnings: List[str] = []
    if not input_dir.exists():
        raise FileNotFoundError(f"Input directory does not exist: {input_dir}")
    if not input_dir.is_dir():
        raise NotADirectoryError(f"Input path is not a directory: {input_dir}")

    csv_paths = sorted(input_dir.glob("*_flat.csv"))
    if not csv_paths:
        return [], [f"No *_flat.csv files found in: {input_dir}"]

    loaded: List[LoadedFlatFile] = []
    for csv_path in csv_paths:
        try:
            df = pd.read_csv(csv_path, dtype=str, keep_default_na=False)
            validate_required_columns(df, csv_path)

            # Preserve source file and source row identity.
            df = df.copy()
            df["flat_file"] = csv_path.name
            df["source_row_index"] = range(len(df))
            df["__record_uid"] = [
                f"{csv_path.name}::row={idx}" for idx in range(len(df))
            ]
            df["context_key"] = df["context_key"].map(normalize_context_key)
            df["lcl_serial_number"] = df["lcl_serial_number"].map(normalize_serial_number)
            df["dose_group"] = df["lcl_serial_number"].map(serial_to_dose)
            df["value_numeric"] = pd.to_numeric(df["value"], errors="coerce")

            # Try to infer device/SN from content, fallback to filename.
            lcl_names = sorted({normalize_text(x) for x in df["lcl_name"] if normalize_text(x)})
            serials = sorted({normalize_text(x) for x in df["lcl_serial_number"] if normalize_text(x)})

            lcl_name = lcl_names[0] if lcl_names else csv_path.stem.split("_")[0]
            lcl_serial_number = serials[0] if serials else "UNKNOWN"

            if len(lcl_names) > 1:
                warnings.append(f"{csv_path.name}: multiple lcl_name values found {lcl_names}, using {lcl_name}")
            if len(serials) > 1:
                warnings.append(f"{csv_path.name}: multiple serial values found {serials}, using {lcl_serial_number}")

            loaded.append(
                LoadedFlatFile(
                    path=csv_path,
                    df=df,
                    lcl_name=lcl_name,
                    lcl_serial_number=lcl_serial_number,
                )
            )
            logging.debug("Loaded %s with %d rows", csv_path.name, len(df))
        except Exception as exc:
            msg = f"Failed to load {csv_path.name}: {exc}"
            logging.exception(msg)
            warnings.append(msg)

    return loaded, warnings


def get_device_expected_measurements(config: Dict[str, Any], lcl_name: str) -> Dict[str, Any]:
    """Return expected measurement mapping for a device."""
    devices = config.get("devices", {})
    device_cfg = devices.get(lcl_name, {})
    return device_cfg.get("measurements", {}) if isinstance(device_cfg, dict) else {}


def get_logical_measurement_map(config: Dict[str, Any], lcl_name: str) -> Dict[str, Dict[str, Any]]:
    """Return logical measurement definitions for a device."""
    result: Dict[str, Dict[str, Any]] = {}
    device_measurements = get_device_expected_measurements(config, lcl_name)
    for logical_name, spec in device_measurements.items():
        spec = spec or {}
        metrics = list(spec.get("metrics", []) or [])
        contexts = list(spec.get("contexts", []) or [])
        source_measurement_type = spec.get("source_measurement_type", logical_name)
        result[logical_name] = {
            "logical_measurement_name": logical_name,
            "source_measurement_type": source_measurement_type,
            "metrics": metrics,
            "contexts": contexts,
        }
    return result


def expand_logical_measurements(all_df: pd.DataFrame, config: Dict[str, Any]) -> pd.DataFrame:
    """
    Expand flat rows into logical measurements.

    For normal cases logical_measurement_name == measurement_type.
    For aliases like time_on/time_off sourced from time_on_off, duplicate only matching metrics.
    """
    expanded_frames: List[pd.DataFrame] = []

    known_devices = sorted({normalize_text(x) for x in all_df["lcl_name"].unique() if normalize_text(x)})

    for lcl_name in known_devices:
        device_df = all_df.loc[all_df["lcl_name"] == lcl_name].copy()
        logical_map = get_logical_measurement_map(config, lcl_name)
        if not logical_map:
            # keep original measurement types so GUI can still show something for unknown devices if needed
            tmp = device_df.copy()
            tmp["logical_measurement_name"] = tmp["measurement_type"]
            tmp["source_measurement_type"] = tmp["measurement_type"]
            expanded_frames.append(tmp)
            continue

        for logical_name, spec in logical_map.items():
            src_type = spec["source_measurement_type"]
            metrics = set(spec["metrics"])
            subset = device_df.loc[
                (device_df["measurement_type"] == src_type)
                & (device_df["metric"].isin(metrics))
            ].copy()
            if subset.empty:
                continue
            subset["logical_measurement_name"] = logical_name
            subset["source_measurement_type"] = src_type
            expanded_frames.append(subset)

    if not expanded_frames:
        return pd.DataFrame(columns=list(all_df.columns) + ["logical_measurement_name", "source_measurement_type"])

    expanded = pd.concat(expanded_frames, ignore_index=True)
    return expanded


def check_missing_measurements(loaded_files: List[LoadedFlatFile], config: Dict[str, Any]) -> List[Dict[str, str]]:
    """Check completeness of all loaded flat CSV files."""
    missing: List[Dict[str, str]] = []
    expected_stages = [normalize_text(x) for x in config.get("expected_stages", [])]

    for item in loaded_files:
        lcl_name = normalize_text(item.lcl_name)
        serial = normalize_text(item.lcl_serial_number)

        if should_ignore_serial(serial, config):
            logging.info("Skipping ignored serial %s in %s", serial, item.path.name)
            continue

        device_measurements = get_logical_measurement_map(config, lcl_name)
        if not device_measurements:
            missing.append(
                {
                    "flat_file": item.path.name,
                    "lcl_name": lcl_name,
                    "lcl_serial_number": serial,
                    "irradiation_stage": "",
                    "logical_measurement_name": "",
                    "source_measurement_type": "",
                    "missing_metric": "",
                    "missing_context_key": "",
                    "note": f"Unknown lcl_name not present in YAML: {lcl_name}",
                }
            )
            continue

        df = item.df.copy()
        present_stages = {normalize_text(x) for x in df["irradiation_stage"].unique() if normalize_text(x)}
        for stage in expected_stages:
            if stage not in present_stages:
                missing.append(
                    {
                        "flat_file": item.path.name,
                        "lcl_name": lcl_name,
                        "lcl_serial_number": serial,
                        "irradiation_stage": stage,
                        "logical_measurement_name": "",
                        "source_measurement_type": "",
                        "missing_metric": "",
                        "missing_context_key": "",
                        "note": "Missing irradiation stage",
                    }
                )

        for stage in expected_stages:
            stage_df = df.loc[df["irradiation_stage"] == stage]
            for logical_name, spec in device_measurements.items():
                src_type = normalize_text(spec["source_measurement_type"])
                metrics = [normalize_text(x) for x in spec.get("metrics", [])]
                contexts = [normalize_text(x) for x in spec.get("contexts", [])]

                for metric in metrics:
                    if contexts:
                        for context_key in contexts:
                            mask = (
                                (stage_df["measurement_type"] == src_type)
                                & (stage_df["metric"] == metric)
                                & (stage_df["context_key"].map(normalize_context_key) == context_key)
                            )
                            if not mask.any():
                                missing.append(
                                    {
                                        "flat_file": item.path.name,
                                        "lcl_name": lcl_name,
                                        "lcl_serial_number": serial,
                                        "irradiation_stage": stage,
                                        "logical_measurement_name": logical_name,
                                        "source_measurement_type": src_type,
                                        "missing_metric": metric,
                                        "missing_context_key": context_key,
                                        "note": "",
                                    }
                                )
                    else:
                        mask = (
                            (stage_df["measurement_type"] == src_type)
                            & (stage_df["metric"] == metric)
                        )
                        if not mask.any():
                            missing.append(
                                {
                                    "flat_file": item.path.name,
                                    "lcl_name": lcl_name,
                                    "lcl_serial_number": serial,
                                    "irradiation_stage": stage,
                                    "logical_measurement_name": logical_name,
                                    "source_measurement_type": src_type,
                                    "missing_metric": metric,
                                    "missing_context_key": "",
                                    "note": "",
                                }
                            )
    return missing


def build_missing_report(missing_items: List[Dict[str, str]], output_path: Path) -> None:
    """Write missing-measurements TXT report."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        if not missing_items:
            f.write("No missing measurements\n")
            return

        for item in missing_items:
            note = normalize_text(item.get("note"))
            if note:
                f.write(
                    f"{item['flat_file']} | {item['lcl_name']} | {item['lcl_serial_number']} | "
                    f"{item['irradiation_stage']} | {item['logical_measurement_name']} | "
                    f"{item['source_measurement_type']} | {note}\n"
                )
            else:
                context_part = f" | context: {item['missing_context_key']}" if normalize_text(item.get("missing_context_key")) else ""
                f.write(
                    f"{item['flat_file']} | {item['lcl_name']} | {item['lcl_serial_number']} | "
                    f"{item['irradiation_stage']} | {item['logical_measurement_name']} | "
                    f"{item['source_measurement_type']} | missing metric: {item['missing_metric']}{context_part}\n"
                )


def save_flagged_points(flagged_points: Dict[str, Dict[str, Any]], output_path: Path) -> None:
    """Write flagged points TXT report."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        if not flagged_points:
            f.write("No flagged points\n")
            return

        for uid, entry in flagged_points.items():
            row = entry["record"]
            category = entry["flag_category"]
            parts = [
                f"flat_file={normalize_text(row.get('flat_file'))}",
                f"lcl_name={normalize_text(row.get('lcl_name'))}",
                f"lcl_serial_number={normalize_text(row.get('lcl_serial_number'))}",
                f"irradiation_stage={normalize_text(row.get('irradiation_stage'))}",
                f"logical_measurement_name={normalize_text(row.get('logical_measurement_name'))}",
                f"source_measurement_type={normalize_text(row.get('source_measurement_type'))}",
                f"metric={normalize_text(row.get('metric'))}",
                f"context_key={normalize_text(row.get('context_key'))}",
                f"value={normalize_text(row.get('value'))}",
                f"unit={normalize_text(row.get('unit'))}",
                f"source_dir_name={normalize_text(row.get('source_dir_name'))}",
                f"timestamp={normalize_text(row.get('timestamp'))}",
                f"run_id={normalize_text(row.get('run_id'))}",
                f"data_origin={normalize_text(row.get('data_origin'))}",
                f"flag_category={normalize_text(category)}",
                f"record_uid={uid}",
            ]
            f.write(" | ".join(parts) + "\n")


def build_record_details(record: Dict[str, Any]) -> str:
    """Format record details for the info panel."""
    fields = [
        "flat_file",
        "lcl_name",
        "lcl_serial_number",
        "irradiation_stage",
        "logical_measurement_name",
        "source_measurement_type",
        "metric",
        "context_key",
        "value",
        "unit",
        "source_dir_name",
        "timestamp",
        "run_id",
        "data_origin",
        "__record_uid",
    ]
    return "\n".join(f"{field}: {normalize_text(record.get(field))}" for field in fields)


def get_stage_order(config: Dict[str, Any]) -> List[str]:
    """Return stage order from YAML."""
    return [normalize_text(x) for x in config.get("expected_stages", [])]


def stage_position_map(config: Dict[str, Any]) -> Dict[str, int]:
    """Return mapping stage -> x-position."""
    return {stage: idx for idx, stage in enumerate(get_stage_order(config))}


def deterministic_jitter(uid: str) -> float:
    """Deterministic small jitter based on uid."""
    base = sum(ord(ch) for ch in uid) % 1000
    return ((base / 999.0) - 0.5) * 0.24


def get_logical_measurement_contexts(config: Dict[str, Any], lcl_name: str, logical_measurement_name: str) -> List[str]:
    """Return configured context list for one logical measurement."""
    logical_map = get_logical_measurement_map(config, lcl_name)
    spec = logical_map.get(logical_measurement_name, {})
    return [normalize_context_key(x) for x in spec.get("contexts", []) if normalize_context_key(x)]


def extract_plot_records(
    expanded_df: pd.DataFrame,
    lcl_name: str,
    logical_measurement_name: str,
    selected_context: Optional[str] = None,
) -> pd.DataFrame:
    """Extract rows for one device and one logical measurement, optionally filtered by context."""
    subset = expanded_df.loc[
        (expanded_df["lcl_name"] == lcl_name)
        & (expanded_df["logical_measurement_name"] == logical_measurement_name)
    ].copy()
    if subset.empty:
        return subset

    subset["context_key_norm"] = subset["context_key"].map(normalize_context_key)
    subset["value_numeric"] = pd.to_numeric(subset["value"], errors="coerce")

    context_norm = normalize_context_key(selected_context)
    if context_norm and context_norm != "ALL":
        subset = subset.loc[subset["context_key_norm"] == context_norm].copy()

    return subset


def filter_visible_serial_records(records: pd.DataFrame, visible_serials: Iterable[str]) -> pd.DataFrame:
    """Keep only records whose lcl_serial_number is selected as visible."""
    if records.empty:
        return records

    visible = {normalize_serial_number(sn) for sn in visible_serials if normalize_serial_number(sn)}
    if not visible:
        return records.iloc[0:0].copy()

    out = records.copy()
    out["lcl_serial_number_norm"] = out["lcl_serial_number"].map(normalize_serial_number)
    return out.loc[out["lcl_serial_number_norm"].isin(visible)].copy()


def filter_duplicate_measurement_records(records: pd.DataFrame) -> pd.DataFrame:
    """
    Keep only records that belong to duplicated measurement slots.

    Duplicate definition: same lcl_name, lcl_serial_number, irradiation_stage,
    logical_measurement_name, source_measurement_type, metric and context_key.
    Both/all duplicated records are kept so the user can compare them.
    """
    if records.empty:
        return records

    out = records.copy()
    if "context_key_norm" not in out.columns:
        out["context_key_norm"] = out["context_key"].map(normalize_context_key)

    group_cols = [
        "lcl_name",
        "lcl_serial_number",
        "irradiation_stage",
        "logical_measurement_name",
        "source_measurement_type",
        "metric",
        "context_key_norm",
    ]
    available_cols = [col for col in group_cols if col in out.columns]
    if not available_cols or "__record_uid" not in out.columns:
        return out.iloc[0:0].copy()

    out["duplicate_group_count"] = (
        out.groupby(available_cols, dropna=False)["__record_uid"].transform("count")
    )
    return out.loc[out["duplicate_group_count"] > 1].copy()


def create_measurement_figure(
    records: pd.DataFrame,
    config: Dict[str, Any],
    lcl_name: str,
    logical_measurement_name: str,
    flagged_points: Dict[str, Dict[str, Any]],
    selected_context: Optional[str] = None,
) -> Figure:
    """Create a matplotlib Figure for one logical measurement."""
    logical_map = get_logical_measurement_map(config, lcl_name)
    spec = logical_map.get(logical_measurement_name, {})
    metrics = list(spec.get("metrics", []))
    stage_pos = stage_position_map(config)
    stages = get_stage_order(config)

    n_metrics = max(1, len(metrics))
    fig = Figure(figsize=(10, 4 * n_metrics))
    axes = [fig.add_subplot(n_metrics, 1, i + 1) for i in range(n_metrics)]
    if n_metrics == 1:
        axes = [axes[0]]

    context_suffix = ""
    context_norm = normalize_context_key(selected_context)
    if context_norm and context_norm != "ALL":
        context_suffix = f" | {context_norm}"
    fig.suptitle(f"{lcl_name} | {logical_measurement_name}{context_suffix}", fontsize=12)

    if records.empty:
        for ax, metric in zip(axes, metrics or ["No data"]):
            ax.set_title(metric)
            ax.text(0.5, 0.5, "No data", ha="center", va="center", transform=ax.transAxes)
            ax.set_xticks(list(range(len(stages))))
            ax.set_xticklabels(stages, rotation=15)
            ax.grid(True, alpha=0.3)
        fig.tight_layout(rect=[0, 0.03, 1, 0.95])
        return fig

    visible_uids = set()
    if "__record_uid" in records.columns:
        visible_uids = {normalize_text(uid) for uid in records["__record_uid"].tolist()}

    # Legend control: show only when number of unique series is manageable.
    unique_series = sorted(
        {
            (normalize_text(r["lcl_serial_number"]), normalize_context_key(r.get("context_key", "")))
            for _, r in records.iterrows()
        }
    )
    show_legend = len(unique_series) <= 20

    for ax, metric in zip(axes, metrics):
        metric_df = records.loc[records["metric"] == metric].copy()
        ax.set_title(metric)
        ax.set_xticks(list(range(len(stages))))
        ax.set_xticklabels(stages, rotation=15)
        ax.grid(True, alpha=0.3)

        numeric_df = metric_df.loc[metric_df["value_numeric"].notna()].copy()

        if numeric_df.empty:
            ax.text(0.5, 0.5, "No data", ha="center", va="center", transform=ax.transAxes)
            continue

        grouped = numeric_df.groupby(["lcl_serial_number", "context_key_norm"], dropna=False)
        for (serial, context_key), grp in grouped:
            grp = grp.copy()
            xs = [
                stage_pos.get(normalize_text(stage), 0) + deterministic_jitter(uid)
                for stage, uid in zip(grp["irradiation_stage"], grp["__record_uid"])
            ]
            ys = grp["value_numeric"].tolist()
            label = normalize_text(serial)
            if normalize_text(context_key):
                label = f"{label} | {context_key}"

            artist = ax.scatter(xs, ys, label=label, picker=5)
            artist._record_uids = grp["__record_uid"].tolist()  # type: ignore[attr-defined]

        # Overlay flagged markers.
        flagged_metric_rows = []
        for uid, entry in flagged_points.items():
            if visible_uids and normalize_text(uid) not in visible_uids:
                continue

            row = entry["record"]
            if (
                normalize_text(row.get("lcl_name")) == lcl_name
                and normalize_text(row.get("logical_measurement_name")) == logical_measurement_name
                and normalize_text(row.get("metric")) == metric
            ):
                y = safe_float(row.get("value"))
                if y is None:
                    continue
                x = stage_pos.get(normalize_text(row.get("irradiation_stage")), 0) + deterministic_jitter(uid)
                flagged_metric_rows.append((x, y))
        if flagged_metric_rows:
            ax.scatter(
                [x for x, _ in flagged_metric_rows],
                [y for _, y in flagged_metric_rows],
                s=120,
                facecolors="none",
                edgecolors="red",
                linewidths=1.5,
            )

        if show_legend:
            handles, labels = ax.get_legend_handles_labels()
            if handles:
                ax.legend(loc="best", fontsize=8)

    fig.tight_layout(rect=[0, 0.03, 1, 0.95])
    return fig


class ReviewApp:
    """Tkinter GUI for reviewing flat CSV measurements."""

    def __init__(
        self,
        root: tk.Tk,
        config: Dict[str, Any],
        expanded_df: pd.DataFrame,
        output_dir: Path,
    ) -> None:
        self.root = root
        self.config = config
        self.expanded_df = expanded_df.copy()
        self.output_dir = output_dir
        self.flagged_points: Dict[str, Dict[str, Any]] = {}
        self.selected_record: Optional[Dict[str, Any]] = None
        self.current_canvas: Optional[FigureCanvasTkAgg] = None
        self.current_toolbar: Optional[NavigationToolbar2Tk] = None

        self.root.title("Flat file reviewer")
        self.root.geometry("1500x900")
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        self.devices = sorted({normalize_text(x) for x in self.expanded_df["lcl_name"].unique() if normalize_text(x)})
        self.device_var = tk.StringVar(value=self.devices[0] if self.devices else "")
        self.measurement_var = tk.StringVar()
        self.context_var = tk.StringVar(value="ALL")
        self.flag_category_var = tk.StringVar(value=DEFAULT_FLAG_CATEGORIES[0])
        self.visible_serials_by_device: Dict[str, set[str]] = {}
        self.show_duplicates_only_var = tk.BooleanVar(value=False)

        self._build_ui()
        self._refresh_measurement_options()
        self.load_refresh_plot()

    def _build_ui(self) -> None:
        top = ttk.Frame(self.root, padding=8)
        top.pack(side=tk.TOP, fill=tk.X)

        ttk.Label(top, text="lcl_name").pack(side=tk.LEFT)
        self.device_combo = ttk.Combobox(top, textvariable=self.device_var, state="readonly", width=18)
        self.device_combo["values"] = self.devices
        self.device_combo.pack(side=tk.LEFT, padx=5)
        self.device_combo.bind("<<ComboboxSelected>>", self.on_device_changed)

        ttk.Label(top, text="logical measurement").pack(side=tk.LEFT, padx=(15, 0))
        self.measurement_combo = ttk.Combobox(top, textvariable=self.measurement_var, state="readonly", width=30)
        self.measurement_combo.pack(side=tk.LEFT, padx=5)
        self.measurement_combo.bind("<<ComboboxSelected>>", self.on_measurement_changed)

        ttk.Label(top, text="context / prąd").pack(side=tk.LEFT, padx=(15, 0))
        self.context_combo = ttk.Combobox(top, textvariable=self.context_var, state="disabled", width=24)
        self.context_combo.pack(side=tk.LEFT, padx=5)
        self.context_combo.bind("<<ComboboxSelected>>", self.on_context_changed)

        self.duplicates_only_check = ttk.Checkbutton(
            top,
            text="Only duplicated measurements",
            variable=self.show_duplicates_only_var,
            command=self.on_filter_controls_changed,
        )
        self.duplicates_only_check.pack(side=tk.LEFT, padx=(15, 5))

        ttk.Button(top, text="Load / Refresh plot", command=self.load_refresh_plot).pack(side=tk.LEFT, padx=10)

        main = ttk.Panedwindow(self.root, orient=tk.HORIZONTAL)
        main.pack(fill=tk.BOTH, expand=True)

        left = ttk.Frame(main, padding=8)
        right = ttk.Frame(main, padding=8)
        main.add(left, weight=4)
        main.add(right, weight=2)

        self.figure_frame = ttk.Frame(left)
        self.figure_frame.pack(fill=tk.BOTH, expand=True)

        info_box = ttk.LabelFrame(right, text="Selected record", padding=6)
        info_box.pack(fill=tk.BOTH, expand=False)

        self.info_text = tk.Text(info_box, height=18, width=50, wrap="word")
        self.info_text.pack(fill=tk.BOTH, expand=True)

        visible_sn_box = ttk.LabelFrame(right, text="Visible serial numbers", padding=6)
        visible_sn_box.pack(fill=tk.BOTH, expand=False, pady=(8, 0))

        ttk.Label(
            visible_sn_box,
            text="Zaznaczone SN będą widoczne. Lista pokazuje tylko SN możliwe dla aktualnego pomiaru/filtra duplikatów.",
        ).pack(anchor="w")

        self.visible_sn_listbox = tk.Listbox(
            visible_sn_box,
            selectmode=tk.MULTIPLE,
            height=7,
            exportselection=False,
        )
        self.visible_sn_listbox.pack(fill=tk.BOTH, expand=True, pady=(4, 0))

        visible_sn_btns = ttk.Frame(visible_sn_box)
        visible_sn_btns.pack(fill=tk.X, pady=(6, 0))
        ttk.Button(visible_sn_btns, text="Apply visible SN", command=self.apply_visible_serial_selection).pack(side=tk.LEFT)
        ttk.Button(visible_sn_btns, text="Select all SN", command=self.select_all_visible_serials).pack(side=tk.LEFT, padx=(6, 0))
        ttk.Button(visible_sn_btns, text="Clear SN", command=self.clear_visible_serial_selection).pack(side=tk.LEFT, padx=(6, 0))

        flag_box = ttk.LabelFrame(right, text="Flagging", padding=6)
        flag_box.pack(fill=tk.X, pady=(8, 0))

        ttk.Label(flag_box, text="Category").grid(row=0, column=0, sticky="w")
        self.flag_combo = ttk.Combobox(flag_box, textvariable=self.flag_category_var, state="readonly", width=20)
        self.flag_combo["values"] = DEFAULT_FLAG_CATEGORIES
        self.flag_combo.grid(row=0, column=1, sticky="ew", padx=5)
        ttk.Button(flag_box, text="Flag selected record", command=self.flag_selected_record).grid(row=1, column=0, columnspan=2, sticky="ew", pady=(6, 0))

        flagged_box = ttk.LabelFrame(right, text="Flagged points", padding=6)
        flagged_box.pack(fill=tk.BOTH, expand=True, pady=(8, 0))

        self.flagged_listbox = tk.Listbox(flagged_box, height=12)
        self.flagged_listbox.pack(fill=tk.BOTH, expand=True)
        self.flagged_listbox.bind("<<ListboxSelect>>", self.on_flagged_list_select)

        flagged_btns = ttk.Frame(flagged_box)
        flagged_btns.pack(fill=tk.X, pady=(6, 0))
        ttk.Button(flagged_btns, text="Remove selected flag", command=self.remove_selected_flag).pack(side=tk.LEFT)
        ttk.Button(flagged_btns, text="Save flagged points and exit", command=self.save_and_exit).pack(side=tk.RIGHT)

        table_box = ttk.LabelFrame(right, text="fault_and_latch records", padding=6)
        table_box.pack(fill=tk.BOTH, expand=True, pady=(8, 0))

        cols = ("SN", "stage", "metric", "value", "source_dir_name")
        self.table = ttk.Treeview(table_box, columns=cols, show="headings", height=10)
        for col in cols:
            self.table.heading(col, text=col)
            self.table.column(col, width=100, anchor="w")
        self.table.pack(fill=tk.BOTH, expand=True)
        self.table.bind("<<TreeviewSelect>>", self.on_table_select)

        self.table_uid_map: Dict[str, str] = {}

    def _refresh_measurement_options(self) -> None:
        device = normalize_text(self.device_var.get())
        logical_map = get_logical_measurement_map(self.config, device)
        measurements = sorted(logical_map.keys())
        self.measurement_combo["values"] = measurements
        if measurements:
            current = normalize_text(self.measurement_var.get())
            if current not in measurements:
                self.measurement_var.set(measurements[0])
        else:
            self.measurement_var.set("")
        self._refresh_context_options()
        self._refresh_visible_serial_options()

    def _refresh_context_options(self) -> None:
        """Refresh extra context selector for measurements like rdson or ilim_accuracy."""
        device = normalize_text(self.device_var.get())
        logical_name = normalize_text(self.measurement_var.get())
        contexts = get_logical_measurement_contexts(self.config, device, logical_name)

        if contexts:
            values = ["ALL"] + contexts
            self.context_combo["values"] = values
            self.context_combo.configure(state="readonly")
            current = normalize_context_key(self.context_var.get()) or "ALL"
            if current not in values:
                current = values[0]
            self.context_var.set(current)
        else:
            self.context_combo["values"] = []
            self.context_combo.configure(state="disabled")
            self.context_var.set("ALL")


    def _records_for_visible_serial_options(self) -> pd.DataFrame:
        """Return records used only to build the visible-SN listbox.

        This intentionally ignores the current visible-SN selection, so SN values can
        reappear in the list and be selected again. It respects the currently selected
        device, logical measurement and the "Only duplicated measurements" checkbox.
        """
        device = normalize_text(self.device_var.get())
        logical_name = normalize_text(self.measurement_var.get())
        if not device or self.expanded_df.empty:
            return pd.DataFrame()

        selected_context = normalize_context_key(self.context_var.get())
        if logical_name:
            records = extract_plot_records(
                self.expanded_df,
                device,
                logical_name,
                selected_context=selected_context,
            )
        else:
            records = self.expanded_df.loc[self.expanded_df["lcl_name"] == device].copy()
            if not records.empty:
                records["context_key_norm"] = records["context_key"].map(normalize_context_key)
                records["value_numeric"] = pd.to_numeric(records["value"], errors="coerce")

        if self.show_duplicates_only_var.get():
            records = filter_duplicate_measurement_records(records)

        return records

    def _available_serials_for_current_device(self) -> List[str]:
        """Return SN values available for current device/measurement/filter settings."""
        records = self._records_for_visible_serial_options()
        if records.empty or "lcl_serial_number" not in records.columns:
            return []

        serials = {
            normalize_serial_number(sn)
            for sn in records["lcl_serial_number"].tolist()
            if normalize_serial_number(sn)
        }
        return sorted(serials, key=lambda sn: int(sn[2:]) if sn.startswith("SN") and sn[2:].isdigit() else 10**9)

    def _refresh_visible_serial_options(self) -> None:
        """Refresh the listbox used to choose visible serial numbers."""
        if not hasattr(self, "visible_sn_listbox"):
            return

        device = normalize_text(self.device_var.get())
        serials = self._available_serials_for_current_device()

        if device not in self.visible_serials_by_device:
            self.visible_serials_by_device[device] = set(serials)

        visible_for_device = self.visible_serials_by_device.get(device, set(serials))

        self.visible_sn_listbox.delete(0, tk.END)
        for idx, serial in enumerate(serials):
            dose = serial_to_dose(serial)
            label = f"{serial} ({dose})" if dose and dose != "UNKNOWN" else serial
            self.visible_sn_listbox.insert(tk.END, label)
            if serial in visible_for_device:
                self.visible_sn_listbox.selection_set(idx)

    def _selected_visible_serials_from_listbox(self) -> set[str]:
        """Read selected SN values from the visible-SN listbox."""
        selected: set[str] = set()
        if not hasattr(self, "visible_sn_listbox"):
            return selected

        for idx in self.visible_sn_listbox.curselection():
            label = self.visible_sn_listbox.get(idx)
            serial = label.split(" ", 1)[0]
            serial = normalize_serial_number(serial)
            if serial:
                selected.add(serial)
        return selected

    def apply_visible_serial_selection(self) -> None:
        """Apply selected visible SN values and refresh the current view."""
        device = normalize_text(self.device_var.get())
        if not device:
            return
        self.visible_serials_by_device[device] = self._selected_visible_serials_from_listbox()
        self.load_refresh_plot()

    def select_all_visible_serials(self) -> None:
        """Select all SN values for the current device and refresh the view."""
        device = normalize_text(self.device_var.get())
        serials = self._available_serials_for_current_device()
        if device:
            self.visible_serials_by_device[device] = set(serials)
        if hasattr(self, "visible_sn_listbox"):
            self.visible_sn_listbox.selection_set(0, tk.END)
        self.load_refresh_plot()

    def clear_visible_serial_selection(self) -> None:
        """Clear visible SN selection and refresh the view; this hides all SN values."""
        device = normalize_text(self.device_var.get())
        if device:
            self.visible_serials_by_device[device] = set()
        if hasattr(self, "visible_sn_listbox"):
            self.visible_sn_listbox.selection_clear(0, tk.END)
        self.load_refresh_plot()

    def get_visible_serials_for_current_device(self) -> set[str]:
        """Return selected visible SN values for the current device."""
        device = normalize_text(self.device_var.get())
        serials = self._available_serials_for_current_device()
        if device not in self.visible_serials_by_device:
            self.visible_serials_by_device[device] = set(serials)
        return set(self.visible_serials_by_device.get(device, set(serials)))

    def on_measurement_changed(self, event: Any = None) -> None:
        """Handle logical measurement change."""
        self._refresh_context_options()
        self._refresh_visible_serial_options()
        self.load_refresh_plot()

    def on_context_changed(self, event: Any = None) -> None:
        """Handle context/current selector change."""
        self._refresh_visible_serial_options()
        self.load_refresh_plot()

    def on_filter_controls_changed(self, event: Any = None) -> None:
        """Refresh dependent controls after changing a filter checkbox."""
        self._refresh_visible_serial_options()
        self.load_refresh_plot()

    def on_device_changed(self, event: Any = None) -> None:
        """Handle lcl_name change."""
        self._refresh_measurement_options()
        self._refresh_visible_serial_options()
        self.load_refresh_plot()

    def clear_figure(self) -> None:
        """Destroy old canvas and toolbar."""
        for child in self.figure_frame.winfo_children():
            child.destroy()
        self.current_canvas = None
        self.current_toolbar = None

    def load_refresh_plot(self) -> None:
        """Load currently selected device/measurement into the GUI."""
        self.clear_figure()
        self.table.delete(*self.table.get_children())
        self.table_uid_map.clear()

        device = normalize_text(self.device_var.get())
        logical_name = normalize_text(self.measurement_var.get())
        if not device or not logical_name:
            self._set_info_text("No device or measurement selected.")
            return

        selected_context = normalize_context_key(self.context_var.get())
        records = extract_plot_records(
            self.expanded_df,
            device,
            logical_name,
            selected_context=selected_context,
        )
        visible_serials = self.get_visible_serials_for_current_device()
        records = filter_visible_serial_records(records, visible_serials)

        if self.show_duplicates_only_var.get():
            records = filter_duplicate_measurement_records(records)

        if logical_name == "fault_and_latch":
            self._populate_fault_and_latch_table(records)
            visible_serials_sorted = sorted(self.get_visible_serials_for_current_device())
            visible_info = f" Visible SN: {', '.join(visible_serials_sorted)}." if visible_serials_sorted else " No SN selected."
            duplicate_info = " Showing only duplicated records." if self.show_duplicates_only_var.get() else ""
            self._set_info_text("fault_and_latch is displayed as a table. Select a row to inspect and flag." + visible_info + duplicate_info)
            return

        fig = create_measurement_figure(
            records,
            self.config,
            device,
            logical_name,
            self.flagged_points,
            selected_context=selected_context,
        )

        canvas = FigureCanvasTkAgg(fig, master=self.figure_frame)
        canvas.mpl_connect("pick_event", self.on_pick_point)
        canvas.draw()
        toolbar = NavigationToolbar2Tk(canvas, self.figure_frame, pack_toolbar=False)
        toolbar.update()
        toolbar.pack(side=tk.TOP, fill=tk.X)
        canvas.get_tk_widget().pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        self.current_canvas = canvas
        self.current_toolbar = toolbar

        if records.empty:
            if self.show_duplicates_only_var.get():
                self._set_info_text("No duplicated measurement records for the selected SN/filter settings.")
            else:
                self._set_info_text("No data for the selected measurement and selected SN filters.")
        else:
            if self.show_duplicates_only_var.get():
                self._set_info_text("Showing only duplicated measurement records. Click a point to inspect a record.")
            else:
                self._set_info_text("Click a point to inspect a record.")

    def _populate_fault_and_latch_table(self, records: pd.DataFrame) -> None:
        """Populate the table for non-numeric fault_and_latch data."""
        if records.empty:
            return
        for _, row in records.iterrows():
            uid = normalize_text(row["__record_uid"])
            values = (
                normalize_text(row["lcl_serial_number"]),
                normalize_text(row["irradiation_stage"]),
                normalize_text(row["metric"]),
                normalize_text(row["value"]),
                normalize_text(row["source_dir_name"]),
            )
            item_id = self.table.insert("", tk.END, values=values)
            self.table_uid_map[item_id] = uid

    def _set_info_text(self, text: str) -> None:
        self.info_text.delete("1.0", tk.END)
        self.info_text.insert("1.0", text)

    def find_record_by_uid(self, uid: str) -> Optional[Dict[str, Any]]:
        """Return a record as dict by uid."""
        rows = self.expanded_df.loc[self.expanded_df["__record_uid"] == uid]
        if rows.empty:
            return None
        return rows.iloc[0].to_dict()

    def on_pick_point(self, event: Any) -> None:
        """Handle matplotlib pick event."""
        artist = event.artist
        uids = getattr(artist, "_record_uids", None)
        inds = getattr(event, "ind", None)
        if uids is None or len(uids) == 0:
            return
        if inds is None or len(inds) == 0:
            return
        idx = int(inds[0])
        if idx >= len(uids):
            return
        uid = normalize_text(uids[idx])
        record = self.find_record_by_uid(uid)
        if not record:
            return
        self.selected_record = record
        self._set_info_text(build_record_details(record))

    def on_table_select(self, event: Any) -> None:
        """Handle selection in fault_and_latch table."""
        selected = self.table.selection()
        if not selected:
            return
        item_id = selected[0]
        uid = self.table_uid_map.get(item_id, "")
        record = self.find_record_by_uid(uid)
        if not record:
            return
        self.selected_record = record
        self._set_info_text(build_record_details(record))

    def flag_selected_record(self) -> None:
        """Flag the currently selected record."""
        if not self.selected_record:
            messagebox.showwarning("No selection", "Select a point or table row first.")
            return
        uid = normalize_text(self.selected_record["__record_uid"])
        category = normalize_text(self.flag_category_var.get()) or DEFAULT_FLAG_CATEGORIES[0]
        self.flagged_points[uid] = {
            "record": self.selected_record.copy(),
            "flag_category": category,
        }
        self.refresh_flagged_list()
        self.load_refresh_plot()

    def refresh_flagged_list(self) -> None:
        """Refresh flagged listbox."""
        self.flagged_listbox.delete(0, tk.END)
        for uid, entry in self.flagged_points.items():
            row = entry["record"]
            category = entry["flag_category"]
            label = (
                f"{category} | {normalize_text(row.get('lcl_name'))} | "
                f"{normalize_text(row.get('lcl_serial_number'))} | "
                f"{normalize_text(row.get('irradiation_stage'))} | "
                f"{normalize_text(row.get('metric'))} | "
                f"{normalize_text(row.get('context_key'))} | "
                f"{normalize_text(row.get('value'))}"
            )
            self.flagged_listbox.insert(tk.END, f"{uid} || {label}")

    def on_flagged_list_select(self, event: Any) -> None:
        """Show flagged record details after selecting it in the list."""
        selection = self.flagged_listbox.curselection()
        if not selection:
            return
        value = self.flagged_listbox.get(selection[0])
        uid = value.split(" || ", 1)[0]
        entry = self.flagged_points.get(uid)
        if not entry:
            return
        self.selected_record = entry["record"]
        self._set_info_text(build_record_details(entry["record"]) + f"\nflag_category: {entry['flag_category']}")

    def remove_selected_flag(self) -> None:
        """Remove selected flagged point."""
        selection = self.flagged_listbox.curselection()
        if not selection:
            messagebox.showwarning("No selection", "Select a flagged point from the list first.")
            return
        value = self.flagged_listbox.get(selection[0])
        uid = value.split(" || ", 1)[0]
        if uid in self.flagged_points:
            del self.flagged_points[uid]
        self.refresh_flagged_list()
        self.load_refresh_plot()

    def save_and_exit(self) -> None:
        """Save flagged points and close."""
        output_path = self.output_dir / "flagged_points.txt"
        save_flagged_points(self.flagged_points, output_path)
        self.root.destroy()

    def on_close(self) -> None:
        """Handle window close."""
        self.save_and_exit()


def main() -> None:
    """Entry point."""
    args = parse_args()
    setup_logging(args.verbose)

    input_dir = Path(args.input_dir)
    config_yaml = Path(args.config_yaml)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        config = load_yaml_config(config_yaml)
    except Exception as exc:
        logging.exception("Failed to load YAML config")
        print(f"ERROR: failed to load YAML config: {exc}", file=sys.stderr)
        sys.exit(1)

    loaded_files, warnings = load_flat_files(input_dir)
    for warning in warnings:
        logging.warning(warning)

    missing_report_path = output_dir / "missing_measurements.txt"
    flagged_report_path = output_dir / "flagged_points.txt"

    if not loaded_files:
        build_missing_report(
            [
                {
                    "flat_file": "",
                    "lcl_name": "",
                    "lcl_serial_number": "",
                    "irradiation_stage": "",
                    "logical_measurement_name": "",
                    "source_measurement_type": "",
                    "missing_metric": "",
                    "missing_context_key": "",
                    "note": "No valid flat CSV files loaded",
                }
            ],
            missing_report_path,
        )
        save_flagged_points({}, flagged_report_path)
        print("No valid flat CSV files loaded. Reports were created.")
        return

    missing_items = check_missing_measurements(loaded_files, config)
    missing_items = filter_missing_measurements(missing_items, config)
    build_missing_report(missing_items, missing_report_path)

    all_df = pd.concat([item.df for item in loaded_files], ignore_index=True)
    expanded_df = expand_logical_measurements(all_df, config)

    if expanded_df.empty:
        save_flagged_points({}, flagged_report_path)
        print("No plottable or mappable records were found after loading. Reports were created.")
        return

    root = tk.Tk()
    app = ReviewApp(root=root, config=config, expanded_df=expanded_df, output_dir=output_dir)
    app.refresh_flagged_list()
    root.mainloop()


if __name__ == "__main__":
    main()