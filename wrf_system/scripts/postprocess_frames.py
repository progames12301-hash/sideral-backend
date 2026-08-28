#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import netCDF4
import numpy as np
from matplotlib.colors import BoundaryNorm, ListedColormap


COLORS = [
    "#04e9e7", "#019ff4", "#0300f4", "#02fd02", "#01c501", "#008e00",
    "#fdf802", "#e5bc00", "#fd9500", "#fd0000", "#d40000", "#bc0000",
    "#f800fd", "#9854c6", "#ffffff",
]
BOUNDS = np.arange(5, 81, 5)


def valid_time(path: Path) -> str:
    match = re.search(r"wrfout_d01_(\d{4}-\d{2}-\d{2})_(\d{2})[-:](\d{2})[-:](\d{2})", path.name)
    if not match:
        return path.name
    return f"{match.group(1)}T{match.group(2)}:{match.group(3)}:{match.group(4)}Z"


def render(path: Path, target: Path) -> dict[str, object]:
    with netCDF4.Dataset(path) as dataset:
        if "REFL_10CM" not in dataset.variables:
            raise RuntimeError(f"REFL_10CM ausente em {path.name}")
        reflectivity = np.asarray(dataset.variables["REFL_10CM"][0])
        if reflectivity.ndim == 3:
            reflectivity = np.nanmax(reflectivity, axis=0)
        lats = np.asarray(dataset.variables["XLAT"][0])
        lons = np.asarray(dataset.variables["XLONG"][0])

    cmap = ListedColormap(COLORS)
    cmap.set_under((0, 0, 0, 0))
    norm = BoundaryNorm(BOUNDS, cmap.N)
    height, width = reflectivity.shape
    figure = plt.figure(figsize=(width / 100, height / 100), dpi=200, frameon=False)
    axis = figure.add_axes((0, 0, 1, 1))
    axis.set_axis_off()
    axis.pcolormesh(
        lons,
        lats,
        reflectivity,
        cmap=cmap,
        norm=norm,
        shading="nearest",
        antialiased=False,
        rasterized=True,
    )
    figure.savefig(target, transparent=True, dpi=200, pad_inches=0)
    plt.close(figure)
    return {
        "file": target.name,
        "validTime": valid_time(path),
        "bounds": {
            "south": float(np.nanmin(lats)), "west": float(np.nanmin(lons)),
            "north": float(np.nanmax(lats)), "east": float(np.nanmax(lons)),
        },
        "nativeGrid": {"width": int(width), "height": int(height)},
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--model", required=True)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    frames = []
    files = sorted(args.input.glob("wrfout_d01_*"))
    if not files:
        raise FileNotFoundError(f"Nenhum wrfout encontrado em {args.input}")
    for index, source in enumerate(files):
        target = args.output / f"reflectivity_{index:03d}.png"
        frames.append(render(source, target))

    manifest = {
        "schemaVersion": 1,
        "model": args.model,
        "runId": args.run_id,
        "generatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "frameCount": len(frames),
        "frames": frames,
    }
    (args.output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
