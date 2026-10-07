#!/usr/bin/env bash
# Brasil WRF 21 km runner
# Publicação: wrf-brasil-12km-data / wrf_brasil_21km.json
# Grade nativa: 215x215 pontos; domínio WRF e_we/e_sn=216x216; dx=dy=21000 m.
set -euo pipefail

# Mantém o contrato de publicação existente. A geometria geográfica deve ser
# obtida dos XLAT/XLONG do próprio WRF durante a extração, nunca reconstruída
# a partir de limites aproximados no HTML.
python3 wrf/extract_wrf_json.py
