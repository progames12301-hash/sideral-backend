#!/usr/bin/env python3
from __future__ import annotations
import argparse, datetime as dt, gzip, json, re, sys
from pathlib import Path
import numpy as np
import xarray as xr
from extract_wrf_severe_core import compute as compute_severe

EXPECTED_NX=215; EXPECTED_NY=215; EXPECTED_DX=21000; EXPECTED_DY=21000; EXPECTED_FRAMES=49
LEVELS_HPA=(925,850,700,500,300,200)

def valid_time(path:Path)->dt.datetime:
    m=re.search(r"wrfout_d01_(\d{4}-\d{2}-\d{2})_(\d{2})[:_-](\d{2})[:_-](\d{2})",path.name)
    if not m: raise RuntimeError(f"Nome wrfout inesperado: {path.name}")
    return dt.datetime.strptime(f"{m[1]} {m[2]}:{m[3]}:{m[4]}","%Y-%m-%d %H:%M:%S").replace(tzinfo=dt.timezone.utc)

def arr(ds:xr.Dataset,name:str):
    if name not in ds: return None
    return np.asarray(ds[name].isel(Time=0).to_numpy(),dtype=float)

def s2(a,r,c): return np.asarray(a)[np.ix_(r,c)]
def flat(a,decimals=2):
    x=np.asarray(a,dtype=float); y=np.round(x,decimals).reshape(-1)
    return [float(v) if np.isfinite(v) else None for v in y]

def interp_pressure(p,value,target_pa):
    p=np.asarray(p,float); value=np.asarray(value,float); out=np.full(p.shape[1:],np.nan)
    for k in range(p.shape[0]-1):
        p0,p1,v0,v1=p[k],p[k+1],value[k],value[k+1]
        ok=np.isfinite(p0)&np.isfinite(p1)&np.isfinite(v0)&np.isfinite(v1)&((target_pa-p0)*(target_pa-p1)<=0)
        den=p1-p0; f=np.divide(target_pa-p0,den,out=np.full_like(p0,np.nan),where=np.abs(den)>1e-9)
        out=np.where(ok&~np.isfinite(out),v0+f*(v1-v0),out)
    return out

def dewpoint_from_q(q,p_pa):
    q=np.clip(np.asarray(q,float),1e-9,.08); p=np.asarray(p_pa,float); e=np.clip(q*p/(.622+q),1,p*.99)
    l=np.log(e/611.2); return 243.5*l/(17.67-l)

def rh_from_q(q,p_pa,temp_k):
    q=np.clip(np.asarray(q,float),1e-9,.08); p=np.asarray(p_pa,float); tc=np.asarray(temp_k,float)-273.15
    es=611.2*np.exp(17.67*tc/(tc+243.5)); e=q*p/(.622+q); return np.clip(100*e/np.maximum(es,1),0,100)

def theta_e(temp_k,q,p_pa):
    t=np.asarray(temp_k,float); q=np.clip(np.asarray(q,float),1e-9,.08); p=np.asarray(p_pa,float)
    td=np.clip(dewpoint_from_q(q,p),170,t); tl=1/(1/(td-56)+np.log(t/td)/800)+56
    th=t*(100000/p)**(.2854*(1-.28*q)); return th*np.exp((3376/tl-2.54)*q*(1+.81*q))

def wind_dir(u,v): return (270-np.degrees(np.arctan2(v,u)))%360

def cloud_field(ds):
    c=arr(ds,"CLDFRA")
    if c is None:return None
    if c.ndim==3:return np.clip(np.nanmax(c,axis=0)*100,0,100)
    if c.ndim==2:return np.clip(c*100,0,100)
    return None

def slp_field(ds):
    for name in ("slp","SLP","PMSL","MSLP"):
        if name in ds:
            x=arr(ds,name)
            while x is not None and x.ndim>2:x=x[0]
            if x is not None:
                finite=x[np.isfinite(x)]
                if finite.size and float(np.nanmedian(finite))>2000:x=x/100
                return x
    raise RuntimeError("SLP/MSLP ausente: o Brasil 21 km nao pode publicar pressao/isobaras reais")

