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

(( START_H > 0 && END_H > START_H && START_H % 3 == 0 && END_H % 3 == 0 && END_H <= 42 )) || {
  echo "Restart invalido: F${START_H}-F${END_H}" >&2
  exit 2
}
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
elif [[ -f "$ROOT/wrf/namelist.metbr.template" ]]; then
  cp -f "$ROOT/wrf/namelist.metbr.template" "$WORK/run/namelist.input"
else
  echo "No namelist.input or wrf/namelist.metbr.template available" >&2
  exit 6
fi

chmod -R a+rwX "$WORK"

python3 - "$WORK/run/namelist.input" "$START_H" "$SEG_H" "$HIST" <<'PY'
import datetime as dt
import os
import re
import sys

p, start_h, hours, hist = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4])
s = open(p, encoding='utf-8').read()
run_date = os.environ.get('RUN_DATE', '')
run_cycle = os.environ.get('RUN_CYCLE', '')
if not re.fullmatch(r'\d{8}', run_date) or run_cycle not in {'00','06','12','18'}:
    raise SystemExit(f'RUN_DATE/RUN_CYCLE invalidos para restart: {run_date!r} {run_cycle!r}')
base = dt.datetime.strptime(run_date + run_cycle, '%Y%m%d%H')
restart_time = base + dt.timedelta(hours=start_h)
expected_rst = f"wrfrst_d01_{restart_time:%Y-%m-%d_%H:%M:%S}"
vals = {
    'start_year': restart_time.year,
    'start_month': restart_time.month,
    'start_day': restart_time.day,
    'start_hour': restart_time.hour,
    'start_minute': 0,
    'start_second': 0,
    'run_days': 0,
    'run_hours': hours,
    'run_minutes': 0,
    'run_seconds': 0,
    'restart': '.true.',
    'restart_interval': 180,
    'override_restart_timers': '.true.',
    'history_interval': hist,
}
for key, value in vals.items():
    pattern = rf'(?m)^\s*{re.escape(key)}\s*=.*$'
    replacement = f' {key} = {value},'
    if re.search(pattern, s):
        s = re.sub(pattern, replacement, s)
    elif key in {'restart','restart_interval','override_restart_timers','history_interval','run_days','run_hours','run_minutes','run_seconds'}:
        s = s.replace('&time_control', f'&time_control\n{replacement}', 1)
open(p, 'w', encoding='utf-8').write(s)
print('RESTART NAMELIST:', restart_time.isoformat(), '->', hours, 'h')
print('EXPECTED RESTART FILE:', expected_rst)
with open(os.path.join(os.path.dirname(p), '.expected_restart'), 'w', encoding='utf-8') as f:
    f.write(expected_rst + '\n')
PY

EXPECTED_RST="$(cat "$WORK/run/.expected_restart")"
if [[ ! -s "$WORK/run/$EXPECTED_RST" ]]; then
  echo "Restart exato nao encontrado: $EXPECTED_RST" >&2
  echo "Restarts disponiveis:" >&2
  ls -lh "$WORK/run"/wrfrst_d01_* >&2 || true
  exit 8
fi
find "$WORK/run" -maxdepth 1 -type f -name 'wrfrst_d01_*' ! -name "$EXPECTED_RST" -delete

echo "Using exact restart: $EXPECTED_RST"

# METBR must not depend on a host-side ozone file. The WRF container ships the
# runtime ozone lookup table required by some radiation configurations. Stage
# the real table from the same WRF image into the run directory when available;
# never generate a synthetic replacement.
echo '--- staging WRF radiation lookup tables ---'
docker run --rm \
  -v "$WORK/run:/run" \
  "$IMAGE" /bin/bash -lc '
    set -euo pipefail
    if [[ -s /run/ozone_plev.formatted ]]; then
      echo "ozone_plev.formatted already present in restart package"
      exit 0
    fi
    ozone="$(find /comsoftware /opt /usr/local -type f -name ozone_plev.formatted -print -quit 2>/dev/null || true)"
    if [[ -n "$ozone" && -s "$ozone" ]]; then
      cp -f "$ozone" /run/ozone_plev.formatted
      echo "Staged real ozone_plev.formatted from: $ozone"
      exit 0
    fi
    echo "ERROR: WRF image does not contain ozone_plev.formatted and checkpoint did not provide it" >&2
    exit 12
  '
test -s "$WORK/run/ozone_plev.formatted" || { echo "ozone_plev.formatted missing after WRF image staging" >&2; exit 12; }

