#!/usr/bin/env bash
set -euo pipefail

START_H="${1:?start hour required}"
END_H="${2:?end hour required}"
RESTART_DIR="${3:?restart directory required}"
ROOT="${GITHUB_WORKSPACE:-$PWD}"
IMAGE="dtcenter/wps_wrf:latest"
WORK="$ROOT/wrf_restart_work"
OUTPUT="$ROOT/metbr_segment_output"
MPI_PROCS="${WRF_MPI_PROCS:-8}"
HIST="${WRF_HISTORY_INTERVAL_MINUTES:-60}"
SEG_H=$((END_H-START_H))

(( START_H > 0 && END_H > START_H && START_H % 3 == 0 && END_H % 3 == 0 && END_H <= 42 )) || { echo "Restart invalido: F${START_H}-F${END_H}" >&2; exit 2; }
test -d "$RESTART_DIR" || { echo "Restart directory missing: $RESTART_DIR" >&2; exit 3; }

mapfile -t RST < <(find "$RESTART_DIR" -maxdepth 1 -type f -name 'wrfrst_d01_*' -size +0c -print | sort)
((${#RST[@]} > 0)) || { echo "No wrfrst_d01_* non-empty found in checkpoint F${START_H}" >&2; exit 4; }
test -s "$RESTART_DIR/wrfbdy_d01" || { echo "wrfbdy_d01 missing/empty" >&2; exit 5; }

rm -rf "$WORK" "$OUTPUT"
mkdir -p "$WORK/run" "$OUTPUT"
cp -f "${RST[@]}" "$WORK/run/"
cp -f "$RESTART_DIR/wrfbdy_d01" "$WORK/run/"
if [[ -f "$RESTART_DIR/namelist.input" ]]; then
  cp -f "$RESTART_DIR/namelist.input" "$WORK/run/namelist.input"
else
  echo "namelist.input ausente no checkpoint" >&2
  exit 6
fi

# Old checkpoints may carry a newer-only namelist key. WRF 4.3 must not
# receive it, while the CLWRFGHG runtime data itself is still provisioned.
sed -i -E '/^[[:space:]]*ghg_input[[:space:]]*=/d' "$WORK/run/namelist.input"
chmod -R a+rwX "$WORK"

python3 - "$WORK/run/namelist.input" "$START_H" "$SEG_H" "$HIST" <<'PY'
import datetime as dt, os, re, sys
p, start_h, hours, hist = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4])
s = open(p, encoding='utf-8').read()
run_date=os.environ.get('RUN_DATE',''); run_cycle=os.environ.get('RUN_CYCLE','')
if not re.fullmatch(r'\d{8}',run_date) or run_cycle not in {'00','06','12','18'}:
    raise SystemExit(f'RUN_DATE/RUN_CYCLE invalidos para restart: {run_date!r} {run_cycle!r}')
base=dt.datetime.strptime(run_date+run_cycle,'%Y%m%d%H')
restart_time=base+dt.timedelta(hours=start_h)
end_time=restart_time+dt.timedelta(hours=hours)
vals={
 'start_year':restart_time.year,'start_month':restart_time.month,'start_day':restart_time.day,'start_hour':restart_time.hour,
 'start_minute':0,'start_second':0,
 'end_year':end_time.year,'end_month':end_time.month,'end_day':end_time.day,'end_hour':end_time.hour,
 'run_days':0,'run_hours':hours,'run_minutes':0,'run_seconds':0,
 'restart':'.true.','restart_interval':hours*60,'override_restart_timers':'.true.','history_interval':hist,
 'time_step':20,'time_step_fract_num':0,'time_step_fract_den':1,
}
for key,value in vals.items():
    pat=rf'(?m)^\s*{re.escape(key)}\s*=.*$'
    rep=f' {key} = {value},'
    if re.search(pat,s): s=re.sub(pat,rep,s)
    elif key in {'restart','restart_interval','override_restart_timers','history_interval','run_days','run_hours','run_minutes','run_seconds','end_year','end_month','end_day','end_hour'}:
        s=s.replace('&time_control', f'&time_control\n{rep}',1)
    elif key in {'time_step','time_step_fract_num','time_step_fract_den'}:
        s=s.replace('&domains', f'&domains\n{rep}',1)
open(p,'w',encoding='utf-8').write(s)
expected=f"wrfrst_d01_{restart_time:%Y-%m-%d_%H:%M:%S}"
open(os.path.join(os.path.dirname(p),'.expected_restart'),'w').write(expected+'\n')
print('RESTART NAMELIST:',restart_time.isoformat(),'->',hours,'h; end=',end_time.isoformat(),'; time_step=20 s')
print('EXPECTED RESTART FILE:',expected)
PY

EXPECTED_RST="$(cat "$WORK/run/.expected_restart")"
[[ -s "$WORK/run/$EXPECTED_RST" ]] || { echo "Restart exato nao encontrado: $EXPECTED_RST" >&2; ls -lh "$WORK/run"/wrfrst_d01_* >&2 || true; exit 8; }
find "$WORK/run" -maxdepth 1 -type f -name 'wrfrst_d01_*' ! -name "$EXPECTED_RST" -delete

# Provision the complete WRF 4.3 runtime physics set before real/wrf starts.
RUNTIME_HELPER_URL="https://raw.githubusercontent.com/progames12301-hash/sideral-backend/wrf-runner/wrf/ensure_metbr_wrf_runtime.sh"
curl -fL --retry 4 --retry-delay 2 --connect-timeout 20 --max-time 600 \
  -o "$WORK/ensure_metbr_wrf_runtime.sh" "$RUNTIME_HELPER_URL"
chmod +x "$WORK/ensure_metbr_wrf_runtime.sh"
docker run --rm --entrypoint /bin/bash \
  -v "$WORK/run:/run" -v "$WORK/ensure_metbr_wrf_runtime.sh:/ensure_metbr_wrf_runtime.sh:ro" \
  "$IMAGE" -lc 'set -e; /bin/bash /ensure_metbr_wrf_runtime.sh /run'

WRF_TABLES=(VEGPARM.TBL LANDUSE.TBL GENPARM.TBL SOILPARM.TBL MPTABLE.TBL URBPARM.TBL RRTMG_LW_DATA RRTMG_SW_DATA ozone.formatted ozone_lat.formatted ozone_plev.formatted aerosol.formatted aerosol_lat.formatted aerosol_lon.formatted aerosol_plev.formatted CAM_ABS_DATA)
for name in "${WRF_TABLES[@]}"; do
  test -s "$WORK/run/$name" || {
    docker run --rm --entrypoint /bin/bash -v "$WORK/run:/run" "$IMAGE" -lc "set -e; src=\$(find / -type f -name '$name' -not -path '/proc/*' -not -path '/sys/*' -not -path '/dev/*' -print -quit 2>/dev/null || true); test -n \"\$src\" || { echo 'Missing WRF runtime table: $name' >&2; exit 1; }; echo \"Using WRF table $name from \$src\"; cp -f \"\$src\" /run/$name"
  }
done

for name in VEGPARM.TBL LANDUSE.TBL GENPARM.TBL SOILPARM.TBL MPTABLE.TBL RRTMG_LW_DATA RRTMG_SW_DATA ozone.formatted ozone_lat.formatted ozone_plev.formatted aerosol.formatted aerosol_lat.formatted aerosol_lon.formatted aerosol_plev.formatted CAM_ABS_DATA CAMtr_volume_mixing_ratio; do
  test -s "$WORK/run/$name" || { echo "Missing mandatory WRF runtime table: $name" >&2; exit 12; }
done

WRFEXE="$(docker run --rm --entrypoint /bin/bash "$IMAGE" -lc "find / -type f -path '*/main/wrf.exe' -not -path '/proc/*' -not -path '/sys/*' -not -path '/dev/*' -print -quit 2>/dev/null || true")"
[[ -n "$WRFEXE" ]] || { echo "wrf.exe not found in WRF container" >&2; exit 7; }

touch "$WORK/run/rsl.out.restart"
set +e
docker run --rm --entrypoint /bin/bash \
  -e OMPI_ALLOW_RUN_AS_ROOT=1 -e OMPI_ALLOW_RUN_AS_ROOT_CONFIRM=1 \
  -e WRF_MPI_PROCS="$MPI_PROCS" -v "$WORK/run:/run" "$IMAGE" -lc '
  set -u; cd /run; rst="$(cat .expected_restart)"
  echo "Starting METBR WRF restart; MPI=${WRF_MPI_PROCS:-8}; dt=20 s; restart=$rst"
  mpirun --allow-run-as-root --oversubscribe --mca orte_base_help_aggregate 0 \
    -np "${WRF_MPI_PROCS:-8}" "'"$WRFEXE"'" > /run/rsl.out.restart 2>&1
  rc=$?; echo "WRF_MPI_EXIT_CODE=$rc" >> /run/rsl.out.restart; exit "$rc"
'
STATUS=$?
set -e
cp -f "$WORK/run/rsl.out.restart" "$OUTPUT/" 2>/dev/null || true
cp -f "$WORK/run"/rsl.error.* "$OUTPUT/" 2>/dev/null || true
cp -f "$WORK/run"/rsl.out.* "$OUTPUT/" 2>/dev/null || true
cp -f "$WORK/run/namelist.input" "$OUTPUT/namelist.restart.input" 2>/dev/null || true

if (( STATUS != 0 )); then
  echo "WRF restart F${START_H}-F${END_H} failed with exit code ${STATUS}" >&2
  tail -n 200 "$WORK/run/rsl.out.restart" >&2 || true
  for f in "$WORK/run"/rsl.error.* "$WORK/run"/rsl.out.*; do [[ -f "$f" ]] || continue; echo "===== $(basename "$f") =====" >&2; tail -n 200 "$f" >&2 || true; done
  free -h >&2 || true
  exit "$STATUS"
fi

mapfile -t OUTS < <(find "$WORK/run" -maxdepth 1 -type f -name 'wrfout_d01_*' -size +0c -print | sort)
mapfile -t NEW_RST < <(find "$WORK/run" -maxdepth 1 -type f -name 'wrfrst_d01_*' ! -name "$EXPECTED_RST" -size +0c -print | sort)
((${#OUTS[@]} > 0)) || { echo "No non-empty wrfout produced for F${START_H}-F${END_H}" >&2; exit 41; }
((${#NEW_RST[@]} > 0)) || { echo "No non-empty wrfrst produced for F${START_H}-F${END_H}" >&2; exit 42; }
cp -f "${OUTS[@]}" "$OUTPUT/"
cp -f "${NEW_RST[@]}" "$OUTPUT/"
test -s "$WORK/run/wrfbdy_d01" || { echo "wrfbdy_d01 ausente em $WORK/run apos o restart" >&2; exit 43; }
cp -f "$WORK/run/wrfbdy_d01" "$OUTPUT/wrfbdy_d01"
chmod -R a+rwX "$OUTPUT"
echo "METBR restart F${START_H}-F${END_H} completed successfully."
echo "wrfout files: ${#OUTS[@]}"
echo "wrfrst files: ${#NEW_RST[@]}"