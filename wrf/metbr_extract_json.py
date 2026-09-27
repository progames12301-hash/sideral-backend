#!/usr/bin/env python3
import argparse
import json
from pathlib import Path

import numpy as np
from netCDF4 import Dataset

REFLECTIVITY_NAMES = ("REFL_10CM", "REFL_10CM_NATIVE", "REFLECTIVITY_10CM", "REFLECTIVITY", "REFL", "DBZ10", "DBZ", "ZH")

def read_time(ds):
    times = ds.variables.get("Times")
    if times is None: return ""
    raw = np.asarray(times[:]); chars = raw[0] if raw.ndim == 2 else raw
    return "".join(x.decode() if isinstance(x, bytes) else str(x) for x in chars).replace("_", "T") + "Z"

def describe_variables(ds):
    names = sorted(ds.variables.keys())
    radar = [n for n in names if any(k in n.upper() for k in ("REFL", "REFLECT", "DBZ", "ZH"))]
    return names, radar

def _as_2d_reflectivity(value, expected_shape=None):
    a = np.ma.asarray(value)
    if np.ma.isMaskedArray(a): a = a.filled(np.nan)
    a = np.asarray(a, dtype=np.float32)
    if a.ndim < 2: return None
    if expected_shape is not None and tuple(a.shape[-2:]) != tuple(expected_shape): return None
    if a.ndim > 2:
        with np.errstate(all="ignore"): a = np.nanmax(a, axis=tuple(range(a.ndim - 2)))
    if a.ndim != 2 or not np.isfinite(a).any(): return None
    return np.asarray(a, dtype=np.float32)

def try_reflectivity_file(path, expected_shape=None):
    try:
        with Dataset(path) as ds:
            names, radar_names = describe_variables(ds)
            print(f"[METBR][DIAG] {path.name}: {len(names)} variaveis")
            print(f"[METBR][DIAG] {path.name}: candidatas de refletividade: {radar_names or 'nenhuma'}")
            ordered = [n for n in REFLECTIVITY_NAMES if n in ds.variables]
            ordered += [n for n in radar_names if n not in ordered]
            for name in ordered:
                var = ds.variables[name]
                print(f"[METBR][DIAG] {path.name}:{name}: dimensoes={getattr(var, 'dimensions', ())} shape={getattr(var, 'shape', ())}")
                refl = _as_2d_reflectivity(var[:], expected_shape)
                if refl is not None:
                    print(f"[METBR] refletividade nativa encontrada: {path.name}:{name} shape={refl.shape}")
                    return refl, name, path.name
    except Exception as exc:
        print(f"[METBR][DIAG] falha lendo {path}: {exc}")
    return None, None, None

def grid_shape(ds):
    lat, lon = ds.variables.get("XLAT"), ds.variables.get("XLONG")
    if lat is None or lon is None: raise RuntimeError("XLAT/XLONG ausentes no WRFOUT")
    shape = np.asarray(lat[:]).shape[-2:]
    if shape != np.asarray(lon[:]).shape[-2:]: raise RuntimeError("XLAT/XLONG com grades diferentes")
    return int(shape[1]), int(shape[0])

def clean_array(a, expected_size):
    flat = np.nan_to_num(np.asarray(a, dtype=np.float32).reshape(-1), nan=-9999.0, posinf=-9999.0, neginf=-9999.0)
    if flat.size != expected_size: raise RuntimeError(f"REFL_10CM incompleta: {flat.size} valores; esperados {expected_size}")
    return flat.tolist()

def validate_frames(frames, nx, ny):
    expected = int(nx) * int(ny)
    for frame in frames:
        actual = len(frame.get("reflectivityDbz", []))
        if actual != expected: raise RuntimeError(f"Frame {frame.get('file', '?')} possui {actual} valores; esperados {expected} ({ny}x{nx})")
    print(f"[METBR][VALID] {len(frames)} frames validados: {expected} valores/frame ({ny}x{nx})")

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--run-dir", required=True); ap.add_argument("--output-dir", required=True); args = ap.parse_args()
    files = sorted(Path(args.run_dir).glob("wrfout_d01_*"))
    if not files: raise SystemExit("Nenhum wrfout_d01 encontrado")
    out = Path(args.output_dir); out.mkdir(parents=True, exist_ok=True)
    frames=[]; nx=ny=None; init_time=""; sources=set()
    for index,path in enumerate(files):
        with Dataset(path) as ds:
            x,y=grid_shape(ds); expected_shape=(y,x); expected_size=x*y
            refl,source_name,source_file=try_reflectivity_file(path, expected_shape)
            if refl is None: raise RuntimeError(f"Nenhuma refletividade nativa válida em {path.name}")
            if nx is None: nx,ny=x,y
            elif (nx,ny)!=(x,y): raise RuntimeError("Os WRFOUTs possuem grades diferentes")
            if refl.shape != expected_shape or refl.size != expected_size: raise RuntimeError(f"REFL_10CM incompleta em {path.name}: {refl.shape}, {refl.size}; esperado {expected_shape}, {expected_size}")
            print(f"[METBR][REFL] arquivo={path.name}"); print(f"[METBR][REFL] shape_original={getattr(ds.variables[source_name], 'shape', ())}"); print(f"[METBR][REFL] shape_horizontal={refl.shape[0]}x{refl.shape[1]}"); print(f"[METBR][REFL] valores={refl.size}")
            t=read_time(ds)
            if not init_time: init_time=t
            sources.add(f"{source_file}:{source_name}")
            frames.append({"file":path.name,"time":t,"forecastHour":index,"reflectivityDbz":clean_array(refl,expected_size)})
    validate_frames(frames,nx,ny)
    payload={"schemaVersion":"1.0","model":"METBR WRF 4 km","modelKey":"metbr_wrf","resolutionKm":4,"grid":{"nx":nx,"ny":ny},"frames":frames,"frameCount":len(frames),"temporalResolutionMinutes":60,"reflectivitySource":sorted(sources),"nativeGrid":True,"initTime":init_time}
    metadata={"schemaVersion":"1.0","model":"METBR WRF 4 km","modelKey":"metbr_wrf","resolutionKm":4,"grid":{"nx":nx,"ny":ny},"frames":[{"file":f["file"],"time":f["time"],"forecastHour":f["forecastHour"]} for f in frames],"frameCount":len(frames),"temporalResolutionMinutes":60,"reflectivitySource":sorted(sources),"nativeGrid":True,"initTime":init_time}
    (out/"metbr_wrf_4km.json").write_text(json.dumps(payload,separators=(",",":"),allow_nan=False),encoding="utf-8")
    (out/"metadata.json").write_text(json.dumps(metadata,separators=(",",":"),allow_nan=False),encoding="utf-8")
    serialized=json.loads((out/"metbr_wrf_4km.json").read_text(encoding="utf-8")); validate_frames(serialized["frames"],nx,ny)
    print(f"METBR JSON pronto: {len(frames)} frames, grade {nx}x{ny}")

if __name__ == "__main__": main()
