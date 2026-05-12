#!/usr/bin/env python3
"""
make_flat_files.py

Agreguje wyniki pomiarów automatycznych do plików flat CSV.

Źródła danych:
1. klasyczne pliki results.csv odnajdywane rekurencyjnie w results-root
2. opcjonalne timing CSV z oscyloskopu w timing-root

Dla każdej pary (lcl_name, lcl_serial_number) tworzy osobny plik flat:
    <LCL>_<SN>_flat.csv

Dodatkowo generuje indeks:
    _flat_index.csv

Użycie:
python make_flat_files.py --results-root "D:\PomiaryTID\results" --timing-root "D:\PomiaryTID\timing_results" --output-dir "D:\PomiaryTID\flat_by_device" --verbose
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import re
from pathlib import Path
from typing import Any, Iterable

import pandas as pd


OUTPUT_COLUMNS = [
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

INDEX_COLUMNS = [
    "flat_file",
    "lcl_name",
    "lcl_serial_number",
    "row_count",
    "results_row_count",
    "timing_row_count",
    "irradiation_stage_count",
    "source_dir_count",
]

EXCLUDED_CONTEXT_KEYS_EXACT = {
    "imeas_a",
    "measured_current_a",
    "measured_voltage_v",
}

EXCLUDED_CONTEXT_KEY_PREFIXES = (
    "measured_",
)


def setup_logging(verbose: bool) -> None:
    """Konfiguruje logowanie do terminala."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(levelname)s: %(message)s",
    )


def parse_args() -> argparse.Namespace:
    """Parsuje argumenty CLI."""
    parser = argparse.ArgumentParser(
        description="Agregacja wyników results.csv i timing CSV do flat files."
    )
    parser.add_argument(
        "--results-root",
        required=True,
        type=Path,
        help="Ścieżka do głównego folderu z klasycznymi wynikami results.csv",
    )
    parser.add_argument(
        "--timing-root",
        required=False,
        type=Path,
        default=None,
        help="Opcjonalna ścieżka do folderu z timing results z oscyloskopu",
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        type=Path,
        help="Folder wyjściowy na pliki flat",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Włącza bardziej szczegółowe logowanie",
    )
    return parser.parse_args()


def ensure_directory(path: Path) -> None:
    """Tworzy katalog, jeśli nie istnieje."""
    path.mkdir(parents=True, exist_ok=True)


def safe_str(value: Any) -> str:
    """Zwraca bezpieczny string; NaN/None -> pusty string."""
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass
    return str(value).strip()


def is_blank(value: Any) -> bool:
    """Sprawdza, czy wartość jest pusta."""
    text = safe_str(value)
    return text == ""


def normalize_serial_number(value: Any) -> str:
    """
    Ustandaryzowany numer seryjny do postaci SNxx.

    Obsługuje m.in.:
    - 1 -> SN01
    - "2" -> SN02
    - "SN1" -> SN01
    - "sn02" -> SN02
    - "01" -> SN01
    """
    if value is None:
        return ""

    if isinstance(value, float):
        if math.isnan(value):
            return ""
        if value.is_integer():
            return f"SN{int(value):02d}"

    if isinstance(value, int):
        return f"SN{value:02d}"

    text = safe_str(value)
    if not text:
        return ""

    match_sn = re.fullmatch(r"(?i)sn[_\-\s]*0*(\d+)", text)
    if match_sn:
        return f"SN{int(match_sn.group(1)):02d}"

    match_num = re.fullmatch(r"0*(\d+)", text)
    if match_num:
        return f"SN{int(match_num.group(1)):02d}"

    match_tail_num = re.search(r"(?i)(?:sn[_\-\s]*)?0*(\d+)$", text)
    if match_tail_num:
        return f"SN{int(match_tail_num.group(1)):02d}"

    return text.upper()


def normalize_lcl_name(value: Any) -> str:
    """Ustandaryzowana nazwa układu."""
    return safe_str(value)


def normalize_scalar_for_context(value: Any) -> str:
    """Ustandaryzowany zapis wartości w context_key."""
    if value is None:
        return ""

    if isinstance(value, bool):
        return "true" if value else "false"

    if isinstance(value, int):
        return str(value)

    if isinstance(value, float):
        if math.isnan(value):
            return ""
        return str(value)

    return safe_str(value)


