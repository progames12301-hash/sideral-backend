#!/usr/bin/env bash
set -euo pipefail

# Compatibilidade com os adaptadores existentes de 7 km.
ROOT="${GITHUB_WORKSPACE:-$PWD}"
LEGACY="$ROOT/wrf/run_wrf_with_source_sudeste_legacy.sh"
[[ -f "$LEGACY" ]] || { echo "Executor legado ausente: $LEGACY" >&2; exit 2; }

TMP="$(mktemp "$ROOT/wrf/run_wrf_with_source_sudeste.XXXXXX.sh")"
trap 'rm -f "$TMP"' EXIT
cp -f "$LEGACY" "$TMP"

python3 - "$TMP" <<'PY'
from pathlib import Path
import re
import sys

p = Path(sys.argv[1])
s = p.read_text(encoding='utf-8')

# Uma substituição por variável. Não encadeia 390 -> 223 e depois tenta
# substituir 223 novamente, que era a causa da falha observada.
patterns = [
    (r'(?m)^(\s*e_we\s*=\s*)\d+(\s*,\s*)$', r'\g<1>223\g<2>'),
    (r'(?m)^(\s*e_sn\s*=\s*)\d+(\s*,\s*)$', r'\g<1>206\g<2>'),
    (r'(?m)^(\s*dx\s*=\s*)\d+(\s*,\s*)$', r'\g<1>7000\g<2>'),
    (r'(?m)^(\s*dy\s*=\s*)\d+(\s*,\s*)$', r'\g<1>7000\g<2>'),
    (r'(?m)^(\s*time_step\s*=\s*)\d+(\s*,\s*)$', r'\g<1>42\g<2>'),
    (r"(?m)^(\s*map_proj\s*=\s*)'[^']+'(\s*,\s*)$", r"\g<1>'mercator'\g<2>"),
    (r'(?m)^(\s*truelat1\s*=\s*)[-+0-9.]+(\s*,\s*)$', r'\g<1>-19.50\g<2>'),
]
for pattern, replacement in patterns:
    s, n = re.subn(pattern, replacement, s)
    if n == 0:
        raise SystemExit(f'Padrao ausente no executor legado: {pattern}')

p.write_text(s, encoding='utf-8')
PY

chmod +x "$TMP"
exec bash "$TMP"
