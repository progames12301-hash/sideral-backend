#!/usr/bin/env python3
"""Publish incremental CBR/TARC JSON frames from compact per-hour NPZ checkpoints.

The segment runners already create these NPZ products, so this avoids re-downloading
multi-gigabyte native WRFOUT files just to update the operational web dataset.
"""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import json
import re
from pathlib import Path

import numpy as np

NAME_RE = re.compile(r"^(\d{8})_(\d{6})\.npz$")
FIELD_MAP = {
    "reflectivity_native_dbz": ("reflectivityDbz", 1),
    "reflectivity_composite_dbz": ("reflectivityCompositeDbz", 1),
    "u10_ms": ("u10Ms", 1),
    "v10_ms": ("v10Ms", 1),
    "wind10_ms": ("wind10Ms", 1),
    "wind10_dir_deg": ("wind10DirDeg", 1),
    "t2_c": ("t2C", 1),
    "q2_kgkg": ("q2KgKg", 6),
    "td2_c": ("td2C", 1),
    "rh2_pct": ("rh2Pct", 1),
    "psfc_hpa": ("psfcHpa", 1),
    "slp_hpa": ("slpHpa", 1),
    "rainc_mm": ("raincMm", 2),
    "rainnc_mm": ("rainncMm", 2),
    "rainsh_mm": ("rainshMm", 2),
    "rain_total_mm": ("rainTotalMm", 2),
    "snownc_mm": ("snowncMm", 2),
    "graupelnc_mm": ("graupelncMm", 2),
    "hailnc_mm": ("hailncMm", 2),
    "cloud_fraction": ("cloudFraction", 3),
}
ALL_FIELDS = [
    "reflectivityDbz", "reflectivityCompositeDbz",
    "u10Ms", "v10Ms", "wind10Ms", "wind10DirDeg",
    "t2C", "q2KgKg", "td2C", "rh2Pct",
    "psfcHpa", "slpHpa",
    "raincMm", "rainncMm", "rainshMm", "rainTotalMm",
    "snowncMm", "graupelncMm", "hailncMm", "cloudFraction",
]


def parse_run_init(path: Path) -> dt.datetime:
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    date = values.get("RUN_DATE", "").replace("-", "")
    cycle = values.get("RUN_CYCLE", "")
    if not re.fullmatch(r"\d{8}", date) or not re.fullmatch(r"\d{2}", cycle):
        raise SystemExit(f"RUN_DATE/RUN_CYCLE inválidos em {path}")
    if cycle not in {"00", "06", "12", "18"}:
        raise SystemExit(f"RUN_CYCLE inválido em {path}: {cycle}")
    try:
        return dt.datetime.strptime(date + cycle, "%Y%m%d%H").replace(tzinfo=dt.timezone.utc)
    except ValueError as exc:
        raise SystemExit(f"Data/ciclo inválido em {path}: {exc}") from exc


