"""Configuration loading and validation.

Two YAML files are involved:

* ``dose_map.yaml`` - maps each ``SNxx`` to its absorbed dose in kRad and
  optionally declares the list of reference (control) samples.

* ``plot_config.yaml`` - top-level ``data`` / ``defaults`` / ``plots``
  sections. Each plot entry inherits ``defaults`` and may declare
  ``variants`` to emit multiple PNGs from one entry.

The loader:

* Validates required keys early so the user gets a clear failure before
  any plotting starts.
* Resolves variants into a flat list of fully merged plot specs.
* Returns plain ``dict`` objects rather than dataclasses to keep things
  hackable - the YAML schema is intentionally open and a downstream
  plotter can pick up extra keys without changes here.
"""

from __future__ import annotations

import copy
import logging
from pathlib import Path
from typing import Any

import yaml


logger = logging.getLogger(__name__)


# Plot types that the rest of the package knows how to render.
SUPPORTED_PLOT_TYPES = {"absolute", "delta", "annealing"}

# Statistic line names accepted in `show_lines`.
SUPPORTED_STAT_LINES = {"min", "max", "mean", "median"}

# Axis scales accepted on `x_scale` / `y_scale`.
SUPPORTED_AXIS_SCALES = {"linear", "log", "symlog"}

# Tokens accepted in `split_by`. The plot is expanded into one PNG per
# combination of values for the listed dimensions.
SUPPORTED_SPLIT_DIMS = {"lot", "bias"}

# Required fields per plot type. Extra fields are allowed (and ignored
# here - the plotter is responsible for picking them up).
REQUIRED_FIELDS_BY_TYPE: dict[str, set[str]] = {
    "absolute": {"lcl_name", "measurement_type", "metric", "stages"},
    "delta": {
        "lcl_name",
        "measurement_type",
        "metric",
        "delta_from",
        "delta_to",
    },
    "annealing": {"lcl_name", "measurement_type", "metric", "stages_order"},
}


class ConfigError(ValueError):
    """Raised when a config file is missing required information."""


# ---------------------------------------------------------------------------
# Dose map
# ---------------------------------------------------------------------------


def _normalize_sn(value: Any) -> str:
    """Return ``SNxx`` form for a serial number written in any common style."""
    if value is None:
        return ""
    text = str(value).strip().upper()
    if not text:
        return ""
    if text.startswith("SN"):
        digits = text[2:].lstrip("_- ")
        try:
            return f"SN{int(digits):02d}"
        except ValueError:
            return text
    try:
        return f"SN{int(text):02d}"
    except ValueError:
        return text


