#!/usr/bin/env python3
import argparse
import json
import math
import os
from pathlib import Path

import numpy as np
from netCDF4 import Dataset


def read_time(ds):
    times = ds.variables.get("Times")
    if times is None:
        return ""
    raw = np.asarray(times[:])
    if raw.ndim == 2:
        chars = raw[0]
    else:
        chars = raw
    text = "".join(x.decode() if isinstance(x, bytes) else str(x) for x in chars)
    return text.replace("_", "T") + "Z"


def native_reflectivity(ds):
    for name in ("REFL_10CM", "REFL_10CM_NATIVE"):
        if name not in ds.variables:
            continue
        a = np.asarray(ds.variables[name][:])
        if a.ndim == 3:
            a = np.nanmax(a, axis=0)
        elif a.ndim != 2:
            continue
        return np.asarray(a, dtype=np.float32)
    raise RuntimeError("REFL_10CM/REFL_10CM_NATIVE ausente; refletividade aproximada nao e permitida")


def grid_shape(ds):
    lat = ds.variables.get("XLAT")
    lon = ds.variables.get("XLONG")
    if lat is None or lon is None:
        raise RuntimeError("XLAT/XLONG ausentes no WRFOUT")
    shape = np.asarray(lat[:]).shape[-2:]
    if shape != np.asarray(lon[:]).shape[-2:]:
        raise RuntimeError("XLAT/XLONG com grades diferentes")
    return int(shape[1]), int(shape[0])


def clean_array(a):
    return np.nan_to_num(a, nan=-9999.0, posinf=-9999.0, neginf=-9999.0).tolist()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--output-dir", required=True)
    args = ap.parse_args()

    files = sorted(Path(args.run_dir).glob("wrfout_d01_*"))
    if not files:
        raise SystemExit("Nenhum wrfout_d01 encontrado")

    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)

    frames = []
    nx = ny = None
    init_time = ""

    for index, path in enumerate(files):
        with Dataset(path) as ds:
            x, y = grid_shape(ds)
            refl = native_reflectivity(ds)
            if refl.shape != (y, x):
                raise RuntimeError(f"Grade inesperada em {path.name}: {refl.shape}, esperado {(y, x)}")
            if nx is None:
                nx, ny = x, y
            elif (nx, ny) != (x, y):
                raise RuntimeError("Os WRFOUTs possuem grades diferentes")

            t = read_time(ds)
            if not init_time:
                init_time = t

            frames.append({
                "file": path.name,
                "time": t,
                "forecastHour": index,
                "reflectivityDbz": clean_array(refl),
            })

    payload = {
        "schemaVersion": "1.0",
        "model": "METBR WRF 4 km",
        "modelKey": "metbr_wrf",
        "resolutionKm": 4,
        "grid": {"nx": nx, "ny": ny},
        "frames": frames,
        "frameCount": len(frames),
        "temporalResolutionMinutes": 60,
        "reflectivitySource": "REFL_10CM_NATIVE",
        "nativeGrid": True,
        "initTime": init_time,
    }

    metadata = {
        "schemaVersion": "1.0",
        "model": "METBR WRF 4 km",
        "modelKey": "metbr_wrf",
        "resolutionKm": 4,
        "grid": {"nx": nx, "ny": ny},
        "frames": [
            {"file": f["file"], "time": f["time"], "forecastHour": f["forecastHour"]}
            for f in frames
        ],
        "frameCount": len(frames),
        "temporalResolutionMinutes": 60,
        "reflectivitySource": "REFL_10CM_NATIVE",
        "nativeGrid": True,
        "initTime": init_time,
    }

    (out / "metbr_wrf_4km.json").write_text(
        json.dumps(payload, separators=(",", ":"), allow_nan=False), encoding="utf-8"
    )
    (out / "metadata.json").write_text(
        json.dumps(metadata, separators=(",", ":"), allow_nan=False), encoding="utf-8"
    )
    print(f"METBR JSON publicado: {len(frames)} frames, grade {nx}x{ny}")


if __name__ == "__main__":
    main()
