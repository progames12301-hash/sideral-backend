#!/usr/bin/env python3
"""Publica campos do CIM diretamente da grade nativa do wrfout, sem remapeamento."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
from pathlib import Path

import numpy as np
import xarray as xr

from extract_wrf_json import native_composite_reflectivity
from extract_wrf_severe import compute, flat, idx, runenv, validtime, write_gz

FIELDS = ("reflectivity", "stp", "scp", "temperature", "humidity")


def iso(value):
    return value.isoformat().replace("+00:00", "Z")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", default="wrf_work/run")
    ap.add_argument("--run-env", default="wrf_diagnostics/run.env")
    ap.add_argument("--output-dir", required=True)
    ap.add_argument("--model", choices=("icon", "ecmwf"), required=True)
    ap.add_argument("--grid-x", type=int, default=240)
    ap.add_argument("--grid-y", type=int, default=200)
    args = ap.parse_args()

    output_interval_hours = max(1, int(os.environ.get("CIM_OUTPUT_INTERVAL_HOURS", "1")))
    max_published_hour = max(0, int(os.environ.get("CIM_MAX_PUBLISHED_HOUR", "40")))
    expected_resolution_km = float(os.environ.get("CIM_RESOLUTION_KM", "8"))

    output = Path(args.output_dir)
    shutil.rmtree(output, ignore_errors=True)
    output.mkdir(parents=True)

    env = runenv(Path(args.run_env))
    date, cycle = env.get("RUN_DATE"), env.get("RUN_CYCLE")
    files = sorted(Path(args.run_dir).glob("wrfout_d01_*"), key=validtime)
    if not files:
        raise SystemExit("Nenhum wrfout produzido; nada sera publicado.")

    init = (
        dt.datetime.strptime(date + cycle, "%Y%m%d%H").replace(tzinfo=dt.timezone.utc)
        if date and cycle
        else validtime(files[0])
    )
    date, cycle = init.strftime("%Y%m%d"), init.strftime("%H")
    frames = []
    native_dx = native_dy = None

    for path in files:
        valid = validtime(path)
        hour = int(round((valid - init).total_seconds() / 3600))
        if hour < 0 or hour > max_published_hour or hour % output_interval_hours:
            continue

        with xr.open_dataset(path, engine="netcdf4", decode_times=False) as ds:
            ny, nx = ds["XLAT"].isel(Time=0).shape
            rows, cols = idx(ny, args.grid_y), idx(nx, args.grid_x)
            derived, variables, methods, detail = compute(ds, rows, cols)

            # Refletividade exatamente de REFL_10CM no wrfout.
            # Sem suavizacao, preenchimento, reamostragem ou substituto.
            refl = native_composite_reflectivity(ds)[np.ix_(rows, cols)]
            t2 = np.asarray(ds["T2"].isel(Time=0), float)[np.ix_(rows, cols)] - 273.15
            q2 = np.asarray(ds["Q2"].isel(Time=0), float)[np.ix_(rows, cols)]
            psfc = np.asarray(ds["PSFC"].isel(Time=0), float)[np.ix_(rows, cols)]
            es = 6.112 * np.exp(17.67 * np.maximum(t2, -80) / (np.maximum(t2, -80) + 243.5))
            vapor = (q2 * psfc / 100) / (0.622 + q2)
            humidity = np.clip(vapor / es * 100, 0, 100)
            lat, lon = derived["lat"], derived["lon"]

            dx_m = int(round(float(ds.attrs.get("DX", expected_resolution_km * 1000))))
            dy_m = int(round(float(ds.attrs.get("DY", expected_resolution_km * 1000))))
            native_dx = dx_m if native_dx is None else native_dx
            native_dy = dy_m if native_dy is None else native_dy
            resolution_km = round((dx_m + dy_m) / 2000.0, 3)

            payload = {
                "schema": "sideral-cim-wrf-native-v2",
                "model": args.model,
                "source": f"CIM WRF {resolution_km:g} km · {args.model.upper()} · REFL_10CM nativo",
                "runDate": date,
                "runCycle": f"{cycle}Z",
                "initTime": iso(init),
                "forecastHour": hour,
                "validTime": iso(valid),
                "resolutionKm": resolution_km,
                "dxMeters": dx_m,
                "dyMeters": dy_m,
                "gridX": len(cols),
                "gridY": len(rows),
                "nativeGrid": True,
                "reflectivitySource": "REFL_10CM_NATIVE",
                "variables": {"stp": variables["stp"], "scp": variables["scp"]},
                "diagnosticMethods": {"stp": methods["stp"], "scp": methods["scp"]},
                "fields": {
                    "lat": flat(lat, 4),
                    "lon": flat(lon, 4),
                    "reflectivity": flat(refl, 1, 0, 95),
                    "stp": flat(derived["stp"], 2, 0, 10),
                    "scp": flat(derived["scp"], 2, 0, 50),
                    "temperature": flat(t2, 1, -90, 60),
                    "humidity": flat(humidity, 0, 0, 100),
                },
            }

        rel = f"frames/f{hour:03d}.json.gz"
        write_gz(output / rel, payload)
        frames.append(
            {
                "index": len(frames),
                "forecastHour": hour,
                "validTime": iso(valid),
                "file": rel,
                "gridX": len(cols),
                "gridY": len(rows),
                "nativeGrid": True,
                "reflectivitySource": "REFL_10CM_NATIVE",
            }
        )

    if not frames:
        raise SystemExit("Nenhum quadro CIM foi produzido.")

    actual_resolution_km = round(((native_dx or 0) + (native_dy or 0)) / 2000.0, 3)
    if abs(actual_resolution_km - expected_resolution_km) > 0.05:
        raise SystemExit(
            f"Resolucao nativa inesperada: {actual_resolution_km} km; "
            f"esperado {expected_resolution_km:g} km"
        )

    metadata = {
        "schema": "sideral-cim-wrf-native-metadata-v2",
        "model": args.model,
        "resolutionKm": actual_resolution_km,
        "dxMeters": native_dx,
        "dyMeters": native_dy,
        "nativeGrid": True,
        "source": f"CIM WRF {actual_resolution_km:g} km · {args.model.upper()}",
        "runDate": date,
        "runCycle": f"{cycle}Z",
        "initTime": iso(init),
        "generatedAt": iso(dt.datetime.now(dt.timezone.utc)),
        "status": "complete",
        "forecastHorizonHours": max_published_hour,
        "frameCount": len(frames),
        "temporalResolutionMinutes": output_interval_hours * 60,
        "reflectivitySource": "REFL_10CM_NATIVE",
        "availableVariables": list(FIELDS),
        "frames": frames,
    }
    (output / "metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(metadata, ensure_ascii=False))


if __name__ == "__main__":
    main()
