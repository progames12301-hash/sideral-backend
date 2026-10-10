#!/usr/bin/env python3
"""Render Southern Brazil Skew-T profiles from native METBR WRF 4-km WRFOUT data."""
from __future__ import annotations

import argparse
import datetime as dt
import gc
import importlib.util
import json
import math
from pathlib import Path

import numpy as np
from netCDF4 import Dataset

ROOT = Path(__file__).resolve().parents[1]
RENDERER = ROOT / "tools" / "skewt" / "native_spc_render.py"
KAPPA = 0.2854
RD = 287.05
G = 9.80665
MS_TO_KT = 1.943844
FORECAST_HOURS = list(range(0, 43, 3))
CITIES = [
    {"name": "Curitiba", "slug": "curitiba", "latitude": -25.4284, "longitude": -49.2733, "state": "PR"},
    {"name": "Florianópolis", "slug": "florianopolis", "latitude": -27.5954, "longitude": -48.5480, "state": "SC"},
    {"name": "Porto Alegre", "slug": "porto-alegre", "latitude": -30.0346, "longitude": -51.2177, "state": "RS"},
]


def as_float_array(value):
    arr = np.ma.asarray(value)
    if np.ma.isMaskedArray(arr):
        arr = arr.filled(np.nan)
    return np.asarray(arr, dtype=np.float64)


def finite(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def text_time(value):
    chars = np.asarray(value).reshape(-1)
    text = "".join(x.decode("ascii") if isinstance(x, (bytes, np.bytes_)) else str(x) for x in chars).strip()
    try:
        return dt.datetime.strptime(text, "%Y-%m-%d_%H:%M:%S")
    except ValueError:
        return None


def time_at(ds, index):
    if "Times" not in ds.variables:
        raise RuntimeError("METBR WRFOUT sem variável Times")
    return text_time(ds.variables["Times"][index])


def read_time_count(ds):
    if "Time" in ds.dimensions:
        return len(ds.dimensions["Time"])
    if "Times" in ds.variables:
        return len(ds.variables["Times"])
    return 1


def grid_latlon(ds):
    if "XLAT" not in ds.variables or "XLONG" not in ds.variables:
        raise RuntimeError("XLAT/XLONG ausentes no METBR WRFOUT")
    lat = as_float_array(ds.variables["XLAT"][0])
    lon = as_float_array(ds.variables["XLONG"][0])
    if lat.ndim != 2 or lon.shape != lat.shape:
        raise RuntimeError(f"XLAT/XLONG possuem formas incompatíveis: {lat.shape}, {lon.shape}")
    return lat, lon


def choose_gridpoint(ds, city):
    lat, lon = grid_latlon(ds)
    lat0 = math.radians(city["latitude"])
    lon0 = math.radians(city["longitude"])
    latr = np.radians(lat)
    lonr = np.radians(lon)
    dlat = latr - lat0
    dlon = lonr - lon0
    a = np.sin(dlat / 2.0) ** 2 + np.cos(lat0) * np.cos(latr) * np.sin(dlon / 2.0) ** 2
    distance = 6371.0 * 2.0 * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0)))
    distance[~np.isfinite(distance)] = np.inf
    j, i = np.unravel_index(np.argmin(distance), distance.shape)
    km = float(distance[j, i])
    if not math.isfinite(km) or km > 15.0:
        raise RuntimeError(f"{city['name']}: ponto mais próximo está a {km:.1f} km; localidade pode estar fora do domínio METBR.")
    if not (1 <= j < lat.shape[0] - 1 and 1 <= i < lat.shape[1] - 1):
        raise RuntimeError(f"{city['name']}: ponto WRF demasiado perto da borda para interpolar vento.")
    return {
        "i": int(i), "j": int(j), "distance_km": km,
        "grid_latitude": float(lat[j, i]), "grid_longitude": float(lon[j, i]),
        "grid_shape": {"ny": int(lat.shape[0]), "nx": int(lat.shape[1])},
    }


def point_value(ds, key, time_index, j, i):
    if key not in ds.variables:
        raise RuntimeError(f"METBR WRFOUT sem variável obrigatória {key}")
    value = float(as_float_array(ds.variables[key][time_index, j, i]))
    if not math.isfinite(value):
        raise RuntimeError(f"{key} inválido no ponto j={j}, i={i}")
    return value


