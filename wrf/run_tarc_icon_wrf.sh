#!/usr/bin/env bash
set -euo pipefail

: "$GITHUB_WORKSPACE"
: "$WRF_START_HOUR"
: "$WRF_END_HOUR"

ROOT="$GITHUB_WORKSPACE"
ICON_REGRID_IMAGE="deutscherwetterdienst/regrid@sha256:163aecdbc58f78482d203c98e96a077947ad66490f6bf3c41be4d4b79773e921"
RAW_DIR="$ROOT/tarc_icon_source_raw"
REG_DIR="$ROOT/tarc_icon_source_regular"
REGRID_DIR="$ROOT/tarc_icon_regrid"
HOST_UID="$(id -u)"

ICON_SOURCE_XSIZE=93
ICON_SOURCE_YSIZE=81
ICON_SOURCE_XFIRST=-65.0
ICON_SOURCE_YFIRST=-38.0
ICON_SOURCE_XINC=0.25
ICON_SOURCE_YINC=0.25

(( WRF_START_HOUR >= 0 && WRF_END_HOUR > WRF_START_HOUR && WRF_START_HOUR % 3 == 0 && WRF_END_HOUR % 3 == 0 && WRF_END_HOUR <= 48 )) || {
  echo "Segmento ICON TARC invalido: F$WRF_START_HOUR-F$WRF_END_HOUR" >&2
  exit 2
}

