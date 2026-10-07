#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import json
from pathlib import Path

import numpy as np
from netCDF4 import Dataset

EPS = 0.622


def arr2d(ds, name):
    if name not in ds.variables:
        return None
    a = np.asarray(ds.variables[name][0], dtype=np.float32)
    while a.ndim > 2:
        a = a[0]
    if a.ndim != 2:
        return None
    return a


def flatten(a, decimals=2):
    if a is None:
        return None
    x = np.asarray(a, dtype=np.float32)
    x = np.round(x, decimals)
    return [float(v) if np.isfinite(v) else None for v in x.reshape(-1)]


def scalar_meta(a):
    if a is None:
        return None
    x = np.asarray(a, dtype=np.float32)
    finite = x[np.isfinite(x)]
    if finite.size == 0:
        return {"min": None, "max": None}
    return {"min": float(np.min(finite)), "max": float(np.max(finite))}


def read_time(ds):
    var = ds.variables.get("Times")
    if var is None:
        return None
    raw = np.asarray(var[0])
    text = "".join(x.decode() if isinstance(x, bytes) else str(x) for x in raw)
    text = text.replace("_", " ")
    try:
        return dt.datetime.strptime(text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=dt.timezone.utc)
    except ValueError:
        return None


def native_reflectivity(ds):
    if "REFL_10CM" in ds.variables:
        a = np.asarray(ds.variables["REFL_10CM"][0], dtype=np.float32)
    elif "REFL_10CM_MAX" in ds.variables:
        a = np.asarray(ds.variables["REFL_10CM_MAX"][0], dtype=np.float32)
        while a.ndim > 2:
            a = a[0]
        return a, a
    else:
        return None, None

    while a.ndim > 3:
        a = a[0]
    if a.ndim == 3:
        return a[0], np.nanmax(a, axis=0)
    if a.ndim == 2:
        return a, a
    return None, None


def dewpoint_rh(t2, q2, psfc):
    q = np.maximum(np.asarray(q2, dtype=np.float32), 1e-8)
    p = np.asarray(psfc, dtype=np.float32)
    t = np.asarray(t2, dtype=np.float32)
    vapor = q * p / (EPS + q)
    vapor = np.maximum(vapor, 1.0)
    l = np.log(vapor / 611.2)
    td = 243.5 * l / (17.67 - l)
    tc = t - 273.15
    es = 611.2 * np.exp(17.67 * tc / (tc + 243.5))
    rh = np.clip(100.0 * vapor / es, 0.0, 100.0)
    return td.astype(np.float32), rh.astype(np.float32)


