#!/usr/bin/env python3
"""Run the WRF SCM columns and generate Skew-T PNG + machine-readable profiles."""
import argparse, datetime as dt, gc, importlib.util, json, math, os, shutil, subprocess, time
from pathlib import Path
import numpy as np
from netCDF4 import Dataset

ROOT=Path(__file__).resolve().parents[2]
RENDERER=ROOT/"tools"/"skewt"/"native_spc_render.py"
KAPPA=.2854
RD=287.05
G=9.80665
MS_TO_KT=1.943844

def run_logged(command,cwd,log_path,timeout):
    env=os.environ.copy()
    env.update({"OMP_NUM_THREADS":"1","OPENBLAS_NUM_THREADS":"1","MKL_NUM_THREADS":"1"})
    start=time.monotonic()
    print(f"[SCM] running {command[-1]} for {cwd.name}",flush=True)
    with log_path.open("w",encoding="utf-8",errors="replace") as log:
        try:
            subprocess.run(command,cwd=cwd,env=env,stdout=log,stderr=subprocess.STDOUT,check=True,timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"{cwd.name}: timeout; log: {log_path}") from exc
        except subprocess.CalledProcessError as exc:
            tail="\n".join(log_path.read_text(encoding="utf-8",errors="replace").splitlines()[-100:])
            raise RuntimeError(f"{cwd.name}: command failed ({exc.returncode}).\n{tail}") from exc
    print(f"[SCM] {cwd.name}: {time.monotonic()-start:.1f}s",flush=True)

def finite(x):
    try:
        x=float(x)
        return x if math.isfinite(x) else None
    except (TypeError,ValueError):
        return None

def read_var(ds,key,ti):
    if key not in ds.variables: raise RuntimeError(f"WRF output missing variable {key}")
    x=ds.variables[key][ti]
    if np.ma.isMaskedArray(x): x=x.filled(np.nan)
    return np.asarray(x,dtype=float)

def time_text(ds,ti):
    if "Times" not in ds.variables: return None
    return "".join(x.decode("ascii") if isinstance(x,bytes) else str(x) for x in ds.variables["Times"][ti]).strip()

def parse_time(text):
    try: return dt.datetime.strptime(text,"%Y-%m-%d_%H:%M:%S") if text else None
    except ValueError: return None

def dewpoint_from_q(q,p):
    q=max(1.0e-8,float(q))
    e=min(p*q/(.622+q),p*.98)
    loge=math.log(max(e,1.0e-5)/6.112)
    return 243.5*loge/(17.67-loge)

