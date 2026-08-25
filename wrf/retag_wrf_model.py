#!/usr/bin/env python3
from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path

LABELS = {
    "gfs": "WRF Sul 4 km inicializado pelo GFS (REFL_10CM nativo)",
    "icon": "WRF Sul 4 km inicializado pelo ICON Global (REFL_10CM nativo)",
    "ecmwf": "WRF Sul 4 km inicializado pelo ECMWF IFS (REFL_10CM nativo)",
}


def read_gz(path: Path) -> dict:
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return json.load(fh)


def write_gz(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    with path.open("wb") as fh:
        with gzip.GzipFile(filename="", mode="wb", fileobj=fh, compresslevel=9, mtime=0) as gz:
            gz.write(raw)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--root", required=True)
    p.add_argument("--model", choices=sorted(LABELS), required=True)
    args = p.parse_args()

    root = Path(args.root)
    meta_path = root / "metadata.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    model = args.model
    label = LABELS[model]

    if meta.get("reflectivitySource") != "REFL_10CM_NATIVE":
        raise SystemExit(f"Saida nao e REFL_10CM nativo: {meta.get('reflectivitySource')}")

    for frame in meta.get("frames", []):
        old_rel = str(frame["file"])
        old_path = root / old_rel
        data = read_gz(old_path)
        if data.get("reflectivitySource") != "REFL_10CM_NATIVE":
            raise SystemExit(f"Frame nao nativo: {old_rel}")

        data["model"] = model
        data["initialConditionModel"] = model.upper()
        data["source"] = label

        new_rel = f"{model}/{Path(old_rel).name}"
        new_path = root / new_rel
        write_gz(new_path, data)
        if new_path.resolve() != old_path.resolve():
            old_path.unlink(missing_ok=True)

        frame["file"] = new_rel
        frame["model"] = model
        frame["source"] = label
        frame["initialConditionModel"] = model.upper()
        frame["reflectivitySource"] = "REFL_10CM_NATIVE"

    old_gfs = root / "gfs"
    if model != "gfs" and old_gfs.exists():
        try:
            old_gfs.rmdir()
        except OSError:
            pass

    meta["model"] = model
    meta["initialConditionModel"] = model.upper()
    meta["source"] = label
    meta["reflectivitySource"] = "REFL_10CM_NATIVE"
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(meta, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