def should_exclude_context_key(key: str) -> bool:
    """Sprawdza, czy klucz z context_json ma być pominięty w context_key."""
    key_lower = key.lower()
    if key_lower in EXCLUDED_CONTEXT_KEYS_EXACT:
        return True
    return any(key_lower.startswith(prefix) for prefix in EXCLUDED_CONTEXT_KEY_PREFIXES)


def parse_context_json(value: Any) -> dict[str, Any] | None:
    """
    Parsuje context_json do dict.

    Zwraca None, jeśli:
    - wartość jest pusta
    - JSON jest niepoprawny
    - JSON nie jest słownikiem
    """
    if value is None:
        return None

    if isinstance(value, dict):
        return value

    text = safe_str(value)
    if not text:
        return None

    try:
        parsed = json.loads(text)
    except Exception:
        return None

    if isinstance(parsed, dict):
        return parsed

    return None

def parse_timestamp_from_text(text: str) -> str:
    """
    Wyciąga timestamp z napisu zawierającego fragment:
    YYYY-MM-DD_HH-MM-SS

    Zwraca format:
    YYYY-MM-DDTHH:MM:SS
    """
    if not text:
        return ""

    match = re.search(r"(\d{4}-\d{2}-\d{2})_(\d{2})-(\d{2})-(\d{2})", text)
    if not match:
        return ""

    date_part = match.group(1)
    hh = match.group(2)
    mm = match.group(3)
    ss = match.group(4)
    return f"{date_part}T{hh}:{mm}:{ss}"


def build_timing_run_id(source_file: Any) -> str:
    """
    Buduje run_id dla timingów na podstawie source_file.

    Przyjmuje, że source_file jest nazwą pliku / przebiegu bez rozszerzenia
    albo z rozszerzeniem. Zwraca basename bez rozszerzenia.
    """
    text = safe_str(source_file)
    if not text:
        return ""

    return Path(text).stem


