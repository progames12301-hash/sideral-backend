"""Isolated CPTEC polar conversion worker.

This module intentionally runs outside the Render HTTP process because
netCDF4/HDF5 are native dependencies and a segmentation fault must not
bring down the API serving radar clients.
"""
from __future__ import annotations

import sys

from .adapters.cptec_raw import ensure_recent


def main() -> int:
    if len(sys.argv) != 3:
        print("uso: python -m backend.radar_v3.cptec_raw_worker <root> <limit>", file=sys.stderr)
        return 2
    root = sys.argv[1]
    try:
        limit = max(1, min(96, int(sys.argv[2])))
    except ValueError:
        print("limite inválido", file=sys.stderr)
        return 2

    try:
        result = ensure_recent(root, limit)
        print(f"[CPTEC-RAW] volumes convertidos: {len(result)}", flush=True)
        return 0
    except Exception as exc:
        print(f"[CPTEC-RAW] erro: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
