#!/usr/bin/env python3
import argparse
import json
from pathlib import Path

import numpy as np
from netCDF4 import Dataset

REFLECTIVITY_NAMES = (
    "REFL_10CM", "REFL_10CM_NATIVE", "REFLECTIVITY_10CM", "REFLECTIVITY",
    "REFL", "DBZ10", "DBZ", "ZH",
)

# Variáveis/aliases que o boletim Sideral pode consumir. O extrator procura
# primeiro os nomes já calculados no WRF e só deriva campos quando os dados
# necessários realmente existem no WRFOUT.
FIELD_ALIASES = {
    "temp2m": ("T2",),
    "dewpoint2m": ("TD2", "DPT2"),
    "humidity2m": ("RH2", "RELH2"),
    "mslp": ("MSLP", "SLP"),
    "windU10": ("U10",),
    "windV10": ("V10",),
    "wind10m": ("WSPD10", "WS10", "WIND10"),
    "gust10m": ("GUST10", "GUST", "WMAX10"),
    "rainc": ("RAINC",),
    "rainnc": ("RAINNC",),
    "rainsh": ("RAINSH",),
    "swdown": ("SWDOWN",),
    "glw": ("GLW",),
    "olr": ("OLR",),
    "pblh": ("PBLH",),
    "sbcape": ("SBCAPE", "CAPE"),
    "mlcape": ("MLCAPE",),
    "mucape": ("MUCAPE",),
    "cin": ("CIN", "CINH"),
    "dcape": ("DCAPE",),
    "lifted_index": ("LI", "LIFTED_INDEX"),
    "lcl": ("LCL", "LCL_HEIGHT"),
    "lfc": ("LFC", "LFC_HEIGHT"),
    "el": ("EL", "EL_HEIGHT"),
    "pwat": ("PWAT", "PRECIPITABLE_WATER", "TCWV"),
    "shear01": ("SHEAR01", "BULK_SHEAR01", "BULK_SHEAR_01"),
    "shear03": ("SHEAR03", "BULK_SHEAR03", "BULK_SHEAR_03"),
    "shear06": ("SHEAR06", "BULK_SHEAR06", "BULK_SHEAR_06"),
    "srh01": ("SRH01", "SRH_01", "SRH01KM"),
    "srh03": ("SRH03", "SRH_03", "SRH03KM"),
    "scp": ("SCP", "STORM_RELATIVE_HELICITY_COMPOSITE"),
    "stp": ("STP", "STP_FIXED", "STP_EFFECTIVE"),
    "ship": ("SHIP",),
    "updraft_helicity": ("UP_HELI", "UPDRAFT_HELICITY", "UH25_3000"),
    "thetae850": ("THETAE850", "THETA_E_850", "THETA_E850"),
    "thetae_adv850": ("THETAE_ADV850", "THETA_E_ADV850", "THETAE850_ADV"),
    "u850": ("U850",), "v850": ("V850",),
    "u500": ("U500",), "v500": ("V500",),
    "omega700": ("OMEGA700", "OMEGA_700", "W700"),
    "vort500": ("VORT500", "VORT_500", "VORTICITY500"),
    "thickness": ("THICKNESS", "THICK_1000_500"),
}


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
        # Para campos com níveis, não escolher silenciosamente um nível.
        # Só aceitamos 2D quando o produto já está horizontalmente pronto.
        return None
    if a.ndim != 2 or not np.isfinite(a).any():
        return None
    return np.asarray(a, dtype=np.float32)


def _clean_values(a, expected_size):
    arr = np.ma.asarray(a)
    if np.ma.isMaskedArray(arr):
        arr = arr.filled(np.nan)
    arr = np.asarray(arr, dtype=np.float32).reshape(-1)
    if arr.size != expected_size:
        raise RuntimeError(f"Campo possui {arr.size} valores; esperados {expected_size}")
    return np.nan_to_num(arr, nan=-9999.0, posinf=-9999.0, neginf=-9999.0).tolist()


def _find_var(ds, names):
    upper = {str(k).upper(): k for k in ds.variables.keys()}
    for name in names:
        key = upper.get(name.upper())
        if key is not None:
            return key
    return None