def canonicalize_context_json(value: Any) -> str:
    """
    Zwraca ustandaryzowany JSON string dla context_json.
    Jeśli wejście jest puste lub niepoprawne, zwraca pusty string.
    """
    parsed = parse_context_json(value)
    if parsed is None:
        return ""
    try:
        return json.dumps(parsed, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except Exception:
        return ""


def parse_context_key(context_json: Any) -> str:
    """
    Buduje stabilny context_key na podstawie context_json.

    Zasady:
    - tylko dict JSON
    - sortowanie kluczy alfabetycznie
    - format: key=value|key=value
    - pomija klucze pomocnicze typu measured_* oraz kilka wskazanych jawnie
    """
    parsed = parse_context_json(context_json)
    if parsed is None:
        return ""

    items: list[str] = []
    for key in sorted(parsed.keys(), key=lambda x: str(x).lower()):
        key_str = safe_str(key)
        if not key_str:
            continue
        if should_exclude_context_key(key_str):
            continue

        value = parsed[key]
        value_str = normalize_scalar_for_context(value)
        items.append(f"{key_str}={value_str}")

    return "|".join(items)


def coerce_value_or_blank(value: Any) -> Any:
    """Zwraca pusty string dla NaN/None, w przeciwnym razie oryginalną wartość."""
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass
    return value


def infer_stage_from_results_path(csv_path: Path, results_root: Path) -> str:
    """
    Wyznacza irradiation_stage dla results.csv.

    Zakładana struktura:
    results_root/<stage>/<run_dir>/results.csv
    """
    try:
        relative = csv_path.relative_to(results_root)
        parts = relative.parts
        if len(parts) >= 3:
            return parts[0]
    except Exception:
        pass

    if csv_path.parent.parent and csv_path.parent.parent.name:
        return csv_path.parent.parent.name

    return ""


def extract_lcl_and_sn_from_text(text: str) -> tuple[str, str]:
    """
    Próbuje wyciągnąć lcl_name i serial z napisu typu:
    - TPS2553_SN01
    - timing_results_TPS25963_SN02
    - 2026-03-02_20-43-50_TPS2553_SN01
    """
    if not text:
        return "", ""

    match = re.search(r"([A-Za-z0-9]+)_SN[_\-\s]*0*(\d+)", text, flags=re.IGNORECASE)
    if match:
        lcl_name = match.group(1)
        serial = normalize_serial_number(f"SN{match.group(2)}")
        return lcl_name, serial

    return "", ""


def read_csv_safely(csv_path: Path) -> pd.DataFrame | None:
    """
    Czyta CSV w sposób odporny na pojedyncze błędy.
    Zwraca None, jeśli nie uda się odczytać.
    """
    encodings_to_try = ["utf-8", "utf-8-sig", "cp1250", "latin1"]

    for encoding in encodings_to_try:
        try:
            df = pd.read_csv(csv_path, encoding=encoding)
            return df
        except pd.errors.EmptyDataError:
            logging.warning("Pusty CSV: %s", csv_path)
            return pd.DataFrame()
        except Exception:
            continue

    logging.warning("Nie udało się odczytać CSV: %s", csv_path)
    return None


def validate_required_columns(
    df: pd.DataFrame,
    required_columns: Iterable[str],
    csv_path: Path,
) -> bool:
    """Sprawdza obecność wymaganych kolumn."""
    missing = [col for col in required_columns if col not in df.columns]
    if missing:
        logging.warning(
            "Brakujące kolumny w pliku %s: %s",
            csv_path,
            ", ".join(missing),
        )
        return False
    return True


def make_empty_output_record() -> dict[str, Any]:
    """Tworzy pusty rekord w formacie flat."""
    return {column: "" for column in OUTPUT_COLUMNS}


def parse_results_csv(csv_path: Path, results_root: Path) -> list[dict[str, Any]]:
    """
    Parsuje pojedynczy results.csv do listy rekordów flat.
    """
    df = read_csv_safely(csv_path)
    if df is None:
        return []
    if df.empty:
        logging.info("Pomijam pusty results.csv: %s", csv_path)
        return []

    required_columns = [
        "timestamp",
        "run_id",
        "lcl_name",
        "lcl_serial_number",
        "measurement_type",
        "metric",
        "value",
        "unit",
        "context_json",
    ]
    if not validate_required_columns(df, required_columns, csv_path):
        return []

    irradiation_stage = infer_stage_from_results_path(csv_path, results_root)
    source_dir_name = csv_path.parent.name

    records: list[dict[str, Any]] = []

    for _, row in df.iterrows():
        lcl_name = normalize_lcl_name(row.get("lcl_name", ""))
        lcl_serial_number = normalize_serial_number(row.get("lcl_serial_number", ""))

        if not lcl_name or not lcl_serial_number:
            logging.warning(
                "Brak lcl_name lub lcl_serial_number w %s, source_dir=%s",
                csv_path,
                source_dir_name,
            )

        context_json_raw = row.get("context_json", "")
        context_json_str = canonicalize_context_json(context_json_raw)
        context_key = parse_context_key(context_json_raw)

        record = make_empty_output_record()
        record.update(
            {
                "timestamp": safe_str(row.get("timestamp", "")),
                "run_id": safe_str(row.get("run_id", "")),
                "lcl_name": lcl_name,
                "lcl_serial_number": lcl_serial_number,
                "measurement_type": safe_str(row.get("measurement_type", "")),
                "metric": safe_str(row.get("metric", "")),
                "value": coerce_value_or_blank(row.get("value", "")),
                "unit": safe_str(row.get("unit", "")),
                "context_json": context_json_str,
                "irradiation_stage": irradiation_stage,
                "source_dir_name": source_dir_name,
                "context_key": context_key,
                "data_origin": "results",
            }
        )
        records.append(record)

    return records


def parse_timing_csv(csv_path: Path) -> list[dict[str, Any]]:
    """
    Parsuje pojedynczy timing CSV do listy rekordów flat.
    """
    df = read_csv_safely(csv_path)
    if df is None:
        return []
    if df.empty:
        logging.info("Pomijam pusty timing CSV: %s", csv_path)
        return []

    required_columns = [
        "alias",
        "test",
        "metric",
        "result",
        "unit",
        "source_file",
    ]
    if not validate_required_columns(df, required_columns, csv_path):
        return []

    parent_dir_name = csv_path.parent.name
    file_name = csv_path.name

    inferred_lcl_name, inferred_sn = extract_lcl_and_sn_from_text(parent_dir_name)
    if not inferred_lcl_name or not inferred_sn:
        alt_lcl_name, alt_sn = extract_lcl_and_sn_from_text(file_name)
        if alt_lcl_name and alt_sn:
            inferred_lcl_name, inferred_sn = alt_lcl_name, alt_sn

    if not inferred_lcl_name or not inferred_sn:
        logging.warning(
            "Nie udało się wyznaczyć lcl_name/SN dla timing CSV: %s",
            csv_path,
        )

    records: list[dict[str, Any]] = []

    for _, row in df.iterrows():
        test_name = safe_str(row.get("test", ""))
        measurement_type = f"timing_{test_name}" if test_name else "timing_"

        source_file_value = safe_str(row.get("source_file", ""))
        timing_timestamp = parse_timestamp_from_text(source_file_value)
        timing_run_id = build_timing_run_id(source_file_value)

        if not timing_timestamp:
            logging.debug(
                "Nie udało się wyciągnąć timestamp z source_file='%s' w %s",
                source_file_value,
                csv_path,
            )

        record = make_empty_output_record()
        record.update(
            {
                "timestamp": timing_timestamp,
                "run_id": timing_run_id,
                "lcl_name": inferred_lcl_name,
                "lcl_serial_number": inferred_sn,
                "measurement_type": measurement_type,
                "metric": safe_str(row.get("metric", "")),
                "value": coerce_value_or_blank(row.get("result", "")),
                "unit": safe_str(row.get("unit", "")),
                "context_json": "",
                "irradiation_stage": safe_str(row.get("alias", "")),
                "source_dir_name": source_file_value,
                "context_key": "",
                "data_origin": "timing",
            }
        )
        records.append(record)

    return records


def collect_results_records(results_root: Path) -> list[dict[str, Any]]:
    """Zbiera rekordy ze wszystkich plików results.csv."""
    if not results_root.exists():
        logging.error("results-root nie istnieje: %s", results_root)
        return []

    csv_files = sorted(results_root.rglob("results.csv"))
    logging.info("Znaleziono %d plików results.csv", len(csv_files))

    all_records: list[dict[str, Any]] = []
    for csv_path in csv_files:
        try:
            records = parse_results_csv(csv_path, results_root)
            all_records.extend(records)
        except Exception as exc:
            logging.warning("Błąd podczas przetwarzania %s: %s", csv_path, exc)

    logging.info("Zebrano %d rekordów z results.csv", len(all_records))
    return all_records


def collect_timing_records(timing_root: Path | None) -> list[dict[str, Any]]:
    """Zbiera rekordy ze wszystkich timing CSV."""
    if timing_root is None:
        logging.info("Nie podano --timing-root, pomijam timing CSV.")
        return []

    if not timing_root.exists():
        logging.warning("timing-root nie istnieje: %s", timing_root)
        return []

    csv_files = sorted(timing_root.rglob("timing_results_*.csv"))
    logging.info("Znaleziono %d plików timing CSV", len(csv_files))

    all_records: list[dict[str, Any]] = []
    for csv_path in csv_files:
        try:
            records = parse_timing_csv(csv_path)
            all_records.extend(records)
        except Exception as exc:
            logging.warning("Błąd podczas przetwarzania %s: %s", csv_path, exc)

    logging.info("Zebrano %d rekordów z timing CSV", len(all_records))
    return all_records


def sort_flat_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """Sortuje rekordy w kolejności wymaganej przez specyfikację."""
    sort_columns = [
        "irradiation_stage",
        "data_origin",
        "measurement_type",
        "metric",
        "source_dir_name",
        "timestamp",
    ]

    df_sorted = df.copy()

    for column in sort_columns:
        if column not in df_sorted.columns:
            df_sorted[column] = ""
        df_sorted[column] = df_sorted[column].fillna("").astype(str)

    return df_sorted.sort_values(by=sort_columns, kind="stable").reset_index(drop=True)


def sanitize_file_component(value: str) -> str:
    """Czyści nazwę używaną w nazwie pliku."""
    cleaned = re.sub(r'[<>:"/\\|?*]+', "_", safe_str(value))
    cleaned = cleaned.strip().strip(".")
    return cleaned or "UNKNOWN"


def build_flat_files(
    records: list[dict[str, Any]],
    output_dir: Path,
) -> list[dict[str, Any]]:
    """
    Buduje pliki flat dla każdej pary (lcl_name, lcl_serial_number).

    Zwraca listę rekordów indeksu.
    """
    ensure_directory(output_dir)

    if not records:
        logging.warning("Brak rekordów do zapisania.")
        return []

    df = pd.DataFrame(records)

    for column in OUTPUT_COLUMNS:
        if column not in df.columns:
            df[column] = ""

    df = df[OUTPUT_COLUMNS]

    missing_identity_mask = (
        df["lcl_name"].fillna("").astype(str).str.strip().eq("")
        | df["lcl_serial_number"].fillna("").astype(str).str.strip().eq("")
    )
    missing_identity_count = int(missing_identity_mask.sum())
    if missing_identity_count > 0:
        logging.warning(
            "Pominięto %d rekordów bez lcl_name lub lcl_serial_number przy budowie flatów.",
            missing_identity_count,
        )

    df = df[~missing_identity_mask].copy()

    if df.empty:
        logging.warning("Po odfiltrowaniu rekordów bez identyfikatora nie ma danych do zapisania.")
        return []

    index_records: list[dict[str, Any]] = []

    grouped = df.groupby(["lcl_name", "lcl_serial_number"], dropna=False, sort=True)

    for (lcl_name, lcl_serial_number), group_df in grouped:
        lcl_name_str = safe_str(lcl_name)
        serial_str = normalize_serial_number(lcl_serial_number)

        file_name = (
            f"{sanitize_file_component(lcl_name_str)}_"
            f"{sanitize_file_component(serial_str)}_flat.csv"
        )
        out_path = output_dir / file_name

        group_sorted = sort_flat_dataframe(group_df)
        group_sorted.to_csv(out_path, index=False, encoding="utf-8-sig")

        results_row_count = int((group_sorted["data_origin"] == "results").sum())
        timing_row_count = int((group_sorted["data_origin"] == "timing").sum())

        irradiation_stage_count = int(
            group_sorted["irradiation_stage"]
            .fillna("")
            .astype(str)
            .replace("", pd.NA)
            .dropna()
            .nunique()
        )
        source_dir_count = int(
            group_sorted["source_dir_name"]
            .fillna("")
            .astype(str)
            .replace("", pd.NA)
            .dropna()
            .nunique()
        )

        index_records.append(
            {
                "flat_file": file_name,
                "lcl_name": lcl_name_str,
                "lcl_serial_number": serial_str,
                "row_count": int(len(group_sorted)),
                "results_row_count": results_row_count,
                "timing_row_count": timing_row_count,
                "irradiation_stage_count": irradiation_stage_count,
                "source_dir_count": source_dir_count,
            }
        )

        logging.info(
            "Zapisano %s (%d wierszy: results=%d, timing=%d)",
            out_path.name,
            len(group_sorted),
            results_row_count,
            timing_row_count,
        )

    return index_records


def write_index_file(index_records: list[dict[str, Any]], output_dir: Path) -> Path:
    """Zapisuje plik _flat_index.csv."""
    ensure_directory(output_dir)
    index_path = output_dir / "_flat_index.csv"

    if not index_records:
        df = pd.DataFrame(columns=INDEX_COLUMNS)
    else:
        df = pd.DataFrame(index_records)
        for column in INDEX_COLUMNS:
            if column not in df.columns:
                df[column] = ""
        df = df[INDEX_COLUMNS]
        df = df.sort_values(
            by=["lcl_name", "lcl_serial_number", "flat_file"],
            kind="stable",
        ).reset_index(drop=True)

    df.to_csv(index_path, index=False, encoding="utf-8-sig")
    logging.info("Zapisano indeks: %s", index_path.name)
    return index_path


def main() -> int:
    """Punkt wejścia CLI."""
    args = parse_args()
    setup_logging(args.verbose)

    results_root: Path = args.results_root
    timing_root: Path | None = args.timing_root
    output_dir: Path = args.output_dir

    ensure_directory(output_dir)

    logging.info("=== Start make_flat_files.py ===")
    logging.info("results-root: %s", results_root)
    logging.info("timing-root:  %s", timing_root if timing_root else "(brak)")
    logging.info("output-dir:   %s", output_dir)

    all_records: list[dict[str, Any]] = []

    try:
        results_records = collect_results_records(results_root)
        all_records.extend(results_records)
    except Exception as exc:
        logging.error("Błąd podczas zbierania results.csv: %s", exc)

    try:
        timing_records = collect_timing_records(timing_root)
        all_records.extend(timing_records)
    except Exception as exc:
        logging.error("Błąd podczas zbierania timing CSV: %s", exc)

    logging.info("Łącznie zebrano %d rekordów.", len(all_records))

    index_records = build_flat_files(all_records, output_dir)
    write_index_file(index_records, output_dir)

    logging.info("=== Koniec make_flat_files.py ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())