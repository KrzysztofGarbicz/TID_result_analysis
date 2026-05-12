from __future__ import annotations

import argparse
import copy
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import yaml
from matplotlib.lines import Line2D
from tqdm import tqdm


DEFAULT_COLORS = {
    "waveform_00": "tab:blue",
    "waveform_01": "tab:orange",
    "marker_start": "red",
    "marker_stop1": "green",
    "marker_stop2": "purple",
    "marker_a": "red",
    "marker_b": "orange",
}
ALIAS_COLORS = {
    "before_irradiate": "tab:blue",
    "after_irradiate": "tab:red",
    "annealing_24h_25c": "tab:green",
    "annealing_168h_25c": "tab:purple",
}

def load_waveform(path: Path) -> Tuple[np.ndarray, np.ndarray]:
    df = pd.read_csv(path)
    return df.iloc[:, 0].to_numpy(dtype=float), df.iloc[:, 1].to_numpy(dtype=float)


def compute_threshold(signal: np.ndarray, ratio: float) -> float:
    return float(ratio) * float(np.max(signal))


def resolve_threshold(signal: np.ndarray, marker_cfg: Dict) -> Tuple[float, str]:
    if "threshold_voltage" in marker_cfg:
        thr = float(marker_cfg["threshold_voltage"])
        desc = f"threshold {thr:g} V"
    else:
        ratio = float(marker_cfg["threshold"])
        thr = compute_threshold(signal, ratio)
        desc = f"threshold {ratio * 100:.0f}%"
    return thr, desc

def merge_test_cfg(base_cfg: dict, override_cfg: dict) -> dict:
    merged = copy.deepcopy(base_cfg)

    if not override_cfg:
        return merged

    # proste pola
    for key, value in override_cfg.items():
        if key not in ["markers", "slew_rate"]:
            merged[key] = value

    # merge markerów po polu "id"
    if "markers" in override_cfg:
        base_markers = {m["id"]: copy.deepcopy(m) for m in merged.get("markers", [])}

        for m_override in override_cfg["markers"]:
            mid = m_override["id"]
            if mid in base_markers:
                base_markers[mid].update(m_override)
            else:
                base_markers[mid] = copy.deepcopy(m_override)

        merged["markers"] = list(base_markers.values())

    # merge slew_rate
    if "slew_rate" in override_cfg:
        merged.setdefault("slew_rate", {})
        merged["slew_rate"].update(override_cfg["slew_rate"])

    return merged


def find_crossing(t, s, thr, edge, crossing="first"):
    hits = []

    for i in range(1, len(s)):
        if edge == "rising":
            crossed = s[i - 1] < thr and s[i] >= thr
        else:
            crossed = s[i - 1] > thr and s[i] <= thr

        if crossed:
            t0 = t[i - 1]
            t1 = t[i]
            v0 = s[i - 1]
            v1 = s[i]

            if v1 != v0:
                t_cross = t0 + (thr - v0) * (t1 - t0) / (v1 - v0)
            else:
                t_cross = t1

            hits.append(float(t_cross))

    if not hits:
        return None

    if crossing == "last":
        return hits[-1]

    return hits[0]


def parse_filename(name: str) -> Tuple[str, Optional[str], str]:
    base = re.sub(r"_\d\d\.csv$", "", name)
    test = None
    if "time_on" in name:
        test = "time_on"
    elif "time_off" in name:
        test = "time_off"
    elif "overcurrent_trip_time" in name:
        test = "overcurrent_trip_time"
    elif "short_circuit_response" in name:
        test = "short_circuit_response"
    device = name.split("_")[0]
    return base, test, device


def get_time_factor(scale: str) -> float:
    return {"s": 1.0, "ms": 1e3, "us": 1e6, "ns": 1e9}.get(scale, 1.0)


def deep_merge(base: Dict, override: Dict) -> Dict:
    result = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def get_test_cfg(cfg: Dict, device: str, test: str) -> Dict:
    global_test_cfg = cfg.get("tests", {}).get(test, {})
    device_override = cfg.get("devices", {}).get(device, {}).get("tests", {}).get(test, {})
    return deep_merge(global_test_cfg, device_override)


