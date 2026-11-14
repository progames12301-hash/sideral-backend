"""Build the national Sideral Skew-T lookup grid.

The grid is tied to the ECMWF IFS 0.25-degree dissemination grid. It contains
all 0.25-degree cells whose centers fall inside Brazil. Full SHARPpy images
are not pre-rendered for every cell: the existing request workflow renders a
cell on demand and caches it under data/skewt/points/.
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


def _repair_geometry(geometry):
    """Repair invalid municipal polygons before the national union.

    The geodata-br municipal dataset can contain self-intersections, dangling
    rings, or other topology defects. GEOS can abort unary_union on those
    features, so repair each feature first and discard only empty results.
    ``buffer(0)`` is deliberately used as a compatibility fallback because it
    works across the Shapely versions used by the GitHub runner.
    """
    if geometry is None or geometry.is_empty:
        return None
    if geometry.is_valid:
        return geometry
    try:
        repaired = geometry.buffer(0)
    except Exception:
        return None
    if repaired.is_empty:
        return None
    return repaired


def download_geometry(cache: Path):
    cache.parent.mkdir(parents=True, exist_ok=True)
    if not cache.exists():
        with urllib.request.urlopen(GEOJSON_URL, timeout=60) as r:
            cache.write_bytes(r.read())
    data = json.loads(cache.read_text(encoding="utf-8"))

    geoms = []
    for feature in data["features"]:
        geometry = feature.get("geometry")
        if not geometry:
            continue
        repaired = _repair_geometry(shape(geometry))
        if repaired is not None:
            geoms.append(repaired)

    if not geoms:
        raise RuntimeError("No valid Brazil municipality geometries were loaded")

    # Use a second repair pass after normalization. This prevents one repaired
    # multipart feature from reintroducing a GEOS topology error during union.
    brazil = unary_union(geoms)
    if not brazil.is_valid:
        brazil = brazil.buffer(0)
    if brazil.is_empty or not brazil.is_valid:
        raise RuntimeError("Brazil boundary remains invalid after geometry repair")
    return brazil


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
