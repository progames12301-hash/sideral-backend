#!/usr/bin/env bash
set -euo pipefail

START_HOUR="${1:?start hour}"
END_HOUR="${2:?end hour}"
SEGMENT_INDEX="${3:?segment index}"
COLD_START="${4:?cold start 1/0}"
: "${GH_TOKEN:?GH_TOKEN ausente}"
: "${CHECKPOINT_TAG:?CHECKPOINT_TAG ausente}"
: "${GITHUB_REPOSITORY:?GITHUB_REPOSITORY ausente}"

ROOT="${GITHUB_WORKSPACE:-$PWD}"
INPUT="$ROOT/metbr_restart_input"
mkdir -p "$INPUT"

export WRF_TARGET_RESOLUTION_KM=4
export WRF_DX_METERS=4000
export WRF_DY_METERS=4000
export WRF_E_WE=300
export WRF_E_SN=360
export WRF_HISTORY_INTERVAL_MINUTES=60
export WRF_RUN_HOURS=$((END_HOUR-START_HOUR))
export WRF_START_HOUR="$START_HOUR"
export WRF_END_HOUR="$END_HOUR"
export WRF_DATA_SOURCE=ICON
export WRF_INPUT_MODEL=ICON
export WRF_INITIALIZATION_MODEL=ICON
export WRF_REFLECTIVITY_SOURCE=REFL_10CM_NATIVE
export WRF_NATIVE_GRID=true
export WRF_NO_FALLBACK=true

if [[ "$WRF_DATA_SOURCE" != "ICON" || "$WRF_INPUT_MODEL" != "ICON" || "$WRF_INITIALIZATION_MODEL" != "ICON" ]]; then
  echo "ERROR: METBR accepts ICON only; no GFS/ECMWF fallback is permitted." >&2
  exit 1
fi

if [[ "$COLD_START" == "1" ]]; then
  # Resolve the published ICON cycle used by the existing real ICON runner.
  python3 - <<'PY'
import datetime as dt, json, os, urllib.request
repo=os.environ['GITHUB_REPOSITORY']
url=f"https://raw.githubusercontent.com/{repo}/icon-data/metadata.json?run={os.environ.get('GITHUB_RUN_ID','0')}"
req=urllib.request.Request(url, headers={'Cache-Control':'no-cache','User-Agent':'Sideral-METBR'})
with urllib.request.urlopen(req, timeout=30) as r:
    m=json.load(r)
if str(m.get('model','')).lower() != 'icon':
    raise SystemExit(f"ICON metadata retornou modelo incorreto: {m.get('model')}")
run_date=str(m['runDate']).replace('-','')
run_cycle=''.join(ch for ch in str(m['runCycle']) if ch.isdigit()).zfill(2)[:2]
if run_cycle not in {'00','06','12','18'}:
    raise SystemExit(f'Rodada ICON invalida: {run_date} {run_cycle}')
with open('metbr_run.env','w',encoding='utf-8') as f:
    f.write(f'RUN_DATE={run_date}\nRUN_CYCLE={run_cycle}\n')
print(f'METBR ICON selecionado: {run_date} {run_cycle}Z')
PY
  source metbr_run.env
  gh release create "$CHECKPOINT_TAG" --target wrf-runner --prerelease --latest=false --notes "METBR WRF 4 KM ICON checkpoint $GITHUB_RUN_ID"
  gh release upload "$CHECKPOINT_TAG" metbr_run.env --repo "$GITHUB_REPOSITORY"
else
  gh release download "$CHECKPOINT_TAG" --repo "$GITHUB_REPOSITORY" --pattern 'metbr_run.env' --dir . --clobber
  source metbr_run.env
  # The previous segment's restart and boundary remain the authoritative model state.
  rm -f "$INPUT"/*
  gh release download "$CHECKPOINT_TAG" --repo "$GITHUB_REPOSITORY" --pattern "metbr-restart-$(printf '%03d' "$START_HOUR")-*" --dir "$INPUT" --clobber
  gh release download "$CHECKPOINT_TAG" --repo "$GITHUB_REPOSITORY" --pattern 'metbr-boundary-040-*' --dir "$INPUT" --clobber || true
  compgen -G "$INPUT/metbr-restart-$(printf '%03d' "$START_HOUR")-*" > /dev/null || {
    echo "ERROR: restart ICON METBR F${START_HOUR} ausente." >&2
    exit 22
  }
fi

# METBR uses the existing, verified ICON WRF source pipeline. It is never
# allowed to silently select GFS or ECMWF.
chmod +x wrf/run_icon_wrf.sh
export FORCE_RUN_DATE="${RUN_DATE:-}"
export FORCE_RUN_CYCLE="${RUN_CYCLE:-}"
export SOURCE_MODEL=icon
export WRF_RUN_HOURS=$((END_HOUR-START_HOUR))
export WRF_START_HOUR="$START_HOUR"
export WRF_END_HOUR="$END_HOUR"
export WRF_E_WE=300
export WRF_E_SN=360
export WRF_DX_METERS=4000
export WRF_DY_METERS=4000
export WRF_TIME_STEP=24
export WRF_HISTORY_INTERVAL_MINUTES=60

# Explicit contract: no atmospheric GFS/ECMWF input may be selected.
if [[ "${SOURCE_MODEL}" != "icon" ]]; then
  echo "ERROR: METBR source is not ICON." >&2
  exit 23
fi

bash wrf/run_icon_wrf.sh

# Preserve outputs for the checkpoint chain.
for f in wrf_work/run/wrfrst_d01_*; do
  cp -f "$f" "metbr-restart-$(printf '%03d' "$END_HOUR")-$(basename "$f")"
done
for f in wrf_work/run/wrfout_d01_*; do
  cp -f "$f" "metbr-wrfout-$(printf '%02d' "$SEGMENT_INDEX")-$(basename "$f")"
done

# Boundary is produced by the cold-start ICON initialization and reused by
# later stages. No new model initialization is allowed after F000.
if [[ "$COLD_START" == "1" ]]; then
  for f in wrf_work/run/wrfbdy_d01*; do
    cp -f "$f" "metbr-boundary-040-$(basename "$f")"
    gh release upload "$CHECKPOINT_TAG" "metbr-boundary-040-$(basename "$f")" --repo "$GITHUB_REPOSITORY"
  done
fi

gh release upload "$CHECKPOINT_TAG" "metbr-restart-$(printf '%03d' "$END_HOUR")-*" "metbr-wrfout-$(printf '%02d' "$SEGMENT_INDEX")-*" --repo "$GITHUB_REPOSITORY"
