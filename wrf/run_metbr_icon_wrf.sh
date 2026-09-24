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
# METBR 4 km uses 20 s instead of 24 s: with the domain Lambert map factor
# (~1.01), 24 s produces dt/dx=6.04 s/km and crosses the configured WRF
# reasonable-time-step guard of 6 s/km.
export WRF_TIME_STEP="${WRF_TIME_STEP:-20}"

(( WRF_END_HOUR > WRF_START_HOUR && WRF_START_HOUR % 3 == 0 && WRF_END_HOUR % 3 == 0 && WRF_END_HOUR <= 42 )) || { echo "METBR segmento invalido: inicio/fim precisam ser multiplos de 3 h entre F000 e F042" >&2; exit 2; }

if (( WRF_START_HOUR > 0 )); then
  : "${WRF_RESTART_DIR:?WRF_RESTART_DIR required for restart}"
  exec "$ROOT/wrf/run_wrf_restart_segment.sh" "$WRF_START_HOUR" "$WRF_END_HOUR" "$WRF_RESTART_DIR"
fi

python3 "$ROOT/wrf/prepare_restart_segment.py" --start-hour "$WRF_START_HOUR" --end-hour "$WRF_END_HOUR" --root "$ROOT"

LEGACY_COMMIT="08b38047f2103022bd1b40eefe4a84c0ef716d80"
LEGACY="$ROOT/.metbr_legacy_run_icon_wrf.sh"
if ! git cat-file -e "${LEGACY_COMMIT}:wrf/run_icon_wrf.sh" 2>/dev/null; then
  git fetch --no-tags --depth=1 origin "$LEGACY_COMMIT"
fi
git show "${LEGACY_COMMIT}:wrf/run_icon_wrf.sh" > "$LEGACY"
chmod +x "$LEGACY"

# The legacy path calls run_wrf_with_source.sh directly. Normalize its MPI
# count in a temporary copy so METBR remains at 8 ranks without touching CIM.
SOURCE_RUN="$ROOT/wrf/run_wrf_with_source.sh"
SOURCE_RUN_ORIG="$ROOT/wrf/.metbr_run_wrf_with_source.original"
SOURCE_RUN_8="$ROOT/wrf/.metbr_run_wrf_with_source.8mpi"
cp -f "$SOURCE_RUN" "$SOURCE_RUN_ORIG"
cp -f "$SOURCE_RUN" "$SOURCE_RUN_8"
sed -i -E 's/(mpirun[^\n]*-np[[:space:]]+)4([^0-9]|$)/\18\2/g; s/(mpirun[^\n]*--np[=[:space:]]*)4([^0-9]|$)/\18\2/g' "$SOURCE_RUN_8"
# WRF 4.3's RRTMG startup checks for CAMtr_volume_mixing_ratio when ghg_input
# is left at its default. METBR does not use time-varying GHG forcing, so
# disable that optional table read instead of shipping an unrelated forcing file.
sed -i -E '/^[[:space:]]*fractional_seaice[[:space:]]*=[[:space:]]*1,/a\ ghg_input = 0,' "$SOURCE_RUN_8"
chmod +x "$SOURCE_RUN_8"
restore_source_run(){
  if [[ -f "$SOURCE_RUN_ORIG" ]]; then
    mv -f "$SOURCE_RUN_ORIG" "$SOURCE_RUN"
  fi
  rm -f "$SOURCE_RUN_8"
}
trap restore_source_run EXIT
mv -f "$SOURCE_RUN_8" "$SOURCE_RUN"

# Also normalize any literal 4-rank mpirun in the temporary legacy script.
sed -i -E 's/(mpirun[^\n]*-np[[:space:]]+)4([^0-9]|$)/\18\2/g; s/(mpirun[^\n]*--np[=[:space:]]*)4([^0-9]|$)/\18\2/g' "$LEGACY"
export OMP_NUM_THREADS="${OMP_NUM_THREADS:-1}"
exec "$LEGACY"
