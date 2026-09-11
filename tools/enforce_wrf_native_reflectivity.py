#!/usr/bin/env python3
from __future__ import annotations

import re
from pathlib import Path

SERVER = Path("server.py")


def main() -> None:
    text = SERVER.read_text(encoding="utf-8")
    original = text

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

    replacement = '''        # Contrato operacional: a camada WRF nunca inventa dBZ a partir de chuva\n        # ou hidrometeoros. A refletividade publicada/exposta vem somente do\n        # REFL_10CM calculado pela microfisica do proprio WRF.\n        if "REFL_10CM" not in dataset:\n            raise RuntimeError("REFL_10CM ausente no wrfout; refletividade sintetica e proibida.")\n        raw_reflectivity = dataset["REFL_10CM"].isel(Time=0).to_numpy()\n        if raw_reflectivity.ndim == 3:\n            reflectivity = np.nanmax(raw_reflectivity, axis=0)\n        elif raw_reflectivity.ndim == 2:\n            reflectivity = raw_reflectivity\n        else:\n            raise RuntimeError(f"REFL_10CM com dimensoes inesperadas: {raw_reflectivity.shape}")\n        reflectivity = np.maximum(0.0, np.nan_to_num(reflectivity, nan=0.0))\n        reflectivity_source = "REFL_10CM_NATIVE"\n'''

    text, count = pattern.subn(replacement, text, count=1)
    if count == 0:
        # Se o bloco ja foi corrigido, apenas valida o contrato.
        if 'reflectivity_source = "REFL_10CM_NATIVE"' not in text:
            raise SystemExit("Bloco WRF mudou e a trava REFL_10CM_NATIVE nao foi encontrada.")

    start = text.find("def build_wrf_cells(")
    end = text.find("\ndef build_gfs_cells(", start)
    if start < 0 or end < 0:
        raise SystemExit("Nao foi possivel localizar build_wrf_cells para validar.")
    block = text[start:end]

    forbidden = (
        "REFL_10CM+hydrometeors_approx",
        'reflectivity_source = "precip_rate"',
        "reflectivity = approximate_reflectivity_dbz",
        "np.vectorize(precipitation_to_dbz)(precip_rate)",
    )
    found = [token for token in forbidden if token in block]
    if found:
        raise SystemExit(f"Refletividade artificial ainda ativa em build_wrf_cells: {found}")
    if 'reflectivity_source = "REFL_10CM_NATIVE"' not in block:
        raise SystemExit("Fonte REFL_10CM_NATIVE nao confirmada em build_wrf_cells.")

    if text != original:
        SERVER.write_text(text, encoding="utf-8")
        print("server.py corrigido: fallback artificial removido.")
    else:
        print("server.py ja cumpre o contrato REFL_10CM_NATIVE.")


if __name__ == "__main__":
    main()
