#!/usr/bin/env bash
set -euo pipefail

: "${FORCE_RUN_DATE:?FORCE_RUN_DATE ausente}"
: "${FORCE_RUN_CYCLE:?FORCE_RUN_CYCLE ausente}"
: "${WRF_START_HOUR:?WRF_START_HOUR ausente}"
: "${WRF_END_HOUR:?WRF_END_HOUR ausente}"

if (( WRF_START_HOUR < 0 || WRF_END_HOUR <= WRF_START_HOUR )); then
  echo "Intervalo WRF invalido" >&2; exit 2
fi
if (( WRF_START_HOUR % 3 != 0 || WRF_END_HOUR % 3 != 0 )); then
  echo "Start/end precisam ser multiplos de 3" >&2; exit 3
fi
WRF_COLD_START="${WRF_COLD_START:-0}"
if (( WRF_START_HOUR > 0 )) && [[ -z "${WRF_RESTART_FILE:-}" ]] && [[ "$WRF_COLD_START" != "1" ]]; then
  echo "Continuacao exige WRF_RESTART_FILE ou WRF_COLD_START=1" >&2; exit 4
fi

export FORCE_RUN_DATE FORCE_RUN_CYCLE WRF_START_HOUR WRF_END_HOUR WRF_COLD_START
export WRF_SEGMENT_HOURS=$((WRF_END_HOUR-WRF_START_HOUR))

python3 - <<'PY'
from pathlib import Path
import os, re

path=Path('wrf/run_gfs_test.sh')
text=path.read_text(encoding='utf-8')
# Domínio independente do Sudeste: SP, MG, RJ e ES, mantendo o Sul intacto.
domain_replacements = {
    ' e_we              = 300,': ' e_we              = 390,',
    ' e_sn              = 360,': ' e_sn              = 360,',
    ' ref_lat   = -28.10,': ' ref_lat   = -19.50,',
    ' ref_lon   = -53.45,': ' ref_lon   = -46.50,',
    ' truelat1  = -25.0,': ' truelat1  = -15.0,',
    ' truelat2  = -35.0,': ' truelat2  = -25.0,',
    ' stand_lon = -53.45,': ' stand_lon = -46.50,',
    ' e_we = 300,': ' e_we = 390,',
    ' e_sn = 360,': ' e_sn = 360,',
}
for old, new in domain_replacements.items():
    if old not in text:
        raise SystemExit(f'Parametro do dominio Sul nao encontrado: {old}')
    text = text.replace(old, new)
start=int(os.environ['WRF_START_HOUR'])
end=int(os.environ['WRF_END_HOUR'])
duration=end-start
cold=os.environ.get('WRF_COLD_START') == '1'

if 'do_radar_ref = 1' not in text:
    raise SystemExit('do_radar_ref=1 ausente')

selection=f'''log "Usando rodada GFS fixa para segmento F{start:03d}-F{end:03d}"
RUN_DATE="${{FORCE_RUN_DATE:?FORCE_RUN_DATE ausente}}"
RUN_CYCLE="${{FORCE_RUN_CYCLE:?FORCE_RUN_CYCLE ausente}}"
BASE_URL="https://noaa-gfs-bdp-pds.s3.amazonaws.com/gfs.${{RUN_DATE}}/${{RUN_CYCLE}}/atmos"
printf -v END_FH "%03d" {end}
END_URL="$BASE_URL/gfs.t${{RUN_CYCLE}}z.pgrb2.0p25.f${{END_FH}}"
READY=0
for TRY in $(seq 1 18); do
  if curl -fsSL --range 0-0 --connect-timeout 15 --max-time 45 -o /dev/null "$END_URL"; then
    READY=1; break
  fi
  echo "GFS F{end:03d} ainda indisponivel ($TRY/18)"; sleep 300
done
[[ "$READY" -eq 1 ]] || {{ echo "GFS F{end:03d} indisponivel" >&2; exit 22; }}
'''
text,count=re.subn(
    r'log "Escolhendo rodada GFS mais recente com F006 disponível".*?(?=echo "RUN_DATE=\$RUN_DATE")',
    selection+'\n', text, count=1, flags=re.S)
if count!=1: raise SystemExit('Falha ao fixar rodada GFS')

# Horizonte absoluto e inicio do segmento.
text=text.replace('+6 hours', f'+{end} hours')
base='${RUN_DATE} ${RUN_CYCLE}:00 UTC'
seg=f'${{RUN_DATE}} ${{RUN_CYCLE}}:00 UTC +{start} hours'
for suffix in ('+%Y-%m-%d_%H:%M:%S','+%Y','+%m','+%d','+%H'):
    text=text.replace(f'date -u -d "{base}" {suffix}', f'date -u -d "{seg}" {suffix}')
text=text.replace('run_hours = 6,', f'run_hours = {duration},')
text=text.replace('history_interval = 180,', 'history_interval = 60,')
restart='.true.' if start>0 and not cold else '.false.'
text=text.replace('restart = .false.,', f'restart = {restart},\n restart_interval = {duration*60},\n write_hist_at_0h_rst = .true.,')

