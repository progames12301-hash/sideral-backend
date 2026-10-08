#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import os
import re
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import numpy as np
import requests
from PIL import Image, ImageDraw

SCHEMA = "sideral-redemet-radar-scope-v2"
DEFAULT_API = "https://sideral-backend.onrender.com"
OFFICIAL_HOST = "estatico-redemet.decea.mil.br"
PRODUCTS = ("03km", "05km", "07km", "10km", "maxcappi")
PALETTE = np.array([
    (94,173,206),
    (102,196,220),
    (96,210,195),
    (71,214,135),
    (46,219,82),
    (22,218,22),
    (24,175,15),
    (26,141,12),
    (32,115,9),
    (100,143,6),
    (171,179,4),
    (229,205,1),
], dtype=np.int16)
WEIGHTS = np.array((2,4,1), dtype=np.int32)


def number(item: dict[str, Any], key: str) -> float | None:
    try:
        value = float(item.get(key))
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def when(value: Any) -> dt.datetime | None:
    if not value:
        return None
    text = str(value).strip().replace(" ", "T")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        out = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    return (out.replace(tzinfo=dt.timezone.utc) if out.tzinfo is None else out).astimezone(dt.timezone.utc)


def item_bounds(item: dict[str, Any]) -> tuple[float, float, float, float] | None:
    vals = tuple(number(item, k) for k in ("lon_min","lat_min","lon_max","lat_max"))
    if any(v is None for v in vals):
        return None
    west, south, east, north = vals
    if east <= west or north <= south:
        return None
    return west, south, east, north


def mercator_y(lat: float) -> float:
    lat = max(-85.0511, min(85.0511, lat))
    s = math.sin(math.radians(lat))
    return 0.5 - math.log((1+s)/(1-s))/(4*math.pi)


def center_pixels(item: dict[str, Any], width: int, height: int) -> tuple[float,float] | None:
    b = item_bounds(item)
    lon, lat = number(item, "lon_center"), number(item, "lat_center")
    if not b or lon is None or lat is None:
        return None
    west,south,east,north = b
    ny, sy = mercator_y(north), mercator_y(south)
    return (
        (lon-west)/(east-west)*max(1,width-1),
        (mercator_y(lat)-ny)/(sy-ny)*max(1,height-1),
    )


def echo_mask(rgba: np.ndarray) -> np.ndarray:
    rgb = rgba[..., :3].astype(np.int16)
    hi = rgb.max(axis=2)
    lo = rgb.min(axis=2)
    sat = hi-lo
    d = ((rgb[...,None,:]-PALETTE[None,None,:,:])**2*WEIGHTS).sum(axis=3)
    mask = d.min(axis=2) < 42000
    mask &= rgba[...,3] >= 18
    mask &= ~((hi < 18) | ((sat < 12) & (hi < 235)))
    return mask


def sample_source(rgba: np.ndarray, mask: np.ndarray, cx: float, cy: float, radius: float, angle: float):
    ca, sa = math.cos(angle), math.sin(angle)
    hits = []
    for dr, tangent in ((0,0),(1,0),(-1,0),(0,1),(0,-1)):
        rr = radius + dr
        x = int(round(cx + ca*rr - sa*tangent))
        y = int(round(cy + sa*rr + ca*tangent))
        if 0 <= x < rgba.shape[1] and 0 <= y < rgba.shape[0] and mask[y,x]:
            hits.append(tuple(int(v) for v in rgba[y,x]))
    if not hits:
        return None
    counts = {}
    for pixel in hits:
        counts[pixel] = counts.get(pixel,0)+1
    pixel = max(counts.items(), key=lambda kv: (kv[1], kv[0]))[0]
    rgb = np.array(pixel[:3], dtype=np.int16)
    distances = ((PALETTE - rgb) ** 2).sum(axis=1)
    mapped = PALETTE[int(np.argmin(distances))]
    return (int(mapped[0]), int(mapped[1]), int(mapped[2]), int(pixel[3]))


