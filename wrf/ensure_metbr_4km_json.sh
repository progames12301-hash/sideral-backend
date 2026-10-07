#!/usr/bin/env bash
set -euo pipefail
python3 "$(dirname "$0")/ensure_metbr_4km_json.py" "${1:-metbr_publish}"
test -s "${1:-metbr_publish}/metbr_wrf_4km.json"