def get_time_scale(cfg: Dict, device: str, test: str) -> str:
    device_cfg = cfg.get("devices", {}).get(device, {})
    return (
        device_cfg.get("tests", {}).get(test, {}).get("time_scale")
        or device_cfg.get("time_scale")
        or cfg.get("defaults", {}).get("time_scale")
        or "us"
    )


def get_test_markers(test_cfg: Dict) -> List[Dict]:
    if "markers" in test_cfg:
        return test_cfg["markers"]

    markers = [
        {
            "id": "marker_a",
            "waveform": "00",
            "edge": test_cfg["edge"]["00"],
            "color": DEFAULT_COLORS["marker_a"],
        },
        {
            "id": "marker_b",
            "waveform": "01",
            "edge": test_cfg["edge"]["01"],
            "color": DEFAULT_COLORS["marker_b"],
        },
    ]
    if "threshold_voltage" in test_cfg and "00" in test_cfg["threshold_voltage"]:
        markers[0]["threshold_voltage"] = test_cfg["threshold_voltage"]["00"]
    else:
        markers[0]["threshold"] = test_cfg["threshold"]["00"]

    if "threshold_voltage" in test_cfg and "01" in test_cfg["threshold_voltage"]:
        markers[1]["threshold_voltage"] = test_cfg["threshold_voltage"]["01"]
    else:
        markers[1]["threshold"] = test_cfg["threshold"]["01"]

    return markers


def marker_value_at_time(time: float, t: np.ndarray, s: np.ndarray) -> float:
    return float(np.interp(time, t, s))


def build_marker_results(markers_cfg: List[Dict], t1, s1, t2, s2) -> Tuple[List[Dict], List[str]]:
    markers = []
    problems = []
    signal_map = {"00": (t1, s1), "01": (t2, s2)}

    for idx, marker_cfg in enumerate(markers_cfg):
        waveform = marker_cfg["waveform"]
        if waveform not in signal_map:
            problems.append(f"Unknown waveform id {waveform}")
            continue
        t, s = signal_map[waveform]
        thr, thr_desc = resolve_threshold(s, marker_cfg)
        crossing_mode = marker_cfg.get("crossing", "first")
        t_cross = find_crossing(t, s, thr, marker_cfg["edge"], crossing=crossing_mode)
        if t_cross is None:
            problems.append(f"No crossing for marker {marker_cfg.get('id', idx)}")
            continue
        markers.append(
            {
                "id": marker_cfg.get("id", f"marker_{idx}"),
                "label": marker_cfg.get("label", marker_cfg.get("id", f"marker_{idx}")),
                "waveform": waveform,
                "edge": marker_cfg["edge"],
                "time": t_cross,
                "value": marker_value_at_time(t_cross, t, s),
                "threshold_value": thr,
                "threshold_desc": thr_desc,
                "color": marker_cfg.get("color", DEFAULT_COLORS.get(f"marker_{idx}", "red")),
                "result_name": marker_cfg.get("result_name"),
            }
        )
    return markers, problems

def get_signal_by_waveform_id(waveform_id: str, t1, s1, t2, s2):
    signal_map = {"00": (t1, s1), "01": (t2, s2)}
    if waveform_id not in signal_map:
        raise RuntimeError(f"Unknown waveform id: {waveform_id}")
    return signal_map[waveform_id]


