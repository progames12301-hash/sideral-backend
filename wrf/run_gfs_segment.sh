#!/usr/bin/env bash
set -euo pipefail

# Keep the existing segmented/GFS implementation, but apply a stability patch
# before it generates and launches WRF. The previous 15 km run reached repeated
# vertical-CFL warnings and ended with MPI rank 2 / SIGSEGV (139).
BASE_URL="https://raw.githubusercontent.com/progames12301-hash/sideral-backend/66241965485f26d812294a00c479d436184d7cc8/wrf/run_gfs_segment.sh"
TMP_SCRIPT="/tmp/sideral-run-gfs-segment-base.sh"
curl -fsSL --retry 3 --connect-timeout 20 --max-time 120 "$BASE_URL" -o "$TMP_SCRIPT"

python3 - "$TMP_SCRIPT" <<'PY'
from pathlib import Path
import sys
p = Path(sys.argv[1])
s = p.read_text(encoding='utf-8')
# The base script's Brasil adaptation changes the WRF time step from 18 to 90 s.
# Make that operational setting 60 s for this 15 km run.
s = s.replace("(' time_step = 18,', ' time_step = 90,')", "(' time_step = 18,', ' time_step = 60,')")
# Make the generated namelist explicitly enable vertical-velocity damping and
# modest sound-wave off-centering when the 60 s timestep is inserted.
s = s.replace("(' time_step = 18,', ' time_step = 60,'),", "(' time_step = 18,', ' time_step = 60,'),\n              (' time_step = 60,', ' time_step = 60,\\n w_damping = 1,\\n epssm = 0.2,'),")
# Remove the misleading legacy 4 km label from the execution log.
s = s.replace('=== WRF 4 KM SEGMENTO F{start:03d}-F{end:03d} ===', '=== WRF Brasil 15 KM SEGMENTO F{start:03d}-F{end:03d} ===')
p.write_text(s, encoding='utf-8')
PY

chmod +x "$TMP_SCRIPT"
exec "$TMP_SCRIPT"
