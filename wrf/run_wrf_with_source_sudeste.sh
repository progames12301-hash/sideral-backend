#!/usr/bin/env bash
set -euo pipefail

: "${SOURCE_MODEL:?SOURCE_MODEL ausente}"
: "${RUN_DATE:?RUN_DATE ausente}"
: "${RUN_CYCLE:?RUN_CYCLE ausente}"
: "${WRF_RUN_HOURS:?WRF_RUN_HOURS ausente}"
: "${SOURCE_DIR:?SOURCE_DIR ausente}"
: "${SOURCE_VTABLE:?SOURCE_VTABLE ausente}"

# Reduz NetCDF/quadros publicados sem alterar o passo numerico do WRF.
WRF_HISTORY_INTERVAL_MINUTES="${WRF_HISTORY_INTERVAL_MINUTES:-60}"
WRF_START_HOUR="${WRF_START_HOUR:-0}"
WRF_END_HOUR="${WRF_END_HOUR:-$WRF_RUN_HOURS}"
case "$WRF_HISTORY_INTERVAL_MINUTES" in
  60|180) ;;
  *) echo "WRF_HISTORY_INTERVAL_MINUTES precisa ser 60 ou 180" >&2; exit 2 ;;
esac
for VALUE in "$WRF_START_HOUR" "$WRF_END_HOUR"; do
  [[ "$VALUE" =~ ^[0-9]+$ ]] || { echo "Inicio/fim WRF invalidos" >&2; exit 2; }
done
(( WRF_START_HOUR % 3 == 0 && WRF_END_HOUR % 3 == 0 )) || {
  echo "Inicio/fim WRF precisam ser multiplos de 3 h" >&2; exit 2;
}
(( WRF_END_HOUR > WRF_START_HOUR && WRF_END_HOUR <= 72 )) || {
  echo "Segmento WRF invalido: F${WRF_START_HOUR}-F${WRF_END_HOUR}" >&2; exit 2;
}
WRF_SEGMENT_HOURS=$((WRF_END_HOUR - WRF_START_HOUR))

IMAGE="dtcenter/wps_wrf:latest"
ROOT="${GITHUB_WORKSPACE:-$PWD}"
WORK="$ROOT/wrf_work"
DIAG="$ROOT/wrf_diagnostics"
HOST_UID="$(id -u)"
HOST_GID="$(id -g)"

mkdir -p "$WORK" "$DIAG"
rm -rf "$WORK/source" "$WORK/soil" "$WORK/run"
mkdir -p "$WORK/source" "$WORK/soil"

log(){ printf '\n===== %s =====\n' "$*"; }