def point_column(ds, key, time_index, j, i):
    if key not in ds.variables:
        raise RuntimeError(f"METBR WRFOUT sem variável obrigatória {key}")
    # Read only a single vertical column from NetCDF; never load the complete
    # 3-D atmospheric field into RAM on the hosted runner.
    col = np.asarray(as_float_array(ds.variables[key][time_index, :, j, i]), dtype=np.float64)
    if col.ndim != 1 or not np.isfinite(col).any():
        raise RuntimeError(f"Coluna vertical {key} inválida em j={j}, i={i}")
    return col


def dewpoint_from_mixing_ratio(mixing_ratio, pressure_hpa):
    q = max(1.0e-8, float(mixing_ratio))
    p = max(1.0, float(pressure_hpa))
    vapor = min(p * q / (0.622 + q), p * 0.98)
    loge = math.log(max(vapor, 1.0e-5) / 6.112)
    denominator = 17.67 - loge
    if abs(denominator) < 1.0e-6:
        return -100.0
    return 243.5 * loge / denominator


def make_profile(ds, time_index, city, point, valid_time):
    j, i = point["j"], point["i"]
    p_pa = point_column(ds, "P", time_index, j, i) + point_column(ds, "PB", time_index, j, i)
    theta = point_column(ds, "T", time_index, j, i) + 300.0
    qv = point_column(ds, "QVAPOR", time_index, j, i)
    ph = point_column(ds, "PH", time_index, j, i) + point_column(ds, "PHB", time_index, j, i)
    if len(ph) != len(p_pa) + 1:
        raise RuntimeError(f"{city['name']}: PH/PHB não têm um nível staggered a mais que P/PB.")
    z = 0.5 * (ph[:-1] + ph[1:]) / G

    # U/V are stored on WRF's staggered grids. Read only the two points
    # surrounding the selected mass-grid point and interpolate to its center.
    u_pair = as_float_array(ds.variables["U"][time_index, :, j, i:i + 2])
    v_pair = as_float_array(ds.variables["V"][time_index, :, j:j + 2, i])
    if u_pair.ndim != 2 or u_pair.shape[-1] != 2 or v_pair.ndim != 2 or v_pair.shape[-1] != 2:
        raise RuntimeError(f"{city['name']}: grade staggered U/V insuficiente.")
    u = 0.5 * (u_pair[:, 0] + u_pair[:, 1])
    v = 0.5 * (v_pair[:, 0] + v_pair[:, 1])
    w_stag = point_column(ds, "W", time_index, j, i)
    w = 0.5 * (w_stag[:-1] + w_stag[1:])

    pressure = p_pa / 100.0
    temperature_k = theta * (p_pa / 100000.0) ** KAPPA
    temperature_c = temperature_k - 273.15
    dewpoint_c = np.asarray(
        [min(dewpoint_from_mixing_ratio(q, p), t) for q, p, t in zip(qv, pressure, temperature_c)],
        dtype=np.float64,
    )
    density = p_pa / (RD * np.maximum(temperature_k, 150.0))
    omega = -w * density * G

    psfc = point_value(ds, "PSFC", time_index, j, i) / 100.0
    terrain = point_value(ds, "HGT", time_index, j, i)
    t2 = point_value(ds, "T2", time_index, j, i) - 273.15
    q2 = point_value(ds, "Q2", time_index, j, i)
    u10 = point_value(ds, "U10", time_index, j, i) * MS_TO_KT
    v10 = point_value(ds, "V10", time_index, j, i) * MS_TO_KT
    td2 = min(dewpoint_from_mixing_ratio(q2, psfc), t2)

    rows = [{
        "pressure_hpa": psfc, "height_m": terrain, "temperature_c": t2,
        "dewpoint_c": td2, "wind_u_kt": u10, "wind_v_kt": v10,
        "omega_pa_s": None,
    }]
    for p, height, temp, td, uu, vv, om in zip(pressure, z, temperature_c, dewpoint_c, u * MS_TO_KT, v * MS_TO_KT, omega):
        if not all(math.isfinite(float(x)) for x in (p, height, temp, td, uu, vv)):
            continue
        if p >= psfc - 1.0 or p <= 0 or height <= terrain + 5.0:
            continue
        rows.append({
            "pressure_hpa": float(p), "height_m": float(height),
            "temperature_c": float(temp), "dewpoint_c": float(min(td, temp)),
            "wind_u_kt": float(uu), "wind_v_kt": float(vv),
            "omega_pa_s": float(om) if math.isfinite(float(om)) else None,
        })

    rows.sort(key=lambda row: row["pressure_hpa"], reverse=True)
    clean = []
    for row in rows:
        if clean and (
            abs(row["pressure_hpa"] - clean[-1]["pressure_hpa"]) < 0.05
            or row["height_m"] <= clean[-1]["height_m"] + 0.5
        ):
            continue
        clean.append(row)
    if len(clean) < 10:
        raise RuntimeError(f"{city['name']} {valid_time:%Y-%m-%d %HZ}: apenas {len(clean)} níveis válidos.")

    from sharppy.sharptab import profile as shp_profile
    pressure_array = np.asarray([r["pressure_hpa"] for r in clean], dtype=np.float64)
    height_array = np.asarray([r["height_m"] for r in clean], dtype=np.float64)
    temp_array = np.asarray([r["temperature_c"] for r in clean], dtype=np.float64)
    dew_array = np.asarray([r["dewpoint_c"] for r in clean], dtype=np.float64)
    u_array = np.asarray([r["wind_u_kt"] for r in clean], dtype=np.float64)
    v_array = np.asarray([r["wind_v_kt"] for r in clean], dtype=np.float64)
    omega_array = np.asarray([np.nan if r["omega_pa_s"] is None else r["omega_pa_s"] for r in clean])
    sounding = shp_profile.create_profile(
        profile="convective", pres=pressure_array, hght=height_array,
        tmpc=temp_array, dwpc=dew_array, u=u_array, v=v_array,
        omeg=np.ma.masked_invalid(omega_array), strictQC=False,
        latitude=city["latitude"], date=valid_time, location=city["name"],
    )
    return sounding, clean, terrain