# Validate checkpoint files before MPI starts.
docker run --rm --entrypoint /bin/bash \
  -v "$WORK/run:/run" \
  "$IMAGE" -lc '
    set -euo pipefail
    cd /run
    test -r namelist.input
    test -r wrfbdy_d01
    test -s .expected_restart
    rst="$(cat .expected_restart)"
    test -s "$rst"
    test -s ozone_plev.formatted
    ls -lh "$rst" wrfbdy_d01 namelist.input ozone_plev.formatted
    if command -v ncdump >/dev/null 2>&1; then
      ncdump -h "$rst" >/dev/null
      ncdump -h wrfbdy_d01 >/dev/null
    fi
  '

WRFEXE="$(docker run --rm --entrypoint /bin/bash "$IMAGE" -lc "find /comsoftware/wrf -type f -path '*/main/wrf.exe' -print -quit 2>/dev/null || true")"
if [[ -z "$WRFEXE" ]]; then
  echo "wrf.exe not found in WRF container" >&2
  exit 7
fi
echo "Using WRF executable: $WRFEXE"

touch "$WORK/run/rsl.out.restart"
chmod 666 "$WORK/run/rsl.out.restart"

set +e
docker run --rm \
  --entrypoint /bin/bash \
  -e OMPI_ALLOW_RUN_AS_ROOT=1 \
  -e OMPI_ALLOW_RUN_AS_ROOT_CONFIRM=1 \
  -e WRF_MPI_PROCS="$MPI_PROCS" \
  -v "$WORK/run:/run" \
  "$IMAGE" -lc '
    set -u
    cd /run
    test -r namelist.input
    test -r wrfbdy_d01
    test -s ozone_plev.formatted
    rst="$(cat .expected_restart)"
    test -s "$rst"
    test -x "'"$WRFEXE"'"
    rm -f rsl.error.* rsl.out.*
    echo "Starting WRF restart in $(pwd)"
    echo "MPI_PROCS=${WRF_MPI_PROCS:-8}"
    echo "Exact restart: $rst"
    echo "Ozone lookup: /run/ozone_plev.formatted"
    ls -lh "$rst" wrfbdy_d01 ozone_plev.formatted
    if command -v /usr/bin/time >/dev/null 2>&1; then
      /usr/bin/time -v mpirun --allow-run-as-root --oversubscribe \
        --mca orte_base_help_aggregate 0 \
        -np "${WRF_MPI_PROCS:-8}" \
        "'"$WRFEXE"'" > /run/rsl.out.restart 2>&1
    else
      mpirun --allow-run-as-root --oversubscribe \
        --mca orte_base_help_aggregate 0 \
        -np "${WRF_MPI_PROCS:-8}" \
        "'"$WRFEXE"'" > /run/rsl.out.restart 2>&1
    fi
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
cp -f "$RESTART_DIR"/wrfrst_d01_* "$OUTPUT/" 2>/dev/null || true
cp -f "$RESTART_DIR/wrfbdy_d01" "$OUTPUT/" 2>/dev/null || true

if (( STATUS != 0 )); then
  echo "WRF restart F${START_H}-F${END_H} failed with exit code ${STATUS}" >&2
  echo '--- ultimo log wrapper ---' >&2
  tail -n 200 "$WORK/run/rsl.out.restart" 2>/dev/null || true
  echo '--- rsl.error.* ---' >&2
  for f in "$WORK/run"/rsl.error.*; do
    [[ -f "$f" ]] || continue
    echo "===== $(basename "$f") =====" >&2
    tail -n 200 "$f" >&2 || true
  done
  echo '--- rsl.out.* ---' >&2
  for f in "$WORK/run"/rsl.out.*; do
    [[ -f "$f" ]] || continue
    echo "===== $(basename "$f") =====" >&2
    tail -n 120 "$f" >&2 || true
  done
  echo '--- host memory after failure ---' >&2
  free -h >&2 || true
  exit "$STATUS"
fi

mapfile -t OUTS < <(find "$WORK/run" -maxdepth 1 -type f -name 'wrfout_d01_*' -size +0c -print | sort)
mapfile -t NEW_RST < <(find "$WORK/run" -maxdepth 1 -type f -name 'wrfrst_d01_*' -size +0c -print | sort)
((${#OUTS[@]} > 0)) || { echo "No non-empty wrfout produced for F${START_H}-F${END_H}" >&2; exit 41; }
((${#NEW_RST[@]} > 0)) || { echo "No non-empty wrfrst produced for F${START_H}-F${END_H}" >&2; exit 42; }

cp -f "${OUTS[@]}" "$OUTPUT/"
cp -f "${NEW_RST[@]}" "$OUTPUT/"
chmod -R a+rwX "$OUTPUT"

echo "METBR restart F${START_H}-F${END_H} completed successfully."
echo "wrfout files: ${#OUTS[@]}"
echo "wrfrst files: ${#NEW_RST[@]}"
