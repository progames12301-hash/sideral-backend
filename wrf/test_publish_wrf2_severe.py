#!/usr/bin/env python3
from __future__ import annotations

import gzip
import json
import subprocess
import sys
import tempfile
from pathlib import Path


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_gzip_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False)


def main() -> None:
    script = Path(__file__).with_name("publish_wrf2_severe.py")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        seg1 = root / "seg1"
        seg2 = root / "seg2"
        out = root / "out"

        base_meta = {
            "schema": "sideral-wrf2-severe-metadata-v2",
            "model": "gfs",
            "source": "WRF 2 Sudeste 4 km GFS · diagnósticos severos",
            "runDate": "20260907",
            "runCycle": "12Z",
            "initTime": "2026-09-07T12:00:00Z",
            "generatedAt": "2026-09-07T12:00:00Z",
            "status": "complete",
            "frameCount": 2,
            "temporalResolutionMinutes": 180,
            "variables": {
                "stp": {"classification": "derived", "status": "available", "totalFrames": 2},
                "sbcape": {"classification": "native", "status": "available", "totalFrames": 2},
            },
            "diagnosticMethods": {
                "stp": {
                    "classification": "derived",
                    "components": ["MLCAPE", "LCL", "SRH 0-1 km", "bulk shear 0-6 km", "MLCIN"],
                }
            },
            "diagnostics": {"file": "diagnostics.json"},
            "reflectivity": {
                "status": "independent",
                "source": "REFL_10CM_NATIVE",
                "note": "Os diagnósticos severos não alteram a refletividade nativa.",
            },
        }

        meta1 = dict(base_meta)
        meta1["expectedFrames"] = ["severe/gfs/f063.json.gz", "severe/gfs/f066.json.gz"]
        meta1["frames"] = [
            {"forecastHour": 63, "file": "severe/gfs/f063.json.gz"},
            {"forecastHour": 66, "file": "severe/gfs/f066.json.gz"},
        ]
        write_json(seg1 / "metadata.json", meta1)
        write_json(seg1 / "diagnostics.json", {"missingFrames": ["f066.json.gz"]})
        write_gzip_json(seg1 / "severe/gfs/f063.json.gz", {"fields": {"lat": [-20.0], "lon": [-45.0], "stp": [1.0]}})
        # f066 intentionally absent

        meta2 = dict(base_meta)
        meta2["expectedFrames"] = ["severe/gfs/f069.json.gz"]
        meta2["frames"] = [{"forecastHour": 69, "file": "severe/gfs/f069.json.gz"}]
        write_json(seg2 / "metadata.json", meta2)
        write_json(seg2 / "diagnostics.json", {"missingFrames": []})
        write_gzip_json(seg2 / "severe/gfs/f069.json.gz", {"fields": {"lat": [-20.0], "lon": [-45.0], "stp": [2.0]}})

        subprocess.run(
            [
                sys.executable,
                str(script),
                "--input", str(seg1),
                "--input", str(seg2),
                "--output", str(out),
                "--model", "gfs",
            ],
            check=True,
        )

        meta = json.loads((out / "metadata.json").read_text(encoding="utf-8"))
        diag = json.loads((out / "diagnostics.json").read_text(encoding="utf-8"))

        assert meta["status"] == "partial", meta
        assert meta["frameCount"] == 2, meta
        assert meta["reflectivity"]["source"] == "REFL_10CM_NATIVE", meta
        assert not (out / "severe/gfs/f066.json.gz").exists()
        assert any(item["forecastHour"] == 66 for item in diag["missingFiles"]), diag
        assert meta["variables"]["stp"]["classification"] == "derived", meta
        assert meta["variables"]["sbcape"]["classification"] == "native", meta

        print("OK: missing severe frame remains missing, status=partial, native reflectivity stays independent")


if __name__ == "__main__":
    main()