def build_slew_rate_result(test_cfg, t1, s1, t2, s2):
    slew_cfg = test_cfg.get("slew_rate", {})
    if not slew_cfg.get("enabled", False):
        return None

    signal_map = {
        "00": (t1, s1),
        "01": (t2, s2),
    }

    waveform = slew_cfg["waveform"]
    t, s = signal_map[waveform]

    start_thr = float(slew_cfg["start_threshold"]) * float(np.max(s))
    stop_thr = float(slew_cfg["stop_threshold"]) * float(np.max(s))

    t_start = find_crossing(
        t,
        s,
        start_thr,
        slew_cfg["edge"],
        crossing="first",
    )
    t_stop = find_crossing(
        t,
        s,
        stop_thr,
        slew_cfg["edge"],
        crossing="first",
    )

    if t_start is None or t_stop is None or t_stop == t_start:
        return None

    v_start = marker_value_at_time(t_start, t, s)
    v_stop = marker_value_at_time(t_stop, t, s)

    slew_rate = (v_stop - v_start) / (t_stop - t_start)

    return {
        "waveform": waveform,
        "t_start": t_start,
        "t_stop": t_stop,
        "v_start": v_start,
        "v_stop": v_stop,
        "value_v_per_s": slew_rate,
        "color": slew_cfg.get("color", "black"),
        "result_name": slew_cfg.get("result_name", "slew_rate"),
        "start_desc": f"{100*slew_cfg['start_threshold']:.0f}%",
        "stop_desc": f"{100*slew_cfg['stop_threshold']:.0f}%",
    }

def add_marker_legend(ax, waveform_names: Dict[str, str], markers: List[Dict], plot_mode: str) -> None:
    handles = []
    labels = []

    handles.append(Line2D([0], [0], color=DEFAULT_COLORS["waveform_00"], lw=2))
    labels.append(waveform_names["00"])
    handles.append(Line2D([0], [0], color=DEFAULT_COLORS["waveform_01"], lw=2))
    labels.append(waveform_names["01"])

    for marker in markers:
        handles.append(
            Line2D(
                [0],
                [0],
                marker="o",
                linestyle="--",
                color=marker["color"],
                markerfacecolor=marker["color"],
                markersize=8,
            )
        )
        labels.append(
            f"{marker['label']}: {waveform_names[marker['waveform']]} | {marker['threshold_desc']} | {marker['edge']}"
        )

    if plot_mode == "single":
        ax.legend(handles, labels, loc="best")
    else:
        ax.legend(handles, labels, loc="upper center", bbox_to_anchor=(0.5, -0.16), ncol=1)


def plot_single(t1, s1, t2, s2, markers: List[Dict], waveform_names: Dict[str, str], title: str, path: Path, scale: str, test_cfg, slew_result=None) -> None:
    factor = get_time_factor(scale)
    fig, ax = plt.subplots(figsize=(11, 6.5))

    ax.plot(t1 * factor, s1, label=waveform_names["00"], color=DEFAULT_COLORS["waveform_00"])
    ax.plot(t2 * factor, s2, label=waveform_names["01"], color=DEFAULT_COLORS["waveform_01"])

    for marker in markers:
        marker_t = marker["time"] * factor
        ax.scatter(marker_t, marker["value"], c=marker["color"], s=80, zorder=5)
        ax.axvline(marker_t, color=marker["color"], linestyle="--", alpha=0.9)

    if len(markers) >= 2:
        start = markers[0]
        y_min = min(np.min(s1), np.min(s2))
        y_max = max(np.max(s1), np.max(s2))
        span = y_max - y_min if y_max != y_min else 1.0
        base_y = y_min + 0.55 * span

        for idx, stop in enumerate(markers[1:]):
            y_arrow = base_y - idx * 0.12 * span
            ax.annotate(
                "",
                xy=(start["time"] * factor, y_arrow),
                xytext=(stop["time"] * factor, y_arrow),
                arrowprops=dict(arrowstyle="<->", lw=2, color=stop["color"]),
            )
            dt_scaled = (stop["time"] - start["time"]) * factor
            ax.text(
                ((start["time"] + stop["time"]) / 2) * factor,
                y_arrow + 0.03 * span,
                f"Δt = {dt_scaled:.3f} {scale}",
                ha="center",
                va="bottom",
                bbox=dict(boxstyle="round,pad=0.3", fc="white"),
            )
    if slew_result is not None:
        target_t = t1 if slew_result["waveform"] == "00" else t2
        target_s = s1 if slew_result["waveform"] == "00" else s2

        x0 = slew_result["t_start"] * get_time_factor(scale)
        x1 = slew_result["t_stop"] * get_time_factor(scale)
        y0 = slew_result["v_start"]
        y1 = slew_result["v_stop"]

        ax.scatter(x0, y0, c=slew_result["color"], s=80, marker="s", zorder=6)
        ax.scatter(x1, y1, c=slew_result["color"], s=80, marker="s", zorder=6)

        ax.plot(
            [x0, x1],
            [y0, y1],
            linestyle="-.",
            linewidth=2,
            color=slew_result["color"],
            label=f"Slew rate ({slew_result['start_desc']}→{slew_result['stop_desc']})",
        )

        slope_scaled = slew_result["value_v_per_s"] / get_time_factor(scale)

        ax.text(
            (x0 + x1) / 2,
            (y0 + y1) / 2,
            f"Slew = {slope_scaled:.3f} V/{scale}",
            color=slew_result["color"],
            ha="left",
            va="bottom",
            bbox=dict(boxstyle="round,pad=0.3", fc="white"),
        )
    
    #limity osi
    axes_cfg = test_cfg.get("axes")
    if axes_cfg:
        if "x" in axes_cfg:
            ax.set_xlim(axes_cfg["x"])
        if "y" in axes_cfg:
            ax.set_ylim(axes_cfg["y"])

    ax.set_title(title)
    ax.set_xlabel(f"time [{scale}]")
    plt.ylabel("Voltage [V]")
    ax.grid(True)
    add_marker_legend(ax, waveform_names, markers, plot_mode="single")

    path.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)

