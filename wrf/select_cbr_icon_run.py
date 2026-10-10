#!/usr/bin/env python3
"""Pick a complete DWD ICON run for the exact valid-time window of one CBR segment.

404 is definitive for a path and is not retried. The selected source cycle can
be newer than the forecast base cycle, but never newer than the segment start;
ICON_OFFSET maps the target WRF hour to the selected source lead time.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import time
from pathlib import Path

import requests

BASE = "https://opendata.dwd.de/weather/nwp/icon/grib"
UA = "Sideral-CBR-run-selector/1.0"
PRESSURE_LEVELS = [1000, 925, 850, 700, 600, 500, 400, 300, 250, 200, 150, 100, 50]
PRESSURE_FIELDS = {
    "t": "T",
    "u": "U",
    "v": "V",
    "relhum": "RELHUM",
    "fi": "FI",
}
SURFACE_FIELDS = {
    "t_2m": "T_2M",
    "relhum_2m": "RELHUM_2M",
    "u_10m": "U_10M",
    "v_10m": "V_10M",
    "ps": "PS",
    "pmsl": "PMSL",
}


def pressure_url(date: str, cycle: str, step: int, folder: str, token: str, level: int) -> str:
    return (
        f"{BASE}/{cycle}/{folder}/"
        f"icon_global_icosahedral_pressure-level_{date}{cycle}_{step:03d}_{level}_{token}.grib2.bz2"
    )


def surface_url(date: str, cycle: str, step: int, folder: str, token: str) -> str:
    return (
        f"{BASE}/{cycle}/{folder}/"
        f"icon_global_icosahedral_single-level_{date}{cycle}_{step:03d}_{token}.grib2.bz2"
    )


def url_exists(url: str) -> bool:
    for attempt in range(1, 4):
        try:
            with requests.Session() as session:
                session.headers.update({"User-Agent": UA})
                response = session.head(url, timeout=18, allow_redirects=True)
                status = response.status_code
                response.close()
                if status == 200:
                    return True
                if status in (401, 403, 404, 410):
                    return False
                if status in (405, 501):
                    with session.get(url, headers={"Range": "bytes=0-0"}, timeout=18, stream=True) as ranged:
                        if ranged.status_code in (200, 206):
                            return True
                        if ranged.status_code in (401, 403, 404, 410):
                            return False
                        status = ranged.status_code
                if 500 <= status <= 599 and attempt < 3:
                    time.sleep(attempt)
                    continue
                return False
        except (requests.Timeout, requests.ConnectionError):
            if attempt < 3:
                time.sleep(attempt)
                continue
            return False
        except requests.RequestException:
            return False
    return False


def check_urls(urls: list[str], workers: int = 24) -> list[str]:
    missing: list[str] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        for url, ok in zip(urls, pool.map(url_exists, urls)):
            if not ok:
                missing.append(url)
    return missing


def required_urls(date: str, cycle: str, source_step: int) -> list[str]:
    urls: list[str] = []
    for folder, token in PRESSURE_FIELDS.items():
        for level in PRESSURE_LEVELS:
            urls.append(pressure_url(date, cycle, source_step, folder, token, level))
    for folder, token in SURFACE_FIELDS.items():
        urls.append(surface_url(date, cycle, source_step, folder, token))
    return urls


def candidate_runs(target_start: dt.datetime, count: int) -> list[dt.datetime]:
    base = target_start.replace(hour=(target_start.hour // 6) * 6, minute=0, second=0, microsecond=0)
    return [base - dt.timedelta(hours=6 * i) for i in range(count)]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target-date", required=True, help="Base WRF date YYYYMMDD")
    parser.add_argument("--target-cycle", required=True, help="Base WRF cycle HH")
    parser.add_argument("--start-hour", type=int, required=True)
    parser.add_argument("--end-hour", type=int, required=True)
    parser.add_argument("--max-back", type=int, default=8)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    if args.start_hour < 0 or args.end_hour < args.start_hour or args.end_hour > 42:
        raise SystemExit("Intervalo CBR ICON invalido")
    if args.start_hour % 3 or args.end_hour % 3:
        raise SystemExit("Inicio/fim CBR precisam ser multiplos de 3 horas")

    target_base = dt.datetime.strptime(args.target_date + args.target_cycle.zfill(2), "%Y%m%d%H").replace(tzinfo=dt.timezone.utc)
    target_start = target_base + dt.timedelta(hours=args.start_hour)
    selected = None

    for run in candidate_runs(target_start, max(1, args.max_back)):
        offset = int((target_base - run).total_seconds() // 3600)
        date, cycle = run.strftime("%Y%m%d"), run.strftime("%H")
        steps = [hour + offset for hour in range(args.start_hour, args.end_hour + 1, 3)]
        if not steps or min(steps) < 0:
            continue

        print(
            f"CBR ICON probe: {date} {cycle}Z; target F{args.start_hour:03d}-F{args.end_hour:03d}; "
            f"source steps F{steps[0]:03d}-F{steps[-1]:03d}",
            flush=True,
        )
        urls = [url for source_step in steps for url in required_urls(date, cycle, source_step)]
        missing = check_urls(urls)
        if not missing:
            selected = (date, cycle, offset, steps)
            print(f"CBR ICON probe: run completo; {len(urls)} arquivos essenciais confirmados", flush=True)
            break
        print(
            f"CBR ICON probe: run rejeitado; {len(missing)} arquivo(s) ausente(s), "
            f"primeiro={missing[0].rsplit('/', 1)[-1]}",
            flush=True,
        )

    if selected is None:
        raise SystemExit("Nenhum run ICON completo encontrado para a janela válida CBR")

    date, cycle, offset, steps = selected
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + ".tmp")
    temporary.write_text(
        f"ICON_RUN_DATE={date}\nICON_RUN_CYCLE={cycle}\nICON_OFFSET={offset}\n"
        f"ICON_SOURCE_START_STEP={steps[0]}\nICON_SOURCE_END_STEP={steps[-1]}\n",
        encoding="utf-8",
    )
    temporary.replace(output)


if __name__ == "__main__":
    main()
