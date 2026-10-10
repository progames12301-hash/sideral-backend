#!/usr/bin/env python3
"""Prepare ECMWF-forced 3x3 WRF-SCM columns for Sideral city Skew-Ts."""
import argparse, datetime as dt, json, math, re, shutil, subprocess, time
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import numpy as np
from netCDF4 import Dataset

API="https://single-runs-api.open-meteo.com/v1/forecast"
HOURS=list(range(0,49,3))
LEVELS=[1000,925,850,700,600,500,400,300,250,200,150,100,50]
ZFORCE=np.asarray([0,500,1000,2000,4000,7000,10000,14000],dtype=float)
ZTOP=14500.0
CAPITALS=[
("Rio Branco","rio-branco",-9.9754,-67.8249),("Maceió","maceio",-9.6658,-35.7353),
("Macapá","macapa",0.0349,-51.0694),("Manaus","manaus",-3.1190,-60.0217),
("Salvador","salvador",-12.9777,-38.5016),("Fortaleza","fortaleza",-3.7319,-38.5267),
("Brasília","brasilia",-15.7939,-47.8828),("Vitória","vitoria",-20.3155,-40.3128),
("Goiânia","goiania",-16.6869,-49.2648),("São Luís","sao-luis",-2.5307,-44.3068),
("Cuiabá","cuiaba",-15.6014,-56.0979),("Campo Grande","campo-grande",-20.4697,-54.6201),
("Belo Horizonte","belo-horizonte",-19.9167,-43.9345),("Belém","belem",-1.4558,-48.5039),
("João Pessoa","joao-pessoa",-7.1195,-34.8450),("Curitiba","curitiba",-25.4284,-49.2733),
("Recife","recife",-8.0476,-34.8770),("Teresina","teresina",-5.0892,-42.8019),
("Rio de Janeiro","rio-de-janeiro",-22.9068,-43.1729),("Natal","natal",-5.7945,-35.2110),
("Porto Alegre","porto-alegre",-30.0346,-51.2177),("Porto Velho","porto-velho",-8.7608,-63.8999),
("Boa Vista","boa-vista",2.8235,-60.6758),("Florianópolis","florianopolis",-27.5949,-48.5482),
("São Paulo","sao-paulo",-23.5505,-46.6333),("Aracaju","aracaju",-10.9472,-37.0731),
("Palmas","palmas",-10.1840,-48.3336)]

def finite(x):
    try:
        y=float(x)
        return y if math.isfinite(y) else None
    except (TypeError,ValueError):
        return None

def run_time(cycle):
    now=dt.datetime.now(dt.timezone.utc).replace(minute=0,second=0,microsecond=0)
    run=now.replace(hour=int(cycle))
    if run>now: run-=dt.timedelta(days=1)
    return run.replace(tzinfo=None)

def get_json(params):
    url=API+"?"+urlencode(params)
    last=None
    for attempt in range(5):
        try:
            req=Request(url,headers={"User-Agent":"Sideral-WRF-SCM-SkewT","Accept":"application/json"})
            with urlopen(req,timeout=90) as r: data=json.loads(r.read().decode("utf-8"))
            if isinstance(data,dict) and data.get("error"): raise RuntimeError(data.get("reason","API error"))
            return data
        except Exception as e:
            last=e
            if attempt<4: time.sleep(10*(attempt+1))
    raise RuntimeError(f"Open-Meteo Single Runs falhou: {last}")

def val(obj,key,i):
    seq=(obj.get("hourly") or {}).get(key)
    if not isinstance(seq,list) or i>=len(seq): return None
    return finite(seq[i])

def name(prefix,lev=None): return f"{prefix}_{lev}hPa" if lev is not None else prefix

def dewpoint_rh(t,rh):
    a,b=17.625,243.04
    g=math.log(max(.1,min(100.,rh))/100.)+a*t/(b+t)
    return b*g/(a-g)

def q_from_td(td,p):
    e=min(6.112*math.exp(17.67*td/(td+243.5)),p*.98)
    return .622*e/max(p-e,.1)

