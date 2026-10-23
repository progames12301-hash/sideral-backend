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
if [[ "${WRF2_TOMORROW:-0}" == "1" ]]; then
  ICON_SOURCE_DATE="$(date -u -d "${RUN_DATE} ${RUN_CYCLE}:00 UTC -24 hours" +%Y%m%d)"
  ICON_SOURCE_CYCLE="$RUN_CYCLE"
  export ICON_SOURCE_DATE ICON_SOURCE_CYCLE WRF2_ICON_OFFSET_HOURS=24
  python3 - "$LEGACY" <<'PYICONOFFSET'
import pathlib, sys
p=pathlib.Path(sys.argv[1])
s=p.read_text(encoding="utf-8")
old='    --date "$RUN_DATE" ' + chr(92) + '\n' + '    --cycle "$RUN_CYCLE" ' + chr(92) + '\n' + '    --step "$H" ' + chr(92) + '\n'
new='    --date "${ICON_SOURCE_DATE:-$RUN_DATE}" ' + chr(92) + '\n' + '    --cycle "${ICON_SOURCE_CYCLE:-$RUN_CYCLE}" ' + chr(92) + '\n' + '    --step "$((H+${WRF2_ICON_OFFSET_HOURS:-0}))" ' + chr(92) + '\n'
if s.count(old) != 1:
    raise SystemExit(f"Esperava 1 chamada de download ICON para ajustar, encontrei {s.count(old)}")
p.write_text(s.replace(old,new,1),encoding="utf-8")
PYICONOFFSET
  echo "METBR WRF2: validade ${RUN_DATE} ${RUN_CYCLE}Z; inicialização ICON ${ICON_SOURCE_DATE} ${ICON_SOURCE_CYCLE}Z F024+"
fi
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

# run_wrf_with_source writes the WPS namelist and the WRF namelist from the
# same END_ISO. Keep WPS at F042, then reset only namelist.input to F000-F003.
sed -i '/^chmod -R a+rwX "\$WORK"$/i\SIM_END_Y=$(date -u -d "${RUN_DATE} ${RUN_CYCLE}:00 UTC +${WRF_SIM_END_HOUR} hours" +%Y)\nSIM_END_M=$(date -u -d "${RUN_DATE} ${RUN_CYCLE}:00 UTC +${WRF_SIM_END_HOUR} hours" +%m)\nSIM_END_D=$(date -u -d "${RUN_DATE} ${RUN_CYCLE}:00 UTC +${WRF_SIM_END_HOUR} hours" +%d)\nSIM_END_H=$(date -u -d "${RUN_DATE} ${RUN_CYCLE}:00 UTC +${WRF_SIM_END_HOUR} hours" +%H)\nsed -i -E "s/^ end_year = .*/ end_year = ${SIM_END_Y},/; s/^ end_month = .*/ end_month = ${SIM_END_M},/; s/^ end_day = .*/ end_day = ${SIM_END_D},/; s/^ end_hour = .*/ end_hour = ${SIM_END_H},/; s/^ run_hours = .*/ run_hours = ${WRF_SIM_END_HOUR},/" "$WORK/namelist.input"' "$SOURCE_RUN_8"

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