def load_dose_map(
    path: Path,
) -> tuple[dict[str, float], list[str], dict[str, str], dict[str, str]]:
    """Load the dose map.

    Returns
    -------
    (doses, reference_sns, lot_by_sn, bias_by_sn)
        ``doses`` maps ``SNxx`` to absorbed dose in kRad.
        ``reference_sns`` is the list of SNs that should be treated as
        controls (excluded from absolute/delta plots, plotted separately
        on annealing plots).
        ``lot_by_sn`` maps ``SNxx`` to lot name (e.g. ``"A"`` / ``"B"``).
        Missing SNs are simply absent from the mapping.
        ``bias_by_sn`` maps ``SNxx`` to bias group (e.g. ``"bias"`` /
        ``"unbias"``).
    """
    path = Path(path)
    if not path.is_file():
        raise ConfigError(f"Dose map file not found: {path}")

    with path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    if not isinstance(raw, dict):
        raise ConfigError(f"Dose map root must be a mapping, got {type(raw).__name__}")

    has_doses = "doses" in raw
    has_groups = "dose_groups" in raw

    if has_doses and has_groups:
        raise ConfigError(
            "Dose map cannot define both `doses` and `dose_groups`. Pick one."
        )
    if not (has_doses or has_groups):
        raise ConfigError(
            "Dose map must define either `doses` (SN -> kRad) "
            "or `dose_groups` (kRad -> list of SN)."
        )

    doses: dict[str, float] = {}

    if has_doses:
        doses_raw = raw["doses"] or {}
        if not isinstance(doses_raw, dict):
            raise ConfigError("`doses` must be a mapping of SN -> dose")
        for sn_raw, dose_raw in doses_raw.items():
            sn = _normalize_sn(sn_raw)
            if not sn:
                raise ConfigError(f"Invalid serial number in dose map: {sn_raw!r}")
            try:
                dose = float(dose_raw)
            except (TypeError, ValueError) as exc:
                raise ConfigError(
                    f"Dose for {sn} is not a number: {dose_raw!r}"
                ) from exc
            doses[sn] = dose
    else:
        groups_raw = raw["dose_groups"] or {}
        if not isinstance(groups_raw, dict):
            raise ConfigError("`dose_groups` must be a mapping of dose -> [SN, ...]")
        for dose_raw, sn_list in groups_raw.items():
            try:
                dose = float(dose_raw)
            except (TypeError, ValueError) as exc:
                raise ConfigError(
                    f"Dose key is not a number: {dose_raw!r}"
                ) from exc
            if not isinstance(sn_list, list):
                raise ConfigError(
                    f"Value for dose {dose_raw} must be a list, "
                    f"got {type(sn_list).__name__}"
                )
            for sn_raw in sn_list:
                sn = _normalize_sn(sn_raw)
                if not sn:
                    raise ConfigError(f"Invalid SN in dose_groups: {sn_raw!r}")
                if sn in doses and doses[sn] != dose:
                    raise ConfigError(
                        f"{sn} appears in two different dose groups "
                        f"({doses[sn]} and {dose})"
                    )
                doses[sn] = dose

    # Reference SNs: explicit list or auto-detect via dose == 0.
    ref_raw = raw.get("reference_sns")
    if ref_raw is None:
        reference_sns = sorted(sn for sn, d in doses.items() if d == 0)
    else:
        if not isinstance(ref_raw, list):
            raise ConfigError("`reference_sns` must be a list of SNs")
        reference_sns = []
        for sn_raw in ref_raw:
            sn = _normalize_sn(sn_raw)
            if not sn:
                raise ConfigError(f"Invalid SN in reference_sns: {sn_raw!r}")
            if sn not in doses:
                raise ConfigError(
                    f"reference_sns contains {sn} but it has no entry in `doses`"
                )
            reference_sns.append(sn)
        reference_sns = sorted(set(reference_sns))

    # Lots: optional mapping lot name -> [SN, ...]. Inverted to lot_by_sn
    # so we can build a single column.
    lot_by_sn: dict[str, str] = {}
    lots_raw = raw.get("lots")
    if lots_raw is not None:
        if not isinstance(lots_raw, dict):
            raise ConfigError("`lots` must be a mapping of lot_name -> [SN, ...]")
        for lot_name, sn_list in lots_raw.items():
            lot_str = str(lot_name).strip()
            if not lot_str:
                raise ConfigError("Lot name in `lots` must be non-empty")
            if not isinstance(sn_list, list):
                raise ConfigError(
                    f"Lot '{lot_str}': value must be a list, got "
                    f"{type(sn_list).__name__}"
                )
            for sn_raw in sn_list:
                sn = _normalize_sn(sn_raw)
                if not sn:
                    raise ConfigError(f"Invalid SN in lots[{lot_str}]: {sn_raw!r}")
                if sn in lot_by_sn and lot_by_sn[sn] != lot_str:
                    raise ConfigError(
                        f"{sn} is assigned to two different lots "
                        f"('{lot_by_sn[sn]}' and '{lot_str}')"
                    )
                lot_by_sn[sn] = lot_str

    # Bias groups: optional mapping bias_name -> [SN, ...].
    bias_by_sn: dict[str, str] = {}
    bias_raw = raw.get("bias_groups")
    if bias_raw is not None:
        if not isinstance(bias_raw, dict):
            raise ConfigError(
                "`bias_groups` must be a mapping of bias_name -> [SN, ...]"
            )
        for bias_name, sn_list in bias_raw.items():
            bias_str = str(bias_name).strip()
            if not bias_str:
                raise ConfigError("Bias group name must be non-empty")
            if not isinstance(sn_list, list):
                raise ConfigError(
                    f"bias_groups['{bias_str}']: value must be a list"
                )
            for sn_raw in sn_list:
                sn = _normalize_sn(sn_raw)
                if not sn:
                    raise ConfigError(
                        f"Invalid SN in bias_groups[{bias_str}]: {sn_raw!r}"
                    )
                if sn in bias_by_sn and bias_by_sn[sn] != bias_str:
                    raise ConfigError(
                        f"{sn} is assigned to two different bias groups "
                        f"('{bias_by_sn[sn]}' and '{bias_str}')"
                    )
                bias_by_sn[sn] = bias_str

    logger.info(
        "Loaded dose map: %d serial numbers, %d reference samples (%s); "
        "lots: %s; bias groups: %s",
        len(doses),
        len(reference_sns),
        ", ".join(reference_sns) if reference_sns else "none",
        sorted(set(lot_by_sn.values())) if lot_by_sn else "none",
        sorted(set(bias_by_sn.values())) if bias_by_sn else "none",
    )
    return doses, reference_sns, lot_by_sn, bias_by_sn


