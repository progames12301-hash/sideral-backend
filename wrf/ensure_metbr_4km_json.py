#!/usr/bin/env python3
import gzip, json, sys
from pathlib import Path

root = Path(sys.argv[1] if len(sys.argv) > 1 else "metbr_publish")
meta_path = root / "metadata.json"
out = root / "metbr_wrf_4km.json"
meta = json.loads(meta_path.read_text(encoding="utf-8"))
frames = meta.get("frames") or []
if not frames:
    raise SystemExit("metadata.json sem frames")

payload = {
    "schema": "sideral-wrf-reflectivity-v2",
    "model": meta.get("model", "icon"),
    "resolutionKm": meta.get("resolutionKm", 4),
    "runDate": meta.get("runDate"),
    "runCycle": meta.get("runCycle"),
    "initTime": meta.get("initTime"),
    "generatedAt": meta.get("generatedAt"),
    "reflectivitySource": meta.get("reflectivitySource", "REFL_10CM"),
    "nativeGrid": meta.get("nativeGrid", True),
    "grid": meta.get("grid"),
    "frames": [],
}

for f in frames:
    src = root / f["file"]
    if not src.is_file() or src.stat().st_size == 0:
        raise SystemExit(f"frame ausente/vazio: {src}")
    with gzip.open(src, "rt", encoding="utf-8") as fh:
        data = json.load(fh)
    payload["frames"].append({
        "index": f.get("index"),
        "forecastHour": f.get("forecastHour"),
        "validTime": f.get("validTime"),
        "gridX": f.get("gridX"),
        "gridY": f.get("gridY"),
        "sourceVariable": f.get("sourceVariable", "REFL_10CM"),
        "reflectivityStats": f.get("reflectivityStats", {}),
        "data": data,
    })

out.write_text(json.dumps(payload, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
print(f"METBR JSON garantido: {out} | frames={len(frames)} | bytes={out.stat().st_size}")
