#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import json
from io import BytesIO
from pathlib import Path

import requests
from PIL import Image

DEFAULT_API = "https://sideral-backend.onrender.com"
SCHEMA = "sideral-sdcsc-chapeco-ppi-z-v2"

def get_json(session, url, params=None):
    r = session.get(
        url,
        params=params or {},
        timeout=(15, 90),
        headers={
            "User-Agent": "Sideral-SDC-SC-Chapeco-PPI-Z/2.0",
            "Accept": "application/json",
        },
    )
    r.raise_for_status()
    return r.json()

def get_image(session, url, params):
    r = session.get(
        url,
        params=params,
        timeout=(15, 120),
        headers={
            "User-Agent": "Sideral-SDC-SC-Chapeco-PPI-Z/2.0",
            "Accept": "image/png,image/jpeg,*/*",
        },
    )
    r.raise_for_status()
    ctype = r.headers.get("Content-Type", "").lower()
    if "image/png" not in ctype and "image/jpeg" not in ctype:
        raise RuntimeError(f"O proxy do Sideral não retornou uma imagem: {ctype}")
    if len(r.content) < 1000:
        raise RuntimeError("A imagem do PPI retornada é pequena/inválida.")
    return r.content, ctype

def make_uhd(source: Image.Image, width: int, height: int) -> Image.Image:
    source = source.convert("RGBA")
    scale = min(width / source.width, height / source.height)
    fit_w = max(1, round(source.width * scale))
    fit_h = max(1, round(source.height * scale))
    fit = source.resize((fit_w, fit_h), Image.Resampling.LANCZOS)

    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    canvas.alpha_composite(fit, ((width - fit_w) // 2, (height - fit_h) // 2))
    fit.close()
    return canvas

def main() -> int:
    ap = argparse.ArgumentParser(
        description="Baixa o PPI Z real do radar de Chapecó via endpoint SIFAP já integrado ao Sideral."
    )
    ap.add_argument("--api", default=DEFAULT_API)
    ap.add_argument("--width", type=int, default=3840)
    ap.add_argument("--height", type=int, default=2160)
    ap.add_argument("--output", default="sdc-sc-chapeco-ppi-z")
    args = ap.parse_args()

    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)

    session = requests.Session()

    catalog_url = args.api.rstrip("/") + "/api/regional/radar"
    image_url = args.api.rstrip("/") + "/api/regional/radar/image"

    payload = get_json(session, catalog_url, {"product": "reflectivity"})
    radars = payload.get("radars") or []
    radar = next((r for r in radars if str(r.get("id", "")).lower() == "sc-chapeco"), None)

    if not radar:
        # Diagnostic: keep the exact catalog response for inspection.
        (out / "catalog.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        raise RuntimeError("O endpoint regional do Sideral não retornou o radar sc-chapeco.")

    frames = radar.get("frames") or []
    if not frames:
        raise RuntimeError("O radar de Chapecó foi encontrado, mas não possui quadros PPI.")

    frame = frames[-1]
    filename = str(frame.get("key") or "").strip()
    if not filename:
        raise RuntimeError("O quadro de Chapecó não possui nome de arquivo.")

    body, content_type = get_image(
        session,
        image_url,
        {
            "provider": "sc",
            "radar": "sc-chapeco",
            "product": "reflectivity",
            "file": filename,
        },
    )

    source_name = "chapeco-ppi-z-source.png"
    uhd_name = "chapeco-ppi-z-uhd.png"

    with Image.open(BytesIO(body)) as source:
        source = source.convert("RGBA")
        source.save(out / source_name, "PNG", optimize=True, compress_level=6)
        uhd = make_uhd(source, max(256, args.width), max(256, args.height))
        uhd.save(out / uhd_name, "PNG", optimize=True, compress_level=6)
        source_width, source_height = source.size
        uhd_width, uhd_height = uhd.size
        uhd.close()

    manifest = {
        "schema": SCHEMA,
        "provider": "Defesa Civil de Santa Catarina / SIFAP",
        "radar": {
            "id": radar.get("id"),
            "code": radar.get("code"),
            "name": radar.get("name"),
            "latitude": radar.get("latitude"),
            "longitude": radar.get("longitude"),
            "rangeKm": radar.get("rangeKm"),
        },
        "product": "PPI Z / Reflectivity",
        "source": {
            "sideralCatalog": catalog_url,
            "sideralImageProxy": image_url,
            "upstream": "https://sifap.defesacivil.sc.gov.br/radarsc/rest/radar",
            "productCode": radar.get("product", "0"),
            "file": filename,
        },
        "frame": frame,
        "sourceImage": {
            "width": source_width,
            "height": source_height,
            "contentType": content_type,
        },
        "output": {
            "file": uhd_name,
            "width": uhd_width,
            "height": uhd_height,
            "format": "PNG",
        },
        "processing": {
            "mode": "official-radar-image-proxy",
            "inventedDbz": False,
            "inventedCells": False,
            "note": "A saída é a própria imagem PPI de refletividade do radar, ampliada para UHD; nenhum campo meteorológico é sintetizado.",
        },
        "generatedAt": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
    }

    (out / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(json.dumps({
        "radar": radar.get("name"),
        "frame": frame,
        "source": source_name,
        "uhd": uhd_name,
        "resolution": f"{uhd_width}x{uhd_height}",
    }, ensure_ascii=False))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