def load_renderer():
    spec = importlib.util.spec_from_file_location("sideral_native_spc_render", RENDERER)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Renderizador Sideral não encontrado: {RENDERER}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def profile_json(city, point, run_time, valid_time, forecast_hour, rows, terrain):
    levels = []
    for row in rows:
        u, v = row["wind_u_kt"], row["wind_v_kt"]
        levels.append({
            "pressure_hpa": finite(row["pressure_hpa"]),
            "height_m": finite(row["height_m"]),
            "height_agl_m": finite(row["height_m"] - terrain),
            "temperature_c": finite(row["temperature_c"]),
            "dewpoint_c": finite(row["dewpoint_c"]),
            "wind_u_kt": finite(u), "wind_v_kt": finite(v),
            "wind_speed_kt": finite(math.hypot(u, v)),
            "wind_direction_deg": finite((math.degrees(math.atan2(-u, -v)) + 360.0) % 360.0),
            "omega_pa_s": finite(row["omega_pa_s"]),
        })
    return {
        "schema": "sideral-metbr-wrf-skewt-v1",
        "model": "METBR WRF 4 km",
        "model_key": "metbr_wrf_4km",
        "source": "METBR native WRFOUT",
        "initialization_model": "ICON",
        "native_grid_spacing_km": 4,
        "sampling": "nearest WRF mass-grid column",
        "location": city["name"], "state": city["state"],
        "latitude": city["latitude"], "longitude": city["longitude"],
        "grid_point": {
            "i": point["i"], "j": point["j"],
            "grid_latitude": point["grid_latitude"],
            "grid_longitude": point["grid_longitude"],
            "distance_to_city_km": round(point["distance_km"], 3),
        },
        "terrain_elevation_m": finite(terrain),
        "run_utc": run_time.strftime("%Y-%m-%d %H:%MZ"),
        "forecast_hour": forecast_hour,
        "valid_utc": valid_time.strftime("%Y-%m-%d %H:%MZ"),
        "vertical_level_count": len(levels),
        "profile": levels,
    }