def apply_offset_correction(
    t: np.ndarray,
    s: np.ndarray,
    correction_cfg: Dict,
) -> Tuple[np.ndarray, Dict]:
    window_start = float(correction_cfg["window_start"])
    window_end = float(correction_cfg["window_end"])
    target_mean = float(correction_cfg["target_mean"])

    mask = (t >= window_start) & (t <= window_end)
    if not np.any(mask):
        raise RuntimeError(
            f"Brak próbek w oknie offset correction: {window_start} .. {window_end} s"
        )

    measured_mean = float(np.mean(s[mask]))
    delta = target_mean - measured_mean
    s_corrected = s + delta

    info = {
        "window_start": window_start,
        "window_end": window_end,
        "target_mean": target_mean,
        "measured_mean": measured_mean,
        "applied_offset": delta,
    }
    return s_corrected, info

def get_overlay_time_reference(test_cfg: Dict, t1, s1, t2, s2) -> Optional[float]:
    """
    Dla overlay summary wybieramy punkt odniesienia jako czas pierwszego markera.
    """
    markers_cfg = get_test_markers(test_cfg)
    markers, problems = build_marker_results(markers_cfg, t1, s1, t2, s2)

    if problems or len(markers) == 0:
        return None

    return markers[0]["time"]

def apply_time_shift(t: np.ndarray, t_ref: float) -> np.ndarray:
    return t - t_ref

def get_alias_from_path(file_path: Path, input_root: Path) -> str:
    """
    Dla struktury:
      input_root/
        before_irradiate_csv/
          TPS2553_01/
            ...csv

    zwróci:
      before_irradiate
    """
    rel = file_path.relative_to(input_root)

    if len(rel.parts) < 2:
        return "unknown"

    alias_folder = rel.parts[0]
    alias = re.sub(r"_csv$", "", alias_folder)
    return alias

def _data_x_to_fig_fraction(fig, ax, x_data: float) -> float:
    display_x = ax.transData.transform((x_data, 0))[0]
    return fig.transFigure.inverted().transform((display_x, 0))[0]


