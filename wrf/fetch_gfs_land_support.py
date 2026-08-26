#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import time
from pathlib import Path

import requests

UA = "SideralMeteorologia-WRF/1.0"
BASE = "https://noaa-gfs-bdp-pds.s3.amazonaws.com"

# O WRF recebe a atmosfera do ICON/ECMWF. Estes campos do GFS sao usados
# somente para completar solo/terreno/snow que o WPS/real.exe exige.
#
# Usa pgrb2.0p25 (e nao sfluxgrb) porque esta e a familia de GRIB2 coberta
# pela Vtable.GFS oficial do WPS, inclusive para ST/SM em 4 camadas.
WANTED = (
    r":HGT:surface:",
    r":TMP:surface:",
    r":LAND:surface:",
    r":ICEC:surface:",
    r":WEASD:surface:",
    r":SNOD:surface:",
    r":TSOIL:0-0\.1 m below ground:",
    r":TSOIL:0\.1-0\.4 m below ground:",
    r":TSOIL:0\.4-1 m below ground:",
    r":TSOIL:1-2 m below ground:",
    r":SOILW:0-0\.1 m below ground:",
    r":SOILW:0\.1-0\.4 m below ground:",
    r":SOILW:0\.4-1 m below ground:",
    r":SOILW:1-2 m below ground:",
)
WANTED_RE = [re.compile(p) for p in WANTED]


def get_text(session: requests.Session, url: str) -> str:
    last = None
    for attempt in range(1, 6):
        try:
            r = session.get(url, timeout=60)
            r.raise_for_status()
            return r.text
        except requests.RequestException as exc:
            last = exc
            if attempt == 5:
                break
            time.sleep(attempt * 2)
    raise RuntimeError(f"Falha ao baixar {url}: {last}")


def get_range(session: requests.Session, url: str, start: int, end: int | None) -> bytes:
    headers = {"Range": f"bytes={start}-{'' if end is None else end}"}
    last = None
    for attempt in range(1, 6):
        try:
            r = session.get(url, headers=headers, timeout=120)
            if r.status_code not in (200, 206):
                r.raise_for_status()
            return r.content
        except requests.RequestException as exc:
            last = exc
            if attempt == 5:
                break
            time.sleep(attempt * 2)
    raise RuntimeError(f"Falha no range {start}-{end} de {url}: {last}")


def parse_idx(text: str) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    for line in text.splitlines():
        parts = line.split(":", 2)
        if len(parts) < 3:
            continue
        try:
            offset = int(parts[1])
        except ValueError:
            continue
        out.append((offset, line))
    if not out:
        raise RuntimeError("Indice GFS vazio ou em formato inesperado")
    return out


def fetch_step(session: requests.Session, date: str, cycle: str, step: int, output: Path) -> None:
    fh = f"{step:03d}"
    name = f"gfs.t{cycle}z.pgrb2.0p25.f{fh}"
    url = f"{BASE}/gfs.{date}/{cycle}/atmos/{name}"
    idx_url = url + ".idx"
    entries = parse_idx(get_text(session, idx_url))

    selected: list[tuple[int, int | None, str]] = []
    for i, (start, line) in enumerate(entries):
        if not any(rx.search(line) for rx in WANTED_RE):
            continue
        next_start = entries[i + 1][0] if i + 1 < len(entries) else None
        end = None if next_start is None else next_start - 1
        selected.append((start, end, line))

    # 4x ST + 4x SM e os principais campos de superficie devem estar presentes.
    if len(selected) < 12:
        lines = "\n".join(line for _, line in entries)
        raise RuntimeError(
            f"Poucos campos de solo selecionados em {name}: {len(selected)}.\n"
            f"Inventario:\n{lines[:12000]}"
        )

    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("wb") as fhout:
        for start, end, line in selected:
            print("GFS land:", line)
            fhout.write(get_range(session, url, start, end))
    print(f"{output}: {output.stat().st_size / 1024 / 1024:.1f} MiB, {len(selected)} mensagens")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--date", required=True)
    p.add_argument("--cycle", required=True)
    p.add_argument("--max-hour", type=int, required=True)
    p.add_argument("--output-dir", required=True)
    args = p.parse_args()

    session = requests.Session()
    session.headers.update({"User-Agent": UA})
    out = Path(args.output_dir)
    for step in range(0, args.max_hour + 1, 3):
        fetch_step(
            session,
            args.date,
            args.cycle.zfill(2),
            step,
            out / f"gfs_land_f{step:03d}.grib2",
        )


if __name__ == "__main__":
    main()