if [[ -n "$FORCE_RUN_DATE" && -n "$FORCE_RUN_CYCLE" ]]; then
  RUN_DATE="$FORCE_RUN_DATE"
  RUN_CYCLE="$(printf '%02d' "$((10#$FORCE_RUN_CYCLE))")"
else
  mapfile -t CANDIDATES < <(python3 - <<'PY'
import datetime as dt
now=dt.datetime.now(dt.timezone.utc)
base=now.replace(hour=(now.hour//6)*6, minute=0, second=0, microsecond=0)
for n in range(8):
    x=base-dt.timedelta(hours=6*n)
    print(x.strftime('%Y%m%d %H'))
PY
)
  RUN_DATE=""
  RUN_CYCLE=""
  for C in "${CANDIDATES[@]}"; do
    read -r DATE CYCLE <<< "$C"
    STAMP="$(printf '%s%s' "$DATE" "$CYCLE")"
    FH="$(printf '%03d' "$WRF_END_HOUR")"
    URL="https://opendata.dwd.de/weather/nwp/icon/grib/"$CYCLE"/t_2m/icon_global_icosahedral_single-level_"$STAMP"_"$FH"_T_2M.grib2.bz2"
    if curl --fail --location --retry 5 --retry-delay 5 --silent --show-error --range 0-0 --connect-timeout 15 --max-time 45 -o /dev/null "$URL"; then
      RUN_DATE="$DATE"
      RUN_CYCLE="$CYCLE"
      break
    fi
  done
fi

test -n "$RUN_DATE" || { echo "Nenhuma rodada ICON recente com F$WRF_END_HOUR" >&2; exit 20; }
echo "ICON TARC selecionado: $RUN_DATE $RUN_CYCLE Z"

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

python3 - <<'PY'
ICON_XSIZE=93
ICON_YSIZE=81
ICON_XFIRST=-65.0
ICON_YFIRST=-38.0
ICON_XINC=0.25
ICON_YINC=0.25
WRF_WEST=-59.0
WRF_EAST=-48.0
WRF_SOUTH=-34.0
WRF_NORTH=-22.0
lon_min=ICON_XFIRST
lon_max=ICON_XFIRST + (ICON_XSIZE-1)*ICON_XINC
lat_min=ICON_YFIRST
lat_max=ICON_YFIRST + (ICON_YSIZE-1)*ICON_YINC
print(f"ICON lonlat target: {ICON_XSIZE}x{ICON_YSIZE}; dx={ICON_XINC:.2f}°, dy={ICON_YINC:.2f}°")
print(f"ICON lonlat box: lon={lon_min:.2f}..{lon_max:.2f}, lat={lat_min:.2f}..{lat_max:.2f}")
print("WRF TARC 3 KM box: lon=-59.00..-48.00, lat=-34.00..-22.00; grid=361x445; dx=3000 m; dy=3000 m")
margins={
    "west": WRF_WEST - lon_min,
    "east": lon_max - WRF_EAST,
    "south": WRF_SOUTH - lat_min,
    "north": lat_max - WRF_NORTH,
}
print("Margens ICON→WRF: " + ", ".join(f"{k}={v:.2f}°" for k,v in margins.items()))
if min(margins.values()) < 1.0:
    raise SystemExit("Grade ICON lonlat nao cobre o dominio WRF com margem minima de 1 grau em todos os lados")
PY

docker pull "$ICON_REGRID_IMAGE"
docker run --rm --user "$HOST_UID:$HOST_UID" -v "$REGRID_DIR:/work" "$ICON_REGRID_IMAGE" cdo gennn,/work/target_grid.txt /data/grids/icon/icon_grid.nc /work/icon_weights.nc

for H in $(seq "$WRF_START_HOUR" 3 "$WRF_END_HOUR"); do
  printf -v FH '%03d' "$H"
  RAW="$RAW_DIR/icon_f"$FH"_raw.grib2"
  SIMPLE="$RAW_DIR/icon_f"$FH"_simple.grib2"
  OUT="$REG_DIR/icon_f"$FH".grib2"
  FI="$RAW_DIR/icon_f"$FH"_fi.grib2"
  HGT0="$RAW_DIR/icon_f"$FH"_hgt0.grib2"
  HGT="$RAW_DIR/icon_f"$FH"_hgt.grib2"
  CHECK="$RAW_DIR/icon_f"$FH"_hgt_check.grib2"

  python3 "$ROOT/wrf/fetch_icon_wrf_step.py" --date "$RUN_DATE" --cycle "$RUN_CYCLE" --step "$H" --output "$RAW"
  ICON_COUNT="$(grib_count "$RAW")"
  if [[ "$ICON_COUNT" -ne 71 ]]; then
    echo "ICON TARC F$FH: esperado exatamente 71 mensagens GRIB2 (13 niveis x 5 campos + 6 superficie), encontrado $ICON_COUNT" >&2
    exit 25
  fi
  echo "ICON TARC F$FH: 71/71 mensagens GRIB2 validadas"
  grib_set -r -s packingType=grid_simple "$RAW" "$SIMPLE"
  docker run --rm --user "$HOST_UID:$HOST_UID"     -v "$RAW_DIR:/input" -v "$REG_DIR:/output" -v "$REGRID_DIR:/weights"     "$ICON_REGRID_IMAGE" cdo -f grb2 remap,/weights/target_grid.txt,/weights/icon_weights.nc "/input/$(basename "$SIMPLE")" "/output/$(basename "$OUT")"

  grib_copy -w discipline=0,parameterCategory=3,parameterNumber=4 "$OUT" "$FI" || true
  test -s "$FI" || { echo "FI do ICON nao encontrado em F$FH" >&2; exit 23; }
  docker run --rm --user "$HOST_UID:$HOST_UID" -v "$RAW_DIR:/input" "$ICON_REGRID_IMAGE" cdo -f grb2 divc,9.80665 "/input/$(basename "$FI")" "/input/$(basename "$HGT0")"
  grib_set -r -s discipline=0,parameterCategory=3,parameterNumber=5,typeOfFirstFixedSurface=100 "$HGT0" "$HGT"
  test -s "$HGT"
  cat "$HGT" >> "$OUT"
  grib_copy -w discipline=0,parameterCategory=3,parameterNumber=5 "$OUT" "$CHECK" || true
  COUNT=0
  [[ -s "$CHECK" ]] && COUNT=$(grib_count "$CHECK")
  test "$COUNT" -ge 10 || { echo "Poucos niveis HGT no ICON F$FH: $COUNT" >&2; exit 24; }
  rm -f "$RAW" "$SIMPLE" "$FI" "$HGT0" "$HGT" "$CHECK"
done

export SOURCE_MODEL=icon RUN_DATE RUN_CYCLE WRF_START_HOUR WRF_END_HOUR
export WRF_HISTORY_INTERVAL_MINUTES=60
export WRF_DX_METERS=3000 WRF_DY_METERS=3000 WRF_E_WE=361 WRF_E_SN=445 WRF_TIME_STEP=18
export WRF_REF_LAT=-28.0 WRF_REF_LON=-53.5 WRF_STAND_LON=-53.5
export SOURCE_DIR="$REG_DIR"
export SOURCE_VTABLE="$ROOT/wrf/Vtable.ICONp"
bash "$ROOT/wrf/run_tarc_wrf_with_source.sh"
