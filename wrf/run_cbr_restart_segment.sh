#!/usr/bin/env bash
set -euo pipefail

START_H="$1"
END_H="$2"
RESTART_DIR="$3"
ROOT="$GITHUB_WORKSPACE"
IMAGE="dtcenter/wps_wrf:latest"
WORK="$ROOT/cbr_restart_work"
OUTPUT="$ROOT/cbr_segment_output"
MPI_PROCS="$WRF_MPI_PROCS"
HIST="$WRF_HISTORY_INTERVAL_MINUTES"

(( START_H > 0 && END_H > START_H && START_H % 3 == 0 && END_H % 3 == 0 && END_H <= 42 )) || {
  echo "Restart CBR invalido: F$START_H-F$END_H" >&2
  exit 2
}
test -d "$RESTART_DIR" || { echo "Restart ausente: $RESTART_DIR" >&2; exit 3; }

RST_COUNT="$(find "$RESTART_DIR" -maxdepth 1 -type f -name 'wrfrst_d01_*' -size +0c | wc -l)"
(( RST_COUNT > 0 )) || { echo "Nenhum wrfrst CBR valido em $RESTART_DIR" >&2; exit 4; }
test -s "$RESTART_DIR/wrfbdy_d01" || { echo "wrfbdy_d01 ausente/empty" >&2; exit 5; }
test -s "$RESTART_DIR/namelist.input" || { echo "namelist.input ausente" >&2; exit 6; }

rm -rf "$WORK" "$OUTPUT"
mkdir -p "$WORK/run" "$OUTPUT"
find "$RESTART_DIR" -maxdepth 1 -type f -name 'wrfrst_d01_*' -size +0c -exec cp -f {} "$WORK/run/" \;
cp -f "$RESTART_DIR/wrfbdy_d01" "$WORK/run/"
cp -f "$RESTART_DIR/namelist.input" "$WORK/run/namelist.input"

sed -i -E '/^[[:space:]]*ghg_input[[:space:]]*=/d' "$WORK/run/namelist.input"

python3 - "$WORK/run/namelist.input" "$START_H" "$END_H" "$HIST" <<'PY'
import datetime as dt, os, re, sys
p, start_h, end_h, hist = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4])
s = open(p, encoding='utf-8').read()
run_date = os.environ.get('RUN_DATE','')
run_cycle = os.environ.get('RUN_CYCLE','')
if not re.fullmatch(r'\d{8}', run_date) or run_cycle not in {'00','06','12','18'}:
    raise SystemExit(f'RUN_DATE/RUN_CYCLE invalidos: {run_date!r} {run_cycle!r}')
base = dt.datetime.strptime(run_date + run_cycle, '%Y%m%d%H')
start = base + dt.timedelta(hours=start_h)
end = base + dt.timedelta(hours=end_h)
pairs = {
 'start_year':start.year,'start_month':start.month,'start_day':start.day,'start_hour':start.hour,
 'end_year':end.year,'end_month':end.month,'end_day':end.day,'end_hour':end.hour,
 'run_days':0,'run_hours':end_h-start_h,'run_minutes':0,'run_seconds':0,
 'restart':'.true.','restart_interval':(end_h-start_h)*60,'override_restart_timers':'.true.','history_interval':hist,
 'time_step':20,'time_step_fract_num':0,'time_step_fract_den':1,
}
for key, value in pairs.items():
    pat=rf'(?m)^\s*{re.escape(key)}\s*=.*$'
    rep=f' {key} = {value},'
    if re.search(pat,s):
        s=re.sub(pat,rep,s)
    elif key in {'restart','restart_interval','override_restart_timers','history_interval','run_days','run_hours','run_minutes','run_seconds','end_year','end_month','end_day','end_hour'}:
        s=s.replace('&time_control', '&time_control\n'+rep, 1)
    else:
        s=s.replace('&domains', '&domains\n'+rep, 1)
open(p,'w',encoding='utf-8').write(s)
expected=f"wrfrst_d01_{start:%Y-%m-%d_%H:%M:%S}"
open(os.path.join(os.path.dirname(p),'.expected_restart'),'w').write(expected+'\n')
print('CBR restart:', start.isoformat(), '->', end.isoformat(), 'dt=20 s')
PY

EXPECTED_RST="$(cat "$WORK/run/.expected_restart")"
test -s "$WORK/run/$EXPECTED_RST" || { echo "Restart exato nao encontrado: $EXPECTED_RST" >&2; exit 8; }
find "$WORK/run" -maxdepth 1 -type f -name 'wrfrst_d01_*' ! -name "$EXPECTED_RST" -delete

