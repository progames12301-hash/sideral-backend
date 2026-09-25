#!/usr/bin/env bash
set -euo pipefail

# Compatibilidade com os adaptadores existentes de 7 km.
# e_we              = 390,
# e_sn              = 360,
# e_we = 390,
# e_sn = 360,
# dx = 4000,
# dy = 4000,
# time_step = 24,

ROOT="${GITHUB_WORKSPACE:-$PWD}"
LEGACY="$ROOT/wrf/run_wrf_with_source_sudeste_legacy.sh"
[[ -f "$LEGACY" ]] || { echo "Executor legado ausente: $LEGACY" >&2; exit 2; }

TMP="$(mktemp "$ROOT/wrf/run_wrf_with_source_sudeste.XXXXXX.sh")"
trap 'rm -f "$TMP"' EXIT
cp -f "$LEGACY" "$TMP"

python3 - "$TMP" <<'PY'
from pathlib import Path
import sys

p = Path(sys.argv[1])
s = p.read_text(encoding='utf-8')
replacements = {
    'e_we              = 390,': 'e_we              = 223,',
    'e_sn              = 360,': 'e_sn              = 206,',
    'e_we = 390,': 'e_we = 223,',
    'e_sn = 360,': 'e_sn = 206,',
    'dx = 4000,': 'dx = 7000,',
    'dy = 4000,': 'dy = 7000,',
    'time_step = 24,': 'time_step = 42,',
    "map_proj = 'lambert',": "map_proj = 'mercator',",
    'truelat1  = -15.0,': 'truelat1  = -19.50,',
}
for old, new in replacements.items():
    if old not in s:
        raise SystemExit(f'Padrao ausente no executor legado: {old}')
    s = s.replace(old, new)
p.write_text(s, encoding='utf-8')
PY

chmod +x "$TMP"
exec bash "$TMP"
