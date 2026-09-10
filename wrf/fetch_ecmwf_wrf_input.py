#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import os
import random
import time
from pathlib import Path

from ecmwf.opendata import Client

PRESSURE_LEVELS = [1000, 925, 850, 700, 600, 500, 400, 300, 250, 200, 150, 100, 50]
PRESSURE_PARAMS = ["gh", "t", "u", "v", "r"]
SURFACE_PARAMS = ["2t", "2d", "10u", "10v", "sp", "msl"]

MAX_ATTEMPTS = int(os.getenv("ECMWF_MAX_ATTEMPTS", "6"))
BASE_DELAY_SECONDS = float(os.getenv("ECMWF_BASE_DELAY_SECONDS", "15"))
BETWEEN_REQUESTS_SECONDS = float(os.getenv("ECMWF_BETWEEN_REQUESTS_SECONDS", "12"))


def as_utc(value) -> dt.datetime:
    if isinstance(value, dt.datetime):
        parsed = value
    else:
        parsed = dt.datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def _status_code(exc: Exception) -> int | None:
    response = getattr(exc, "response", None)
    code = getattr(response, "status_code", None)
    if isinstance(code, int):
        return code
    text = str(exc).lower()
    if "too many requests" in text or "429" in text:
        return 429
    for code in (500, 502, 503, 504):
        if str(code) in text:
            return code
    return None


def with_retry(label: str, func):
    last_error: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            print(f"ECMWF {label}: tentativa {attempt}/{MAX_ATTEMPTS}")
            return func()
        except Exception as exc:
            last_error = exc
            code = _status_code(exc)
            retryable = code == 429 or (code is not None and 500 <= code <= 599)
            if not retryable or attempt >= MAX_ATTEMPTS:
                raise

            response = getattr(exc, "response", None)
            retry_after = None
            if response is not None:
                headers = getattr(response, "headers", {}) or {}
                raw = headers.get("Retry-After")
                if raw:
                    try:
                        retry_after = float(raw)
                    except (TypeError, ValueError):
                        retry_after = None

            if retry_after is None:
                retry_after = BASE_DELAY_SECONDS * (2 ** (attempt - 1))
            delay = min(300.0, retry_after + random.uniform(0.0, 5.0))
            print(
                f"ECMWF {label}: HTTP {code}; aguardando {delay:.1f}s antes de tentar novamente",
                flush=True,
            )
            time.sleep(delay)

    if last_error is not None:
        raise last_error
    raise RuntimeError(f"ECMWF {label}: falha sem excecao registrada")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--max-hour", type=int, required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--run-env", required=True)
    p.add_argument("--date")
    p.add_argument("--cycle")
    args = p.parse_args()

    if args.max_hour < 0 or args.max_hour % 3:
        raise SystemExit("--max-hour precisa ser multiplo de 3")

    client = Client(source="ecmwf", model="ifs")
    steps = list(range(0, args.max_hour + 1, 3))

    if args.date and args.cycle:
        run = dt.datetime.strptime(args.date + args.cycle.zfill(2), "%Y%m%d%H").replace(tzinfo=dt.timezone.utc)
    else:
        # Consulta leve para descobrir uma rodada que ja tenha o ultimo passo necessario.
        run = as_utc(
            with_retry(
                "descoberta da rodada",
                lambda: client.latest(type="fc", step=args.max_hour, param=["2t"]),
            )
        )

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    pressure = output.with_name(output.stem + "_pressure.grib2")
    surface = output.with_name(output.stem + "_surface.grib2")

    common = {
        "date": run.strftime("%Y%m%d"),
        "time": int(run.strftime("%H")),
        "type": "fc",
        "step": steps,
    }

    print("ECMWF IFS rodada:", run.isoformat(), "passos:", steps)
    with_retry(
        "campos de pressao",
        lambda: client.retrieve(
            **common,
            param=PRESSURE_PARAMS,
            levelist=PRESSURE_LEVELS,
            target=str(pressure),
        ),
    )

    # Evita duas recuperacoes pesadas coladas, que favorecem HTTP 429.
    if BETWEEN_REQUESTS_SECONDS > 0:
        print(
            f"ECMWF: aguardando {BETWEEN_REQUESTS_SECONDS:.1f}s entre pressao e superficie",
            flush=True,
        )
        time.sleep(BETWEEN_REQUESTS_SECONDS)

    with_retry(
        "campos de superficie",
        lambda: client.retrieve(
            **common,
            param=SURFACE_PARAMS,
            target=str(surface),
        ),
    )

    if pressure.stat().st_size < 1_000_000:
        raise RuntimeError(f"ECMWF pressure pequeno demais: {pressure.stat().st_size}")
    if surface.stat().st_size < 100_000:
        raise RuntimeError(f"ECMWF surface pequeno demais: {surface.stat().st_size}")

    with output.open("wb") as out:
        out.write(pressure.read_bytes())
        out.write(surface.read_bytes())

    env = Path(args.run_env)
    env.parent.mkdir(parents=True, exist_ok=True)
    env.write_text(
        f"RUN_DATE={run:%Y%m%d}\n"
        f"RUN_CYCLE={run:%H}\n"
        "SOURCE_MODEL=ecmwf\n",
        encoding="utf-8",
    )

    print(f"ECMWF combinado: {output.stat().st_size / 1024 / 1024:.1f} MiB")
    print(env.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