# ---------------------------------------------------------------------------
# Plot config
# ---------------------------------------------------------------------------


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge ``override`` into a copy of ``base``.

    Nested dicts are merged key-by-key. Lists and scalars are *replaced*,
    not concatenated - this matches the principle of least surprise for
    config inheritance.
    """
    result = copy.deepcopy(base)
    for key, value in override.items():
        if (
            key in result
            and isinstance(result[key], dict)
            and isinstance(value, dict)
        ):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


# Type-specific overrides for keys that came purely from `defaults` (not
# from the plot entry or its variant). Currently the only case is
# `x_label`, which makes sense as "Dose [kRad]" for absolute/delta but
# not for annealing.
_TYPE_SPECIFIC_DEFAULT_OVERRIDES: dict[str, dict[str, str]] = {
    "annealing": {"x_label": "Stage"},
}


def _apply_type_specific_defaults(
    plot_spec: dict[str, Any],
    user_set_keys: set[str],
) -> None:
    """Override generic defaults that don't make sense for the plot type.

    Only fires for keys the user did NOT explicitly set on the plot
    entry or variant - so explicit `x_label:` always wins.
    """
    overrides = _TYPE_SPECIFIC_DEFAULT_OVERRIDES.get(plot_spec.get("type", ""))
    if not overrides:
        return
    for key, type_default in overrides.items():
        if key not in user_set_keys:
            plot_spec[key] = type_default


def _validate_plot_entry(plot: dict[str, Any], index: int) -> None:
    """Sanity-check a single plot entry. Raises ``ConfigError`` on issues."""
    where = f"plots[{index}]"

    name = plot.get("name")
    if not name or not isinstance(name, str):
        raise ConfigError(f"{where}: missing or invalid `name`")

    ptype = plot.get("type")
    if ptype not in SUPPORTED_PLOT_TYPES:
        raise ConfigError(
            f"{where} ({name}): `type` must be one of "
            f"{sorted(SUPPORTED_PLOT_TYPES)}, got {ptype!r}"
        )

    required = REQUIRED_FIELDS_BY_TYPE[ptype]
    missing = [field for field in required if not plot.get(field)]
    if missing:
        raise ConfigError(
            f"{where} ({name}): missing required fields for type '{ptype}': "
            f"{missing}"
        )

    show_lines = plot.get("show_lines", [])
    if not isinstance(show_lines, list):
        raise ConfigError(f"{where} ({name}): `show_lines` must be a list")
    bad_lines = [s for s in show_lines if s not in SUPPORTED_STAT_LINES]
    if bad_lines:
        raise ConfigError(
            f"{where} ({name}): unknown stat lines {bad_lines}. "
            f"Supported: {sorted(SUPPORTED_STAT_LINES)}"
        )

    # Delta plots are always rendered in percent; `delta_mode` is ignored
    # if present (kept tolerated so old YAMLs still load).

    for axis_key in ("x_scale", "y_scale"):
        value = plot.get(axis_key)
        if value is not None and value not in SUPPORTED_AXIS_SCALES:
            raise ConfigError(
                f"{where} ({name}): `{axis_key}` must be one of "
                f"{sorted(SUPPORTED_AXIS_SCALES)}, got {value!r}"
            )

    lot = plot.get("lot")
    if lot is not None and not isinstance(lot, (str, int)):
        raise ConfigError(f"{where} ({name}): `lot` must be a scalar (string)")

    bias = plot.get("bias")
    if bias is not None and not isinstance(bias, str):
        raise ConfigError(f"{where} ({name}): `bias` must be a string")

    split_by = plot.get("split_by")
    if split_by is not None:
        if isinstance(split_by, str):
            split_by_list = [split_by]
        elif isinstance(split_by, list):
            split_by_list = split_by
        else:
            raise ConfigError(
                f"{where} ({name}): `split_by` must be a string or list of "
                f"strings; got {type(split_by).__name__}"
            )
        bad = [t for t in split_by_list if t not in SUPPORTED_SPLIT_DIMS]
        if bad:
            raise ConfigError(
                f"{where} ({name}): unknown split_by tokens {bad}. "
                f"Supported: {sorted(SUPPORTED_SPLIT_DIMS)}"
            )
        # Normalise to list, preserve order, drop duplicates.
        seen: list[str] = []
        for t in split_by_list:
            if t not in seen:
                seen.append(t)
        plot["split_by"] = seen


def load_plot_config(path: Path) -> dict[str, Any]:
    """Load and resolve the plot config.

    Returns a dict with keys:

    * ``data``: paths section (verbatim).
    * ``defaults``: merged defaults dict (verbatim).
    * ``plots``: list of fully resolved plot specs, with variants expanded.
      Each spec already has defaults merged in and contains an extra
      ``output_name`` field (final filename stem).
    """
    path = Path(path)
    if not path.is_file():
        raise ConfigError(f"Plot config file not found: {path}")

    with path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    if not isinstance(raw, dict):
        raise ConfigError("Plot config root must be a mapping")

    data_section = raw.get("data") or {}
    if not isinstance(data_section, dict):
        raise ConfigError("`data` must be a mapping")
    for required_key in ("flat_files_dir", "dose_map", "output_dir"):
        if required_key not in data_section:
            raise ConfigError(f"`data.{required_key}` is required")

    defaults = raw.get("defaults") or {}
    if not isinstance(defaults, dict):
        raise ConfigError("`defaults` must be a mapping")

    plots_raw = raw.get("plots") or []
    if not isinstance(plots_raw, list):
        raise ConfigError("`plots` must be a list")

    resolved_plots: list[dict[str, Any]] = []
    seen_output_names: set[str] = set()

    for index, plot_entry in enumerate(plots_raw):
        if not isinstance(plot_entry, dict):
            raise ConfigError(f"plots[{index}] must be a mapping")

        variants = plot_entry.get("variants")
        base_entry = {k: v for k, v in plot_entry.items() if k != "variants"}

        if variants is None:
            # No variants - emit one plot spec.
            merged = _deep_merge(defaults, base_entry)
            _apply_type_specific_defaults(merged, set(base_entry.keys()))
            merged["output_name"] = merged["name"]
            _validate_plot_entry(merged, index)
            _check_unique_output_name(merged["output_name"], seen_output_names, index)
            resolved_plots.append(merged)
            continue

        if not isinstance(variants, list) or not variants:
            raise ConfigError(
                f"plots[{index}] ({base_entry.get('name')}): "
                "`variants` must be a non-empty list"
            )

        for v_idx, variant in enumerate(variants):
            if not isinstance(variant, dict):
                raise ConfigError(
                    f"plots[{index}].variants[{v_idx}] must be a mapping"
                )
            suffix = variant.get("suffix", f"_v{v_idx}")
            variant_overrides = {k: v for k, v in variant.items() if k != "suffix"}
            merged = _deep_merge(defaults, base_entry)
            merged = _deep_merge(merged, variant_overrides)
            user_set_keys = set(base_entry.keys()) | set(variant_overrides.keys())
            _apply_type_specific_defaults(merged, user_set_keys)
            merged["output_name"] = f"{merged['name']}{suffix}"
            _validate_plot_entry(merged, index)
            _check_unique_output_name(merged["output_name"], seen_output_names, index)
            resolved_plots.append(merged)

    logger.info(
        "Loaded plot config: %d plot specs (after variant expansion)",
        len(resolved_plots),
    )
    return {
        "data": data_section,
        "defaults": defaults,
        "plots": resolved_plots,
    }


def expand_splits(
    plot_specs: list[dict[str, Any]],
    *,
    lot_values: list[str],
    bias_values: list[str],
) -> list[dict[str, Any]]:
    """Expand specs declaring ``split_by`` into one spec per group.

    For a spec with ``split_by: [lot]`` and lots ``[A, B]`` we emit two
    specs with ``lot`` set to ``A`` and ``B`` and the matching suffix
    appended to ``output_name``. Combinations of dimensions multiply,
    so ``split_by: [lot, bias]`` yields ``len(lots) * len(biases)`` specs.

    If the relevant dimension has no values (e.g. dose_map has no
    ``lots:`` section), the spec is left untouched and a warning is
    logged.
    """
    out: list[dict[str, Any]] = []
    for spec in plot_specs:
        split_by = spec.get("split_by") or []
        if not split_by:
            out.append(spec)
            continue

        # Build per-dimension value list.
        dim_values: list[tuple[str, list[str]]] = []
        for dim in split_by:
            if dim == "lot":
                values = lot_values
            elif dim == "bias":
                values = bias_values
            else:  # validated earlier - defensive only
                values = []
            if not values:
                logger.warning(
                    "Plot '%s': split_by includes '%s' but the dose map "
                    "declares no %s groups; skipping that dimension.",
                    spec["output_name"],
                    dim,
                    dim,
                )
                continue
            dim_values.append((dim, values))

        if not dim_values:
            out.append(spec)
            continue

        # Cartesian product across dimensions.
        combos: list[list[tuple[str, str]]] = [[]]
        for dim, values in dim_values:
            combos = [c + [(dim, v)] for c in combos for v in values]

        for combo in combos:
            new_spec = copy.deepcopy(spec)
            suffix_parts: list[str] = []
            for dim, value in combo:
                if dim == "lot":
                    new_spec["lot"] = value
                    suffix_parts.append(f"lot{value}")
                elif dim == "bias":
                    new_spec["bias"] = value
                    suffix_parts.append(value)
            suffix = "_" + "_".join(suffix_parts)
            new_spec["output_name"] = f"{spec['output_name']}{suffix}"
            # Drop split_by on the expanded specs - we've already split.
            new_spec.pop("split_by", None)
            out.append(new_spec)

    return out


def _check_unique_output_name(
    output_name: str,
    seen: set[str],
    index: int,
) -> None:
    if output_name in seen:
        raise ConfigError(
            f"plots[{index}]: duplicate output_name '{output_name}'. "
            "Use unique `name`s or unique `variants[].suffix` values."
        )
    seen.add(output_name)
