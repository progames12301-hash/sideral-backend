#!/usr/bin/env bash
set -euo pipefail

IMAGE="dtcenter/wps_wrf:latest"
ROOT="${GITHUB_WORKSPACE:-$PWD}"
WORK="$ROOT/wrf_work"
DIAG="$ROOT/wrf_diagnostics"
HOST_UID="$(id -u)"
HOST_GID="$(id -g)"
mkdir -p "$WORK" "$DIAG"
rm -rf "$WORK"/*

log(){ printf '\n===== %s =====\n' "$*"; }

log "Recursos antes do WRF"
nproc
free -h
df -h
printf 'HOST_UID=%s HOST_GID=%s\n' "$HOST_UID" "$HOST_GID"

log "Escolhendo rodada GFS mais recente com F006 disponível"
RUN_DATE=""
RUN_CYCLE=""
BASE_URL=""

# O GFS operacional possui apenas ciclos 00Z, 06Z, 12Z e 18Z.
# Gera candidatos a partir do ciclo sinoptico mais recente e volta de 6 em 6 horas.
mapfile -t GFS_CANDIDATES < <(python3 - <<'PY'
import datetime as dt
now = dt.datetime.now(dt.timezone.utc)
base_hour = (now.hour // 6) * 6
base = now.replace(hour=base_hour, minute=0, second=0, microsecond=0)
for offset in range(0, 55, 6):
    candidate = base - dt.timedelta(hours=offset)
    print(candidate.strftime('%Y%m%d %H'))
PY
)

for CANDIDATE in "${GFS_CANDIDATES[@]}"; do
  read -r DATE CYCLE <<< "$CANDIDATE"
  URL="https://noaa-gfs-bdp-pds.s3.amazonaws.com/gfs.${DATE}/${CYCLE}/atmos/gfs.t${CYCLE}z.pgrb2.0p25.f006"
  echo "Testando ${DATE} ${CYCLE}Z"

  # Consulta um byte do F006. Isto evita considerar uma rodada ainda nao publicada.
  if curl -fsSL --range 0-0 --connect-timeout 15 --max-time 45 -o /dev/null "$URL"; then
    RUN_DATE="$DATE"
    RUN_CYCLE="$CYCLE"
    BASE_URL="https://noaa-gfs-bdp-pds.s3.amazonaws.com/gfs.${DATE}/${CYCLE}/atmos"
    echo "Rodada selecionada: ${RUN_DATE} ${RUN_CYCLE}Z"
    break
  fi
done

if [[ -z "$RUN_DATE" ]]; then
  echo "Nenhuma rodada GFS 00Z/06Z/12Z/18Z recente com F006 encontrada." >&2
  exit 20
fi

echo "RUN_DATE=$RUN_DATE" | tee "$DIAG/run.env"
echo "RUN_CYCLE=$RUN_CYCLE" | tee -a "$DIAG/run.env"
echo "BASE_URL=$BASE_URL" | tee -a "$DIAG/run.env"

START_ISO=$(date -u -d "${RUN_DATE} ${RUN_CYCLE}:00 UTC" +%Y-%m-%d_%H:%M:%S)
END_ISO=$(date -u -d "${RUN_DATE} ${RUN_CYCLE}:00 UTC +6 hours" +%Y-%m-%d_%H:%M:%S)
START_Y=$(date -u -d "${RUN_DATE} ${RUN_CYCLE}:00 UTC" +%Y)
START_M=$(date -u -d "${RUN_DATE} ${RUN_CYCLE}:00 UTC" +%m)
START_D=$(date -u -d "${RUN_DATE} ${RUN_CYCLE}:00 UTC" +%d)
START_H=$(date -u -d "${RUN_DATE} ${RUN_CYCLE}:00 UTC" +%H)
END_Y=$(date -u -d "${RUN_DATE} ${RUN_CYCLE}:00 UTC +6 hours" +%Y)
END_M=$(date -u -d "${RUN_DATE} ${RUN_CYCLE}:00 UTC +6 hours" +%m)
END_D=$(date -u -d "${RUN_DATE} ${RUN_CYCLE}:00 UTC +6 hours" +%d)
END_H=$(date -u -d "${RUN_DATE} ${RUN_CYCLE}:00 UTC +6 hours" +%H)

log "Baixando GFS F000 F003 F006"
mkdir -p "$WORK/gfs"
for FH in 000 003 006; do
  FILE="gfs.t${RUN_CYCLE}z.pgrb2.0p25.f${FH}"
  curl -fL --retry 4 --retry-delay 5 --connect-timeout 20 --max-time 900 \
    -o "$WORK/gfs/$FILE" "$BASE_URL/$FILE"
  ls -lh "$WORK/gfs/$FILE"
done

log "Baixando e extraindo geografia WPS low-res"
mkdir -p "$WORK/geog"
curl -fL --retry 3 --connect-timeout 20 --max-time 900 \
  -o "$WORK/geog.tar.gz" \
  https://www2.mmm.ucar.edu/wrf/src/wps_files/geog_low_res_mandatory.tar.gz
mkdir -p "$WORK/WPS_GEOG"
tar -xzf "$WORK/geog.tar.gz" -C "$WORK/WPS_GEOG"
find "$WORK/WPS_GEOG" -maxdepth 2 -type f -name index | sed -n '1,30p' | tee "$DIAG/geog-indexes.txt"

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
 e_we              = 300,
 e_sn              = 360,
 geog_data_res     = 'lowres',
 dx = 4000,
 dy = 4000,
 map_proj = 'lambert',
 ref_lat   = -28.10,
 ref_lon   = -53.45,
 truelat1  = -25.0,
 truelat2  = -35.0,
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

cat > "$WORK/namelist.input" <<EOF
&time_control
 run_days = 0,
 run_hours = 6,
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
 history_interval = 180,
 frames_per_outfile = 1,
 restart = .false.,
 io_form_history = 2,
 io_form_restart = 2,
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

log "Preparando nomes GRIBFILE"
ln -sf "gfs/gfs.t${RUN_CYCLE}z.pgrb2.0p25.f000" "$WORK/GRIBFILE.AAA"
ln -sf "gfs/gfs.t${RUN_CYCLE}z.pgrb2.0p25.f003" "$WORK/GRIBFILE.AAB"
ln -sf "gfs/gfs.t${RUN_CYCLE}z.pgrb2.0p25.f006" "$WORK/GRIBFILE.AAC"

log "Ajustando permissoes do volume para o container DTC"
# A imagem oficial DTC usa LOCAL_USER_ID para executar com o mesmo UID do host.
# Mantemos tambem permissao de escrita no volume como protecao adicional.
chmod -R a+rwX "$WORK"
ls -ld "$WORK" "$WORK/WPS_GEOG" "$WORK/gfs"

log "Rodando WPS + real.exe + WRF"
docker run --rm \
  -e LOCAL_USER_ID="$HOST_UID" \
  -e OMPI_ALLOW_RUN_AS_ROOT=1 \
  -e OMPI_ALLOW_RUN_AS_ROOT_CONFIRM=1 \
  -v "$WORK:/work" \
  "$IMAGE" /bin/bash -lc '
    set -euo pipefail
    cd /work

    echo "=== IDENTIDADE / TESTE DE ESCRITA ==="
    id
    pwd
    ls -ld /work /work/WPS_GEOG /work/gfs
    test -r /work/namelist.wps
    test -r /work/namelist.input
    touch /work/.sideral-write-test
    echo "container-write-ok" > /work/.sideral-write-test
    cat /work/.sideral-write-test
    rm -f /work/.sideral-write-test

    echo "=== GEOGRID ==="
    /comsoftware/wrf/WPS-4.3/geogrid.exe > geogrid.stdout 2>&1 || {
      STATUS=$?
      echo "GEOGRID FALHOU status=$STATUS"
      cat geogrid.stdout || true
      cat geogrid.log || true
      exit "$STATUS"
    }
    cat geogrid.stdout
    test -f geo_em.d01.nc

    echo "=== UNGRIB ==="
    ln -sf /comsoftware/wrf/WPS-4.3/ungrib/Variable_Tables/Vtable.GFS Vtable
    /comsoftware/wrf/WPS-4.3/ungrib.exe > ungrib.stdout 2>&1 || {
      STATUS=$?
      echo "UNGRIB FALHOU status=$STATUS"
      cat ungrib.stdout || true
      cat ungrib.log || true
      exit "$STATUS"
    }
    cat ungrib.stdout
    ls FILE:* >/dev/null

    echo "=== METGRID ==="
    /comsoftware/wrf/WPS-4.3/metgrid.exe > metgrid.stdout 2>&1 || {
      STATUS=$?
      echo "METGRID FALHOU status=$STATUS"
      cat metgrid.stdout || true
      cat metgrid.log || true
      exit "$STATUS"
    }
    cat metgrid.stdout
    ls met_em.d01.*.nc

    echo "=== PREPARAR WRF RUN ==="
    mkdir -p run
    cp -a /comsoftware/wrf/WRF-4.3/run/. run/
    cp namelist.input run/namelist.input
    cp met_em.d01.*.nc run/
    cd run

    echo "=== REAL ==="
    mpirun -np 4 /comsoftware/wrf/WRF-4.3/main/real.exe || {
      STATUS=$?
      echo "REAL.EXE FALHOU status=$STATUS"
      tail -160 rsl.error.0000 || true
      exit "$STATUS"
    }
    tail -80 rsl.error.0000 || true
    test -f wrfinput_d01
    test -f wrfbdy_d01

    echo "=== WRF 4 KM F000-F006 ==="
    START_TS=$(date +%s)
    mpirun -np 4 /comsoftware/wrf/WRF-4.3/main/wrf.exe || {
      STATUS=$?
      echo "WRF.EXE FALHOU status=$STATUS"
      tail -200 rsl.error.0000 || true
      exit "$STATUS"
    }
    END_TS=$(date +%s)
    echo "WRF_RUNTIME_SECONDS=$((END_TS-START_TS))" | tee /work/wrf-runtime.env
    tail -100 rsl.error.0000 || true
    grep -q "SUCCESS COMPLETE WRF" rsl.error.0000
    ls -lh wrfout_d01_* | tee /work/wrfout-files.txt
  '

log "Copiando apenas diagnosticos (nao wrfout)"
cp -f "$WORK"/*.stdout "$DIAG/" 2>/dev/null || true
cp -f "$WORK"/*.log "$DIAG/" 2>/dev/null || true
cp -f "$WORK/wrf-runtime.env" "$DIAG/" 2>/dev/null || true
cp -f "$WORK/wrfout-files.txt" "$DIAG/" 2>/dev/null || true
cp -f "$WORK/run/rsl.error.0000" "$DIAG/" 2>/dev/null || true
cp -f "$WORK/run/rsl.out.0000" "$DIAG/" 2>/dev/null || true

log "Resultado"
cat "$DIAG/run.env"
cat "$DIAG/wrf-runtime.env" 2>/dev/null || true
cat "$DIAG/wrfout-files.txt" 2>/dev/null || true
free -h
df -h