RUNTIME_HELPER_URL="https://raw.githubusercontent.com/progames12301-hash/sideral-backend/cbr-wrf-4km/wrf/ensure_cbr_wrf_runtime.sh"
curl --fail --location --retry 5 --retry-delay 5 --connect-timeout 20 --max-time 600 -o "$WORK/ensure_runtime.sh" "$RUNTIME_HELPER_URL"
chmod +x "$WORK/ensure_runtime.sh"
docker run --rm --entrypoint /bin/bash -v "$WORK/run:/run" -v "$WORK/ensure_runtime.sh:/ensure_runtime.sh:ro" "$IMAGE" -lc 'set -e; /bin/bash /ensure_runtime.sh /run'

for name in VEGPARM.TBL LANDUSE.TBL GENPARM.TBL SOILPARM.TBL MPTABLE.TBL URBPARM.TBL RRTMG_LW_DATA RRTMG_SW_DATA ozone.formatted ozone_lat.formatted ozone_plev.formatted aerosol.formatted aerosol_lat.formatted aerosol_plev.formatted; do
  test -s "$WORK/run/$name" || { echo "Tabela WRF ausente: $name" >&2; exit 12; }
done

WRFEXE="$(docker run --rm --entrypoint /bin/bash "$IMAGE" -lc "find / -type f -path '*/main/wrf.exe' -not -path '/proc/*' -not -path '/sys/*' -not -path '/dev/*' -print -quit 2>/dev/null || true")"
test -n "$WRFEXE"

set +e
docker run --rm --entrypoint /bin/bash   -e OMPI_ALLOW_RUN_AS_ROOT=1 -e OMPI_ALLOW_RUN_AS_ROOT_CONFIRM=1   -e WRF_MPI_PROCS="$MPI_PROCS" -e WRFEXE="$WRFEXE"   -e START_H="$START_H" -e END_H="$END_H"   -v "$WORK/run:/run" "$IMAGE" -lc '
    set -u
    cd /run
    rst="$(cat .expected_restart)"
    echo "WRF CBR 4 KM restart F$START_H-F$END_H; MPI=$WRF_MPI_PROCS; restart=$rst"
    mpirun --allow-run-as-root --oversubscribe --mca orte_base_help_aggregate 0       -np "$WRF_MPI_PROCS" "$WRFEXE" > /run/rsl.out.restart 2>&1
    rc=$?
    echo "WRF_MPI_EXIT_CODE=$rc" >> /run/rsl.out.restart
    exit "$rc"
  '
STATUS=$?
set -e

cp -f "$WORK/run/rsl.out.restart" "$OUTPUT/" 2>/dev/null || true
cp -f "$WORK/run"/rsl.error.* "$OUTPUT/" 2>/dev/null || true
cp -f "$WORK/run"/rsl.out.* "$OUTPUT/" 2>/dev/null || true
cp -f "$WORK/run/namelist.input" "$OUTPUT/namelist.restart.input" 2>/dev/null || true

if (( STATUS != 0 )); then
  echo "WRF CBR restart F$START_H-F$END_H falhou: $STATUS" >&2
  tail -n 240 "$WORK/run/rsl.out.restart" >&2 || true
  exit "$STATUS"
fi

OUT_COUNT="$(find "$WORK/run" -maxdepth 1 -type f -name 'wrfout_d01_*' -size +0c | wc -l)"
RST_NEW_COUNT="$(find "$WORK/run" -maxdepth 1 -type f -name 'wrfrst_d01_*' ! -name "$EXPECTED_RST" -size +0c | wc -l)"
(( OUT_COUNT > 0 )) || { echo "Nenhum wrfout CBR produzido" >&2; exit 41; }
(( RST_NEW_COUNT > 0 )) || { echo "Nenhum wrfrst CBR produzido" >&2; exit 42; }

find "$WORK/run" -maxdepth 1 -type f -name 'wrfout_d01_*' -size +0c -exec cp -f {} "$OUTPUT/" \;
find "$WORK/run" -maxdepth 1 -type f -name 'wrfrst_d01_*' ! -name "$EXPECTED_RST" -size +0c -exec cp -f {} "$OUTPUT/" \;
cp -f "$WORK/run/wrfbdy_d01" "$OUTPUT/wrfbdy_d01"
echo "CBR restart F$START_H-F$END_H concluido."