copy_diag(){
  cp -f "$WORK"/*.stdout "$DIAG/" 2>/dev/null || true
  cp -f "$WORK"/*.log "$DIAG/" 2>/dev/null || true
  cp -f "$WORK/met-header.txt" "$DIAG/" 2>/dev/null || true
  cp -f "$WORK/wrf-runtime.env" "$DIAG/" 2>/dev/null || true
  cp -f "$WORK/wrfout-files.txt" "$DIAG/" 2>/dev/null || true
  cp -f "$WORK/run/rsl.error.0000" "$DIAG/" 2>/dev/null || true
  cp -f "$WORK/run/rsl.out.0000" "$DIAG/" 2>/dev/null || true
}
trap copy_diag EXIT

case "$SOURCE_MODEL" in
  icon|ecmwf) ;;
  *) echo "SOURCE_MODEL invalido: $SOURCE_MODEL" >&2; exit 2 ;;
esac

log "Copiando atmosfera ${SOURCE_MODEL^^}"
find "$SOURCE_DIR" -maxdepth 1 -type f -name '*.grib2' -print | sort | tee "$DIAG/source-files.txt"
if [[ ! -s "$DIAG/source-files.txt" ]]; then
  echo "Nenhum GRIB2 em SOURCE_DIR=$SOURCE_DIR" >&2
  exit 4
fi
while IFS= read -r FILE; do
  cp -f "$FILE" "$WORK/source/"
done < "$DIAG/source-files.txt"
cp -f "$SOURCE_VTABLE" "$WORK/Vtable.source"
cp -f "$ROOT/wrf/Vtable.GFS_SOIL" "$WORK/Vtable.soil"

log "Baixando apenas solo/superficie GFS de suporte"
python3 "$ROOT/wrf/fetch_gfs_land_support.py" \
  --date "$RUN_DATE" \
  --cycle "$RUN_CYCLE" \
  --max-hour "$WRF_END_HOUR" \
  --output-dir "$WORK/soil"

printf 'RUN_DATE=%s\nRUN_CYCLE=%s\nSOURCE_MODEL=%s\n' \
  "$RUN_DATE" "$RUN_CYCLE" "$SOURCE_MODEL" | tee "$DIAG/run.env"

START_ISO=$(date -u -d "${RUN_DATE} ${RUN_CYCLE}:00 UTC +${WRF_START_HOUR} hours" +%Y-%m-%d_%H:%M:%S)
END_ISO=$(date -u -d "${RUN_DATE} ${RUN_CYCLE}:00 UTC +${WRF_END_HOUR} hours" +%Y-%m-%d_%H:%M:%S)
START_Y=$(date -u -d "${RUN_DATE} ${RUN_CYCLE}:00 UTC +${WRF_START_HOUR} hours" +%Y)
START_M=$(date -u -d "${RUN_DATE} ${RUN_CYCLE}:00 UTC +${WRF_START_HOUR} hours" +%m)
START_D=$(date -u -d "${RUN_DATE} ${RUN_CYCLE}:00 UTC +${WRF_START_HOUR} hours" +%d)
START_H=$(date -u -d "${RUN_DATE} ${RUN_CYCLE}:00 UTC +${WRF_START_HOUR} hours" +%H)
END_Y=$(date -u -d "${RUN_DATE} ${RUN_CYCLE}:00 UTC +${WRF_END_HOUR} hours" +%Y)
END_M=$(date -u -d "${RUN_DATE} ${RUN_CYCLE}:00 UTC +${WRF_END_HOUR} hours" +%m)
END_D=$(date -u -d "${RUN_DATE} ${RUN_CYCLE}:00 UTC +${WRF_END_HOUR} hours" +%d)
END_H=$(date -u -d "${RUN_DATE} ${RUN_CYCLE}:00 UTC +${WRF_END_HOUR} hours" +%H)

log "Baixando e validando geografia WPS low-res"
rm -rf "$WORK/geog_extract" "$WORK/WPS_GEOG"
mkdir -p "$WORK/geog_extract"
curl -fL --retry 3 --connect-timeout 20 --max-time 900 \
  -o "$WORK/geog.tar.gz" \
  https://www2.mmm.ucar.edu/wrf/src/wps_files/geog_low_res_mandatory.tar.gz
tar -xzf "$WORK/geog.tar.gz" -C "$WORK/geog_extract"
TOPO_INDEX="$(find "$WORK/geog_extract" -type f -path '*/topo_gmted2010_5m/index' -print -quit)"
test -n "$TOPO_INDEX" || { echo "topo_gmted2010_5m ausente" >&2; exit 31; }
GEOG_ROOT="$(dirname "$(dirname "$TOPO_INDEX")")"
mkdir -p "$WORK/WPS_GEOG"
cp -a "$GEOG_ROOT/." "$WORK/WPS_GEOG/"
REQUIRED_GEOG=(topo_gmted2010_5m modis_landuse_20class_5m_with_lakes soiltype_top_5m soiltype_bot_5m greenfrac_fpar_modis_5m soiltemp_1deg albedo_modis maxsnowalb_modis lai_modis_10m)
for DATASET in "${REQUIRED_GEOG[@]}"; do
  test -f "$WORK/WPS_GEOG/$DATASET/index" || { echo "GEOG AUSENTE: $DATASET" >&2; exit 32; }
done

cat > "$WORK/namelist.wps" <<EOF
&share
 wrf_core = 'ARW',
 max_dom = 1,
 start_date = '${START_ISO}',
 end_date   = '${END_ISO}',
 interval_seconds = 10800,
 io_form_geogrid = 2,
/
&geogrid
 parent_id         = 1,
 parent_grid_ratio = 1,
 i_parent_start    = 1,
 j_parent_start    = 1,
 e_we              = 390,
 e_sn              = 360,
 geog_data_res     = 'lowres',
 dx = 4000,
 dy = 4000,
 map_proj = 'lambert',
 ref_lat   = -19.50,
 ref_lon   = -46.50,
 truelat1  = -15.0,
 truelat2  = -25.0,
 stand_lon = -46.50,
 geog_data_path = '/work/WPS_GEOG',
 opt_geogrid_tbl_path = '/comsoftware/wrf/WPS-4.3/geogrid/',
/
&ungrib
 out_format = 'WPS',
 prefix = 'SRC',
/
&metgrid
 fg_name = 'SRC','SOIL',
 io_form_metgrid = 2,
 opt_metgrid_tbl_path = '/comsoftware/wrf/WPS-4.3/metgrid/',
/
EOF

cat > "$WORK/namelist.input" <<EOF
&time_control
 run_days = 0,
 run_hours = ${WRF_SEGMENT_HOURS},
 run_minutes = 0,
 run_seconds = 0,
 start_year = ${START_Y},
 start_month = ${START_M},
 start_day = ${START_D},
 start_hour = ${START_H},
 end_year = ${END_Y},
 end_month = ${END_M},
 end_day = ${END_D},
 end_hour = ${END_H},
 interval_seconds = 10800,
 input_from_file = .true.,
 history_interval = ${WRF_HISTORY_INTERVAL_MINUTES},
 frames_per_outfile = 1,
 restart = .false.,
 io_form_history = 2,
 io_form_restart = 2,
 io_form_input = 2,
 io_form_boundary = 2,
/
&domains
 time_step = 24,
 time_step_fract_num = 0,
 time_step_fract_den = 1,
 max_dom = 1,
 e_we = 390,
 e_sn = 360,
 e_vert = 45,
 p_top_requested = 5000,
 num_metgrid_levels = 14,
 num_metgrid_soil_levels = 4,
 dx = 4000,
 dy = 4000,
 grid_id = 1,
 parent_id = 0,
 i_parent_start = 1,
 j_parent_start = 1,
 parent_grid_ratio = 1,
 parent_time_step_ratio = 1,
 feedback = 0,
 smooth_option = 0,
/
&physics
 physics_suite = 'CONUS',
 mp_physics = 8,
 do_radar_ref = 1,
 cu_physics = 0,
 ra_lw_physics = 4,
 ra_sw_physics = 4,
 bl_pbl_physics = 1,
 sf_sfclay_physics = 1,
 sf_surface_physics = 2,
 radt = 15,
 bldt = 0,
 cudt = 0,
 icloud = 1,
 num_land_cat = 21,
 sf_urban_physics = 0,
 fractional_seaice = 1,
/
&fdda
/
&dynamics
 hybrid_opt = 2,
 w_damping = 0,
 diff_opt = 2,
 km_opt = 4,
 diff_6th_opt = 0,
 diff_6th_factor = 0.12,
 base_temp = 290.,
 damp_opt = 3,
 zdamp = 5000.,
 dampcoef = 0.2,
 khdif = 0,
 kvdif = 0,
 non_hydrostatic = .true.,
 moist_adv_opt = 1,
 scalar_adv_opt = 1,
 gwd_opt = 1,
/
&bdy_control
 spec_bdy_width = 5,
 specified = .true.,
 nested = .false.,
/
&grib2
/
&namelist_quilt
 nio_tasks_per_group = 0,
 nio_groups = 1,
/
EOF

chmod -R a+rwX "$WORK"

log "Rodando WPS + WRF com atmosfera ${SOURCE_MODEL^^}"
docker run --rm \
  -e LOCAL_USER_ID="$HOST_UID" \
  -e OMPI_ALLOW_RUN_AS_ROOT=1 \
  -e OMPI_ALLOW_RUN_AS_ROOT_CONFIRM=1 \
  -v "$WORK:/work" \
  "$IMAGE" /bin/bash -lc '
    set -euo pipefail
    cd /work
    LETTERS=(AAA AAB AAC AAD AAE AAF AAG AAH AAI AAJ AAK AAL AAM AAN AAO AAP AAQ AAR AAS AAT AAU AAV AAW AAX AAY AAZ)

    echo "=== GEOGRID ==="
    /comsoftware/wrf/WPS-4.3/geogrid.exe > geogrid.stdout 2>&1 || { cat geogrid.stdout; cat geogrid.log || true; exit 41; }
    test -f geo_em.d01.nc

    echo "=== UNGRIB ATMOSFERA ==="
    sed -i -E "s/^[[:space:]]*prefix[[:space:]]*=.*/ prefix = '\''SRC'\'',/" namelist.wps
    rm -f GRIBFILE.* Vtable
    IDX=0
    while IFS= read -r FILE; do
      test "$IDX" -lt "${#LETTERS[@]}" || { echo "Muitos arquivos fonte" >&2; exit 42; }
      ln -sf "$FILE" "GRIBFILE.${LETTERS[$IDX]}"
      IDX=$((IDX+1))
    done < <(find /work/source -maxdepth 1 -type f -name "*.grib2" -print | sort)
    ln -sf /work/Vtable.source Vtable
    /comsoftware/wrf/WPS-4.3/ungrib.exe > ungrib-source.stdout 2>&1 || { cat ungrib-source.stdout; cat ungrib.log || true; exit 43; }
    ls -lh SRC:* || { cat ungrib-source.stdout; cat ungrib.log || true; exit 43; }

    echo "=== UNGRIB SOLO GFS ==="
    sed -i -E "s/^[[:space:]]*prefix[[:space:]]*=.*/ prefix = '\''SOIL'\'',/" namelist.wps
    rm -f GRIBFILE.* Vtable
    IDX=0
    while IFS= read -r FILE; do
      test "$IDX" -lt "${#LETTERS[@]}" || { echo "Muitos arquivos solo" >&2; exit 44; }
      ln -sf "$FILE" "GRIBFILE.${LETTERS[$IDX]}"
      IDX=$((IDX+1))
    done < <(find /work/soil -maxdepth 1 -type f -name "*.grib2" -print | sort)
    ln -sf /work/Vtable.soil Vtable
    /comsoftware/wrf/WPS-4.3/ungrib.exe > ungrib-soil.stdout 2>&1 || { cat ungrib-soil.stdout; cat ungrib.log || true; exit 45; }
    ls -lh SOIL:* || { cat ungrib-soil.stdout; cat ungrib.log || true; exit 45; }

    echo "=== METGRID ==="
    /comsoftware/wrf/WPS-4.3/metgrid.exe > metgrid.stdout 2>&1 || { cat metgrid.stdout; cat metgrid.log || true; exit 46; }
    FIRST_MET=$(find . -maxdepth 1 -name "met_em.d01.*.nc" -print | sort | head -1)
    test -n "$FIRST_MET" || { cat metgrid.stdout; cat metgrid.log || true; exit 46; }
    if ! ncdump -h "$FIRST_MET" > met-header.txt; then
      cat metgrid.stdout
      cat metgrid.log || true
      exit 47
    fi

    NUM_LEVELS=$(sed -n -E "s/.*:NUM_METGRID_LEVELS = ([0-9]+).*/\1/p" met-header.txt | head -1)
    if test -z "$NUM_LEVELS"; then
      NUM_LEVELS=$(sed -n -E "s/.*num_metgrid_levels = ([0-9]+).*/\1/p" met-header.txt | head -1)
    fi
    NUM_SOIL=$(sed -n -E "s/.*:NUM_METGRID_SOIL_LEVELS = ([0-9]+).*/\1/p" met-header.txt | head -1)

    test -n "$NUM_LEVELS" || {
      echo "NUM_METGRID_LEVELS nao detectado" >&2
      cat met-header.txt
      cat metgrid.stdout
      cat metgrid.log || true
      exit 47
    }
    test -n "$NUM_SOIL" || NUM_SOIL=0
    if test "$NUM_SOIL" -lt 1; then
      echo "NUM_METGRID_SOIL_LEVELS invalido: $NUM_SOIL" >&2
      grep -E "NUM_METGRID|num_sm_layers|num_st_layers|SOIL|ST\(|SM\(" met-header.txt || true
      cat metgrid.stdout
      cat metgrid.log || true
      exit 48
    fi
    grep -Eq "(float|double)[[:space:]]+ST\(" met-header.txt || { echo "Campo ST ausente do met_em" >&2; exit 49; }
    grep -Eq "(float|double)[[:space:]]+SM\(" met-header.txt || { echo "Campo SM ausente do met_em" >&2; exit 50; }

    echo "METGRID attributes: levels=$NUM_LEVELS soil=$NUM_SOIL"
    sed -i -E "s/num_metgrid_levels = [0-9]+/num_metgrid_levels = $NUM_LEVELS/" namelist.input
    sed -i -E "s/num_metgrid_soil_levels = [0-9]+/num_metgrid_soil_levels = $NUM_SOIL/" namelist.input

    echo "=== PREPARAR WRF ==="
    mkdir -p run
    cp -a /comsoftware/wrf/WRF-4.3/run/. run/
    cp namelist.input run/namelist.input
    cp met_em.d01.*.nc run/
    cd run

    echo "=== REAL.EXE ==="
    mpirun --oversubscribe --bind-to none -np 4 /comsoftware/wrf/WRF-4.3/main/real.exe || {
      STATUS=$?; tail -240 rsl.error.0000 || true; exit "$STATUS";
    }
    test -f wrfinput_d01
    test -f wrfbdy_d01

    echo "=== WRF 4 KM / REFL_10CM NATIVO ==="
    START_TS=$(date +%s)
    mpirun --oversubscribe --bind-to none -np 4 /comsoftware/wrf/WRF-4.3/main/wrf.exe || {
      STATUS=$?; tail -260 rsl.error.0000 || true; exit "$STATUS";
    }
    END_TS=$(date +%s)
    echo "WRF_RUNTIME_SECONDS=$((END_TS-START_TS))" | tee /work/wrf-runtime.env
    grep -q "SUCCESS COMPLETE WRF" rsl.error.0000
    ls -lh wrfout_d01_* | tee /work/wrfout-files.txt
  '

log "Copiando diagnosticos"
copy_diag
trap - EXIT

cat "$DIAG/run.env"
cat "$DIAG/wrf-runtime.env" 2>/dev/null || true
cat "$DIAG/wrfout-files.txt" 2>/dev/null || true
