#!/usr/bin/env python3
import argparse, gzip, json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from netCDF4 import Dataset


def native_reflectivity(ds):
    for name in ("REFL_10CM", "REFL_10CM_NATIVE"):
        if name in ds.variables:
            a = np.asarray(ds.variables[name][:])
            if a.ndim == 3:
                return np.nanmax(a, axis=0).astype(np.float32)
            if a.ndim == 2:
                return a.astype(np.float32)
    raise RuntimeError("REFL_10CM/REFL_10CM_NATIVE ausente; METBR exige refletividade nativa")


def read_time(ds):
    times = ds.variables.get("Times")
    if times is None:
        raise RuntimeError("Times ausente no wrfout")
    raw = times[:]
    if raw.ndim == 2:
        text = ''.join(x.decode() if isinstance(x, bytes) else str(x) for x in raw[0])
    else:
        text = ''.join(x.decode() if isinstance(x, bytes) else str(x) for x in raw)
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
            refl = native_reflectivity(ds)
            if refl.ndim != 2:
                raise RuntimeError(f'Grade inesperada em {fn}: {refl.shape}')
            grid_y, grid_x = map(int, refl.shape)
            valid_time = read_time(ds)
            if init_time is None:
                init_time = valid_time

            # WRF output may contain fill values/infinite values. Keep missing pixels
            # explicit and do not manufacture reflectivity.
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
