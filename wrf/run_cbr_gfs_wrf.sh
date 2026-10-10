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
RAW_DIR="$ROOT/cbr_gfs_source"
BASE="https://noaa-gfs-bdp-pds.s3.amazonaws.com"

(( WRF_START_HOUR >= 0 && WRF_END_HOUR > WRF_START_HOUR && WRF_START_HOUR % 3 == 0 && WRF_END_HOUR % 3 == 0 && SOURCE_END_HOUR >= WRF_END_HOUR && SOURCE_END_HOUR % 3 == 0 && SOURCE_END_HOUR <= 42 )) || {
  echo "Intervalo GFS CBR invalido" >&2
  exit 2
}
export TARGET_DATE TARGET_CYCLE WRF_START_HOUR
mapfile -t CANDIDATES < <(python3 - <<'PY'
import datetime as dt, os
base=dt.datetime.strptime(os.environ["TARGET_DATE"]+os.environ["TARGET_CYCLE"], "%Y%m%d%H").replace(tzinfo=dt.timezone.utc)
start=base+dt.timedelta(hours=int(os.environ["WRF_START_HOUR"]))
candidate=start.replace(hour=(start.hour//6)*6,minute=0,second=0,microsecond=0)
for back in range(8):
    run=candidate-dt.timedelta(hours=6*back)
    offset=int((base-run).total_seconds()//3600)
    print(run.strftime("%Y%m%d %H"), offset)
PY
)

SELECTED_DATE=""
SELECTED_CYCLE=""
SELECTED_OFFSET=""
for CANDIDATE in "${CANDIDATES[@]}"; do
  read -r DATE CYCLE OFFSET <<< "$CANDIDATE"
  READY=1
  for H in $(seq "$WRF_START_HOUR" 3 "$SOURCE_END_HOUR"); do
    STEP=$((H+OFFSET))
    if (( STEP < 0 )); then READY=0; break; fi
    printf -v FH '%03d' "$STEP"
    URL="$BASE/gfs.$DATE/$CYCLE/atmos/gfs.t${CYCLE}z.pgrb2.0p25.f${FH}"
    if ! curl -fsSL --range 0-0 --connect-timeout 15 --max-time 45 -o /dev/null "$URL"; then
      READY=0
      echo "GFS CBR $DATE ${CYCLE}Z F${FH} indisponivel; testando outro ciclo"
      break
    fi
  done
  if (( READY == 1 )); then
    SELECTED_DATE="$DATE"
    SELECTED_CYCLE="$CYCLE"
    SELECTED_OFFSET="$OFFSET"
    break
  fi
done

[[ -n "$SELECTED_DATE" ]] || {
  echo "Nenhum ciclo GFS cobre a janela valida CBR" >&2
  exit 22
}
echo "Fallback CBR GFS: fonte ${SELECTED_DATE} ${SELECTED_CYCLE}Z; offset base=${SELECTED_OFFSET}h; alvo F${WRF_START_HOUR}-F${SOURCE_END_HOUR}"
rm -rf "$RAW_DIR"
mkdir -p "$RAW_DIR"

for H in $(seq "$WRF_START_HOUR" 3 "$SOURCE_END_HOUR"); do
  SOURCE_STEP=$((H+SELECTED_OFFSET))
  printf -v OUT_FH '%03d' "$H"
  printf -v SOURCE_FH '%03d' "$SOURCE_STEP"
  URL="$BASE/gfs.$SELECTED_DATE/$SELECTED_CYCLE/atmos/gfs.t${SELECTED_CYCLE}z.pgrb2.0p25.f${SOURCE_FH}"
  OUT="$RAW_DIR/gfs_valid_f${OUT_FH}.grib2"
  curl -fL --retry 4 --retry-delay 5 --connect-timeout 20 --max-time 1800 -o "$OUT.part" "$URL"
  test -s "$OUT.part" || { echo "GFS CBR vazio em source F${SOURCE_FH}" >&2; exit 23; }
  COUNT="$(grib_count "$OUT.part")"
  (( COUNT >= 50 )) || { echo "GFS CBR source F${SOURCE_FH} tem poucas mensagens: $COUNT" >&2; exit 24; }
  mv -f "$OUT.part" "$OUT"
  echo "GFS CBR: valid F${OUT_FH} usa fonte F${SOURCE_FH} ($COUNT mensagens)"
done

RUN_DATE="$TARGET_DATE"
RUN_CYCLE="$TARGET_CYCLE"
export RUN_DATE RUN_CYCLE SOURCE_MODEL=gfs
export SOURCE_DIR="$RAW_DIR"
export SOURCE_VTABLE="__WPS_GFS__"
export WRF_DX_METERS=4000 WRF_DY_METERS=4000 WRF_E_WE=401 WRF_E_SN=501 WRF_TIME_STEP=20
export WRF_REF_LAT=-10.5 WRF_REF_LON=-40.0 WRF_STAND_LON=-40.0 WRF_HISTORY_INTERVAL_MINUTES=60 WRF_MPI_PROCS=8
exec bash "$ROOT/wrf/run_cbr_wrf_with_source.sh"
