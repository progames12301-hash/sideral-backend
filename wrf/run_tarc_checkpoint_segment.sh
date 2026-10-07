#!/usr/bin/env bash
set -euo pipefail

START_HOUR="$1"
END_HOUR="$2"
SEGMENT_INDEX="$3"
COLD_START="$4"

: "$GH_TOKEN"
: "$CHECKPOINT_TAG"
: "$GITHUB_REPOSITORY"

ROOT="$GITHUB_WORKSPACE"
INPUT="$ROOT/tarc_restart_input"
mkdir -p "$INPUT"

(( START_HOUR >= 0 && END_HOUR > START_HOUR && START_HOUR % 3 == 0 && END_HOUR % 3 == 0 && END_HOUR <= 48 )) || {
  echo "F000-F048 TARC: intervalo invalido" >&2
  exit 1
}
case "$COLD_START" in 0|1) ;; *) echo "COLD_START deve ser 1 no F000" >&2; exit 1 ;; esac

if ! command -v grib_set >/dev/null 2>&1; then
  sudo apt-get update -qq
  sudo apt-get install -y -qq libeccodes-tools
fi
for tool in grib_set grib_copy grib_count; do
  command -v "$tool" >/dev/null || { echo "ecCodes/$tool indisponivel" >&2; exit 10; }
done

chmod +x wrf/run_tarc_checkpoint_segment.sh wrf/run_tarc_icon_wrf.sh wrf/run_tarc_wrf_with_source.sh wrf/run_tarc_restart_segment.sh

export WRF_TARGET_RESOLUTION_KM=3
export WRF_DX_METERS=3000 WRF_DY_METERS=3000
export WRF_E_WE=361 WRF_E_SN=445
export WRF_HISTORY_INTERVAL_MINUTES=60
export WRF_TIME_STEP=18
export WRF_MPI_PROCS=4
export WRF_BOUNDARY_END_HOUR="${WRF_BOUNDARY_END_HOUR:-48}"
export WRF_REF_LAT=-28.0 WRF_REF_LON=-53.5 WRF_STAND_LON=-53.5
export WRF_RUN_HOURS=$((END_HOUR-START_HOUR))
export WRF_START_HOUR="$START_HOUR" WRF_END_HOUR="$END_HOUR"