def frame_payload(path):
    with Dataset(path) as ds:
        ds.set_auto_maskandscale(True)
        lat = arr2d(ds, "XLAT")
        lon = arr2d(ds, "XLONG")
        if lat is None or lon is None:
            raise RuntimeError(f"XLAT/XLONG ausentes em {path.name}")

        refl, comp = native_reflectivity(ds)
        u10, v10 = arr2d(ds, "U10"), arr2d(ds, "V10")
        t2, q2, psfc = arr2d(ds, "T2"), arr2d(ds, "Q2"), arr2d(ds, "PSFC")
        slp = arr2d(ds, "SLP")
        rainc, rainnc, rainsh = arr2d(ds, "RAINC"), arr2d(ds, "RAINNC"), arr2d(ds, "RAINSH")
        snownc, graupel, hail, cldfra = [arr2d(ds, n) for n in ("SNOWNC", "GRAUPELNC", "HAILNC", "CLDFRA")]

        fields = {}
        fields["lat"] = flatten(lat, 4)
        fields["lon"] = flatten(lon, 4)

        if refl is not None:
            fields["reflectivityDbz"] = flatten(refl, 1)
        if comp is not None:
            fields["reflectivityCompositeDbz"] = flatten(comp, 1)

        if u10 is not None:
            fields["u10Ms"] = flatten(u10, 1)
        if v10 is not None:
            fields["v10Ms"] = flatten(v10, 1)
        if u10 is not None and v10 is not None:
            wind = np.hypot(u10, v10)
            direction = (np.degrees(np.arctan2(-u10, -v10)) + 360.0) % 360.0
            fields["wind10Ms"] = flatten(wind, 1)
            fields["wind10DirDeg"] = flatten(direction, 1)

        if t2 is not None:
            fields["t2C"] = flatten(t2 - 273.15, 1)
        if q2 is not None:
            fields["q2KgKg"] = flatten(q2, 6)
        if psfc is not None:
            fields["psfcHpa"] = flatten(psfc / 100.0, 1)
        if slp is not None:
            fields["slpHpa"] = flatten(slp / 100.0 if np.nanmedian(slp) > 2000 else slp, 1)
        if t2 is not None and q2 is not None and psfc is not None:
            td2, rh2 = dewpoint_rh(t2, q2, psfc)
            fields["td2C"] = flatten(td2, 1)
            fields["rh2Pct"] = flatten(rh2, 1)

        for value, key, decimals in (
            (rainc, "raincMm", 2), (rainnc, "rainncMm", 2), (rainsh, "rainshMm", 2),
            (snownc, "snowncMm", 2), (graupel, "graupelncMm", 2),
            (hail, "hailncMm", 2), (cldfra, "cloudFraction", 3),
        ):
            if value is not None:
                fields[key] = flatten(value, decimals)

        if rainc is not None and rainnc is not None:
            fields["rainTotalMm"] = flatten(rainc + rainnc, 2)

        valid = read_time(ds)
        if valid is None:
            raise RuntimeError(f"Tempo Times ausente/invalido em {path.name}")

        return {
            "file": path.name,
            "time": valid.isoformat().replace("+00:00", "Z"),
            "grid": {"nx": int(lat.shape[1]), "ny": int(lat.shape[0])},
            "fields": fields,
            "ranges": {
                key: scalar_meta(np.asarray(value, dtype=np.float32).reshape(lat.shape))
                for key, value in (
                    ("reflectivityDbz", refl),
                    ("reflectivityCompositeDbz", comp),
                    ("wind10Ms", np.hypot(u10, v10) if u10 is not None and v10 is not None else None),
                    ("t2C", t2 - 273.15 if t2 is not None else None),
                    ("psfcHpa", psfc / 100.0 if psfc is not None else None),
                )
                if value is not None
            },
        }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--output-dir", required=True)
    ap.add_argument("--expected-start", type=int, default=0)
    ap.add_argument("--expected-end", type=int, default=42)
    args = ap.parse_args()

    run_dir = Path(args.run_dir)
    out = Path(args.output_dir)
    frames_dir = out / "frames"
    out.mkdir(parents=True, exist_ok=True)
    frames_dir.mkdir(parents=True, exist_ok=True)

    files = sorted(run_dir.glob("wrfout_d01_*"))
    if not files:
        raise SystemExit("Nenhum WRFOUT encontrado")

    unique = {}
    for path in files:
        with Dataset(path) as ds:
            valid = read_time(ds)
        if valid is None:
            print(f"[CBR][SKIP] tempo invalido: {path.name}")
            continue
        unique[valid] = path

    selected = sorted(unique.items())
    init = selected[0][0]
    frames = []

    for valid, path in selected:
        fh = int(round((valid - init).total_seconds() / 3600.0))
        if fh < args.expected_start or fh > args.expected_end:
            continue
        payload = frame_payload(path)
        payload["model"] = "WRF CBR 4 KM"
        payload["modelKey"] = "cbr_wrf_4km"
        payload["resolutionKm"] = 3
        payload["forecastHour"] = fh
        payload["nativeReflectivity"] = "REFL_10CM"
        payload["nativeGrid"] = True

        target = frames_dir / f"f{fh:03d}.json.gz"
        with target.open("wb") as raw:
            with gzip.GzipFile(filename="", mode="wb", fileobj=raw, compresslevel=9, mtime=0) as z:
                z.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8"))

        frames.append({
            "forecastHour": fh,
            "time": payload["time"],
            "file": f"frames/f{fh:03d}.json.gz",
            "grid": payload["grid"],
            "ranges": payload["ranges"],
        })

    expected = list(range(args.expected_start, args.expected_end + 1))
    actual = [x["forecastHour"] for x in frames]
    missing = [x for x in expected if x not in actual]
    first_grid = frames[0]["grid"] if frames else None

    metadata = {
        "schemaVersion": "sideral-cbr-wrf-4km-v1",
        "model": "WRF CBR 4 KM",
        "modelKey": "cbr_wrf_4km",
        "sourceModel": "ICON",
        "resolutionKm": 4,
        "forecastHours": [args.expected_start, args.expected_end],
        "temporalResolutionMinutes": 60,
        "initTime": init.isoformat().replace("+00:00", "Z"),
        "grid": first_grid or {"nx": 401, "ny": 501},
        "domain": {"west": -50.0, "east": -30.0, "south": -21.0, "north": 1.0},
        "projection": "Lambert Conformal",
        "nativeReflectivity": "REFL_10CM",
        "reflectivityComposite": "maximum through the native WRF column",
        "wind": {"u": "U10", "v": "V10", "barbs": "frontend renders from U10/V10"},
        "fields": [
            "reflectivityDbz", "reflectivityCompositeDbz",
            "u10Ms", "v10Ms", "wind10Ms", "wind10DirDeg",
            "t2C", "q2KgKg", "td2C", "rh2Pct",
            "psfcHpa", "slpHpa",
            "raincMm", "rainncMm", "rainshMm", "rainTotalMm",
            "snowncMm", "graupelncMm", "hailncMm", "cloudFraction"
        ],
        "frames": frames,
        "frameCount": len(frames),
        "missingForecastHours": missing,
        "status": "complete" if not missing else "partial",
    }

    (out / "metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )

    print(f"[CBR] JSON publicado em {frames_dir}: {len(frames)} frames; faltantes={missing}")


if __name__ == "__main__":
    main()
