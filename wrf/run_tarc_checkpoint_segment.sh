#!/usr/bin/env bash
set -euo pipefail

START_HOUR="$1"
END_HOUR="$2"
SEGMENT_INDEX="$3"
COLD_START="$4"
SEGMENT_STARTED_AT="$(date +%s)"

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

chmod +x wrf/run_tarc_checkpoint_segment.sh wrf/run_tarc_icon_wrf.sh wrf/run_tarc_ecmwf_wrf.sh wrf/run_tarc_gfs_wrf.sh wrf/run_tarc_wrf_with_source.sh wrf/run_tarc_restart_segment.sh

export WRF_TARGET_RESOLUTION_KM="${WRF_TARGET_RESOLUTION_KM:-4}"
export WRF_DX_METERS="${WRF_DX_METERS:-4000}" WRF_DY_METERS="${WRF_DY_METERS:-4000}"
export WRF_E_WE="${WRF_E_WE:-337}" WRF_E_SN="${WRF_E_SN:-501}"
export WRF_HISTORY_INTERVAL_MINUTES="${WRF_HISTORY_INTERVAL_MINUTES:-60}"
export WRF_TIME_STEP="${WRF_TIME_STEP:-20}"
export WRF_MPI_PROCS="${WRF_MPI_PROCS:-4}"
export WRF_BOUNDARY_END_HOUR="$END_HOUR"
export WRF_REF_LAT="${WRF_REF_LAT:--23.0}" WRF_REF_LON="${WRF_REF_LON:--45.5}" WRF_STAND_LON="${WRF_STAND_LON:--45.5}"
export WRF_RUN_HOURS=$((END_HOUR-START_HOUR))
export WRF_START_HOUR="$START_HOUR" WRF_END_HOUR="$END_HOUR"


prepare_source_with_fallback() {
  local boundary_only="${1:?Informe 0 para cold start, 1 para LBC de restart}"
  local first="${SOURCE_MODEL:-icon}"
  local model rc selected=""
  local -a candidates=()

  case "$first" in
    icon)
      if [[ "${WRF2_ICON_ONLY:-0}" == "1" ]]; then candidates=(icon); else candidates=(icon ecmwf gfs); fi
      ;;
    ecmwf) candidates=(ecmwf gfs) ;;
    gfs) candidates=(gfs) ;;
    *) echo "SOURCE_MODEL invalido no checkpoint: $first" >&2; return 2 ;;
  esac

  export WRF_BOUNDARY_ONLY="$boundary_only"
  for model in "${candidates[@]}"; do
    echo
    echo "===== SIDERAL ICON SOURCE: tentando ${model^^}; boundary_only=$boundary_only ====="
    case "$model" in
      icon)
        if bash wrf/run_tarc_icon_wrf.sh; then rc=0; else rc=$?; fi
        ;;
      ecmwf)
        if bash wrf/run_tarc_ecmwf_wrf.sh; then rc=0; else rc=$?; fi
        ;;
      gfs)
        if bash wrf/run_tarc_gfs_wrf.sh; then rc=0; else rc=$?; fi
        ;;
    esac
    if (( rc == 0 )); then
      selected="$model"
      if [[ "$model" == "icon" && -s "$ROOT/tarc-icon-run.env" ]]; then
        source "$ROOT/tarc-icon-run.env"
        export RUN_DATE RUN_CYCLE
        FORCE_RUN_DATE="$RUN_DATE"
        FORCE_RUN_CYCLE="$RUN_CYCLE"
        export FORCE_RUN_DATE FORCE_RUN_CYCLE
        echo "TARC: ciclo ICON validado e fixado: $RUN_DATE $RUN_CYCLE Z"
      fi
      echo "SIDERAL ICON SOURCE: fonte escolhida ${selected^^}"
      break
    fi
    echo "SIDERAL ICON SOURCE: ${model^^} falhou (exit=$rc); seguindo para a proxima fonte"
  done

  [[ -n "$selected" ]] || {
    echo "ERRO: TARC falhou com todas as fontes permitidas a partir de $first (ICON -> ECMWF -> GFS)." >&2
    return 1
  }

  SOURCE_MODEL="$selected"
  printf 'RUN_DATE=%s\nRUN_CYCLE=%s\nSOURCE_MODEL=%s\n' "$RUN_DATE" "$RUN_CYCLE" "$SOURCE_MODEL" > tarc-run.env
  gh release upload "$CHECKPOINT_TAG" tarc-run.env --repo "$GITHUB_REPOSITORY" --clobber
}

