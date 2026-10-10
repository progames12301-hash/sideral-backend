#!/usr/bin/env bash
set -euo pipefail

: "$GITHUB_WORKSPACE"
: "$WRF_START_HOUR"
: "$WRF_END_HOUR"

ROOT="$GITHUB_WORKSPACE"
ICON_REGRID_IMAGE="deutscherwetterdienst/regrid@sha256:163aecdbc58f78482d203c98e96a077947ad66490f6bf3c41be4d4b79773e921"
RAW_DIR="$ROOT/cbr_icon_source_raw"
REG_DIR="$ROOT/cbr_icon_source_regular"
REGRID_DIR="$ROOT/cbr_icon_regrid"
HOST_UID="$(id -u)"

ICON_SOURCE_XSIZE=109
ICON_SOURCE_YSIZE=109
ICON_SOURCE_XFIRST=-55.0
ICON_SOURCE_YFIRST=-24.0
ICON_SOURCE_XINC=0.25
ICON_SOURCE_YINC=0.25
SOURCE_END_HOUR="${WRF_BOUNDARY_END_HOUR:-$WRF_END_HOUR}"

(( WRF_START_HOUR >= 0 && WRF_END_HOUR > WRF_START_HOUR && WRF_START_HOUR % 3 == 0 && WRF_END_HOUR % 3 == 0 && WRF_END_HOUR <= 42 && SOURCE_END_HOUR >= WRF_END_HOUR && SOURCE_END_HOUR % 3 == 0 && SOURCE_END_HOUR <= 42 )) || {
  echo "Segmento ICON CBR invalido: F$WRF_START_HOUR-F$WRF_END_HOUR" >&2
  exit 2
}

TARGET_DATE="${FORCE_RUN_DATE:?FORCE_RUN_DATE ausente}"
TARGET_CYCLE="$(printf '%02d' "$((10#${FORCE_RUN_CYCLE:?FORCE_RUN_CYCLE ausente}))")"
SELECTION_ENV="$ROOT/cbr-icon-run.env"
python3 "$ROOT/wrf/select_cbr_icon_run.py" \
  --target-date "$TARGET_DATE" --target-cycle "$TARGET_CYCLE" \
  --start-hour "$WRF_START_HOUR" --end-hour "$SOURCE_END_HOUR" \
  --output "$SELECTION_ENV"
source "$SELECTION_ENV"
RUN_DATE="$TARGET_DATE"
RUN_CYCLE="$TARGET_CYCLE"
echo "ICON CBR selecionado: fonte ${ICON_RUN_DATE} ${ICON_RUN_CYCLE}Z; offset=${ICON_OFFSET}h; alvo=${RUN_DATE} ${RUN_CYCLE}Z F${WRF_START_HOUR}-F${SOURCE_END_HOUR}"
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
python3 - <<'PY'
import math
nx,ny,dx=401,501,4000.0
ref_lat,ref_lon=-10.5,-40.0
truelat1,truelat2=-25.0,-35.0
R=6370000.0
p1,p2,p0=map(math.radians,[truelat1,truelat2,ref_lat])
l0=math.radians(ref_lon)
n=math.log(math.cos(p1)/math.cos(p2))/math.log(math.tan(math.pi/4+p1/2)/math.tan(math.pi/4+p2/2))
F=(math.cos(p1)*(math.tan(math.pi/4+p1/2)**n))/n
rho0=R*F/(math.tan(math.pi/4+p0/2)**n)
def inv(x,y):
    rho=math.hypot(x,rho0-y)
    lat=2*math.atan((R*F/rho)**(1/n))-math.pi/2
    lon=l0+math.atan2(x,rho0-y)/n
    return math.degrees(lat),math.degrees(lon)
hx=(nx-1)*dx/2; hy=(ny-1)*dx/2
pts=[inv(x,y) for x,y in [(-hx,-hy),(hx,-hy),(-hx,hy),(hx,hy)]]
w,e=min(p[1] for p in pts),max(p[1] for p in pts)
south,north=min(p[0] for p in pts),max(p[0] for p in pts)
print(f"CBR WRF 4 KM: lon {w:.4f}..{e:.4f}; lat {south:.4f}..{north:.4f}")
margins=(w-(-55),(-28)-e,south-(-24),3-north)
print("ICON margins:",*[f"{m:.3f}°" for m in margins])
if min(margins)<1.0: raise SystemExit("ICON target grid sem margem de 1 grau")
PY

docker run --rm --user "$HOST_UID:$HOST_UID" -v "$REGRID_DIR:/work" "$ICON_REGRID_IMAGE" cdo gennn,/work/target_grid.txt /data/grids/icon/icon_grid.nc /work/icon_weights.nc

echo "CBR ICON: baixando todos os GRIBs antes do processamento"
for H in $(seq "$WRF_START_HOUR" 3 "$SOURCE_END_HOUR"); do
  printf -v FH '%03d' "$H"
  SOURCE_H=$((H+ICON_OFFSET))
  printf -v SOURCE_FH '%03d' "$SOURCE_H"
  RAW="$RAW_DIR/icon_target_f${FH}_raw.grib2"
  python3 "$ROOT/wrf/fetch_icon_wrf_step.py" --date "$ICON_RUN_DATE" --cycle "$ICON_RUN_CYCLE" --step "$SOURCE_H" --output "$RAW"
  RAW_COUNT="$(grib_count "$RAW")"
  test "$RAW_COUNT" -eq 71 || { echo "ERRO: ICON alvo F$FH (fonte F$SOURCE_FH) contém $RAW_COUNT mensagens; esperado 71." >&2; exit 22; }
done

echo "CBR ICON: download completo para todos os horários; iniciando regradeamento"
for H in $(seq "$WRF_START_HOUR" 3 "$SOURCE_END_HOUR"); do
  printf -v FH '%03d' "$H"
  RAW="$RAW_DIR/icon_target_f${FH}_raw.grib2"
  SIMPLE="$RAW_DIR/icon_target_f${FH}_simple.grib2"
  OUT="$REG_DIR/icon_f${FH}.grib2"
  FI="$RAW_DIR/icon_target_f${FH}_fi.grib2"
  HGT0="$RAW_DIR/icon_target_f${FH}_hgt0.grib2"
  HGT="$RAW_DIR/icon_target_f${FH}_hgt.grib2"
  CHECK="$RAW_DIR/icon_target_f${FH}_hgt_check.grib2"

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
  rm -f "$RAW" "$SIMPLE" "$FI" "$HGT0" "$HGT" "$CHECK"
done

export SOURCE_MODEL=icon RUN_DATE RUN_CYCLE WRF_START_HOUR WRF_END_HOUR WRF_BOUNDARY_END_HOUR="$SOURCE_END_HOUR"
export WRF_HISTORY_INTERVAL_MINUTES=60
export WRF_DX_METERS=4000 WRF_DY_METERS=4000 WRF_E_WE=401 WRF_E_SN=501 WRF_TIME_STEP=20
export WRF_REF_LAT=-10.5 WRF_REF_LON=-40.0 WRF_STAND_LON=-40.0
export SOURCE_DIR="$REG_DIR"
export SOURCE_VTABLE="$ROOT/wrf/Vtable.ICONp"
bash "$ROOT/wrf/run_cbr_wrf_with_source.sh"
