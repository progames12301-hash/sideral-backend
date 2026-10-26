#!/usr/bin/env bash
set -euo pipefail

ROOT="${GITHUB_WORKSPACE:-$PWD}"
WRF_RUN_HOURS="${WRF_RUN_HOURS:-6}"
WRF_START_HOUR="${WRF_START_HOUR:-0}"
WRF_END_HOUR="${WRF_END_HOUR:-$WRF_RUN_HOURS}"
CDO_IMAGE="deutscherwetterdienst/regrid:icon-grids"
RAW_DIR="$ROOT/ecmwf_source_raw"
REG_DIR="$ROOT/ecmwf_source_regular"
ENV_FILE="$ROOT/ecmwf_run.env"

log(){ printf '\n===== %s =====\n' "$*"; }
(( WRF_START_HOUR >= 0 && WRF_END_HOUR > WRF_START_HOUR && WRF_END_HOUR <= 72 )) || {
  echo "Segmento ECMWF invalido: F${WRF_START_HOUR}-F${WRF_END_HOUR}" >&2; exit 2;
}

rm -rf "$RAW_DIR" "$REG_DIR" "$ENV_FILE"
mkdir -p "$RAW_DIR" "$REG_DIR"
RAW="$RAW_DIR/ecmwf_raw.grib2"
RAW_PRESSURE="$RAW_DIR/ecmwf_raw_pressure.grib2"
RAW_SURFACE="$RAW_DIR/ecmwf_raw_surface.grib2"
SIMPLE_PRESSURE="$RAW_DIR/ecmwf_pressure_simple.grib2"
SIMPLE_SURFACE="$RAW_DIR/ecmwf_surface_simple.grib2"

ARGS=(--max-hour "$WRF_END_HOUR" --output "$RAW" --run-env "$ENV_FILE")
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

log "Reempacotando ECMWF para WPS"
grib_set -r -s packingType=grid_simple "$RAW_PRESSURE" "$SIMPLE_PRESSURE"
grib_set -r -s packingType=grid_simple "$RAW_SURFACE" "$SIMPLE_SURFACE"

docker pull "$CDO_IMAGE"

log "Separando cada forecast hour ECMWF antes do WPS"
for H in $(seq "$WRF_START_HOUR" 3 "$WRF_END_HOUR"); do
  printf -v FH '%03d' "$H"
  P_STEP="$RAW_DIR/ecmwf_pressure_f${FH}.grib2"
  S_STEP="$RAW_DIR/ecmwf_surface_f${FH}.grib2"
  P_REG="$RAW_DIR/ecmwf_pressure_f${FH}_regional.grib2"
  S_REG="$RAW_DIR/ecmwf_surface_f${FH}_regional.grib2"
  OUT="$REG_DIR/ecmwf_f${FH}.grib2"

  rm -f "$P_STEP" "$S_STEP" "$P_REG" "$S_REG" "$OUT"
  grib_copy -w stepRange="$H" "$SIMPLE_PRESSURE" "$P_STEP" || true
  grib_copy -w stepRange="$H" "$SIMPLE_SURFACE" "$S_STEP" || true
  test -s "$P_STEP" || { echo "ECMWF pressure F${FH} ausente" >&2; exit 25; }
  test -s "$S_STEP" || { echo "ECMWF surface F${FH} ausente" >&2; exit 26; }

  docker run --rm \
    -v "$RAW_DIR:/input" \
    "$CDO_IMAGE" \
    cdo -f grb2 sellonlatbox,-57,-36,-29,-10 \
      "/input/$(basename "$P_STEP")" "/input/$(basename "$P_REG")"

  docker run --rm \
    -v "$RAW_DIR:/input" \
    "$CDO_IMAGE" \
    cdo -f grb2 sellonlatbox,-57,-36,-29,-10 \
      "/input/$(basename "$S_STEP")" "/input/$(basename "$S_REG")"

  test -s "$P_REG"
  test -s "$S_REG"
  cat "$P_REG" "$S_REG" > "$OUT"
  test -s "$OUT"

  # Garante que cada arquivo conserva seu passo temporal antes do ungrib.
  COUNT=$(grib_count "$OUT")
  test "$COUNT" -gt 20 || { echo "ECMWF F${FH} tem poucas mensagens: $COUNT" >&2; exit 27; }
  grib_ls -p dataDate,dataTime,stepRange,validityDate,validityTime "$OUT" | head -12

done

rm -f "$RAW" "$RAW_PRESSURE" "$RAW_SURFACE" "$SIMPLE_PRESSURE" "$SIMPLE_SURFACE"
rm -f "$RAW_DIR"/ecmwf_pressure_f*.grib2 "$RAW_DIR"/ecmwf_surface_f*.grib2
ls -lh "$REG_DIR"/ecmwf_f*.grib2

log "Rodando WRF Sudeste 4 km inicializado pelo ECMWF"
export SOURCE_MODEL=ecmwf
export WRF_RUN_HOURS WRF_START_HOUR WRF_END_HOUR
export SOURCE_DIR="$REG_DIR"
export SOURCE_VTABLE="$ROOT/wrf/Vtable.ECMWF_OPEN"
chmod +x "$ROOT/wrf/run_wrf_with_source_sudeste.sh"
exec "$ROOT/wrf/run_wrf_with_source_sudeste.sh"
