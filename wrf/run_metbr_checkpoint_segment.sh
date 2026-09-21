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

if (( START_HOUR < 0 || END_HOUR <= START_HOUR || START_HOUR % 3 != 0 || END_HOUR % 3 != 0 || END_HOUR > 42 )); then
  echo "Start/end precisam ser multiplos de 3 h entre F000 e F042" >&2
  exit 1
fi

if ! command -v grib_set >/dev/null 2>&1; then
  sudo apt-get update -qq
  sudo apt-get install -y -qq libeccodes-tools
fi
for tool in grib_set grib_copy grib_count; do
  command -v "$tool" >/dev/null || { echo "ERRO: ecCodes/$tool indisponivel" >&2; exit 10; }
done

grib_set_check="$(grib_set -V 2>&1 | head -1)"
echo "ecCodes OK: $grib_set_check"

export WRF_TARGET_RESOLUTION_KM=4 WRF_DX_METERS=4000 WRF_DY_METERS=4000
export WRF_E_WE=300 WRF_E_SN=360 WRF_HISTORY_INTERVAL_MINUTES=60
export WRF_RUN_HOURS=$((END_HOUR-START_HOUR)) WRF_START_HOUR="$START_HOUR" WRF_END_HOUR="$END_HOUR"
export WRF_DATA_SOURCE=ICON WRF_INPUT_MODEL=ICON WRF_INITIALIZATION_MODEL=ICON
export WRF_REFLECTIVITY_SOURCE=REFL_10CM_NATIVE WRF_NATIVE_GRID=true WRF_NO_FALLBACK=true
export WRF_MPI_PROCS="${WRF_MPI_PROCS:-8}"

[[ "$WRF_DATA_SOURCE" == ICON && "$WRF_INPUT_MODEL" == ICON && "$WRF_INITIALIZATION_MODEL" == ICON ]] || { echo 'ERROR: METBR ICON-only contract violated' >&2; exit 1; }

if [[ "$COLD_START" == "0" ]]; then
  rm -rf "$INPUT"/*
  gh release download "$CHECKPOINT_TAG" --repo "$GITHUB_REPOSITORY" --pattern 'metbr-run.env' --dir . --clobber
  source metbr-run.env
  gh release download "$CHECKPOINT_TAG" --repo "$GITHUB_REPOSITORY" --pattern "metbr-restart-${START_HOUR}-*" --dir "$INPUT" --clobber
  gh release download "$CHECKPOINT_TAG" --repo "$GITHUB_REPOSITORY" --pattern 'metbr-boundary-040-*' --dir "$INPUT" --clobber
  gh release download "$CHECKPOINT_TAG" --repo "$GITHUB_REPOSITORY" --pattern 'metbr-namelist.input' --dir "$INPUT" --clobber
  mkdir -p "$INPUT/normalized"; found=0
  for f in "$INPUT"/metbr-restart-${START_HOUR}-*; do [[ -f "$f" ]] || continue; cp -f "$f" "$INPUT/normalized/$(basename "$f" | sed "s/^metbr-restart-${START_HOUR}-//")"; found=1; done
  (( found == 1 )) || { echo "Restart F${START_HOUR} ausente" >&2; exit 22; }
  boundary_found=0
  for f in "$INPUT"/metbr-boundary-040-*; do [[ -f "$f" ]] || continue; cp -f "$f" "$INPUT/normalized/wrfbdy_d01"; boundary_found=1; break; done
  (( boundary_found == 1 )) || { echo "wrfbdy_d01 ausente no checkpoint" >&2; exit 23; }
  cp -f "$INPUT/metbr-namelist.input" "$INPUT/normalized/namelist.input"
  export WRF_RESTART_DIR="$INPUT/normalized"
else
  python3 - <<'PY'
import json, os, urllib.request
repo=os.environ['GITHUB_REPOSITORY']; url=f"https://raw.githubusercontent.com/{repo}/icon-data/metadata.json?run={os.environ.get('GITHUB_RUN_ID','0')}"
req=urllib.request.Request(url,headers={'Cache-Control':'no-cache','User-Agent':'Sideral-METBR'})
with urllib.request.urlopen(req,timeout=30) as r: m=json.load(r)
if str(m.get('model','')).lower()!='icon': raise SystemExit('metadata nao e ICON')
run_date=str(m['runDate']).replace('-',''); run_cycle=''.join(c for c in str(m['runCycle']) if c.isdigit()).zfill(2)[:2]
if run_cycle not in {'00','06','12','18'}: raise SystemExit('ciclo ICON invalido')
with open('metbr-run.env','w') as f: f.write(f'RUN_DATE={run_date}\nRUN_CYCLE={run_cycle}\n')
PY
  source metbr-run.env
  gh release create "$CHECKPOINT_TAG" --target wrf-runner --prerelease --latest=false --notes "METBR WRF 4 KM ICON checkpoint $GITHUB_RUN_ID" || true
  gh release upload "$CHECKPOINT_TAG" metbr-run.env --repo "$GITHUB_REPOSITORY" --clobber
fi

export FORCE_RUN_DATE="${RUN_DATE:-}" FORCE_RUN_CYCLE="${RUN_CYCLE:-}"
chmod +x wrf/run_metbr_icon_wrf.sh
bash wrf/run_metbr_icon_wrf.sh

if [[ "$COLD_START" == "1" ]]; then
  for f in wrf_work/run/wrfrst_d01_*; do cp -f "$f" "metbr-restart-${END_HOUR}-$(basename "$f")"; done
  for f in wrf_work/run/wrfout_d01_*; do cp -f "$f" "metbr-wrfout-${SEGMENT_INDEX}-$(basename "$f")"; done
  for f in wrf_work/run/wrfbdy_d01*; do cp -f "$f" "metbr-boundary-040-$(basename "$f")"; done
  cp -f wrf_work/run/namelist.input metbr-namelist.input
  gh release upload "$CHECKPOINT_TAG" metbr-run.env metbr-namelist.input metbr-boundary-040-* metbr-restart-${END_HOUR}-* metbr-wrfout-${SEGMENT_INDEX}-* --repo "$GITHUB_REPOSITORY" --clobber
else
  for f in wrf_restart_work/run/wrfout_d01_*; do cp -f "$f" "metbr-wrfout-${SEGMENT_INDEX}-$(basename "$f")"; done
  for f in wrf_restart_work/run/wrfrst_d01_*; do cp -f "$f" "metbr-restart-${END_HOUR}-$(basename "$f")"; done
  gh release upload "$CHECKPOINT_TAG" metbr-restart-${END_HOUR}-* metbr-wrfout-${SEGMENT_INDEX}-* --repo "$GITHUB_REPOSITORY" --clobber
fi
