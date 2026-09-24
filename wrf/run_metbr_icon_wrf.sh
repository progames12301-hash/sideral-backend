#!/usr/bin/env bash
set -euo pipefail

# METBR-only ICON dispatcher. CIM must never enter this path.
ROOT="${GITHUB_WORKSPACE:-$PWD}"
WRF_START_HOUR="${WRF_START_HOUR:-0}"
WRF_END_HOUR="${WRF_END_HOUR:-$WRF_RUN_HOURS}"
WRF_RUN_HOURS="${WRF_RUN_HOURS:-$((WRF_END_HOUR-WRF_START_HOUR))}"
export SOURCE_MODEL=icon WRF_INPUT_MODEL=ICON WRF_DATA_SOURCE=ICON
export WRF_INITIALIZATION_MODEL=ICON WRF_NO_FALLBACK=true
export WRF_REFLECTIVITY_SOURCE=REFL_10CM_NATIVE WRF_NATIVE_GRID=true
export WRF_MPI_PROCS="${WRF_MPI_PROCS:-8}"
export WRF_TIME_STEP="${WRF_TIME_STEP:-20}"

(( WRF_END_HOUR > WRF_START_HOUR && WRF_START_HOUR % 3 == 0 && WRF_END_HOUR % 3 == 0 && WRF_END_HOUR <= 42 )) || { echo "METBR segmento invalido: inicio/fim precisam ser multiplos de 3 h entre F000 e F042" >&2; exit 2; }

if (( WRF_START_HOUR > 0 )); then
  : "${WRF_RESTART_DIR:?WRF_RESTART_DIR required for restart}"
  exec "$ROOT/wrf/run_wrf_restart_segment.sh" "$WRF_START_HOUR" "$WRF_END_HOUR" "$WRF_RESTART_DIR"
fi

# F000-F003 is only the first compute segment. The boundary file must cover
# the complete F000-F042 forecast because all later restart segments reuse it.
export WRF_SIM_END_HOUR="$WRF_END_HOUR"
export WRF_BOUNDARY_END_HOUR=42
export WRF_END_HOUR=42
export WRF_RUN_HOURS=42

python3 "$ROOT/wrf/prepare_restart_segment.py" --start-hour "$WRF_START_HOUR" --end-hour "$WRF_SIM_END_HOUR" --root "$ROOT"

LEGACY_COMMIT="08b38047f2103022bd1b40eefe4a84c0ef716d80"
LEGACY="$ROOT/.metbr_legacy_run_icon_wrf.sh"
if ! git cat-file -e "${LEGACY_COMMIT}:wrf/run_icon_wrf.sh" 2>/dev/null; then
  git fetch --no-tags --depth=1 origin "$LEGACY_COMMIT"
fi
git show "${LEGACY_COMMIT}:wrf/run_icon_wrf.sh" > "$LEGACY"
chmod +x "$LEGACY"

SOURCE_RUN="$ROOT/wrf/run_wrf_with_source.sh"
SOURCE_RUN_ORIG="$ROOT/wrf/.metbr_run_wrf_with_source.original"
SOURCE_RUN_8="$ROOT/wrf/.metbr_run_wrf_with_source.8mpi"
cp -f "$SOURCE_RUN" "$SOURCE_RUN_ORIG"
cp -f "$SOURCE_RUN" "$SOURCE_RUN_8"

# The legacy ICON fetcher sees F042 so it downloads all forcing required by
# real.exe. WRF itself still runs only the requested first segment.
sed -i -E 's/(WRF_SEGMENT_HOURS=\$\(\(WRF_END_HOUR-WRF_START_HOUR\)\))/WRF_SEGMENT_HOURS=$((WRF_SIM_END_HOUR-WRF_START_HOUR))/g' "$SOURCE_RUN_8"
sed -i 's/--max-hour "\$WRF_END_HOUR"/--max-hour "\$WRF_BOUNDARY_END_HOUR"/' "$SOURCE_RUN_8"

# WRF's wrfbdy_d01 must be generated for the complete horizon. Temporarily
# expand the real.exe namelist to F042, then restore the F000-F003 namelist
# before wrf.exe starts. RUN_DATE/RUN_CYCLE are passed into the container.
sed -i '/^    echo "=== REAL.EXE ==="/i\    cp namelist.input namelist.segment.input\n    python3 - <<'\''PYBOUNDARY'\''\nimport datetime as dt, os, re\np="namelist.input"\ns=open(p).read()\nbase=dt.datetime.strptime(os.environ["RUN_DATE"]+os.environ["RUN_CYCLE"], "%Y%m%d%H")\nend=base+dt.timedelta(hours=int(os.environ["WRF_BOUNDARY_END_HOUR"]))\ndef put(k,v):\n    global s\n    s=re.sub(rf"(?m)^\\s*{re.escape(k)}\\s*=.*$", f" {k} = {v},", s)\nfor k,v in (("run_days",0),("run_hours",int(os.environ["WRF_BOUNDARY_END_HOUR"])),("run_minutes",0),("run_seconds",0),("end_year",end.year),("end_month",end.month),("end_day",end.day),("end_hour",end.hour)):\n    put(k,v)\nopen(p,"w").write(s)\nPYBOUNDARY' "$SOURCE_RUN_8"
sed -i '/^    test -f wrfinput_d01$/a\    if test -f namelist.segment.input; then cp -f namelist.segment.input namelist.input; fi' "$SOURCE_RUN_8"
sed -i -E 's/(mpirun[^\n]*-np[[:space:]]+)4([^0-9]|$)/\18\2/g; s/(mpirun[^\n]*--np[=[:space:]]*)4([^0-9]|$)/\18\2/g' "$SOURCE_RUN_8"

RUNTIME_HELPER="$ROOT/wrf/ensure_metbr_wrf_runtime.sh"
RUNTIME_HELPER_IN_WORK="$ROOT/wrf_work/.metbr_ensure_runtime.sh"
mkdir -p "$ROOT/wrf_work"
cp -f "$RUNTIME_HELPER" "$RUNTIME_HELPER_IN_WORK"
chmod +x "$RUNTIME_HELPER_IN_WORK"
sed -i "/^[[:space:]]*cd \/work[[:space:]]*$/a\\    /bin/bash /work/.metbr_ensure_runtime.sh /work/run" "$SOURCE_RUN_8"
sed -i 's/-e LOCAL_USER_ID="\$HOST_UID" \\/-e LOCAL_USER_ID="\$HOST_UID" -e RUN_DATE="\$RUN_DATE" -e RUN_CYCLE="\$RUN_CYCLE" -e WRF_BOUNDARY_END_HOUR="\$WRF_BOUNDARY_END_HOUR" -e WRF_SIM_END_HOUR="\$WRF_SIM_END_HOUR" \\/' "$SOURCE_RUN_8"
chmod +x "$SOURCE_RUN_8"

restore_source_run(){
  if [[ -f "$SOURCE_RUN_ORIG" ]]; then
    mv -f "$SOURCE_RUN_ORIG" "$SOURCE_RUN"
  fi
  rm -f "$SOURCE_RUN_8"
}
trap restore_source_run EXIT
mv -f "$SOURCE_RUN_8" "$SOURCE_RUN"

sed -i -E 's/(mpirun[^\n]*-np[[:space:]]+)4([^0-9]|$)/\18\2/g; s/(mpirun[^\n]*--np[=[:space:]]*)4([^0-9]|$)/\18\2/g' "$LEGACY"
export OMP_NUM_THREADS="${OMP_NUM_THREADS:-1}"
exec "$LEGACY"
