#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import json
from io import BytesIO
from pathlib import Path

import requests
import urllib3
from PIL import Image
import cv2
import numpy as np

SIFAP = "https://sifap.defesacivil.sc.gov.br/radarsc/rest/radar"
SCHEMA = "sideral-sdcsc-chapeco-ppi-z-v5"
EDSR_URL = "https://raw.githubusercontent.com/Saafke/EDSR_Tensorflow/master/models/EDSR_x4.pb"

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



def super_res_edsr(source: Image.Image, model_path: str, tile: int = 192, overlap: int = 16) -> Image.Image:
    """Aplica EDSR x4 em tiles para manter o consumo de RAM controlado."""
    rgba = np.array(source.convert("RGBA"))
    bgr = cv2.cvtColor(rgba[:, :, :3], cv2.COLOR_RGB2BGR)
    alpha = rgba[:, :, 3]

    sr = cv2.dnn_superres.DnnSuperResImpl_create()
    sr.readModel(model_path)
    sr.setModel("edsr", 4)

    h, w = bgr.shape[:2]
    scale = 4
    out_h, out_w = h * scale, w * scale
    acc = np.zeros((out_h, out_w, 3), dtype=np.float32)
    weights = np.zeros((out_h, out_w, 1), dtype=np.float32)
    step = max(32, tile - overlap * 2)

    for y0 in range(0, h, step):
        y1 = min(h, y0 + tile)
        y0e = max(0, y1 - tile)
        for x0 in range(0, w, step):
            x1 = min(w, x0 + tile)
            x0e = max(0, x1 - tile)

            patch = bgr[y0e:y1, x0e:x1]
            result = sr.upsample(patch).astype(np.float32)

            oh, ow = result.shape[:2]
            oy0, ox0 = y0e * scale, x0e * scale
            oy1, ox1 = oy0 + oh, ox0 + ow

            wy = np.ones(oh, dtype=np.float32)
            wx = np.ones(ow, dtype=np.float32)
            edge = overlap * scale

            if y0e > 0:
                n = min(edge, oh // 2)
                wy[:n] = np.linspace(0.05, 1.0, n, dtype=np.float32)
            if y1 < h:
                n = min(edge, oh // 2)
                wy[-n:] = np.linspace(1.0, 0.05, n, dtype=np.float32)
            if x0e > 0:
                n = min(edge, ow // 2)
                wx[:n] = np.linspace(0.05, 1.0, n, dtype=np.float32)
            if x1 < w:
                n = min(edge, ow // 2)
                wx[-n:] = np.linspace(1.0, 0.05, n, dtype=np.float32)

            ww = (wy[:, None] * wx[None, :])[:, :, None]
            acc[oy0:oy1, ox0:ox1] += result * ww
            weights[oy0:oy1, ox0:ox1] += ww

    out_bgr = np.clip(acc / np.maximum(weights, 1e-6), 0, 255).astype(np.uint8)
    out_rgb = cv2.cvtColor(out_bgr, cv2.COLOR_BGR2RGB)
    out_alpha = cv2.resize(alpha, (out_w, out_h), interpolation=cv2.INTER_LANCZOS4)
    return Image.fromarray(np.dstack([out_rgb, out_alpha]), "RGBA")

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
    ap.add_argument("--model", default="EDSR_x4.pb")
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

        model_path = Path(args.model)
        if not model_path.exists():
            raise RuntimeError(f"Modelo EDSR não encontrado: {model_path}")

        print(f"Aplicando super-resolução EDSR x4 em tiles no PPI real {source.size}...")
        sr = super_res_edsr(source, str(model_path))
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
            "model": "EDSR x4",
            "method": "OpenCV dnn_superres em tiles com sobreposição",
            "inventedDbz": False,
            "inventedCells": False,
            "note": (
                "A super-resolução atua somente sobre a imagem real do radar; "
                "nenhum dBZ, célula ou precipitação é inventado."
            ),
        },
        "processing": {
            "mode": "official-radar-image-plus-edsr",
            "inventedDbz": False,
            "inventedCells": False,
            "note": (
                "A fonte é o PPI de refletividade do SIFAP/Defesa Civil SC. "
                "A versão UHD é redimensionada e a super-res usa EDSR x4."
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
        },
        ensure_ascii=False,
    ))


if __name__ == "__main__":
    raise SystemExit(main())