def plot_dual(t1, s1, t2, s2, markers: List[Dict], waveform_names: Dict[str, str], title: str, path: Path, scale: str, test_cfg, slew_result=None) -> None:
    factor = get_time_factor(scale)
    fig, ax = plt.subplots(2, 1, sharex=True, figsize=(11, 8), gridspec_kw={"hspace": 0.18})

    ax[0].plot(t1 * factor, s1, label=waveform_names["00"], color=DEFAULT_COLORS["waveform_00"])
    ax[1].plot(t2 * factor, s2, label=waveform_names["01"], color=DEFAULT_COLORS["waveform_01"])

    for marker in markers:
        x = marker["time"] * factor
        if marker["waveform"] == "00":
            ax[0].scatter(x, marker["value"], c=marker["color"], s=80, zorder=5)
        else:
            ax[1].scatter(x, marker["value"], c=marker["color"], s=80, zorder=5)
        ax[0].axvline(x, color=marker["color"], linestyle="--", alpha=0.9)
        ax[1].axvline(x, color=marker["color"], linestyle="--", alpha=0.9)

    if slew_result is not None:
        target_ax = ax[0] if slew_result["waveform"] == "00" else ax[1]

        x0 = slew_result["t_start"] * get_time_factor(scale)
        x1 = slew_result["t_stop"] * get_time_factor(scale)
        y0 = slew_result["v_start"]
        y1 = slew_result["v_stop"]

        target_ax.scatter(x0, y0, c=slew_result["color"], s=80, marker="s", zorder=6)
        target_ax.scatter(x1, y1, c=slew_result["color"], s=80, marker="s", zorder=6)

        target_ax.plot(
            [x0, x1],
            [y0, y1],
            linestyle="-.",
            linewidth=2,
            color=slew_result["color"],
            label=f"Slew rate ({slew_result['start_desc']}→{slew_result['stop_desc']})",
        )

        slope_scaled = slew_result["value_v_per_s"] / get_time_factor(scale)

        target_ax.text(
            (x0 + x1) / 2,
            (y0 + y1) / 2,
            f"Slew = {slope_scaled:.3f} V/{scale}",
            color=slew_result["color"],
            ha="left",
            va="bottom",
            bbox=dict(boxstyle="round,pad=0.3", fc="white"),
        )

    # Limity
    axes_cfg = test_cfg.get("axes")
    if axes_cfg:
        if "x" in axes_cfg:
            ax[0].set_xlim(axes_cfg["x"])
            ax[1].set_xlim(axes_cfg["x"])
        if "top" in axes_cfg:
            if "y" in axes_cfg["top"]:
                ax[0].set_ylim(axes_cfg["top"]["y"])
        if "bottom" in axes_cfg:
            if "y" in axes_cfg["bottom"]:
                ax[1].set_ylim(axes_cfg["bottom"]["y"])

    ax[0].grid(True)
    ax[1].grid(True)
    ax[1].set_xlabel(f"time [{scale}]")
    ax[0].set_ylabel("Voltage [V]")
    ax[1].set_ylabel("ILOAD [V] | scale: 0.1 V/A")
    fig.suptitle(title)

    if len(markers) >= 2:
        start = markers[0]
        gap_top = ax[0].get_position().y0
        gap_bottom = ax[1].get_position().y1
        gap_height = max(gap_top - gap_bottom, 0.04)
        base_y = gap_bottom + 0.65 * gap_height
        step = min(0.18 * gap_height, 0.03)

        for idx, stop in enumerate(markers[1:]):
            x0 = _data_x_to_fig_fraction(fig, ax[0], start["time"] * factor)
            x1 = _data_x_to_fig_fraction(fig, ax[0], stop["time"] * factor)
            y = base_y - idx * step
            plt.annotate(
                "",
                xy=(x0, y),
                xytext=(x1, y),
                xycoords=fig.transFigure,
                textcoords=fig.transFigure,
                arrowprops=dict(arrowstyle="<->", lw=2, color=stop["color"]),
            )
            dt_scaled = (stop["time"] - start["time"]) * factor
            fig.text(
                (x0 + x1) / 2,
                y + 0.012,
                f"Δt = {dt_scaled:.3f} {scale}",
                ha="center",
                va="bottom",
                bbox=dict(boxstyle="round,pad=0.3", fc="white"),
            )

    add_marker_legend(ax[1], waveform_names, markers, plot_mode="dual")

    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)

