#!/usr/bin/env python3
import argparse
import csv
import re
import struct
from pathlib import Path
from typing import BinaryIO, Tuple

import numpy as np


def read_exact(f: BinaryIO, n: int) -> bytes:
    b = f.read(n)
    if len(b) != n:
        raise EOFError(f"Unexpected EOF (wanted {n} bytes, got {len(b)} bytes)")
    return b


def cstr(b: bytes) -> str:
    return b.split(b"\x00", 1)[0].decode("ascii", errors="replace").strip()


def parse_file_header(f: BinaryIO) -> Tuple[str, int, int, int]:
    cookie = read_exact(f, 2).decode("ascii", errors="replace")
    version = struct.unpack("<H", read_exact(f, 2))[0]
    file_size = struct.unpack("<I", read_exact(f, 4))[0]
    n_wfm = struct.unpack("<I", read_exact(f, 4))[0]
    return cookie, version, file_size, n_wfm


def parse_waveform_header(f: BinaryIO) -> dict:
    raw = read_exact(f, 140)
    off = 0

    def u32():
        nonlocal off
        v = struct.unpack_from("<I", raw, off)[0]
        off += 4
        return v

    def i32():
        nonlocal off
        v = struct.unpack_from("<i", raw, off)[0]
        off += 4
        return v

    def f32():
        nonlocal off
        v = struct.unpack_from("<f", raw, off)[0]
        off += 4
        return v

    def f64():
        nonlocal off
        v = struct.unpack_from("<d", raw, off)[0]
        off += 8
        return v

    def bytes_n(n):
        nonlocal off
        v = raw[off:off + n]
        off += n
        return v

    hdr_size = u32()
    wfm_type = u32()
    num_buf = u32()
    points = u32()
    count = u32()
    x_disp_range = f32()
    x_disp_origin = f64()
    x_inc = f64()
    x_org = f64()
    x_units = i32()
    y_units = i32()
    date = cstr(bytes_n(16))
    time = cstr(bytes_n(16))
    frame = cstr(bytes_n(24))
    label = cstr(bytes_n(16))
    time_tags = f64()
    seg_idx = u32()

    return {
        "header_size": hdr_size,
        "waveform_type": wfm_type,
        "num_buffers": num_buf,
        "points": points,
        "count": count,
        "x_display_range": x_disp_range,
        "x_display_origin": x_disp_origin,
        "x_increment": x_inc,
        "x_origin": x_org,
        "x_units": x_units,
        "y_units": y_units,
        "date": date,
        "time": time,
        "frame": frame,
        "label": label,
        "time_tags": time_tags,
        "segment_index": seg_idx,
    }


def parse_data_header(f: BinaryIO) -> dict:
    raw = read_exact(f, 12)
    data_hdr_size, buf_type, bpp, buf_size = struct.unpack("<IhhI", raw)
    return {
        "data_header_size": data_hdr_size,
        "buffer_type": buf_type,
        "bytes_per_point": bpp,
        "buffer_size": buf_size,
    }


def read_waveform_data(f: BinaryIO, points: int, dh: dict) -> np.ndarray:
    buf = read_exact(f, dh["buffer_size"])
    bpp = dh["bytes_per_point"]

    if bpp == 4:
        data = np.frombuffer(buf, dtype="<f4", count=points)
    elif bpp == 2:
        data = np.frombuffer(buf, dtype="<i2", count=points).astype(np.float64)
    elif bpp == 1:
        data = np.frombuffer(buf, dtype=np.int8, count=points).astype(np.float64)
    elif bpp == 8:
        data = np.frombuffer(buf, dtype="<f8", count=points)
    else:
        raise ValueError(f"Unsupported bytes_per_point={bpp} (buffer_type={dh['buffer_type']})")

    if data.size != points:
        data = data[:points]
    return data


def write_csv(path: Path, t: np.ndarray, y: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["time_s", "value"])
        w.writerows(zip(t, y))


def extract_device_folder_name(file_stem: str) -> str:
    match = re.match(r"^([A-Za-z0-9]+_\d+)", file_stem)
    if match:
        return match.group(1)
    return "unknown_device"


