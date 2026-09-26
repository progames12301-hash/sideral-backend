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
case "$COLD_START" in
  0|1) ;;
  *) echo "COLD_START invalido: use 1 no F000 e 0 nos segmentos de continuacao" >&2; exit 1;;
esac

if ! command -v grib_set >/dev/null 2>&1; then
  sudo apt-get update -qq
  sudo apt-get install -y -qq libeccodes-tools
fi
for tool in grib_set grib_copy grib_count; do
  command -v "$tool" >/dev/null || { echo "ERRO: ecCodes/$tool indisponivel" >&2; exit 10; }
done

echo "ecCodes OK: $(grib_set -V 2>&1 | head -1)"
chmod +x wrf/run_metbr_checkpoint_segment.sh wrf/run_metbr_icon_wrf.sh wrf/run_wrf_restart_segment.sh

export WRF_TARGET_RESOLUTION_KM=4 WRF_DX_METERS=4000 WRF_DY_METERS=4000
export WRF_E_WE=300 WRF_E_SN=360 WRF_HISTORY_INTERVAL_MINUTES=60
export WRF_RUN_HOURS=$((END_HOUR-START_HOUR)) WRF_START_HOUR="$START_HOUR" WRF_END_HOUR="$END_HOUR"
export WRF_DATA_SOURCE=ICON WRF_INPUT_MODEL=ICON WRF_INITIALIZATION_MODEL=ICON
export WRF_REFLECTIVITY_SOURCE=REFL_10CM_NATIVE WRF_NATIVE_GRID=true WRF_NO_FALLBACK=true
export WRF_MPI_PROCS="${WRF_MPI_PROCS:-8}"
[[ "$WRF_DATA_SOURCE" == ICON && "$WRF_INPUT_MODEL" == ICON && "$WRF_INITIALIZATION_MODEL" == ICON ]] || { echo 'ERROR: METBR ICON-only contract violated' >&2; exit 1; }

download_checkpoint() {
  local asset="$1" dest="$2" attempt
  mkdir -p "$dest"
  for attempt in 1 2 3 4 5 6 7 8; do
    if gh release view "$CHECKPOINT_TAG" --repo "$GITHUB_REPOSITORY" --json assets --jq '.assets[].name' 2>/dev/null | grep -Fxq "$asset"; then
      if gh release download "$CHECKPOINT_TAG" --repo "$GITHUB_REPOSITORY" --pattern "$asset" --dir "$dest" --clobber; then
        test -s "$dest/$asset" && return 0
      fi
    fi
    echo "Checkpoint $asset ainda nao disponivel (tentativa $attempt/8); aguardando 10s..." >&2
    sleep 10
  done
  echo "ERRO: asset de checkpoint nao encontrado apos 8 tentativas: $asset" >&2
  echo "Assets disponiveis no release $CHECKPOINT_TAG:" >&2
  gh release view "$CHECKPOINT_TAG" --repo "$GITHUB_REPOSITORY" --json assets --jq '.assets[].name' >&2 || true
  return 20
}

