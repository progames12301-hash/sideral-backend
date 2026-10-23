#!/usr/bin/env bash
set -euo pipefail

: "$GITHUB_WORKSPACE"
: "$WRF_START_HOUR"
: "$WRF_END_HOUR"

ROOT="$GITHUB_WORKSPACE"
ICON_REGRID_IMAGE="deutscherwetterdienst/regrid:icon-grids"
RAW_DIR="$ROOT/tarc_icon_source_raw"
REG_DIR="$ROOT/tarc_icon_source_regular"
REGRID_DIR="$ROOT/tarc_icon_regrid"
HOST_UID="$(id -u)"

# Regular ICON source grid has margin around the Sudeste WRF domain.
ICON_SOURCE_XSIZE=101
ICON_SOURCE_YSIZE=95
ICON_SOURCE_XFIRST=-57.0
ICON_SOURCE_YFIRST=-34.0
ICON_SOURCE_XINC=0.25
ICON_SOURCE_YINC=0.25
SOURCE_END_HOUR="${WRF_BOUNDARY_END_HOUR:-$WRF_END_HOUR}"

(( WRF_START_HOUR >= 0 && WRF_END_HOUR > WRF_START_HOUR && WRF_START_HOUR % 3 == 0 && WRF_END_HOUR % 3 == 0 && WRF_END_HOUR <= 48 && SOURCE_END_HOUR >= WRF_END_HOUR && SOURCE_END_HOUR % 3 == 0 && SOURCE_END_HOUR <= 48 )) || {
  echo "Segmento ICON SIDERAL invalido: F$WRF_START_HOUR-F$WRF_END_HOUR" >&2
  exit 2
}

