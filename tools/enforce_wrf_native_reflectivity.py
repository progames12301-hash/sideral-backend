#!/usr/bin/env python3
from __future__ import annotations

import re
from pathlib import Path

SERVER = Path("server.py")


def main() -> None:
    text = SERVER.read_text(encoding="utf-8")
    original = text

    # Compatibilidade com uma versao antiga do backend: se o fallback ainda
    # estiver presente, substitui-o por REFL_10CM estritamente nativo.
    pattern = re.compile(
        r'''        reflectivity_source = "REFL_10CM" if "REFL_10CM" in dataset else "hydrometeors"\n'''
        r'''        if "REFL_10CM" in dataset:\n'''
        r'''            reflectivity = np\.maximum\(0\.0, np\.nanmax\(dataset\["REFL_10CM"\]\.isel\(Time=0\)\.to_numpy\(\), axis=0\)\)\n'''
        r'''            approx_reflectivity = approximate_reflectivity_dbz\(dataset, t2m, precip_rate\)\n'''
        r'''            if np\.nanmax\(reflectivity\) < 20\.0 and np\.nanmax\(approx_reflectivity\) > np\.nanmax\(reflectivity\):\n'''
        r'''                reflectivity_source = "REFL_10CM\+hydrometeors_approx"; reflectivity = np\.maximum\(reflectivity, approx_reflectivity\)\n'''
        r'''        else:\n'''
        r'''            reflectivity = approximate_reflectivity_dbz\(dataset, t2m, precip_rate\)\n'''
        r'''            if np\.nanmax\(reflectivity\) < 1\.0: reflectivity_source = "precip_rate"; reflectivity = np\.vectorize\(precipitation_to_dbz\)\(precip_rate\)\n'''
    )

    replacement = '''        # Contrato operacional: a camada WRF nunca inventa dBZ a partir de chuva\n        # ou hidrometeoros. A refletividade vem somente de REFL_10CM calculado\n        # pela microfisica do proprio WRF.\n        if "REFL_10CM" not in dataset:\n            raise RuntimeError("WRF sem REFL_10CM nativo; refletividade recusada para evitar geracao artificial.")\n        reflectivity_source = "REFL_10CM_NATIVE"\n        reflectivity_raw = dataset["REFL_10CM"].isel(Time=0).to_numpy()\n        if reflectivity_raw.ndim == 3:\n            reflectivity = np.nanmax(reflectivity_raw, axis=0)\n        elif reflectivity_raw.ndim == 2:\n            reflectivity = reflectivity_raw\n        else:\n            raise RuntimeError(f"REFL_10CM com dimensoes inesperadas: {reflectivity_raw.shape}")\n        reflectivity = np.maximum(0.0, np.nan_to_num(reflectivity, nan=0.0))\n'''
    text, _ = pattern.subn(replacement, text, count=1)

    # Remove de vez os calculadores sinteticos antigos. Eles nao sao mais usados
    # pelo WRF e nao devem permanecer disponiveis para serem religados por engano.
    synthetic_block = re.compile(
        r'\ndef hydrometeor_reflectivity_dbz\(dataset: Any, t2m: Any\) -> Any:\n.*?(?=\ndef find_wrf_files\()',
        re.DOTALL,
    )
    text, _ = synthetic_block.subn('\n', text, count=1)

    # A conversao chuva->dBZ continua apenas para o produto Meteoblue, que nao e
    # WRF. O nome explicito impede que ela seja confundida com refletividade WRF.
    text = text.replace(
        'def precipitation_to_dbz(precipitation_mm_per_hour: float) -> float:',
        'def meteoblue_precipitation_to_dbz(precipitation_mm_per_hour: float) -> float:',
        1,
    )
    text = text.replace(
        'reflectivity = precipitation_to_dbz(precipitation)',
        'reflectivity = meteoblue_precipitation_to_dbz(precipitation)',
        1,
    )

    start = text.find("def build_wrf_cells(")
    end = text.find("\ndef build_gfs_cells(", start)
    if start < 0 or end < 0:
        raise SystemExit("Nao foi possivel localizar build_wrf_cells para validar.")
    block = text[start:end]

    forbidden_wrf = (
        "REFL_10CM+hydrometeors_approx",
        'reflectivity_source = "precip_rate"',
        "approximate_reflectivity_dbz(",
        "hydrometeor_reflectivity_dbz(",
        "precipitation_to_dbz(",
        "QRAIN",
        "QSNOW",
        "QGRAUP",
        "QHAIL",
    )
    found = [token for token in forbidden_wrf if token in block]
    if found:
        raise SystemExit(f"Refletividade artificial ainda ativa em build_wrf_cells: {found}")
    if 'reflectivity_source = "REFL_10CM_NATIVE"' not in block:
        raise SystemExit("Fonte REFL_10CM_NATIVE nao confirmada em build_wrf_cells.")
    if 'if "REFL_10CM" not in dataset:' not in block:
        raise SystemExit("WRF ainda nao falha quando REFL_10CM esta ausente.")

    # Nao deixa os helpers sinteticos antigos sobreviverem em nenhum ponto do backend.
    global_forbidden = (
        "def approximate_reflectivity_dbz(",
        "def hydrometeor_reflectivity_dbz(",
        "def precipitation_to_dbz(",
    )
    leftovers = [token for token in global_forbidden if token in text]
    if leftovers:
        raise SystemExit(f"Helpers sinteticos antigos ainda presentes: {leftovers}")
    if 'def meteoblue_precipitation_to_dbz(' not in text:
        raise SystemExit("Conversor Meteoblue isolado nao encontrado.")

    if text != original:
        SERVER.write_text(text, encoding="utf-8")
        print("server.py corrigido: WRF zero-dBZ-artificial e helpers antigos removidos.")
    else:
        print("server.py ja cumpre o contrato WRF zero-dBZ-artificial.")


if __name__ == "__main__":
    main()
