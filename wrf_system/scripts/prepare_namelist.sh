#!/bin/bash
set -euo pipefail

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODEL="${MODEL:-GFS}"
MODEL_LOWER="$(printf '%s' "$MODEL" | tr '[:upper:]' '[:lower:]')"
DATA_DIR="$BASE_DIR/data/$MODEL_LOWER"
WPS_DIR="$BASE_DIR/wps"
WRF_DIR="$BASE_DIR/wrf"

if [ ! -f "$DATA_DIR/run_info.env" ]; then
  echo "ERRO: metadados da rodada ausentes em $DATA_DIR/run_info.env"
  exit 1
fi
source "$DATA_DIR/run_info.env"

RUN_DATE="$(printf '%s' "$RUN_DATE" | tr -d '\r\n[:space:]')"
RUN_CYCLE="$(printf '%s' "$RUN_CYCLE" | tr -d '\r\n[:space:]')"
FORECAST_HOURS="${FORECAST_HOURS:-72}"
RUN_HOURS="${RUN_HOURS:-$FORECAST_HOURS}"
RUN_MINUTES="${RUN_MINUTES:-0}"
HISTORY_INTERVAL="${HISTORY_INTERVAL:-60}"

run_year="${RUN_DATE:0:4}"
run_month="${RUN_DATE:4:2}"
run_day="${RUN_DATE:6:2}"
base_time="${run_year}-${run_month}-${run_day} ${RUN_CYCLE}:00:00 UTC"
start_date="${run_year}-${run_month}-${run_day}_${RUN_CYCLE}:00:00"
end_date="$(date -u -d "${base_time} + ${FORECAST_HOURS} hours" +'%Y-%m-%d_%H:00:00')"

if [ "$MODEL_LOWER" = "icon" ]; then
  num_levels="${NUM_METGRID_LEVELS:-15}"
  soil_levels="${NUM_METGRID_SOIL_LEVELS:-8}"
  p_top="${P_TOP_REQUESTED:-10000}"
  interval="${INTERVAL_SECONDS:-3600}"
else
  num_levels="${NUM_METGRID_LEVELS:-34}"
  soil_levels="${NUM_METGRID_SOIL_LEVELS:-4}"
  p_top="${P_TOP_REQUESTED:-5000}"
  interval="${INTERVAL_SECONDS:-10800}"
fi

mkdir -p "$WPS_DIR" "$WRF_DIR"
sed \
  -e "s|%START_DATE%|${start_date}|g" \
  -e "s|%END_DATE%|${end_date}|g" \
  -e "s|%INTERVAL_SECONDS%|${interval}|g" \
  -e "s|%GEOG_DATA_PATH%|${BASE_DIR}/data/geog|g" \
  "$BASE_DIR/config/namelist.template.wps" > "$WPS_DIR/namelist.wps"

end_year="${end_date:0:4}"; end_month="${end_date:5:2}"; end_day="${end_date:8:2}"; end_hour="${end_date:11:2}"
sed \
  -e "s|%START_YEAR%|${run_year}|g" -e "s|%START_MONTH%|${run_month}|g" \
  -e "s|%START_DAY%|${run_day}|g" -e "s|%START_HOUR%|${RUN_CYCLE}|g" \
  -e "s|%RUN_HOURS%|${RUN_HOURS}|g" -e "s|%RUN_MINUTES%|${RUN_MINUTES}|g" \
  -e "s|%HISTORY_INTERVAL%|${HISTORY_INTERVAL}|g" -e "s|%INTERVAL_SECONDS%|${interval}|g" \
  -e "s|%NUM_METGRID_LEVELS%|${num_levels}|g" -e "s|%NUM_METGRID_SOIL_LEVELS%|${soil_levels}|g" \
  -e "s|%P_TOP_REQUESTED%|${p_top}|g" -e "s|%END_YEAR%|${end_year}|g" \
  -e "s|%END_MONTH%|${end_month}|g" -e "s|%END_DAY%|${end_day}|g" -e "s|%END_HOUR%|${end_hour}|g" \
  "$BASE_DIR/config/namelist.template.input" > "$WRF_DIR/namelist.input"

echo "Namelists prontos para ${MODEL} ${RUN_DATE}${RUN_CYCLE}: ${RUN_HOURS}h, saida a cada ${HISTORY_INTERVAL} min."
