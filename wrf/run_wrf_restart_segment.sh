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

# Every restart segment is exactly three hours and starts after F000.
(( START_H > 0 && END_H > START_H && START_H % 3 == 0 && END_H % 3 == 0 && END_H <= 42 )) || {
  echo "Restart invalido: F${START_H}-F${END_H}" >&2
  exit 2
}
test -d "$RESTART_DIR" || { echo "Restart directory missing: $RESTART_DIR" >&2; exit 3; }

mapfile -t RST < <(find "$RESTART_DIR" -maxdepth 1 -type f -name 'wrfrst_d01_*' -print | sort)
((${#RST[@]} > 0)) || { echo "No wrfrst_d01_* found in checkpoint F${START_H}" >&2; exit 4; }
test -s "$RESTART_DIR/wrfbdy_d01" || { echo "wrfbdy_d01 missing/empty" >&2; exit 5; }

rm -rf "$WORK" "$OUTPUT"
mkdir -p "$WORK/run" "$OUTPUT"

# Work only on copies. Never chmod/chown the original checkpoint directory.
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

# The DTCenter image runs the WRF process as UID 9999. A bind mount keeps the
# host ownership, so chmod INSIDE the container is not allowed on /run.
# Make the temporary host copy writable BEFORE mounting it. This avoids the
# EACCES/Operation-not-permitted loop seen in previous attempts.
chmod -R a+rwX "$WORK"

python3 - "$WORK/run/namelist.input" "$SEG_H" "$HIST" <<'PY'
import re, sys
p, hours, hist = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
s = open(p, encoding="utf-8").read()
repls = [
    (r'(?m)^\s*run_days\s*=.*$', ' run_days = 0,'),
    (r'(?m)^\s*run_hours\s*=.*$', f' run_hours = {hours},'),
    (r'(?m)^\s*run_minutes\s*=.*$', ' run_minutes = 0,'),
    (r'(?m)^\s*run_seconds\s*=.*$', ' run_seconds = 0,'),
    (r'(?m)^\s*restart\s*=.*$', ' restart = .true.,'),
    (r'(?m)^\s*restart_interval\s*=.*$', ' restart_interval = 180,'),
    (r'(?m)^\s*history_interval\s*=.*$', f' history_interval = {hist},'),
]
for pat, new in repls:
    s = re.sub(pat, new, s)
if not re.search(r'(?m)^\s*restart\s*=', s):
    s = s.replace('&time_control', '&time_control\n restart = .true.,', 1)
if not re.search(r'(?m)^\s*restart_interval\s*=', s):
    s = s.replace(' restart = .true.,', ' restart = .true.,\n restart_interval = 180,', 1)
open(p, 'w', encoding='utf-8').write(s)
PY

# Resolve the executable from the image without assuming one WRF version.
WRFEXE="$(docker run --rm "$IMAGE" /bin/bash -lc "find /comsoftware/wrf -type f -path '*/main/wrf.exe' -print -quit 2>/dev/null || true")"
if [[ -z "$WRFEXE" ]]; then
  echo "wrf.exe not found in WRF container" >&2
  exit 7
fi
echo "Using WRF executable: $WRFEXE"

# Do NOT chmod the bind-mounted /run from inside the container. The image runs
# as UID 9999 and the host-side chmod above already grants write permission.
# The output files are created by the shell before MPI starts.
touch "$WORK/run/rsl.out.restart" "$WORK/run/rsl.error.restart"
chmod 666 "$WORK/run/rsl.out.restart" "$WORK/run/rsl.error.restart"

set +e
docker run --rm \
  -e OMPI_ALLOW_RUN_AS_ROOT=1 \
  -e OMPI_ALLOW_RUN_AS_ROOT_CONFIRM=1 \
  -e WRF_MPI_PROCS="$MPI_PROCS" \
  -v "$WORK/run:/run" \
  "$IMAGE" /bin/bash -lc '
    set -euo pipefail
    cd /run
    test -r namelist.input
    test -r wrfbdy_d01
    ls wrfrst_d01_* >/dev/null
    test -x "'"$WRFEXE"'"
    echo "Starting WRF restart in $(pwd)"
    mpirun --allow-run-as-root --oversubscribe -np "${WRF_MPI_PROCS:-8}" \
      "'"$WRFEXE"'" > /run/rsl.out.restart 2>&1
  '
STATUS=$?
set -e

# Always preserve the WRF log for diagnosis, including failed segments.
cp -f "$WORK/run/rsl.out.restart" "$OUTPUT/" 2>/dev/null || true
cp -f "$WORK/run/rsl.error.restart" "$OUTPUT/" 2>/dev/null || true

if (( STATUS != 0 )); then
  echo "WRF restart F${START_H}-F${END_H} failed with exit code ${STATUS}" >&2
  tail -n 120 "$WORK/run/rsl.out.restart" 2>/dev/null || true
  exit "$STATUS"
fi

# A segment is successful only if WRF produced both forecast output and a
# restart checkpoint for the next segment. Never mark a partial run as valid.
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
