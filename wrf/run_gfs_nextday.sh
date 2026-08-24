#!/usr/bin/env bash
set -euo pipefail

: "${FORCE_RUN_DATE:?FORCE_RUN_DATE ausente}"
: "${FORCE_RUN_CYCLE:?FORCE_RUN_CYCLE ausente}"

BASE_SCRIPT="wrf/run_gfs_test.sh"
python3 - <<'PY'
from pathlib import Path
import re

path = Path('wrf/run_gfs_test.sh')
text = path.read_text(encoding='utf-8')
if 'do_radar_ref = 1' not in text:
    raise SystemExit('do_radar_ref=1 nao encontrado no wrf-runner')

selection = r'''log "Usando a mesma rodada GFS para o proximo dia"
RUN_DATE="${FORCE_RUN_DATE:?FORCE_RUN_DATE ausente}"
RUN_CYCLE="${FORCE_RUN_CYCLE:?FORCE_RUN_CYCLE ausente}"
BASE_URL="https://noaa-gfs-bdp-pds.s3.amazonaws.com/gfs.${RUN_DATE}/${RUN_CYCLE}/atmos"
echo "Rodada fixa: ${RUN_DATE} ${RUN_CYCLE}Z"

F042_URL="$BASE_URL/gfs.t${RUN_CYCLE}z.pgrb2.0p25.f042"
READY=0
for TRY in $(seq 1 18); do
  if curl -fsSL --range 0-0 --connect-timeout 15 --max-time 45 -o /dev/null "$F042_URL"; then
    READY=1
    echo "F042 disponivel na tentativa $TRY"
    break
  fi
  echo "F042 ainda nao disponivel; aguardando 5 minutos ($TRY/18)"
  sleep 300
done
if [[ "$READY" -ne 1 ]]; then
  echo "F042 nao ficou disponivel para ${RUN_DATE} ${RUN_CYCLE}Z." >&2
  exit 22
fi
'''
pattern = re.compile(
    r'log "Escolhendo rodada GFS mais recente com F006 disponível".*?'
    r'(?=echo "RUN_DATE=\$RUN_DATE")',
    re.S,
)
text, count = pattern.subn(selection + '\n', text, count=1)
if count != 1:
    raise SystemExit(f'Nao foi possivel fixar a rodada GFS: {count}')

text = text.replace('+6 hours', '+42 hours')
text = text.replace('run_hours = 6,', 'run_hours = 42,')

download = r'''log "Baixando GFS F000-F042 de 3 em 3 horas"
mkdir -p "$WORK/gfs"
for H in $(seq 0 3 42); do
  printf -v FH "%03d" "$H"
  FILE="gfs.t${RUN_CYCLE}z.pgrb2.0p25.f${FH}"
  curl -fL --retry 4 --retry-delay 5 --connect-timeout 20 --max-time 900 \
    -o "$WORK/gfs/$FILE" "$BASE_URL/$FILE"
  ls -lh "$WORK/gfs/$FILE"
done
'''
text, count = re.subn(
    r'log "Baixando GFS F000 F003 F006".*?done\n',
    download + '\n',
    text,
    count=1,
    flags=re.S,
)
if count != 1:
    raise SystemExit(f'Nao foi possivel ampliar downloads GFS: {count}')

geog = r'''log "Baixando e validando geografia WPS low-res"
rm -rf "$WORK/geog_extract" "$WORK/WPS_GEOG"
mkdir -p "$WORK/geog_extract"
curl -fL --retry 3 --connect-timeout 20 --max-time 900 \
  -o "$WORK/geog.tar.gz" \
  https://www2.mmm.ucar.edu/wrf/src/wps_files/geog_low_res_mandatory.tar.gz
tar -xzf "$WORK/geog.tar.gz" -C "$WORK/geog_extract"
TOPO_INDEX="$(find "$WORK/geog_extract" -type f -path '*/topo_gmted2010_5m/index' -print -quit)"
test -n "$TOPO_INDEX" || { echo "topo_gmted2010_5m ausente" >&2; exit 31; }
GEOG_ROOT="$(dirname "$(dirname "$TOPO_INDEX")")"
mkdir -p "$WORK/WPS_GEOG"
cp -a "$GEOG_ROOT/." "$WORK/WPS_GEOG/"
REQUIRED_GEOG=(topo_gmted2010_5m modis_landuse_20class_5m_with_lakes soiltype_top_5m soiltype_bot_5m greenfrac_fpar_modis_5m soiltemp_1deg albedo_modis maxsnowalb_modis lai_modis_10m)
for DATASET in "${REQUIRED_GEOG[@]}"; do
  test -f "$WORK/WPS_GEOG/$DATASET/index" || { echo "GEOG AUSENTE: $DATASET" >&2; exit 32; }
done
echo "WPS_GEOG VALIDADO COMPLETAMENTE"
'''
pattern = re.compile(
    r'log "Baixando e extraindo geografia WPS low-res".*?'
    r'(?=cat > "\$WORK/namelist\.wps" <<EOF)',
    re.S,
)
text, count = pattern.subn(geog + '\n\n', text, count=1)
if count != 1:
    raise SystemExit(f'Patch WPS_GEOG nao aplicado: {count}')

links = r'''log "Preparando nomes GRIBFILE F000-F042"
LETTERS=(AAA AAB AAC AAD AAE AAF AAG AAH AAI AAJ AAK AAL AAM AAN AAO)
IDX=0
for H in $(seq 0 3 42); do
  printf -v FH "%03d" "$H"
  FILE="gfs.t${RUN_CYCLE}z.pgrb2.0p25.f${FH}"
  ln -sf "gfs/$FILE" "$WORK/GRIBFILE.${LETTERS[$IDX]}"
  IDX=$((IDX + 1))
done
'''
pattern = re.compile(
    r'log "Preparando nomes GRIBFILE".*?'
    r'(?=log "Ajustando permissoes do volume para o container DTC")',
    re.S,
)
text, count = pattern.subn(links + '\n', text, count=1)
if count != 1:
    raise SystemExit(f'Links GRIBFILE nao ampliados: {count}')

text = text.replace(
    'mpirun -np 4 /comsoftware/wrf/WRF-4.3/main/real.exe',
    'mpirun --oversubscribe --bind-to none -np 4 /comsoftware/wrf/WRF-4.3/main/real.exe',
)
text = text.replace(
    'mpirun -np 4 /comsoftware/wrf/WRF-4.3/main/wrf.exe',
    'mpirun --oversubscribe --bind-to none -np 4 /comsoftware/wrf/WRF-4.3/main/wrf.exe',
)
text = text.replace('WRF 4 KM F000-F006', 'WRF 4 KM F000-F042')
path.write_text(text, encoding='utf-8')
PY

grep -q "run_hours = 42" "$BASE_SCRIPT"
grep -q "do_radar_ref = 1" "$BASE_SCRIPT"
grep -q "F000-F042" "$BASE_SCRIPT"
grep -q -- "--oversubscribe --bind-to none -np 4" "$BASE_SCRIPT"

chmod +x "$BASE_SCRIPT"
exec "$BASE_SCRIPT"
