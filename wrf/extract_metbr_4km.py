#!/usr/bin/env python3
import argparse, gzip, json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from netCDF4 import Dataset

REFLECTIVITY_NAMES = (
    "REFL_10CM", "REFL_10CM_NATIVE", "REFLECTIVITY_10CM", "REFLECTIVITY",
    "REFL", "DBZ10", "DBZ", "ZH",
)


def _as_2d(value, expected_shape=None):
    a = np.ma.asarray(value)
    if np.ma.isMaskedArray(a):
        a = a.filled(np.nan)
    a = np.asarray(a, dtype=np.float32)
    if a.ndim < 2:
        return None
    if expected_shape is not None and tuple(a.shape[-2:]) != tuple(expected_shape):
        return None
    if a.ndim > 2:
        axes = tuple(range(a.ndim - 2))
        with np.errstate(all="ignore"):
            a = np.nanmax(a, axis=axes)
    if a.ndim != 2 or not np.isfinite(a).any():
        return None
    return a.astype(np.float32)


def native_reflectivity(ds, expected_shape=None):
    names = list(ds.variables.keys())
    ordered = [n for n in REFLECTIVITY_NAMES if n in ds.variables]
    for n in names:
        u = n.upper()
        if any(k in u for k in ("REFL", "REFLECT", "DBZ", "ZH")) and n not in ordered:
            ordered.append(n)

    for name in ordered:
        try:
            refl = _as_2d(ds.variables[name][:], expected_shape)
        except Exception:
            continue
        if refl is not None:
            print(f"[METBR][REFL] refletividade nativa encontrada: {name}, shape={refl.shape}")
            return refl, name
    return None, None


def find_reflectivity_in_run(run_dir, expected_shape):
    root = Path(run_dir)
    candidates = sorted(root.glob("wrfout_d01_*"))
    for pattern in ("*.nc", "*.nc4", "*.cdf", "*reflect*", "*refl*", "*dbz*"):
        candidates.extend(sorted(p for p in root.glob(pattern) if p.is_file() and p not in candidates))

    for path in candidates:
        try:
            with Dataset(path) as ds:
                refl, name = native_reflectivity(ds, expected_shape)
                if refl is not None:
                    return refl, name, path.name
        except Exception as exc:
            print(f"[METBR][DIAG] falha lendo {path.name}: {exc}")
    return None, None, None


def read_time(ds):
    times = ds.variables.get("Times")
    if times is None:
        raise RuntimeError("Times ausente no wrfout")
    raw = np.asarray(times[:])
    chars = raw[0] if raw.ndim == 2 else raw
    text = ''.join(x.decode() if isinstance(x, bytes) else str(x) for x in chars)
    return text.replace('_', 'T') + 'Z'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--run-dir', required=True)
    ap.add_argument('--output-dir', required=True)
    ap.add_argument('--run-date', required=True)
    ap.add_argument('--run-cycle', required=True)
    args = ap.parse_args()

    run_dir = Path(args.run_dir)
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    frame_dir = out / 'icon'
    frame_dir.mkdir(parents=True, exist_ok=True)

    files = sorted(run_dir.glob('wrfout_d01_*'))
    if not files:
        raise SystemExit('Nenhum wrfout_d01 encontrado')

    frames = []
    init_time = None
    grid_x = grid_y = None

    for index, fn in enumerate(files):
        with Dataset(fn) as ds:
            lat = ds.variables.get('XLAT')
            lon = ds.variables.get('XLONG')
            expected_shape = None
            if lat is not None and lon is not None:
                expected_shape = tuple(np.asarray(lat[:]).shape[-2:])
                if expected_shape != tuple(np.asarray(lon[:]).shape[-2:]):
                    raise RuntimeError(f'XLAT/XLONG com grades diferentes em {fn.name}')

            refl, source_name = native_reflectivity(ds, expected_shape)
            source_file = fn.name
            if refl is None and expected_shape is not None:
                print(f'[METBR][DIAG] {fn.name}: refletividade não encontrada no próprio WRFOUT; procurando nos NetCDF da rodada')
                refl, source_name, source_file = find_reflectivity_in_run(run_dir, expected_shape)
            if refl is None:
                raise RuntimeError(f'Nenhuma variável de refletividade nativa encontrada para {fn.name}; nenhuma aproximação dBZ será usada')

            if refl.ndim != 2:
                raise RuntimeError(f'Grade inesperada em {fn.name}: {refl.shape}')
            grid_y, grid_x = map(int, refl.shape)
            valid_time = read_time(ds)
            if init_time is None:
                init_time = valid_time

            arr = np.asarray(refl, dtype=np.float32)
            arr = np.where(np.isfinite(arr), arr, -9999.0)
            forecast_hour = index
            target = frame_dir / f'f{forecast_hour:03d}.json.gz'
            payload = {
                'forecastHour': forecast_hour,
                'validTime': valid_time,
                'gridX': grid_x,
                'gridY': grid_y,
                'reflectivityDbz': arr.tolist(),
                'reflectivitySource': 'REFL_10CM_NATIVE',
                'nativeGrid': True,
                'sourceVariable': source_name,
                'sourceFile': source_file,
            }
            with gzip.open(target, 'wt', encoding='utf-8', compresslevel=6) as fh:
                json.dump(payload, fh, separators=(',', ':'))

            finite = arr[arr > -9000]
            stats = {
                'maxDbz': round(float(np.max(finite)), 2) if finite.size else None,
                'p99Dbz': round(float(np.percentile(finite, 99)), 2) if finite.size else None,
                'positivePixels': int(np.count_nonzero(finite > 0)) if finite.size else 0,
                'fractionAbove5Dbz': round(float(np.mean(finite > 5)), 6) if finite.size else 0.0,
                'fractionAbove40Dbz': round(float(np.mean(finite > 40)), 6) if finite.size else 0.0,
            }
            frames.append({
                'index': index,
                'forecastHour': forecast_hour,
                'validTime': valid_time,
                'file': f'icon/f{forecast_hour:03d}.json.gz',
                'gridX': grid_x,
                'gridY': grid_y,
                'source': 'METBR WRF 4 KM ICON (REFL_10CM nativo)',
                'reflectivitySource': 'REFL_10CM_NATIVE',
                'nativeGrid': True,
                'sourceVariable': source_name,
                'sourceFile': source_file,
                'reflectivityStats': stats,
            })

    generated = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
    cycle = args.run_cycle if args.run_cycle.endswith('Z') else args.run_cycle + 'Z'
    metadata = {
        'schema': 'sideral-wrf-metadata-v1',
        'model': 'icon',
        'resolutionKm': 4,
        'runDate': args.run_date,
        'runCycle': cycle,
        'initTime': init_time,
        'generatedAt': generated,
        'reflectivitySource': 'REFL_10CM_NATIVE',
        'nativeGrid': True,
        'grid': {'nx': grid_x, 'ny': grid_y},
        'frameCount': len(frames),
        'temporalResolutionMinutes': 60,
        'frames': frames,
    }
    (out / 'metadata.json').write_text(json.dumps(metadata, indent=2), encoding='utf-8')
    print(f'METBR 4 km validado: {args.run_date} {cycle}, {len(frames)} frames, {grid_x}x{grid_y}')


if __name__ == '__main__':
    main()
