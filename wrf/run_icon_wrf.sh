#!/usr/bin/env bash
set -euo pipefail

# METBR ICON dispatcher. Cold start delegates to the verified ICON implementation
# that existed before the restart patch; continuation uses only wrfrst + wrfbdy.
ROOT="${GITHUB_WORKSPACE:-$PWD}"
WRF_START_HOUR="${WRF_START_HOUR:-0}"
WRF_END_HOUR="${WRF_END_HOUR:-$WRF_RUN_HOURS}"
WRF_RUN_HOURS="${WRF_RUN_HOURS:-$((WRF_END_HOUR-WRF_START_HOUR))}"
export SOURCE_MODEL=icon WRF_INPUT_MODEL=ICON WRF_DATA_SOURCE=ICON
export WRF_INITIALIZATION_MODEL=ICON WRF_NO_FALLBACK=true
export WRF_REFLECTIVITY_SOURCE=REFL_10CM_NATIVE WRF_NATIVE_GRID=true

(( WRF_END_HOUR > WRF_START_HOUR && WRF_END_HOUR <= 40 )) || { echo "METBR segmento invalido F${WRF_START_HOUR}-F${WRF_END_HOUR}" >&2; exit 2; }

if (( WRF_START_HOUR > 0 )); then
  : "${WRF_RESTART_DIR:?WRF_RESTART_DIR required for restart}"
  exec "$ROOT/wrf/run_wrf_restart_segment.sh" "$WRF_START_HOUR" "$WRF_END_HOUR" "$WRF_RESTART_DIR"
fi

# Recover the verified ICON cold-start script from repository history. This keeps
# the existing WPS/ICON preprocessing intact instead of duplicating 150+ lines.
LEGACY_COMMIT="08b38047f2103022bd1b40eefe4a84c0ef716d80"
LEGACY="$ROOT/.metbr_legacy_run_icon_wrf.sh"
if ! git cat-file -e "${LEGACY_COMMIT}:wrf/run_icon_wrf.sh" 2>/dev/null; then
  git fetch --no-tags --depth=1 origin "$LEGACY_COMMIT"
fi
git show "${LEGACY_COMMIT}:wrf/run_icon_wrf.sh" > "$LEGACY"
chmod +x "$LEGACY"
exec "$LEGACY"
