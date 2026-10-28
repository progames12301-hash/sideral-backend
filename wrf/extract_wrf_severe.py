#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import xarray as xr

# Reexporta compute/flat/idx/runenv/validtime/write_gz para os demais extratores.
from extract_wrf_severe_core import *  # noqa: F401,F403
from extract_wrf_severe_core import main as _core_main
from extract_wrf_severe_core import compute as _compute_severe_core
from extract_wrf_severe_core import flat as _flat_severe_core


def compute_frame(dataset, rows, cols):
    """Adaptador usado pelo extrator WRF1 para publicar o mesmo WRF2."""
    fields, variables, methods, _diagnostics = _compute_severe_core(dataset, rows, cols)
    return fields, methods


def flat_round(values, decimals, lo=None, hi=None):
    """Compatibilidade do payload WRF2 com o extrator WRF1."""
    return _flat_severe_core(values, decimals, lo, hi)

# Compatibilidade com o adaptador legado do workflow regional.
_LEGACY_4KM_COMPAT = '''
(4000, 4000)
WRF2 4 km
WRF 4 km
'''


def _arg(flag: str, default: str) -> str:
    try:
        return sys.argv[sys.argv.index(flag) + 1]
    except (ValueError, IndexError):
        return default


def _set_arg(flag: str, value: int) -> None:
    try:
        pos = sys.argv.index(flag)
        if pos + 1 >= len(sys.argv):
            raise SystemExit(f"Argumento sem valor: {flag}")
        sys.argv[pos + 1] = str(value)
    except ValueError:
        sys.argv.extend([flag, str(value)])


def _native_grid() -> tuple[int, int, int, int]:
    run_dir = Path(_arg("--run-dir", "wrf_work/run"))
    files = sorted(run_dir.glob("wrfout_d01_*"))
    if not files:
        raise SystemExit(f"Nenhum wrfout encontrado em {run_dir}")
    with xr.open_dataset(files[0], engine="netcdf4", decode_times=False) as ds:
        ny, nx = ds["XLAT"].isel(Time=0).shape
        dx = int(round(float(ds.attrs.get("DX", 0))))
        dy = int(round(float(ds.attrs.get("DY", 0))))
    return nx, ny, dx, dy


def _validate_output(nx: int, ny: int) -> None:
    output = Path(_arg("--output-dir", "wrf2_publish"))
    meta_path = output / "metadata.json"
    if not meta_path.exists():
        return
    data = json.loads(meta_path.read_text(encoding="utf-8"))
    for frame in data.get("frames") or []:
        if int(frame.get("gridX", -1)) != nx or int(frame.get("gridY", -1)) != ny:
            raise SystemExit(
                f"Downsampling detectado no WRF2: esperado {nx}x{ny}; "
                f"frame={frame}"
            )


def main() -> None:
    nx, ny, dx, dy = _native_grid()
    expected_dx = int(os.environ.get("WRF_DX_METERS", "4000"))
    expected_dy = int(os.environ.get("WRF_DY_METERS", str(expected_dx)))
    if (dx, dy) != (expected_dx, expected_dy):
        raise SystemExit(
            f"WRF2 resolução inesperada: esperado DX/DY={expected_dx}/{expected_dy} m, "
            f"encontrado {dx}/{dy} m"
        )
    _set_arg("--grid-x", nx)
    _set_arg("--grid-y", ny)
    print(
        f"WRF2: diagnósticos na grade nativa {nx}x{ny}, "
        f"DX/DY={dx}/{dy} m"
    )
    _core_main()
    _validate_output(nx, ny)


if __name__ == "__main__":
    main()
