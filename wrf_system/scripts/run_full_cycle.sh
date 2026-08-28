#!/bin/bash
set -euo pipefail

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODEL="${MODEL:-GFS}"
MODEL_LOWER="$(printf '%s' "$MODEL" | tr '[:upper:]' '[:lower:]')"
FORECAST_HOURS="${FORECAST_HOURS:-72}"
export MODEL FORECAST_HOURS

case "$MODEL_LOWER" in
  gfs) "$BASE_DIR/scripts/download_gfs.sh" ;;
  icon) "$BASE_DIR/scripts/download_icon.sh" ;;
  ecmwf) "$BASE_DIR/scripts/download_ecmwf.sh" ;;
  *) echo "ERRO: modelo nao suportado: $MODEL"; exit 2 ;;
esac

if [ ! -x "$BASE_DIR/wrf/wrf.exe" ] || [ ! -x "$BASE_DIR/wps/metgrid.exe" ]; then
  echo "WRF/WPS ainda nao compilados; preparando uma unica vez."
  "$BASE_DIR/scripts/setup_wrf.sh"
fi

if [ ! -s "$BASE_DIR/data/geog/GEOGRID.TBL" ] && [ -z "$(find "$BASE_DIR/data/geog" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null)" ]; then
  "$BASE_DIR/scripts/download_geog.sh"
fi

"$BASE_DIR/scripts/prepare_namelist.sh"
"$BASE_DIR/scripts/run_wps.sh"
"$BASE_DIR/scripts/run_wrf.sh"

source "$BASE_DIR/data/$MODEL_LOWER/run_info.env"
run_id="${RUN_DATE}${RUN_CYCLE}"
images_dir="$BASE_DIR/output/images/$MODEL_LOWER/$run_id"
python3 "$BASE_DIR/scripts/postprocess_frames.py" \
  --input "$BASE_DIR/output/$MODEL_LOWER" \
  --output "$images_dir" \
  --model "$MODEL_LOWER" \
  --run-id "$run_id"

echo "Rodada ${MODEL} $run_id concluida e pronta para publicacao."