def _derive_humidity(t2, q2, psfc):
    if t2 is None or q2 is None or psfc is None:
        return None
    # RH aproximada a partir de razão de mistura específica, pressão e T.
    t = np.asarray(t2, dtype=np.float64)
    q = np.asarray(q2, dtype=np.float64)
    p = np.asarray(psfc, dtype=np.float64)
    e = q * p / np.maximum(0.01, 0.622 + 0.378 * q)
    tc = t - 273.15
    es = 611.2 * np.exp((17.67 * tc) / np.maximum(1.0, tc + 243.5))
    rh = 100.0 * e / np.maximum(1.0, es)
    return np.clip(rh, 0.0, 100.0).astype(np.float32)


def _derive_dewpoint(t2, rh):
    if t2 is None or rh is None:
        return None
    tc = np.asarray(t2, dtype=np.float64) - 273.15
    r = np.clip(np.asarray(rh, dtype=np.float64), 1e-3, 100.0)
    a, b = 17.625, 243.04
    gamma = np.log(r / 100.0) + (a * tc) / (b + tc)
    td = (b * gamma) / np.maximum(1e-9, a - gamma)
    return (td + 273.15).astype(np.float32)


def _derive_wind_speed(u, v):
    if u is None or v is None:
        return None
    return np.sqrt(np.square(u, dtype=np.float32) + np.square(v, dtype=np.float32)).astype(np.float32)


def extract_fields(ds, expected_shape):
    names, _ = describe_variables(ds)
    found = {}
    diagnostics = {}
    for product, aliases in FIELD_ALIASES.items():
        name = _find_var(ds, aliases)
        if name is None:
            continue
        arr = _as_2d(ds.variables[name][:], expected_shape)
        if arr is None:
            continue
        found[product] = arr
        diagnostics[product] = name

    # Derivações somente quando os campos fundamentais realmente existem.
    t2_name = _find_var(ds, ("T2",))
    q2_name = _find_var(ds, ("Q2",))
    psfc_name = _find_var(ds, ("PSFC",))
    u10_name = _find_var(ds, ("U10",))
    v10_name = _find_var(ds, ("V10",))

    t2 = found.get("temp2m")
    if t2 is not None:
        # T2 no WRF é Kelvin; o produto Sideral é °C.
        found["temp2m"] = (t2 - 273.15).astype(np.float32)
    if found.get("dewpoint2m") is not None:
        found["dewpoint2m"] = (found["dewpoint2m"] - 273.15).astype(np.float32)
    if "humidity2m" not in found and q2_name and psfc_name and t2 is not None:
        q2 = _as_2d(ds.variables[q2_name][:], expected_shape)
        psfc = _as_2d(ds.variables[psfc_name][:], expected_shape)
        rh = _derive_humidity(t2, q2, psfc)
        if rh is not None:
            found["humidity2m"] = rh
            diagnostics["humidity2m"] = "derived(Q2,PSFC,T2)"
    if "dewpoint2m" not in found and "humidity2m" in found and t2 is not None:
        found["dewpoint2m"] = _derive_dewpoint(t2, found["humidity2m"])
        diagnostics["dewpoint2m"] = "derived(T2,RH2)"
    if "wind10m" not in found and u10_name and v10_name:
        u = _as_2d(ds.variables[u10_name][:], expected_shape)
        v = _as_2d(ds.variables[v10_name][:], expected_shape)
        ws = _derive_wind_speed(u, v)
        if ws is not None:
            found["wind10m"] = ws
            diagnostics["wind10m"] = "derived(U10,V10)"
    if u10_name and "windU10" not in found:
        u = _as_2d(ds.variables[u10_name][:], expected_shape)
        if u is not None:
            found["windU10"] = u
            diagnostics["windU10"] = u10_name
    if v10_name and "windV10" not in found:
        v = _as_2d(ds.variables[v10_name][:], expected_shape)
        if v is not None:
            found["windV10"] = v
            diagnostics["windV10"] = v10_name

    return found, diagnostics


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
                refl = _as_2d(var[:], expected_shape)
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
    raise RuntimeError("Nenhuma variável de refletividade nativa foi encontrada. Nenhuma aproximação dBZ será usada.")


def grid_shape(ds):
    lat = ds.variables.get("XLAT")
    lon = ds.variables.get("XLONG")
    if lat is None or lon is None:
        raise RuntimeError("XLAT/XLONG ausentes no WRFOUT")
    shape = np.asarray(lat[:]).shape[-2:]
    if shape != np.asarray(lon[:]).shape[-2:]:
        raise RuntimeError("XLAT/XLONG com grades diferentes")
    return int(shape[1]), int(shape[0])