def make_sounding(ds,ti,city,valid):
    p_pa=read_var(ds,"P",ti)[:,1,1]+read_var(ds,"PB",ti)[:,1,1]
    theta=read_var(ds,"T",ti)[:,1,1]+300.
    qv=read_var(ds,"QVAPOR",ti)[:,1,1]
    ph=read_var(ds,"PH",ti)[:,1,1]+read_var(ds,"PHB",ti)[:,1,1]
    z=.5*(ph[:-1]+ph[1:])/G
    us=read_var(ds,"U",ti)[:,1,:]
    vs=read_var(ds,"V",ti)[:,:,1]
    u=.5*(us[:,1]+us[:,2])
    v=.5*(vs[:,1]+vs[:,2])
    ws=read_var(ds,"W",ti)[:,1,1]
    w=.5*(ws[:-1]+ws[1:])
    p=p_pa/100.
    tk=theta*(p_pa/100000.)**KAPPA
    tc=tk-273.15
    td=np.asarray([dewpoint_from_q(q,pp) for q,pp in zip(qv,p)])
    density=p_pa/(RD*np.maximum(tk,150.))
    omega=-w*density*G
    ps=float(read_var(ds,"PSFC",ti)[1,1])/100.
    ground=float(read_var(ds,"HGT",ti)[1,1])
    t2=float(read_var(ds,"T2",ti)[1,1])
    q2=float(read_var(ds,"Q2",ti)[1,1])
    u10=float(read_var(ds,"U10",ti)[1,1])*MS_TO_KT
    v10=float(read_var(ds,"V10",ti)[1,1])*MS_TO_KT
    rows=[{"pressure_hpa":ps,"height_m":ground,"temperature_c":t2-273.15,
           "dewpoint_c":dewpoint_from_q(q2,ps),"u_kt":u10,"v_kt":v10,"omega_pa_s":None}]
    for pp,zz,tt,dw,uu,vv,om in zip(p,z,tc,td,u*MS_TO_KT,v*MS_TO_KT,omega):
        if not all(math.isfinite(x) for x in (pp,zz,tt,dw,uu,vv)): continue
        if pp>=ps-1. or zz<=ground+5: continue
        rows.append({"pressure_hpa":float(pp),"height_m":float(zz),"temperature_c":float(tt),
          "dewpoint_c":float(dw),"u_kt":float(uu),"v_kt":float(vv),
          "omega_pa_s":float(om) if math.isfinite(om) else None})
    rows.sort(key=lambda r:r["pressure_hpa"],reverse=True)
    clean=[]
    for row in rows:
        if clean and abs(row["pressure_hpa"]-clean[-1]["pressure_hpa"])<.05: continue
        clean.append(row)
    if len(clean)<10: raise RuntimeError(f"{city['name']}: only {len(clean)} usable WRF vertical levels")
    from sharppy.sharptab import profile as shp_profile
    pres=np.asarray([r["pressure_hpa"] for r in clean])
    hght=np.asarray([r["height_m"] for r in clean])
    temp=np.asarray([r["temperature_c"] for r in clean])
    dew=np.asarray([r["dewpoint_c"] for r in clean])
    uu=np.asarray([r["u_kt"] for r in clean])
    vv=np.asarray([r["v_kt"] for r in clean])
    om=np.asarray([np.nan if r["omega_pa_s"] is None else r["omega_pa_s"] for r in clean])
    sounding=shp_profile.create_profile(profile="convective",pres=pres,hght=hght,tmpc=temp,dwpc=dew,
        u=uu,v=vv,omeg=np.ma.masked_invalid(om),strictQC=False,
        latitude=city["latitude"],date=valid,location=city["name"])
    return sounding,clean,ground

