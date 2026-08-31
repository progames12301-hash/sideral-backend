#!/usr/bin/env python3
"""Build a small, public cache of polar-looking REDEMET radar PNGs.

This job only downloads the official image URLs returned by Sideral's API and
stores rendered images on the separate ``radar-pixels`` branch.  It never
touches WRF/model files and never creates meteorological values.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import os
import re
import shutil
from pathlib import Path
from typing import Any

import numpy as np
import requests
from PIL import Image
from io import BytesIO


SCHEMA = "sideral-radar-pixel-cache-v1"
PRODUCT = "03km"
MAX_FRAMES = 6
TTL_SECONDS = 900
REFLECTIVITY = np.array(
    [
        (4, 233, 231), (1, 159, 244), (3, 0, 244), (2, 253, 2),
        (1, 197, 1), (0, 142, 0), (254, 254, 0), (253, 149, 0),
        (253, 0, 0), (212, 0, 0), (188, 0, 0), (248, 0, 253),
        (152, 84, 198), (255, 255, 255),
    ], dtype=np.int16,
)
REFLECTIVITY_WEIGHTS = np.array((2, 4, 1), dtype=np.int32)


def parse_time(value: Any) -> dt.datetime | None:
    if not value:
        return None
    text = str(value).strip().replace(" ", "T")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def mercator_y(latitude: float) -> float:
    clipped = max(-85.0511, min(85.0511, latitude))
    sine = math.sin(math.radians(clipped))
    return 0.5 - math.log((1 + sine) / (1 - sine)) / (4 * math.pi)


def numeric(item: dict[str, Any], key: str) -> float | None:
    try:
        value = float(item.get(key))
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def item_bounds(item: dict[str, Any]) -> tuple[float, float, float, float] | None:
    values = tuple(numeric(item, key) for key in ("lon_min", "lat_min", "lon_max", "lat_max"))
    if any(value is None for value in values):
        return None
    west, south, east, north = values
    if east <= west or north <= south:
        return None
    return west, south, east, north


def render_polar(source: Image.Image, item: dict[str, Any], profile: str = "super") -> Image.Image:
    rgba = np.asarray(source.convert("RGBA"), dtype=np.uint8)
    height, width = rgba.shape[:2]
    bounds = item_bounds(item)
    longitude = numeric(item, "lon_center")
    latitude = numeric(item, "lat_center")
    if not bounds or longitude is None or latitude is None:
        return source.convert("RGBA")
    west, south, east, north = bounds
    north_y, south_y = mercator_y(north), mercator_y(south)
    cx = (longitude - west) / (east - west) * max(1, width - 1)
    cy = (mercator_y(latitude) - north_y) / (south_y - north_y) * max(1, height - 1)

    # A gate is selected by azimuth and range, then sampled with nearest neighbor.
    azimuth_resolution = math.radians(0.18 if profile == "super" else 0.30)
    raster_scale = max(0.75, min(3.0, max(width, height) / 400.0))
    range_resolution = (1.8 if profile == "super" else 2.6) * raster_scale
    yy, xx = np.indices((height, width), dtype=np.float32)
    dx, dy = xx - cx, yy - cy
    radius = np.hypot(dx, dy)
    azimuth_index = np.floor((np.arctan2(dy, dx) + math.pi) / azimuth_resolution)
    theta_center = (azimuth_index + 0.5) * azimuth_resolution - math.pi
    range_index = np.floor(radius / range_resolution)
    range_center = (range_index + 0.5) * range_resolution
    sample_x = np.rint(cx + np.cos(theta_center) * range_center).astype(np.int32)
    sample_y = np.rint(cy + np.sin(theta_center) * range_center).astype(np.int32)
    valid = (sample_x >= 0) & (sample_x < width) & (sample_y >= 0) & (sample_y < height)
    sample_x = np.clip(sample_x, 0, width - 1)
    sample_y = np.clip(sample_y, 0, height - 1)
    sampled = rgba[sample_y, sample_x]

    rgb = sampled[..., :3].astype(np.int16)
    maximum = rgb.max(axis=2)
    minimum = rgb.min(axis=2)
    saturation = maximum - minimum
    distances = ((rgb[..., None, :] - REFLECTIVITY[None, None, :, :]) ** 2 * REFLECTIVITY_WEIGHTS).sum(axis=3)
    echo = distances.min(axis=2) < 42000
    echo &= sampled[..., 3] >= 18
    echo &= ~((maximum < 18) | ((saturation < 12) & (maximum < 235)))
    echo &= valid

    output = np.zeros_like(rgba)
    output[echo] = sampled[echo]
    # Keep the short downward gate drop used by the client renderer near the antenna.
    near = echo & (radius < 90)
    for step in (1, 2):
        shifted = np.zeros_like(output)
        if step < height:
            shifted[step:] = output[:-step]
            shifted_alpha = (shifted[..., 3].astype(np.uint16) // 2).astype(np.uint8)
            vacant = near & (output[..., 3] == 0) & (shifted[..., 3] > 0)
            output[vacant, :3] = shifted[vacant, :3]
            output[vacant, 3] = shifted_alpha[vacant]
    return Image.fromarray(output, mode="RGBA")


def safe_name(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "-", value).strip("-")
    return cleaned.lower() or "radar"


def timestamp_for(item: dict[str, Any], path: str) -> str:
    parsed = parse_time(item.get("data"))
    if parsed:
        return parsed.strftime("%Y%m%dT%H%M%SZ")
    match = re.search(r"(\d{4})/(\d{2})/(\d{2}).*?(\d{4})-(\d{2})-(\d{2})--(\d{2}):(\d{2})", path)
    if match:
        return "{}{}{}T{}{}{}Z".format(*match.groups())
    return "unknown"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="radar-cache")
    parser.add_argument("--api", default=os.getenv("SIDERAL_API_BASE", "https://sideral-backend.onrender.com"))
    args = parser.parse_args()
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.headers.update({"User-Agent": "Sideral-radar-cache/1.0"})
    endpoint = args.api.rstrip("/") + "/api/redemet/radar"
    try:
        response = session.get(endpoint, params={"product": PRODUCT, "anima": MAX_FRAMES}, timeout=35)
        response.raise_for_status()
        payload = response.json()
        raw_frames = payload.get("data", {}).get("radar", [])
        if not isinstance(raw_frames, list):
            raise RuntimeError("API returned no radar frames")
    except Exception as error:
        print(f"Radar API unavailable: {error}")
        return 2

    generated = dt.datetime.now(dt.timezone.utc)
    manifest_frames: list[dict[str, Any]] = []
    processed_paths: set[str] = set()
    success_count = 0
    for raw_frame in raw_frames[:MAX_FRAMES]:
        if not isinstance(raw_frame, list):
            continue
        frame_items: list[dict[str, Any]] = []
        frame_dates = [parse_time(item.get("data")) for item in raw_frame if isinstance(item, dict)]
        frame_dates = [value for value in frame_dates if value]
        for item in raw_frame:
            if not isinstance(item, dict):
                continue
            source_path = str(item.get("path") or "")
            localidade = str(item.get("localidade") or "").strip().lower()
            if not source_path.startswith(("http://", "https://")) or not localidade or source_path in processed_paths:
                continue
            bounds = item_bounds(item)
            longitude, latitude = numeric(item, "lon_center"), numeric(item, "lat_center")
            if not bounds or longitude is None or latitude is None:
                continue
            try:
                image_response = session.get(source_path, timeout=35)
                image_response.raise_for_status()
                with Image.open(BytesIO(image_response.content)) as original:
                    processed = render_polar(original, item, "super")
                filename = f"{timestamp_for(item, source_path)}-{hashlib.sha1(source_path.encode()).hexdigest()[:10]}.png"
                relative = Path(PRODUCT) / safe_name(localidade) / filename
                target = output_dir / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                processed.save(target, format="PNG", optimize=True, compress_level=6)
                processed.close()
            except Exception as error:
                print(f"Skipping {localidade}: {error}")
                continue
            processed_paths.add(source_path)
            success_count += 1
            frame_items.append({
                "localidade": localidade,
                "date": item.get("data"),
                "sourcePath": source_path,
                "key": f"redemet-{PRODUCT}-{localidade}-{filename}",
                "url": f"https://raw.githubusercontent.com/progames12301-hash/sideral-backend/radar-pixels/radar-cache/{relative.as_posix()}",
                "bounds": list(bounds),
                "radar": {"longitude": longitude, "latitude": latitude},
            })
        if frame_items:
            manifest_frames.append({
                "date": max(frame_dates).isoformat().replace("+00:00", "Z") if frame_dates else None,
                "items": frame_items,
            })

    if not success_count:
        print("No radar image could be cached")
        return 3
    manifest = {
        "schema": SCHEMA,
        "generated_at": generated.isoformat().replace("+00:00", "Z"),
        "product": PRODUCT,
        "ttl_seconds": TTL_SECONDS,
        "frames": manifest_frames,
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Cached {success_count} official REDEMET images in {output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