def render_scope(source: Image.Image, item: dict[str, Any], azimuth_deg: float, gate_px: float, gap_px: float) -> Image.Image:
    rgba = np.asarray(source.convert("RGBA"), dtype=np.uint8)
    h,w = rgba.shape[:2]
    center = center_pixels(item,w,h)
    out = Image.new("RGBA",(w,h),(0,0,0,0))
    if not center:
        return out
    cx,cy = center
    mask = echo_mask(rgba)
    max_r = math.hypot(max(cx,w-1-cx), max(cy,h-1-cy))
    az = math.radians(max(0.08,min(3.0,azimuth_deg)))
    half = az*0.46
    gate = max(1.0,gate_px)
    gap = max(0.0,min(gate*0.6,gap_px))
    draw = ImageDraw.Draw(out,"RGBA")
    for gi in range(int(max_r/gate)+1):
        inner = gi*gate + gap/2
        outer = (gi+1)*gate - gap/2
        if outer <= inner:
            continue
        mid = (inner+outer)/2
        for ai in range(int(math.ceil(2*math.pi/az))):
            angle = -math.pi + (ai+0.5)*az
            color = sample_source(rgba,mask,cx,cy,mid,angle)
            if color is None:
                continue
            a1,a2 = angle-half,angle+half
            poly = [
                (cx+math.cos(a1)*inner,cy+math.sin(a1)*inner),
                (cx+math.cos(a2)*inner,cy+math.sin(a2)*inner),
                (cx+math.cos(a2)*outer,cy+math.sin(a2)*outer),
                (cx+math.cos(a1)*outer,cy+math.sin(a1)*outer),
            ]
            draw.polygon(poly,fill=(color[0],color[1],color[2],min(255,max(20,int(color[3]*0.94)))))
    return out


def safe(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9._-]+","-",value).strip("-").lower()
    return value or "radar"


def source_timestamp(item: dict[str, Any], url: str) -> str:
    t = when(item.get("data"))
    if t:
        return t.strftime("%Y%m%dT%H%M%SZ")
    return hashlib.sha1(url.encode()).hexdigest()[:16]


