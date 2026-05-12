# radiation_plot

Generate PNG plots from radiation-test flat CSV files produced by
`make_flat_files.py`.

The script is configuration-driven. You describe each plot once in YAML
and the script renders all of them, writing a `_plot_index.csv` summary
alongside the PNGs.

## Layout

```
plot_radiation.py            # CLI entry point
radiation_plot/
├── __init__.py
├── config.py                # YAML loading + validation
├── data_loader.py           # flat CSV -> tidy DataFrame, dose attach, repeats average
└── plotters.py              # absolute / delta / annealing renderers
examples/
├── dose_map.yaml            # SN -> dose mapping
└── plot_config.yaml         # plot definitions
```

## Requirements

```
python >= 3.10
pandas
numpy
matplotlib
pyyaml
```

Install in a virtual env:

```bash
pip install pandas numpy matplotlib pyyaml
```

## Quick start

1. Run `make_flat_files.py` to produce `*_flat.csv` per device.
2. Edit `examples/dose_map.yaml` to map SNs to doses.
3. Edit `examples/plot_config.yaml` and point `data.flat_files_dir` at
   the folder produced in step 1.
4. Generate plots:

```bash
python plot_radiation.py --config examples/plot_config.yaml
```

Run only a subset, useful while iterating on one plot:

```bash
python plot_radiation.py --config examples/plot_config.yaml --only TPS2553_iq_absolute_stats
```

Validate the config and see how many rows would feed each plot, without
rendering:

```bash
python plot_radiation.py --config examples/plot_config.yaml --dry-run
```

## Plot types

### `absolute`

Raw measurement value vs dose. One coloured series per `stage` in
`stages:`. Reference samples are excluded.

Required: `lcl_name`, `measurement_type`, `metric`, `stages`.

### `delta`

Per-SN change between `delta_from` and `delta_to`, plotted against dose.
Always rendered as **relative percent change** — any `delta_mode` field
in the YAML is ignored. Only SNs that have measurements in both stages
contribute. A reference line at zero is drawn automatically.

Required: `lcl_name`, `measurement_type`, `metric`, `delta_from`,
`delta_to`.

### `annealing`

Trend across an ordered list of stages, one line per dose group on the
main subplot. If the dose map declares any `reference_sns`, a narrower
panel on the right shows the reference samples in grey so you can see
control drift over the same timeline.

Required: `lcl_name`, `measurement_type`, `metric`, `stages_order`.

## Variants

To emit several PNGs from a single entry (e.g. one with stats lines,
one with raw points only), add a `variants:` list:

```yaml
- name: "TPS2553_iq_views"
  type: absolute
  ...
  variants:
    - suffix: "_stats_only"
      show_points: false
      show_lines: [min, max, mean]
    - suffix: "_points_only"
      show_points: true
      show_lines: []
```

The final filename is `<name><suffix>.png`. Each variant inherits the
plot's settings, then overrides them with its own fields.

## Common filters

Available on every plot:

| Field              | Effect                                               |
|--------------------|------------------------------------------------------|
| `context_key`      | Restrict to one variant (e.g. `iload_a=1.0`)         |
| `exclude_sn`       | Drop specific serial numbers from this plot only     |
| `include_doses`    | Keep only the listed doses (kRad)                    |
| `exclude_doses`    | Drop the listed doses                                |
| `lot`              | Keep only one lot, e.g. `A` or `B`                   |
| `bias`             | Keep only `bias` or `unbias` samples                 |
| `x_lim`, `y_lim`   | Two-element lists; omitting them = matplotlib auto   |
| `x_scale`,`y_scale`| `linear` (default), `log`, or `symlog`               |

> On `annealing` plots the X axis is categorical (stage names), so
> `x_scale` is ignored; `y_scale` works as expected.

## Splitting into lot / bias panels

Use `split_by:` to emit one PNG per group along the listed dimension(s)
without writing the same plot block several times:

```yaml
- name: "TPS2553_iq_delta_by_lot_bias"
  type: delta
  ...
  split_by: [lot, bias]   # -> 4 PNGs: _lotA_bias, _lotA_unbias,
                          #           _lotB_bias, _lotB_unbias
```

Accepted values: `lot`, `bias`, or any subset of `[lot, bias]`. The
actual lot / bias names come from the dose map (`lots:` and
`bias_groups:`). If the dose map declares no groups for a dimension,
that dimension is skipped with a warning.

`split_by` and `variants:` both expand the plot block — they compose,
so a plot with three variants and `split_by: lot` produces `3 × n_lots`
PNGs.

## Reference (control) samples

The dose map's `reference_sns:` list selects which serial numbers are
treated as controls. They are:

- **excluded** from `absolute` and `delta` plots
- **shown on a separate right subplot** of every `annealing` plot

If `reference_sns:` is omitted, the loader treats any SN with `dose == 0`
as a reference.

## Output

The output directory contains:

- one PNG per plot (or per variant)
- `_plot_index.csv` listing every plot attempt with its parameters,
  point count, and skip reason if it produced no output

A plot that finds no data after filtering is logged as a warning and
recorded in the index with `skipped=True` rather than aborting the run.
