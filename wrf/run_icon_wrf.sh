#!/usr/bin/env bash
set -euo pipefail

ROOT="${GITHUB_WORKSPACE:-$PWD}"
WRF_RUN_HOURS="${WRF_RUN_HOURS:-6}"
WRF_START_HOUR="${WRF_START_HOUR:-0}"
WRF_END_HOUR="${WRF_END_HOUR:-$WRF_RUN_HOURS}"
ICON_REGRID_IMAGE="deutscherwetterdienst/regrid:icon-grids"
RAW_DIR="$ROOT/icon_source_raw"
REG_DIR="$ROOT/icon_source_regular"
REGRID_DIR="$ROOT/icon_regrid"
HOST_UID="$(id -u)"
HOST_GID="$(id -g)"
ICON_SOURCE_XSIZE="${ICON_SOURCE_XSIZE:-93}"
ICON_SOURCE_YSIZE="${ICON_SOURCE_YSIZE:-81}"
ICON_SOURCE_XFIRST="${ICON_SOURCE_XFIRST:--65.0}"
ICON_SOURCE_YFIRST="${ICON_SOURCE_YFIRST:--38.0}"
ICON_SOURCE_XINC="${ICON_SOURCE_XINC:-0.25}"
ICON_SOURCE_YINC="${ICON_SOURCE_YINC:-0.25}"
log(){ printf '\n===== %s =====\n' "$*"; }
(( WRF_START_HOUR >= 0 && WRF_END_HOUR > WRF_START_HOUR && WRF_END_HOUR <= 72 )) || { echo "Segmento ICON invalido: F${WRF_START_HOUR}-F${WRF_END_HOUR}" >&2; exit 2; }
[[ "$ICON_SOURCE_XSIZE" =~ ^[0-9]+$ && "$ICON_SOURCE_YSIZE" =~ ^[0-9]+$ ]] || exit 2
for VALUE in "$ICON_SOURCE_XFIRST" "$ICON_SOURCE_YFIRST" "$ICON_SOURCE_XINC" "$ICON_SOURCE_YINC"; do [[ "$VALUE" =~ ^-?[0-9]+([.][0-9]+)?$ ]] || exit 2; done
if [[ -n "${FORCE_RUN_DATE:-}" && -n "${FORCE_RUN_CYCLE:-}" ]]; then RUN_DATE="$FORCE_RUN_DATE"; RUN_CYCLE="$(printf '%02d' "$((10#$FORCE_RUN_CYCLE))")"; else
  mapfile -t CANDIDATES < <(python3 - <<'PY'
import datetime as dt
now=dt.datetime.now(dt.timezone.utc); base=now.replace(hour=(now.hour//6)*6,minute=0,second=0,microsecond=0)
for n in range(8):
 x=base-dt.timedelta(hours=6*n); print(x.strftime('%Y%m%d %H'))
PY
)
  RUN_DATE=""; RUN_CYCLE=""
  for C in "${CANDIDATES[@]}"; do read -r DATE CYCLE <<< "$C"; URL="https://opendata.dwd.de/weather/nwp/icon/grib/${CYCLE}/t_2m/icon_global_icosahedral_single-level_${DATE}${CYCLE}_$(printf '%03d' "$WRF_END_HOUR")_T_2M.grib2.bz2"; if curl -fsSL --range 0-0 --connect-timeout 15 --max-time 45 -o /dev/null "$URL"; then RUN_DATE="$DATE"; RUN_CYCLE="$CYCLE"; break; fi; done
  test -n "$RUN_DATE" || { echo "Nenhuma rodada ICON recente com F${WRF_END_HOUR}" >&2; exit 20; }
fi
echo "ICON selecionado: ${RUN_DATE} ${RUN_CYCLE}Z"
rm -rf "$RAW_DIR" "$REG_DIR" "$REGRID_DIR"; mkdir -p "$RAW_DIR" "$REG_DIR" "$REGRID_DIR"
cat > "$REGRID_DIR/target_grid.txt" <<EOF
gridtype = lonlat
xsize = ${ICON_SOURCE_XSIZE}
ysize = ${ICON_SOURCE_YSIZE}
xfirst = ${ICON_SOURCE_XFIRST}
xinc = ${ICON_SOURCE_XINC}
yfirst = ${ICON_SOURCE_YFIRST}
yinc = ${ICON_SOURCE_YINC}
EOF
docker pull "$ICON_REGRID_IMAGE"
docker run --rm --user "$HOST_UID:$HOST_GID" -v "$REGRID_DIR:/work" "$ICON_REGRID_IMAGE" cdo gennn,/work/target_grid.txt /data/grids/icon/icon_grid.nc /work/icon_weights.nc
test -s "$REGRID_DIR/icon_weights.nc"
for H in $(seq "$WRF_START_HOUR" 3 "$WRF_END_HOUR"); do
  printf -v FH '%03d' "$H"; RAW="$RAW_DIR/icon_f${FH}_raw.grib2"; SIMPLE="$RAW_DIR/icon_f${FH}_simple.grib2"; OUT="$REG_DIR/icon_f${FH}.grib2"; FI="$RAW_DIR/icon_f${FH}_fi.grib2"; HGT0="$RAW_DIR/icon_f${FH}_hgt_values.grib2"; HGT="$RAW_DIR/icon_f${FH}_hgt.grib2"; HGT_CHECK="$RAW_DIR/icon_f${FH}_hgt_check.grib2"
  python3 "$ROOT/wrf/fetch_icon_wrf_step.py" --date "$RUN_DATE" --cycle "$RUN_CYCLE" --step "$H" --output "$RAW"
  grib_set -r -s packingType=grid_simple "$RAW" "$SIMPLE"
  docker run --rm --user "$HOST_UID:$HOST_GID" -v "$RAW_DIR:/input" -v "$REG_DIR:/output" -v "$REGRID_DIR:/weights" "$ICON_REGRID_IMAGE" cdo -f grb2 remap,/weights/target_grid.txt,/weights/icon_weights.nc "/input/$(basename "$SIMPLE")" "/output/$(basename "$OUT")"
  test -s "$OUT"
  rm -f "$FI" "$HGT0" "$HGT" "$HGT_CHECK"
  grib_copy -w discipline=0,parameterCategory=3,parameterNumber=4 "$OUT" "$FI" || true
  if [[ -s "$FI" ]]; then
    docker run --rm --user "$HOST_UID:$HOST_GID" -v "$RAW_DIR:/input" "$ICON_REGRID_IMAGE" cdo -f grb2 divc,9.80665 "/input/$(basename "$FI")" "/input/$(basename "$HGT0")"
    grib_set -r -s discipline=0,parameterCategory=3,parameterNumber=5,typeOfFirstFixedSurface=100 "$HGT0" "$HGT"; test -s "$HGT"; cat "$HGT" >> "$OUT"
  else echo "FI do ICON nao encontrado em F${FH}; abortando" >&2; exit 23; fi
  grib_copy -w discipline=0,parameterCategory=3,parameterNumber=5 "$OUT" "$HGT_CHECK" || true
  HGT_COUNT=0; [[ -s "$HGT_CHECK" ]] && HGT_COUNT=$(grib_count "$HGT_CHECK"); test "${HGT_COUNT:-0}" -ge 10 || { echo "Poucos niveis HGT no ICON F${FH}: ${HGT_COUNT:-0}" >&2; exit 24; }
  rm -f "$RAW" "$SIMPLE" "$FI" "$HGT0" "$HGT" "$HGT_CHECK"
done
export SOURCE_MODEL=icon RUN_DATE RUN_CYCLE WRF_RUN_HOURS WRF_START_HOUR WRF_END_HOUR SOURCE_DIR="$REG_DIR" SOURCE_VTABLE="$ROOT/wrf/Vtable.ICONp"
chmod +x "$ROOT/wrf/run_wrf_with_source.sh"
exec "$ROOT/wrf/run_wrf_with_source.sh"
