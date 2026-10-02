#!/usr/bin/env python3
"""Sideral raw radar collector.

Downloads openly exposed raw/quantitative radar files from official sources.
For CEMADEN, the runner walks the official radar/product/file indexes and only
attempts a download when the published file link itself is directly accessible.
It never solves, submits, or bypasses CAPTCHA/authentication.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from html import unescape
from pathlib import Path
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

INPE_SOURCES = {
    "inpe_radar_raw": "https://ftp.cptec.inpe.br/nowcasting/radar/",
    "inpe_radar_volume": "https://ftp.cptec.inpe.br/nowcasting/DADOS/radar_volumetrico/",
    "inpe_radar_velocity": "https://ftp.cptec.inpe.br/nowcasting/DADOS/velocidade_radial/",
}

CEMADEN_BASE = "https://mapainterativo.cemaden.gov.br"
CEMADEN_RADARS = (
    "almenara", "jaraguari", "maceio", "natal", "petrolina",
    "salvador", "santa_teresa", "sao_francisco", "tres_marias",
)

ALLOWED_EXTENSIONS = {".raw", ".nc", ".h5", ".hdf5", ".vol", ".bufr", ".bin"}
RADAR_EXTENSIONS = {".vol", ".h5", ".hdf5", ".nc", ".raw", ".bufr", ".bin"}


def get(url: str, *, max_bytes: int | None = None) -> bytes:
    req = Request(url, headers={"User-Agent": "Sideral-Radar-Runner/1.1"})
    with urlopen(req, timeout=60) as response:
        if max_bytes is None:
            return response.read()
        data = response.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise RuntimeError(f"resposta excede limite de {max_bytes} bytes")
        return data


def get_html(url: str) -> str:
    return get(url, max_bytes=8 * 1024 * 1024).decode("utf-8", errors="replace")


def list_links(index_url: str) -> list[tuple[str, str]]:
    html = get_html(index_url)
    links = re.findall(r'href=["\']([^"\']+)["\']', html, flags=re.I)
    result: list[tuple[str, str]] = []
    for href in links:
        href = unescape(href.strip())
        if href.startswith("#") or href.startswith("?"):
            continue
        absolute = urljoin(index_url, href)
        name = Path(urlparse(absolute).path).name
        if not name or name in {"..", "."}:
            continue
        result.append((name, absolute))
    return result


def list_files(index_url: str) -> list[tuple[str, str]]:
    return [
        (name, url)
        for name, url in list_links(index_url)
        if Path(name).suffix.lower() in ALLOWED_EXTENSIONS
    ]


def timestamp_key(name: str) -> tuple[int, str]:
    matches = re.findall(r"20\d{10,12}", name)
    if matches:
        return (int(matches[-1]), name)
    return (0, name)


def download_latest(source: str, url: str, output: Path, count: int) -> dict:
    files = list_files(url)
    if not files:
        raise RuntimeError(f"Nenhum arquivo bruto encontrado em {url}")

    files.sort(key=lambda item: timestamp_key(item[0]), reverse=True)
    selected = files[:count]
    source_dir = output / source
    source_dir.mkdir(parents=True, exist_ok=True)

    downloaded = []
    for name, file_url in selected:
        target = source_dir / name
        data = get(file_url)
        target.write_bytes(data)
        downloaded.append({
            "name": name,
            "url": file_url,
            "bytes": len(data),
            "path": str(target),
        })
        print(f"[OK] {source}: {name} ({len(data)} bytes)")

    return {"source": source, "index": url, "count": len(downloaded), "files": downloaded}


def is_direct_file_url(url: str) -> bool:
    """True only for a published file URL, never for the CAPTCHA handler."""
    path = urlparse(url).path.lower()
    if "downradarescaptcha.php" in path:
        return False
    return Path(path).suffix.lower() in RADAR_EXTENSIONS


def collect_cemaden(output: Path, count: int) -> dict:
    """Discover CEMADEN raw files from its public indexes.

    The official site exposes radar -> product -> file indexes. Some file links
    currently point to downradarescaptcha.php. Those are recorded as blocked and
    are NOT submitted or bypassed. If CEMADEN publishes a direct binary link,
    the same runner will download it automatically.
    """
    source_dir = output / "cemaden"
    source_dir.mkdir(parents=True, exist_ok=True)

    result = {
        "source": "cemaden",
        "index": f"{CEMADEN_BASE}/download/downradares.php",
        "radars": [],
        "downloaded": [],
        "captcha_required": [],
        "errors": [],
    }

    for radar in CEMADEN_RADARS:
        radar_url = f"{CEMADEN_BASE}/download/downradares.php?radar={radar}"
        radar_info = {"radar": radar, "products": []}
        try:
            product_links = list_links(radar_url)
        except Exception as exc:
            radar_info["error"] = str(exc)
            result["errors"].append({"radar": radar, "stage": "products", "error": str(exc)})
            result["radars"].append(radar_info)
            continue

        product_links = [
            (name, url) for name, url in product_links
            if "produto=" in url and "radar=" in url
        ]

        # Keep unique product URLs and prefer the two known volume products.
        seen = set()
        unique_products = []
        for name, url in product_links:
            if url in seen:
                continue
            seen.add(url)
            unique_products.append((name, url))

        for product_name, product_url in unique_products:
            product_info = {"product": product_name, "url": product_url, "files_seen": 0}
            try:
                file_links = list_links(product_url)
            except Exception as exc:
                product_info["error"] = str(exc)
                radar_info["products"].append(product_info)
                continue

            file_links = [
                (name, url) for name, url in file_links
                if Path(urlparse(url).path).name.lower().endswith(tuple(RADAR_EXTENSIONS))
            ]
            file_links.sort(key=lambda item: timestamp_key(item[0]), reverse=True)
            product_info["files_seen"] = len(file_links)

            # We only need a small recent sample. Prefer dBZ/V and then the other
            # dual-pol variables so the artifact demonstrates the real structure.
            preferred = ("dBZ", "V", "W", "ZDR", "KDP", "PhiDP", "RhoHV")
            ordered = sorted(
                file_links,
                key=lambda item: (
                    next((len(preferred) - i for i, token in enumerate(preferred) if token in item[0]), 0),
                    timestamp_key(item[0]),
                ),
                reverse=True,
            )

            downloaded_here = 0
            for name, file_url in ordered:
                if downloaded_here >= max(1, count):
                    break
                if not is_direct_file_url(file_url):
                    result["captcha_required"].append({
                        "radar": radar,
                        "product": product_name,
                        "name": name,
                        "url": file_url,
                        "reason": "published link requires CAPTCHA",
                    })
                    continue

                try:
                    target = source_dir / radar / product_name / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    data = get(file_url)
                    target.write_bytes(data)
                    item = {
                        "radar": radar,
                        "product": product_name,
                        "name": name,
                        "url": file_url,
                        "bytes": len(data),
                        "path": str(target),
                    }
                    result["downloaded"].append(item)
                    downloaded_here += 1
                    print(f"[OK] CEMADEN {radar}/{product_name}: {name} ({len(data)} bytes)")
                except Exception as exc:
                    result["errors"].append({
                        "radar": radar,
                        "product": product_name,
                        "name": name,
                        "url": file_url,
                        "error": str(exc),
                    })

            radar_info["products"].append(product_info)

        result["radars"].append(radar_info)

    result["count"] = len(result["downloaded"])
    result["captcha_count"] = len(result["captcha_required"])
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="radar_raw", help="Diretório de saída")
    parser.add_argument("--count", type=int, default=2, help="Arquivos mais recentes por fonte/produto")
    args = parser.parse_args()

    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)

    manifest = {
        "project": "Sideral Meteorologia",
        "collector": "radar_raw_runner",
        "sources": [],
        "notes": [
            "Somente dados expostos publicamente sem bypass de CAPTCHA/autenticação.",
            "CEMADEN: o runner percorre os índices oficiais radar -> produto -> arquivo e baixa automaticamente qualquer link de arquivo que seja realmente direto.",
            "Links que apontam para downradarescaptcha.php são apenas registrados como captcha_required e nunca são submetidos pelo runner.",
        ],
    }

    failed = []
    for source, url in INPE_SOURCES.items():
        try:
            manifest["sources"].append(download_latest(source, url, output, max(1, args.count)))
        except Exception as exc:
            failed.append({"source": source, "url": url, "error": str(exc)})
            print(f"[WARN] {source}: {exc}", file=sys.stderr)

    try:
        cemaden = collect_cemaden(output, max(1, args.count))
        manifest["sources"].append(cemaden)
    except Exception as exc:
        failed.append({"source": "cemaden", "error": str(exc)})
        print(f"[WARN] cemaden: {exc}", file=sys.stderr)

    (output / "manifest.json").write_text(
        json.dumps({**manifest, "failed": failed}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    if not manifest["sources"]:
        print("Nenhuma fonte pública de radar pôde ser baixada ou consultada.", file=sys.stderr)
        return 1

    print(json.dumps({
        "downloaded_sources": len(manifest["sources"]),
        "failed": failed,
        "cemaden_direct_attempted": any(s.get("source") == "cemaden" for s in manifest["sources"]),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
