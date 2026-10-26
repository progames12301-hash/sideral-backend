#!/usr/bin/env bash
set -euo pipefail

ROOT="${GITHUB_WORKSPACE:-$PWD}"
LEGACY="$ROOT/wrf/run_gfs_segment_sudeste_legacy.sh"
[[ -f "$LEGACY" ]] || { echo "Executor GFS legado ausente: $LEGACY" >&2; exit 2; }

TMP="$(mktemp "$ROOT/wrf/run_gfs_segment_sudeste.XXXXXX.sh")"
trap 'rm -f "$TMP"' EXIT
cp -f "$LEGACY" "$TMP"

python3 - "$TMP" <<'PY'
from pathlib import Path
import sys

p = Path(sys.argv[1])
s = p.read_text(encoding='utf-8')
# O legado fazia uma segunda adaptação e devolvia o Sudeste para 4 km.
# Corrigimos os valores que ele injeta em run_gfs_test.sh.
replacements = {
    "' e_we              = 300,': ' e_we              = 390,'": "' e_we              = 300,': ' e_we              = 223,'",
    "' e_sn              = 360,': ' e_sn              = 360,'": "' e_sn              = 360,': ' e_sn              = 206,'",
    "' e_we = 300,': ' e_we = 390,'": "' e_we = 300,': ' e_we = 223,'",
    "' e_sn = 360,': ' e_sn = 360,'": "' e_sn = 360,': ' e_sn = 206,'",
    "' truelat1  = -25.0,': ' truelat1  = -15.0,'": "' truelat1  = -25.0,': ' truelat1  = -19.50,'",
}
for old, new in replacements.items():
    if old not in s:
        raise SystemExit(f'Padrao GFS legado ausente: {old}')
    s = s.replace(old, new)
anchor = "    ' stand_lon = -53.45,': ' stand_lon = -46.50,',\n"
insert = anchor + "    ' map_proj = \\'lambert\\',': ' map_proj = \\'mercator\\',',\n"
if anchor not in s:
    raise SystemExit('Anchor map_proj GFS ausente')
s = s.replace(anchor, insert, 1)
p.write_text(s, encoding='utf-8')
PY

chmod +x "$TMP"
exec bash "$TMP"
