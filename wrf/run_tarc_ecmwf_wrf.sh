#!/usr/bin/env bash
set -euo pipefail

ROOT="${GITHUB_WORKSPACE:-$PWD}"
WRF_RUN_HOURS="${WRF_RUN_HOURS:-6}"
WRF_START_HOUR="${WRF_START_HOUR:-0}"
WRF_END_HOUR="${WRF_END_HOUR:-$WRF_RUN_HOURS}"
CDO_IMAGE="deutscherwetterdienst/regrid:icon-grids"
RAW_DIR="$ROOT/tarc_ecmwf_source_raw"
REG_DIR="$ROOT/tarc_ecmwf_source_regular"
ENV_FILE="$ROOT/tarc_ecmwf_run.env"

# Recorte fonte para o WPS. Os defaults preservam os regionais existentes;
# o CIM pode ampliar a area via variaveis de ambiente.
ECMWF_SOURCE_WEST="${ECMWF_SOURCE_WEST:--65}"
ECMWF_SOURCE_EAST="${ECMWF_SOURCE_EAST:--42}"
ECMWF_SOURCE_SOUTH="${ECMWF_SOURCE_SOUTH:--38}"
ECMWF_SOURCE_NORTH="${ECMWF_SOURCE_NORTH:--18}"

log(){ printf '\n===== %s =====\n' "$*"; }
(( WRF_START_HOUR >= 0 && WRF_END_HOUR > WRF_START_HOUR && WRF_END_HOUR <= 72 )) || {
  echo "Segmento ECMWF invalido: F${WRF_START_HOUR}-F${WRF_END_HOUR}" >&2; exit 2;
}
for VALUE in "$ECMWF_SOURCE_WEST" "$ECMWF_SOURCE_EAST" "$ECMWF_SOURCE_SOUTH" "$ECMWF_SOURCE_NORTH"; do
  [[ "$VALUE" =~ ^-?[0-9]+([.][0-9]+)?$ ]] || { echo "Recorte ECMWF invalido: $VALUE" >&2; exit 2; }
done

rm -rf "$RAW_DIR" "$REG_DIR" "$ENV_FILE"
mkdir -p "$RAW_DIR" "$REG_DIR"
RAW="$RAW_DIR/ecmwf_raw.grib2"
RAW_PRESSURE="$RAW_DIR/ecmwf_raw_pressure.grib2"
RAW_SURFACE="$RAW_DIR/ecmwf_raw_surface.grib2"
SIMPLE_PRESSURE="$RAW_DIR/ecmwf_pressure_simple.grib2"
SIMPLE_SURFACE="$RAW_DIR/ecmwf_surface_simple.grib2"

