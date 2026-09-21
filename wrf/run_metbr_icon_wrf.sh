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
# O METBR usa 8 MPI. O legado pode conter defaults antigos de 4 MPI;
# normalizamos somente a cópia temporária executada nesta rodada.
sed -i -E 's/(mpirun[^\n]*-np[[:space:]]+)4([^0-9]|$)/\18\2/g; s/(mpirun[^\n]*--np[=[:space:]]*)4([^0-9]|$)/\18\2/g' "$LEGACY"
export OMP_NUM_THREADS="${OMP_NUM_THREADS:-1}"
exec "$LEGACY"
