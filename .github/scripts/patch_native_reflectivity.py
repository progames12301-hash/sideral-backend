from pathlib import Path

path = Path("server.py")
with path.open("r", encoding="utf-8", newline="") as handle:
    text = handle.read()

newline = "\r\n" if "\r\n" in text else "\n"

old = '''        reflectivity_source = "REFL_10CM" if "REFL_10CM" in dataset else "hydrometeors"
        if "REFL_10CM" in dataset:
            reflectivity = np.maximum(0.0, np.nanmax(dataset["REFL_10CM"].isel(Time=0).to_numpy(), axis=0))
            approx_reflectivity = approximate_reflectivity_dbz(dataset, t2m, precip_rate)
            if np.nanmax(reflectivity) < 20.0 and np.nanmax(approx_reflectivity) > np.nanmax(reflectivity):
                reflectivity_source = "REFL_10CM+hydrometeors_approx"; reflectivity = np.maximum(reflectivity, approx_reflectivity)
        else:
            reflectivity = approximate_reflectivity_dbz(dataset, t2m, precip_rate)
            if np.nanmax(reflectivity) < 1.0: reflectivity_source = "precip_rate"; reflectivity = np.vectorize(precipitation_to_dbz)(precip_rate)
'''.replace("\n", newline)

new = '''        # Refletividade WRF estritamente nativa. Nunca sintetizar dBZ a partir de
        # precipitacao, hidrometeoros, Z-R ou qualquer outro fallback. Se o wrfout
        # nao trouxer REFL_10CM, a API falha explicitamente em vez de fabricar
        # um campo que possa ser confundido com refletividade do modelo.
        if "REFL_10CM" not in dataset:
            raise RuntimeError(
                "WRF sem REFL_10CM nativo; refletividade recusada para evitar geracao artificial."
            )
        reflectivity_source = "REFL_10CM_NATIVE"
        reflectivity_raw = dataset["REFL_10CM"].isel(Time=0).to_numpy()
        if reflectivity_raw.ndim == 3:
            # Produto composto nativo: maximo vertical do REFL_10CM calculado pelo WRF.
            # Nao ha conversao de chuva/hidrometeoros para dBZ neste caminho.
            reflectivity = np.nanmax(reflectivity_raw, axis=0)
        elif reflectivity_raw.ndim == 2:
            reflectivity = reflectivity_raw
        else:
            raise RuntimeError(
                f"Formato inesperado de REFL_10CM: {reflectivity_raw.shape}."
            )
        reflectivity = np.where(
            np.isfinite(reflectivity), np.maximum(0.0, reflectivity), 0.0
        )
'''.replace("\n", newline)

if old not in text:
    if 'reflectivity_source = "REFL_10CM_NATIVE"' in text:
        print("server.py ja esta no modo estritamente nativo.")
    else:
        raise SystemExit("Bloco WRF esperado nao foi encontrado; abortando sem alterar server.py.")
else:
    text = text.replace(old, new, 1)

start = text.index("def build_wrf_cells(")
end = text.index("def build_gfs_cells(", start)
block = text[start:end]

forbidden = (
    "approximate_reflectivity_dbz(",
    'reflectivity_source = "REFL_10CM+hydrometeors_approx"',
    'reflectivity_source = "precip_rate"',
)
found = [token for token in forbidden if token in block]
if found:
    raise SystemExit(f"Fallback artificial ainda presente em build_wrf_cells: {found}")
if 'reflectivity_source = "REFL_10CM_NATIVE"' not in block:
    raise SystemExit("Marcador REFL_10CM_NATIVE nao foi aplicado.")
if 'if "REFL_10CM" not in dataset:' not in block:
    raise SystemExit("Protecao contra ausencia de REFL_10CM nao foi aplicada.")

with path.open("w", encoding="utf-8", newline="") as handle:
    handle.write(text)

print("OK: build_wrf_cells usa apenas REFL_10CM nativo para refletividade.")