def clean_array(a, expected_size):
    return _clean_values(a, expected_size)


def validate_frames(frames, nx, ny):
    expected = int(nx) * int(ny)
    for frame in frames:
        values = frame.get("reflectivityDbz")
        actual = len(values) if isinstance(values, list) else 0
        if actual != expected:
            raise RuntimeError(f"Frame {frame.get('file', '?')} possui {actual} valores; esperados {expected} ({ny}x{nx})")
    print(f"[METBR][VALID] {len(frames)} frames validados: {expected} valores/frame ({ny}x{nx})")


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
    available_variables = set()
    variable_sources = {}

    for index, path in enumerate(files):
        with Dataset(path) as ds:
            x, y = grid_shape(ds)
            expected_shape = (y, x)
            expected_size = x * y
            refl, source_name, source_file = try_reflectivity_file(path, expected_shape)
            if refl is None:
                refl, source_name, source_file = find_native_reflectivity(args.run_dir, expected_shape)
            if source_name is None or source_file is None:
                raise RuntimeError(f"Fonte nativa não identificada para {path.name}")
            source_descriptions.add(f"{source_file}:{source_name}")
            if refl.shape != expected_shape:
                raise RuntimeError(f"Grade inesperada em {path.name}: {refl.shape}, esperado {expected_shape}")
            if nx is None:
                nx, ny = x, y
            elif (nx, ny) != (x, y):
                raise RuntimeError("Os WRFOUTs possuem grades diferentes")

            fields, field_sources = extract_fields(ds, expected_shape)
            for key, source in field_sources.items():
                available_variables.add(key)
                variable_sources[key] = source

            print(f"[METBR][REFL] arquivo={path.name} source={source_name} shape={refl.shape} valores={refl.size}")
            print(f"[METBR][FIELDS] {path.name}: {len(fields)} campos exportados: {', '.join(sorted(fields)) or 'nenhum'}")
            if refl.size != expected_size:
                raise RuntimeError(f"REFL_10CM incompleta: {refl.size} valores; esperados {expected_size} ({y}x{x})")

            t = read_time(ds)
            if not init_time:
                init_time = t
            frame = {
                "file": path.name,
                "time": t,
                "forecastHour": index,
                "reflectivityDbz": clean_array(refl, expected_size),
            }
            for key, values in fields.items():
                frame[key] = clean_array(values, expected_size)
            frames.append(frame)

    validate_frames(frames, nx, ny)
    sources = sorted(source_descriptions)
    payload = {
        "schemaVersion": "1.1", "model": "METBR WRF 4 km", "modelKey": "metbr_wrf",
        "resolutionKm": 4, "grid": {"nx": nx, "ny": ny}, "frames": frames,
        "frameCount": len(frames), "temporalResolutionMinutes": 60,
        "reflectivitySource": sources, "nativeGrid": True, "initTime": init_time,
        "availableVariables": sorted(available_variables), "variableSources": variable_sources,
    }
    metadata = {
        "schemaVersion": "1.1", "model": "METBR WRF 4 km", "modelKey": "metbr_wrf",
        "resolutionKm": 4, "grid": {"nx": nx, "ny": ny},
        "frames": [{"file": f["file"], "time": f["time"], "forecastHour": f["forecastHour"]} for f in frames],
        "frameCount": len(frames), "temporalResolutionMinutes": 60,
        "reflectivitySource": sources, "nativeGrid": True, "initTime": init_time,
        "availableVariables": sorted(available_variables), "variableSources": variable_sources,
    }
    json_path = out / "metbr_wrf_4km.json"
    metadata_path = out / "metadata.json"
    json_path.write_text(json.dumps(payload, separators=(",", ":"), allow_nan=False), encoding="utf-8")
    metadata_path.write_text(json.dumps(metadata, separators=(",", ":"), allow_nan=False), encoding="utf-8")

    serialized = json.loads(json_path.read_text(encoding="utf-8"))
    validate_frames(serialized["frames"], nx, ny)
    print(f"METBR JSON pronto: {len(frames)} frames, grade {nx}x{ny}, variaveis={len(available_variables)}")
    print(f"[METBR] fontes nativas usadas: {', '.join(sources)}")
    print(f"[METBR] variaveis exportadas: {', '.join(sorted(available_variables)) or 'nenhuma além de refletividade'}")


if __name__ == "__main__":
    main()
