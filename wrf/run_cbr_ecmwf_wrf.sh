#!/usr/bin/env bash
set -euo pipefail

ROOT="${GITHUB_WORKSPACE:-$PWD}"
: "${FORCE_RUN_DATE:?FORCE_RUN_DATE ausente}"
: "${FORCE_RUN_CYCLE:?FORCE_RUN_CYCLE ausente}"
: "${WRF_START_HOUR:?WRF_START_HOUR ausente}"
: "${WRF_END_HOUR:?WRF_END_HOUR ausente}"

CDO_IMAGE="deutscherwetterdienst/regrid@sha256:163aecdbc58f78482d203c98e96a077947ad66490f6bf3c41be4d4b79773e921"
RAW_DIR="$ROOT/cbr_ecmwf_source_raw"
REG_DIR="$ROOT/cbr_ecmwf_source_regular"
ENV_FILE="$ROOT/cbr_ecmwf_run.env"
ECMWF_SOURCE_WEST="-58"
ECMWF_SOURCE_EAST="-25"
ECMWF_SOURCE_SOUTH="-30"
ECMWF_SOURCE_NORTH="5"

(( WRF_START_HOUR >= 0 && WRF_END_HOUR > WRF_START_HOUR && WRF_START_HOUR % 3 == 0 && WRF_END_HOUR % 3 == 0 && WRF_END_HOUR <= 42 )) || {
  echo "Segmento ECMWF CBR invalido: F${WRF_START_HOUR}-F${WRF_END_HOUR}" >&2
  exit 2
}
for VALUE in "$ECMWF_SOURCE_WEST" "$ECMWF_SOURCE_EAST" "$ECMWF_SOURCE_SOUTH" "$ECMWF_SOURCE_NORTH"; do
  [[ "$VALUE" =~ ^-?[0-9]+([.][0-9]+)?$ ]] || { echo "Recorte ECMWF CBR invalido: $VALUE" >&2; exit 2; }
done

python3 -m pip install --disable-pip-version-check -q 'ecmwf-opendata>=0.3'
rm -rf "$RAW_DIR" "$REG_DIR" "$ENV_FILE"
mkdir -p "$RAW_DIR" "$REG_DIR"
RAW="$RAW_DIR/ecmwf_raw.grib2"
RAW_PRESSURE="$RAW_DIR/ecmwf_raw_pressure.grib2"
RAW_SURFACE="$RAW_DIR/ecmwf_raw_surface.grib2"
SIMPLE_PRESSURE="$RAW_DIR/ecmwf_pressure_simple.grib2"
SIMPLE_SURFACE="$RAW_DIR/ecmwf_surface_simple.grib2"

