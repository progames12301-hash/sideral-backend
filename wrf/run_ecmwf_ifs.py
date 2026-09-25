#!/usr/bin/env python3
"""Compatibility launcher for the Sideral ECMWF IFS extractor.

The existing extractor contains all GRIB decoding/publishing logic.  This
launcher only fixes the cycle selection: Sideral's IFS product is anchored to
00 UTC and must never silently switch to the 06/12/18 UTC cycles.
"""
from __future__ import annotations

import datetime as dt
import sys

import ecmwf_ifs_runner as runner


def choose_00z(client):
    """Return the newest available 00Z IFS forecast.

    Client.latest() can return a newer 06/12/18Z cycle.  The old extractor
    rejected those cycles, which made the workflow fail instead of selecting
    the latest 00Z run.  We deliberately floor the discovered cycle to 00Z.
    """
    latest = client.latest(stream="oper", type="fc", step=120, param=["2t", "msl"])
    if not isinstance(latest, dt.datetime):
        latest = dt.datetime.fromisoformat(str(latest))
    latest = latest.replace(tzinfo=dt.timezone.utc) if latest.tzinfo is None else latest.astimezone(dt.timezone.utc)
    run = latest.replace(hour=0, minute=0, second=0, microsecond=0)
    if run > latest:
        run -= dt.timedelta(days=1)
    print(f"ECMWF IFS: ciclo operacional fixado em {run:%Y-%m-%d 00Z}")
    return run


runner.choose_run = choose_00z

if __name__ == "__main__":
    sys.exit(runner.main())
