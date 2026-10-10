#!/usr/bin/env bash
set -euo pipefail

ROOT="${GITHUB_WORKSPACE:-$PWD}"
: "${FORCE_RUN_DATE:?FORCE_RUN_DATE ausente}"
: "${FORCE_RUN_CYCLE:?FORCE_RUN_CYCLE ausente}"
: "${WRF_START_HOUR:?WRF_START_HOUR ausente}"
: "${WRF_END_HOUR:?WRF_END_HOUR ausente}"

TARGET_DATE="$FORCE_RUN_DATE"
TARGET_CYCLE="$(printf '%02d' "$((10#$FORCE_RUN_CYCLE))")"
SOURCE_END_HOUR="${WRF_BOUNDARY_END_HOUR:-$WRF_END_HOUR}"
RAW_DIR="$ROOT/tarc_gfs_source"
BASE="https://noaa-gfs-bdp-pds.s3.amazonaws.com"

(( WRF_START_HOUR >= 0 && WRF_END_HOUR > WRF_START_HOUR && WRF_START_HOUR % 3 == 0 && WRF_END_HOUR % 3 == 0 && SOURCE_END_HOUR >= WRF_END_HOUR && SOURCE_END_HOUR % 3 == 0 && SOURCE_END_HOUR <= 48 )) || {
  echo "Intervalo GFS TARC invalido" >&2
  exit 2
}

mapfile -t CANDIDATES < <(python3 - <<'PY'
import datetime as dt, os
target=dt.datetime.strptime(os.environ["FORCE_RUN_DATE"]+os.environ["FORCE_RUN_CYCLE"].zfill(2), "%Y%m%d%H")
for lag in (0,6,12,18,24,30,36):
    source=target-dt.timedelta(hours=lag)
    print(source.strftime("%Y%m%d %H"), lag)
PY
)

SELECTED_DATE=""
SELECTED_CYCLE=""
SELECTED_LAG=""
for CANDIDATE in "${CANDIDATES[@]}"; do
  read -r DATE CYCLE LAG <<< "$CANDIDATE"
  READY=1
  for H in $(seq "$WRF_START_HOUR" 3 "$SOURCE_END_HOUR"); do
    STEP=$((H+LAG))
    printf -v FH '%03d' "$STEP"
    URL="$BASE/gfs.$DATE/$CYCLE/atmos/gfs.t${CYCLE}z.pgrb2.0p25.f${FH}"
    if ! curl -fsSL --range 0-0 --connect-timeout 15 --max-time 45 -o /dev/null "$URL"; then
      READY=0
      echo "GFS $DATE ${CYCLE}Z F${FH} indisponivel; testando outro ciclo"
      break
    fi
  done
  if (( READY == 1 )); then
    SELECTED_DATE="$DATE"
    SELECTED_CYCLE="$CYCLE"
    SELECTED_LAG="$LAG"
    break
  fi
done

[[ -n "$SELECTED_DATE" ]] || {
  echo "Nenhum ciclo GFS cobre os horarios TARC necessarios" >&2
  exit 22
}

echo "TARC fallback GFS: ciclo fonte ${SELECTED_DATE} ${SELECTED_CYCLE}Z; deslocamento ${SELECTED_LAG} h"
rm -rf "$RAW_DIR"
mkdir -p "$RAW_DIR"

for H in $(seq "$WRF_START_HOUR" 3 "$SOURCE_END_HOUR"); do
  SOURCE_STEP=$((H+SELECTED_LAG))
  printf -v OUT_FH '%03d' "$H"
  printf -v SOURCE_FH '%03d' "$SOURCE_STEP"
  URL="$BASE/gfs.$SELECTED_DATE/$SELECTED_CYCLE/atmos/gfs.t${SELECTED_CYCLE}z.pgrb2.0p25.f${SOURCE_FH}"
  OUT="$RAW_DIR/gfs_f${OUT_FH}.grib2"
  curl -fL --retry 4 --retry-delay 5 --connect-timeout 20 --max-time 1800 -o "$OUT" "$URL"
  test -s "$OUT" || { echo "GFS vazio em F${SOURCE_FH}" >&2; exit 23; }
  COUNT="$(grib_count "$OUT")"
  (( COUNT >= 50 )) || { echo "GFS F${SOURCE_FH} possui poucas mensagens: $COUNT" >&2; exit 24; }
  echo "GFS TARC: valid F${OUT_FH} usando arquivo fonte F${SOURCE_FH} ($COUNT mensagens)"
done

# RUN_DATE/RUN_CYCLE remain the target initialization; GRIB valid times align to it.
export RUN_DATE="$TARGET_DATE" RUN_CYCLE="$TARGET_CYCLE"
export SOURCE_MODEL=gfs
export SOURCE_DIR="$RAW_DIR"
export SOURCE_VTABLE="__WPS_GFS__"
export WRF_DX_METERS=3000 WRF_DY_METERS=3000 WRF_E_WE=361 WRF_E_SN=445
export WRF_TIME_STEP=18 WRF_HISTORY_INTERVAL_MINUTES=60
export WRF_REF_LAT=-28.0 WRF_REF_LON=-53.5 WRF_STAND_LON=-53.5
exec bash "$ROOT/wrf/run_tarc_wrf_with_source.sh"
