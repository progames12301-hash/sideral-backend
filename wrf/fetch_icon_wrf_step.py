#!/usr/bin/env python3
from __future__ import annotations

import argparse
import bz2
import concurrent.futures
import tempfile
import time
from pathlib import Path

import requests

BASE = "https://opendata.dwd.de/weather/nwp/icon/grib"
UA = "SideralMeteorologia-WRF/1.0"
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
    stamp = f"{date}{cycle}"
    return (
        f"{BASE}/{cycle}/{folder}/"
        f"icon_global_icosahedral_pressure-level_{stamp}_{step:03d}_{level}_{token}.grib2.bz2"
    )


def surface_url(date: str, cycle: str, step: int, folder: str, token: str) -> str:
    stamp = f"{date}{cycle}"
    return (
        f"{BASE}/{cycle}/{folder}/"
        f"icon_global_icosahedral_single-level_{stamp}_{step:03d}_{token}.grib2.bz2"
    )


def download_one(url: str, path: Path, required: bool) -> Path | None:
    last = None
    for attempt in range(1, 6):
        try:
            with requests.get(url, headers={"User-Agent": UA}, timeout=180, stream=True) as r:
                if r.status_code == 404:
                    if required:
                        raise RuntimeError(f"Campo ICON essencial nao publicado (HTTP 404): {url}")
                    print(f"AVISO ICON: campo opcional indisponivel (404), ignorando: {url.rsplit('/', 1)[-1]}")
                    return None
                r.raise_for_status()
                data = r.content
            path.write_bytes(bz2.decompress(data))
            print(f"OK {url.rsplit('/', 1)[-1]} -> {path.stat().st_size / 1024 / 1024:.2f} MiB")
            return path
        except Exception as exc:
            last = exc
            path.unlink(missing_ok=True)
            if attempt == 5:
                break
            time.sleep(attempt * 2)
    if not required:
        print(f"AVISO ICON: campo opcional falhou apos tentativas; ignorando: {url.rsplit('/', 1)[-1]} ({last})")
        return None
    raise RuntimeError(f"Falha ICON apos 5 tentativas: {url}: {last}")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--date", required=True)
    p.add_argument("--cycle", required=True)
    p.add_argument("--step", required=True, type=int)
    p.add_argument("--output", required=True)
    p.add_argument("--workers", type=int, default=8)
    args = p.parse_args()

    cycle = args.cycle.zfill(2)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)

    jobs: list[tuple[str, Path, bool]] = []
    idx = 0
    with tempfile.TemporaryDirectory(prefix="sideral-icon-") as tmp:
        tmpdir = Path(tmp)
        for folder, token in PRESSURE_FIELDS.items():
            for level in PRESSURE_LEVELS:
                url = pressure_url(args.date, cycle, args.step, folder, token, level)
                # T/U/V em 850 hPa sao essenciais para uma LBC utilizavel.
                required = folder in {"t", "u", "v"} and level == 850
                jobs.append((url, tmpdir / f"{idx:04d}.grib2", required))
                idx += 1
        for folder, token in SURFACE_FIELDS.items():
            url = surface_url(args.date, cycle, args.step, folder, token)
            required = folder in {"t_2m", "u_10m", "v_10m", "ps"}
            jobs.append((url, tmpdir / f"{idx:04d}.grib2", required))
            idx += 1

        results: dict[str, Path | None] = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
            future_map = {
                pool.submit(download_one, url, path, required): (url, path, required)
                for url, path, required in jobs
            }
            for future in concurrent.futures.as_completed(future_map):
                url, path, required = future_map[future]
                result = future.result()
                results[url] = result

        missing_required = [
            url for url, _, required in jobs if required and results.get(url) is None
        ]
        if missing_required:
            raise RuntimeError(
                "Campos ICON essenciais ausentes; nao e seguro gerar LBC: "
                + ", ".join(url.rsplit("/", 1)[-1] for url in missing_required)
            )

        available = [(url, path, required) for url, path, required in jobs if results.get(url) is not None]
        if len(available) < 10:
            raise RuntimeError(f"Dados ICON insuficientes: apenas {len(available)} arquivos baixados")

        # Mantem ordem deterministica e ignora somente campos indisponiveis.
        with output.open("wb") as out:
            for _, part, _ in available:
                out.write(part.read_bytes())

    if output.stat().st_size < 1_000_000:
        raise RuntimeError(f"Arquivo ICON combinado pequeno demais: {output.stat().st_size}")
    print(
        f"ICON F{args.step:03d} combinado: {output.stat().st_size / 1024 / 1024:.1f} MiB "
        f"({len(available)}/{len(jobs)} campos/arquivos)"
    )


if __name__ == "__main__":
    main()