if [[ "$COLD_START" == "0" ]]; then
  rm -rf "$INPUT"/*
  gh release download "$CHECKPOINT_TAG" --repo "$GITHUB_REPOSITORY" --pattern 'tarc-run.env' --dir . --clobber
  source tarc-run.env
  gh release download "$CHECKPOINT_TAG" --repo "$GITHUB_REPOSITORY" --pattern "tarc-restart-$START_HOUR-*" --dir "$INPUT" --clobber
  gh release download "$CHECKPOINT_TAG" --repo "$GITHUB_REPOSITORY" --pattern 'tarc-boundary-3km-*' --dir "$INPUT" --clobber
  gh release download "$CHECKPOINT_TAG" --repo "$GITHUB_REPOSITORY" --pattern 'tarc-namelist.input' --dir "$INPUT" --clobber
  mkdir -p "$INPUT/normalized"
  for f in "$INPUT"/tarc-restart-"$START_HOUR"-*; do
    [[ -f "$f" ]] || continue
    cp -f "$f" "$INPUT/normalized/$(basename "$f" | sed -E "s/^tarc-restart-$START_HOUR-//; s/_([0-9]{2})[.]([0-9]{2})[.]([0-9]{2})$/_\1:\2:\3/")"
  done
  cp -f "$INPUT"/tarc-boundary-3km-* "$INPUT/normalized/wrfbdy_d01"
  cp -f "$INPUT/tarc-namelist.input" "$INPUT/normalized/namelist.input"
  test -s "$INPUT/normalized/wrfbdy_d01"
  test -s "$INPUT/normalized/namelist.input"
  export WRF_RESTART_DIR="$INPUT/normalized"
  bash wrf/run_tarc_restart_segment.sh "$START_HOUR" "$END_HOUR" "$WRF_RESTART_DIR"

  OUTPUT_DIR="$ROOT/tarc_segment_output"
  test -d "$OUTPUT_DIR"
  UPLOAD_DIR="$ROOT/tarc_upload_$SEGMENT_INDEX"
  rm -rf "$UPLOAD_DIR"; mkdir -p "$UPLOAD_DIR"
  for f in "$OUTPUT_DIR"/wrfrst_d01_*; do cp -f "$f" "$UPLOAD_DIR/tarc-restart-$END_HOUR-$(basename "$f")"; done
  for f in "$OUTPUT_DIR"/wrfout_d01_*; do cp -f "$f" "$UPLOAD_DIR/tarc-wrfout-$SEGMENT_INDEX-$(basename "$f")"; done
  gh release upload "$CHECKPOINT_TAG" "$UPLOAD_DIR"/* --repo "$GITHUB_REPOSITORY" --clobber
else
  python3 - <<'PY'
import json, os, urllib.request
repo=os.environ["GITHUB_REPOSITORY"]
url=f"https://raw.githubusercontent.com/{repo}/icon-data/metadata.json?run={os.environ.get('GITHUB_RUN_ID','0')}"
req=urllib.request.Request(url, headers={"Cache-Control":"no-cache","User-Agent":"Sideral-TARC"})
with urllib.request.urlopen(req, timeout=30) as r:
    m=json.load(r)
if str(m.get("model","")).lower() != "icon":
    raise SystemExit("metadata atual nao e ICON")
run_date=str(m["runDate"]).replace("-","")
run_cycle="".join(c for c in str(m["runCycle"]) if c.isdigit()).zfill(2)[:2]
if run_cycle not in {"00","06","12","18"}:
    raise SystemExit("ciclo ICON invalido")
open("tarc-run.env","w").write(f"RUN_DATE={run_date}\nRUN_CYCLE={run_cycle}\n")
PY
  source tarc-run.env
  gh release create "$CHECKPOINT_TAG" --target tarc-wrf-3km --prerelease --latest=false     --repo "$GITHUB_REPOSITORY" --notes "WRF TARC 3 KM ICON checkpoint $GITHUB_RUN_ID" || true
  gh release upload "$CHECKPOINT_TAG" tarc-run.env --repo "$GITHUB_REPOSITORY" --clobber

  export FORCE_RUN_DATE="$RUN_DATE" FORCE_RUN_CYCLE="$RUN_CYCLE"
  bash wrf/run_tarc_icon_wrf.sh

  test -s wrf_work/run/wrfinput_d01
  test -s wrf_work/run/wrfbdy_d01
  test -n "$(find wrf_work/run -maxdepth 1 -type f -name 'wrfout_d01_*' -size +0c -print -quit)"
  test -n "$(find wrf_work/run -maxdepth 1 -type f -name 'wrfrst_d01_*' -size +0c -print -quit)"

  for f in wrf_work/run/wrfrst_d01_*; do cp -f "$f" "tarc-restart-$END_HOUR-$(basename "$f")"; done
  for f in wrf_work/run/wrfout_d01_*; do cp -f "$f" "tarc-wrfout-$SEGMENT_INDEX-$(basename "$f")"; done
  for f in wrf_work/run/wrfbdy_d01; do cp -f "$f" "tarc-boundary-3km-$(basename "$f")"; done
  cp -f wrf_work/run/namelist.input tarc-namelist.input

  gh release upload "$CHECKPOINT_TAG"     tarc-run.env tarc-namelist.input tarc-boundary-3km-*     tarc-restart-$END_HOUR-* tarc-wrfout-$SEGMENT_INDEX-*     --repo "$GITHUB_REPOSITORY" --clobber
fi


# Gera produtos 2D compactos para o frontend sem substituir os wrfout nativos.
if [[ "$COLD_START" == "1" ]]; then
  POST_INPUT="$ROOT/wrf_work/run"
else
  POST_INPUT="$ROOT/tarc_segment_output"
fi
if [[ -n "$(find "$POST_INPUT" -maxdepth 1 -type f -name 'wrfout_d01_*' -size +0c -print -quit)" ]]; then
  python3 -m pip install --disable-pip-version-check -q netCDF4 numpy
  PRODUCT_DIR="$ROOT/tarc_products_$SEGMENT_INDEX"
  rm -rf "$PRODUCT_DIR"
  python3 wrf/tarc_postprocess.py "$POST_INPUT"/wrfout_d01_* --output-dir "$PRODUCT_DIR"
  mv "$PRODUCT_DIR/metadata.json" "tarc-products-$SEGMENT_INDEX.json"
  gh release upload "$CHECKPOINT_TAG" "$PRODUCT_DIR"/*.npz "tarc-products-$SEGMENT_INDEX.json" --repo "$GITHUB_REPOSITORY" --clobber
fi

echo "TARC checkpoint F$START_HOUR-F$END_HOUR publicado em $CHECKPOINT_TAG."