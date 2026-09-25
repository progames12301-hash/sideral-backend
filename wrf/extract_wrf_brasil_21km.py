#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
from pathlib import Path

import numpy as np
from netCDF4 import Dataset

EXPECTED_NX = 215
EXPECTED_NY = 215
EXPECTED_DX = 21000
EXPECTED_DY = 21000
EXPECTED_FRAMES = 49


def valid_time(path: Path) -> dt.datetime:
    match = re.search(r"wrfout_d01_(\d{4}-\d{2}-\d{2})_(\d{2})[:_-](\d{2})[:_-](\d{2})", path.name)
    if not match:
        raise RuntimeError(f"Nome wrfout inesperado: {path.name}")
    return dt.datetime.strptime(
        f"{match.group(1)} {match.group(2)}:{match.group(3)}:{match.group(4)}",
        "%Y-%m-%d %H:%M:%S",
    ).replace(tzinfo=dt.timezone.utc)


def read_times(ds: Dataset, fallback: dt.datetime) -> str:
    if "Times" not in ds.variables:
        return fallback.strftime("%Y-%m-%dT%H:%M:%SZ")
    raw = np.asarray(ds.variables["Times"][:])
    if raw.ndim != 2 or raw.shape[0] < 1:
        return fallback.strftime("%Y-%m-%dT%H:%M:%SZ")
    chars = raw[0]
    text = "".join(x.decode("ascii") if isinstance(x, bytes) else str(x) for x in chars)
    return text.replace("_", "T") + "Z"


def native_reflectivity(ds: Dataset, path: Path) -> np.ndarray:
    if "REFL_10CM" not in ds.variables:
        raise RuntimeError(
            f"REFL_10CM ausente em {path.name}; Brasil 21 km exige refletividade nativa e nao usa substituto."
        )
    variable = ds.variables["REFL_10CM"]
    values = np.asarray(variable[:], dtype=np.float32)
    if values.ndim == 4:
        values = values[0]
    if values.ndim == 3:
        values = np.nanmax(values, axis=0)
    elif values.ndim != 2:
        raise RuntimeError(f"REFL_10CM com shape inesperado em {path.name}: {values.shape}")
    if values.shape != (EXPECTED_NY, EXPECTED_NX):
        raise RuntimeError(
            f"Grade de refletividade inesperada em {path.name}: {values.shape}; "
            f"esperado {(EXPECTED_NY, EXPECTED_NX)}"
        )
    return np.nan_to_num(values, nan=-9999.0, posinf=-9999.0, neginf=-9999.0).astype(np.float32)


def sea_level_pressure(ds: Dataset, path: Path) -> np.ndarray:
    """Retorna SLP em hPa para isobaras. Usa SLP/SLP_P se presente; nunca inventa campo."""
    name = next((candidate for candidate in ("slp", "SLP", "PMSL", "MSLP") if candidate in ds.variables), None)
    if name is None:
        raise RuntimeError(f"Campo de pressao ao nivel do mar (SLP/MSLP) ausente em {path.name}; nao e possivel gerar isobaras reais.")
    values = np.asarray(ds.variables[name][:], dtype=np.float32)
    while values.ndim > 2:
        values = values[0]
    if values.shape != (EXPECTED_NY, EXPECTED_NX):
        raise RuntimeError(f"SLP com shape inesperado em {path.name}: {values.shape}")
    # WRF normalmente grava SLP em hPa; somente converte se os valores forem inequivocamente Pa.
    finite = values[np.isfinite(values)]
    if finite.size and float(np.nanmedian(finite)) > 2000.0:
        values = values / 100.0
    values = np.nan_to_num(values, nan=-9999.0, posinf=-9999.0, neginf=-9999.0)
    return values.astype(np.float32)