# WRF2: a validade WRF comeca 24 h apos o ciclo ICON fonte.
# O selector valida todos os campos/horarios fonte de F024 a F072 antes da integracao.
if [[ "${WRF2_TOMORROW:-0}" == "1" ]]; then
  TARGET_DATE="${FORCE_RUN_DATE:-${RUN_DATE:-}}"
  TARGET_CYCLE="$(printf '%02d' "$((10#${FORCE_RUN_CYCLE:-${RUN_CYCLE:-0}}))")"
  [[ "$TARGET_DATE" =~ ^[0-9]{8}$ && "$TARGET_CYCLE" =~ ^(00|06|12|18)$ ]] || {
    echo "WRF2 TARC: data/ciclo alvo de amanha invalidos: $TARGET_DATE $TARGET_CYCLE" >&2; exit 21;
  }
  SOURCE_BASE="$(date -u -d "${TARGET_DATE} ${TARGET_CYCLE}:00 UTC -24 hours" +%Y%m%d%H)"
  ICON_SOURCE_DATE="${SOURCE_BASE:0:8}"
  ICON_SOURCE_CYCLE="${SOURCE_BASE:8:2}"
  SELECTION_ENV="$ROOT/tarc-icon-source-run.env"
  python3 "$ROOT/wrf/select_tarc_icon_run.py" \
    --start-hour "$((WRF_START_HOUR+24))" --end-hour "$((SOURCE_END_HOUR+24))" \
    --date "$ICON_SOURCE_DATE" --cycle "$ICON_SOURCE_CYCLE" --output "$SELECTION_ENV"
  source "$SELECTION_ENV"
  ICON_SOURCE_DATE="$RUN_DATE"
  ICON_SOURCE_CYCLE="$RUN_CYCLE"
  ICON_OFFSET=24
  RUN_DATE="$TARGET_DATE"
  RUN_CYCLE="$TARGET_CYCLE"
  printf 'RUN_DATE=%s\nRUN_CYCLE=%s\nSOURCE_MODEL=icon\nWRF2_TOMORROW=1\n' \
    "$RUN_DATE" "$RUN_CYCLE" > "$ROOT/tarc-icon-run.env"
else
  SELECTION_ARGS=(--start-hour "$WRF_START_HOUR" --end-hour "$SOURCE_END_HOUR" --output "$ROOT/tarc-icon-run.env")
  if [[ -n "$FORCE_RUN_DATE" && -n "$FORCE_RUN_CYCLE" ]]; then
    SELECTION_ARGS+=(--date "$FORCE_RUN_DATE" --cycle "$FORCE_RUN_CYCLE")
  fi
  python3 "$ROOT/wrf/select_tarc_icon_run.py" "${SELECTION_ARGS[@]}"
  source "$ROOT/tarc-icon-run.env"
  ICON_SOURCE_DATE="$RUN_DATE"
  ICON_SOURCE_CYCLE="$RUN_CYCLE"
  ICON_OFFSET=0
fi
echo "ICON SIDERAL selecionado: fonte $ICON_SOURCE_DATE $ICON_SOURCE_CYCLE Z offset=$ICON_OFFSET h; validade WRF $RUN_DATE $RUN_CYCLE Z"
rm -rf "$RAW_DIR" "$REG_DIR" "$REGRID_DIR"
mkdir -p "$RAW_DIR" "$REG_DIR" "$REGRID_DIR"

cat > "$REGRID_DIR/target_grid.txt" <<EOF
gridtype = lonlat
xsize = $ICON_SOURCE_XSIZE
ysize = $ICON_SOURCE_YSIZE
xfirst = $ICON_SOURCE_XFIRST
xinc = $ICON_SOURCE_XINC
yfirst = $ICON_SOURCE_YFIRST
yinc = $ICON_SOURCE_YINC
EOF

docker pull "$ICON_REGRID_IMAGE"
docker run --rm --user "$HOST_UID:$HOST_UID" -v "$REGRID_DIR:/work" "$ICON_REGRID_IMAGE" cdo gennn,/work/target_grid.txt /data/grids/icon/icon_grid.nc /work/icon_weights.nc

echo "ICON SIDERAL: baixando atomicamente todos os passos antes do WPS"
for H in $(seq "$WRF_START_HOUR" 3 "$SOURCE_END_HOUR"); do
  printf -v FH "%03d" "$H"
  RAW="$RAW_DIR/icon_f${FH}_raw.grib2"
  SOURCE_H=$((H+ICON_OFFSET))
  python3 "$ROOT/wrf/fetch_icon_wrf_step.py" --date "$ICON_SOURCE_DATE" --cycle "$ICON_SOURCE_CYCLE" --step "$SOURCE_H" --output "$RAW"
  test -s "$RAW" || { echo "GRIB ICON F$FH ausente após download" >&2; exit 22; }
done

echo "ICON SIDERAL: todos os passos brutos baixados; iniciando conversão/regradeamento"
for H in $(seq "$WRF_START_HOUR" 3 "$SOURCE_END_HOUR"); do
  printf -v FH "%03d" "$H"
  RAW="$RAW_DIR/icon_f${FH}_raw.grib2"
  SIMPLE="$RAW_DIR/icon_f${FH}_simple.grib2"
  OUT="$REG_DIR/icon_f${FH}.grib2"
  FI="$RAW_DIR/icon_f${FH}_fi.grib2"
  HGT0="$RAW_DIR/icon_f${FH}_hgt0.grib2"
  HGT="$RAW_DIR/icon_f${FH}_hgt.grib2"
  CHECK="$RAW_DIR/icon_f${FH}_hgt_check.grib2"

  grib_set -r -s packingType=grid_simple "$RAW" "$SIMPLE"
  docker run --rm --user "$HOST_UID:$HOST_UID" \
    -v "$RAW_DIR:/input" -v "$REG_DIR:/output" -v "$REGRID_DIR:/weights" \
    "$ICON_REGRID_IMAGE" cdo -f grb2 remap,/weights/target_grid.txt,/weights/icon_weights.nc \
    "/input/$(basename "$SIMPLE")" "/output/$(basename "$OUT")"

  grib_copy -w discipline=0,parameterCategory=3,parameterNumber=4 "$OUT" "$FI" || true
  test -s "$FI" || { echo "FI do ICON nao encontrado em F$FH" >&2; exit 23; }
  docker run --rm --user "$HOST_UID:$HOST_UID" -v "$RAW_DIR:/input" "$ICON_REGRID_IMAGE" \
    cdo -f grb2 divc,9.80665 "/input/$(basename "$FI")" "/input/$(basename "$HGT0")"
  grib_set -r -s discipline=0,parameterCategory=3,parameterNumber=5,typeOfFirstFixedSurface=100 "$HGT0" "$HGT"
  test -s "$HGT"
  cat "$HGT" >> "$OUT"
  grib_copy -w discipline=0,parameterCategory=3,parameterNumber=5 "$OUT" "$CHECK" || true
  COUNT=0
  [[ -s "$CHECK" ]] && COUNT=$(grib_count "$CHECK")
  test "$COUNT" -ge 10 || { echo "Poucos niveis HGT no ICON F$FH: $COUNT" >&2; exit 24; }
  rm -f "$SIMPLE" "$FI" "$HGT0" "$HGT" "$CHECK"
done

export SOURCE_MODEL=icon RUN_DATE RUN_CYCLE WRF_START_HOUR WRF_END_HOUR WRF_BOUNDARY_END_HOUR="$SOURCE_END_HOUR"
export WRF_HISTORY_INTERVAL_MINUTES=60
export WRF_DX_METERS="${WRF_DX_METERS:-4000}" WRF_DY_METERS="${WRF_DY_METERS:-4000}" WRF_E_WE="${WRF_E_WE:-337}" WRF_E_SN="${WRF_E_SN:-501}" WRF_TIME_STEP="${WRF_TIME_STEP:-20}"
export WRF_REF_LAT="${WRF_REF_LAT:--23.0}" WRF_REF_LON="${WRF_REF_LON:--45.5}" WRF_STAND_LON="${WRF_STAND_LON:--45.5}"
export SOURCE_DIR="$REG_DIR"
export SOURCE_VTABLE="$ROOT/wrf/Vtable.ICONp"
bash "$ROOT/wrf/run_tarc_wrf_with_source.sh"
