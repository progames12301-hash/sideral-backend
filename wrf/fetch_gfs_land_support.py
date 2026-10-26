#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import math
import re
import time
from pathlib import Path

import requests

UA = "SideralMeteorologia-WRF/2.0"
BASE = "https://noaa-gfs-bdp-pds.s3.amazonaws.com"

# O WRF recebe a atmosfera do ICON/ECMWF. Estes campos do GFS sao usados
# somente para completar solo/terreno/snow exigidos pelo WPS/real.exe.
#
# A rodada atmosferica pode aparecer antes da rodada GFS equivalente. Para
# evitar 404 nesse intervalo, este script escolhe automaticamente a rodada
# GFS mais recente que cubra os MESMOS horarios validos do ICON/ECMWF.
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


def gfs_urls(date: str, cycle: str, step: int) -> tuple[str, str]:
    name = f"gfs.t{cycle}z.pgrb2.0p25.f{step:03d}"
    url = f"{BASE}/gfs.{date}/{cycle}/atmos/{name}"
    return url, url + ".idx"


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


def exists(session: requests.Session, url: str) -> bool:
    try:
        r = session.get(url, headers={"Range": "bytes=0-0"}, timeout=25)
        return r.status_code in (200, 206)
    except requests.RequestException:
        return False


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
    url, idx_url = gfs_urls(date, cycle, step)
    entries = parse_idx(get_text(session, idx_url))

    selected: list[tuple[int, int | None, str]] = []
    for i, (start, line) in enumerate(entries):
        if not any(rx.search(line) for rx in WANTED_RE):
            continue
        next_start = entries[i + 1][0] if i + 1 < len(entries) else None
        end = None if next_start is None else next_start - 1
        selected.append((start, end, line))

    if len(selected) < 12:
        lines = "\n".join(line for _, line in entries)
        raise RuntimeError(
            f"Poucos campos de solo selecionados em {url}: {len(selected)}.\n"
            f"Inventario:\n{lines[:12000]}"
        )

    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("wb") as fhout:
        for start, end, line in selected:
            print("GFS land:", line)
            fhout.write(get_range(session, url, start, end))
    print(f"{output}: {output.stat().st_size / 1024 / 1024:.1f} MiB, {len(selected)} mensagens")


def choose_run(session: requests.Session, target: dt.datetime, max_target_hour: int) -> tuple[dt.datetime, int]:
    # GFS roda de 6 em 6 h. Tenta a rodada equivalente e depois recua.
    for lag in (0, 6, 12, 18, 24, 30, 36):
        candidate = target - dt.timedelta(hours=lag)
        last_step = lag + max_target_hour
        _, first_idx = gfs_urls(candidate.strftime("%Y%m%d"), candidate.strftime("%H"), lag)
        _, last_idx = gfs_urls(candidate.strftime("%Y%m%d"), candidate.strftime("%H"), last_step)
        print(
            f"Testando GFS land {candidate:%Y%m%d %H}Z: "
            f"F{lag:03d}..F{last_step:03d} para validar {target:%Y%m%d %H}Z"
        )
        if exists(session, first_idx) and exists(session, last_idx):
            return candidate, lag
    raise RuntimeError(
        f"Nenhuma rodada GFS de suporte cobre {target:%Y%m%d %H}Z ate +{max_target_hour} h"
    )


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--date", required=True)
    p.add_argument("--cycle", required=True)
    p.add_argument("--max-hour", type=int, required=True)
    p.add_argument("--output-dir", required=True)
    args = p.parse_args()

    if args.max_hour < 0:
        raise SystemExit("--max-hour deve ser >= 0")

    # As fontes atmosfericas usadas pelo WPS estao em passos de 3 h. Para um
    # horizonte como F040, a cobertura lateral precisa chegar a F042.
    source_max_hour = int(math.ceil(args.max_hour / 3.0) * 3)
    target = dt.datetime.strptime(args.date + args.cycle.zfill(2), "%Y%m%d%H").replace(tzinfo=dt.timezone.utc)

    session = requests.Session()
    session.headers.update({"User-Agent": UA})
    out = Path(args.output_dir)
    gfs_run, lag = choose_run(session, target, source_max_hour)
    gfs_date, gfs_cycle = gfs_run.strftime("%Y%m%d"), gfs_run.strftime("%H")
    print(
        f"GFS land escolhido: {gfs_date} {gfs_cycle}Z (lag {lag} h); "
        f"horarios validos alinhados ao alvo {target:%Y%m%d %H}Z"
    )

    for target_step in range(0, source_max_hour + 1, 3):
        gfs_step = lag + target_step
        fetch_step(
            session,
            gfs_date,
            gfs_cycle,
            gfs_step,
            out / f"gfs_land_f{target_step:03d}.grib2",
        )


if __name__ == "__main__":
    main()
