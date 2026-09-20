#!/usr/bin/env bash
set -euo pipefail

: "${FORCE_RUN_DATE:?FORCE_RUN_DATE ausente}"
: "${FORCE_RUN_CYCLE:?FORCE_RUN_CYCLE ausente}"
: "${WRF_START_HOUR:?WRF_START_HOUR ausente}"
: "${WRF_END_HOUR:?WRF_END_HOUR ausente}"
: "${METBR_COLD_START:?METBR_COLD_START ausente}"

if (( WRF_END_HOUR <= WRF_START_HOUR )); then
  echo "Intervalo METBR invalido" >&2
  exit 2
fi

IMAGE="dtcenter/wps_wrf:latest"
ROOT="${GITHUB_WORKSPACE:-$PWD}"
WORK="$ROOT/wrf_work"
DIAG="$ROOT/wrf_diagnostics"
RESTART_INPUT="$ROOT/metbr_restart_input"
HOST_UID="$(id -u)"

RUN_DATE="$FORCE_RUN_DATE"
RUN_CYCLE="$FORCE_RUN_CYCLE"
START_HOUR="$WRF_START_HOUR"
END_HOUR="$WRF_END_HOUR"

# The first segment creates one complete lateral-boundary file for the
# entire forecast. WRF itself still runs only the short segment.
if [[ "$METBR_COLD_START" == "1" ]]; then
  BOUNDARY_END_HOUR="${METBR_BOUNDARY_END_HOUR:-40}"
else
  BOUNDARY_END_HOUR="$END_HOUR"
fi

if [[ "$METBR_COLD_START" == "1" ]] && (( BOUNDARY_END_HOUR < END_HOUR )); then
  echo "METBR_BOUNDARY_END_HOUR deve cobrir o segmento" >&2
  exit 3
fi

SOURCE_END=$(( ((BOUNDARY_END_HOUR + 2) / 3) * 3 ))
BASE_URL="https://noaa-gfs-bdp-pds.s3.amazonaws.com/gfs.${RUN_DATE}/${RUN_CYCLE}/atmos"

START_ISO="$(date -u -d "${RUN_DATE} ${RUN_CYCLE}:00 UTC +${START_HOUR} hours" +%Y-%m-%d_%H:%M:%S)"
END_ISO="$(date -u -d "${RUN_DATE} ${RUN_CYCLE}:00 UTC +${END_HOUR} hours" +%Y-%m-%d_%H:%M:%S)"
BOUNDARY_END_ISO="$(date -u -d "${RUN_DATE} ${RUN_CYCLE}:00 UTC +${BOUNDARY_END_HOUR} hours" +%Y-%m-%d_%H:%M:%S)"
SOURCE_END_ISO="$(date -u -d "${RUN_DATE} ${RUN_CYCLE}:00 UTC +${SOURCE_END} hours" +%Y-%m-%d_%H:%M:%S)"

START_Y="$(date -u -d "${RUN_DATE} ${RUN_CYCLE}:00 UTC +${START_HOUR} hours" +%Y)"
START_M="$(date -u -d "${RUN_DATE} ${RUN_CYCLE}:00 UTC +${START_HOUR} hours" +%m)"
START_D="$(date -u -d "${RUN_DATE} ${RUN_CYCLE}:00 UTC +${START_HOUR} hours" +%d)"
START_CLOCK="$(date -u -d "${RUN_DATE} ${RUN_CYCLE}:00 UTC +${START_HOUR} hours" +%H)"

# real.exe uses start/end; wrf.exe uses run_hours, so the cold-start
# namelist can create a full 40 h wrfbdy while WRF only integrates 4 h.
REAL_END_Y="$(date -u -d "${RUN_DATE} ${RUN_CYCLE}:00 UTC +${BOUNDARY_END_HOUR} hours" +%Y)"
REAL_END_M="$(date -u -d "${RUN_DATE} ${RUN_CYCLE}:00 UTC +${BOUNDARY_END_HOUR} hours" +%m)"
REAL_END_D="$(date -u -d "${RUN_DATE} ${RUN_CYCLE}:00 UTC +${BOUNDARY_END_HOUR} hours" +%d)"
REAL_END_CLOCK="$(date -u -d "${RUN_DATE} ${RUN_CYCLE}:00 UTC +${BOUNDARY_END_HOUR} hours" +%H)"

