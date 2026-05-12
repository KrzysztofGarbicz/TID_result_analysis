#!/usr/bin/env python3
import argparse
import os
import struct
import csv
from typing import BinaryIO, List, Tuple

import numpy as np


def read_exact(f: BinaryIO, n: int) -> bytes:
    b = f.read(n)
    if len(b) != n:
        raise EOFError(f"Unexpected EOF (wanted {n} bytes, got {len(b)})")
    return b


def cstr(b: bytes) -> str:
    # ASCII z obcięciem zer i spacji
    return b.split(b"\x00", 1)[0].decode("ascii", errors="replace").strip()


def parse_file_header(f: BinaryIO) -> Tuple[str, int, int, int]:
    # Cookie(2), Version(2), FileSize(u32), NumWaveforms(u32)
    cookie = read_exact(f, 2).decode("ascii", errors="replace")
    version = struct.unpack("<H", read_exact(f, 2))[0]
    file_size = struct.unpack("<I", read_exact(f, 4))[0]
    n_wfm = struct.unpack("<I", read_exact(f, 4))[0]
    return cookie, version, file_size, n_wfm


def parse_waveform_header(f: BinaryIO) -> dict:
    # Układ zgodny z “Agilent Binary Data” gdzie Waveform Header = 140B:
    # HeaderSize(u32), WaveformType(u32), NumBuffers(u32), Points(u32), Count(u32),
    # XDisplayRange(f32), XDisplayOrigin(f64), XIncrement(f64), XOrigin(f64),
    # XUnits(i32), YUnits(i32),
    # Date(16B), Time(16B), Frame(24B), Label(16B),
    # TimeTags(f64), SegmentIndex(u32)
    raw = read_exact(f, 140)

    off = 0
    def u32():
        nonlocal off
        v = struct.unpack_from("<I", raw, off)[0]; off += 4
        return v
    def i32():
        nonlocal off
        v = struct.unpack_from("<i", raw, off)[0]; off += 4
        return v
    def f32():
        nonlocal off
        v = struct.unpack_from("<f", raw, off)[0]; off += 4
        return v
    def f64():
        nonlocal off
        v = struct.unpack_from("<d", raw, off)[0]; off += 8
        return v
    def bytes_n(n):
        nonlocal off
        v = raw[off:off+n]; off += n
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
    # Waveform Data Header = 12B:
    # DataHeaderSize(u32), BufferType(i16), BytesPerPoint(i16), BufferSize(u32)
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

    # Najczęściej (w tym formacie) analog jest float32 i buffer_type=1, bpp=4.
    # Ale dodajemy fallback zależnie od bpp.
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
        # Zdarza się, że buffer ma więcej (padding) – bierzemy tyle ile points
        data = data[:points]
    return data


def safe_name(s: str) -> str:
    s = s.strip().replace(" ", "_")
    return "".join(ch for ch in s if ch.isalnum() or ch in "._-") or "waveform"


def write_csv(path: str, t: np.ndarray, y: np.ndarray) -> None:
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["time_s", "value"])
        w.writerows(zip(t, y))


def main():
    ap = argparse.ArgumentParser(description="Convert Rigol/Agilent-style oscilloscope .bin waveform to CSV")
    ap.add_argument("bin", help="input .bin file (from Rigol MSO5000 series Save Wave)")
    ap.add_argument("-o", "--outdir", default=".", help="output directory (default: current)")
    ap.add_argument("--single", action="store_true", help="write only first waveform to CSV")
    args = ap.parse_args()

    os.makedirs(args.outdir, exist_ok=True)

    with open(args.bin, "rb") as f:
        cookie, ver, file_size, n_wfm = parse_file_header(f)

        if cookie not in ("RG", "AG"):
            raise ValueError(f"Unknown cookie '{cookie}' (expected 'RG' or 'AG'). Is this really a SaveWave .bin?")

        base = os.path.splitext(os.path.basename(args.bin))[0]
        print(f"Cookie={cookie} Version={ver} FileSize={file_size} Waveforms={n_wfm}")

        for i in range(n_wfm):
            wh = parse_waveform_header(f)
            dh = parse_data_header(f)
            y = read_waveform_data(f, wh["points"], dh)

            # oś czasu
            x0 = wh["x_origin"]
            dx = wh["x_increment"]
            t = x0 + np.arange(y.size, dtype=np.float64) * dx

            label = wh["label"] or f"wfm{i}"
            out_name = f"{base}_{i:02d}_{safe_name(label)}.csv"
            out_path = os.path.join(args.outdir, out_name)
            write_csv(out_path, t, y)

            print(f"[{i+1}/{n_wfm}] {out_name}  points={y.size}  dx={dx:g}s  label='{label}'  bpp={dh['bytes_per_point']}")

            if args.single:
                break


if __name__ == "__main__":
    main()