def inspect_grid(ds: Dataset, path: Path) -> None:
    if "XLAT" not in ds.variables or "XLONG" not in ds.variables:
        raise RuntimeError(f"XLAT/XLONG ausentes em {path.name}")
    lat = np.asarray(ds.variables["XLAT"][0])
    lon = np.asarray(ds.variables["XLONG"][0])
    if lat.shape != (EXPECTED_NY, EXPECTED_NX) or lon.shape != (EXPECTED_NY, EXPECTED_NX):
        raise RuntimeError(
            f"Grade nativa inesperada em {path.name}: XLAT={lat.shape}, XLONG={lon.shape}; "
            f"esperado {(EXPECTED_NY, EXPECTED_NX)}"
        )
    dx = int(round(float(getattr(ds, "DX", 0))))
    dy = int(round(float(getattr(ds, "DY", 0))))
    if dx != EXPECTED_DX or dy != EXPECTED_DY:
        raise RuntimeError(f"DX/DY inesperados em {path.name}: {dx}/{dy} m; esperado 21000/21000 m")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--grid-x", type=int, default=EXPECTED_NX)
    parser.add_argument("--grid-y", type=int, default=EXPECTED_NY)
    args = parser.parse_args()

    if args.grid_x != EXPECTED_NX or args.grid_y != EXPECTED_NY:
        raise SystemExit(f"Brasil 21 km exige grade {EXPECTED_NX}x{EXPECTED_NY}; recebido {args.grid_x}x{args.grid_y}")

    run_dir = Path(args.run_dir)
    output = Path(args.output_dir)
    files = sorted(run_dir.glob("wrfout_d01_*"), key=valid_time)
    if len(files) != EXPECTED_FRAMES:
        raise SystemExit(f"Esperados {EXPECTED_FRAMES} wrfout F000-F048; encontrados {len(files)}")

    times = [valid_time(path) for path in files]
    init_time = times[0]
    expected = [init_time + dt.timedelta(hours=i) for i in range(EXPECTED_FRAMES)]
    if times != expected:
        raise SystemExit("Os 49 wrfout nao formam a sequencia horaria F000-F048 sem lacunas")

    output.mkdir(parents=True, exist_ok=True)
    json_path = output / "wrf_brasil_21km.json"
    temp_path = output / "wrf_brasil_21km.json.tmp"

    first_stats = None
    with temp_path.open("w", encoding="utf-8") as stream:
        stream.write('{"schemaVersion":"1.1","model":"WRF Brasil","resolutionKm":21,')
        stream.write('"grid":{"nx":215,"ny":215,"dxMeters":21000,"dyMeters":21000},')
        stream.write('"reflectivitySource":"REFL_10CM_NATIVE","pressureSource":"SLP_NATIVE","nativeGrid":true,')
        stream.write(f'"initTime":{json.dumps(init_time.strftime("%Y-%m-%dT%H:%M:%SZ"))},')
        stream.write('"temporalResolutionMinutes":60,"frameCount":49,"frames":[')

        for index, path in enumerate(files):
            with Dataset(path, "r") as ds:
                inspect_grid(ds, path)
                refl = native_reflectivity(ds, path)
                slp = sea_level_pressure(ds, path)
                t = read_times(ds, times[index])
            if index == 0:
                valid = refl[refl > -9000]
                first_stats = {
                    "minDbz": float(np.nanmin(valid)) if valid.size else None,
                    "maxDbz": float(np.nanmax(valid)) if valid.size else None,
                    "minSlpHpa": float(np.nanmin(slp[slp > -9000])) if np.any(slp > -9000) else None,
                    "maxSlpHpa": float(np.nanmax(slp[slp > -9000])) if np.any(slp > -9000) else None,
                }
            if index:
                stream.write(",")
            frame = {
                "forecastHour": index,
                "time": t,
                "reflectivityDbz": refl.tolist(),
                "seaLevelPressureHpa": slp.tolist(),
            }
            json.dump(frame, stream, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
            print(f"Brasil 21 km: F{index:03d} {path.name}")

        stream.write(']}')

    temp_path.replace(json_path)
    metadata = {
        "schemaVersion": "1.1",
        "model": "WRF Brasil",
        "resolutionKm": 21,
        "nx": EXPECTED_NX,
        "ny": EXPECTED_NY,
        "dxMeters": EXPECTED_DX,
        "dyMeters": EXPECTED_DY,
        "frameCount": EXPECTED_FRAMES,
        "temporalResolutionMinutes": 60,
        "reflectivitySource": "REFL_10CM_NATIVE",
        "pressureSource": "SLP_NATIVE",
        "nativeGrid": True,
        "initTime": init_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "lastValidTime": times[-1].strftime("%Y-%m-%dT%H:%M:%SZ"),
        "firstFrameReflectivity": first_stats,
    }
    metadata_path = output / "metadata.json"
    metadata_tmp = output / "metadata.json.tmp"
    metadata_tmp.write_text(json.dumps(metadata, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
    metadata_tmp.replace(metadata_path)

    print(f"Extracao Brasil 21 km concluida: {EXPECTED_FRAMES} frames, 215x215, REFL_10CM nativo + SLP nativo")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"ERRO EXTRATOR WRF BRASIL 21 KM: {exc}", file=sys.stderr)
        raise
