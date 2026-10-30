#!/usr/bin/env python3
"""Generate one native METBR WRF 4-km Skew-T PNG for a requested map point/hour.

This is an isolated GitHub Actions worker. It imports the established METBR
profile extraction and renderer, but does not modify the existing scheduled
capital publisher or the Render Click-to-Point endpoint.
"""
from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import math
import shutil
from pathlib import Path

from netCDF4 import Dataset

ROOT = Path(__file__).resolve().parents[1]
NATIVE_PATH = ROOT / "wrf" / "metbr_skewt_south.py"


def load_native():
    spec = importlib.util.spec_from_file_location("sideral_metbr_clickpoint_native", NATIVE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Não foi possível carregar o extrator METBR: {NATIVE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def parse_time(value: str) -> dt.datetime:
    text = str(value or "").strip()
    if not text:
        raise ValueError("Horário ausente nos metadados METBR.")
    return dt.datetime.fromisoformat(text.replace("Z", "+00:00")).replace(tzinfo=None)


def choose_click_gridpoint(native, dataset, latitude: float, longitude: float) -> dict:
    """Select the closest native WRF cell using the same 8-km rule as Render Click-to-Point."""
    import numpy as np

    lat_grid, lon_grid = native.grid_latlon(dataset)
    lat_r = np.radians(lat_grid)
    lon_r = np.radians(lon_grid)
    lat0 = math.radians(float(latitude))
    lon0 = math.radians(float(longitude))
    dlat = lat_r - lat0
    dlon = lon_r - lon0
    a = (
        np.sin(dlat / 2.0) ** 2
        + math.cos(lat0) * np.cos(lat_r) * np.sin(dlon / 2.0) ** 2
    )
    distance = 6371.0 * 2.0 * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0)))
    distance[~np.isfinite(distance)] = np.inf

    j, i = np.unravel_index(int(np.argmin(distance)), distance.shape)
    distance_km = float(distance[j, i])
    ny, nx = lat_grid.shape
    domain = {
        "south": float(np.nanmin(lat_grid)),
        "north": float(np.nanmax(lat_grid)),
        "west": float(np.nanmin(lon_grid)),
        "east": float(np.nanmax(lon_grid)),
    }
    if (
        latitude < domain["south"] - 0.02
        or latitude > domain["north"] + 0.02
        or longitude < domain["west"] - 0.02
        or longitude > domain["east"] + 0.02
    ):
        raise RuntimeError("Ponto fora da cobertura METBR WRF 4 km.")
    if not math.isfinite(distance_km) or distance_km > 8.0:
        raise RuntimeError(
            "O ponto selecionado está muito distante da grade nativa METBR WRF 4 km "
            f"({distance_km:.2f} km; limite 8 km)."
        )
    if not (1 <= j < ny - 1 and 1 <= i < nx - 1):
        raise RuntimeError("O ponto selecionado está perto demais da borda da grade METBR.")

    return {
        "i": int(i),
        "j": int(j),
        "distance_km": distance_km,
        "grid_latitude": float(lat_grid[j, i]),
        "grid_longitude": float(lon_grid[j, i]),
        "grid_shape": {"ny": int(ny), "nx": int(nx)},
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wrfout", required=True)
    parser.add_argument("--forecast-hour", required=True, type=int)
    parser.add_argument("--latitude", required=True, type=float)
    parser.add_argument("--longitude", required=True, type=float)
    parser.add_argument("--run-time", required=True)
    parser.add_argument("--valid-time", required=True)
    parser.add_argument("--request-id", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()

    if not (0 <= args.forecast_hour <= 42):
        raise ValueError("Validade fora do intervalo METBR F000–F042.")
    if not (-90 <= args.latitude <= 90 and -180 <= args.longitude <= 180):
        raise ValueError("Latitude/longitude inválidas.")

    run_time = parse_time(args.run_time)
    valid_time = parse_time(args.valid_time)
    actual_hour = (valid_time - run_time).total_seconds() / 3600.0
    if abs(actual_hour - args.forecast_hour) > 0.02:
        raise RuntimeError(
            f"Validade inconsistente: metadados indicam F{actual_hour:.2f}, "
            f"mas foi solicitado F{args.forecast_hour:03d}."
        )

    native = load_native()
    city = {
        "name": f"Ponto selecionado ({args.latitude:.4f}, {args.longitude:.4f})",
        "state": "",
        "latitude": args.latitude,
        "longitude": args.longitude,
    }
    wrfout = Path(args.wrfout).resolve()
    if not wrfout.is_file() or wrfout.stat().st_size < 5 * 1024 * 1024:
        raise FileNotFoundError(f"WRFOUT ausente ou incompleto: {wrfout}")

    with Dataset(str(wrfout), "r") as dataset:
        matches = []
        for index in range(native.read_time_count(dataset)):
            stamp = native.time_at(dataset, index)
            if stamp is not None and abs((stamp - valid_time).total_seconds()) <= 60:
                matches.append(index)
        if not matches:
            raise RuntimeError(
                f"O WRFOUT {wrfout.name} não contém {valid_time:%Y-%m-%d %H:%MZ}."
            )
        time_index = matches[0]
        point = choose_click_gridpoint(native, dataset, args.latitude, args.longitude)
        distance_km = float(point.get("distance_km", math.inf))
        if not math.isfinite(distance_km) or distance_km > 8.0:
            raise RuntimeError(
                f"Ponto fora da grade METBR utilizável: célula mais próxima a "
                f"{distance_km:.2f} km (limite operacional 8 km)."
            )

        sounding, rows, terrain, thermal_advection = native.make_profile(
            dataset, time_index, city, point, valid_time
        )
        renderer = native.load_renderer()
        output_dir = Path(args.output_dir).resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        temp_dir = output_dir / ".render-tmp"
        temp_dir.mkdir(parents=True, exist_ok=True)
        meta = {
            "location": city["name"],
            "state": "",
            "model": "METBR WRF 4 km",
            "station": city["name"] + " / METBR WRF 4 km",
            "latitude": args.latitude,
            "longitude": args.longitude,
            "elevation": terrain,
            "fh": args.forecast_hour,
            "valid": valid_time,
            "run": run_time,
            "source": "native WRFOUT · GitHub Actions sob demanda",
            "thermal_advection": thermal_advection,
        }
        result_path = renderer.render_native_spc(sounding, temp_dir, meta)
        candidate = Path(result_path) if result_path else temp_dir / "full.png"
        if not candidate.is_file() or candidate.stat().st_size < 10000:
            candidate = temp_dir / "full.png"
        if not candidate.is_file() or candidate.stat().st_size < 10000:
            raise RuntimeError(f"Renderizador não produziu PNG válido para F{args.forecast_hour:03d}.")
        png_path = output_dir / f"f{args.forecast_hour:03d}.png"
        png_path.write_bytes(candidate.read_bytes())
        shutil.rmtree(temp_dir, ignore_errors=True)

    profile = native.profile_json(
        city, point, run_time, valid_time, args.forecast_hour, rows, terrain
    )
    profile.update({
        "request_id": args.request_id,
        "model": "METBR WRF 4 km",
        "model_key": "metbr_wrf_4km",
        "latitude": args.latitude,
        "longitude": args.longitude,
        "grid_latitude": point["grid_latitude"],
        "grid_longitude": point["grid_longitude"],
        "grid_distance_km": round(distance_km, 3),
        "run_utc": run_time.strftime("%Y-%m-%d %H:%MZ"),
        "forecast_hour": args.forecast_hour,
        "valid_utc": valid_time.strftime("%Y-%m-%d %H:%MZ"),
        "terrain_elevation_m": float(terrain),
        "renderer": "SHARPpy · Sideral native SPC",
    })
    json_path = Path(args.output_dir) / f"f{args.forecast_hour:03d}.json"
    json_path.write_text(
        json.dumps(profile, ensure_ascii=False, allow_nan=False, separators=(",", ":")),
        encoding="utf-8",
    )
    print(
        f"[METBR Click-to-Point GitHub] F{args.forecast_hour:03d} concluído · "
        f"{len(rows)} níveis · grade a {distance_km:.2f} km · {png_path.stat().st_size} bytes",
        flush=True,
    )


if __name__ == "__main__":
    main()
