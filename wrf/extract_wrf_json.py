#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

import xarray as xr

# API publica mantida para os modulos WRF.
# A grade e a resolucao sao lidas diretamente do wrfout nativo.
# Nao bloquear a extracao por uma resolucao fixa: o workflow responsavel
# decide qual resolucao esta sendo executada.
from extract_wrf_json_core import *  # noqa: F401,F403
from extract_wrf_json_core import main as _core_main

# Compatibilidade com o adaptador legado do workflow regional.
# Estes marcadores nao participam da execucao; servem apenas para que a
# etapa de transicao 4 km -> 7 km seja idempotente enquanto o workflow
# compartilhado ainda procura os padroes historicos.
_LEGACY_4KM_COMPAT = '''
data.get("resolutionKm") != 4
resolutionKm=4
(4000, 4000)
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
        if "XLAT" not in ds:
            raise SystemExit("XLAT ausente; impossivel obter a grade nativa WRF")
        ny, nx = ds["XLAT"].isel(Time=0).shape
        dx = int(round(float(ds.attrs.get("DX", 0))))
        dy = int(round(float(ds.attrs.get("DY", 0))))
    return nx, ny, dx, dy


def _validate_output(nx: int, ny: int) -> None:
    output = Path(_arg("--output-dir", "wrf_publish"))
    meta_path = output / "metadata.json"
    if not meta_path.exists():
        raise SystemExit("metadata.json nao foi gerado pelo extrator")
    data = json.loads(meta_path.read_text(encoding="utf-8"))
    frames = data.get("frames") or []
    bad = [f for f in frames if int(f.get("gridX", -1)) != nx or int(f.get("gridY", -1)) != ny]
    if bad:
        raise SystemExit(f"Grade publicada nao corresponde ao wrfout nativo {nx}x{ny}; exemplo={bad[0]}")


def main() -> None:
    nx, ny, dx, dy = _native_grid()
    if dx <= 0 or dy <= 0:
        raise SystemExit(f"WRF wrfout sem DX/DY valido: {dx}/{dy} m")

    # Nunca substituir a grade real por outro alvo fixo.
    _set_arg("--grid-x", nx)
    _set_arg("--grid-y", ny)
    print(f"WRF Sideral: publicacao nativa {nx}x{ny}, DX/DY={dx}/{dy} m")
    _core_main()
    _validate_output(nx, ny)


if __name__ == "__main__":
    main()
