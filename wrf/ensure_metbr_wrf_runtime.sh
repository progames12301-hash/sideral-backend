#!/usr/bin/env bash
set -euo pipefail

# METBR WRF 4.3 runtime support. The DTCenter image contains the normal
# WRF run/ tables, but its WRF 4.3 build can be compiled with CLWRFGHG.
# In that case CAMtr_volume_mixing_ratio is a real runtime dependency and
# must exist in the WRF working directory. Do not disable physics to hide it.

RUN_DIR="${1:?WRF run directory required}"
mkdir -p "$RUN_DIR"

WRF_RELEASE="${WRF_RUNTIME_RELEASE:-v4.3}"
BASE="https://raw.githubusercontent.com/wrf-model/WRF/${WRF_RELEASE}/run"

# Files required by the METBR namelist/physics path. Existing files from the
# official WRF image are retained; only missing files are downloaded.
REQUIRED=(
  VEGPARM.TBL
  LANDUSE.TBL
  GENPARM.TBL
  SOILPARM.TBL
  MPTABLE.TBL
  URBPARM.TBL
  RRTMG_LW_DATA
  RRTMG_SW_DATA
  CAM_ABS_DATA
  ozone.formatted
  ozone_lat.formatted
  ozone_plev.formatted
  aerosol.formatted
  aerosol_lat.formatted
  aerosol_lon.formatted
  aerosol_plev.formatted
)

# WRF 4.3/CLWRFGHG uses the pre-4.4 AR5 scenario files. RCP8.5 is the
# historical default used by WRF when the generic CAMtr file is linked.
GHG_SCENARIOS=(
  CAMtr_volume_mixing_ratio.RCP4.5
  CAMtr_volume_mixing_ratio.RCP6
  CAMtr_volume_mixing_ratio.RCP8.5
  CAMtr_volume_mixing_ratio.A1B
  CAMtr_volume_mixing_ratio.A2
)

fetch_missing() {
  local name="$1"
  if [[ -s "$RUN_DIR/$name" ]]; then
    return 0
  fi
  echo "METBR runtime support missing: $name"
  curl -fL --retry 4 --retry-delay 2 --connect-timeout 20 --max-time 600 \
    -o "$RUN_DIR/$name" "$BASE/$name"
  test -s "$RUN_DIR/$name" || {
    echo "Downloaded runtime file is empty: $name" >&2
    exit 71
  }
}

for file in "${REQUIRED[@]}"; do
  fetch_missing "$file"
done
for file in "${GHG_SCENARIOS[@]}"; do
  fetch_missing "$file"
done

# The WRF 4.3 CLWRFGHG code reads the unqualified filename. Keep an actual
# file (not a broken symlink) in the run directory for portable Docker runs.
cp -f "$RUN_DIR/CAMtr_volume_mixing_ratio.RCP8.5" \
      "$RUN_DIR/CAMtr_volume_mixing_ratio"

# Fail before real.exe/wrf.exe if an active runtime dependency is absent.
# This prevents the old "one missing table per run" failure pattern.
for file in "${REQUIRED[@]}" "${GHG_SCENARIOS[@]}" CAMtr_volume_mixing_ratio; do
  test -s "$RUN_DIR/$file" || {
    echo "METBR WRF runtime support validation failed: $file" >&2
    exit 72
  }
done

printf 'METBR WRF runtime support validated: %s files + generic CAMtr_volume_mixing_ratio\n' \
  "$(( ${#REQUIRED[@]} + ${#GHG_SCENARIOS[@]} ))"