def grid_check(ds,path):
    lat,lon=arr(ds,"XLAT"),arr(ds,"XLONG")
    if lat is None or lon is None:raise RuntimeError(f"XLAT/XLONG ausentes em {path.name}")
    if lat.shape!=(EXPECTED_NY,EXPECTED_NX) or lon.shape!=(EXPECTED_NY,EXPECTED_NX):raise RuntimeError(f"Grade inesperada em {path.name}: {lat.shape}/{lon.shape}")
    dx=int(round(float(ds.attrs.get("DX",0)))); dy=int(round(float(ds.attrs.get("DY",0))))
    if (dx,dy)!=(EXPECTED_DX,EXPECTED_DY):raise RuntimeError(f"DX/DY inesperados em {path.name}: {dx}/{dy} m")
    return lat,lon

def pressure_level_fields(ds,rows,cols,dx,dy):
    required=("P","PB","T","QVAPOR","PH","PHB","U","V"); missing=[x for x in required if x not in ds]
    if missing:raise RuntimeError("Variaveis verticais ausentes: "+", ".join(missing))
    p=np.asarray(ds["P"].isel(Time=0).to_numpy(),float)+np.asarray(ds["PB"].isel(Time=0).to_numpy(),float)
    t=np.asarray(ds["T"].isel(Time=0).to_numpy(),float)+300; q=np.asarray(ds["QVAPOR"].isel(Time=0).to_numpy(),float)
    ph=np.asarray(ds["PH"].isel(Time=0).to_numpy(),float)+np.asarray(ds["PHB"].isel(Time=0).to_numpy(),float); z=.5*(ph[:-1]+ph[1:])/9.80665
    ur=np.asarray(ds["U"].isel(Time=0).to_numpy(),float); vr=np.asarray(ds["V"].isel(Time=0).to_numpy(),float)
    u=.5*(ur[:,:,:-1]+ur[:,:,1:]); v=.5*(vr[:,:-1,:]+vr[:,1:,:])
    w=None
    if "W" in ds:
        wr=np.asarray(ds["W"].isel(Time=0).to_numpy(),float); w=.5*(wr[:-1]+wr[1:])
    result={"levels":{},"available":[]}
    for hpa in LEVELS_HPA:
        target=hpa*100.; temp=interp_pressure(p,t,target); ql=interp_pressure(p,q,target); uu=interp_pressure(p,u,target); vv=interp_pressure(p,v,target); zz=interp_pressure(p,z,target)
        rh=rh_from_q(ql,np.full_like(temp,target),temp); td=dewpoint_from_q(ql,np.full_like(temp,target))-273.15; the=theta_e(temp,ql,np.full_like(temp,target))
        vort=np.gradient(vv,dx,axis=1)-np.gradient(uu,dy,axis=0)
        temp_adv=-(uu*np.gradient(temp-273.15,dx,axis=1)+vv*np.gradient(temp-273.15,dy,axis=0))*3600
        omega=None
        if w is not None:
            ww=interp_pressure(p,w,target); rho=target/(287.05*(temp*(1+.61*ql))); omega=-rho*9.80665*ww
        result["levels"][str(hpa)]={
            "geopotentialHeight":flat(s2(zz,rows,cols),0),"windU":flat(s2(uu,rows,cols),1),"windV":flat(s2(vv,rows,cols),1),
            "windSpeed":flat(s2(np.hypot(uu,vv),rows,cols),1),"windDirection":flat(s2(wind_dir(uu,vv),rows,cols),0),
            "temperature":flat(s2(temp-273.15,rows,cols),1),"humidity":flat(s2(rh,rows,cols),0),"dewpoint":flat(s2(td,rows,cols),1),
            "thetaE":flat(s2(the,rows,cols),1),"vorticity":flat(s2(vort,rows,cols),7),"temperatureAdvection":flat(s2(temp_adv,rows,cols),3),
            "omega":flat(s2(omega,rows,cols),3) if omega is not None else None}
        result["available"].append(hpa)
    return result

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--run-dir",required=True); ap.add_argument("--output-dir",required=True); ap.add_argument("--grid-x",type=int,default=215); ap.add_argument("--grid-y",type=int,default=215); a=ap.parse_args()
    if (a.grid_x,a.grid_y)!=(215,215):raise SystemExit("Brasil 21 km exige grade 215x215")
    run_dir=Path(a.run_dir); output=Path(a.output_dir); files=sorted(run_dir.glob("wrfout_d01_*"),key=valid_time)
    if len(files)!=49:raise SystemExit(f"Esperados 49 wrfout F000-F048; encontrados {len(files)}")
    times=[valid_time(p) for p in files]; expected=[times[0]+dt.timedelta(hours=i) for i in range(49)]
    if times!=expected:raise SystemExit("Os 49 wrfout nao formam F000-F048 horario sem lacunas")
    output.mkdir(parents=True,exist_ok=True); fields_dir=output/"fields"; fields_dir.mkdir(parents=True,exist_ok=True)
    json_path=output/"wrf_brasil_21km.json"; temp_path=output/"wrf_brasil_21km.json.tmp"; frames=[]; previous=[]; available=set(); first_stats=None
    with temp_path.open("w",encoding="utf-8") as stream:
        stream.write('{"schemaVersion":"2.0","model":"WRF Brasil","resolutionKm":21,"grid":{"nx":215,"ny":215,"dxMeters":21000,"dyMeters":21000},')
        stream.write('"reflectivitySource":"REFL_10CM_NATIVE","pressureSource":"SLP_NATIVE","nativeGrid":true,"fieldData":"fields/f{forecastHour:03d}.json.gz",')
        stream.write(f'"initTime":{json.dumps(times[0].strftime("%Y-%m-%dT%H:%M:%SZ"))},"temporalResolutionMinutes":60,"frameCount":49,"frames":[')
        for index,path in enumerate(files):
            with xr.open_dataset(path,engine="netcdf4",decode_times=False) as ds:
                lat,lon=grid_check(ds,path); refl=arr(ds,"REFL_10CM")
                if refl is None:raise RuntimeError(f"REFL_10CM ausente em {path.name}")
                if refl.ndim==4:refl=np.nanmax(refl[0],axis=0)
                elif refl.ndim==3:refl=np.nanmax(refl,axis=0)
                slp=slp_field(ds); t2=arr(ds,"T2"); q2=arr(ds,"Q2"); psfc=arr(ds,"PSFC"); u10=arr(ds,"U10"); v10=arr(ds,"V10"); hgt=arr(ds,"HGT")
                if any(x is None for x in (t2,q2,psfc,u10,v10,hgt)):raise RuntimeError(f"Campos de superficie obrigatorios ausentes em {path.name}")
                rainc=arr(ds,"RAINC"); rainnc=arr(ds,"RAINNC")
                if rainc is None and rainnc is None:raise RuntimeError(f"RAINC/RAINNC ausentes em {path.name}")
                if rainc is None:rainc=np.zeros_like(rainnc)
                if rainnc is None:rainnc=np.zeros_like(rainc)
                rain=rainc+rainnc; precip_rate=np.zeros_like(rain) if index==0 else np.maximum(0,rain-previous[-1][1]); history=list(previous); previous.append((index,rain.copy())); previous=previous[-121:]
                rows=np.arange(215); cols=np.arange(215); severe,severe_meta,severe_methods,_=compute_severe(ds,rows,cols); levels=pressure_level_fields(ds,rows,cols,21000,21000)
                tc=t2-273.15; es=611.2*np.exp(17.67*np.maximum(-80,tc)/(np.maximum(-80,tc)+243.5)); e=q2*psfc/(.622+q2); rh=np.clip(100*e/np.maximum(es,1),0,100); td2=dewpoint_from_q(q2,psfc)-273.15; the2=theta_e(t2,q2,psfc)
                gust=arr(ds,"GUST");
                if gust is None:gust=arr(ds,"WINDGUST")
                cloud=cloud_field(ds)
                fields={"lat":flat(lat,4),"lon":flat(lon,4),"reflectivity":flat(refl,1),"precipitation":flat(precip_rate,2),"precipitationAccumulated":flat(rain,2),"windSpeed":flat(np.hypot(u10,v10),1),"windDirection":flat(wind_dir(u10,v10),0),"windGust":flat(gust,1) if gust is not None else None,"temperature":flat(tc,1),"humidity":flat(rh,0),"dewpoint":flat(td2,1),"thetaE":flat(the2,1),"mslp":flat(slp,1),"pwat":flat(severe["pwat"],1),"cloudFraction":flat(cloud,0) if cloud is not None else None,"levels":levels["levels"]}
                for fh,old in history:
                    age=index-fh
                    if age in (3,6,24,120):fields[f"qpf{age}h"]=flat(np.maximum(0,rain-old),2)
                for k in ("qpf3h","qpf6h","qpf24h","qpf120h"):fields.setdefault(k,None)
                severe_names=("sbcape","mlcape","mucapeWrf2","cin","lclHeight","stp","scp","srh01","srh03","bulkShear06","effectiveBulkShear","thetaE850","thetaEAdvection","wind850","wind500","vorticity500","omega700","thickness","dewpoint2m","kIndex","totalTotals")
                for k in severe_names:fields[k]=flat(severe[k],2)
                valid=times[index].strftime("%Y-%m-%dT%H:%M:%SZ"); payload={"schema":"sideral-wrf-brasil-21km-fields-v1","model":"WRF Brasil","resolutionKm":21,"gridX":215,"gridY":215,"forecastHour":index,"validTime":valid,"fields":fields,"variableStatus":severe_meta,"diagnosticMethods":severe_methods,"levelStatus":levels["available"],"derivedNotes":{"isobars":"Geradas a partir de fields.mslp no frontend.","qpf":"Derivado do acumulado RAINC+RAINNC entre frames horarios.","heightAnomaly":"Indisponivel sem climatologia de referencia; nenhum valor e inventado.","precipitationType":"Publicado somente se houver diagnostico dedicado no wrfout; nao e inferido por temperatura simples.","frontogenesis":"Nao publicada sem diagnostico validado; nenhum valor e inventado."}}
                gz_path=fields_dir/f"f{index:03d}.json.gz"; body=json.dumps(payload,ensure_ascii=False,separators=(",",":"),allow_nan=False).encode()
                with gz_path.open("wb") as raw:
                    with gzip.GzipFile(filename="",mode="wb",fileobj=raw,compresslevel=9,mtime=0) as gz:gz.write(body)
                available.update(fields.keys()); valid_refl=refl[np.isfinite(refl)]; valid_slp=slp[np.isfinite(slp)]
                if index==0:first_stats={"minDbz":float(np.nanmin(valid_refl)),"maxDbz":float(np.nanmax(valid_refl)),"minSlpHpa":float(np.nanmin(valid_slp)),"maxSlpHpa":float(np.nanmax(valid_slp))}
                if index:stream.write(",")
                json.dump({"forecastHour":index,"time":valid,"reflectivityDbz":refl.tolist(),"seaLevelPressureHpa":slp.tolist(),"fieldFile":f"fields/f{index:03d}.json.gz","fieldCount":len(fields),"pressureLevelsHpa":list(LEVELS_HPA)},stream,separators=(",",":"),ensure_ascii=False,allow_nan=False)
                print(f"Brasil 21 km: F{index:03d} {path.name} -> campos completos")
        stream.write("]}")
    temp_path.replace(json_path)
    metadata={"schemaVersion":"2.0","model":"WRF Brasil","resolutionKm":21,"nx":215,"ny":215,"dxMeters":21000,"dyMeters":21000,"frameCount":49,"temporalResolutionMinutes":60,"reflectivitySource":"REFL_10CM_NATIVE","pressureSource":"SLP_NATIVE","nativeGrid":True,"fieldData":"fields/f{forecastHour:03d}.json.gz","pressureLevelsHpa":list(LEVELS_HPA),"availableFields":sorted(available),"pressureLevelVariables":["geopotentialHeight","windU","windV","windSpeed","windDirection","temperature","humidity","dewpoint","thetaE","vorticity","temperatureAdvection","omega"],"severeFields":["sbcape","mlcape","mucapeWrf2","cin","lclHeight","stp","scp","srh01","srh03","bulkShear06","effectiveBulkShear","pwat","thetaE850","thetaEAdvection","wind850","wind500","vorticity500","omega700","mslp","thickness","dewpoint2m","kIndex","totalTotals"],"firstFrame":first_stats,"initTime":times[0].strftime("%Y-%m-%dT%H:%M:%SZ"),"lastValidTime":times[-1].strftime("%Y-%m-%dT%H:%M:%SZ"),"isobars":{"sourceField":"mslp","unit":"hPa","frontend":"contour"}}
    (output/"metadata.json").write_text(json.dumps(metadata,ensure_ascii=False,indent=2),encoding="utf-8")
    print("Extracao Brasil 21 km concluida: superficie + pressao/isobaras + niveis + CAPE/CIN + severo")

if __name__=="__main__":
    try:main()
    except Exception as exc:print(f"ERRO EXTRATOR WRF BRASIL 21 KM: {exc}",file=sys.stderr);raise
