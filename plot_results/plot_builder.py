#!/usr/bin/env python3
"""Interactive GUI builder for plot_config.yaml entries.

Run:
    python plot_builder.py
    python plot_builder.py --config path/to/plot_config.yaml

The GUI exposes every field supported by the YAML schema. As you tweak
values, the right panel re-renders a live preview (debounced ~500 ms).
"Generate YAML" writes the configured plot to
``_generated_plots/<name>.yaml`` next to the plot config; "Copy YAML"
puts the same text on the clipboard so you can paste it straight into
``plot_config.yaml``.

Designed to speed up authoring of plot specs: the dropdowns are
populated from the actual master DataFrame so you can only pick values
that exist in the data.
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk
from typing import Any

import matplotlib

matplotlib.use("TkAgg")
import matplotlib.pyplot as plt  # noqa: E402
import yaml  # noqa: E402
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg  # noqa: E402

# Local imports
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from radiation_plot import config as cfg  # noqa: E402
from radiation_plot import data_loader as dl  # noqa: E402
from radiation_plot import plotters  # noqa: E402


logger = logging.getLogger(__name__)


# Canonical stage order used to sort multi-select picks in the YAML output.
CANONICAL_STAGES = [
    "before_irradiate",
    "after_irradiate",
    "annealing_24h_25c",
    "annealing_168h_25c",
    "annealing_168h_100c",
]

PLOT_TYPES = ["absolute", "delta", "annealing"]
AXIS_SCALES = ["linear", "log", "symlog"]
STAT_LINES = ["min", "max", "mean", "median"]
TRISTATE = ["(default)", "true", "false"]
SPLIT_DIMS = ["lot", "bias"]

# Key order for the emitted YAML. Anything not listed is appended at the end.
YAML_KEY_ORDER = [
    "name", "type", "title", "note",
    "lcl_name", "measurement_type", "metric", "context_key",
    "stages", "stages_order", "delta_from", "delta_to",
    "include_doses", "exclude_doses", "exclude_sn",
    "lot", "bias",
    "show_before_at_zero",
    "y_label", "x_label",
    "x_lim", "y_lim",
    "x_scale", "y_scale",
    "figsize", "dpi", "format",
    "grid", "legend",
    "show_points", "show_lines",
    "marker_size", "alpha_points",
    "reference_lines",
    "split_by", "series_by",
    "subplots", "subplot_layout", "share_x", "share_y",
    "variants",
]

# Colour and linestyle options for the reference-line editor.
LINE_COLORS = ["red", "black", "blue", "green", "orange", "gray", "magenta"]
LINE_STYLES = ["--", ":", "-.", "-"]


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _parse_number_list(text: str) -> list[float]:
    """Parse "0, 100" / "0 100" / "0; 100" -> [0.0, 100.0]."""
    if not text or not text.strip():
        return []
    out: list[float] = []
    for chunk in text.replace(",", " ").replace(";", " ").split():
        try:
            out.append(float(chunk))
        except ValueError as exc:
            raise ValueError(f"'{chunk}' is not a number") from exc
    return out


def _listbox_selected(lb: tk.Listbox) -> list[str]:
    """Selected values from a Listbox, preserving display order."""
    return [lb.get(i) for i in lb.curselection()]


def _set_listbox(lb: tk.Listbox, values: list[str]) -> None:
    """Replace the contents of a Listbox."""
    lb.delete(0, tk.END)
    for v in values:
        lb.insert(tk.END, v)


def _sort_stages(stages: list[str]) -> list[str]:
    """Sort a list of stage names by canonical order; unknown stages at the end."""
    canon = {s: i for i, s in enumerate(CANONICAL_STAGES)}
    return sorted(stages, key=lambda s: canon.get(s, 9999))


def _ordered_yaml(spec: dict[str, Any]) -> dict[str, Any]:
    """Return ``spec`` with keys in canonical order for clean YAML emission."""
    out: dict[str, Any] = {}
    for key in YAML_KEY_ORDER:
        if key in spec:
            out[key] = spec[key]
    for key, value in spec.items():
        if key not in out:
            out[key] = value
    return out


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------


class PlotBuilder:
    """Tkinter GUI that builds a single plot_config.yaml entry."""

    def __init__(self, root: tk.Tk, config_path: Path) -> None:
        self.root = root
        self.config_path = config_path
        self.root.title(f"Plot Builder - {config_path.name}")
        self.root.geometry("1400x850")

        self.status_var = tk.StringVar(value="Loading data...")

        # Loaded once, used for dropdown population.
        self.master_df = None
        self.dose_map: dict[str, float] = {}
        self.reference_sns: list[str] = []
        self.lot_by_sn: dict[str, str] = {}
        self.bias_by_sn: dict[str, str] = {}
        self.defaults: dict[str, Any] = {}
        self.output_dir_base: Path = HERE

        # Debounce handle for live preview.
        self._preview_job: str | None = None
        # When True, var traces should NOT schedule a preview. Used while
        # auto-fill writes to vars so we don't spawn redundant redraws.
        self._suppress_preview = False

        # Auto-fill toggles for derived fields. Each is paired with an
        # Entry whose state flips between "readonly" (auto on) and
        # "normal" (auto off). Created here so the tab builders can read
        # them.
        self.var_auto_name = tk.BooleanVar(value=True)
        self.var_auto_title = tk.BooleanVar(value=True)
        self.var_auto_y_label = tk.BooleanVar(value=True)
        self.var_auto_x_label = tk.BooleanVar(value=True)

        # Widget refs for the four auto-managed Entry fields (set by
        # _add_entry_with_auto). Used to flip their state.
        self.entry_name: ttk.Entry | None = None
        self.entry_title: ttk.Entry | None = None
        self.entry_y_label: ttk.Entry | None = None
        self.entry_x_label: ttk.Entry | None = None

        # Dynamic list of reference-line editor rows. Each item is a dict
        # with keys: frame, axis, value, label, color, linestyle.
        self._ref_line_rows: list[dict[str, Any]] = []

        # Build skeleton first so the status bar exists.
        self._build_ui()

        # Load data after the window is mapped so the status updates show.
        self.root.after(50, self._load_data_and_populate)

    # ----- Data loading -----

    def _load_data_and_populate(self) -> None:
        try:
            plot_config = cfg.load_plot_config(self.config_path)
            data_section = plot_config["data"]
            self.defaults = plot_config.get("defaults", {})

            base = self.config_path.parent.resolve()

            def _resolve(p: str) -> Path:
                pp = Path(p)
                return pp if pp.is_absolute() else (base / pp).resolve()

            flat_dir = _resolve(data_section["flat_files_dir"])
            dose_map_path = _resolve(data_section["dose_map"])
            self.output_dir_base = _resolve(data_section["output_dir"])

            (
                self.dose_map,
                self.reference_sns,
                self.lot_by_sn,
                self.bias_by_sn,
            ) = cfg.load_dose_map(dose_map_path)

            self.master_df = dl.build_master_dataframe(
                flat_dir,
                self.dose_map,
                lot_by_sn=self.lot_by_sn,
                bias_by_sn=self.bias_by_sn,
            )
        except Exception as exc:
            self.status_var.set(f"Failed to load data: {exc}")
            messagebox.showerror("Data load failed", str(exc))
            return

        # Populate dropdowns now that we have data.
        self._populate_data_dropdowns()
        self._populate_filter_dropdowns()
        self.status_var.set(
            f"Loaded {len(self.master_df):,} rows from "
            f"{self.master_df['lcl_name'].nunique()} device(s)."
        )
        self._schedule_preview()

    # ----- UI skeleton -----

    def _build_ui(self) -> None:
        main_paned = ttk.PanedWindow(self.root, orient=tk.HORIZONTAL)
        main_paned.pack(fill=tk.BOTH, expand=True)

        # ---- Left: tabbed form ----
        left = ttk.Frame(main_paned, padding=4)
        main_paned.add(left, weight=1)

        self.notebook = ttk.Notebook(left)
        self.notebook.pack(fill=tk.BOTH, expand=True)

        self._build_tab_data()
        self._build_tab_filters()
        self._build_tab_appearance()
        self._build_tab_lines()
        self._build_tab_grouping()
        self._build_tab_advanced()

        # ---- Right: matplotlib preview ----
        right = ttk.Frame(main_paned, padding=4)
        main_paned.add(right, weight=2)

        self.fig = plt.Figure(figsize=(8, 6), dpi=90)
        self.canvas = FigureCanvasTkAgg(self.fig, master=right)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

        # ---- Bottom: action buttons + status ----
        bottom = ttk.Frame(self.root, padding=6)
        bottom.pack(fill=tk.X, side=tk.BOTTOM)

        ttk.Button(
            bottom, text="Refresh preview", command=self._refresh_preview
        ).pack(side=tk.LEFT, padx=2)
        ttk.Button(
            bottom, text="Generate YAML", command=self._on_generate
        ).pack(side=tk.LEFT, padx=2)
        ttk.Button(
            bottom, text="Copy YAML to clipboard", command=self._on_copy
        ).pack(side=tk.LEFT, padx=2)
        ttk.Button(
            bottom, text="Reset form", command=self._on_reset
        ).pack(side=tk.LEFT, padx=2)

        ttk.Label(bottom, textvariable=self.status_var, foreground="gray").pack(
            side=tk.LEFT, padx=10
        )

    # ----- Tab: Data -----

    def _build_tab_data(self) -> None:
        tab = ttk.Frame(self.notebook, padding=8)
        self.notebook.add(tab, text="Data")
        r = 0

        self.var_name = tk.StringVar(value="my_plot")
        self.var_type = tk.StringVar(value="absolute")
        self.var_title = tk.StringVar(value="")
        self.var_note = tk.StringVar(value="")

        r, self.entry_name = self._add_entry_with_auto(
            tab, r, "name", self.var_name, self.var_auto_name
        )
        r = self._add_combobox(
            tab, r, "type", self.var_type, PLOT_TYPES, on_change=self._on_type_change
        )
        r, self.entry_title = self._add_entry_with_auto(
            tab, r, "title", self.var_title, self.var_auto_title
        )
        r = self._add_entry(tab, r, "note", self.var_note)

        ttk.Separator(tab, orient="horizontal").grid(
            row=r, column=0, columnspan=2, sticky="ew", pady=6
        )
        r += 1

        self.var_lcl_name = tk.StringVar()
        self.var_measurement_type = tk.StringVar()
        self.var_context_key = tk.StringVar()

        self.cb_lcl_name = self._add_combobox(
            tab, r, "lcl_name", self.var_lcl_name, [],
            on_change=self._on_lcl_change, return_widget=True,
        )
        r += 1
        self.cb_measurement_type = self._add_combobox(
            tab, r, "measurement_type", self.var_measurement_type, [],
            on_change=self._on_mt_change, return_widget=True,
        )
        r += 1

        # metric (multi-select)
        ttk.Label(tab, text="metric (multi)").grid(
            row=r, column=0, sticky="nw", padx=2, pady=2
        )
        self.lb_metric = tk.Listbox(
            tab, selectmode=tk.EXTENDED, height=6, exportselection=False
        )
        self.lb_metric.grid(row=r, column=1, sticky="ew", padx=2, pady=2)
        self.lb_metric.bind("<<ListboxSelect>>", self._on_metric_change)
        r += 1

        self.cb_context_key = self._add_combobox(
            tab, r, "context_key", self.var_context_key, [],
            on_change=self._schedule_preview, return_widget=True,
        )
        r += 1

        ttk.Separator(tab, orient="horizontal").grid(
            row=r, column=0, columnspan=2, sticky="ew", pady=6
        )
        r += 1

        # stages multi-select (used by absolute + annealing). For delta we
        # also expose delta_from/delta_to separately below.
        ttk.Label(tab, text="stages (multi)").grid(
            row=r, column=0, sticky="nw", padx=2, pady=2
        )
        self.lb_stages = tk.Listbox(
            tab, selectmode=tk.EXTENDED, height=5, exportselection=False
        )
        self.lb_stages.grid(row=r, column=1, sticky="ew", padx=2, pady=2)
        self.lb_stages.bind("<<ListboxSelect>>", self._schedule_preview)
        r += 1

        self.var_delta_from = tk.StringVar()
        self.var_delta_to = tk.StringVar()
        self.cb_delta_from = self._add_combobox(
            tab, r, "delta_from", self.var_delta_from, [],
            on_change=self._schedule_preview, return_widget=True,
        )
        r += 1
        self.cb_delta_to = self._add_combobox(
            tab, r, "delta_to", self.var_delta_to, [],
            on_change=self._schedule_preview, return_widget=True,
        )
        r += 1

        tab.columnconfigure(1, weight=1)

    # ----- Tab: Filters -----

    def _build_tab_filters(self) -> None:
        tab = ttk.Frame(self.notebook, padding=8)
        self.notebook.add(tab, text="Filters")
        r = 0

        ttk.Label(tab, text="include_doses (multi)").grid(
            row=r, column=0, sticky="nw", padx=2, pady=2
        )
        self.lb_include_doses = tk.Listbox(
            tab, selectmode=tk.EXTENDED, height=6, exportselection=False
        )
        self.lb_include_doses.grid(row=r, column=1, sticky="ew", padx=2, pady=2)
        self.lb_include_doses.bind("<<ListboxSelect>>", self._schedule_preview)
        r += 1

        ttk.Label(tab, text="exclude_doses (multi)").grid(
            row=r, column=0, sticky="nw", padx=2, pady=2
        )
        self.lb_exclude_doses = tk.Listbox(
            tab, selectmode=tk.EXTENDED, height=6, exportselection=False
        )
        self.lb_exclude_doses.grid(row=r, column=1, sticky="ew", padx=2, pady=2)
        self.lb_exclude_doses.bind("<<ListboxSelect>>", self._schedule_preview)
        r += 1

        ttk.Label(tab, text="exclude_sn (multi)").grid(
            row=r, column=0, sticky="nw", padx=2, pady=2
        )
        self.lb_exclude_sn = tk.Listbox(
            tab, selectmode=tk.EXTENDED, height=8, exportselection=False
        )
        self.lb_exclude_sn.grid(row=r, column=1, sticky="ew", padx=2, pady=2)
        self.lb_exclude_sn.bind("<<ListboxSelect>>", self._schedule_preview)
        r += 1

        self.var_lot = tk.StringVar(value="(any)")
        self.var_bias = tk.StringVar(value="(any)")
        r = self._add_combobox(
            tab, r, "lot", self.var_lot, ["(any)"], on_change=self._schedule_preview
        )
        r = self._add_combobox(
            tab, r, "bias", self.var_bias, ["(any)"], on_change=self._schedule_preview
        )

        self.var_show_before = tk.StringVar(value=TRISTATE[0])
        r = self._add_combobox(
            tab, r, "show_before_at_zero (absolute)", self.var_show_before,
            TRISTATE, on_change=self._schedule_preview,
        )

        tab.columnconfigure(1, weight=1)

    # ----- Tab: Appearance -----

    def _build_tab_appearance(self) -> None:
        tab = ttk.Frame(self.notebook, padding=8)
        self.notebook.add(tab, text="Appearance")
        r = 0

        self.var_y_label = tk.StringVar(value="")
        self.var_x_label = tk.StringVar(value="")
        self.var_x_lim = tk.StringVar(value="")
        self.var_y_lim = tk.StringVar(value="")
        self.var_x_scale = tk.StringVar(value="linear")
        self.var_y_scale = tk.StringVar(value="linear")
        self.var_figsize = tk.StringVar(value="10, 6")
        self.var_dpi = tk.StringVar(value="150")
        self.var_format = tk.StringVar(value="png")
        self.var_grid = tk.StringVar(value=TRISTATE[0])
        self.var_legend = tk.StringVar(value=TRISTATE[0])
        self.var_show_points = tk.StringVar(value=TRISTATE[0])
        self.var_marker_size = tk.StringVar(value="")
        self.var_alpha_points = tk.StringVar(value="")

        r, self.entry_y_label = self._add_entry_with_auto(
            tab, r, "y_label", self.var_y_label, self.var_auto_y_label
        )
        r, self.entry_x_label = self._add_entry_with_auto(
            tab, r, "x_label", self.var_x_label, self.var_auto_x_label
        )
        r = self._add_entry(tab, r, "x_lim (min, max)", self.var_x_lim)
        r = self._add_entry(tab, r, "y_lim (min, max)", self.var_y_lim)
        r = self._add_combobox(tab, r, "x_scale", self.var_x_scale, AXIS_SCALES,
                               on_change=self._schedule_preview)
        r = self._add_combobox(tab, r, "y_scale", self.var_y_scale, AXIS_SCALES,
                               on_change=self._schedule_preview)
        r = self._add_entry(tab, r, "figsize (w, h)", self.var_figsize)
        r = self._add_entry(tab, r, "dpi", self.var_dpi)
        r = self._add_combobox(tab, r, "format", self.var_format,
                               ["png", "jpg", "pdf", "svg"], on_change=self._schedule_preview)
        r = self._add_combobox(tab, r, "grid", self.var_grid, TRISTATE,
                               on_change=self._schedule_preview)
        r = self._add_combobox(tab, r, "legend", self.var_legend, TRISTATE,
                               on_change=self._schedule_preview)
        r = self._add_combobox(tab, r, "show_points", self.var_show_points, TRISTATE,
                               on_change=self._schedule_preview)

        ttk.Label(tab, text="show_lines (multi)").grid(
            row=r, column=0, sticky="nw", padx=2, pady=2
        )
        self.lb_show_lines = tk.Listbox(
            tab, selectmode=tk.EXTENDED, height=4, exportselection=False
        )
        _set_listbox(self.lb_show_lines, STAT_LINES)
        self.lb_show_lines.grid(row=r, column=1, sticky="ew", padx=2, pady=2)
        self.lb_show_lines.bind("<<ListboxSelect>>", self._schedule_preview)
        r += 1

        r = self._add_entry(tab, r, "marker_size", self.var_marker_size)
        r = self._add_entry(tab, r, "alpha_points", self.var_alpha_points)

        tab.columnconfigure(1, weight=1)

    # ----- Tab: Grouping -----

    def _build_tab_grouping(self) -> None:
        tab = ttk.Frame(self.notebook, padding=8)
        self.notebook.add(tab, text="Grouping")
        r = 0

        ttk.Label(
            tab,
            text=(
                "split_by: emit one PNG per group along the chosen "
                "dimension(s)."
            ),
            foreground="gray",
            wraplength=400,
        ).grid(row=r, column=0, columnspan=2, sticky="w", pady=2)
        r += 1

        self.split_vars = {dim: tk.BooleanVar() for dim in SPLIT_DIMS}
        for dim in SPLIT_DIMS:
            cb = ttk.Checkbutton(
                tab, text=f"split_by: {dim}", variable=self.split_vars[dim],
                command=self._schedule_preview,
            )
            cb.grid(row=r, column=0, columnspan=2, sticky="w", padx=2)
            r += 1

        ttk.Separator(tab, orient="horizontal").grid(
            row=r, column=0, columnspan=2, sticky="ew", pady=8
        )
        r += 1

        ttk.Label(
            tab,
            text=(
                "series_by: draw multiple series on a SINGLE PNG "
                "(one per group value)."
            ),
            foreground="gray",
            wraplength=400,
        ).grid(row=r, column=0, columnspan=2, sticky="w", pady=2)
        r += 1

        self.series_vars = {dim: tk.BooleanVar() for dim in SPLIT_DIMS}
        for dim in SPLIT_DIMS:
            cb = ttk.Checkbutton(
                tab, text=f"series_by: {dim}", variable=self.series_vars[dim],
                command=self._schedule_preview,
            )
            cb.grid(row=r, column=0, columnspan=2, sticky="w", padx=2)
            r += 1

        tab.columnconfigure(1, weight=1)

    # ----- Tab: Lines -----

    def _build_tab_lines(self) -> None:
        tab = ttk.Frame(self.notebook, padding=8)
        self.notebook.add(tab, text="Lines")

        ttk.Label(
            tab,
            text=(
                "Reference / limit lines. Each row draws one line at the "
                "chosen value with a label in the legend. Empty value = "
                "row ignored. Typical use: +/-8 % accuracy bands."
            ),
            foreground="gray",
            wraplength=460,
        ).pack(anchor="w", pady=(0, 6))

        # Header row.
        header = ttk.Frame(tab)
        header.pack(fill=tk.X)
        for text, width in (
            ("axis", 4),
            ("value", 10),
            ("label", 22),
            ("color", 10),
            ("style", 5),
            ("", 3),
        ):
            ttk.Label(header, text=text, foreground="gray").pack(
                side=tk.LEFT, padx=2
            )

        # Container that holds the dynamic rows.
        self.lines_container = ttk.Frame(tab)
        self.lines_container.pack(fill=tk.X, pady=4)

        # Add-row button.
        btns = ttk.Frame(tab)
        btns.pack(fill=tk.X, pady=4)
        ttk.Button(
            btns, text="+ Add line", command=self._add_ref_line_row
        ).pack(side=tk.LEFT)
        ttk.Button(
            btns,
            text="Add ±8 % band",
            command=lambda: self._add_percent_band(8.0),
        ).pack(side=tk.LEFT, padx=4)
        ttk.Button(
            btns,
            text="Add ±5 % band",
            command=lambda: self._add_percent_band(5.0),
        ).pack(side=tk.LEFT)

        # Start with two empty rows so the editor isn't blank on first open.
        self._add_ref_line_row()
        self._add_ref_line_row()

    def _add_ref_line_row(
        self,
        axis: str = "y",
        value: str = "",
        label: str = "",
        color: str = "red",
        linestyle: str = "--",
    ) -> None:
        row = ttk.Frame(self.lines_container)
        row.pack(fill=tk.X, pady=1)

        axis_var = tk.StringVar(value=axis)
        value_var = tk.StringVar(value=value)
        label_var = tk.StringVar(value=label)
        color_var = tk.StringVar(value=color)
        ls_var = tk.StringVar(value=linestyle)

        ttk.Combobox(
            row, textvariable=axis_var, values=["y", "x"],
            width=3, state="readonly",
        ).pack(side=tk.LEFT, padx=2)
        ttk.Entry(row, textvariable=value_var, width=10).pack(
            side=tk.LEFT, padx=2
        )
        ttk.Entry(row, textvariable=label_var, width=22).pack(
            side=tk.LEFT, padx=2
        )
        ttk.Combobox(
            row, textvariable=color_var, values=LINE_COLORS, width=9,
        ).pack(side=tk.LEFT, padx=2)
        ttk.Combobox(
            row, textvariable=ls_var, values=LINE_STYLES, width=4,
            state="readonly",
        ).pack(side=tk.LEFT, padx=2)

        entry = {
            "frame": row,
            "axis": axis_var,
            "value": value_var,
            "label": label_var,
            "color": color_var,
            "linestyle": ls_var,
        }

        def _remove() -> None:
            row.destroy()
            try:
                self._ref_line_rows.remove(entry)
            except ValueError:
                pass
            self._schedule_preview()

        ttk.Button(row, text="✕", width=2, command=_remove).pack(
            side=tk.LEFT, padx=2
        )

        # Live preview hook for every editable field.
        for var in (axis_var, value_var, label_var, color_var, ls_var):
            var.trace_add("write", lambda *_: self._schedule_preview())

        self._ref_line_rows.append(entry)
        self._schedule_preview()

    def _add_percent_band(self, pct: float) -> None:
        """Shortcut: drop in ``y=+pct`` and ``y=-pct`` rows."""
        self._add_ref_line_row(
            axis="y", value=f"{pct:g}", label=f"+{pct:g} %",
            color="red", linestyle="--",
        )
        self._add_ref_line_row(
            axis="y", value=f"{-pct:g}", label=f"-{pct:g} %",
            color="red", linestyle="--",
        )

    # ----- Tab: Advanced -----

    def _build_tab_advanced(self) -> None:
        tab = ttk.Frame(self.notebook, padding=8)
        self.notebook.add(tab, text="Advanced")

        ttk.Label(
            tab,
            text=(
                "Optional raw YAML appended to the generated entry "
                "(merged at the top level). Use this for `variants:` and "
                "`subplots:` until they get a dedicated editor."
            ),
            foreground="gray",
            wraplength=460,
        ).pack(anchor="w", pady=4)

        self.txt_advanced = scrolledtext.ScrolledText(
            tab, wrap=tk.WORD, height=24, font=("Consolas", 9)
        )
        self.txt_advanced.pack(fill=tk.BOTH, expand=True)
        # Plain Text widget doesn't have a Variable - bind to KeyRelease.
        self.txt_advanced.bind("<KeyRelease>", self._schedule_preview)

    # ----- Form helpers -----

    def _add_entry(
        self, parent: tk.Widget, row: int, label: str, var: tk.StringVar
    ) -> int:
        ttk.Label(parent, text=label).grid(
            row=row, column=0, sticky="w", padx=2, pady=2
        )
        e = ttk.Entry(parent, textvariable=var)
        e.grid(row=row, column=1, columnspan=2, sticky="ew", padx=2, pady=2)
        var.trace_add("write", lambda *_: self._schedule_preview())
        return row + 1

    def _add_entry_with_auto(
        self,
        parent: tk.Widget,
        row: int,
        label: str,
        var: tk.StringVar,
        auto_var: tk.BooleanVar,
    ) -> tuple[int, ttk.Entry]:
        """Entry paired with an "auto" checkbox. Returns ``(next_row, entry)``.

        When the checkbox is on, the entry is read-only and gets its value
        from ``_auto_fill_all``. When off, the entry is editable.
        """
        ttk.Label(parent, text=label).grid(
            row=row, column=0, sticky="w", padx=2, pady=2
        )
        initial_state = "readonly" if auto_var.get() else "normal"
        e = ttk.Entry(parent, textvariable=var, state=initial_state)
        e.grid(row=row, column=1, sticky="ew", padx=2, pady=2)
        var.trace_add("write", lambda *_: self._schedule_preview())

        def _on_toggle() -> None:
            if auto_var.get():
                e.config(state="readonly")
                # Re-derive the field immediately so the user sees the
                # suggested value the moment they flip the switch on.
                self._auto_fill_all()
            else:
                e.config(state="normal")

        ttk.Checkbutton(
            parent, text="auto", variable=auto_var, command=_on_toggle
        ).grid(row=row, column=2, sticky="w", padx=2)
        return row + 1, e

    def _add_combobox(
        self,
        parent: tk.Widget,
        row: int,
        label: str,
        var: tk.StringVar,
        values: list[str],
        on_change=None,
        return_widget: bool = False,
    ):
        ttk.Label(parent, text=label).grid(
            row=row, column=0, sticky="w", padx=2, pady=2
        )
        cb = ttk.Combobox(parent, textvariable=var, values=values, state="normal")
        cb.grid(row=row, column=1, sticky="ew", padx=2, pady=2)
        if on_change is not None:
            cb.bind("<<ComboboxSelected>>", lambda *_: on_change())
            var.trace_add("write", lambda *_: on_change())
        if return_widget:
            return cb
        return row + 1

    # ----- Dropdown population -----

    def _populate_data_dropdowns(self) -> None:
        names = sorted(self.master_df["lcl_name"].unique().tolist())
        self.cb_lcl_name["values"] = names
        if names and not self.var_lcl_name.get():
            self.var_lcl_name.set(names[0])

    def _populate_filter_dropdowns(self) -> None:
        doses = sorted(self.master_df["dose_krad"].dropna().unique().tolist())
        _set_listbox(
            self.lb_include_doses, [self._fmt_dose(d) for d in doses]
        )
        _set_listbox(
            self.lb_exclude_doses, [self._fmt_dose(d) for d in doses]
        )
        sns = sorted(self.master_df["lcl_serial_number"].unique().tolist())
        _set_listbox(self.lb_exclude_sn, sns)

        lot_vals = sorted({v for v in self.lot_by_sn.values() if v})
        bias_vals = sorted({v for v in self.bias_by_sn.values() if v})
        self.root.nametowidget(
            self._find_combobox_for_var(self.var_lot)
        )["values"] = ["(any)"] + lot_vals
        self.root.nametowidget(
            self._find_combobox_for_var(self.var_bias)
        )["values"] = ["(any)"] + bias_vals

    def _find_combobox_for_var(self, var: tk.StringVar) -> str:
        """Walk the widget tree to find the Combobox bound to ``var``.

        Simpler than threading widget refs everywhere, and only used once
        at startup so the brute-force walk is fine.
        """
        def _walk(w: tk.Widget) -> str | None:
            if isinstance(w, ttk.Combobox) and w.cget("textvariable") == str(var):
                return str(w)
            for child in w.winfo_children():
                found = _walk(child)
                if found:
                    return found
            return None

        result = _walk(self.root)
        if not result:
            raise RuntimeError("Combobox for var not found")
        return result

    def _fmt_dose(self, d: float) -> str:
        if d == int(d):
            return f"{int(d)}"
        return f"{d:g}"

    # ----- Cascading dropdowns -----

    def _on_lcl_change(self) -> None:
        if self.master_df is None:
            return
        sub = self.master_df[self.master_df["lcl_name"] == self.var_lcl_name.get()]
        mts = sorted(sub["measurement_type"].unique().tolist())
        self.cb_measurement_type["values"] = mts
        if mts and self.var_measurement_type.get() not in mts:
            self.var_measurement_type.set(mts[0])
        else:
            self._on_mt_change()

    def _on_mt_change(self) -> None:
        if self.master_df is None:
            return
        sub = self.master_df[
            (self.master_df["lcl_name"] == self.var_lcl_name.get())
            & (self.master_df["measurement_type"] == self.var_measurement_type.get())
        ]
        metrics = sorted(sub["metric"].unique().tolist())
        _set_listbox(self.lb_metric, metrics)
        # Pre-select first metric so preview has something.
        if metrics:
            self.lb_metric.selection_set(0)
        self._on_metric_change()

    def _on_metric_change(self, *_args) -> None:
        if self.master_df is None:
            return
        selected_metrics = _listbox_selected(self.lb_metric)
        sub = self.master_df[
            (self.master_df["lcl_name"] == self.var_lcl_name.get())
            & (self.master_df["measurement_type"] == self.var_measurement_type.get())
        ]
        if selected_metrics:
            sub = sub[sub["metric"].isin(selected_metrics)]

        # context_keys
        cks = sorted({c for c in sub["context_key"].unique() if c})
        self.cb_context_key["values"] = [""] + cks

        # stages
        stages = _sort_stages(sub["irradiation_stage"].unique().tolist())
        _set_listbox(self.lb_stages, stages)
        self.cb_delta_from["values"] = stages
        self.cb_delta_to["values"] = stages

        self._schedule_preview()

    def _on_type_change(self) -> None:
        # Just trigger preview; the spec builder picks the right fields.
        self._schedule_preview()

    # ----- Spec building -----

    def _build_spec(self) -> dict[str, Any]:
        """Translate form state into a plot_spec dict suitable for plotters."""
        spec: dict[str, Any] = {}

        name = self.var_name.get().strip() or "preview"
        spec["name"] = name
        spec["output_name"] = name  # plotters._save_figure uses this
        spec["type"] = self.var_type.get()

        if self.var_title.get().strip():
            spec["title"] = self.var_title.get().strip()
        if self.var_note.get().strip():
            spec["note"] = self.var_note.get().strip()

        if self.var_lcl_name.get():
            spec["lcl_name"] = self.var_lcl_name.get()
        if self.var_measurement_type.get():
            spec["measurement_type"] = self.var_measurement_type.get()

        metrics = _listbox_selected(self.lb_metric)
        if not metrics:
            raise ValueError("Select at least one `metric`.")
        spec["metric"] = metrics[0] if len(metrics) == 1 else metrics

        if self.var_context_key.get().strip():
            spec["context_key"] = self.var_context_key.get().strip()

        ptype = spec["type"]
        if ptype == "delta":
            if not self.var_delta_from.get() or not self.var_delta_to.get():
                raise ValueError("Pick both `delta_from` and `delta_to`.")
            spec["delta_from"] = self.var_delta_from.get()
            spec["delta_to"] = self.var_delta_to.get()
        else:
            stages = _sort_stages(_listbox_selected(self.lb_stages))
            if not stages:
                raise ValueError("Select at least one stage.")
            if ptype == "annealing":
                spec["stages_order"] = stages
            else:
                spec["stages"] = stages

        # ---- Filters ----
        inc = _listbox_selected(self.lb_include_doses)
        if inc:
            spec["include_doses"] = [float(x) for x in inc]
        exc = _listbox_selected(self.lb_exclude_doses)
        if exc:
            spec["exclude_doses"] = [float(x) for x in exc]
        ex_sn = _listbox_selected(self.lb_exclude_sn)
        if ex_sn:
            spec["exclude_sn"] = ex_sn
        if self.var_lot.get() and self.var_lot.get() != "(any)":
            spec["lot"] = self.var_lot.get()
        if self.var_bias.get() and self.var_bias.get() != "(any)":
            spec["bias"] = self.var_bias.get()

        tri = self.var_show_before.get()
        if tri == "true":
            spec["show_before_at_zero"] = True
        elif tri == "false":
            spec["show_before_at_zero"] = False

        # ---- Appearance ----
        if self.var_y_label.get().strip():
            spec["y_label"] = self.var_y_label.get().strip()
        if self.var_x_label.get().strip():
            spec["x_label"] = self.var_x_label.get().strip()
        xlim = _parse_number_list(self.var_x_lim.get())
        if len(xlim) == 2:
            spec["x_lim"] = xlim
        elif xlim:
            raise ValueError("`x_lim` must be two numbers")
        ylim = _parse_number_list(self.var_y_lim.get())
        if len(ylim) == 2:
            spec["y_lim"] = ylim
        elif ylim:
            raise ValueError("`y_lim` must be two numbers")
        if self.var_x_scale.get() and self.var_x_scale.get() != "linear":
            spec["x_scale"] = self.var_x_scale.get()
        if self.var_y_scale.get() and self.var_y_scale.get() != "linear":
            spec["y_scale"] = self.var_y_scale.get()
        figsize = _parse_number_list(self.var_figsize.get())
        if len(figsize) == 2:
            spec["figsize"] = figsize
        elif figsize:
            raise ValueError("`figsize` must be two numbers")
        if self.var_dpi.get().strip():
            try:
                spec["dpi"] = int(self.var_dpi.get())
            except ValueError as e:
                raise ValueError(f"`dpi` must be an integer: {e}")
        if self.var_format.get() and self.var_format.get() != "png":
            spec["format"] = self.var_format.get()
        for var, key in (
            (self.var_grid, "grid"),
            (self.var_legend, "legend"),
            (self.var_show_points, "show_points"),
        ):
            v = var.get()
            if v == "true":
                spec[key] = True
            elif v == "false":
                spec[key] = False
        lines_sel = _listbox_selected(self.lb_show_lines)
        if lines_sel:
            spec["show_lines"] = lines_sel
        if self.var_marker_size.get().strip():
            try:
                spec["marker_size"] = int(self.var_marker_size.get())
            except ValueError as e:
                raise ValueError(f"`marker_size` must be an integer: {e}")
        if self.var_alpha_points.get().strip():
            try:
                spec["alpha_points"] = float(self.var_alpha_points.get())
            except ValueError as e:
                raise ValueError(f"`alpha_points` must be a number: {e}")

        # ---- Reference lines ----
        ref_lines: list[dict[str, Any]] = []
        for idx, row in enumerate(self._ref_line_rows):
            raw_val = row["value"].get().strip()
            if not raw_val:
                continue
            try:
                pos = float(raw_val)
            except ValueError:
                raise ValueError(
                    f"reference_lines row {idx + 1}: '{raw_val}' is not a number"
                )
            line: dict[str, Any] = {row["axis"].get(): pos}
            lab = row["label"].get().strip()
            if lab:
                line["label"] = lab
            col = row["color"].get().strip()
            if col and col != "red":
                line["color"] = col
            ls = row["linestyle"].get().strip()
            if ls and ls != "--":
                line["linestyle"] = ls
            ref_lines.append(line)
        if ref_lines:
            spec["reference_lines"] = ref_lines

        # ---- Grouping ----
        split = [d for d in SPLIT_DIMS if self.split_vars[d].get()]
        if split:
            spec["split_by"] = split
        series = [d for d in SPLIT_DIMS if self.series_vars[d].get()]
        if series:
            spec["series_by"] = series

        # ---- Advanced raw YAML merge ----
        advanced_text = self.txt_advanced.get("1.0", tk.END).strip()
        if advanced_text:
            try:
                extra = yaml.safe_load(advanced_text)
            except yaml.YAMLError as e:
                raise ValueError(f"Advanced YAML parse error: {e}")
            if not isinstance(extra, dict):
                raise ValueError("Advanced YAML must be a mapping at the top level.")
            spec.update(extra)

        # Fold defaults in so the plotter has stage_styles / stage_labels.
        merged = dict(self.defaults)
        merged.update(spec)
        return merged

    # ----- Auto-fill of name / title / labels -----

    _METRIC_PRETTY_FALLBACK = {
        "iq_ua": "I_Q",
        "ish_ua": "I_SH",
        "rdson_mohm": "R_DS(ON)",
        "vout_slew_rate": "V_OUT slew rate",
        "time_to_10pct": "Turn-on time (10%)",
        "measured_falling_v": "V_EN falling",
        "measured_rising_v": "V_EN rising",
    }

    def _pretty_metric(self, metric: str) -> str:
        return self._METRIC_PRETTY_FALLBACK.get(metric, metric)

    def _lookup_unit(self, metric: str) -> str:
        """Return the most common ``unit`` for ``metric`` in master_df."""
        if self.master_df is None or not metric:
            return ""
        sub = self.master_df[self.master_df["metric"] == metric]
        if sub.empty:
            return ""
        units = [str(u) for u in sub["unit"].dropna().unique() if str(u).strip()]
        return units[0] if units else ""

    def _suggest_name(self) -> str:
        parts: list[str] = []
        if self.var_lcl_name.get():
            parts.append(self.var_lcl_name.get())
        if self.var_measurement_type.get():
            parts.append(self.var_measurement_type.get())
        metrics = _listbox_selected(self.lb_metric)
        if len(metrics) == 1:
            parts.append(metrics[0])
        elif len(metrics) > 1:
            parts.append("multi")
        ck = self.var_context_key.get().strip()
        if ck:
            parts.append(ck)
        parts.append(self.var_type.get())
        for d in SPLIT_DIMS:
            if self.split_vars[d].get():
                parts.append(f"by_{d}")
        for d in SPLIT_DIMS:
            if self.series_vars[d].get():
                parts.append(f"series_{d}")
        slug = re.sub(r"[^A-Za-z0-9]+", "_", "_".join(parts)).strip("_")
        return slug or "preview"

    def _suggest_title(self) -> str:
        lcl = self.var_lcl_name.get() or "?"
        metrics = _listbox_selected(self.lb_metric)
        if len(metrics) == 1:
            mlabel = self._pretty_metric(metrics[0])
        elif len(metrics) > 1:
            mlabel = " & ".join(self._pretty_metric(m) for m in metrics)
        else:
            mlabel = "(no metric)"

        ck = self.var_context_key.get().strip()
        # Pretty-print common context keys (iload_a=1.0 -> "I_load=1.0 A").
        ctx = ""
        if ck:
            ctx = f" @ {self._pretty_context(ck)}"

        ptype = self.var_type.get()
        if ptype == "delta":
            df, dt = self.var_delta_from.get(), self.var_delta_to.get()
            stages = f" ({df} → {dt})" if df and dt else ""
            return f"{lcl} - Delta {mlabel}{stages}{ctx} [%]"
        if ptype == "annealing":
            return f"{lcl} - {mlabel} recovery during annealing{ctx}"
        return f"{lcl} - {mlabel} vs dose{ctx}"

    @staticmethod
    def _pretty_context(ck: str) -> str:
        """Try to turn 'iload_a=1.0' into 'I_load=1.0 A'."""
        m = re.match(r"^iload_a=([\d.]+)$", ck)
        if m:
            return f"I_load={m.group(1)} A"
        return ck

    def _suggest_y_label(self) -> str:
        metrics = _listbox_selected(self.lb_metric)
        if not metrics:
            return ""
        if len(metrics) > 1:
            # No common unit guaranteed; just label by measurement_type.
            return self.var_measurement_type.get() or ""
        metric = metrics[0]
        pretty = self._pretty_metric(metric)
        unit = self._lookup_unit(metric)
        suffix = ""
        if self.var_type.get() == "delta":
            return f"Δ {pretty} [%]"
        if unit:
            suffix = f" [{unit}]"
        return f"{pretty}{suffix}"

    def _suggest_x_label(self) -> str:
        if self.var_type.get() == "annealing":
            return "Stage"
        return "Dose [kRad]"

    def _auto_fill_all(self) -> None:
        """Recompute every auto-managed field. Cheap, idempotent."""
        if self.master_df is None:
            return
        was_suppressed = self._suppress_preview
        self._suppress_preview = True
        try:
            mapping = (
                (self.var_auto_name, self.var_name, self._suggest_name),
                (self.var_auto_title, self.var_title, self._suggest_title),
                (self.var_auto_y_label, self.var_y_label, self._suggest_y_label),
                (self.var_auto_x_label, self.var_x_label, self._suggest_x_label),
            )
            for auto_flag, target_var, suggest_fn in mapping:
                if not auto_flag.get():
                    continue
                new_val = suggest_fn()
                if target_var.get() != new_val:
                    target_var.set(new_val)
        finally:
            self._suppress_preview = was_suppressed

    # ----- Preview -----

    def _schedule_preview(self, *_args) -> None:
        if self._suppress_preview:
            return
        if self._preview_job is not None:
            self.root.after_cancel(self._preview_job)
        self._preview_job = self.root.after(500, self._refresh_preview)

    def _refresh_preview(self) -> None:
        self._preview_job = None
        if self.master_df is None:
            return
        # Run auto-fill BEFORE building the spec so the rendered preview
        # matches what Generate YAML would emit.
        self._auto_fill_all()
        self.fig.clear()
        try:
            spec = self._build_spec()
            self._render_into_fig(spec)
        except ValueError as exc:
            ax = self.fig.add_subplot(111)
            ax.text(
                0.5, 0.5, str(exc),
                ha="center", va="center", transform=ax.transAxes,
                color="#cc4444", fontsize=11, wrap=True,
            )
            ax.set_axis_off()
            self.status_var.set(f"Preview: {exc}")
        except Exception as exc:  # noqa: BLE001
            ax = self.fig.add_subplot(111)
            ax.text(
                0.5, 0.5, f"{type(exc).__name__}: {exc}",
                ha="center", va="center", transform=ax.transAxes,
                color="#cc4444", fontsize=10, wrap=True,
            )
            ax.set_axis_off()
            self.status_var.set(f"Preview error: {exc}")
            logger.exception("Preview failed")
        else:
            self.status_var.set("Preview OK.")
        try:
            self.fig.tight_layout()
        except Exception:
            pass
        self.canvas.draw()

    def _render_into_fig(self, spec: dict[str, Any]) -> None:
        """Draw ``spec`` onto ``self.fig`` without saving to disk."""
        ptype = spec["type"]
        if ptype == "absolute":
            ax = self.fig.add_subplot(111)
            _, _, has = plotters._draw_absolute_on_ax(
                ax, self.master_df, spec, self.reference_sns
            )
            if not has:
                ax.text(
                    0.5, 0.5, "No data after filtering.",
                    ha="center", va="center", transform=ax.transAxes,
                    color="#999999", fontsize=11,
                )
        elif ptype == "delta":
            ax = self.fig.add_subplot(111)
            _, _, has = plotters._draw_delta_on_ax(
                ax, self.master_df, spec, self.reference_sns
            )
            if not has:
                ax.text(
                    0.5, 0.5, "No SN has both delta_from and delta_to.",
                    ha="center", va="center", transform=ax.transAxes,
                    color="#999999", fontsize=11,
                )
        elif ptype == "annealing":
            self._render_annealing(spec)
        else:
            raise ValueError(f"Unknown plot type: {ptype!r}")

    def _render_annealing(self, spec: dict[str, Any]) -> None:
        """Inline annealing preview (no save)."""
        stages_order = spec["stages_order"]
        stage_labels = spec.get("stage_labels", {})

        irr_df = dl.filter_for_plot(
            self.master_df,
            lcl_name=spec["lcl_name"],
            measurement_type=spec["measurement_type"],
            metric=spec["metric"],
            context_key=spec.get("context_key"),
            stages=stages_order,
            exclude_sn=spec.get("exclude_sn"),
            include_doses=spec.get("include_doses"),
            exclude_doses=spec.get("exclude_doses"),
            exclude_reference=self.reference_sns,
            lot=spec.get("lot"),
            bias=spec.get("bias"),
        )

        # Reference subset (same filtering for lot/bias as in plot_annealing)
        ref_df = self.master_df[
            (self.master_df["lcl_name"] == spec["lcl_name"])
            & (self.master_df["measurement_type"] == spec["measurement_type"])
            & (self.master_df["metric"] == spec["metric"])
            & (self.master_df["lcl_serial_number"].isin(self.reference_sns))
            & (self.master_df["irradiation_stage"].isin(stages_order))
        ].copy()
        if spec.get("context_key"):
            ref_df = ref_df[ref_df["context_key"] == spec["context_key"]]
        if spec.get("lot"):
            ref_df = ref_df[ref_df["lot"].astype(str) == str(spec["lot"])]
        if spec.get("bias"):
            ref_df = ref_df[ref_df["bias"].astype(str) == str(spec["bias"])]
        has_ref = not ref_df.empty

        if irr_df.empty and not has_ref:
            ax = self.fig.add_subplot(111)
            ax.text(
                0.5, 0.5, "No data after filtering.",
                ha="center", va="center", transform=ax.transAxes,
                color="#999999", fontsize=11,
            )
            return

        if has_ref:
            ax_main = self.fig.add_subplot(1, 2, 1)
            ax_ref = self.fig.add_subplot(1, 2, 2, sharey=ax_main)
            self.fig.subplots_adjust(wspace=0.3)
        else:
            ax_main = self.fig.add_subplot(111)
            ax_ref = None

        all_doses = sorted(irr_df["dose_krad"].dropna().unique().tolist())
        plotters._plot_annealing_on_axes(
            ax_main,
            irr_df,
            stages_order=stages_order,
            stage_labels=stage_labels,
            show_points=spec.get("show_points", True),
            show_lines=spec.get("show_lines", ["mean"]),
            marker_size=spec.get("marker_size", 40),
            alpha_points=spec.get("alpha_points", 0.65),
            group_by=spec.get("group_by", "dose"),
            all_doses_for_color=all_doses,
        )
        plotters._style_axes(
            ax_main, spec,
            default_x_label="Stage",
            default_y_label=spec.get("y_label", "Value"),
        )
        if spec.get("y_scale"):
            plotters._apply_axis_scales(ax_main, {"y_scale": spec["y_scale"]})
        plotters._apply_axis_limits(ax_main, spec)

        if ax_ref is not None:
            stage_to_x = {s: i for i, s in enumerate(stages_order)}
            ax_ref.set_xticks(list(stage_to_x.values()))
            ax_ref.set_xticklabels(
                [stage_labels.get(s, s) for s in stages_order],
                rotation=20, ha="right",
            )
            for sn, sub in ref_df.groupby("lcl_serial_number"):
                sub = sub.assign(x_pos=sub["irradiation_stage"].map(stage_to_x))
                sub = sub.dropna(subset=["x_pos"]).sort_values("x_pos")
                ax_ref.plot(
                    sub["x_pos"], sub["value_num"], marker="o", linewidth=1.4,
                    label=f"{sn} (ref)", color="#7f7f7f",
                )
            ax_ref.set_title("Reference")
            ax_ref.grid(True, linestyle="--", alpha=0.4)
            if ax_ref.get_legend_handles_labels()[0]:
                ax_ref.legend(loc="best", fontsize=8, framealpha=0.9)

    # ----- Generate / copy / reset -----

    def _emit_yaml(self) -> str:
        """Return a YAML snippet describing the current plot spec.

        ``defaults`` are stripped out so the snippet only carries the
        per-plot overrides the user actually picked.
        """
        full_spec = self._build_spec()
        # Subtract defaults-merged keys so we emit a minimal entry.
        snippet: dict[str, Any] = {}
        for key, value in full_spec.items():
            if key in ("output_name",):
                continue
            if key in self.defaults and self.defaults[key] == value:
                continue
            snippet[key] = value
        ordered = _ordered_yaml(snippet)
        return yaml.safe_dump(
            [ordered], sort_keys=False, allow_unicode=True, default_flow_style=False
        )

    def _on_generate(self) -> None:
        try:
            text = self._emit_yaml()
        except ValueError as exc:
            messagebox.showerror("Cannot generate YAML", str(exc))
            return
        target_dir = self.config_path.parent / "_generated_plots"
        target_dir.mkdir(parents=True, exist_ok=True)
        name = self.var_name.get().strip() or "preview"
        out_path = target_dir / f"{name}.yaml"
        try:
            out_path.write_text(text, encoding="utf-8")
        except OSError as exc:
            messagebox.showerror("Write failed", str(exc))
            return
        self.status_var.set(f"Wrote {out_path}")
        messagebox.showinfo(
            "YAML written",
            f"Saved snippet to:\n{out_path}\n\n"
            "Paste it under the `plots:` list in plot_config.yaml.",
        )

    def _on_copy(self) -> None:
        try:
            text = self._emit_yaml()
        except ValueError as exc:
            messagebox.showerror("Cannot copy YAML", str(exc))
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.root.update()  # keep clipboard contents after window closes
        self.status_var.set("YAML copied to clipboard.")

    def _on_reset(self) -> None:
        if not messagebox.askyesno(
            "Reset form", "Clear every field and reset to defaults?"
        ):
            return
        # Cheapest reset: relaunch.
        self.root.destroy()
        new_root = tk.Tk()
        PlotBuilder(new_root, self.config_path)
        new_root.mainloop()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="GUI builder for radiation_plot YAML entries."
    )
    p.add_argument(
        "--config",
        type=Path,
        default=HERE / "radiation_plot" / "plot_config.yaml",
        help="Existing plot_config.yaml (used for paths + defaults + data).",
    )
    return p.parse_args()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    args = _parse_args()

    cfg_path = args.config
    if not cfg_path.is_file():
        # Let the user pick a config interactively.
        root = tk.Tk()
        root.withdraw()
        picked = filedialog.askopenfilename(
            title="Select plot_config.yaml",
            filetypes=[("YAML", "*.yaml *.yml"), ("All files", "*.*")],
        )
        root.destroy()
        if not picked:
            print("No config selected, aborting.", file=sys.stderr)
            return 2
        cfg_path = Path(picked)

    root = tk.Tk()
    PlotBuilder(root, cfg_path)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
