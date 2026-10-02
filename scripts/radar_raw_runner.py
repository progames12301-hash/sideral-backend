#!/usr/bin/env python3
"""Sideral raw radar collector.

Downloads openly exposed raw/quantitative radar files from official INPE/CPTEC
FTP indexes. It intentionally does not bypass CAPTCHA or other access controls.

The collector keeps only a small number of newest files per source so the
GitHub Actions artifact stays small. Long-term/object storage can be plugged in
later without changing the source adapters.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from urllib.parse import urljoin
from urllib.request import Request, urlopen

INPE_SOURCES = {
    "inpe_radar_raw": "https://ftp.cptec.inpe.br/nowcasting/radar/",
    "inpe_radar_volume": "https://ftp.cptec.inpe.br/nowcasting/DADOS/radar_volumetrico/",
    "inpe_radar_velocity": "https://ftp.cptec.inpe.br/nowcasting/DADOS/velocidade_radial/",
}

ALLOWED_EXTENSIONS = {".raw", ".nc", ".h5", ".hdf5", ".vol", ".bufr", ".bin"}


def get(url: str) -> bytes:
    req = Request(url, headers={"User-Agent": "Sideral-Radar-Runner/1.0"})
    with urlopen(req, timeout=60) as response:
        return response.read()


def list_files(index_url: str) -> list[tuple[str, str]]:
    html = get(index_url).decode("utf-8", errors="replace")
    # Apache directory indexes expose href="filename". Ignore parent dirs.
    links = re.findall(r'href=["\']([^"\']+)["\']', html, flags=re.I)
    result: list[tuple[str, str]] = []
    for href in links:
        if href.startswith("?") or href.endswith("/"):
            continue
        name = href.rsplit("/", 1)[-1]
        if Path(name).suffix.lower() in ALLOWED_EXTENSIONS:
            result.append((name, urljoin(index_url, href)))
    return result


def timestamp_key(name: str) -> tuple[int, str]:
    # Prefer timestamps embedded in radar filenames; fall back to lexical order.
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

    return {
        "source": source,
        "index": url,
        "count": len(downloaded),
        "files": downloaded,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="radar_raw", help="Diretório de saída")
    parser.add_argument("--count", type=int, default=2, help="Arquivos mais recentes por fonte")
    args = parser.parse_args()

    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)

    manifest = {
        "project": "Sideral Meteorologia",
        "collector": "radar_raw_runner",
        "sources": [],
        "notes": [
            "Somente dados expostos publicamente sem bypass de CAPTCHA/autenticação.",
            "CEMADEN possui arquivos VOL/HDF5, mas o endpoint público de download atualmente exige CAPTCHA; por isso não é automatizado aqui.",
        ],
    }

    failed = []
    for source, url in INPE_SOURCES.items():
        try:
            manifest["sources"].append(download_latest(source, url, output, max(1, args.count)))
        except Exception as exc:
            failed.append({"source": source, "url": url, "error": str(exc)})
            print(f"[WARN] {source}: {exc}", file=sys.stderr)

    (output / "manifest.json").write_text(
        json.dumps({**manifest, "failed": failed}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # Do not fail the whole runner when one public directory is unavailable.
    if not manifest["sources"]:
        print("Nenhuma fonte pública de radar pôde ser baixada.", file=sys.stderr)
        return 1

    print(json.dumps({"downloaded_sources": len(manifest["sources"]), "failed": failed}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
