"""Build the national Sideral Skew-T lookup grid.

The grid is tied to the ECMWF IFS 0.25-degree dissemination grid. It contains
all 0.25-degree cells whose centers fall inside Brazil. Full SHARPpy images are
not pre-rendered for every cell: the existing request workflow renders a cell
on demand and caches it under data/skewt/points/.
"""
from __future__ import annotations

import argparse
import json
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from shapely.geometry import Point, shape
from shapely.ops import unary_union

GEOJSON_URL = "https://raw.githubusercontent.com/tbrugz/geodata-br/master/geojson/geojs-100-mun.json"


def download_geometry(cache: Path):
    cache.parent.mkdir(parents=True, exist_ok=True)
    if not cache.exists():
        with urllib.request.urlopen(GEOJSON_URL, timeout=60) as r:
            cache.write_bytes(r.read())
    data = json.loads(cache.read_text(encoding="utf-8"))
    geoms = [shape(f["geometry"]) for f in data["features"] if f.get("geometry")]
    return unary_union(geoms)


def snap_values(start: float, end: float):
    value = round(start * 4) / 4
    while value <= end + 1e-9:
        yield round(value, 2)
        value += 0.25


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/skewt/grid/brazil-0p25.json")
    ap.add_argument("--cache", default="/tmp/brazil.geojson")
    args = ap.parse_args()

    brazil = download_geometry(Path(args.cache))
    minx, miny, maxx, maxy = brazil.bounds
    points = []
    for lat in snap_values(miny, maxy):
        for lon in snap_values(minx, maxx):
            if brazil.contains(Point(lon, lat)) or brazil.touches(Point(lon, lat)):
                points.append({
                    "latitude": lat,
                    "longitude": lon,
                    "key": f"{lat:.2f}_{lon:.2f}".replace("-", "m").replace(".", "p"),
                })

    payload = {
        "schema": "sideral-skewt-grid-v1",
        "model": "ECMWF IFS",
        "resolution": "0.25°",
        "country": "Brasil",
        "point_count": len(points),
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source_boundary": "IBGE via geodata-br",
        "points": points,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"Brazil ECMWF 0.25 grid: {len(points)} points")


if __name__ == "__main__":
    main()