def plot_overlay_summary(
    device_serial: str,
    test: str,
    entries: List[Dict],
    out_path: Path,
) -> None:
    if not entries:
        return

    plot_type = entries[0]["plot_type"]
    scale = entries[0]["scale"]
    factor = get_time_factor(scale)
    test_cfg = entries[0]["test_cfg"]

    if plot_type == "dual":
        fig, ax = plt.subplots(2, 1, sharex=True, figsize=(11, 8), gridspec_kw={"hspace": 0.18})

        for entry in entries:
            alias = entry["alias"]
            color = ALIAS_COLORS.get(alias, None)

            ax[0].plot(
                entry["t1"] * factor,
                entry["s1"],
                label=alias,
                color=color,
            )
            ax[1].plot(
                entry["t2"] * factor,
                entry["s2"],
                label=alias,
                color=color,
            )

        axes_cfg = test_cfg.get("axes")
        if axes_cfg:
            if "x" in axes_cfg:
                ax[0].set_xlim(axes_cfg["x"])
                ax[1].set_xlim(axes_cfg["x"])
            if "top" in axes_cfg and "y" in axes_cfg["top"]:
                ax[0].set_ylim(axes_cfg["top"]["y"])
            if "bottom" in axes_cfg and "y" in axes_cfg["bottom"]:
                ax[1].set_ylim(axes_cfg["bottom"]["y"])

        ax[0].grid(True)
        ax[1].grid(True)
        ax[0].set_ylabel("Voltage [V]")
        ax[1].set_ylabel("ILOAD [V] | scale: 0.1 V/A")
        ax[1].set_xlabel(f"time [{scale}]")
        fig.suptitle(f"{device_serial} | {test} | overlay summary (processed waveforms)")

        ax[0].legend(title="Irradiation stage", loc="best")
        ax[1].legend(title="Irradiation stage", loc="best")

    else:
        fig, ax = plt.subplots(
            2,
            1,
            sharex=True,
            figsize=(11, 8),
            gridspec_kw={"hspace": 0.18},
        )

        for entry in entries:
            alias = entry["alias"]
            color = ALIAS_COLORS.get(alias, None)

            ax[0].plot(
                entry["t1"] * factor,
                entry["s1"],
                label=alias,
                color=color,
            )

            ax[1].plot(
                entry["t2"] * factor,
                entry["s2"],
                label=alias,
                color=color,
            )

        axes_cfg = test_cfg.get("axes")
        if axes_cfg:
            if "x" in axes_cfg:
                ax[0].set_xlim(axes_cfg["x"])
                ax[1].set_xlim(axes_cfg["x"])
            if "y" in axes_cfg:
                ax[0].set_ylim([-0.5, 5.5])
                ax[1].set_ylim(axes_cfg["y"])

        fig.suptitle(f"{device_serial} | {test} | overlay summary (processed waveforms)")
        ax[0].set_ylabel("Enable [V]")
        ax[1].set_ylabel("VOUT [V]")
        ax[1].set_xlabel(f"time [{scale}]")

        ax[0].grid(True)
        ax[1].grid(True)

        ax[0].legend(title="Irradiation stage",loc="best")
        ax[1].legend(title="Irradiation stage",loc="best")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)