TARGET_DATE="${FORCE_RUN_DATE:?FORCE_RUN_DATE ausente}"
TARGET_CYCLE="$(printf '%02d' "$((10#${FORCE_RUN_CYCLE:?FORCE_RUN_CYCLE ausente}))")"
export TARGET_DATE TARGET_CYCLE

# IFS Open Data publica as rodadas operacionais 00Z/12Z. Se o ciclo TARC
# for 06Z/18Z, usa a rodada anterior e offset de lead-time para manter os
# horarios validos exatamente alinhados aos do WRF.
mapfile -t IFS_CANDIDATES < <(python3 - <<'PY'
import datetime as dt, os
target=dt.datetime.strptime(os.environ["TARGET_DATE"]+os.environ["TARGET_CYCLE"], "%Y%m%d%H")
for lag in (0,6,12,18,24,30,36):
    source=target-dt.timedelta(hours=lag)
    if source.hour in (0,12):
        print(source.strftime("%Y%m%d %H"), lag)
PY
)

SELECTED_OFFSET=""
log "Tentando ciclos ECMWF IFS compatíveis com a data válida TARC"
for CANDIDATE in "${IFS_CANDIDATES[@]}"; do
  read -r SRC_DATE SRC_CYCLE OFFSET <<< "$CANDIDATE"
  rm -f "$RAW" "$RAW_PRESSURE" "$RAW_SURFACE" "$ENV_FILE"
  echo "ECMWF fallback: ciclo fonte ${SRC_DATE} ${SRC_CYCLE}Z; offset ${OFFSET} h"
  if python3 "$ROOT/wrf/fetch_ecmwf_wrf_input.py" \
      --max-hour "$WRF_END_HOUR" --hour-offset "$OFFSET" \
      --date "$SRC_DATE" --cycle "$SRC_CYCLE" \
      --output "$RAW" --run-env "$ENV_FILE"; then
    SELECTED_OFFSET="$OFFSET"
    break
  fi
  echo "ECMWF ciclo ${SRC_DATE} ${SRC_CYCLE}Z falhou; tentando ciclo anterior"
done
[[ -n "$SELECTED_OFFSET" ]] || { echo "ECMWF IFS indisponivel para todos os ciclos testados" >&2; exit 24; }
source "$ENV_FILE"
ECMWF_STEP_OFFSET="$SELECTED_OFFSET"
RUN_DATE="$TARGET_DATE"
RUN_CYCLE="$TARGET_CYCLE"
export RUN_DATE RUN_CYCLE

echo "ECMWF selecionado: fonte ${SRC_DATE} ${SRC_CYCLE}Z + offset ${ECMWF_STEP_OFFSET} h; datas validas preservadas na base ${RUN_DATE} ${RUN_CYCLE}Z"
test -s "$RAW_PRESSURE"
test -s "$RAW_SURFACE"

log "Reempacotando ECMWF para WPS"
grib_set -r -s packingType=grid_simple "$RAW_PRESSURE" "$SIMPLE_PRESSURE"
grib_set -r -s packingType=grid_simple "$RAW_SURFACE" "$SIMPLE_SURFACE"

docker pull "$CDO_IMAGE"

log "Separando cada forecast hour ECMWF antes do WPS"
echo "Recorte ECMWF: lon ${ECMWF_SOURCE_WEST}..${ECMWF_SOURCE_EAST}; lat ${ECMWF_SOURCE_SOUTH}..${ECMWF_SOURCE_NORTH}"
for H in $(seq "$WRF_START_HOUR" 3 "$WRF_END_HOUR"); do
  SOURCE_H=$((H+ECMWF_STEP_OFFSET))
  printf -v FH '%03d' "$H"
  printf -v SOURCE_FH '%03d' "$SOURCE_H"
  P_STEP="$RAW_DIR/ecmwf_pressure_f${SOURCE_FH}.grib2"
  S_STEP="$RAW_DIR/ecmwf_surface_f${SOURCE_FH}.grib2"
  P_REG="$RAW_DIR/ecmwf_pressure_f${FH}_regional.grib2"
  S_REG="$RAW_DIR/ecmwf_surface_f${FH}_regional.grib2"
  OUT="$REG_DIR/ecmwf_f${FH}.grib2"

  rm -f "$P_STEP" "$S_STEP" "$P_REG" "$S_REG" "$OUT"
  grib_copy -w stepRange="$SOURCE_H" "$SIMPLE_PRESSURE" "$P_STEP" || true
  grib_copy -w stepRange="$SOURCE_H" "$SIMPLE_SURFACE" "$S_STEP" || true
  test -s "$P_STEP" || { echo "ECMWF pressure F${FH} ausente" >&2; exit 25; }
  test -s "$S_STEP" || { echo "ECMWF surface F${FH} ausente" >&2; exit 26; }

  docker run --rm \
    -v "$RAW_DIR:/input" \
    "$CDO_IMAGE" \
    cdo -f grb2 sellonlatbox,${ECMWF_SOURCE_WEST},${ECMWF_SOURCE_EAST},${ECMWF_SOURCE_SOUTH},${ECMWF_SOURCE_NORTH} \
      "/input/$(basename "$P_STEP")" "/input/$(basename "$P_REG")"

  docker run --rm \
    -v "$RAW_DIR:/input" \
    "$CDO_IMAGE" \
    cdo -f grb2 sellonlatbox,${ECMWF_SOURCE_WEST},${ECMWF_SOURCE_EAST},${ECMWF_SOURCE_SOUTH},${ECMWF_SOURCE_NORTH} \
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

log "Preparando TARC WRF com condicoes de contorno ECMWF"
export SOURCE_MODEL=ecmwf
export WRF_RUN_HOURS WRF_START_HOUR WRF_END_HOUR
export SOURCE_DIR="$REG_DIR"
export SOURCE_VTABLE="$ROOT/wrf/Vtable.ECMWF_OPEN"
chmod +x "$ROOT/wrf/run_tarc_wrf_with_source.sh"
exec "$ROOT/wrf/run_tarc_wrf_with_source.sh"
