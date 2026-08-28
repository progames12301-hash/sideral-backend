#!/usr/bin/env python3
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


BASE = "https://nomads.ncep.noaa.gov/pub/data/nccf/com/gfs/prod"


def available(run_date: str, cycle: str) -> bool:
    name = f"gfs.t{cycle}z.pgrb2.0p25.f000.idx"
    request = Request(
        f"{BASE}/gfs.{run_date}/{cycle}/atmos/{name}",
        method="HEAD",
        headers={"User-Agent": "Sideral-WRF-GitHub/1.0"},
    )
    try:
        with urlopen(request, timeout=15) as response:
            return response.status == 200
    except (HTTPError, URLError, TimeoutError):
        return False


def main() -> None:
    now = datetime.now(timezone.utc)
    candidates = [
        ((now - timedelta(days=shift)).strftime("%Y%m%d"), cycle)
        for shift in range(3)
        for cycle in ("18", "12", "06", "00")
    ]
    for run_date, cycle in sorted(candidates, reverse=True):
        if available(run_date, cycle):
            print(f"{run_date}{cycle}")
            return
    raise SystemExit("Nenhuma rodada GFS recente esta disponivel no NOMADS.")


if __name__ == "__main__":
    main()
