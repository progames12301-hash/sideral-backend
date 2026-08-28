#!/bin/bash
set -euo pipefail

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WRF_SRC_DIR="$BASE_DIR/wrf_source"
WPS_SRC_DIR="$BASE_DIR/wps_source"
WRF_RUN_DIR="$BASE_DIR/wrf"
WPS_RUN_DIR="$BASE_DIR/wps"
mkdir -p "$BASE_DIR/logs"

export NETCDF="${NETCDF:-/usr}"
export NETCDFF="${NETCDFF:-/usr}"
export NETCDF_classic=1
unset HDF5 || true
JASPER_PREFIX="$BASE_DIR/deps/jasper"
export JASPERLIB="${JASPERLIB:-$JASPER_PREFIX/lib}"
export JASPERINC="${JASPERINC:-$JASPER_PREFIX/include}"
export LD_LIBRARY_PATH="$JASPERLIB:${LD_LIBRARY_PATH:-}"

if [ -x "$WRF_RUN_DIR/wrf.exe" ] && [ -x "$WPS_RUN_DIR/metgrid.exe" ]; then
  echo "WRF/WPS recuperados do cache."
  exit 0
fi

rm -rf "$WRF_SRC_DIR" "$WPS_SRC_DIR" "$WRF_RUN_DIR" "$WPS_RUN_DIR"

if [ ! -s "$JASPERLIB/libjasper.so" ]; then
  JASPER_SOURCE="$BASE_DIR/jasper_source"
  rm -rf "$JASPER_SOURCE"
  git clone --depth 1 --branch version-4.2.4 https://github.com/jasper-software/jasper.git "$JASPER_SOURCE"
  cmake -S "$JASPER_SOURCE" -B "$JASPER_SOURCE/build" \
    -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX="$JASPER_PREFIX" \
    -DJAS_ENABLE_DOC=OFF -DJAS_ENABLE_PROGRAMS=OFF
  cmake --build "$JASPER_SOURCE/build" --parallel "$(nproc)"
  cmake --install "$JASPER_SOURCE/build"
  rm -rf "$JASPER_SOURCE"
fi

git clone --depth 1 --recursive --branch v4.5 https://github.com/wrf-model/WRF.git "$WRF_SRC_DIR"
cd "$WRF_SRC_DIR"
printf '34\n1\n' | ./configure
sed -i 's|-L$(WRF_SRC_ROOT_DIR)/external/io_netcdf -lwrfio_nf -L/usr/lib|-L$(WRF_SRC_ROOT_DIR)/external/io_netcdf -lwrfio_nf -L/usr/lib/x86_64-linux-gnu -lnetcdff -lnetcdf|g' configure.wrf
./compile em_real > "$BASE_DIR/logs/compile_wrf.log" 2>&1
test -x main/wrf.exe && test -x main/real.exe

mkdir -p "$WRF_RUN_DIR"
cp -a run/. "$WRF_RUN_DIR/"
cp -f main/wrf.exe main/real.exe "$WRF_RUN_DIR/"

git clone --depth 1 --branch v4.5 https://github.com/wrf-model/WPS.git "$WPS_SRC_DIR"
cd "$WPS_SRC_DIR"
export WRF_DIR="$WRF_SRC_DIR"
printf '3\n' | ./configure
sed -i 's|-L$(NETCDF)/lib  -lnetcdf|-L/usr/lib/x86_64-linux-gnu -lnetcdff -lnetcdf|g' configure.wps
./compile > "$BASE_DIR/logs/compile_wps.log" 2>&1
test -x geogrid.exe && test -x ungrib.exe && test -x metgrid.exe

mkdir -p "$WPS_RUN_DIR"
cp -a geogrid metgrid ungrib "$WPS_RUN_DIR/"
cp -f geogrid.exe ungrib.exe metgrid.exe link_grib.csh "$WPS_RUN_DIR/"
echo "WRF/WPS compilados com sucesso."