def uv(speed_ms,direction):
    r=math.radians(direction)
    return -speed_ms*math.sin(r),-speed_ms*math.cos(r)

def profile(obj,i,city):
    terrain=finite(obj.get("elevation")) or 0.
    sp,t2,td2,ws,wd=[val(obj,k,i) for k in ("surface_pressure","temperature_2m","dew_point_2m","wind_speed_10m","wind_direction_10m")]
    if None in (sp,t2,td2,ws,wd): raise RuntimeError(f"{city[0]}: dados de superfície ausentes")
    u,v=uv(ws*.514444,wd)
    rows=[{"p":sp,"z":terrain,"t":t2,"td":td2,"u":u,"v":v,"omega":0.}]
    for lev in LEVELS:
        p=float(lev)
        if p>=sp-1: continue
        t=val(obj,name("temperature",lev),i); td=val(obj,name("dew_point",lev),i)
        if td is None and t is not None:
            rh=val(obj,name("relative_humidity",lev),i)
            if rh is not None: td=dewpoint_rh(t,rh)
        ws,wd,z,omega=[val(obj,name(k,lev),i) for k in ("wind_speed","wind_direction","geopotential_height","vertical_velocity")]
        if None in (t,td,ws,wd,z) or z<=terrain+10: continue
        u,v=uv(ws*.514444,wd)
        rows.append({"p":p,"z":z,"t":t,"td":td,"u":u,"v":v,"omega":0. if omega is None else omega})
    rows.sort(key=lambda r:r["z"])
    clean=[]
    for r in rows:
        if clean and r["z"]<=clean[-1]["z"]+5: continue
        clean.append(r)
    if len(clean)<8: raise RuntimeError(f"{city[0]}: apenas {len(clean)} níveis")
    if clean[-1]["z"]<ZTOP+200: raise RuntimeError(f"{city[0]}: topo do perfil abaixo de {ZTOP+200:.0f} m")
    return clean

def interp(rows,key):
    z=np.array([r["z"] for r in rows],float); y=np.array([r[key] for r in rows],float)
    ok=np.isfinite(z)&np.isfinite(y)
    if ok.sum()<2: raise RuntimeError(f"Não há níveis suficientes para {key}")
    order=np.argsort(z[ok])
    return np.interp(ZFORCE,z[ok][order],y[ok][order])

def force(rows):
    p=np.array([r["p"] for r in rows],float)
    tk=np.array([r["t"]+273.15 for r in rows],float)
    theta=tk*(1000./p)**.2854
    q=np.array([q_from_td(r["td"],pp) for r,pp in zip(rows,p)])
    ext=[]
    for r,th,qv,pp,temp in zip(rows,theta,q,p,tk):
        rho=pp*100./(287.05*temp)
        ext.append({**r,"theta":th,"q":qv,"w":-float(r["omega"] or 0.)/(rho*9.80665)})
    return {"Z_FORCE":ZFORCE.copy(),"U_G":interp(rows,"u"),"V_G":interp(rows,"v"),
            "W_SUBS":interp(ext,"w"),"TH_LARGESCALE":interp(ext,"theta"),
            "QV_LARGESCALE":interp(ext,"q"),"U_LARGESCALE":interp(rows,"u"),
            "V_LARGESCALE":interp(rows,"v")}

