#!/usr/bin/env bash
set -euo pipefail

# Use the last known-good segmented executor by COMMIT SHA (not blob SHA),
# then apply the Brasil 15 km stability patch locally.
BASE_URL="https://raw.githubusercontent.com/progames12301-hash/sideral-backend/d54b23e6df283544350e1803b225de79e61f7afc/wrf/run_gfs_segment.sh"
TMP_SCRIPT="/tmp/sideral-run-gfs-segment-base.sh"
curl -fsSL --retry 3 --connect-timeout 20 --max-time 120 "$BASE_URL" -o "$TMP_SCRIPT"

python3 - "$TMP_SCRIPT" <<'PY'
from pathlib import Path
import re
import sys

p = Path(sys.argv[1])
s = p.read_text(encoding='utf-8')

# The base executor has changed over time, so do not depend on an exact
# tuple/replacement anchor. Patch the generated namelist structurally.
match = re.search(r'(?m)^(\s*time_step\s*=\s*)\d+(\s*,\s*)$', s)
if not match:
    raise SystemExit('time_step setting not found in base executor')
s = s[:match.start()] + match.group(1) + '60' + match.group(2) + s[match.end():]

# Add the stability controls immediately after time_step when absent.
if not re.search(r'(?m)^\s*w_damping\s*=', s):
    anchor = re.search(r'(?m)^\s*time_step\s*=\s*60\s*,\s*$', s)
    if not anchor:
        raise SystemExit('patched time_step anchor not found')
    insertion = anchor.group(0) + '\n w_damping = 1,\n epssm = 0.2,'
    s = s[:anchor.start()] + insertion + s[anchor.end():]
elif not re.search(r'(?m)^\s*epssm\s*=', s):
    anchor = re.search(r'(?m)^\s*w_damping\s*=.*$', s)
    s = s[:anchor.end()] + '\n epssm = 0.2,' + s[anchor.end():]

# Remove the misleading legacy 4 km label wherever it occurs.
s = s.replace('WRF 4 KM', 'WRF Brasil 15 KM')
s = s.replace('WRF 4 km', 'WRF Brasil 15 km')

p.write_text(s, encoding='utf-8')
PY

chmod +x "$TMP_SCRIPT"
exec "$TMP_SCRIPT"