if [[ "$COLD_START" == "0" ]]; then
  rm -rf "$INPUT"/*
  ARCHIVE="$INPUT/metbr-checkpoint-${START_HOUR}.tar.gz"
  echo "Baixando checkpoint atomico F${START_HOUR}: $ARCHIVE"
  download_checkpoint "metbr-checkpoint-${START_HOUR}.tar.gz" "$INPUT"
  test -s "$ARCHIVE" || { echo "Checkpoint atomico F${START_HOUR} ausente" >&2; exit 20; }
  tar -xzf "$ARCHIVE" -C "$INPUT"
  test -s "$INPUT/metbr-run.env" || { echo "metbr-run.env ausente no checkpoint F${START_HOUR}" >&2; exit 21; }
  test -s "$INPUT/wrfbdy_d01" || { echo "wrfbdy_d01 ausente no checkpoint F${START_HOUR}" >&2; exit 23; }
  test -s "$INPUT/namelist.input" || { echo "namelist.input ausente no checkpoint F${START_HOUR}" >&2; exit 24; }
  source "$INPUT/metbr-run.env"
  mkdir -p "$INPUT/normalized"
  mapfile -t RSTS < <(find "$INPUT" -maxdepth 1 -type f -name 'wrfrst_d01_*' -size +0c -print | sort)
  ((${#RSTS[@]} > 0)) || { echo "Nenhum wrfrst_d01_* no checkpoint F${START_HOUR}" >&2; exit 22; }
  for f in "${RSTS[@]}"; do cp -f "$f" "$INPUT/normalized/"; done
  cp -f "$INPUT/wrfbdy_d01" "$INPUT/normalized/wrfbdy_d01"
  cp -f "$INPUT/namelist.input" "$INPUT/normalized/namelist.input"
  export WRF_RESTART_DIR="$INPUT/normalized"
else
  python3 - <<'PY'
import json, os, urllib.request
repo=os.environ['GITHUB_REPOSITORY']
url=f"https://raw.githubusercontent.com/{repo}/icon-data/metadata.json?run={os.environ.get('GITHUB_RUN_ID','0')}"
req=urllib.request.Request(url,headers={'Cache-Control':'no-cache','User-Agent':'Sideral-METBR'})
with urllib.request.urlopen(req,timeout=30) as r: m=json.load(r)
if str(m.get('model','')).lower()!='icon': raise SystemExit('metadata nao e ICON')
run_date=str(m['runDate']).replace('-','')
run_cycle='00'
with open('metbr-run.env','w') as f: f.write(f'RUN_DATE={run_date}\nRUN_CYCLE={run_cycle}\n')
PY
  source metbr-run.env
  gh release create "$CHECKPOINT_TAG" --target wrf-runner --prerelease --latest=false --notes "METBR WRF 4 KM ICON checkpoint $GITHUB_RUN_ID" || true
fi

export RUN_DATE RUN_CYCLE
[[ "$RUN_DATE" =~ ^[0-9]{8}$ ]] || { echo "RUN_DATE invalido antes do WRF: ${RUN_DATE:-}" >&2; exit 25; }
[[ "$RUN_CYCLE" =~ ^(00|06|12|18)$ ]] || { echo "RUN_CYCLE invalido antes do WRF: ${RUN_CYCLE:-}" >&2; exit 26; }
export FORCE_RUN_DATE="$RUN_DATE" FORCE_RUN_CYCLE="$RUN_CYCLE"
echo "METBR ICON initialization: ${RUN_DATE} ${RUN_CYCLE}Z"
chmod +x wrf/run_metbr_icon_wrf.sh wrf/run_wrf_restart_segment.sh
bash wrf/run_metbr_icon_wrf.sh

publish_outputs() {
  local output_dir="$1"
  local -a files=()
  shopt -s nullglob
  files=("$output_dir"/wrfout_d01_*)
  shopt -u nullglob
  ((${#files[@]} > 0)) || { echo "Nenhum wrfout para publicar no segmento F${START_HOUR}-F${END_HOUR}" >&2; exit 45; }
  echo "Publicando ${#files[@]} wrfout do segmento F${START_HOUR}-F${END_HOUR}..."
  for f in "${files[@]}"; do
    test -s "$f" || { echo "wrfout vazio: $f" >&2; exit 46; }
    gh release upload "$CHECKPOINT_TAG" "$f" --repo "$GITHUB_REPOSITORY" --clobber
  done
  printf '%s\n' "METBR WRF 4 KM ICON" "run_id=$GITHUB_RUN_ID" "run_date=$RUN_DATE" "run_cycle=${RUN_CYCLE}Z" "segment=F${START_HOUR}-F${END_HOUR}" "published_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "metbr-publication-F${END_HOUR}.txt"
  gh release upload "$CHECKPOINT_TAG" "metbr-publication-F${END_HOUR}.txt" --repo "$GITHUB_REPOSITORY" --clobber
  if (( END_HOUR == 42 )); then
    printf '%s\n' "METBR WRF 4 KM ICON - RODADA COMPLETA" "run_id=$GITHUB_RUN_ID" "run_date=$RUN_DATE" "run_cycle=${RUN_CYCLE}Z" "final_forecast=F042" "status=complete" "completed_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)" > metbr-latest-complete.txt
    gh release upload "$CHECKPOINT_TAG" metbr-latest-complete.txt --repo "$GITHUB_REPOSITORY" --clobber
    echo "RODADA COMPLETA PUBLICADA: F042"
  fi
}

if [[ "$COLD_START" == "1" ]]; then
  shopt -s nullglob
  rst_files=(wrf_work/run/wrfrst_d01_*)
  out_files=(wrf_work/run/wrfout_d01_*)
  bdy_files=(wrf_work/run/wrfbdy_d01*)
  shopt -u nullglob
  ((${#out_files[@]} > 0)) || { echo "Nenhum wrfout produzido no segmento F${START_HOUR}-F${END_HOUR}" >&2; exit 30; }
  ((${#rst_files[@]} > 0)) || { echo "Nenhum wrfrst produzido no segmento F${START_HOUR}-F${END_HOUR}" >&2; exit 31; }
  ((${#bdy_files[@]} > 0)) || { echo "Nenhum wrfbdy produzido no segmento F${START_HOUR}-F${END_HOUR}" >&2; exit 32; }
  publish_outputs "wrf_work/run"
  rm -rf checkpoint_pack && mkdir checkpoint_pack
  cp -f "${rst_files[@]}" checkpoint_pack/
  cp -f "${bdy_files[0]}" checkpoint_pack/wrfbdy_d01
  cp -f wrf_work/run/namelist.input checkpoint_pack/namelist.input
  cp -f metbr-run.env checkpoint_pack/metbr-run.env
  tar -czf "metbr-checkpoint-${END_HOUR}.tar.gz" -C checkpoint_pack .
  test -s "metbr-checkpoint-${END_HOUR}.tar.gz"
  gh release upload "$CHECKPOINT_TAG" "metbr-checkpoint-${END_HOUR}.tar.gz" --repo "$GITHUB_REPOSITORY" --clobber
  echo "CHECKPOINT PUBLICADO: metbr-checkpoint-${END_HOUR}.tar.gz"
else
  OUTPUT_DIR="$ROOT/metbr_segment_output"
  test -d "$OUTPUT_DIR" || { echo "METBR output directory ausente: $OUTPUT_DIR" >&2; exit 40; }
  shopt -s nullglob
  outs=("$OUTPUT_DIR"/wrfout_d01_*)
  rsts=("$OUTPUT_DIR"/wrfrst_d01_*)
  bdy=("$OUTPUT_DIR"/wrfbdy_d01)
  shopt -u nullglob
  ((${#outs[@]} > 0)) || { echo "Nenhum wrfout produzido no segmento F${START_HOUR}-F${END_HOUR}" >&2; exit 41; }
  ((${#rsts[@]} > 0)) || { echo "Nenhum wrfrst produzido no segmento F${START_HOUR}-F${END_HOUR}" >&2; exit 42; }
  ((${#bdy[@]} == 1)) || { echo "wrfbdy_d01 ausente no segmento F${START_HOUR}-F${END_HOUR}" >&2; exit 43; }
  test -s "$OUTPUT_DIR/namelist.restart.input" || { echo "namelist.restart.input ausente" >&2; exit 44; }
  publish_outputs "$OUTPUT_DIR"
  rm -rf checkpoint_pack && mkdir checkpoint_pack
  cp -f "${rsts[@]}" checkpoint_pack/
  cp -f "$OUTPUT_DIR/wrfbdy_d01" checkpoint_pack/wrfbdy_d01
  cp -f "$OUTPUT_DIR/namelist.restart.input" checkpoint_pack/namelist.input
  cp -f "$INPUT/metbr-run.env" checkpoint_pack/metbr-run.env
  tar -czf "metbr-checkpoint-${END_HOUR}.tar.gz" -C checkpoint_pack .
  test -s "metbr-checkpoint-${END_HOUR}.tar.gz"
  gh release upload "$CHECKPOINT_TAG" "metbr-checkpoint-${END_HOUR}.tar.gz" --repo "$GITHUB_REPOSITORY" --clobber
  echo "CHECKPOINT PUBLICADO: metbr-checkpoint-${END_HOUR}.tar.gz"
fi