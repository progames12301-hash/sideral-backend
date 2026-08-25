#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
from pathlib import Path

from ecmwf.opendata import Client

PRESSURE_LEVELS = [1000, 925, 850, 700, 600, 500, 400, 300, 250, 200, 150, 100, 50]
PRESSURE_PARAMS = ["gh", "t", "u", "v", "r"]
SURFACE_PARAMS = ["2t", "2d", "10u", "10v", "sp", "msl"]


def as_utc(value) -> dt.datetime:
    if isinstance(value, dt.datetime):
        parsed = value
    else:
        parsed = dt.datetime.fromisoformat(str(value))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


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
        # Usa um campo superficial simples para descobrir a rodada que ja possui
        # o ultimo passo necessario. A recuperacao atmosferica abaixo permanece IFS.
        run = as_utc(client.latest(type="fc", step=args.max_hour, param=["2t"]))

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
    client.retrieve(
        **common,
        param=PRESSURE_PARAMS,
        levelist=PRESSURE_LEVELS,
        target=str(pressure),
    )
    client.retrieve(
        **common,
        param=SURFACE_PARAMS,
        target=str(surface),
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
