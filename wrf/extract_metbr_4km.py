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

# Campos de superfície/diagnóstico que fazem sentido diretamente no mapa 2-D.
# Cada item é publicado somente se existir de fato no WRFOUT.
METBR_SURFACE_FIELDS = {
    "temperature2m": ("T2",),
    "humidity2m": ("RH2", "RELHUM2"),
    "pressureSurface": ("PSFC",),
    "u10": ("U10",),
    "v10": ("V10",),
    "precipAccum": ("RAINNC", "RAINC"),
    "pblHeight": ("PBLH",),
    "cape": ("CAPE", "MCAPE", "MUCAPE"),
    "cin": ("CIN", "MCIN", "MUCIN"),
    "skinTemperature": ("TSK",),
    "surfaceAlbedo": ("ALBEDO",),
    "swdown": ("SWDOWN",),
    "lwdown": ("GLW", "LWDNB"),
}


def _to_2d(value, expected_shape=None):
    a = np.ma.asarray(value)
    if np.ma.isMaskedArray(a):
        a = a.filled(np.nan)
    a = np.asarray(a, dtype=np.float32)
    if a.ndim < 2:
        return None
    if expected_shape is not None and tuple(a.shape[-2:]) != tuple(expected_shape):
        return None
    if a.ndim > 2:
        with np.errstate(all="ignore"):
            a = np.nanmax(a, axis=tuple(range(a.ndim - 2)))
    if a.ndim != 2 or not np.isfinite(a).any():
        return None
    return a.astype(np.float32)


def _json_array(a):
    return np.where(np.isfinite(a), a, -9999.0).astype(np.float32).tolist()


def native_reflectivity(ds, expected_shape=None):
    names = list(ds.variables.keys())
    ordered = [n for n in REFLECTIVITY_NAMES if n in ds.variables]
    for n in names:
        u = n.upper()
        if any(k in u for k in ("REFL", "REFLECT", "DBZ", "ZH")) and n not in ordered:
            ordered.append(n)
    for name in ordered:
        try:
            refl = _to_2d(ds.variables[name][:], expected_shape)
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


def discover_variables(ds):
    """Inventaria todas as variáveis disponíveis sem fabricar nenhuma."""
    inventory = {}
    for name, var in ds.variables.items():
        item = {
            "name": name,
            "dimensions": list(getattr(var, "dimensions", ())),
            "shape": list(getattr(var, "shape", ())),
        }
        for attr in ("units", "description", "long_name", "MemoryOrder", "stagger"):
            if hasattr(var, attr):
                value = getattr(var, attr)
                if isinstance(value, bytes):
                    value = value.decode(errors="replace")
                if np.isscalar(value):
                    item[attr] = str(value)
        try:
            if np.issubdtype(var.dtype, np.number):
                a = np.ma.asarray(var[:])
                if np.ma.isMaskedArray(a):
                    a = a.filled(np.nan)
                finite = np.asarray(a, dtype=np.float64)
                finite = finite[np.isfinite(finite)]
                if finite.size:
                    item["min"] = float(np.min(finite))
                    item["max"] = float(np.max(finite))
        except Exception as exc:
            item["statsError"] = str(exc)
        inventory[name] = item
    return inventory


def select_surface_fields(ds, expected_shape):
    fields = {}
    sources = {}
    for public_name, candidates in METBR_SURFACE_FIELDS.items():
        for candidate in candidates:
            if candidate not in ds.variables:
                continue
            try:
                arr = _to_2d(ds.variables[candidate][:], expected_shape)
            except Exception as exc:
                print(f"[METBR][FIELD] {candidate}: erro {exc}")
                continue
            if arr is not None:
                fields[public_name] = arr
                sources[public_name] = candidate
                break

    # Vento 10 m é muito mais útil no mapa como magnitude/direção.
    if "u10" in fields and "v10" in fields:
        u = fields["u10"]
        v = fields["v10"]
        fields["windSpeed10m"] = np.hypot(u, v)
        # Direção meteorológica: de onde o vento vem, em graus.
        fields["windDirection10m"] = (270.0 - np.degrees(np.arctan2(v, u))) % 360.0
        sources["windSpeed10m"] = "U10+V10"
        sources["windDirection10m"] = "U10+V10"
    return fields, sources


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
    inventory = {}
    published_fields = set()
    field_sources = {}

    for index, fn in enumerate(files):
        with Dataset(fn) as ds:
            if not inventory:
                inventory = discover_variables(ds)
                (out / 'variables.json').write_text(json.dumps(inventory, indent=2, ensure_ascii=False), encoding='utf-8')
                print(f"[METBR][DISCOVERY] {len(inventory)} variáveis encontradas")
                print("[METBR][DISCOVERY] " + ", ".join(inventory.keys()))

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
                refl, source_name, source_file = find_reflectivity_in_run(run_dir, expected_shape)
            if refl is None:
                raise RuntimeError(f'Nenhuma variável de refletividade nativa encontrada para {fn.name}; nenhuma aproximação dBZ será usada')

            grid_y, grid_x = map(int, refl.shape)
            valid_time = read_time(ds)
            if init_time is None:
                init_time = valid_time

            surface_fields, surface_sources = select_surface_fields(ds, expected_shape)
            published_fields.update(surface_fields.keys())
            field_sources.update(surface_sources)

            arr = np.where(np.isfinite(refl), refl, -9999.0).astype(np.float32)
            target = frame_dir / f'f{index:03d}.json.gz'
            payload = {
                'forecastHour': index,
                'validTime': valid_time,
                'gridX': grid_x,
                'gridY': grid_y,
                'reflectivityDbz': arr.tolist(),
                'reflectivitySource': source_name,
                'nativeGrid': True,
                'sourceVariable': source_name,
                'sourceFile': source_file,
                'availableVariables': sorted(inventory.keys()),
                'availableMapFields': sorted(surface_fields.keys()),
                'mapFieldSources': surface_sources,
                'fields': {name: _json_array(values) for name, values in surface_fields.items()},
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
                'forecastHour': index,
                'validTime': valid_time,
                'file': f'icon/f{index:03d}.json.gz',
                'gridX': grid_x,
                'gridY': grid_y,
                'source': 'METBR WRF 4 KM ICON',
                'reflectivitySource': source_name,
                'nativeGrid': True,
                'sourceVariable': source_name,
                'sourceFile': source_file,
                'availableMapFields': sorted(surface_fields.keys()),
                'reflectivityStats': stats,
            })

    generated = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
    cycle = args.run_cycle if args.run_cycle.endswith('Z') else args.run_cycle + 'Z'
    metadata = {
        'schema': 'sideral-wrf-metadata-v2',
        'model': 'icon',
        'resolutionKm': 4,
        'runDate': args.run_date,
        'runCycle': cycle,
        'initTime': init_time,
        'generatedAt': generated,
        'reflectivitySource': frames[0]['reflectivitySource'],
        'nativeGrid': True,
        'grid': {'nx': grid_x, 'ny': grid_y},
        'frameCount': len(frames),
        'temporalResolutionMinutes': 60,
        'availableVariables': sorted(inventory.keys()),
        'availableMapFields': sorted(published_fields),
        'mapFieldSources': field_sources,
        'variablesFile': 'variables.json',
        'frames': frames,
    }
    (out / 'metadata.json').write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding='utf-8')
    print(f'METBR 4 km validado: {args.run_date} {cycle}, {len(frames)} frames, {grid_x}x{grid_y}')
    print('[METBR][MAP FIELDS] ' + ', '.join(sorted(published_fields)))


if __name__ == '__main__':
    main()