def extract_timestamp_from_filename(path: Path) -> str:
    stem = path.stem  # bez .csv
    m = re.search(r"(\d{8}_\d{6})_(?:00|01)$", stem)
    if m:
        return m.group(1)
    return stem

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("folder")
    parser.add_argument("--config", default="timing_config.yaml")
    args = parser.parse_args()

    with open(args.config, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    input_folder = Path(args.folder)
    script_dir = Path(__file__).resolve().parent
    results_root = script_dir / "timing_analysis_result"
    results_root.mkdir(parents=True, exist_ok=True)

    files = list(input_folder.rglob("*.csv"))
    #wybór ręcznie plików
    ALLOWED_SERIALS = {"TPS259474_02"}
    files = [f for f in files if any(s in f.name for s in ALLOWED_SERIALS)]

    # ALLOWED_ALIASES = {"annealing_168h_100c"}
    # files = [
    #     f for f in files
    #     if get_alias_from_path(f, input_folder) in ALLOWED_ALIASES
    # ]

    total_csv = len(files)
    print(f"\nZnaleziono {total_csv} plików CSV")

    grouped: Dict[Tuple[str, str], List[Path]] = {}

    for f in files:
        base, test, _ = parse_filename(f.name)
        if test is None:
            continue

        alias = get_alias_from_path(f, input_folder)
        group_key = (alias, base)

        grouped.setdefault(group_key, []).append(f)

    expected_measurements = len(grouped)
    print(f"Oczekiwana liczba pomiarów: {expected_measurements}")

    results: Dict[str, List[Dict]] = {}
    processed_measurements = []
    failed_measurements = []
    overlay_groups: Dict[Tuple[str, str], List[Dict]] = {}

    def process_measurement(group_key: Tuple[str, str], flist: List[Path]) -> bool:
        alias, base = group_key

        flist = sorted(flist)
        if len(flist) < 2:
            raise RuntimeError("Brakuje jednego z kanałów CSV")

        f1, f2 = flist[0], flist[1]
        base, test, device = parse_filename(f1.name)

        test_cfg = get_test_cfg(cfg, device, test)
        if not test_cfg:
            raise RuntimeError("Test nie istnieje w config.yaml")

        t1, s1 = load_waveform(f1)
        t2, s2 = load_waveform(f2)

        # kopie do overlay
        t1_overlay = t1.copy()
        s1_overlay = s1.copy()
        t2_overlay = t2.copy()
        s2_overlay = s2.copy()

        # Offset
        signal_preprocess = test_cfg.get("signal_preprocess", {})
        preprocess_log = {}

        if "00" in signal_preprocess:
            wf00_cfg = signal_preprocess["00"]
            if "offset_correction" in wf00_cfg:
                s1, info = apply_offset_correction(t1, s1, wf00_cfg["offset_correction"])
                s1_overlay, _ = apply_offset_correction(t1_overlay, s1_overlay, wf00_cfg["offset_correction"])
                preprocess_log["00"] = info

        if "01" in signal_preprocess:
            wf01_cfg = signal_preprocess["01"]
            if "offset_correction" in wf01_cfg:
                s2, info = apply_offset_correction(t2, s2, wf01_cfg["offset_correction"])
                s2_overlay, _ = apply_offset_correction(t2_overlay, s2_overlay, wf01_cfg["offset_correction"])
                preprocess_log["01"] = info

        markers_cfg = get_test_markers(test_cfg)
        markers, problems = build_marker_results(markers_cfg, t1, s1, t2, s2)
        slew_result = build_slew_rate_result(test_cfg, t1, s1, t2, s2)
        overlay_t_ref = get_overlay_time_reference(test_cfg, t1_overlay, s1_overlay, t2_overlay, s2_overlay)

        if overlay_t_ref is not None:
            t1_overlay = apply_time_shift(t1_overlay, overlay_t_ref)
            t2_overlay = apply_time_shift(t2_overlay, overlay_t_ref)

        scale = get_time_scale(cfg, device, test)
        parts = base.split("_")
        device_serial = f"{parts[0]}_SN{parts[1]}"
        device_out_dir = results_root / device_serial
        plot_dir = device_out_dir / "plot"
        fail_plot_dir = device_out_dir / "fail"
        waveform_names = test_cfg["waveforms"]

        if problems or len(markers) < 2:
            fail_title = f"{device_serial} | {alias} | {test} | FAIL: markers not found"
            fail_plot_path = fail_plot_dir / f"{device_serial}_{test}_{alias}_FAIL.png"

            if test_cfg.get("plot", "single") == "dual":
                plot_dual(
                    t1, s1, t2, s2,
                    markers=[],
                    waveform_names=waveform_names,
                    title=fail_title,
                    path=fail_plot_path,
                    scale=scale,
                    test_cfg=test_cfg,
                    slew_result=None,
                )
            else:
                plot_single(
                    t1, s1, t2, s2,
                    markers=[],
                    waveform_names=waveform_names,
                    title=fail_title,
                    path=fail_plot_path,
                    scale=scale,
                    test_cfg=test_cfg,
                    slew_result=None,
                )

            raise RuntimeError(f"Nie znaleziono markerów: {problems}")

        first_dt = (markers[1]["time"] - markers[0]["time"]) * get_time_factor(scale)
  
        title = f"{device_serial} | {alias} | {test} | Δt = {first_dt:.3f} {scale}"
        csv_timestamp = extract_timestamp_from_filename(f1)
        plot_path = plot_dir / f"{device_serial}_{test}_{alias}_{csv_timestamp}.png"

        if test_cfg.get("plot", "single") == "dual":
            plot_dual(t1, s1, t2, s2, markers, waveform_names, title, plot_path, scale, test_cfg, slew_result=slew_result)
        else:
            plot_single(t1, s1, t2, s2, markers, waveform_names, title, plot_path, scale, test_cfg, slew_result=slew_result)

        overlay_groups.setdefault((device_serial, test), [])
        overlay_groups[(device_serial, test)].append(
            {
                "alias": alias,
                "base": base,
                "t1": t1_overlay,
                "s1": s1_overlay,
                "t2": t2_overlay,
                "s2": s2_overlay,
                "waveform_names": waveform_names,
                "plot_type": test_cfg.get("plot", "single"),
                "scale": scale,
                "test_cfg": test_cfg,
            }
        )

        results.setdefault(device_serial, [])
        start_marker = markers[0]
        for stop_marker in markers[1:]:
            metric_name = stop_marker.get("result_name") or stop_marker["label"]
            dt_scaled = (stop_marker["time"] - start_marker["time"]) * get_time_factor(scale)
            results[device_serial].append(
            {
                "alias": alias,
                "test": test,
                "metric": metric_name,
                "result": dt_scaled,
                "unit": scale,
                "source_file": base,
            }
        )
        if slew_result is not None:
            slew_scaled = slew_result["value_v_per_s"] / get_time_factor(scale)
            results[device_serial].append(
                {
                    "alias": alias,
                    "test": test,
                    "metric": slew_result["result_name"],
                    "result": slew_scaled,
                    "unit": f"V/{scale}",
                    "source_file": base,
                }
            )
        return True

    for group_key, flist in tqdm(grouped.items(), total=len(grouped), desc="Processing"):
        try:
            process_measurement(group_key, flist)
            processed_measurements.append(group_key)
        except Exception as e:
            print(f"BŁĄD przy {group_key}: {e}")
            failed_measurements.append(group_key)

    missing = set(grouped.keys()) - set(processed_measurements)
    if missing:
        print("\nPonowna próba brakujących pomiarów...")
        for group_key in missing:
            try:
                process_measurement(group_key, grouped[group_key])
                processed_measurements.append(group_key)
                print(f"OK po retry: {group_key}")
            except Exception as e:
                print(f"Nadal błąd: {group_key} -> {e}")

    for device_serial, data in tqdm(results.items(), total=len(results), desc="Zapis CSV", unit="plik"):
        out = results_root / device_serial / f"timing_results_{device_serial}.csv"
        out.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(data).to_csv(out, index=False)

    for (device_serial, test), entries in tqdm(
        overlay_groups.items(),
        total=len(overlay_groups),
        desc="Overlay",
        unit="plik",
    ):
        if not entries:
            continue

        unique_entries = {}
        for e in sorted(entries, key=lambda x: (x["alias"], x["base"])):
            if e["alias"] not in unique_entries:
                unique_entries[e["alias"]] = e

        entries = list(unique_entries.values())

        out_path = results_root / device_serial / "plot" / f"{device_serial}_{test}_overlay_summary.png"
        plot_overlay_summary(
            device_serial=device_serial,
            test=test,
            entries=entries,
            out_path=out_path,
        )

    final_missing = set(grouped.keys()) - set(processed_measurements)
    print("\n===== PODSUMOWANIE =====")
    print(f"CSV znalezione: {total_csv}")
    print(f"Oczekiwane pomiary: {expected_measurements}")
    print(f"Przetworzone pomiary: {len(processed_measurements)}")
    print(f"Błędy: {len(failed_measurements)}")
    print(f"Brakujące po retry: {len(final_missing)}")

    if final_missing:
        print("\nLista brakujących pomiarów:")
        for m in sorted(final_missing):
            print(m)


if __name__ == "__main__":
    main()