DURATION_HOURS=$((END_HOUR-START_HOUR))

rm -rf "$WORK" "$DIAG" "$RESTART_INPUT"
mkdir -p "$WORK" "$DIAG"

cat > "$DIAG/run.env" <<EOF
RUN_DATE=$RUN_DATE
RUN_CYCLE=$RUN_CYCLE
WRF_START_HOUR=$START_HOUR
WRF_END_HOUR=$END_HOUR
METBR_BOUNDARY_END_HOUR=$BOUNDARY_END_HOUR
METBR_COLD_START=$METBR_COLD_START
START_ISO=$START_ISO
END_ISO=$END_ISO
BOUNDARY_END_ISO=$BOUNDARY_END_ISO
EOF

if [[ "$METBR_COLD_START" == "1" ]]; then
  echo "METBR: cold start F$(printf '%03d' "$START_HOUR")-F$(printf '%03d' "$END_HOUR"), boundary through F$(printf '%03d' "$BOUNDARY_END_HOUR")" | tee "$DIAG/segment.log"
  mkdir -p "$WORK/gfs" "$WORK/WPS_GEOG"

  for H in $(seq 0 3 "$SOURCE_END"); do
    printf -v FH '%03d' "$H"
    FILE="gfs.t${RUN_CYCLE}z.pgrb2.0p25.f${FH}"
    echo "Baixando $FILE"
    curl -fL --retry 4 --retry-delay 5 --connect-timeout 20 --max-time 900 \
      -o "$WORK/gfs/$FILE" "$BASE_URL/$FILE"
  done

  curl -fL --retry 3 --connect-timeout 20 --max-time 900 \
    -o "$WORK/geog.tar.gz" \
    https://www2.mmm.ucar.edu/wrf/src/wps_files/geog_low_res_mandatory.tar.gz
  tar -xzf "$WORK/geog.tar.gz" -C "$WORK/WPS_GEOG"

  cat > "$WORK/namelist.wps" <<EOF
&share
 wrf_core = 'ARW',
 max_dom = 1,
 start_date = '${START_ISO}',
 end_date = '${SOURCE_END_ISO}',
 interval_seconds = 10800,
 io_form_geogrid = 2,
/
&geogrid
 parent_id = 1,
 parent_grid_ratio = 1,
 i_parent_start = 1,
 j_parent_start = 1,
 e_we = 300,
 e_sn = 360,
 geog_data_res = 'lowres',
 dx = 4000,
 dy = 4000,
 map_proj = 'lambert',
 ref_lat = -28.10,
 ref_lon = -53.45,
 truelat1 = -25.0,
 truelat2 = -35.0,
 stand_lon = -53.45,
 geog_data_path = '/work/WPS_GEOG',
 opt_geogrid_tbl_path = '/comsoftware/wrf/WPS-4.3/geogrid/',
/
&ungrib
 out_format = 'WPS',
 prefix = 'FILE',
/
&metgrid
 fg_name = 'FILE',
 io_form_metgrid = 2,
 opt_metgrid_tbl_path = '/comsoftware/wrf/WPS-4.3/metgrid/',
/
EOF

  LETTERS=(AAA AAB AAC AAD AAE AAF AAG AAH AAI AAJ AAK AAL AAM AAN AAO AAP AAQ)
  IDX=0
  for H in $(seq 0 3 "$SOURCE_END"); do
    printf -v FH '%03d' "$H"
    FILE="gfs.t${RUN_CYCLE}z.pgrb2.0p25.f${FH}"
    ln -sf "gfs/$FILE" "$WORK/GRIBFILE.${LETTERS[$IDX]}"
    IDX=$((IDX+1))
  done
else
  echo "METBR: restart F$(printf '%03d' "$START_HOUR")-F$(printf '%03d' "$END_HOUR")" | tee "$DIAG/segment.log"
  test -f "$RESTART_INPUT/wrfbdy_d01" || { echo "wrfbdy_d01 ausente" >&2; exit 21; }
  compgen -G "$RESTART_INPUT/wrfrst_d01_*" > /dev/null || { echo "wrfrst ausente" >&2; exit 22; }
