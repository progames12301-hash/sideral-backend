#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import json
import re
from pathlib import Path

import numpy as np
import xarray as xr


def safe_round_array(values: np.ndarray, decimals: int) -> list[float]:
    arr = np.asarray(values, dtype=float)
    arr = np.nan_to_num(arr, nan=0.0, posinf=0.0, neginf=0.0)
    return np.round(arr, decimals).reshape(-1).tolist()


def wind_direction_deg(u: np.ndarray, v: np.ndarray) -> np.ndarray:
    direction = (270.0 - np.degrees(np.arctan2(v, u))) % 360.0
    calm = (np.abs(u) < 1e-8) & (np.abs(v) < 1e-8)
    return np.where(calm, 0.0, direction)


def native_composite_reflectivity(dataset: xr.Dataset) -> np.ndarray:
    """Retorna refletividade composta usando exclusivamente REFL_10CM do WRF.

    REFL_10CM e calculado pelo proprio WRF quando do_radar_ref=1 para os
    esquemas de microfisica suportados. Nao usamos conversao Z-R nem formula
    empirica de hidrometeoros como fallback: se o campo nao existir, a
    publicacao deve falhar para evitar divulgar uma refletividade enganosa.
    """
    if "REFL_10CM" not in dataset:
        raise RuntimeError(
            "REFL_10CM ausente no wrfout. Verifique do_radar_ref=1 e a microfisica; "
            "a Sideral nao publica refletividade aproximada como substituta."
        )

    raw = np.asarray(dataset["REFL_10CM"].isel(Time=0).to_numpy(), dtype=float)
    if raw.ndim == 3:
        composite = np.nanmax(raw, axis=0)
    elif raw.ndim == 2:
        composite = raw
    else:
        raise RuntimeError(f"REFL_10CM possui dimensao inesperada: shape={raw.shape}")

    finite = np.isfinite(composite)
    if not np.any(finite):
        raise RuntimeError("REFL_10CM nao possui nenhum valor finito.")

    # Valores negativos representam ausencia/eco muito fraco. Mantemos o
    # valor meteorologico nativo e apenas normalizamos o intervalo de exibicao.
    composite = np.where(finite, composite, 0.0)
    return np.clip(composite, 0.0, 95.0)


def parse_run_env(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    if not path.exists():
        return result
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        result[key.strip()] = value.strip()
    return result


def parse_valid_time(path: Path) -> dt.datetime:
    match = re.search(r"wrfout_d01_(\d{4}-\d{2}-\d{2})_(\d{2})[-:](\d{2})[-:](\d{2})", path.name)
    if not match:
        raise ValueError(f"Nome wrfout inesperado: {path.name}")
    return dt.datetime.strptime(
        f"{match.group(1)} {match.group(2)}:{match.group(3)}:{match.group(4)}",
        "%Y-%m-%d %H:%M:%S",
    ).replace(tzinfo=dt.timezone.utc)


def write_gzip_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    with path.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, compresslevel=9, mtime=0) as zipped:
            zipped.write(body)


def sample_indices(size: int, target: int) -> np.ndarray:
    target = max(1, min(target, size))
    return np.rint(np.linspace(0, size - 1, target)).astype(int)


