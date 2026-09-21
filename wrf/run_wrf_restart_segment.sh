#!/usr/bin/env bash
set -euo pipefail

START_H="${1:?start hour required}"
END_H="${2:?end hour required}"
RESTART_DIR="${3:?restart directory required}"
ROOT="${GITHUB_WORKSPACE:-$PWD}"
IMAGE="dtcenter/wps_wrf:latest"
WORK="$ROOT/wrf_restart_work"
MPI_PROCS="${WRF_MPI_PROCS:-8}"
HIST="${WRF_HISTORY_INTERVAL_MINUTES:-60}"
SEG_H=$((END_H-START_H))

(( START_H > 0 && END_H > START_H && START_H % 3 == 0 && END_H % 3 == 0 && END_H <= 42 )) || { echo "Restart invalido: inicio/fim precisam ser multiplos de 3 h entre F003 e F042" >&2; exit 2; }
test -d "$RESTART_DIR" || { echo "Restart directory missing: $RESTART_DIR" >&2; exit 3; }
mapfile -t RST < <(find "$RESTART_DIR" -maxdepth 1 -type f -name 'wrfrst_d01_*' | sort)
((${#RST[@]} > 0)) || { echo "No wrfrst_d01_* found" >&2; exit 4; }
test -f "$RESTART_DIR/wrfbdy_d01" || { echo "wrfbdy_d01 missing" >&2; exit 5; }
rm -rf "$WORK"; mkdir -p "$WORK/run"
cp -f "${RST[@]}" "$WORK/run/"; cp -f "$RESTART_DIR/wrfbdy_d01" "$WORK/run/"
if [[ -f "$RESTART_DIR/namelist.input" ]]; then cp -f "$RESTART_DIR/namelist.input" "$WORK/run/namelist.input"; fi
if [[ ! -f "$WORK/run/namelist.input" && -f "$ROOT/wrf/namelist.metbr.template" ]]; then cp -f "$ROOT/wrf/namelist.metbr.template" "$WORK/run/namelist.input"; fi
test -f "$WORK/run/namelist.input" || { echo "No namelist.input available for restart" >&2; exit 6; }

python3 - "$WORK/run/namelist.input" "$SEG_H" "$HIST" <<'PY'
import re,sys
p,h,hist=sys.argv[1],sys.argv[2],sys.argv[3]
s=open(p,encoding='utf-8').read()
for pat,new in [(r'(?m)^\s*run_days\s*=.*$',' run_days = 0,'),(r'(?m)^\s*run_hours\s*=.*$',f' run_hours = {h},'),(r'(?m)^\s*run_minutes\s*=.*$',' run_minutes = 0,'),(r'(?m)^\s*run_seconds\s*=.*$',' run_seconds = 0,'),(r'(?m)^\s*restart\s*=.*$',' restart = .true.,'),(r'(?m)^\s*restart_interval\s*=.*$',' restart_interval = 180,'),(r'(?m)^\s*history_interval\s*=.*$',f' history_interval = {hist},')]: s=re.sub(pat,new,s)
if ' restart = .true.,' not in s: s=s.replace('&time_control','&time_control\n restart = .true.,',1)
if ' restart_interval =' not in s: s=s.replace(' restart = .true.,',' restart = .true.,\n restart_interval = 180,',1)
open(p,'w',encoding='utf-8').write(s)
PY

docker run --rm -e OMPI_ALLOW_RUN_AS_ROOT=1 -e OMPI_ALLOW_RUN_AS_ROOT_CONFIRM=1 -v "$WORK/run:/run" "$IMAGE" /bin/bash -lc '
set -euo pipefail
cd /run
test -f namelist.input; test -f wrfbdy_d01
WRFEXE=/comsoftware/wrf/WRF-4.5.2/main/wrf.exe
test -x "$WRFEXE" || WRFEXE=/comsoftware/wrf/WRF-4.3.3/main/wrf.exe
test -x "$WRFEXE" || { echo "wrf.exe not found in WRF container" >&2; exit 7; }
mpirun --allow-run-as-root -np "'"$MPI_PROCS"'" "$WRFEXE" > rsl.out.restart 2>&1
grep -Eq "SUCCESS COMPLETE WRF|SUCCESS COMPLETE REAL" rsl.out.restart
'

mkdir -p "$ROOT/metbr_segment_output"
cp -f "$WORK/run/wrfout_d01_"* "$ROOT/metbr_segment_output/" 2>/dev/null || true
cp -f "$WORK/run/wrfrst_d01_"* "$ROOT/metbr_segment_output/" 2>/dev/null || true
cp -f "$WORK/run/rsl.out.restart" "$ROOT/metbr_segment_output/" 2>/dev/null || true
echo "METBR restart F${START_H}-F${END_H} completed without WPS/real.exe using ${MPI_PROCS} MPI."
