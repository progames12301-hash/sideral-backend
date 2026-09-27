#!/usr/bin/env python3
import argparse
import json
from pathlib import Path

import numpy as np
from netCDF4 import Dataset

REFLECTIVITY_NAMES = (
    "REFL_10CM",
    "REFL_10CM_NATIVE",
    "REFLECTIVITY_10CM",
    "REFLECTIVITY",
    "REFL",
    "DBZ10",
    "DBZ",
    "ZH",
)


def read_time(ds):
    times = ds.variables.get("Times")
    if times is None:
        return ""
    raw = np.asarray(times[:])
    chars = raw[0] if raw.ndim == 2 else raw
    text = "".join(x.decode() if isinstance(x, bytes) else str(x) for x in chars)
    return text.replace("_", "T") + "Z"


def describe_variables(ds):
    names = sorted(ds.variables.keys())
    radar = [n for n in names if any(k in n.upper() for k in ("REFL", "REFLECT", "DBZ", "ZH"))]
    return names, radar


def _as_2d_reflectivity(value, name, expected_shape=None):
    a = np.ma.asarray(value)
    if np.ma.isMaskedArray(a):
        a = a.filled(np.nan)
    a = np.asarray(a, dtype=np.float32)
    if a.ndim < 2:
        return None
    if expected_shape is not None and tuple(a.shape[-2:]) != tuple(expected_shape):
        return None
    if a.ndim > 2:
        reduce_axes = tuple(range(a.ndim - 2))
        with np.errstate(all="ignore"):
            a = np.nanmax(a, axis=reduce_axes)
    if a.ndim != 2 or not np.isfinite(a).any():
        return None
    return np.asarray(a, dtype=np.float32)


def try_reflectivity_file(path, expected_shape=None):
    try:
        with Dataset(path) as ds:
            names, radar_names = describe_variables(ds)
            print(f"[METBR][DIAG] {path.name}: {len(names)} variaveis")
            print(f"[METBR][DIAG] {path.name}: candidatas de refletividade: {radar_names or 'nenhuma'}")
            ordered = [n for n in REFLECTIVITY_NAMES if n in ds.variables]
            for n in radar_names:
                if n not in ordered:
                    ordered.append(n)
            for name in ordered:
                var = ds.variables[name]
                print(f"[METBR][DIAG] {path.name}:{name}: dimensoes={getattr(var, 'dimensions', ())} shape={getattr(var, 'shape', ())}")
                refl = _as_2d_reflectivity(var[:], name, expected_shape)
                if refl is None:
                    continue
                print(f"[METBR] refletividade nativa encontrada: {path.name}:{name} shape={refl.shape}")
                return refl, name, path.name
    except Exception as exc:
        print(f"[METBR][DIAG] falha lendo {path}: {exc}")
    return None, None, None


def find_native_reflectivity(run_dir, expected_shape=None):
    root = Path(run_dir)
    candidates = sorted(root.glob("wrfout_d01_*"))
    others = []
    for pattern in ("*.nc", "*.nc4", "*.cdf", "*reflect*", "*refl*", "*dbz*"):
        for p in root.glob(pattern):
            if p not in candidates and p.is_file():
                others.append(p)
    candidates += sorted(set(others))
    if not candidates:
        raise RuntimeError(f"Nenhum NetCDF/WRFOUT encontrado em {run_dir}")
    for path in candidates:
        refl, source, source_file = try_reflectivity_file(path, expected_shape)
        if refl is not None:
            return refl, source, source_file
    raise RuntimeError(
        "Nenhuma variável de refletividade nativa foi encontrada. "
        "Os arquivos possuem candidatas, mas nenhuma tinha uma grade horizontal "
        "compatível com XLAT/XLONG. Veja [METBR][DIAG] dimensoes/shape. "
        "Nenhuma aproximação dBZ será usada."
    )


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
    source_descriptions = set()

    for index, path in enumerate(files):
        with Dataset(path) as ds:
            x, y = grid_shape(ds)
            expected = (y, x)
            refl, source_name, source_file = try_reflectivity_file(path, expected)
            if refl is None:
                refl, source_name, source_file = find_native_reflectivity(args.run_dir, expected)
            if source_name is None or source_file is None:
                raise RuntimeError(f"Fonte nativa não identificada para {path.name}")
            source_descriptions.add(f"{source_file}:{source_name}")
            if refl.shape != expected:
                raise RuntimeError(f"Grade inesperada em {path.name}: {refl.shape}, esperado {expected}")
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

    sources = sorted(source_descriptions)
    payload = {
        "schemaVersion": "1.0",
        "model": "METBR WRF 4 km",
        "modelKey": "metbr_wrf",
        "resolutionKm": 4,
        "grid": {"nx": nx, "ny": ny},
        "frames": frames,
        "frameCount": len(frames),
        "temporalResolutionMinutes": 60,
        "reflectivitySource": sources,
        "nativeGrid": True,
        "initTime": init_time,
    }
    metadata = {
        "schemaVersion": "1.0",
        "model": "METBR WRF 4 km",
        "modelKey": "metbr_wrf",
        "resolutionKm": 4,
        "grid": {"nx": nx, "ny": ny},
        "frames": [{"file": f["file"], "time": f["time"], "forecastHour": f["forecastHour"]} for f in frames],
        "frameCount": len(frames),
        "temporalResolutionMinutes": 60,
        "reflectivitySource": sources,
        "nativeGrid": True,
        "initTime": init_time,
    }
    (out / "metbr_wrf_4km.json").write_text(json.dumps(payload, separators=(",", ":"), allow_nan=False), encoding="utf-8")
    (out / "metadata.json").write_text(json.dumps(metadata, separators=(",", ":"), allow_nan=False), encoding="utf-8")
    print(f"METBR JSON publicado: {len(frames)} frames, grade {nx}x{ny}")
    print(f"[METBR] fontes nativas usadas: {', '.join(sources)}")


if __name__ == "__main__":
    main()
