#!/usr/bin/env bash
set -euo pipefail

: "$SOURCE_MODEL"
: "$RUN_DATE"
: "$RUN_CYCLE"
: "$WRF_START_HOUR"
: "$WRF_END_HOUR"
: "$SOURCE_DIR"
: "$SOURCE_VTABLE"
: "${WRF_DX_METERS:=4000}"
: "${WRF_DY_METERS:=4000}"
: "${WRF_E_WE:=401}"
: "${WRF_E_SN:=501}"
: "${WRF_TIME_STEP:=20}"
: "${WRF_REF_LAT:=-10.5}"
: "${WRF_REF_LON:=-40.0}"
: "${WRF_STAND_LON:=-40.0}"

IMAGE="dtcenter/wps_wrf:latest"
ROOT="$GITHUB_WORKSPACE"
WORK="$ROOT/cbr_wrf_work"
DIAG="$ROOT/cbr_diagnostics"
HOST_UID="$(id -u)"

mkdir -p "$WORK" "$DIAG"

copy_diag() {
  sudo chown -R "$HOST_UID" "$WORK" 2>/dev/null || true
  for f in "$WORK"/*.stdout "$WORK"/geogrid.log* "$WORK"/ungrib.log "$WORK"/metgrid.log*            "$WORK"/namelist.wps "$WORK"/namelist.input "$WORK"/met-header.txt            "$WORK"/run/rsl.error.0000 "$WORK"/run/rsl.out.0000 "$WORK"/run/namelist.input; do
    [[ -f "$f" ]] && cp -f "$f" "$DIAG/$(basename "$(dirname "$f")")-$(basename "$f")" 2>/dev/null || true
  done
}
trap copy_diag EXIT
rm -rf "$WORK/source" "$WORK/soil" "$WORK/run" "$WORK/geog_extract" "$WORK/WPS_GEOG"
mkdir -p "$WORK/source" "$WORK/soil" "$WORK/geog_extract" "$WORK/WPS_GEOG"

echo "CBR 4 KM: $WRF_START_HOUR -> $WRF_END_HOUR; grid=$WRF_E_WE x $WRF_E_SN; dt=$WRF_TIME_STEP s"

find "$SOURCE_DIR" -maxdepth 1 -type f -name '*.grib2' -print | sort > "$DIAG/source-files.txt"
test -s "$DIAG/source-files.txt"
while IFS= read -r f; do cp -f "$f" "$WORK/source/"; done < "$DIAG/source-files.txt"
cp -f "$SOURCE_VTABLE" "$WORK/Vtable.source"
cp -f "$ROOT/wrf/Vtable.GFS_SOIL" "$WORK/Vtable.soil"

python3 "$ROOT/wrf/fetch_gfs_land_support.py" --date "$RUN_DATE" --cycle "$RUN_CYCLE" --max-hour "$WRF_END_HOUR" --output-dir "$WORK/soil"

START_ISO="$(date -u -d "$RUN_DATE $RUN_CYCLE:00 UTC +$WRF_START_HOUR hours" +%Y-%m-%d_%H:%M:%S)"
END_ISO="$(date -u -d "$RUN_DATE $RUN_CYCLE:00 UTC +$WRF_END_HOUR hours" +%Y-%m-%d_%H:%M:%S)"
START_COMPACT="$(date -u -d "$RUN_DATE $RUN_CYCLE:00 UTC +$WRF_START_HOUR hours" +%Y%m%d%H)"
END_COMPACT="$(date -u -d "$RUN_DATE $RUN_CYCLE:00 UTC +$WRF_END_HOUR hours" +%Y%m%d%H)"
START_Y="$(echo "$START_COMPACT" | cut -c1-4)"
START_M="$(echo "$START_COMPACT" | cut -c5-6)"
START_D="$(echo "$START_COMPACT" | cut -c7-8)"
START_H="$(echo "$START_COMPACT" | cut -c9-10)"
END_Y="$(echo "$END_COMPACT" | cut -c1-4)"
END_M="$(echo "$END_COMPACT" | cut -c5-6)"
END_D="$(echo "$END_COMPACT" | cut -c7-8)"
END_H="$(echo "$END_COMPACT" | cut -c9-10)"
SEG_H=$((WRF_END_HOUR-WRF_START_HOUR))
RESTART_MIN=$((SEG_H*60))

curl --fail --location --retry 5 --retry-delay 5 --connect-timeout 20 --max-time 900 -o "$WORK/geog.tar.gz" https://www2.mmm.ucar.edu/wrf/src/wps_files/geog_low_res_mandatory.tar.gz
tar -xzf "$WORK/geog.tar.gz" -C "$WORK/geog_extract"
TOPO_INDEX="$(find "$WORK/geog_extract" -type f -path '*/topo_gmted2010_5m/index' -print -quit)"
test -n "$TOPO_INDEX"
GEOG_ROOT="$(dirname "$(dirname "$TOPO_INDEX")")"
cp -a "$GEOG_ROOT/." "$WORK/WPS_GEOG/"

for d in topo_gmted2010_5m modis_landuse_20class_5m_with_lakes soiltype_top_5m soiltype_bot_5m greenfrac_fpar_modis_5m soiltemp_1deg albedo_modis maxsnowalb_modis lai_modis_10m; do
  test -f "$WORK/WPS_GEOG/$d/index" || { echo "GEOG AUSENTE: $d" >&2; exit 31; }
done

cat > "$WORK/namelist.wps" <<EOF
&share
 wrf_core='ARW',
 max_dom=1,
 start_date='$START_ISO',
 end_date='$END_ISO',
 interval_seconds=10800,
 io_form_geogrid=2,
/
&geogrid
 parent_id=1,
 parent_grid_ratio=1,
 i_parent_start=1,
 j_parent_start=1,
 e_we=$WRF_E_WE,
 e_sn=$WRF_E_SN,
 geog_data_res='lowres',
 dx=$WRF_DX_METERS,
 dy=$WRF_DY_METERS,
 map_proj='lambert',
 ref_lat=$WRF_REF_LAT,
 ref_lon=$WRF_REF_LON,
 truelat1=-25.0,
 truelat2=-35.0,
 stand_lon=$WRF_STAND_LON,
 geog_data_path='/work/WPS_GEOG',
 opt_geogrid_tbl_path='/comsoftware/wrf/WPS-4.3/geogrid/',
/
&ungrib
 out_format='WPS',
 prefix='SRC',
/
&metgrid
 fg_name='SRC','SOIL',
 io_form_metgrid=2,
 opt_metgrid_tbl_path='/comsoftware/wrf/WPS-4.3/metgrid/',
/
EOF

cat > "$WORK/namelist.input" <<EOF
&time_control
 run_days=0,
 run_hours=$SEG_H,
 run_minutes=0,
 run_seconds=0,
 start_year=$START_Y,
 start_month=$START_M,
 start_day=$START_D,
 start_hour=$START_H,
 end_year=$END_Y,
 end_month=$END_M,
 end_day=$END_D,
 end_hour=$END_H,
 interval_seconds=10800,
 input_from_file=.true.,
 history_interval=$WRF_HISTORY_INTERVAL_MINUTES,
 frames_per_outfile=1,
 restart=.false.,
 restart_interval=$RESTART_MIN,
 io_form_history=2,
 io_form_restart=2,
 io_form_input=2,
 io_form_boundary=2,
/
&domains
 time_step=$WRF_TIME_STEP,
 time_step_fract_num=0,
 time_step_fract_den=1,
 max_dom=1,
 e_we=$WRF_E_WE,
 e_sn=$WRF_E_SN,
 e_vert=45,
 p_top_requested=5000,
 num_metgrid_levels=14,
 num_metgrid_soil_levels=4,
 dx=$WRF_DX_METERS,
 dy=$WRF_DY_METERS,
 grid_id=1,
 parent_id=0,
 i_parent_start=1,
 j_parent_start=1,
 parent_grid_ratio=1,
 parent_time_step_ratio=1,
 feedback=0,
 smooth_option=0,
/
&physics
 physics_suite='CONUS',
 mp_physics=8,
 do_radar_ref=1,
 cu_physics=0,
 ra_lw_physics=4,
 ra_sw_physics=4,
 bl_pbl_physics=1,
 sf_sfclay_physics=1,
 sf_surface_physics=2,
 radt=5,
 bldt=0,
 cudt=0,
 icloud=1,
 num_land_cat=21,
 sf_urban_physics=0,
 fractional_seaice=1,
/
&fdda
/
&dynamics
 hybrid_opt=2,
 w_damping=0,
 diff_opt=2,
 km_opt=4,
 diff_6th_opt=0,
 diff_6th_factor=0.12,
 base_temp=290.,
 damp_opt=3,
 zdamp=5000.,
 dampcoef=0.2,
 khdif=0,
 kvdif=0,
 non_hydrostatic=.true.,
 moist_adv_opt=1,
 scalar_adv_opt=1,
 gwd_opt=1,
/
&bdy_control
 spec_bdy_width=5,
 specified=.true.,
 nested=.false.,
/
&grib2
/
&namelist_quilt
 nio_tasks_per_group=0,
 nio_groups=1,
/
EOF

chmod -R a+rwX "$WORK"

docker run --rm --entrypoint /bin/bash -e LOCAL_USER_ID="$HOST_UID" -e OMPI_ALLOW_RUN_AS_ROOT=1 -e OMPI_ALLOW_RUN_AS_ROOT_CONFIRM=1 -v "$WORK:/work" "$IMAGE" -lc '
set -euo pipefail
cd /work
LETTERS=(AAA AAB AAC AAD AAE AAF AAG AAH AAI AAJ AAK AAL AAM AAN AAO AAP AAQ AAR AAS AAT AAU AAV AAW AAX AAY AAZ)

IDX=0
rm -f GRIBFILE.* Vtable
while IFS= read -r FILE; do
  if [ "$IDX" -ge 26 ]; then exit 42; fi
  LINK_NAME=${LETTERS[$IDX]}
  ln -sf "$FILE" "GRIBFILE.$LINK_NAME"
  IDX=$((IDX+1))
done < <(find /work/source -maxdepth 1 -type f -name "*.grib2" -print | sort)
ln -sf /work/Vtable.source Vtable

echo "=== GEOGRID ==="
/comsoftware/wrf/WPS-4.3/geogrid.exe > geogrid.stdout 2>&1 || { cat geogrid.stdout; cat geogrid.log 2>/dev/null || true; exit 41; }
test -f geo_em.d01.nc || { cat geogrid.stdout; exit 41; }
echo "=== UNGRIB ATMOSFERA ==="
/comsoftware/wrf/WPS-4.3/ungrib.exe > ungrib-source.stdout 2>&1 || { cat ungrib-source.stdout; cat ungrib.log 2>/dev/null || true; exit 43; }
ls -lh SRC:* || { cat ungrib-source.stdout; cat ungrib.log 2>/dev/null || true; exit 43; }

sed -i -E "s/^[[:space:]]*prefix[[:space:]]*=.*/ prefix = \"SOIL\",/" namelist.wps
IDX=0
rm -f GRIBFILE.* Vtable
while IFS= read -r FILE; do
  if [ "$IDX" -ge 26 ]; then exit 44; fi
  LINK_NAME=${LETTERS[$IDX]}
  ln -sf "$FILE" "GRIBFILE.$LINK_NAME"
  IDX=$((IDX+1))
done < <(find /work/soil -maxdepth 1 -type f -name "*.grib2" -print | sort)
ln -sf /work/Vtable.soil Vtable
echo "=== UNGRIB SOLO GFS ==="
/comsoftware/wrf/WPS-4.3/ungrib.exe > ungrib-soil.stdout 2>&1 || { cat ungrib-soil.stdout; cat ungrib.log 2>/dev/null || true; exit 45; }
ls -lh SOIL:* || { cat ungrib-soil.stdout; cat ungrib.log 2>/dev/null || true; exit 45; }

echo "=== METGRID ==="
/comsoftware/wrf/WPS-4.3/metgrid.exe > metgrid.stdout 2>&1 || { cat metgrid.stdout; cat metgrid.log 2>/dev/null || true; exit 46; }
FIRST_MET=$(find . -maxdepth 1 -name "met_em.d01.*.nc" -print | sort | head -1)
test -n "$FIRST_MET" || { cat metgrid.stdout; cat metgrid.log 2>/dev/null || true; exit 46; }
ncdump -h "$FIRST_MET" > met-header.txt
NUM_LEVELS=$(sed -n -E "s/^[[:space:]]*num_metgrid_levels = ([0-9]+) ;/\1/p" met-header.txt | head -1)
NUM_SOIL=$(sed -n -E "s/^[[:space:]]*num_sm_layers = ([0-9]+) ;/\1/p" met-header.txt | head -1)
test -n "$NUM_LEVELS"
test -n "$NUM_SOIL"
sed -i -E "s/num_metgrid_levels=[0-9]+/num_metgrid_levels=$NUM_LEVELS/" namelist.input
sed -i -E "s/num_metgrid_soil_levels=[0-9]+/num_metgrid_soil_levels=$NUM_SOIL/" namelist.input

mkdir -p run
cp -a /comsoftware/wrf/WRF-4.3/run/. run/
cp namelist.input run/namelist.input
cp met_em.d01.*.nc run/
cd run

echo "=== REAL.EXE ==="
mpirun --allow-run-as-root --oversubscribe --bind-to none -np "${WRF_MPI_PROCS:-8}" /comsoftware/wrf/WRF-4.3/main/real.exe || { STATUS=$?; tail -240 rsl.error.0000 || true; exit "$STATUS"; }
test -s wrfinput_d01
test -s wrfbdy_d01

echo "=== WRF CBR 3 KM / REFL_10CM NATIVO ==="
mpirun --allow-run-as-root --oversubscribe --bind-to none -np 4 /comsoftware/wrf/WRF-4.3/main/wrf.exe > wrf.stdout 2>&1 || { STATUS=$?; tail -260 rsl.error.0000 || true; exit "$STATUS"; }
grep -q "SUCCESS COMPLETE WRF" rsl.error.0000 || { tail -260 rsl.error.0000 || true; exit 51; }
ls -lh wrfout_d01_* wrfrst_d01_* wrfbdy_d01 | tee /work/tarc-files.txt
'