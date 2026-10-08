#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import json
from io import BytesIO
from pathlib import Path

import requests
import urllib3
from PIL import Image, ImageDraw
import math
import numpy as np

SIFAP = "https://sifap.defesacivil.sc.gov.br/radarsc/rest/radar"
SCHEMA = "sideral-sdcsc-chapeco-ppi-z-v6"

# O certificado apresentado pelo SIFAP/Defesa Civil SC está com a cadeia
# incompleta para o runner do GitHub Actions. A origem continua sendo
# exclusivamente o SIFAP oficial; aqui apenas desabilitamos a validação TLS
# para conseguir acessar o endpoint que já é usado pelo backend Sideral.
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def get(session, url, params, accept, timeout=(15, 90)):
    r = session.get(
        url,
        params=params,
        timeout=timeout,
        verify=False,
        headers={
            "User-Agent": "Sideral-SDC-SC-Chapeco-PPI-Z/4.0",
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

    if isinstance(names, dict):
        for key in ("images", "files", "data", "result"):
            value = names.get(key)
            if isinstance(value, list):
                names = value
                break

    safe = sorted({
        str(name).strip()
        for name in (names if isinstance(names, list) else [])
        if isinstance(name, (str, int))
        and len(str(name).strip()) >= 18
        and str(name).strip().lower().endswith(".png")
        and str(name).strip()[:14].isdigit()
    })

    if not safe:
        raise RuntimeError("O SIFAP não retornou imagens PPI Z para CHP/Chapecó.")

    return safe[-1]


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

def echo_mask(rgba: np.ndarray) -> np.ndarray:
    rgb = rgba[..., :3].astype(np.int16)
    hi = rgb.max(axis=2)
    lo = rgb.min(axis=2)
    sat = hi - lo
    distances = ((rgb[..., None, :] - PALETTE[None, None, :, :]) ** 2).sum(axis=3)
    mask = distances.min(axis=2) < 18000
    mask &= rgba[..., 3] >= 20
    mask &= ~((hi < 18) | ((sat < 18) & (hi < 235)))
    return mask

def sample_color(rgba: np.ndarray, mask: np.ndarray, cx: float, cy: float, radius: float, angle: float):
    ca, sa = math.cos(angle), math.sin(angle)
    hits = []
    for dr, tangent in ((0, 0), (1, 0), (-1, 0), (0, 1), (0, -1)):
        rr = radius + dr
        x = int(round(cx + ca * rr - sa * tangent))
        y = int(round(cy + sa * rr + ca * tangent))
        if 0 <= x < rgba.shape[1] and 0 <= y < rgba.shape[0] and mask[y, x]:
            hits.append(tuple(int(v) for v in rgba[y, x]))
    if not hits:
        return None
    counts = {}
    for pixel in hits:
        counts[pixel] = counts.get(pixel, 0) + 1
    pixel = max(counts.items(), key=lambda kv: (kv[1], kv[0]))[0]
    # Normaliza explicitamente para RGB antes de comparar com a paleta de 3 canais.
    pixel_rgb = tuple(int(v) for v in pixel[:3])
    pixel_alpha = int(pixel[3]) if len(pixel) > 3 else 255
    rgb = np.asarray(pixel_rgb, dtype=np.int16)
    distances = np.sum((PALETTE - rgb[None, :]) ** 2, axis=1)
    mapped = PALETTE[int(np.argmin(distances))]
    return (int(mapped[0]), int(mapped[1]), int(mapped[2]), pixel_alpha)

def make_radar_scope_superres(
    source: Image.Image,
    width: int,
    height: int,
    azimuth_step: float = 0.35,
    gate_pixels: float = 3.0,
    gate_gap: float = 0.0,
) -> Image.Image:
    """Reconstrói o PPI em gates polares, no estilo Super-Res do Radar Scope REDEMET."""
    rgba = np.asarray(source.convert("RGBA"), dtype=np.uint8)
    h, w = rgba.shape[:2]
    cx, cy = (w - 1) / 2.0, (h - 1) / 2.0
    mask = echo_mask(rgba)

    native = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(native, "RGBA")

    max_r = math.hypot(max(cx, w - 1 - cx), max(cy, h - 1 - cy))
    az = math.radians(max(0.08, min(3.0, azimuth_step)))
    half = az * 0.46
    gate = max(1.0, gate_pixels)
    gap = max(0.0, min(gate * 0.6, gate_gap))

    for gi in range(int(max_r / gate) + 1):
        inner = gi * gate + gap / 2.0
        outer = (gi + 1) * gate - gap / 2.0
        if outer <= inner:
            continue

        mid = (inner + outer) / 2.0
        for ai in range(int(math.ceil(2 * math.pi / az))):
            angle = -math.pi + (ai + 0.5) * az
            color = sample_color(rgba, mask, cx, cy, mid, angle)
            if color is None:
                continue

            a1, a2 = angle - half, angle + half
            poly = [
                (cx + math.cos(a1) * inner, cy + math.sin(a1) * inner),
                (cx + math.cos(a2) * inner, cy + math.sin(a2) * inner),
                (cx + math.cos(a2) * outer, cy + math.sin(a2) * outer),
                (cx + math.cos(a1) * outer, cy + math.sin(a1) * outer),
            ]
            draw.polygon(
                poly,
                fill=(
                    int(color[0]),
                    int(color[1]),
                    int(color[2]),
                    min(255, max(35, int(color[3] * 0.96))),
                ),
            )

    scale = min(width / w, height / h)
    fit_w = max(1, round(w * scale))
    fit_h = max(1, round(h * scale))
    fit = native.resize((fit_w, fit_h), Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    canvas.alpha_composite(fit, ((width - fit_w) // 2, (height - fit_h) // 2))
    fit.close()
    native.close()
    return canvas


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
    ap = argparse.ArgumentParser(
        description="PPI Z real do radar de Chapecó via SIFAP/Defesa Civil SC."
    )
    ap.add_argument("--width", type=int, default=3840)
    ap.add_argument("--height", type=int, default=2160)
    ap.add_argument("--output", default="sdc-sc-chapeco-ppi-z")
    ap.add_argument("--azimuth-step", type=float, default=0.35)
    ap.add_argument("--gate-pixels", type=float, default=3.0)
    ap.add_argument("--gate-gap", type=float, default=0.0)
    args = ap.parse_args()

    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)

    session = requests.Session()

    # Única origem: SIFAP oficial da Defesa Civil SC.
    # Não existe fallback para Render ou qualquer outro proxy.
    filename = get_latest_direct(session)
    body, content_type = get_image_direct(session, filename)

    source_name = "chapeco-ppi-z-source.png"
    uhd_name = "chapeco-ppi-z-uhd.png"
    superres_name = "chapeco-ppi-z-superres.png"

    with Image.open(BytesIO(body)) as source:
        source = source.convert("RGBA")
        source.save(out / source_name, "PNG", optimize=True, compress_level=6)

        uhd = make_uhd(source, max(256, args.width), max(256, args.height))
        uhd.save(out / uhd_name, "PNG", optimize=True, compress_level=6)

        print(
            f"Gerando Super-Res Radar Scope "
            f"(azimute={args.azimuth_step}°, gate={args.gate_pixels}px, gap={args.gate_gap}px) "
            f"a partir do PPI real {source.size}..."
        )
        sr = make_radar_scope_superres(
            source,
            max(256, args.width),
            max(256, args.height),
            args.azimuth_step,
            args.gate_pixels,
            args.gate_gap,
        )
        sr.save(out / superres_name, "PNG", optimize=True, compress_level=6)

        source_size = source.size
        uhd_size = uhd.size
        sr_size = sr.size
        uhd.close()
        sr.close()

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
            "mode": "sifap-direct",
            "upstream": SIFAP,
            "listEndpoint": f"{SIFAP}/getUltimasImagens",
            "imageEndpoint": f"{SIFAP}/getImagem",
            "productCode": "0",
            "radarCode": "CHP",
            "file": filename,
            "tlsVerification": False,
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
        "superResolution": {
            "file": superres_name,
            "width": sr_size[0],
            "height": sr_size[1],
            "format": "PNG",
            "model": "Radar Scope polar gates",
            "method": "reconstrução polar com gates no estilo REDEMET",
            "inventedDbz": False,
            "inventedCells": False,
            "note": (
                "A Super-Res reconstrói o desenho dos ecos em gates polares usando somente as cores presentes no PPI oficial; "
                "nenhum dBZ, célula ou precipitação é inventado."
            ),
        },
        "processing": {
            "mode": "official-radar-image-plus-radar-scope-superres",
            "inventedDbz": False,
            "inventedCells": False,
            "note": (
                "A fonte é o PPI de refletividade do SIFAP/Defesa Civil SC. "
                "A Super-Res usa gates polares no estilo Radar Scope da REDEMET."
            ),
        },
        "generatedAt": dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z"),
    }

    (out / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print(json.dumps(
        {
            "mode": "sifap-direct",
            "radar": "CHP",
            "product": "PPI Z",
            "file": filename,
            "source": source_name,
            "uhd": uhd_name,
            "superres": superres_name,
            "resolution": f"{uhd_size[0]}x{uhd_size[1]}",
            "superresResolution": f"{sr_size[0]}x{sr_size[1]}",
            "azimuthStepDegrees": args.azimuth_step,
            "gatePixels": args.gate_pixels,
            "gateGapPixels": args.gate_gap,
        },
        ensure_ascii=False,
    ))


if __name__ == "__main__":
    raise SystemExit(main())
