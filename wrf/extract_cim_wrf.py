#!/usr/bin/env python3
"""Publica somente os cinco campos do CIM WRF 3 km a partir do wrfout real."""
from __future__ import annotations
import argparse, datetime as dt, json, shutil
from pathlib import Path
import numpy as np
import xarray as xr
from extract_wrf_json import native_composite_reflectivity
from extract_wrf_severe import compute, flat, idx, runenv, validtime, write_gz

FIELDS = ("reflectivity", "stp", "scp", "temperature", "humidity")

def iso(value): return value.isoformat().replace("+00:00", "Z")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", default="wrf_work/run")
    ap.add_argument("--run-env", default="wrf_diagnostics/run.env")
    ap.add_argument("--output-dir", required=True)
    ap.add_argument("--model", choices=("icon", "ecmwf"), required=True)
    ap.add_argument("--grid-x", type=int, default=240)
    ap.add_argument("--grid-y", type=int, default=200)
    args = ap.parse_args()
    output = Path(args.output_dir); shutil.rmtree(output, ignore_errors=True); output.mkdir(parents=True)
    env = runenv(Path(args.run_env)); date, cycle = env.get("RUN_DATE"), env.get("RUN_CYCLE")
    files = sorted(Path(args.run_dir).glob("wrfout_d01_*"), key=validtime)
    if not files: raise SystemExit("Nenhum wrfout produzido; nada serÃ¡ publicado.")
    init = dt.datetime.strptime(date + cycle, "%Y%m%d%H").replace(tzinfo=dt.timezone.utc) if date and cycle else validtime(files[0])
    date, cycle = init.strftime("%Y%m%d"), init.strftime("%H")
    frames = []
    for path in files:
        valid = validtime(path); hour = int(round((valid - init).total_seconds() / 3600))
        if hour % 3: continue
        with xr.open_dataset(path, engine="netcdf4", decode_times=False) as ds:
            ny, nx = ds["XLAT"].isel(Time=0).shape; rows, cols = idx(ny, args.grid_y), idx(nx, args.grid_x)
            derived, variables, methods, detail = compute(ds, rows, cols)
            refl = native_composite_reflectivity(ds)[np.ix_(rows, cols)]
            t2 = np.asarray(ds["T2"].isel(Time=0), float)[np.ix_(rows, cols)] - 273.15
            q2 = np.asarray(ds["Q2"].isel(Time=0), float)[np.ix_(rows, cols)]
            psfc = np.asarray(ds["PSFC"].isel(Time=0), float)[np.ix_(rows, cols)]
            es = 6.112 * np.exp(17.67 * np.maximum(t2, -80) / (np.maximum(t2, -80) + 243.5))
            vapor = (q2 * psfc / 100) / (0.622 + q2)
            humidity = np.clip(vapor / es * 100, 0, 100)
            lat, lon = derived["lat"], derived["lon"]
            payload = {"schema":"sideral-cim-wrf-3km-v1", "model":args.model,
                "source":f"CIM WRF 3 km Â· {args.model.upper()} Â· REFL_10CM nativo",
                "runDate":date, "runCycle":f"{cycle}Z", "initTime":iso(init), "forecastHour":hour, "validTime":iso(valid),
                "dxMeters":round(float(ds.attrs.get("DX", 3000))), "dyMeters":round(float(ds.attrs.get("DY", 3000))),
                "gridX":len(cols), "gridY":len(rows), "reflectivitySource":"REFL_10CM_NATIVE",
                "variables":{"stp":variables["stp"], "scp":variables["scp"]},
                "diagnosticMethods":{"stp":methods["stp"], "scp":methods["scp"]},
                "fields":{"lat":flat(lat,4), "lon":flat(lon,4), "reflectivity":flat(refl,1,0,95),
                          "stp":flat(derived["stp"],2,0,10), "scp":flat(derived["scp"],2,0,50),
                          "temperature":flat(t2,1,-90,60), "humidity":flat(humidity,0,0,100)}}
        rel = f"frames/f{hour:03d}.json.gz"; write_gz(output / rel, payload)
        frames.append({"index":len(frames), "forecastHour":hour, "validTime":iso(valid), "file":rel, "gridX":len(cols), "gridY":len(rows)})
    if not frames: raise SystemExit("Nenhum quadro de 3 em 3 horas foi produzido.")
    metadata = {"schema":"sideral-cim-wrf-3km-metadata-v1", "model":args.model, "resolutionKm":3,
        "source":f"CIM WRF 3 km Â· {args.model.upper()}", "runDate":date, "runCycle":f"{cycle}Z", "initTime":iso(init),
        "generatedAt":iso(dt.datetime.now(dt.timezone.utc)), "status":"complete", "frameCount":len(frames),
        "temporalResolutionMinutes":180, "reflectivitySource":"REFL_10CM_NATIVE",
        "availableVariables":list(FIELDS), "frames":frames}
    (output / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(metadata, ensure_ascii=False))
if __name__ == "__main__": main()