def patch_cdl(source,run,city):
    c=source.read_text(encoding="utf-8")
    fields=[
    ("TH_LARGESCALE","large-scale potential temperature","K"),
    ("TH_LARGESCALE_TEND","large-scale potential temperature tendency","K s-1"),
    ("QV_LARGESCALE","large-scale water vapor mixing ratio","kg kg-1"),
    ("QV_LARGESCALE_TEND","large-scale water vapor tendency","kg kg-1 s-1"),
    ("U_LARGESCALE","large-scale U wind","m s-1"),("U_LARGESCALE_TEND","large-scale U wind tendency","m s-2"),
    ("V_LARGESCALE","large-scale V wind","m s-1"),("V_LARGESCALE_TEND","large-scale V wind tendency","m s-2"),
    ("TAU_LARGESCALE","large-scale relaxation timescale","s"),("TAU_LARGESCALE_TEND","large-scale timescale tendency","s s-1"),
    ("TH_T_TEND","potential temperature tendency","K s-1"),("QV_T_TEND","water vapor tendency","kg kg-1 s-1")]
    if "TH_LARGESCALE" not in c:
        defs=[]
        for n,d,u in fields:
            defs.append(f'\tfloat {n}(Time, force_layers) ;\n\t\t{n}:FieldType = 104 ;\n\t\t{n}:MemoryOrder = "Z  " ;\n\t\t{n}:description = "{d}" ;\n\t\t{n}:units = "{u}" ;\n\t\t{n}:stagger = "" ;\n\t\t{n}:_FillValue = -999.f ;')
        c=c.replace("// global attributes:","\n".join(defs)+"\n\n// global attributes:",1)
    attrs={"START_DATE":f'"{run:%Y-%m-%d_%H:%M:%S}"',"SIMULATION_START_DATE":f'"{run:%Y-%m-%d_%H:%M:%S}"',
    "DX":"3000.f","DY":"3000.f","DT":"18.f","CEN_LAT":f"{city[2]:.4f}f","CEN_LON":f"{city[3]:.4f}f",
    "MOAD_CEN_LAT":f"{city[2]:.4f}f","STAND_LON":f"{city[3]:.4f}f","JULYR":str(run.year),"JULDAY":str(run.timetuple().tm_yday)}
    for k,v in attrs.items():
        c,n=re.subn(rf"(:{k}\s*=\s*)[^;]+;",lambda m:m.group(1)+v+";",c,count=1)
        if n!=1: raise RuntimeError(f"CDL attribute missing: {k}")
    return c

def group(text,section,settings):
    m=re.search(rf"(?ms)^([ \t]*&{re.escape(section)}\b)(.*?)(^[ \t]*/[ \t]*$)",text)
    if not m: raise RuntimeError(f"Namelist &{section} missing")
    body=m.group(2)
    for k,v in settings.items():
        rx=re.compile(rf"(?im)^([ \t]*{re.escape(k)}[ \t]*=[ \t]*)[^,\n]*(,?)[ \t]*(?:!.*)?$")
        body,n=rx.subn(lambda a:a.group(1)+v+",",body,count=1)
        if not n: body=body.rstrip()+f"\n {k} = {v},\n"
    return text[:m.start()]+m.group(1)+body+m.group(3)+text[m.end():]

def namelist(base,run,city):
    end=run+dt.timedelta(hours=48)
    base=group(base,"time_control",{"run_days":"0","run_hours":"48","run_minutes":"0","run_seconds":"0",
    "start_year":str(run.year),"start_month":f"{run.month:02d}","start_day":f"{run.day:02d}","start_hour":f"{run.hour:02d}",
    "start_minute":"00","start_second":"00","end_year":str(end.year),"end_month":f"{end.month:02d}","end_day":f"{end.day:02d}",
    "end_hour":f"{end.hour:02d}","end_minute":"00","end_second":"00","history_interval":"180","frames_per_outfile":"1000",
    "restart":".false.","auxinput3_inname":'"force_ideal.nc"',"auxinput3_interval_h":"3","io_form_auxinput3":"2"})
    base=group(base,"domains",{"max_dom":"1","e_we":"3","e_sn":"3","e_vert":"60","dx":"3000","dy":"3000","ztop":str(ZTOP),"time_step":"18"})
    base=group(base,"scm",{"scm_force":"1","scm_force_dx":"10000","num_force_layers":"8",
    "scm_lat":f"{city[2]:.4f}","scm_lon":f"{city[3]:.4f}","scm_th_adv":".false.","scm_wind_adv":".false.",
    "scm_qv_adv":".false.","scm_vert_adv":".true.","scm_th_t_tend":".false.","scm_qv_t_tend":".false.",
    "scm_force_th_largescale":".true.","scm_force_qv_largescale":".true.","scm_force_wind_largescale":".true.",
    "scm_soilT_force":".false.","scm_soilq_force":".false.","scm_force_skintemp":"0","scm_force_flux":"0"})
    return group(base,"dynamics",{"pert_coriolis":".true."})

