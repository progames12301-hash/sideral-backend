#!/bin/bash
set -euo pipefail

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODEL="${MODEL:-GFS}"
MODEL_LOWER="$(printf '%s' "$MODEL" | tr '[:upper:]' '[:lower:]')"
WRF_DIR="$BASE_DIR/wrf"
WPS_DIR="$BASE_DIR/wps"
OUTPUT_DIR="$BASE_DIR/output/$MODEL_LOWER"
LOG_DIR="$BASE_DIR/logs"
mkdir -p "$OUTPUT_DIR" "$LOG_DIR"

cd "$WRF_DIR"
rm -f met_em.d0* wrfout_d01_* wrfrst_d01_*
cp -f "$WPS_DIR"/met_em.d0* .
mpirun --allow-run-as-root -np "${MPI_PROCS:-4}" ./real.exe > "$LOG_DIR/real.log" 2>&1
mpirun --allow-run-as-root -np "${MPI_PROCS:-4}" ./wrf.exe > "$LOG_DIR/wrf.log" 2>&1

new_output="$OUTPUT_DIR/.new"
rm -rf "$new_output"
mkdir -p "$new_output"
cp -f wrfout_d01_* "$new_output/"
rm -f "$OUTPUT_DIR"/wrfout_d01_*
mv "$new_output"/wrfout_d01_* "$OUTPUT_DIR/"
rmdir "$new_output"
echo "WRF concluido: $(find "$OUTPUT_DIR" -maxdepth 1 -name 'wrfout_d01_*' | wc -l) quadros."
