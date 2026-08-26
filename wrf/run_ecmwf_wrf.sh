#!/usr/bin/env bash
set -euo pipefail

ROOT="${GITHUB_WORKSPACE:-$PWD}"
WRF_RUN_HOURS="${WRF_RUN_HOURS:-6}"
CDO_IMAGE="deutscherwetterdienst/regrid:icon-grids"
RAW_DIR="$ROOT/ecmwf_source_raw"
REG_DIR="$ROOT/ecmwf_source_regular"
ENV_FILE="$ROOT/ecmwf_run.env"

log(){ printf '\n===== %s =====\n' "$*"; }
case "$WRF_RUN_HOURS" in 6|42|45) ;; *) echo "WRF_RUN_HOURS precisa ser 6, 42 ou 45" >&2; exit 2;; esac

rm -rf "$RAW_DIR" "$REG_DIR" "$ENV_FILE"
mkdir -p "$RAW_DIR" "$REG_DIR"
RAW="$RAW_DIR/ecmwf_raw.grib2"
RAW_PRESSURE="$RAW_DIR/ecmwf_raw_pressure.grib2"
RAW_SURFACE="$RAW_DIR/ecmwf_raw_surface.grib2"
SIMPLE_PRESSURE="$RAW_DIR/ecmwf_pressure_simple.grib2"
SIMPLE_SURFACE="$RAW_DIR/ecmwf_surface_simple.grib2"
REG_PRESSURE="$REG_DIR/ecmwf_pressure_regional.grib2"
REG_SURFACE="$REG_DIR/ecmwf_surface_regional.grib2"
REGIONAL="$REG_DIR/ecmwf_regional.grib2"

ARGS=(--max-hour "$WRF_RUN_HOURS" --output "$RAW" --run-env "$ENV_FILE")
if [[ -n "${FORCE_RUN_DATE:-}" && -n "${FORCE_RUN_CYCLE:-}" ]]; then
  ARGS+=(--date "$FORCE_RUN_DATE" --cycle "$FORCE_RUN_CYCLE")
fi

log "Baixando atmosfera ECMWF IFS Open Data"
python3 "$ROOT/wrf/fetch_ecmwf_wrf_input.py" "${ARGS[@]}"
source "$ENV_FILE"
export RUN_DATE RUN_CYCLE

echo "ECMWF selecionado: ${RUN_DATE} ${RUN_CYCLE}Z"
test -s "$RAW_PRESSURE"
test -s "$RAW_SURFACE"

# Pressao e superficie sao tratadas separadamente. Misturar os dois conjuntos
# antes do CDO fazia registros superficiais desaparecerem em alguns timesteps.
log "Reempacotando ECMWF para WPS"
grib_set -r -s packingType=grid_simple "$RAW_PRESSURE" "$SIMPLE_PRESSURE"
grib_set -r -s packingType=grid_simple "$RAW_SURFACE" "$SIMPLE_SURFACE"

log "Recortando dominio regional ECMWF sem perder niveis/superficie"
docker pull "$CDO_IMAGE"
docker run --rm \
  -v "$RAW_DIR:/input" \
  -v "$REG_DIR:/output" \
  "$CDO_IMAGE" \
  cdo -f grb2 sellonlatbox,-65,-42,-38,-18 \
    "/input/$(basename "$SIMPLE_PRESSURE")" "/output/$(basename "$REG_PRESSURE")"

docker run --rm \
  -v "$RAW_DIR:/input" \
  -v "$REG_DIR:/output" \
  "$CDO_IMAGE" \
  cdo -f grb2 sellonlatbox,-65,-42,-38,-18 \
    "/input/$(basename "$SIMPLE_SURFACE")" "/output/$(basename "$REG_SURFACE")"

test -s "$REG_PRESSURE"
test -s "$REG_SURFACE"
cat "$REG_PRESSURE" "$REG_SURFACE" > "$REGIONAL"
test -s "$REGIONAL"

rm -f "$RAW" "$RAW_PRESSURE" "$RAW_SURFACE" "$SIMPLE_PRESSURE" "$SIMPLE_SURFACE"
ls -lh "$REG_PRESSURE" "$REG_SURFACE" "$REGIONAL"

log "Rodando WRF 4 km inicializado pelo ECMWF"
export SOURCE_MODEL=ecmwf
export WRF_RUN_HOURS
export SOURCE_DIR="$REG_DIR"
export SOURCE_VTABLE="$ROOT/wrf/Vtable.ECMWF_OPEN"
chmod +x "$ROOT/wrf/run_wrf_with_source.sh"
exec "$ROOT/wrf/run_wrf_with_source.sh"