def sample2d(values: np.ndarray, rows: np.ndarray, cols: np.ndarray) -> np.ndarray:
    return np.asarray(values)[np.ix_(rows, cols)]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", default="wrf_work/run")
    parser.add_argument("--run-env", default="wrf_diagnostics/run.env")
    parser.add_argument("--output-dir", default="wrf_publish")
    parser.add_argument("--grid-x", type=int, default=220)
    parser.add_argument("--grid-y", type=int, default=180)
    args = parser.parse_args()

    run_dir = Path(args.run_dir)
    output_dir = Path(args.output_dir)
    files = sorted(run_dir.glob("wrfout_d01_*"), key=parse_valid_time)
    if not files:
        raise SystemExit(f"Nenhum wrfout encontrado em {run_dir}")

    env = parse_run_env(Path(args.run_env))
    run_date = env.get("RUN_DATE", "")
    run_cycle = env.get("RUN_CYCLE", "")
    if run_date and run_cycle:
        init_time = dt.datetime.strptime(f"{run_date}{run_cycle}", "%Y%m%d%H").replace(tzinfo=dt.timezone.utc)
    else:
        init_time = parse_valid_time(files[0])
        run_date = init_time.strftime("%Y%m%d")
        run_cycle = init_time.strftime("%H")

    output_dir.mkdir(parents=True, exist_ok=True)
    frames: list[dict] = []
    previous_rain: np.ndarray | None = None

    for index, path in enumerate(files):
        valid_time = parse_valid_time(path)
        forecast_hour = max(0, int(round((valid_time - init_time).total_seconds() / 3600.0)))
        print(f"Extraindo {path.name} => F{forecast_hour:03d}")

        with xr.open_dataset(path, engine="netcdf4", decode_times=False) as dataset:
            def field(name: str) -> np.ndarray:
                if name not in dataset:
                    raise RuntimeError(f"Variavel WRF ausente: {name}")
                return dataset[name].isel(Time=0).to_numpy()

            lats = field("XLAT")
            lons = field("XLONG")
            u10 = field("U10")
            v10 = field("V10")
            t2m = field("T2") - 273.15
            q2 = field("Q2")
            psfc = field("PSFC")

            rain_total = field("RAINC") + field("RAINNC")
            if previous_rain is None:
                precip_rate = np.zeros_like(rain_total, dtype=float)
            else:
                elapsed_h = max(1.0, (valid_time - parse_valid_time(files[index - 1])).total_seconds() / 3600.0)
                precip_rate = np.maximum(0.0, rain_total - previous_rain) / elapsed_h
            previous_rain = np.asarray(rain_total, dtype=float).copy()

            reflectivity = native_composite_reflectivity(dataset)
            reflectivity_source = "REFL_10CM_NATIVE"
            refl_positive = reflectivity[reflectivity > 0.0]
            refl_stats = {
                "maxDbz": round(float(np.nanmax(reflectivity)), 1),
                "p99Dbz": round(float(np.nanpercentile(reflectivity, 99.0)), 1),
                "fractionAbove5Dbz": round(float(np.mean(reflectivity >= 5.0)), 5),
                "fractionAbove40Dbz": round(float(np.mean(reflectivity >= 40.0)), 5),
                "positivePixels": int(refl_positive.size),
            }
            print(
                f"REFL_10CM nativo F{forecast_hour:03d}: "
                f"max={refl_stats['maxDbz']} dBZ p99={refl_stats['p99Dbz']} dBZ "
                f">=40dBZ={refl_stats['fractionAbove40Dbz'] * 100:.2f}%"
            )

            temp_for_es = np.maximum(-80.0, t2m)
            es = 6.112 * np.exp((17.67 * temp_for_es) / (temp_for_es + 243.5))
            e = (q2 * psfc / 100.0) / (0.622 + q2)
            rh2 = np.clip((e / es) * 100.0, 0.0, 100.0)

            pressure = (field("P") + field("PB")) / 100.0
            qvapor = field("QVAPOR")
            water_vapor = np.maximum(0.0, -np.trapezoid(qvapor, pressure, axis=0) / 9.81)

            u_mass = 0.5 * (field("U")[:, :, :-1] + field("U")[:, :, 1:])
            v_mass = 0.5 * (field("V")[:, :-1, :] + field("V")[:, 1:, :])
            level_500 = np.nanargmin(np.abs(pressure - 500.0), axis=0)
            rr, cc = np.indices(t2m.shape)
            u500 = u_mass[level_500, rr, cc]
            v500 = v_mass[level_500, rr, cc]
            bulk_shear = np.sqrt((u500 - u10) ** 2 + (v500 - v10) ** 2)

            dx_m = float(dataset.attrs.get("DX", 4000.0))
            dy_m = float(dataset.attrs.get("DY", 4000.0))
            dvdx = np.gradient(v10, dx_m, axis=1)
            dudy = np.gradient(u10, dy_m, axis=0)
            vorticity = dvdx - dudy

            # Mantido por compatibilidade com o produto atual. Esta variavel ainda
            # nao deve ser apresentada como MUCAPE diagnostico nativo do WRF.
            mucape = np.maximum(0.0, (t2m - 20.0) * rh2 * 8.0)

            native_y, native_x = t2m.shape
            rows = sample_indices(native_y, args.grid_y)
            cols = sample_indices(native_x, args.grid_x)
            actual_y, actual_x = len(rows), len(cols)

            lat_s = sample2d(lats, rows, cols)
            lon_s = sample2d(lons, rows, cols)
            u10_s = sample2d(u10, rows, cols)
            v10_s = sample2d(v10, rows, cols)
            wind_speed = np.hypot(u10_s, v10_s) * 3.6
            wind_dir = wind_direction_deg(u10_s, v10_s)

            payload = {
                "schema": "sideral-wrf-grid-v1",
                "model": "gfs",
                "source": "WRF 4 km GFS (REFL_10CM nativo)",
                "reflectivitySource": reflectivity_source,
                "reflectivityStats": refl_stats,
                "runDate": run_date,
                "runCycle": f"{run_cycle}Z",
                "initTime": init_time.isoformat().replace("+00:00", "Z"),
                "forecastHour": forecast_hour,
                "validTime": valid_time.isoformat().replace("+00:00", "Z"),
                "dxMeters": int(round(dx_m)),
                "dyMeters": int(round(dy_m)),
                "gridX": actual_x,
                "gridY": actual_y,
                "bounds": {
                    "south": round(float(np.nanmin(lat_s)), 4),
                    "west": round(float(np.nanmin(lon_s)), 4),
                    "north": round(float(np.nanmax(lat_s)), 4),
                    "east": round(float(np.nanmax(lon_s)), 4),
                },
                "fields": {
                    "lat": safe_round_array(lat_s, 4),
                    "lon": safe_round_array(lon_s, 4),
                    "reflectivity": safe_round_array(sample2d(reflectivity, rows, cols), 1),
                    "precipitation": safe_round_array(sample2d(precip_rate, rows, cols), 2),
                    "windSpeed": safe_round_array(wind_speed, 1),
                    "windDirection": safe_round_array(wind_dir, 0),
                    "bulkShear": safe_round_array(sample2d(bulk_shear, rows, cols), 1),
                    "vorticity850": safe_round_array(sample2d(vorticity, rows, cols), 7),
                    "temperature": safe_round_array(sample2d(t2m, rows, cols), 1),
                    "humidity": safe_round_array(sample2d(rh2, rows, cols), 0),
                    "mucape": safe_round_array(sample2d(mucape, rows, cols), 0),
                    "waterVapor": safe_round_array(sample2d(water_vapor, rows, cols), 1),
                },
            }

        filename = f"gfs/f{forecast_hour:03d}.json.gz"
        write_gzip_json(output_dir / filename, payload)
        frames.append({
            "index": len(frames),
            "forecastHour": forecast_hour,
            "validTime": payload["validTime"],
            "file": filename,
            "gridX": payload["gridX"],
            "gridY": payload["gridY"],
            "source": payload["source"],
            "reflectivitySource": payload["reflectivitySource"],
            "reflectivityStats": payload["reflectivityStats"],
        })

    metadata = {
        "schema": "sideral-wrf-metadata-v1",
        "model": "gfs",
        "resolutionKm": 4,
        "runDate": run_date,
        "runCycle": f"{run_cycle}Z",
        "initTime": init_time.isoformat().replace("+00:00", "Z"),
        "generatedAt": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
        "reflectivitySource": "REFL_10CM_NATIVE",
        "frameCount": len(frames),
        "frames": frames,
    }
    (output_dir / "metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(metadata, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