# Em segmentos independentes baixa somente as fronteiras do proprio trecho.
download_start=start if cold else 0
download=f'''log "Baixando GFS F{download_start:03d}-F{end:03d} de 3 em 3 horas"
mkdir -p "$WORK/gfs"
for H in $(seq {download_start} 3 {end}); do
  printf -v FH "%03d" "$H"
  FILE="gfs.t${{RUN_CYCLE}}z.pgrb2.0p25.f${{FH}}"
  curl -fL --retry 4 --retry-delay 5 --connect-timeout 20 --max-time 900 \\
    -o "$WORK/gfs/$FILE" "$BASE_URL/$FILE"
done
'''
text,count=re.subn(r'log "Baixando GFS F000 F003 F006".*?done\n',download+'\n',text,count=1,flags=re.S)
if count!=1: raise SystemExit('Falha patch download GFS')

# Geografia robusta, igual ao pipeline multimodelo.
geog=r'''log "Baixando e validando geografia WPS low-res"
rm -rf "$WORK/geog_extract" "$WORK/WPS_GEOG"
mkdir -p "$WORK/geog_extract"
curl -fL --retry 3 --connect-timeout 20 --max-time 900 -o "$WORK/geog.tar.gz" https://www2.mmm.ucar.edu/wrf/src/wps_files/geog_low_res_mandatory.tar.gz
tar -xzf "$WORK/geog.tar.gz" -C "$WORK/geog_extract"
TOPO_INDEX="$(find "$WORK/geog_extract" -type f -path '*/topo_gmted2010_5m/index' -print -quit)"
test -n "$TOPO_INDEX" || { echo "topo ausente" >&2; exit 31; }
GEOG_ROOT="$(dirname "$(dirname "$TOPO_INDEX")")"
mkdir -p "$WORK/WPS_GEOG"
cp -a "$GEOG_ROOT/." "$WORK/WPS_GEOG/"
'''
text,count=re.subn(r'log "Baixando e extraindo geografia WPS low-res".*?(?=cat > "\$WORK/namelist\.wps" <<EOF)',geog+'\n',text,count=1,flags=re.S)
if count!=1: raise SystemExit('Falha patch geografia')

letters=['AAA','AAB','AAC','AAD','AAE','AAF','AAG','AAH','AAI','AAJ','AAK','AAL','AAM','AAN','AAO','AAP','AAQ','AAR','AAS','AAT','AAU','AAV','AAW','AAX','AAY','AAZ']
file_count=(end-download_start)//3+1
if file_count>len(letters): raise SystemExit('Horizonte excede GRIBFILE letters')
arr=' '.join(letters[:file_count])
links=f'''log "Preparando nomes GRIBFILE F{download_start:03d}-F{end:03d}"
LETTERS=({arr})
IDX=0
for H in $(seq {download_start} 3 {end}); do
  printf -v FH "%03d" "$H"
  FILE="gfs.t${{RUN_CYCLE}}z.pgrb2.0p25.f${{FH}}"
  ln -sf "gfs/$FILE" "$WORK/GRIBFILE.${{LETTERS[$IDX]}}"
  IDX=$((IDX+1))
done
'''
text,count=re.subn(r'log "Preparando nomes GRIBFILE".*?(?=log "Ajustando permissoes do volume para o container DTC")',links+'\n',text,count=1,flags=re.S)
if count!=1: raise SystemExit('Falha patch GRIBFILE')

# Copia restart para dentro do volume depois que o script limpa WORK.
marker='log "Ajustando permissoes do volume para o container DTC"'
restore_host=r'''if [[ -n "${WRF_RESTART_FILE:-}" ]]; then
  mkdir -p "$WORK/restart_input"
  cp -f "$WRF_RESTART_FILE" "$WORK/restart_input/"
fi

'''
text=text.replace(marker,restore_host+marker,1)

text=text.replace('mpirun -np 4 /comsoftware/wrf/WRF-4.3/main/real.exe','mpirun --oversubscribe --bind-to none -np 4 /comsoftware/wrf/WRF-4.3/main/real.exe')
text=text.replace('mpirun -np 4 /comsoftware/wrf/WRF-4.3/main/wrf.exe','mpirun --oversubscribe --bind-to none -np 4 /comsoftware/wrf/WRF-4.3/main/wrf.exe')
anchor='    test -f wrfinput_d01\n    test -f wrfbdy_d01\n\n    echo "=== WRF 4 KM F000-F006 ==="'
inject=f'''    test -f wrfinput_d01
    test -f wrfbdy_d01
    if test -d /work/restart_input; then
      echo "=== RESTORE WRF RESTART ==="
      cp -f /work/restart_input/wrfrst_d01_* .
      ls -lh wrfrst_d01_*
    fi

    echo "=== WRF 4 KM SEGMENTO F{start:03d}-F{end:03d} ==="'''
if anchor not in text: raise SystemExit('Anchor restart GFS ausente')
text=text.replace(anchor,inject,1)
path.write_text(text,encoding='utf-8')
PY

if [[ "${WRF_PATCH_ONLY:-0}" == "1" ]]; then
  bash -n wrf/run_gfs_test.sh
  grep -q 'restart = .false.' wrf/run_gfs_test.sh
  grep -q 'F063-F072\|SEGMENTO F063-F072\|SEGMENTO F' wrf/run_gfs_test.sh
  echo "PATCH DE SEGMENTO VALIDADO"
  exit 0
fi

chmod +x wrf/run_gfs_test.sh
exec wrf/run_gfs_test.sh