if [[ "$COLD_START" == "0" ]]; then
  rm -rf "$INPUT"/*
  gh release download "$CHECKPOINT_TAG" --repo "$GITHUB_REPOSITORY" --pattern 'tarc-run.env' --dir . --clobber
  source tarc-run.env
  gh release download "$CHECKPOINT_TAG" --repo "$GITHUB_REPOSITORY" --pattern "tarc-restart-$START_HOUR-*" --dir "$INPUT" --clobber
  gh release download "$CHECKPOINT_TAG" --repo "$GITHUB_REPOSITORY" --pattern 'tarc-namelist.input' --dir "$INPUT" --clobber
  mkdir -p "$INPUT/normalized"
  for f in "$INPUT"/tarc-restart-"$START_HOUR"-*; do
    [[ -f "$f" ]] || continue
    cp -f "$f" "$INPUT/normalized/$(basename "$f" | sed -E "s/^tarc-restart-$START_HOUR-//; s/_([0-9]{2})[.]([0-9]{2})[.]([0-9]{2})$/_\1:\2:\3/")"
  done
  cp -f "$INPUT/tarc-namelist.input" "$INPUT/normalized/namelist.input"

  # The previous checkpoint boundary ends at START_HOUR, so build a fresh
  # ICON LBC covering START_HOUR..END_HOUR before restarting WRF.
  export RUN_DATE RUN_CYCLE
  export FORCE_RUN_DATE="$RUN_DATE" FORCE_RUN_CYCLE="$RUN_CYCLE"
  export WRF_TARGET_RESOLUTION_KM="${WRF_TARGET_RESOLUTION_KM:-4}" WRF_DX_METERS="${WRF_DX_METERS:-4000}" WRF_DY_METERS="${WRF_DY_METERS:-4000}"
  export WRF_E_WE="${WRF_E_WE:-337}" WRF_E_SN="${WRF_E_SN:-501}"
  export WRF_HISTORY_INTERVAL_MINUTES="${WRF_HISTORY_INTERVAL_MINUTES:-60}" WRF_TIME_STEP="${WRF_TIME_STEP:-20}" WRF_MPI_PROCS="${WRF_MPI_PROCS:-4}"
  export WRF_START_HOUR="$START_HOUR" WRF_END_HOUR="$END_HOUR"
  export WRF_BOUNDARY_END_HOUR="$END_HOUR" WRF_BOUNDARY_ONLY=1
  echo "SIDERAL: preparando LBC com fallback ICON -> ECMWF -> GFS F$START_HOUR-F$END_HOUR"
  BOUNDARY_PREP_STARTED_AT="$(date +%s)"
  prepare_source_with_fallback 1
  echo "SIDERAL_METRIC boundary_prepare_seconds=$(( $(date +%s) - BOUNDARY_PREP_STARTED_AT ))"
  test -s "$ROOT/wrf_work/run/wrfbdy_d01" || { echo "LBC TARC ausente para F$START_HOUR-F$END_HOUR" >&2; exit 27; }
  cp -f "$ROOT/wrf_work/run/wrfbdy_d01" "$INPUT/normalized/wrfbdy_d01"
  test -s "$INPUT/normalized/wrfbdy_d01"
  test -s "$INPUT/normalized/namelist.input"
  export WRF_RESTART_DIR="$INPUT/normalized"
  RESTART_STARTED_AT="$(date +%s)"
  bash wrf/run_tarc_restart_segment.sh "$START_HOUR" "$END_HOUR" "$WRF_RESTART_DIR"
  echo "SIDERAL_METRIC restart_forecast_seconds=$(( $(date +%s) - RESTART_STARTED_AT ))"

  OUTPUT_DIR="$ROOT/tarc_segment_output"
  test -d "$OUTPUT_DIR"
  UPLOAD_DIR="$ROOT/tarc_upload_$SEGMENT_INDEX"
  rm -rf "$UPLOAD_DIR"; mkdir -p "$UPLOAD_DIR"
  for f in "$OUTPUT_DIR"/wrfrst_d01_*; do cp -f "$f" "$UPLOAD_DIR/tarc-restart-$END_HOUR-$(basename "$f")"; done
  for f in "$OUTPUT_DIR"/wrfout_d01_*; do cp -f "$f" "$UPLOAD_DIR/tarc-wrfout-$SEGMENT_INDEX-$(basename "$f")"; done
  gh release upload "$CHECKPOINT_TAG" "$UPLOAD_DIR"/* --repo "$GITHUB_REPOSITORY" --clobber
else
  python3 - <<'PY'
import datetime as dt, os
now=dt.datetime.now(dt.timezone.utc)
scheduled_cycle=os.environ.get("WRF_SCHEDULED_CYCLE","")
if scheduled_cycle in {"00","12"}:
    run=now.replace(hour=int(scheduled_cycle), minute=0, second=0, microsecond=0)
else:
    run=now.replace(hour=(now.hour//6)*6, minute=0, second=0, microsecond=0)
if os.environ.get("WRF2_TOMORROW") == "1":
    run += dt.timedelta(hours=24)
open("tarc-run.env","w").write(f"RUN_DATE={run:%Y%m%d}\nRUN_CYCLE={run:%H}\nSOURCE_MODEL=icon\nWRF2_TOMORROW={os.environ.get('WRF2_TOMORROW','0')}\n")
PY
  source tarc-run.env
  gh release create "$CHECKPOINT_TAG" --target sideral-wrf-4km-sudeste --prerelease --latest=false --repo "$GITHUB_REPOSITORY" --notes "WRF SIDERAL 4 KM SUDESTE ICON checkpoint $GITHUB_RUN_ID" || true
  gh release upload "$CHECKPOINT_TAG" tarc-run.env --repo "$GITHUB_REPOSITORY" --clobber

  export FORCE_RUN_DATE="$RUN_DATE" FORCE_RUN_CYCLE="$RUN_CYCLE"
  unset TARC_ALLOW_RESELECT_ICON
  export WRF_BOUNDARY_ONLY=0
  COLD_FORECAST_STARTED_AT="$(date +%s)"
  prepare_source_with_fallback 0
  echo "SIDERAL_METRIC cold_start_forecast_seconds=$(( $(date +%s) - COLD_FORECAST_STARTED_AT ))"
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

echo "SIDERAL_METRIC segment=F$START_HOUR-F$END_HOUR total_seconds=$(( $(date +%s) - SEGMENT_STARTED_AT ))"
echo "SIDERAL checkpoint F$START_HOUR-F$END_HOUR publicado em $CHECKPOINT_TAG."
# Notify the main-branch incremental publisher after compact NPZs have reached the checkpoint Release.
if [[ "${WRF2_TOMORROW:-0}" == "1" ]]; then
  PUBLISH_EVENT=sideral-wrf-4km-sudeste-2-checkpoint-published
else
  PUBLISH_EVENT=sideral-wrf-4km-sudeste-checkpoint-published
fi
if ! gh api --method POST "repos/$GITHUB_REPOSITORY/dispatches" \
  -f "event_type=$PUBLISH_EVENT" \
  -f "client_payload[run_id]=$GITHUB_RUN_ID" >/dev/null; then
  echo "AVISO: dispatch da publicação incremental TARC falhou; o publicador de recuperação tentará novamente."
fi