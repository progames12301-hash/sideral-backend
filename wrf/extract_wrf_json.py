#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

import xarray as xr

# Mantem a API publica do extrator original para os outros modulos WRF.
# REFL_10CM_NATIVE continua sendo a unica refletividade publicada.
# A Sideral nao publica refletividade aproximada como substituta.
from extract_wrf_json_core import *  # noqa: F401,F403
from extract_wrf_json_core import main as _core_main


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
        if "XLAT" not in ds:
            raise SystemExit("XLAT ausente; impossivel validar a grade nativa WRF")
        ny, nx = ds["XLAT"].isel(Time=0).shape
        dx = int(round(float(ds.attrs.get("DX", 0))))
        dy = int(round(float(ds.attrs.get("DY", 0))))
    return nx, ny, dx, dy


def _validate_output(nx: int, ny: int) -> None:
    output = Path(_arg("--output-dir", "wrf_publish"))
    meta_path = output / "metadata.json"
    data = json.loads(meta_path.read_text(encoding="utf-8"))
    if data.get("resolutionKm") != 4:
        raise SystemExit(f"WRF Sideral deve publicar resolutionKm=4: {data.get('resolutionKm')}")
    frames = data.get("frames") or []
    bad = [f for f in frames if int(f.get("gridX", -1)) != nx or int(f.get("gridY", -1)) != ny]
    if bad:
        raise SystemExit(f"Downsampling detectado no WRF 4 km: esperado {nx}x{ny}; exemplo={bad[0]}")


def main() -> None:
    nx, ny, dx, dy = _native_grid()
    if (dx, dy) != (4000, 4000):
        raise SystemExit(f"WRF Sideral deveria ser 4 km, mas wrfout informa DX/DY={dx}/{dy} m")
    # Sobrescreve 220x180 (ou qualquer outro alvo) com a grade REAL do wrfout.
    _set_arg("--grid-x", nx)
    _set_arg("--grid-y", ny)
    print(f"WRF Sideral: publicacao nativa travada em {nx}x{ny}, DX/DY={dx}/{dy} m")
    _core_main()
    _validate_output(nx, ny)


if __name__ == "__main__":
    main()
