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
rm -f "$INPUT"/*

if [[ "$COLD_START" == "1" ]]; then
  now=$(date -u +%s)
  selected=''
  for offset in $(seq 0 6 96); do
    ts=$((now-offset*3600))
    date=$(date -u -d "@$ts" +%Y%m%d)
    cycle=$(date -u -d "@$ts" +%H)
    case "$cycle" in 00|06|12|18) ;; *) continue ;; esac
    url="https://noaa-gfs-bdp-pds.s3.amazonaws.com/gfs.${date}/${cycle}/atmos/gfs.t${cycle}z.pgrb2.0p25.f042"
    if curl -fsSL --range 0-0 --connect-timeout 15 --max-time 45 -o /dev/null "$url"; then
      selected="$date $cycle"
      break
    fi
  done
  test -n "$selected"
  read -r RUN_DATE RUN_CYCLE <<< "$selected"
  printf 'RUN_DATE=%s\nRUN_CYCLE=%s\n' "$RUN_DATE" "$RUN_CYCLE" > metbr_run.env
  gh release create "$CHECKPOINT_TAG" --target wrf-runner --prerelease --latest=false --notes "METBR WRF 4 KM checkpoint $GITHUB_RUN_ID"
  gh release upload "$CHECKPOINT_TAG" metbr_run.env --repo "$GITHUB_REPOSITORY"
  export FORCE_RUN_DATE="$RUN_DATE" FORCE_RUN_CYCLE="$RUN_CYCLE"
  export WRF_START_HOUR="$START_HOUR" WRF_END_HOUR="$END_HOUR" METBR_COLD_START=1 METBR_BOUNDARY_END_HOUR=40
else
  gh release download "$CHECKPOINT_TAG" --repo "$GITHUB_REPOSITORY" --pattern 'metbr_run.env' --dir . --clobber
  gh release download "$CHECKPOINT_TAG" --repo "$GITHUB_REPOSITORY" --pattern "metbr-restart-$(printf '%03d' "$START_HOUR")-*" --dir "$INPUT" --clobber
  gh release download "$CHECKPOINT_TAG" --repo "$GITHUB_REPOSITORY" --pattern 'metbr-boundary-004' --dir "$INPUT" --clobber
  for f in "$INPUT"/metbr-restart-$(printf '%03d' "$START_HOUR")-*; do
    cp -f "$f" "$INPUT/$(basename "$f" | sed -E 's/^metbr-restart-[0-9]+-//')"
  done
  cp -f "$INPUT/metbr-boundary-004" "$INPUT/wrfbdy_d01"
  source metbr_run.env
  export FORCE_RUN_DATE="$RUN_DATE" FORCE_RUN_CYCLE="$RUN_CYCLE"
  export WRF_START_HOUR="$START_HOUR" WRF_END_HOUR="$END_HOUR" METBR_COLD_START=0
fi

chmod +x wrf/run_metbr_gfs_segment.sh
wrf/run_metbr_gfs_segment.sh

for f in wrf_work/run/wrfrst_d01_*; do
  cp -f "$f" "metbr-restart-$(printf '%03d' "$END_HOUR")-$(basename "$f")"
done
for f in wrf_work/run/wrfout_d01_*; do
  cp -f "$f" "metbr-wrfout-$(printf '%02d' "$SEGMENT_INDEX")-$(basename "$f")"
done

if [[ "$COLD_START" == "1" ]]; then
  cp -f wrf_work/run/wrfbdy_d01 metbr-boundary-004
  gh release upload "$CHECKPOINT_TAG" metbr-boundary-004 --repo "$GITHUB_REPOSITORY"
fi

gh release upload "$CHECKPOINT_TAG" "metbr-restart-$(printf '%03d' "$END_HOUR")-*" "metbr-wrfout-$(printf '%02d' "$SEGMENT_INDEX")-*" --repo "$GITHUB_REPOSITORY"
