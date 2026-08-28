#!/bin/bash
set -euo pipefail

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA_DIR="$BASE_DIR/data/gfs"
mkdir -p "$DATA_DIR"

FORECAST_HOURS="${FORECAST_HOURS:-72}"
FORECAST_INTERVAL="${FORECAST_INTERVAL:-3}"
RUN_DATE="${RUN_DATE:-}"
RUN_CYCLE="${RUN_CYCLE:-}"
GFS_PROD="https://nomads.ncep.noaa.gov/pub/data/nccf/com/gfs/prod"
GFS_FILTER="https://nomads.ncep.noaa.gov/cgi-bin/filter_gfs_0p25.pl"

if [ -z "$RUN_DATE" ] || [ -z "$RUN_CYCLE" ]; then
  for day_shift in 0 1 2; do
    date_check="$(date -u -d "-${day_shift} day" +%Y%m%d)"
    for cycle in 18 12 06 00; do
      probe="$GFS_PROD/gfs.${date_check}/${cycle}/atmos/gfs.t${cycle}z.pgrb2.0p25.f000.idx"
      if curl -fsI --max-time 15 "$probe" >/dev/null 2>&1; then
        RUN_DATE="$date_check"
        RUN_CYCLE="$cycle"
        break 2
      fi
    done
  done
fi

if ! [[ "$RUN_DATE" =~ ^[0-9]{8}$ && "$RUN_CYCLE" =~ ^(00|06|12|18)$ ]]; then
  echo "ERRO: rodada GFS invalida ou indisponivel: ${RUN_DATE}${RUN_CYCLE}"
  exit 1
fi

run_id="${RUN_DATE}${RUN_CYCLE}"
old_run=""
if [ -f "$DATA_DIR/run_info.env" ]; then
  old_date="$(sed -n 's/^RUN_DATE=//p' "$DATA_DIR/run_info.env" | head -n 1 | tr -d '\r[:space:]')"
  old_cycle="$(sed -n 's/^RUN_CYCLE=//p' "$DATA_DIR/run_info.env" | head -n 1 | tr -d '\r[:space:]')"
  old_run="${old_date}${old_cycle}"
fi

if [ -n "$old_run" ] && [ "$old_run" != "$run_id" ]; then
  echo "Removendo GRIBs da rodada anterior: $old_run"
  find "$DATA_DIR" -maxdepth 1 -type f \( -name 'gfs.t*z.pgrb2.0p25.f*' -o -name '*.part' \) -delete
fi

echo "Rodada GFS selecionada: $run_id (0-${FORECAST_HOURS}h)"

levels=(
  lev_surface lev_mean_sea_level lev_2_m_above_ground lev_10_m_above_ground
  lev_0-0.1_m_below_ground lev_0.1-0.4_m_below_ground
  lev_0.4-1_m_below_ground lev_1-2_m_below_ground
)
for pressure in 1000 975 950 925 900 850 800 750 700 650 600 550 500 450 400 350 300 250 200 150 100 70 50 30 20 10; do
  levels+=("lev_${pressure}_mb")
done

variables=(var_HGT var_TMP var_RH var_SPFH var_UGRD var_VGRD var_PRMSL var_PRES var_LAND var_ICEC var_SNOD var_WEASD var_SOILW var_TSOIL)
query_fields=()
for field in "${levels[@]}" "${variables[@]}"; do
  query_fields+=("${field}=on")
done
query="$(IFS='&'; echo "${query_fields[*]}")"

for hour_num in $(seq 0 "$FORECAST_INTERVAL" "$FORECAST_HOURS"); do
  hour="$(printf '%03d' "$hour_num")"
  file="gfs.t${RUN_CYCLE}z.pgrb2.0p25.f${hour}"
  target="$DATA_DIR/$file"
  if [ -s "$target" ]; then
    echo "  Ja existe $file"
    continue
  fi

  remote_dir="%2Fgfs.${RUN_DATE}%2F${RUN_CYCLE}%2Fatmos"
  url="${GFS_FILTER}?dir=${remote_dir}&file=${file}&${query}&subregion=&toplat=-10&leftlon=285&rightlon=330&bottomlat=-48"
  echo "  Baixando $file (recorte Sul/Sudeste)"
  curl -fL --retry 5 --retry-all-errors --retry-delay 10 --connect-timeout 30 \
    -o "${target}.part" "$url"
  if [ ! -s "${target}.part" ]; then
    echo "ERRO: download vazio para $file"
    exit 1
  fi
  mv -f "${target}.part" "$target"
done

cat > "$DATA_DIR/run_info.env" <<EOF
MODEL=GFS
RUN_DATE=${RUN_DATE}
RUN_CYCLE=${RUN_CYCLE}
FORECAST_HOURS=${FORECAST_HOURS}
EOF

echo "Download GFS concluido: $run_id"