def load_renderer():
    spec=importlib.util.spec_from_file_location("sideral_native_spc_render",RENDERER)
    if spec is None or spec.loader is None: raise RuntimeError(f"Could not load renderer: {RENDERER}")
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--work-root",required=True); ap.add_argument("--out-root",required=True)
    ap.add_argument("--mode",choices=["pilot","full"],required=True)
    ap.add_argument("--ideal-timeout",type=int,default=600); ap.add_argument("--wrf-timeout",type=int,default=1800)
    args=ap.parse_args()
    root=Path(args.work_root).resolve()
    out=Path(args.out_root).resolve()
    out.mkdir(parents=True,exist_ok=True)
    source=json.loads((root/"manifest.json").read_text(encoding="utf-8"))
    run=dt.datetime.strptime(source["run_utc"],"%Y-%m-%d %H:%MZ")
    renderer=load_renderer()
    manifest={"schema":"sideral-wrf-scm-skewt-publication-v1","model":"WRF-ARW SCM","mode":args.mode,
      "nominal_grid_spacing_km":3,"horizontal_stencil":"3x3 periodic; no resolved horizontal gradients",
      "initialization_and_forcing":source["initialization_and_forcing"],"run_utc":source["run_utc"],
      "cycle":source["cycle"],"forecast_hours":list(range(0,49,3)),"cities":[],"assets":[]}
    for city in source["cities"]:
        case=root/city["slug"]
        for pattern in ("wrfout_d01_*","wrfrst_d01_*","wrfinput_d01","rsl.out.*","rsl.error.*"):
            for old in case.glob(pattern): old.unlink(missing_ok=True)
        ideal=case/"ideal.exe"; wrf=case/"wrf.exe"
        if not ideal.exists() or not wrf.exists(): raise RuntimeError(f"{city['name']}: SCM binary missing")
        run_logged([str(ideal)],case,case/"ideal.log",args.ideal_timeout)
        if not (case/"wrfinput_d01").is_file(): raise RuntimeError(f"{city['name']}: ideal.exe did not create wrfinput_d01")
        run_logged([str(wrf)],case,case/"wrf.log",args.wrf_timeout)
        outputs=sorted(p for p in case.glob("wrfout_d01_*") if p.is_file() and p.stat().st_size>0)
        if not outputs: raise RuntimeError(f"{city['name']}: wrf.exe did not create wrfout")
        frames={}
        for path in outputs:
            with Dataset(path) as ds:
                for ti in range(len(ds.dimensions["Time"])):
                    valid=parse_time(time_text(ds,ti))
                    if valid is None: continue
                    hrs=(valid-run).total_seconds()/3600.
                    fh=int(round(hrs))
                    if abs(hrs-fh)>.02 or fh not in range(0,49,3) or fh in frames: continue
                    frames[fh]=(*make_sounding(ds,ti,city,valid),valid)
        if 0 not in frames:
            with Dataset(case/"wrfinput_d01") as ds:
                valid=parse_time(time_text(ds,0)) or run
                frames[0]=(*make_sounding(ds,0,city,valid),valid)
        missing=sorted(set(range(0,49,3))-set(frames))
        if missing: raise RuntimeError(f"{city['name']}: WRF SCM output missing forecast hours {missing}")
        city_out=out/city["slug"]
        city_out.mkdir(parents=True,exist_ok=True)
        manifest["cities"].append(city)
        for fh in range(0,49,3):
            sounding,rows,ground,valid=frames[fh]
            meta={"location":city["name"],"station":city["name"],"latitude":city["latitude"],
                  "longitude":city["longitude"],"elevation":ground,"fh":fh,"valid":valid.strftime("%Y-%m-%d %HZ")}
            tempdir=city_out/f".tmp-f{fh:03d}"
            tempdir.mkdir(parents=True,exist_ok=True)
            png=renderer.render_native_spc(sounding,tempdir,meta)
            png_out=city_out/f"{city['slug']}-f{fh:03d}.png"
            shutil.copy2(png,png_out)
            level_data=[]
            for r in rows:
                u,v=r["u_kt"],r["v_kt"]
                level_data.append({"pressure_hpa":finite(r["pressure_hpa"]),"height_m":finite(r["height_m"]),
                    "temperature_c":finite(r["temperature_c"]),"dewpoint_c":finite(r["dewpoint_c"]),
                    "wind_u_kt":finite(u),"wind_v_kt":finite(v),"wind_speed_kt":math.hypot(u,v),
                    "wind_direction_deg":(math.degrees(math.atan2(-u,-v))+360.)%360.,
                    "omega_pa_s":finite(r["omega_pa_s"])})
            data={"schema":"sideral-wrf-scm-skewt-v1","model":"WRF-ARW SCM",
              "nominal_grid_spacing_km":3,"horizontal_stencil":"3x3 periodic; no resolved horizontal gradients",
              "initialization_and_forcing":source["initialization_and_forcing"],"location":city["name"],
              "latitude":city["latitude"],"longitude":city["longitude"],"elevation_m":ground,
              "run_utc":run.strftime("%Y-%m-%d %H:%MZ"),"forecast_hour":fh,
              "valid_utc":valid.strftime("%Y-%m-%d %H:%MZ"),"renderer":"SHARPpy","profile":level_data}
            json_out=city_out/f"{city['slug']}-f{fh:03d}.json"
            json_out.write_text(json.dumps(data,ensure_ascii=False,allow_nan=False,separators=(",",":")),encoding="utf-8")
            for f in tempdir.iterdir(): f.unlink(missing_ok=True)
            tempdir.rmdir()
            manifest["assets"].extend([png_out.name,json_out.name])
            print(f"[SCM Skew-T] {city['name']} F{fh:03d}",flush=True)
            gc.collect()
        print(f"[SCM] {city['name']}: {len(outputs)} wrfout(s); "
              f"{sum(p.stat().st_size for p in outputs)/1024**2:.1f} MiB",flush=True)
    expected=len(manifest["cities"])*17*2
    if len(manifest["assets"])!=expected: raise RuntimeError(f"Expected {expected} assets; got {len(manifest['assets'])}")
    (out/"manifest.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding="utf-8")
    print(f"[SCM Skew-T] OK: {len(manifest['cities'])} capitals; {expected//2} PNG + {expected//2} JSON",flush=True)

if __name__=="__main__": main()