def convert_bin_file(bin_path: Path, out_root: Path, single: bool = False) -> list[Path]:
    created_files: list[Path] = []
    base = bin_path.stem
    device_folder = extract_device_folder_name(base)
    target_dir = out_root / device_folder

    with open(bin_path, "rb") as f:
        cookie, ver, file_size, n_wfm = parse_file_header(f)

        if cookie not in ("RG", "AG"):
            raise ValueError(
                f"Unknown cookie '{cookie}' (expected 'RG' or 'AG'). "
                f"File may not be a valid Rigol/Agilent SaveWave .bin"
            )

        print(f"[BIN] {bin_path}")
        print(f"      Cookie={cookie} Version={ver} FileSize={file_size} Waveforms={n_wfm}")

        for i in range(n_wfm):
            wh = parse_waveform_header(f)
            dh = parse_data_header(f)
            y = read_waveform_data(f, wh["points"], dh)

            x0 = wh["x_origin"]
            dx = wh["x_increment"]
            t = x0 + np.arange(y.size, dtype=np.float64) * dx

            if n_wfm == 1 or single:
                out_name = f"{base}.csv"
            else:
                out_name = f"{base}_{i:02d}.csv"

            out_path = target_dir / out_name
            write_csv(out_path, t, y)
            created_files.append(out_path)

            print(
                f"      [{i + 1}/{n_wfm}] -> {out_path} "
                f"points={y.size} dx={dx:g}s bpp={dh['bytes_per_point']}"
            )

            if single:
                break

    return created_files


def scan_and_convert(input_dir: Path, out_root: Path, recursive: bool = True, single: bool = False) -> None:
    if recursive:
        all_files = sorted(p for p in input_dir.rglob("*") if p.is_file())
        bin_files = sorted(p for p in input_dir.rglob("*.bin") if p.is_file())
    else:
        all_files = sorted(p for p in input_dir.glob("*") if p.is_file())
        bin_files = sorted(p for p in input_dir.glob("*.bin") if p.is_file())

    print(f"Folder wejściowy : {input_dir}")
    print(f"Wszystkich plików: {len(all_files)}")
    print(f"Plików .bin      : {len(bin_files)}")
    print(f"Folder wyjściowy : {out_root}")

    if not bin_files:
        print("Nie znaleziono żadnych plików .bin do konwersji.")
        return

    ok = 0
    failed = 0
    failed_files: list[tuple[Path, str]] = []

    for idx, bin_file in enumerate(bin_files, start=1):
        print(f"\n--- Przetwarzanie pliku {idx}/{len(bin_files)} ---")
        try:
            convert_bin_file(bin_file, out_root, single=single)
            ok += 1
        except Exception as e:
            failed += 1
            failed_files.append((bin_file, str(e)))
            print(f"[ERROR] {bin_file}: {e}")

    print("\n=== PODSUMOWANIE ===")
    print(f"Znaleziono plików .bin : {len(bin_files)}")
    print(f"Skonwertowano poprawnie: {ok}")
    print(f"Nie udało się          : {failed}")
    print(f"Folder wyjściowy       : {out_root}")

    if failed_files:
        print("\nLista plików, których nie udało się skonwertować:")
        for file_path, error_msg in failed_files:
            print(f" - {file_path}")
            print(f"   Błąd: {error_msg}")


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Scan folder for Rigol/Agilent oscilloscope .bin files and convert them to CSV"
    )
    ap.add_argument("input_dir", help="folder to scan for .bin files")
    ap.add_argument(
        "-o",
        "--outdir",
        default="csv_out",
        help="output root directory for CSV files (default: csv_out)",
    )
    ap.add_argument(
        "--non-recursive",
        action="store_true",
        help="scan only the top-level folder (default: recursive scan)",
    )
    ap.add_argument(
        "--single",
        action="store_true",
        help="write only first waveform from each .bin file",
    )
    args = ap.parse_args()

    input_dir = Path(args.input_dir).resolve()
    out_root = Path(args.outdir).resolve()

    if not input_dir.exists() or not input_dir.is_dir():
        raise NotADirectoryError(f"Input path is not a directory: {input_dir}")

    out_root.mkdir(parents=True, exist_ok=True)

    scan_and_convert(
        input_dir=input_dir,
        out_root=out_root,
        recursive=not args.non_recursive,
        single=args.single,
    )


if __name__ == "__main__":
    main()