def iso(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def flat_array(value, decimals: int):
    arr = np.asarray(value, dtype=np.float32)
    arr = np.round(arr, decimals)
    return [float(v) if np.isfinite(v) else None for v in arr.reshape(-1)]


def stats(value):
    arr = np.asarray(value, dtype=np.float32)
    finite = arr[np.isfinite(arr)]
    if finite.size == 0:
        return {"min": None, "max": None}
    return {"min": float(np.min(finite)), "max": float(np.max(finite))}


def frame_from_npz(path: Path, init: dt.datetime, model: str, max_hour: int):
    match = NAME_RE.fullmatch(path.name)
    if not match:
        return None
    valid = dt.datetime.strptime(match.group(1) + match.group(2), "%Y%m%d%H%M%S").replace(
        tzinfo=dt.timezone.utc
    )
    delta = (valid - init).total_seconds() / 3600.0
    fh = int(round(delta))
    if abs(delta - fh) > 1e-6 or fh < 0 or fh > max_hour:
        return None

    with np.load(path, allow_pickle=False) as archive:
        src = {key: np.asarray(archive[key], dtype=np.float32) for key in archive.files}

    lat = src.get("lat")
    lon = src.get("lon")
    if lat is None or lon is None or lat.ndim != 2 or lon.shape != lat.shape:
        raise RuntimeError(f"Grade lat/lon ausente ou incompatível em {path.name}")
    shape = lat.shape
    fields = {"lat": flat_array(lat, 4), "lon": flat_array(lon, 4)}
    source_fields = {}
    for old_name, (new_name, decimals) in FIELD_MAP.items():
        if old_name not in src:
            continue
        arr = src[old_name]
        if arr.shape != shape:
            raise RuntimeError(f"Campo {old_name} com shape {arr.shape}; esperado {shape} em {path.name}")
        fields[new_name] = flat_array(arr, decimals)
        source_fields[new_name] = arr

    if "reflectivityDbz" not in fields or "reflectivityCompositeDbz" not in fields:
        raise RuntimeError(f"Refletividade nativa/composite ausente em {path.name}")

    ranges = {}
    for key in ("reflectivityDbz", "reflectivityCompositeDbz", "wind10Ms", "t2C", "psfcHpa"):
        if key in source_fields or key in fields:
            lookup = {
                "reflectivityDbz": "reflectivity_native_dbz",
                "reflectivityCompositeDbz": "reflectivity_composite_dbz",
                "wind10Ms": "wind10_ms",
                "t2C": "t2_c",
                "psfcHpa": "psfc_hpa",
            }[key]
            if lookup in src:
                ranges[key] = stats(src[lookup])

    payload = {
        "file": path.name,
        "time": iso(valid),
        "grid": {"nx": int(shape[1]), "ny": int(shape[0])},
        "fields": fields,
        "ranges": ranges,
        "model": "WRF CBR 4 KM" if model == "cbr" else "WRF TARC 3 KM",
        "modelKey": "cbr_wrf_4km" if model == "cbr" else "tarc_wrf_3km",
        "resolutionKm": 4 if model == "cbr" else 3,
        "forecastHour": fh,
        "nativeReflectivity": "REFL_10CM",
        "nativeGrid": True,
    }
    return payload


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", choices=("cbr", "tarc"), required=True)
    ap.add_argument("--input-dir", required=True)
    ap.add_argument("--run-env", required=True)
    ap.add_argument("--output-dir", required=True)
    ap.add_argument("--source-run-id", type=int, default=None)
    args = ap.parse_args()

    model = args.model
    max_hour = 42 if model == "cbr" else 48
    expected = list(range(max_hour + 1))
    init = parse_run_init(Path(args.run_env))
    input_dir = Path(args.input_dir)
    out = Path(args.output_dir)
    frames_dir = out / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)

    for stale in frames_dir.glob("f*.json.gz"):
        stale.unlink()

    frames_by_hour = {}
    for path in sorted(input_dir.glob("*.npz")):
        payload = frame_from_npz(path, init, model, max_hour)
        if payload is None:
            continue
        fh = payload["forecastHour"]
        if fh in frames_by_hour:
            # Same valid time can be re-uploaded by an adjacent checkpoint segment.
            # Keep the first lexically sorted compact asset, whose timestamp is identical.
            continue
        target = frames_dir / f"f{fh:03d}.json.gz"
        with target.open("wb") as raw:
            with gzip.GzipFile(filename="", mode="wb", fileobj=raw, compresslevel=6, mtime=0) as zipped:
                zipped.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8"))
        frames_by_hour[fh] = {
            "forecastHour": fh,
            "time": payload["time"],
            "file": f"frames/f{fh:03d}.json.gz",
            "grid": payload["grid"],
            "ranges": payload["ranges"],
            "availableVariables": sorted(payload["fields"].keys()),
        }

    frames = [frames_by_hour[hour] for hour in sorted(frames_by_hour)]
    missing = sorted(set(expected) - set(frames_by_hour))
    if not frames:
        raise SystemExit("Nenhum frame NPZ utilizável foi encontrado no checkpoint")
    if frames[0]["forecastHour"] != 0:
        raise SystemExit(f"Checkpoint sem F000; primeiro horário encontrado F{frames[0]['forecastHour']:03d}")

    first_grid = frames[0]["grid"]
    complete = not missing
    metadata = {
        "schemaVersion": "sideral-cbr-wrf-4km-v1" if model == "cbr" else "sideral-tarc-wrf-3km-v1",
        "model": "WRF CBR 4 KM" if model == "cbr" else "WRF TARC 3 KM",
        "modelKey": "cbr_wrf_4km" if model == "cbr" else "tarc_wrf_3km",
        "sourceModel": "ICON",
        "resolutionKm": 4 if model == "cbr" else 3,
        "forecastHours": [0, max_hour],
        "temporalResolutionMinutes": 60,
        "initTime": iso(init),
        "grid": first_grid,
        "domain": (
            {"west": -50.0, "east": -30.0, "south": -21.0, "north": 1.0}
            if model == "cbr"
            else {"west": -59.0, "east": -48.0, "south": -34.0, "north": -22.0}
        ),
        "projection": "Lambert Conformal",
        "nativeReflectivity": "REFL_10CM",
        "reflectivityComposite": "maximum through the native WRF column",
        "wind": {"u": "U10", "v": "V10", "barbs": "frontend renders from U10/V10"},
        "fields": ALL_FIELDS,
        "frames": frames,
        "frameCount": len(frames),
        "missingForecastHours": missing,
        "status": "complete" if complete else "partial",
        "publicationStatus": "complete" if complete else "partial",
    }
    if args.source_run_id is not None:
        metadata["sourceRunId"] = args.source_run_id
    (out / "metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    print(
        f"[{model.upper()}] JSON pronto: {len(frames)}/{max_hour + 1} frames, "
        f"faltantes={len(missing)}, init={iso(init)}, status={metadata['status']}"
    )


if __name__ == "__main__":
    main()
