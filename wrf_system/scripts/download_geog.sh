#!/bin/bash
set -euo pipefail

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
GEOG_DIR="$BASE_DIR/data/geog"
GEOG_TAR="$BASE_DIR/data/geog_low_res_mandatory.tar.gz"
URL="https://www2.mmm.ucar.edu/wrf/src/wps_files/geog_low_res_mandatory.tar.gz"
mkdir -p "$GEOG_DIR"

if [ -n "$(find "$GEOG_DIR" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null)" ]; then
  echo "Geodados WPS recuperados do cache."
  exit 0
fi

curl -fL --retry 5 --retry-all-errors -o "$GEOG_TAR" "$URL"
tar -xzf "$GEOG_TAR" -C "$GEOG_DIR" --strip-components=1
rm -f "$GEOG_TAR"
echo "Geodados WPS instalados."