TARGET_DATE="$FORCE_RUN_DATE"
TARGET_CYCLE="$(printf '%02d' "$((10#$FORCE_RUN_CYCLE))")"
export TARGET_DATE TARGET_CYCLE WRF_START_HOUR
mapfile -t IFS_CANDIDATES < <(python3 - <<'PY'
import datetime as dt, os
base=dt.datetime.strptime(os.environ["TARGET_DATE"]+os.environ["TARGET_CYCLE"], "%Y%m%d%H").replace(tzinfo=dt.timezone.utc)
start=base+dt.timedelta(hours=int(os.environ["WRF_START_HOUR"]))
source=start.replace(hour=(start.hour//12)*12,minute=0,second=0,microsecond=0)
for i in range(8):
    run=source-dt.timedelta(hours=12*i)
    offset=int((base-run).total_seconds()//3600)
    print(run.strftime("%Y%m%d %H"), offset)
PY
)

SELECTED_OFFSET=""
log(){ printf '\\n===== %s =====\\n' "$*"; }
log "Fallback CBR ECMWF: procurando ciclo IFS que cubra a janela válida"
for CANDIDATE in "${IFS_CANDIDATES[@]}"; do
  read -r SRC_DATE SRC_CYCLE OFFSET <<< "$CANDIDATE"
  rm -f "$RAW" "$RAW_PRESSURE" "$RAW_SURFACE" "$ENV_FILE"
  echo "ECMWF CBR: fonte ${SRC_DATE} ${SRC_CYCLE}Z; offset base ${OFFSET} h; alvo F${WRF_START_HOUR}-F${WRF_END_HOUR}"
  if python3 "$ROOT/wrf/fetch_ecmwf_wrf_input.py" \\
      --start-hour "$WRF_START_HOUR" --max-hour "$WRF_END_HOUR" --hour-offset "$OFFSET" \\
      --date "$SRC_DATE" --cycle "$SRC_CYCLE" \\
      --output "$RAW" --run-env "$ENV_FILE"; then
    SELECTED_OFFSET="$OFFSET"
    break
  fi
  echo "ECMWF CBR: ciclo ${SRC_DATE} ${SRC_CYCLE}Z falhou; tentando ciclo IFS anterior"
done
[[ -n "$SELECTED_OFFSET" ]] || { echo "ECMWF IFS indisponivel para a janela CBR" >&2; exit 24; }
test -s "$RAW_PRESSURE"
test -s "$RAW_SURFACE"

RUN_DATE="$TARGET_DATE"
RUN_CYCLE="$TARGET_CYCLE"
ECMWF_STEP_OFFSET="$SELECTED_OFFSET"
export RUN_DATE RUN_CYCLE SOURCE_MODEL=ecmwf
echo "ECMWF CBR selecionado: ${SRC_DATE} ${SRC_CYCLE}Z; offset=${ECMWF_STEP_OFFSET}h; timeline alvo=${RUN_DATE} ${RUN_CYCLE}Z"

grib_set -r -s packingType=grid_simple "$RAW_PRESSURE" "$SIMPLE_PRESSURE"
grib_set -r -s packingType=grid_simple "$RAW_SURFACE" "$SIMPLE_SURFACE"
docker pull "$CDO_IMAGE"

for H in $(seq "$WRF_START_HOUR" 3 "$WRF_END_HOUR"); do
  SOURCE_H=$((H+ECMWF_STEP_OFFSET))
  printf -v FH '%03d' "$H"
  printf -v SOURCE_FH '%03d' "$SOURCE_H"
  P_STEP="$RAW_DIR/pressure_source_f${SOURCE_FH}.grib2"
  S_STEP="$RAW_DIR/surface_source_f${SOURCE_FH}.grib2"
  P_REG="$RAW_DIR/pressure_valid_f${FH}.grib2"
  S_REG="$RAW_DIR/surface_valid_f${FH}.grib2"
  GH_IN="$RAW_DIR/gh_source_f${FH}.grib2"
  GH_HEIGHT="$RAW_DIR/gh_height_f${FH}.grib2"
  GH_FIXED="$RAW_DIR/hgt_f${FH}.grib2"
  OUT="$REG_DIR/ecmwf_f${FH}.grib2"

  rm -f "$P_STEP" "$S_STEP" "$P_REG" "$S_REG" "$GH_IN" "$GH_HEIGHT" "$GH_FIXED" "$OUT"
  grib_copy -w stepRange="$SOURCE_H" "$SIMPLE_PRESSURE" "$P_STEP" || true
  grib_copy -w stepRange="$SOURCE_H" "$SIMPLE_SURFACE" "$S_STEP" || true
  test -s "$P_STEP" || { echo "ECMWF pressure source F${SOURCE_FH} ausente" >&2; exit 25; }
  test -s "$S_STEP" || { echo "ECMWF surface source F${SOURCE_FH} ausente" >&2; exit 26; }

  docker run --rm -v "$RAW_DIR:/input" "$CDO_IMAGE" \\
    cdo -f grb2 sellonlatbox,${ECMWF_SOURCE_WEST},${ECMWF_SOURCE_EAST},${ECMWF_SOURCE_SOUTH},${ECMWF_SOURCE_NORTH} \\
    "/input/$(basename "$P_STEP")" "/input/$(basename "$P_REG")"
  docker run --rm -v "$RAW_DIR:/input" "$CDO_IMAGE" \\
    cdo -f grb2 sellonlatbox,${ECMWF_SOURCE_WEST},${ECMWF_SOURCE_EAST},${ECMWF_SOURCE_SOUTH},${ECMWF_SOURCE_NORTH} \\
    "/input/$(basename "$S_STEP")" "/input/$(basename "$S_REG")"

  # IFS fornece geopotencial; converte m²/s² em altura geopotencial (m) para o WPS.
  grib_copy -w discipline=0,parameterCategory=3,parameterNumber=4 "$P_REG" "$GH_IN" || true
  test -s "$GH_IN" || { echo "ECMWF geopotencial ausente no F$FH" >&2; exit 27; }
  docker run --rm -v "$RAW_DIR:/input" "$CDO_IMAGE" \\
    cdo -f grb2 divc,9.80665 "/input/$(basename "$GH_IN")" "/input/$(basename "$GH_HEIGHT")"
  grib_set -r -s discipline=0,parameterCategory=3,parameterNumber=5,typeOfFirstFixedSurface=100 "$GH_HEIGHT" "$GH_FIXED"
  test -s "$GH_FIXED"
  cat "$GH_FIXED" >> "$P_REG"
  cat "$P_REG" "$S_REG" > "$OUT"
  COUNT="$(grib_count "$OUT")"
  test "$COUNT" -gt 20 || { echo "ECMWF CBR F$FH tem poucas mensagens: $COUNT" >&2; exit 28; }
  echo "ECMWF CBR valid F$FH usando source F$SOURCE_FH: $COUNT mensagens"
  rm -f "$P_STEP" "$S_STEP" "$GH_IN" "$GH_HEIGHT" "$GH_FIXED"
done

rm -f "$RAW" "$RAW_PRESSURE" "$RAW_SURFACE" "$SIMPLE_PRESSURE" "$SIMPLE_SURFACE"
ls -lh "$REG_DIR"/ecmwf_f*.grib2

export WRF_DX_METERS=4000 WRF_DY_METERS=4000 WRF_E_WE=401 WRF_E_SN=501 WRF_TIME_STEP=20
export WRF_REF_LAT=-10.5 WRF_REF_LON=-40.0 WRF_STAND_LON=-40.0 WRF_HISTORY_INTERVAL_MINUTES=60 WRF_MPI_PROCS=8
export SOURCE_DIR="$REG_DIR"
export SOURCE_VTABLE="$ROOT/wrf/Vtable.ECMWF_OPEN"
exec bash "$ROOT/wrf/run_cbr_wrf_with_source.sh"