def prepare_runtime(wrf,case):
    for f in (wrf/"run").iterdir():
        if f.name in {"namelist.input","input_sounding","input_soil","force_ideal.nc","wrfinput_d01","wrfbdy_d01"} or f.name.startswith("wrfout_d01_"): continue
        dst=case/f.name
        if not dst.exists() and not dst.is_symlink(): dst.symlink_to(f.resolve(),target_is_directory=f.is_dir())
    for exe in ("ideal.exe","wrf.exe"):
        src=wrf/"test"/"em_scm_xy"/exe
        if not src.is_file(): src=wrf/"main"/exe
        if not src.is_file(): raise RuntimeError(f"Missing WRF SCM binary {exe}")
        dst=case/exe
        if dst.exists() or dst.is_symlink(): dst.unlink()
        dst.symlink_to(src.resolve())

def create_force(case,template,run,city,records):
    cdl=case/"forcing_file.cdl"; cdl.write_text(patch_cdl(template,run,city),encoding="utf-8")
    target=case/"force_ideal.nc"; subprocess.run(["ncgen","-o",str(target),str(cdl)],check=True)
    bases=["Z_FORCE","U_G","V_G","W_SUBS","TH_LARGESCALE","QV_LARGESCALE","U_LARGESCALE","V_LARGESCALE"]
    tend={f"{n}_TEND":n for n in bases if n!="Z_FORCE"}; tend["Z_FORCE_TEND"]="Z_FORCE"
    zero={"TH_UPSTREAM_X","TH_UPSTREAM_Y","QV_UPSTREAM_X","QV_UPSTREAM_Y","U_UPSTREAM_X","U_UPSTREAM_Y","V_UPSTREAM_X","V_UPSTREAM_Y"}
    with Dataset(target,"r+") as ds:
        for n,var in ds.variables.items():
            if n=="Times": continue
            for i,row in enumerate(records):
                if n in bases: a=row[n]
                elif n in tend:
                    key=tend[n]; a=np.zeros(8) if key=="Z_FORCE" or i==len(records)-1 else (records[i+1][key]-row[key])/10800.
                elif n in {"TAU_X","TAU_Y","TAU_LARGESCALE"}: a=np.full(8,10800.)
                elif n.endswith("_TEND") or n in zero: a=np.zeros(8)
                else: continue
                var[i,:]=np.asarray(a,dtype=np.float32)
        for i,h in enumerate(HOURS):
            text=(run+dt.timedelta(hours=h)).strftime("%Y-%m-%d_%H:%M:%S")
            ds.variables["Times"][i,:]=np.frombuffer(text.encode("ascii"),dtype="S1")