def fetch_frames(session: requests.Session, api: str, product: str, count: int):
    url = api.rstrip("/") + "/api/redemet/radar"
    params = {"product": product, "anima": max(1, min(15, count))}
    last_error: Exception | None = None

    # O Render pode levar alguns segundos para acordar. Permita uma leitura
    # longa, mas faça no máximo uma segunda tentativa em caso de timeout/erro
    # transitório, evitando ficar preso indefinidamente.
    for attempt in range(2):
        try:
            r = session.get(url, params=params, timeout=(15, 120))
            r.raise_for_status()
            payload = r.json()
            radar = payload.get("data", {}).get("radar", [])
            if not isinstance(radar, list) or not radar:
                raise RuntimeError("Render não retornou quadros REDEMET")
            return radar[:count]
        except (requests.RequestException, ValueError, RuntimeError) as exc:
            last_error = exc
            if attempt == 0:
                import time
                time.sleep(4)

    raise RuntimeError(f"Falha ao consultar Render/REDEMET: {last_error}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api",default=os.getenv("SIDERAL_RENDER_API",DEFAULT_API))
    ap.add_argument("--product",choices=PRODUCTS,default="03km")
    ap.add_argument("--frames",type=int,default=1)
    ap.add_argument("--output",default="redemet-radar-scope")
    ap.add_argument("--azimuth-step",type=float,default=0.35)
    ap.add_argument("--gate-pixels",type=float,default=3.0)
    ap.add_argument("--gate-gap",type=float,default=0.0)
    ap.add_argument("--target-width",type=int,default=3840)
    ap.add_argument("--target-height",type=int,default=2160)
    args = ap.parse_args()

    out = Path(args.output)
    out.mkdir(parents=True,exist_ok=True)
    session = requests.Session()
    session.headers.update({"User-Agent":"Sideral-REDEMET-Radar-Scope/1.0","Accept":"application/json"})
    frames = fetch_frames(session,args.api,args.product,args.frames)

    seen = set()
    manifest_frames = []
    generated = 0
    now = dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00","Z")

    for frame in frames:
        if not isinstance(frame,list):
            continue
        items = []
        dates = []
        for item in frame:
            if not isinstance(item,dict):
                continue
            src = str(item.get("path") or "").strip()
            loc = str(item.get("localidade") or "").strip()
            b = item_bounds(item)
            if not src or src in seen or not loc or not b:
                continue
            parsed = urlparse(src)
            if parsed.scheme != "https" or parsed.hostname != OFFICIAL_HOST or not parsed.path.startswith("/radar/"):
                raise RuntimeError("Render retornou URL REDEMET fora do domínio oficial")
            r = session.get(src,timeout=(15,90))
            r.raise_for_status()
            with Image.open(BytesIO(r.content)) as original:
                scope = render_scope(original,item,args.azimuth_step,args.gate_pixels,args.gate_gap)
                name = source_timestamp(item,src) + "-scope.png"
                rel = Path(args.product)/safe(loc)/name
                target = out/rel
                target.parent.mkdir(parents=True,exist_ok=True)

                # Saída Ultra HD (UHD): 3840x2160 por padrão.
                # Mantém a proporção original e centraliza o radar em uma tela UHD.
                target_width = max(1, int(args.target_width))
                target_height = max(1, int(args.target_height))
                scale = min(target_width / scope.width, target_height / scope.height)
                fit_w = max(1, round(scope.width * scale))
                fit_h = max(1, round(scope.height * scale))
                scope_fit = scope.resize((fit_w, fit_h), Image.Resampling.LANCZOS)
                uhd = Image.new("RGBA", (target_width, target_height), (0, 0, 0, 0))
                x = (target_width - fit_w) // 2
                y = (target_height - fit_h) // 2
                uhd.alpha_composite(scope_fit, (x, y))
                scope_fit.close()
                scope.close()
                scope = uhd
                scope.save(target,"PNG",optimize=True,compress_level=6)
                scope.close()
                iw,ih = original.size
                ow,oh = target_width,target_height
            seen.add(src)
            generated += 1
            t = when(item.get("data"))
            if t:
                dates.append(t)
            items.append({
                "localidade":loc,
                "date":item.get("data"),
                "sourcePath":src,
                "scopeUrl":f"https://raw.githubusercontent.com/progames12301-hash/sideral-backend/redemet-radar-scope/{rel.as_posix()}",
                "bounds":list(b),
                "radar":{"longitude":number(item,"lon_center"),"latitude":number(item,"lat_center")},
                "image":{"width":iw,"height":ih,"outputWidth":ow,"outputHeight":oh},
                "render":{"azimuthStepDegrees":args.azimuth_step,"gatePixels":args.gate_pixels,"gateGapPixels":args.gate_gap,"outputResolution":"Ultra HD","outputWidth":ow,"outputHeight":oh,"mode":"source-color-sampling"}
            })
        if items:
            manifest_frames.append({"date":max(dates).isoformat().replace("+00:00","Z") if dates else None,"items":items})

    if generated == 0:
        raise RuntimeError("Nenhuma imagem REDEMET foi convertida em gates")
    manifest = {
        "schema":SCHEMA,
        "generated_at":now,
        "provider":"REDEMET / DECEA",
        "source":"Sideral Render /api/redemet/radar",
        "product":args.product,
        "frames":manifest_frames,
        "notes":[
            "Overlay transparente.",
            "Os gates usam somente cores presentes no PNG oficial.",
            "Nenhum dBZ, velocidade ou célula é inventado."
        ]
    }
    (out/"manifest.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"generated":generated,"frames":len(manifest_frames),"product":args.product}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
