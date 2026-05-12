"""Load and normalize flat CSV files produced by ``make_flat_files.py``.

The loader walks ``flat_files_dir``, reads every ``*_flat.csv`` (skipping
``_flat_index.csv``), concatenates them into a single tidy DataFrame, and
annotates each row with the dose of its serial number.

Powtórzenia w tym samym (SN, stage, measurement_type, metric, context_key)
sa uśredniane do jednego wiersza zgodnie z założeniami projektu.
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pandas as pd


logger = logging.getLogger(__name__)


# Columns we rely on downstream. If a flat CSV is missing any of these,
# we log a warning and drop that file rather than crashing.
REQUIRED_COLUMNS = [
    "lcl_name",
    "lcl_serial_number",
    "measurement_type",
    "metric",
    "value",
    "unit",
    "context_key",
    "irradiation_stage",
    "data_origin",
]

# Key uniquely identifying one logical measurement point per device per stage.
GROUP_KEY = [
    "lcl_name",
    "lcl_serial_number",
    "measurement_type",
    "metric",
    "context_key",
    "irradiation_stage",
]


def _read_one_flat_file(path: Path) -> pd.DataFrame | None:
    """Read one flat CSV. Returns ``None`` on error so we can skip it."""
    encodings = ["utf-8-sig", "utf-8", "cp1250", "latin1"]
    for enc in encodings:
        try:
            df = pd.read_csv(path, encoding=enc)
            break
        except UnicodeDecodeError:
            continue
        except pd.errors.EmptyDataError:
            logger.warning("Skipping empty flat file: %s", path)
            return None
        except Exception as exc:
            logger.warning("Failed to read %s: %s", path, exc)
            return None
    else:
        logger.warning("Could not decode %s with any tried encoding", path)
        return None

    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        logger.warning(
            "Skipping %s - missing required columns: %s", path, missing
        )
        return None

    return df


def load_flat_files(flat_files_dir: Path) -> pd.DataFrame:
    """Read every ``*_flat.csv`` from ``flat_files_dir`` and concatenate.

    Returns a tidy long-format DataFrame with the columns from
    ``REQUIRED_COLUMNS`` plus a numeric ``value_num`` column (rows whose
    value could not be parsed as a number are dropped, with a count log).
    """
    flat_files_dir = Path(flat_files_dir)
    if not flat_files_dir.is_dir():
        raise FileNotFoundError(
            f"flat_files_dir does not exist or is not a directory: {flat_files_dir}"
        )

    # Sorted for deterministic concat order.
    csv_paths = sorted(
        p
        for p in flat_files_dir.glob("*_flat.csv")
        if p.name != "_flat_index.csv"
    )
    if not csv_paths:
        raise FileNotFoundError(
            f"No *_flat.csv files found in {flat_files_dir}"
        )

    logger.info("Reading %d flat CSV files from %s", len(csv_paths), flat_files_dir)

    frames: list[pd.DataFrame] = []
    for path in csv_paths:
        df = _read_one_flat_file(path)
        if df is None:
            continue
        frames.append(df)

    if not frames:
        raise RuntimeError("No flat CSV files could be loaded successfully")

    combined = pd.concat(frames, ignore_index=True, sort=False)
    logger.info("Loaded %d rows total before normalisation", len(combined))

    # Normalise key string columns: strip whitespace, fillna with empty string.
    for col in (
        "lcl_name",
        "lcl_serial_number",
        "measurement_type",
        "metric",
        "context_key",
        "irradiation_stage",
        "unit",
        "data_origin",
    ):
        combined[col] = (
            combined[col].astype("object").where(combined[col].notna(), "")
        )
        combined[col] = combined[col].astype(str).str.strip()

    # Numeric coercion: keep original value column for diagnostics,
    # but build value_num for plotting.
    combined["value_num"] = pd.to_numeric(combined["value"], errors="coerce")
    not_numeric = combined["value_num"].isna().sum()
    if not_numeric:
        logger.info(
            "Dropping %d rows whose `value` could not be parsed as a number",
            int(not_numeric),
        )
        combined = combined[combined["value_num"].notna()].copy()

    # Drop rows lacking identity.
    bad_identity = combined["lcl_name"].eq("") | combined["lcl_serial_number"].eq("")
    if bad_identity.any():
        logger.warning(
            "Dropping %d rows with empty lcl_name or lcl_serial_number",
            int(bad_identity.sum()),
        )
        combined = combined[~bad_identity].copy()

    return combined.reset_index(drop=True)


def attach_dose(df: pd.DataFrame, dose_map: dict[str, float]) -> pd.DataFrame:
    """Add a numeric ``dose_krad`` column based on the dose map.

    Rows whose serial number is not in the dose map get NaN and a one-line
    warning so the user knows their config is incomplete.
    """
    df = df.copy()
    df["dose_krad"] = df["lcl_serial_number"].map(dose_map)

    missing_mask = df["dose_krad"].isna()
    if missing_mask.any():
        unknown = sorted(df.loc[missing_mask, "lcl_serial_number"].unique())
        logger.warning(
            "%d rows have no dose mapping. Unknown SNs: %s",
            int(missing_mask.sum()),
            unknown,
        )
    return df


def average_repeats(df: pd.DataFrame) -> pd.DataFrame:
    """Collapse repeated measurements of the same point to a single mean row.

    A "point" is identified by ``GROUP_KEY`` (device + SN + measurement type
    + metric + context + stage). When several rows share the same key, we
    average ``value_num`` and keep the first occurrence of all other columns.
    """
    if df.empty:
        return df

    # Drop ``value`` and ``timestamp``-like columns from the aggregation - they
    # don't aggregate cleanly. Keep just the columns we need downstream.
    keep_cols = [
        c
        for c in df.columns
        if c
        in {
            *GROUP_KEY,
            "value_num",
            "unit",
            "data_origin",
            "dose_krad",
        }
    ]
    df_small = df[keep_cols].copy()

    grouped = df_small.groupby(GROUP_KEY, as_index=False, sort=False)
    sizes = grouped.size()
    n_collapsed = int((sizes["size"] > 1).sum())
    if n_collapsed:
        logger.info(
            "Averaging repeats: %d (SN, stage, metric) groups had >1 measurement",
            n_collapsed,
        )

    agg = grouped.agg(
        value_num=("value_num", "mean"),
        unit=("unit", "first"),
        data_origin=("data_origin", "first"),
        dose_krad=("dose_krad", "first"),
        n_repeats=("value_num", "size"),
    )
    return agg


def build_master_dataframe(
    flat_files_dir: Path,
    dose_map: dict[str, float],
) -> pd.DataFrame:
    """High-level helper used by the CLI.

    Loads everything, attaches dose, collapses repeats. The returned frame
    is the canonical "tidy" dataset that the plotters slice into.
    """
    raw = load_flat_files(flat_files_dir)
    with_dose = attach_dose(raw, dose_map)
    averaged = average_repeats(with_dose)
    logger.info(
        "Master dataframe ready: %d unique measurement points across "
        "%d devices and %d serial numbers",
        len(averaged),
        averaged["lcl_name"].nunique(),
        averaged["lcl_serial_number"].nunique(),
    )
    return averaged


def filter_for_plot(
    df: pd.DataFrame,
    *,
    lcl_name: str,
    measurement_type: str,
    metric: str,
    context_key: str | None = None,
    stages: list[str] | None = None,
    exclude_sn: list[str] | None = None,
    include_doses: list[float] | None = None,
    exclude_doses: list[float] | None = None,
    exclude_reference: list[str] | None = None,
) -> pd.DataFrame:
    """Slice the master dataframe according to the plot spec.

    All filters are AND-ed. ``context_key`` of ``None`` means "any" - useful
    for metrics that don't have variants (``iq_ua``, ``ish_ua``, ...).
    If ``context_key`` is given but does not match anything, the result is
    an empty frame and the caller is expected to log/skip.
    """
    mask = (
        (df["lcl_name"] == lcl_name)
        & (df["measurement_type"] == measurement_type)
        & (df["metric"] == metric)
    )
    if context_key is not None:
        mask &= df["context_key"] == context_key

    if stages:
        mask &= df["irradiation_stage"].isin(stages)

    if exclude_sn:
        mask &= ~df["lcl_serial_number"].isin(exclude_sn)

    if exclude_reference:
        mask &= ~df["lcl_serial_number"].isin(exclude_reference)

    if include_doses is not None:
        mask &= df["dose_krad"].isin(include_doses)

    if exclude_doses:
        mask &= ~df["dose_krad"].isin(exclude_doses)

    return df[mask].copy()


def compute_stats_by_dose(
    df: pd.DataFrame,
    stage: str | None = None,
) -> pd.DataFrame:
    """Aggregate min/max/mean/median over serial numbers per dose.

    If ``stage`` is given, restrict to that stage first. Returns one row
    per dose with columns ``dose_krad``, ``min``, ``max``, ``mean``,
    ``median``, ``n``.
    """
    if stage is not None:
        df = df[df["irradiation_stage"] == stage]
    if df.empty:
        return pd.DataFrame(
            columns=["dose_krad", "min", "max", "mean", "median", "n"]
        )
    grouped = df.groupby("dose_krad", sort=True)["value_num"]
    stats = grouped.agg(["min", "max", "mean", "median", "size"]).reset_index()
    stats = stats.rename(columns={"size": "n"})
    return stats


def compute_deltas(
    df: pd.DataFrame,
    *,
    delta_from: str,
    delta_to: str,
    mode: str = "absolute",
) -> pd.DataFrame:
    """Per-SN delta between two stages.

    Returns a frame with one row per SN that had measurements in both stages.
    Columns: ``lcl_serial_number``, ``dose_krad``, ``value_from``,
    ``value_to``, ``delta``.
    """
    sub = df[df["irradiation_stage"].isin([delta_from, delta_to])]
    if sub.empty:
        return pd.DataFrame(
            columns=[
                "lcl_serial_number",
                "dose_krad",
                "value_from",
                "value_to",
                "delta",
            ]
        )

    pivot = sub.pivot_table(
        index=["lcl_serial_number", "dose_krad"],
        columns="irradiation_stage",
        values="value_num",
        aggfunc="first",
    ).reset_index()

    # Only keep SNs that have BOTH stages available.
    if delta_from not in pivot.columns or delta_to not in pivot.columns:
        return pd.DataFrame(
            columns=[
                "lcl_serial_number",
                "dose_krad",
                "value_from",
                "value_to",
                "delta",
            ]
        )

    pivot = pivot.dropna(subset=[delta_from, delta_to])
    pivot = pivot.rename(
        columns={delta_from: "value_from", delta_to: "value_to"}
    )

    if mode == "absolute":
        pivot["delta"] = pivot["value_to"] - pivot["value_from"]
    elif mode == "relative_percent":
        # Avoid division by zero - rows where value_from == 0 get NaN.
        with np.errstate(divide="ignore", invalid="ignore"):
            pivot["delta"] = np.where(
                pivot["value_from"] != 0,
                100.0 * (pivot["value_to"] - pivot["value_from"]) / pivot["value_from"],
                np.nan,
            )
    else:
        raise ValueError(f"Unknown delta mode: {mode!r}")

    return pivot[
        ["lcl_serial_number", "dose_krad", "value_from", "value_to", "delta"]
    ].copy()
