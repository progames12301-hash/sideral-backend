#!/usr/bin/env bash
set -euo pipefail

# Use the last known-good segmented executor by COMMIT SHA (not blob SHA),
# then apply the Brasil 15 km stability patch locally.
BASE_URL="https://raw.githubusercontent.com/progames12301-hash/sideral-backend/d54b23e6df283544350e1803b225de79e61f7afc/wrf/run_gfs_segment.sh"
TMP_SCRIPT="/tmp/sideral-run-gfs-segment-base.sh"
curl -fsSL --retry 3 --connect-timeout 20 --max-time 120 "$BASE_URL" -o "$TMP_SCRIPT"

python3 - "$TMP_SCRIPT" <<'PY'
from pathlib import Path
import sys

p = Path(sys.argv[1])
s = p.read_text(encoding='utf-8')

# Brasil 15 km: 60 s timestep to reduce the vertical-CFL risk seen in run #15.
s = s.replace("(' time_step = 18,', ' time_step = 90,')", "(' time_step = 18,', ' time_step = 60,')")

# Explicit vertical damping and modest sound-wave off-centering.
needle = "(' time_step = 18,', ' time_step = 60,'),"
replacement = "(' time_step = 18,', ' time_step = 60,'),\n              (' time_step = 60,', ' time_step = 60,' + chr(10) + ' w_damping = 1,' + chr(10) + ' epssm = 0.2,'),"
if needle not in s:
    raise SystemExit('stability patch anchor not found in base executor')
s = s.replace(needle, replacement, 1)

# Eliminate the misleading legacy 4 km execution label.
s = s.replace('=== WRF 4 KM SEGMENTO F{start:03d}-F{end:03d} ===', '=== WRF Brasil 15 KM SEGMENTO F{start:03d}-F{end:03d} ===')

p.write_text(s, encoding='utf-8')
PY

chmod +x "$TMP_SCRIPT"
exec "$TMP_SCRIPT"