def prepare_one(city,obj,times,run,work,wrf,cdl):
    label,slug,lat,lon=city; case=work/slug; case.mkdir(parents=True,exist_ok=True); prepare_runtime(wrf,case)
    parsed=[dt.datetime.fromisoformat(t.replace("Z","+00:00")).replace(tzinfo=None) for t in times]
    records=[]; first=None; terrain=finite(obj.get("elevation")) or 0.
    for h in HOURS:
        valid=run+dt.timedelta(hours=h)
        if valid not in parsed: raise RuntimeError(f"{label}: ECMWF missing F{h:03d}")
        rows=profile(obj,parsed.index(valid),city); records.append(force(rows))
        if h==0: first=rows
    s=first[0]
    # input_sounding's surface record is actual 2-m temperature, not potential temperature.
    lines=[f"{terrain:.3f} {s['u']:.6f} {s['v']:.6f} {s['t']+273.15:.6f} {q_from_td(s['td'],s['p']):.9f} {s['p']*100:.2f}"]
    for r in first[1:]:
        th=(r["t"]+273.15)*(1000./r["p"])**.2854
        lines.append(f"{r['z']:.2f} {r['u']:.6f} {r['v']:.6f} {th:.6f} {q_from_td(r['td'],r['p']):.9f}")
    (case/"input_sounding").write_text("\n".join(lines)+"\n",encoding="ascii")
    skin=s["t"]+273.15
    soil=[f"0.0000000 {skin:.4f} {skin:.4f}"]+[f"{z:.7f} {skin:.4f} 0.2500000" for z in (.05,.25,.70,1.50)]
    (case/"input_soil").write_text("\n".join(soil)+"\n",encoding="ascii")
    create_force(case,cdl,run,city,records)
    src=(wrf/"test"/"em_scm_xy"/"namelist.input").read_text(encoding="utf-8")
    (case/"namelist.input").write_text(namelist(src,run,city),encoding="utf-8")
    print(f"[SCM prepared] {label}: {len(first)} levels, 17 forcing times",flush=True)
    return {"name":label,"slug":slug,"latitude":lat,"longitude":lon,"elevation_m":terrain,
    "nominal_grid_spacing_km":3,"horizontal_stencil":"3x3 periodic; no resolved horizontal gradients"}

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--cycle",choices=["06","12","18"],required=True)
    ap.add_argument("--mode",choices=["pilot","full"],required=True)
    ap.add_argument("--wrf-root",required=True); ap.add_argument("--work-root",required=True)
    a=ap.parse_args(); wrf=Path(a.wrf_root).resolve(); work=Path(a.work_root).resolve()
    template=wrf/"test"/"em_scm_xy"/"forcing_file.cdl"
    if not template.is_file(): raise SystemExit(f"Missing WRF SCM template: {template}")
    if shutil.which("ncgen") is None: raise SystemExit("ncgen missing (netcdf-bin)")
    run=run_time(a.cycle); selected=CAPITALS if a.mode=="full" else [c for c in CAPITALS if c[1]=="brasilia"]
    variables=["surface_pressure","temperature_2m","dew_point_2m","wind_speed_10m","wind_direction_10m"]
    for lev in LEVELS:
        variables += [name("temperature",lev),name("dew_point",lev),name("relative_humidity",lev),
          name("wind_speed",lev),name("wind_direction",lev),name("geopotential_height",lev),name("vertical_velocity",lev)]
    params={"latitude":",".join(str(c[2]) for c in selected),"longitude":",".join(str(c[3]) for c in selected),
      "hourly":",".join(variables),"models":"ecmwf_ifs025","run":run.strftime("%Y-%m-%dT%H:%M"),
      "forecast_hours":"49","wind_speed_unit":"kn","temperature_unit":"celsius","timeformat":"iso8601",
      "timezone":"UTC","cell_selection":"nearest"}
    print(f"[WRF SCM] ECMWF IFS {run:%Y-%m-%d %HZ}; {a.mode}; {len(selected)} city/cities",flush=True)
    payload=get_json(params); objects=[payload] if isinstance(payload,dict) else payload if isinstance(payload,list) else None
    if objects is None or len(objects)!=len(selected): raise RuntimeError(f"Expected {len(selected)} locations; API returned {len(objects) if objects is not None else 'invalid payload'}")
    times=(objects[0].get("hourly") or {}).get("time") or []
    work.mkdir(parents=True,exist_ok=True)
    manifest={"schema":"sideral-wrf-scm-skewt-input-v1","mode":a.mode,"model":"WRF-ARW SCM",
    "nominal_grid_spacing_km":3,"horizontal_stencil":"3x3 periodic; no resolved horizontal gradients",
    "initialization_and_forcing":"ECMWF IFS 0.25° via Open-Meteo Single Runs",
    "run_utc":run.strftime("%Y-%m-%d %H:%MZ"),"cycle":a.cycle,"forecast_hours":HOURS,"cities":[]}
    for city,obj in zip(selected,objects): manifest["cities"].append(prepare_one(city,obj,times,run,work,wrf,template))
    (work/"manifest.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding="utf-8")
    print(f"[WRF SCM] prepared {len(selected)} case(s)",flush=True)
if __name__=="__main__": main()