def parse_run_meta(path):
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    if not values.get("run_date") or not values.get("run_cycle"):
        raise RuntimeError(f"Metadados de inicialização ausentes em {path}")
    cycle = values["run_cycle"].rstrip("Z")
    if len(values["run_date"]) != 8 or cycle not in {"00", "06", "12", "18"}:
        raise RuntimeError(f"Metadados METBR inválidos: {values}")
    return dt.datetime.strptime(values["run_date"] + cycle, "%Y%m%d%H")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", required=True)
    parser.add_argument("--publication-file", required=True)
    parser.add_argument("--out-root", required=True)
    args = parser.parse_args()

    input_root = Path(args.input_dir).resolve()
    out_root = Path(args.out_root).resolve()
    out_root.mkdir(parents=True, exist_ok=True)
    run_time = parse_run_meta(Path(args.publication_file))
    renderer = load_renderer()

    files = sorted(p for p in input_root.glob("wrfout_d01_*") if p.is_file() and p.stat().st_size > 0)
    if len(files) != len(FORECAST_HOURS):
        raise RuntimeError(f"Esperados {len(FORECAST_HOURS)} WRFOUTs selecionados a cada 3 h; encontrados {len(files)}")

    with Dataset(files[0]) as ds:
        points = {city["slug"]: choose_gridpoint(ds, city) for city in CITIES}
    frames_by_city = {city["slug"]: {} for city in CITIES}
    for path in files:
        with Dataset(path) as ds:
            for ti in range(read_time_count(ds)):
                valid_time = time_at(ds, ti)
                if valid_time is None:
                    raise RuntimeError(f"Horário inválido no WRFOUT {path.name}")
                forecast_hour_float = (valid_time - run_time).total_seconds() / 3600.0
                forecast_hour = int(round(forecast_hour_float))
                if abs(forecast_hour_float - forecast_hour) > 0.02:
                    raise RuntimeError(f"Horário fora da hora cheia no WRFOUT {path.name}: {valid_time}")
                if forecast_hour not in FORECAST_HOURS:
                    continue
                if forecast_hour in frames_by_city[CITIES[0]["slug"]]:
                    continue
                print(f"[METBR Skew-T] {path.name}: F{forecast_hour:03d}", flush=True)
                for city in CITIES:
                    point = points[city["slug"]]
                    sounding, rows, terrain = make_profile(ds, ti, city, point, valid_time)
                    frames_by_city[city["slug"]][forecast_hour] = (sounding, rows, terrain, valid_time)

    manifest = {
        "schema": "sideral-metbr-wrf-skewt-publication-v1",
        "model": "METBR WRF 4 km",
        "model_key": "metbr_wrf_4km",
        "source": "Native METBR WRFOUT files generated by the operational METBR WRF 4 km run",
        "initialization_model": "ICON",
        "native_grid_spacing_km": 4,
        "profile_method": "nearest native WRF mass-grid column",
        "run_utc": run_time.strftime("%Y-%m-%d %H:%MZ"),
        "forecast_hours": FORECAST_HOURS,
        "cities": [],
        "assets": [],
    }

    for city in CITIES:
        point = points[city["slug"]]
        city_root = out_root / city["slug"]
        city_root.mkdir(parents=True, exist_ok=True)
        missing = sorted(set(FORECAST_HOURS) - set(frames_by_city[city["slug"]]))
        if missing:
            raise RuntimeError(f"{city['name']}: faltam frames METBR {missing}")
        manifest["cities"].append({**city, "grid_point": point, "native_grid_spacing_km": 4})
        for forecast_hour in FORECAST_HOURS:
            sounding, rows, terrain, valid_time = frames_by_city[city["slug"]][forecast_hour]
            frame = f"f{forecast_hour:03d}"
            meta = {
                "location": city["name"], "station": city["name"] + " / METBR WRF 4 km",
                "latitude": city["latitude"], "longitude": city["longitude"],
                "elevation": terrain, "fh": forecast_hour, "valid": valid_time, "run": run_time,
            }
            temp_dir = city_root / (".tmp-" + frame)
            temp_dir.mkdir(parents=True, exist_ok=True)
            renderer.render_native_spc(sounding, temp_dir, meta)
            png = temp_dir / "full.png"
            if not png.is_file() or png.stat().st_size < 10000:
                raise RuntimeError(f"PNG METBR inválido: {city['name']} {frame}")
            png_out = city_root / f"{city['slug']}-{frame}.png"
            png.replace(png_out)
            for product in temp_dir.iterdir():
                product.unlink(missing_ok=True)
            temp_dir.rmdir()

            data = profile_json(city, point, run_time, valid_time, forecast_hour, rows, terrain)
            json_out = city_root / f"{city['slug']}-{frame}.json"
            temp_json = json_out.with_suffix(".json.tmp")
            temp_json.write_text(json.dumps(data, ensure_ascii=False, allow_nan=False, separators=(",", ":")), encoding="utf-8")
            temp_json.replace(json_out)
            manifest["assets"].extend([png_out.name, json_out.name])
            print(f"[METBR Skew-T] {city['name']} {frame}: {len(rows)} níveis", flush=True)
            gc.collect()

    expected = len(CITIES) * len(FORECAST_HOURS) * 2
    if len(manifest["assets"]) != expected:
        raise RuntimeError(f"Esperados {expected} produtos; encontrados {len(manifest['assets'])}")
    (out_root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[METBR Skew-T] Validado: {len(CITIES)} cidades, {expected // 2} PNGs + {expected // 2} perfis JSON.", flush=True)


if __name__ == "__main__":
    main()
