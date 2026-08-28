#!/bin/bash
set -euo pipefail

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODEL="${MODEL:-GFS}"
MODEL_LOWER="$(printf '%s' "$MODEL" | tr '[:upper:]' '[:lower:]')"
DATA_DIR="$BASE_DIR/data/$MODEL_LOWER"
WPS_DIR="$BASE_DIR/wps"
LOG_DIR="$BASE_DIR/logs"
mkdir -p "$LOG_DIR"
source "$DATA_DIR/run_info.env"

cd "$WPS_DIR"
rm -f geo_em.d0* met_em.d0* GRIBFILE.* FILE:*
./geogrid.exe > "$LOG_DIR/geogrid.log" 2>&1

if [ "$MODEL_LOWER" = "gfs" ]; then
  ln -sf ungrib/Variable_Tables/Vtable.GFS Vtable
  INPUT_FILES=()
  for hour in $(seq 0 "${FORECAST_INTERVAL:-3}" "${FORECAST_HOURS:-72}"); do
    label="$(printf '%03d' "$hour")"
    file="$DATA_DIR/gfs.t${RUN_CYCLE}z.pgrb2.0p25.f${label}"
    test -s "$file" || { echo "ERRO: GFS ausente: $file"; exit 1; }
    INPUT_FILES+=("$file")
  done
else
  echo "ERRO: run_wps do CI esta validado para GFS."
  exit 2
fi

./link_grib.csh "${INPUT_FILES[@]}" > "$LOG_DIR/link_grib.log" 2>&1
./ungrib.exe > "$LOG_DIR/ungrib.log" 2>&1
./metgrid.exe > "$LOG_DIR/metgrid.log" 2>&1
echo "WPS concluido."
