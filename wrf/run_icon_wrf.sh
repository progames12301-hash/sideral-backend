#!/usr/bin/env bash
set -euo pipefail

ROOT="${GITHUB_WORKSPACE:-$PWD}"
WRF_RUN_HOURS="${WRF_RUN_HOURS:-6}"
WRF_START_HOUR="${WRF_START_HOUR:-0}"
WRF_END_HOUR="${WRF_END_HOUR:-$WRF_RUN_HOURS}"
WRF_RESTART_DIR="${WRF_RESTART_DIR:-}"
ICON_SOURCE_XSIZE="${ICON_SOURCE_XSIZE:-93}"
ICON_SOURCE_YSIZE="${ICON_SOURCE_YSIZE:-81}"
ICON_SOURCE_XFIRST="${ICON_SOURCE_XFIRST:--65.0}"
ICON_SOURCE_YFIRST="${ICON_SOURCE_YFIRST:--38.0}"
ICON_SOURCE_XINC="${ICON_SOURCE_XINC:-0.25}"
ICON_SOURCE_YINC="${ICON_SOURCE_YINC:-0.25}"
ICON_REGRID_IMAGE="deutscherwetterdienst/regrid:icon-grids"
RAW_DIR="$ROOT/icon_source_raw"
REG_DIR="$ROOT/icon_source_regular"
REGRID_DIR="$ROOT/icon_regrid"
WORK="$ROOT/wrf_work"
HOST_UID="$(id -u)"
HOST_GID="$(id -g)"

log(){ printf '\n===== %s =====\n' "$*"; }

(( WRF_START_HOUR >= 0 && WRF_END_HOUR > WRF_START_HOUR && WRF_END_HOUR <= 72 )) || { echo "Segmento ICON invalido: F${WRF_START_HOUR}-F${WRF_END_HOUR}" >&2; exit 2; }

# Restart: never run WPS/real.exe again. The previous wrfrst is the initial state.
if (( WRF_START_HOUR > 0 )); then
  : "${WRF_RESTART_DIR:?WRF_RESTART_DIR required for ICON restart segments}"
  test -d "$WRF_RESTART_DIR" || { echo "Restart directory not found: $WRF_RESTART_DIR" >&2; exit 21; }
  mapfile -t RESTART_FILES < <(find "$WRF_RESTART_DIR" -maxdepth 1 -type f -name 'wrfrst_d01_*' | sort)
  ((${#RESTART_FILES[@]} > 0)) || { echo "No wrfrst_d01_* found for F${WRF_START_HOUR}" >&2; exit 22; }
  mkdir -p "$WORK/run"
  rm -f "$WORK/run/wrfrst_d01_"*
  for FILE in "${RESTART_FILES[@]}"; do cp -f "$FILE" "$WORK/run/"; done
  test -s "$WORK/run/wrfrst_d01_$(find "$WORK/run" -maxdepth 1 -type f -name 'wrfrst_d01_*' -printf '%f\n' | head -1 | cut -d_ -f3-)" 2>/dev/null || true
  export WRF_RESTART=true
  export WRF_COLD_START=false
  export SOURCE_MODEL=icon
  export WRF_INPUT_MODEL=ICON
  export WRF_DATA_SOURCE=ICON
  export WRF_NO_FALLBACK=true
  export WRF_REFLECTIVITY_SOURCE=REFL_10CM_NATIVE
  export WRF_NATIVE_GRID=true
  export SOURCE_DIR="${SOURCE_DIR:-$REG_DIR}"
  export SOURCE_VTABLE="$ROOT/wrf/Vtable.ICONp"
  log "METBR ICON RESTART F${WRF_START_HOUR}-F${WRF_END_HOUR}"
  exec "$ROOT/wrf/run_wrf_restart_segment.sh" "$WRF_START_HOUR" "$WRF_END_HOUR" "$WORK/run"
fi

# Cold start: ICON -> WPS -> real.exe -> wrf.exe.
export WRF_RESTART=false
export WRF_COLD_START=true
export SOURCE_MODEL=icon
export WRF_INPUT_MODEL=ICON
export WRF_DATA_SOURCE=ICON
export WRF_NO_FALLBACK=true
export WRF_REFLECTIVITY_SOURCE=REFL_10CM_NATIVE
export WRF_NATIVE_GRID=true

# The existing ICON preprocessing/download path remains below this point.
exec "$ROOT/wrf/run_icon_wrf_cold_start.sh"
