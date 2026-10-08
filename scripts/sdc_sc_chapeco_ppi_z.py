#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import json
from io import BytesIO
from pathlib import Path

import requests
from PIL import Image

SIFAP = "https://sifap.defesacivil.sc.gov.br/radarsc/rest/radar"
RENDER = "https://sideral-backend.onrender.com"
SCHEMA = "sideral-sdcsc-chapeco-ppi-z-v3"

def get(session, url, params, accept, timeout=(15, 90)):
    r = session.get(
        url,
        params=params,
        timeout=timeout,
        headers={
            "User-Agent": "Sideral-SDC-SC-Chapeco-PPI-Z/3.0",
            "Accept": accept,
        },
    )
    r.raise_for_status()
    return r

def get_latest_direct(session):
    r = get(
        session,
        f"{SIFAP}/getUltimasImagens",
        {"prod": "0", "radar": "CHP", "data": ""},
        "application/json,*/*",
        (15, 60),
    )
    names = r.json()
    safe = sorted({
        str(name).strip()
        for name in names
        if isinstance(name, (str, int))
        and len(str(name).strip()) >= 18
        and str(name).strip().lower().endswith(".png")
        and str(name).strip()[:14].isdigit()
    })
    if not safe:
        raise RuntimeError("O SIFAP não retornou imagens PPI Z para CHP/Chapecó.")
    return safe[-1], "sifap-direct"

def get_image_direct(session, filename):
    r = get(
        session,
        f"{SIFAP}/getImagem",
        {"prod": "0", "radar": "CHP", "file": filename},
        "image/png,image/jpeg,*/*",
        (15, 120),
    )
    ctype = (r.headers.get("Content-Type") or "").lower()
    if "image/png" not in ctype and "image/jpeg" not in ctype:
        raise RuntimeError(f"SIFAP retornou conteúdo que não é imagem: {ctype}")
    if len(r.content) < 1000:
        raise RuntimeError("SIFAP retornou uma imagem inválida ou vazia.")
    return r.content, ctype

def get_latest_render(session):
    r = get(
        session,
        f"{RENDER}/api/regional/radar",
        {"product": "reflectivity"},
        "application/json,*/*",
        (15, 90),
    )
    payload = r.json()
    radars = payload.get("radars") or []
    radar = next(
        (item for item in radars if str(item.get("id", "")).lower() == "sc-chapeco"),
        None,
    )
    if not radar:
        raise RuntimeError("Render não retornou sc-chapeco.")
    frames = radar.get("frames") or []
    if not frames:
        raise RuntimeError("Render não retornou quadros para Chapecó.")
    filename = str(frames[-1].get("key") or "").strip()
    if not filename:
        raise RuntimeError("O quadro de Chapecó não possui arquivo.")
    return filename, "sideral-render-proxy"

def get_image_render(session, filename):
    r = get(
        session,
        f"{RENDER}/api/regional/radar/image",
        {
            "provider": "sc",
            "radar": "sc-chapeco",
            "product": "reflectivity",
            "file": filename,
        },
        "image/png,image/jpeg,*/*",
        (15, 120),
    )
    ctype = (r.headers.get("Content-Type") or "").lower()
    if "image/png" not in ctype and "image/jpeg" not in ctype:
        raise RuntimeError(f"Render não retornou imagem: {ctype}")
    if len(r.content) < 1000:
        raise RuntimeError("Render retornou imagem inválida ou vazia.")
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

def main():
    ap = argparse.ArgumentParser(description="PPI Z real do radar de Chapecó via SIFAP/SDC SC.")
    ap.add_argument("--width", type=int, default=3840)
    ap.add_argument("--height", type=int, default=2160)
    ap.add_argument("--output", default="sdc-sc-chapeco-ppi-z")
    args = ap.parse_args()

    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)

    session = requests.Session()

    errors = []
    try:
        filename, source_mode = get_latest_direct(session)
        body, content_type = get_image_direct(session, filename)
    except Exception as exc:
        errors.append(f"SIFAP: {type(exc).__name__}: {exc}")
        filename, source_mode = get_latest_render(session)
        body, content_type = get_image_render(session, filename)

    source_name = "chapeco-ppi-z-source.png"
    uhd_name = "chapeco-ppi-z-uhd.png"

    with Image.open(BytesIO(body)) as source:
        source = source.convert("RGBA")
        source.save(out / source_name, "PNG", optimize=True, compress_level=6)
        uhd = make_uhd(source, max(256, args.width), max(256, args.height))
        uhd.save(out / uhd_name, "PNG", optimize=True, compress_level=6)
        source_size = source.size
        uhd_size = uhd.size
        uhd.close()

    manifest = {
        "schema": SCHEMA,
        "provider": "Defesa Civil de Santa Catarina / SIFAP",
        "radar": {
            "id": "sc-chapeco",
            "code": "CHP",
            "name": "Defesa Civil SC — Chapecó/SC",
            "latitude": -27.10,
            "longitude": -52.62,
            "rangeKm": 240,
        },
        "product": "PPI Z / Reflectivity",
        "source": {
            "mode": source_mode,
            "upstream": SIFAP,
            "listEndpoint": f"{SIFAP}/getUltimasImagens",
            "imageEndpoint": f"{SIFAP}/getImagem",
            "productCode": "0",
            "radarCode": "CHP",
            "file": filename,
        },
        "sourceImage": {
            "file": source_name,
            "width": source_size[0],
            "height": source_size[1],
            "contentType": content_type,
        },
        "output": {
            "file": uhd_name,
            "width": uhd_size[0],
            "height": uhd_size[1],
            "format": "PNG",
        },
        "processing": {
            "mode": "official-radar-image",
            "inventedDbz": False,
            "inventedCells": False,
            "note": "A imagem publicada é o próprio PPI de refletividade retornado pelo SIFAP/Defesa Civil SC; a versão UHD apenas redimensiona a imagem original.",
        },
        "fallbackErrors": errors,
        "generatedAt": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
    }

    (out / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(json.dumps({
        "mode": source_mode,
        "radar": "CHP",
        "product": "PPI Z",
        "file": filename,
        "source": source_name,
        "uhd": uhd_name,
        "resolution": f"{uhd_size[0]}x{uhd_size[1]}",
    }, ensure_ascii=False))

if __name__ == "__main__":
    raise SystemExit(main())