fi

cat > "$WORK/namelist.input" <<EOF
&time_control
 run_days = 0,
 run_hours = ${DURATION_HOURS},
 run_minutes = 0,
 run_seconds = 0,
 start_year = ${START_Y},
 start_month = ${START_M},
 start_day = ${START_D},
 start_hour = ${START_CLOCK},
 end_year = ${REAL_END_Y},
 end_month = ${REAL_END_M},
 end_day = ${REAL_END_D},
 end_hour = ${REAL_END_CLOCK},
 interval_seconds = 10800,
 input_from_file = .true.,
 history_interval = 60,
 frames_per_outfile = 1,
 restart = $( [[ "$METBR_COLD_START" == "1" ]] && echo .false. || echo .true. ),
 restart_interval = $((DURATION_HOURS*60)),
 override_restart_timers = .true.,
 write_hist_at_0h_rst = .false.,
 io_form_history = 2,
 io_form_restart = 102,
 io_form_input = 2,
 io_form_boundary = 2,
/
&domains
 time_step = 18,
 time_step_fract_num = 0,
 time_step_fract_den = 1,
 max_dom = 1,
 e_we = 300,
 e_sn = 360,
 e_vert = 45,
 p_top_requested = 5000,
 num_metgrid_levels = 34,
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
&dynamics
 hybrid_opt = 2,
 w_damping = 1,
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

chmod -R a+rwX "$WORK" "$RESTART_INPUT" 2>/dev/null || true
docker pull "$IMAGE"

docker run --rm \
  -e LOCAL_USER_ID="$HOST_UID" \
  -e OMPI_ALLOW_RUN_AS_ROOT=1 \
  -e OMPI_ALLOW_RUN_AS_ROOT_CONFIRM=1 \
  -v "$WORK:/work" \
  -v "$RESTART_INPUT:/restart_input:ro" \
  "$IMAGE" /bin/bash -lc '
    set -euo pipefail
    cd /work
    mkdir -p run
    if [[ "'"$METBR_COLD_START"'" == "1" ]]; then
      /comsoftware/wrf/WPS-4.3/geogrid.exe > geogrid.stdout 2>&1
      test -f geo_em.d01.nc
      ln -sf /comsoftware/wrf/WPS-4.3/ungrib/Variable_Tables/Vtable.GFS Vtable
      /comsoftware/wrf/WPS-4.3/ungrib.exe > ungrib.stdout 2>&1
      /comsoftware/wrf/WPS-4.3/metgrid.exe > metgrid.stdout 2>&1
      cp -a /comsoftware/wrf/WRF-4.3/run/. run/
      cp namelist.input run/namelist.input
      cp met_em.d01.*.nc run/
      cd run
      mpirun --oversubscribe --bind-to none -np 4 /comsoftware/wrf/WRF-4.3/main/real.exe
      test -f wrfinput_d01
      test -f wrfbdy_d01
    else
      cp -a /comsoftware/wrf/WRF-4.3/run/. run/
      cp namelist.input run/namelist.input
      cp /restart_input/wrfbdy_d01 run/
      cp /restart_input/wrfrst_d01_* run/
      cd run
    fi
    START_TS=$(date +%s)
    mpirun --oversubscribe --bind-to none -np 4 /comsoftware/wrf/WRF-4.3/main/wrf.exe
    END_TS=$(date +%s)
    echo "WRF_RUNTIME_SECONDS=$((END_TS-START_TS))" > /work/wrf-runtime.env
    grep -q "SUCCESS COMPLETE WRF" rsl.error.0000
    compgen -G "wrfout_d01_*" > /dev/null
    compgen -G "wrfrst_d01_*" > /dev/null
  '

cp -f "$WORK/wrf-runtime.env" "$DIAG/" 2>/dev/null || true
cp -f "$WORK/run/rsl.error.0000" "$DIAG/" 2>/dev/null || true
cp -f "$WORK/run/rsl.out.0000" "$DIAG/" 2>/dev/null || true
cp -f "$WORK/run/wrfout_d01_"* "$DIAG/" 2>/dev/null || true
cat "$DIAG/run.env"
cat "$DIAG/wrf-runtime.env" 2>/dev/null || true
