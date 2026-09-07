#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import shutil
from pathlib import Path

FIELD_ORDER = [
    "stp", "scp", "srh01", "srh03", "bulkShear06", "effectiveBulkShear",
    "lclHeight", "cin", "sbcape", "mlcape", "mucapeWrf2", "pwat",
    "thetaE850", "thetaEAdvection", "wind850", "wind500", "vorticity500",
    "omega700", "mslp", "thickness", "dewpoint2m", "kIndex", "totalTotals",
]


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def load_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def hour_from_path(path: Path) -> int | None:
    match = re.search(r"f(\d{3})\.json\.gz$", path.name)
    return int(match.group(1)) if match else None


def merge_classifications(entries: list[dict]) -> dict:
    if not entries:
        return {"classification": "unavailable", "status": "unavailable"}
    classes = {str(entry.get("classification") or "unavailable") for entry in entries}
    if "derived" in classes:
        classification = "derived"
    elif "native" in classes:
        classification = "native"
    else:
        classification = "unavailable"
    available = sum(1 for entry in entries if entry.get("status") == "available")
    total = sum(int(entry.get("totalFrames") or 1) for entry in entries)
    result = {
        "classification": classification if available else "unavailable",
        "status": "available" if available else "unavailable",
        "availableSegments": available,
        "segmentCount": len(entries),
        "totalFramesReported": total,
    }
    diagnostics = sorted({
        text
        for entry in entries
        for text in (entry.get("diagnostics") or [])
        if isinstance(text, str) and text
    })
    if diagnostics:
        result["diagnostics"] = diagnostics
    native_sources = sorted({
        entry.get("nativeSource")
        for entry in entries
        if entry.get("nativeSource")
    })
    if native_sources:
        result["nativeSources"] = native_sources
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", action="append", dest="inputs", default=[])
    parser.add_argument("--output", required=True)
    parser.add_argument("--model", required=True, choices=("gfs", "icon", "ecmwf"))
    parser.add_argument("--temporal-resolution-minutes", type=int, default=180)
    args = parser.parse_args()

    roots = [Path(item) for item in args.inputs]
    output = Path(args.output)
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True, exist_ok=True)

    diagnostics: dict = {
        "schema": "sideral-wrf2-severe-publish-diagnostics-v1",
        "model": args.model,
        "generatedAt": utc_now(),
        "inputs": [],
        "missingFiles": [],
        "duplicateFrames": [],
        "metadataErrors": [],
        "notes": [
            "Este publicador copia somente arquivos WRF2 severos.",
            "Nenhum arquivo de refletividade nativa é lido, alterado ou publicado.",
            "Ausência permanece ausente; nenhum frame faltante é sintetizado com zeros.",
        ],
    }

    metas: list[dict] = []
    variable_entries: dict[str, list[dict]] = {field: [] for field in FIELD_ORDER}
    by_hour: dict[int, Path] = {}
    expected_hours: set[int] = set()
    frame_metadata: dict[int, dict] = {}

    for root in roots:
        item_diag = {
            "root": str(root),
            "metadata": False,
            "diagnostics": False,
            "copiedFrames": 0,
        }
        meta_path = root / "metadata.json"
        diag_path = root / "diagnostics.json"
        meta = load_json(meta_path)
        diag = load_json(diag_path)
        item_diag["metadata"] = meta is not None
        item_diag["diagnostics"] = diag is not None

        if meta is None:
            diagnostics["metadataErrors"].append({
                "root": str(root),
                "error": "metadata.json ausente ou inválido",
            })
        else:
            if str(meta.get("model") or "").lower() != args.model:
                diagnostics["metadataErrors"].append({
                    "root": str(root),
                    "error": f"modelo incompatível: {meta.get('model')}",
                })
            else:
                metas.append(meta)
                for field in FIELD_ORDER:
                    entry = (meta.get("variables") or {}).get(field)
                    if isinstance(entry, dict):
                        variable_entries[field].append(entry)

                for expected in meta.get("expectedFrames") or []:
                    match = re.search(r"f(\d{3})\.json\.gz$", str(expected))
                    if match:
                        expected_hours.add(int(match.group(1)))

                for frame in meta.get("frames") or []:
                    try:
                        fh = int(frame["forecastHour"])
                    except Exception:
                        continue
                    frame_metadata[fh] = dict(frame)

        severe_root = root / "severe" / args.model
        for path in sorted(severe_root.glob("f*.json.gz")) if severe_root.exists() else []:
            fh = hour_from_path(path)
            if fh is None:
                continue
            if fh in by_hour:
                diagnostics["duplicateFrames"].append({
                    "forecastHour": fh,
                    "kept": str(path),
                    "replaced": str(by_hour[fh]),
                })
            by_hour[fh] = path
            item_diag["copiedFrames"] += 1

        if diag is not None:
            for name in diag.get("missingFrames") or []:
                match = re.search(r"f(\d{3})\.json\.gz$", str(name))
                if match:
                    expected_hours.add(int(match.group(1)))
        diagnostics["inputs"].append(item_diag)

    if not expected_hours:
        expected_hours.update(frame_metadata)
        expected_hours.update(by_hour)

    frames: list[dict] = []
    for fh in sorted(by_hour):
        src = by_hour[fh]
        rel = Path("severe") / args.model / f"f{fh:03d}.json.gz"
        dst = output / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        frame = dict(frame_metadata.get(fh) or {})
        frame.update({
            "index": len(frames),
            "forecastHour": fh,
            "file": rel.as_posix(),
        })
        frames.append(frame)

    missing = sorted(expected_hours - set(by_hour))
    diagnostics["missingFiles"] = [
        {
            "forecastHour": fh,
            "file": f"severe/{args.model}/f{fh:03d}.json.gz",
            "reason": "Frame esperado não foi encontrado nos artifacts severos.",
        }
        for fh in missing
    ]

    if not frames:
        status = "unavailable"
    elif missing or diagnostics["metadataErrors"]:
        status = "partial"
    else:
        status = "complete"

    template = metas[0] if metas else {}
    run_date = template.get("runDate")
    run_cycle = template.get("runCycle")
    init_time = template.get("initTime")
    sources = sorted({
        str(meta.get("source"))
        for meta in metas
        if meta.get("source")
    })
    source = sources[0] if len(sources) == 1 else f"WRF 2 Sudeste 4 km {args.model.upper()} · diagnósticos severos"

    variables = {
        field: merge_classifications(variable_entries[field])
        for field in FIELD_ORDER
    }

    diagnostic_methods = {}
    for meta in metas:
        methods = meta.get("diagnosticMethods")
        if isinstance(methods, dict) and methods:
            diagnostic_methods.update(methods)

    diagnostics["status"] = status
    diagnostics["expectedForecastHours"] = sorted(expected_hours)
    diagnostics["publishedForecastHours"] = sorted(by_hour)
    diagnostics["frameCount"] = len(frames)

    metadata = {
        "schema": "sideral-wrf2-severe-metadata-v2",
        "model": args.model,
        "source": source,
        "runDate": run_date,
        "runCycle": run_cycle,
        "initTime": init_time,
        "generatedAt": utc_now(),
        "status": status,
        "frameCount": len(frames),
        "temporalResolutionMinutes": args.temporal_resolution_minutes,
        "variables": variables,
        "diagnosticMethods": diagnostic_methods,
        "diagnostics": {
            "file": "diagnostics.json",
            "missingFrameCount": len(missing),
            "metadataErrorCount": len(diagnostics["metadataErrors"]),
            "inputArtifactCount": len(roots),
        },
        "reflectivity": {
            "status": "independent",
            "source": "REFL_10CM_NATIVE",
            "note": "Os diagnósticos severos não alteram a refletividade nativa.",
        },
        "frames": frames,
    }

    write_json(output / "metadata.json", metadata)
    write_json(output / "diagnostics.json", diagnostics)
    print(json.dumps(metadata, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
