#!/usr/bin/env python3
from __future__ import annotations
import argparse, datetime as dt, gzip, json, math, re, shutil, traceback
from pathlib import Path
import numpy as np
import xarray as xr

G,RD,CP,EPS=9.80665,287.05,1004.0,0.622
K=RD/CP
FIELDS=["stp","scp","srh01","srh03","bulkShear06","effectiveBulkShear","lclHeight","cin","sbcape","mlcape","mucapeWrf2","pwat","thetaE850","thetaEAdvection","wind850","wind500","vorticity500","omega700","mslp","thickness","dewpoint2m","kIndex","totalTotals"]
METHODS={
"stp":{"classification":"derived","method":"fixed-layer STP","components":["MLCAPE","surface LCL height","cyclonic SRH 0-1 km","bulk shear 0-6 km","MLCIN"],"rule":"Only calculated where CAPE, LCL, SRH, shear and CIN components are finite."},
"scp":{"classification":"derived","method":"SCP","components":["MUCAPE","cyclonic SRH 0-3 km","effective bulk shear proxy"],"rule":"Only calculated where MUCAPE, SRH 0-3 km and shear are finite."},
"srh01":"Bunkers cyclonic motion; 250 m hodograph; 0-1 km AGL",
"srh03":"Bunkers cyclonic motion; 250 m hodograph; 0-3 km AGL",
"bulkShear06":"10 m to 6 km AGL vector difference",
"effectiveBulkShear":"derived 0-6 km bulk-shear proxy; never native",
"lclHeight":"Bolton surface parcel",
"pwat":"vertical integral QVAPOR dp/g","thetaE850":"Bolton theta-e at 850 hPa",
"thetaEAdvection":"-V.grad(theta-e) at 850 hPa","wind850":"interpolated wind magnitude at 850 hPa",
"wind500":"interpolated wind magnitude at 500 hPa","vorticity500":"dvdx-dudy at 500 hPa",
"omega700":"-rho*g*w at 700 hPa","mslp":"hypsometric reduction from PSFC/T2/HGT",
"thickness":"Z500 minus extrapolated Z1000","dewpoint2m":"dewpoint from Q2/PSFC",
"kIndex":"T850-T500+Td850-(T700-Td700)","totalTotals":"T850+Td850-2*T500"}

def now(): return dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00","Z")
def runenv(path):
    out={}
    if path.exists():
        for line in path.read_text(encoding="utf-8",errors="ignore").splitlines():
            if "=" in line:
                k,v=line.split("=",1); out[k.strip()]=v.strip()
    return out
def validtime(path):
    m=re.search(r"wrfout_d01_(\d{4}-\d{2}-\d{2})_(\d{2})[-:](\d{2})[-:](\d{2})",path.name)
    if not m: raise ValueError(f"Nome wrfout inesperado: {path.name}")
    return dt.datetime.strptime(f"{m[1]} {m[2]}:{m[3]}:{m[4]}","%Y-%m-%d %H:%M:%S").replace(tzinfo=dt.timezone.utc)
def idx(n,target): return np.rint(np.linspace(0,n-1,max(1,min(n,target)))).astype(int)
def s2(a,r,c): return np.asarray(a)[np.ix_(r,c)]
def s3(a,r,c): return np.asarray(a)[:,r,:][:,:,c]
def nan(shape): return np.full(shape,np.nan,dtype=float)
def count(a): return int(np.isfinite(np.asarray(a,dtype=float)).sum())
def flat(a,d,lo=None,hi=None):
    a=np.asarray(a,dtype=float); finite=np.isfinite(a)
    if lo is not None: a=np.where(finite,np.maximum(a,lo),a)
    if hi is not None: a=np.where(finite,np.minimum(a,hi),a)
    return [float(x) if math.isfinite(float(x)) else None for x in np.round(a,d).ravel()]
def write_json(path,obj):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(obj,ensure_ascii=False,indent=2,allow_nan=False),encoding="utf-8")
def write_gz(path,obj):
    path.parent.mkdir(parents=True,exist_ok=True)
    body=json.dumps(obj,ensure_ascii=False,separators=(",",":"),allow_nan=False).encode()
    with path.open("wb") as raw:
        with gzip.GzipFile(filename="",mode="wb",fileobj=raw,compresslevel=9,mtime=0) as z:z.write(body)

def td(q,p):
    q,p=np.asarray(q,float),np.asarray(p,float); ok=np.isfinite(q)&np.isfinite(p)&(q>0)&(p>1000)
    out=np.full(np.broadcast(q,p).shape,np.nan); q2=np.clip(q,1e-8,.08); p2=np.clip(p,1000,110000)
    e=np.clip(q2*p2/(EPS+q2),1,p2*.99); l=np.log(e/611.2); cand=243.5*l/(17.67-l)+273.15
    out[ok]=cand[ok]; return out
def qs(p,t):
    p,t=np.asarray(p,float),np.asarray(t,float); ok=np.isfinite(p)&np.isfinite(t); out=np.full(np.broadcast(p,t).shape,np.nan)
    tc=t-273.15; es=611.2*np.exp(17.67*tc/(tc+243.5)); es=np.clip(es,1,np.maximum(2,p*.98))
    cand=np.clip(EPS*es/np.maximum(1,p-es),0,.08); out[ok]=cand[ok]; return out
def thetae(t,q,p):
    t,q,p=np.asarray(t,float),np.asarray(q,float),np.asarray(p,float); ok=np.isfinite(t)&np.isfinite(q)&np.isfinite(p)&(q>0)&(p>1000)
    out=np.full(np.broadcast(t,q,p).shape,np.nan); t2=np.clip(t,180,340); q2=np.clip(q,1e-8,.08); p2=np.clip(p,1000,110000)
    d=np.clip(td(q2,p2),170,t2); tl=1/(1/(d-56)+np.log(t2/d)/800)+56
    th=t2*(100000/p2)**(.2854*(1-.28*q2)); cand=th*np.exp((3376/tl-2.54)*q2*(1+.81*q2)); out[ok]=cand[ok]; return out
def lcl(t,q,p):
    t,q,p=np.asarray(t,float),np.asarray(q,float),np.asarray(p,float); ok=np.isfinite(t)&np.isfinite(q)&np.isfinite(p)&(q>0)&(p>1000)
    out=np.full(np.broadcast(t,q,p).shape,np.nan); t2=np.clip(t,180,340); d=np.clip(td(q,p),170,t2)
    tl=1/(1/(d-56)+np.log(t2/d)/800)+56; cand=np.maximum(0,(t2-tl)/(G/CP)); out[ok]=cand[ok]; return out,tl
def interp(coord,val,target,log=False):
    c,v=np.asarray(coord,float),np.asarray(val,float); c=np.log(np.where(c>0,c,np.nan)) if log else c; target=math.log(target) if log else target
    out=np.full(c.shape[1:],np.nan)
    for k in range(c.shape[0]-1):
        c0,c1,v0,v1=c[k],c[k+1],v[k],v[k+1]; ok=np.isfinite(c0)&np.isfinite(c1)&np.isfinite(v0)&np.isfinite(v1)&(((target-c0)*(target-c1))<=0)
        den=c1-c0; f=np.divide(target-c0,den,out=np.full_like(c0,np.nan),where=np.abs(den)>1e-12); cand=v0+f*(v1-v0); fill=ok&~np.isfinite(out); out[fill]=cand[fill]
    return out
def profile(z,u,v,u10,v10,hs):
    us=[];vs=[]
    for h in hs:
        if h==0: us.append(u10);vs.append(v10)
        else: us.append(interp(z,u,h));vs.append(interp(z,v,h))
    return np.stack(us),np.stack(vs)
def bunkers(lat,u,v,hs):
    hs=np.asarray(hs); mean=hs<=6000; low=hs<=500; high=hs>=5500
    with np.errstate(invalid="ignore"):
        um,vm=np.nanmean(u[mean],0),np.nanmean(v[mean],0); ul,vl=np.nanmean(u[low],0),np.nanmean(v[low],0); uh,vh=np.nanmean(u[high],0),np.nanmean(v[high],0)
    du,dv=uh-ul,vh-vl; mag=np.hypot(du,dv); ok=np.isfinite(um)&np.isfinite(vm)&np.isfinite(mag)&(mag>1e-6); sign=np.where(lat<0,-1,1)
    cu,cv=np.full_like(um,np.nan),np.full_like(vm,np.nan); cu[ok]=(um+sign*7.5*dv/mag)[ok]; cv[ok]=(vm-sign*7.5*du/mag)[ok]; return cu,cv
def srh(u,v,hs,su,sv,top):
    ids=np.where(np.asarray(hs)<=top)[0]; u,v=u[ids],v[ids]; total=np.zeros_like(su); used=np.zeros_like(su,dtype=int)
    for k in range(len(ids)-1):
        ok=np.isfinite(su)&np.isfinite(sv)&np.isfinite(u[k])&np.isfinite(v[k])&np.isfinite(u[k+1])&np.isfinite(v[k+1])
        term=(u[k+1]-su)*(v[k]-sv)-(u[k]-su)*(v[k+1]-sv); total=np.where(ok,total+term,total); used+=ok
    return np.where(used==max(1,len(ids)-1),total,np.nan)

def cape_cin(p3,t3,q3,z3,p0,t0,q0,z0):
    p0,t0,q0,z0=map(lambda x:np.asarray(x,float),(p0,t0,q0,z0)); surf=np.isfinite(p0)&np.isfinite(t0)&np.isfinite(q0)&np.isfinite(z0)
    ldz,tl=lcl(t0,q0,p0); zl=z0+ldz; dz3=z3-z0[None]; dry=t0[None]-G/CP*dz3; moist=tl[None]-.006*(z3-zl[None]); tp=np.where(z3<=zl[None],dry,moist)
    qp=np.where(z3<=zl[None],q0[None],qs(p3,tp)); b=G*(tp*(1+.61*qp)-t3*(1+.61*q3))/(t3*(1+.61*q3))
    b=np.where(surf[None]&np.isfinite(b)&np.isfinite(z3)&np.isfinite(p3)&(z3>=z0[None])&(p3<=p0[None]*1.01),b,np.nan)
    cape=np.zeros_like(p0); cin=np.zeros_like(p0); seen=np.zeros_like(p0,dtype=bool); layers=np.zeros_like(p0,dtype=int)
    for k in range(z3.shape[0]-1):
        dz=z3[k+1]-z3[k]; area=.5*(b[k]+b[k+1])*dz; ok=np.isfinite(area)&np.isfinite(dz)&(dz>0)&(dz<3000); layers+=ok
        pos=ok&(area>0); neg=ok&(area<0)&~seen; cape+=np.where(pos,area,0); cin+=np.where(neg,area,0); seen|=pos
    ok=surf&(layers>=3); return np.where(ok,np.clip(cape,0,8000),np.nan),np.where(ok,np.clip(cin,-600,0),np.nan)
def mixed(p,t,q,psfc,z0):
    th=t*(100000/p)**K; mask=np.isfinite(th)&np.isfinite(q)&np.isfinite(p)&(p<=psfc[None]+1000)&(p>=psfc[None]-10000); n=mask.sum(0)
    thm=np.divide(np.nansum(np.where(mask,th,np.nan),0),n,out=nan(psfc.shape),where=n>0); qm=np.divide(np.nansum(np.where(mask,q,np.nan),0),n,out=nan(psfc.shape),where=n>0)
    return psfc,thm*(psfc/100000)**K,qm,z0
def most_unstable(p,t,q,z,psfc):
    te=thetae(t,q,p); mask=np.isfinite(te)&(p<=psfc[None]+1000)&(p>=psfc[None]-30000); score=np.where(mask,te,-np.inf); valid=mask.any(0); ids=np.argmax(score,0); yy,xx=np.indices(psfc.shape)
    def pick(a): return np.where(valid,np.asarray(a)[ids,yy,xx],np.nan)
    return pick(p),pick(t),pick(q),pick(z)

def compute(ds,r,c):
    shape=(len(r),len(c)); out={f:nan(shape) for f in FIELDS}; why={f:[] for f in FIELDS}; klass={f:"derived" for f in FIELDS}
    def note(fs,msg):
        for f in fs:
            if msg not in why[f]: why[f].append(msg)
    def two(name):
        if name not in ds:return None
        a=np.asarray(ds[name].isel(Time=0).to_numpy(),float); return s2(a,r,c) if a.ndim==2 else None
    def native(names):
        for name in names:
            a=two(name)
            if a is not None:return a,name
        return None,None
    lat,lon=two("XLAT"),two("XLONG")
    if lat is None or lon is None: raise RuntimeError("XLAT/XLONG ausentes")
    t2,q2,psfc,hgt,u10,v10=[two(x) for x in ("T2","Q2","PSFC","HGT","U10","V10")]
    if t2 is not None and q2 is not None and psfc is not None:
        out["dewpoint2m"]=td(q2,psfc)-273.15; out["lclHeight"]=lcl(t2,q2,psfc)[0]
    else: note(["dewpoint2m","lclHeight","stp"],"T2/Q2/PSFC incompletos.")
    if all(x is not None for x in (t2,q2,psfc,hgt)):
        tv=t2*(1+.61*q2); out["mslp"]=psfc*np.exp(G*hgt/(RD*tv))/100
    else: note(["mslp"],"T2/Q2/PSFC/HGT incompletos.")
    native_map={"sbcape":["SBCAPE","AFWA_SBCAPE"],"cin":["SBCIN","CIN","AFWA_CIN"],"mlcape":["MLCAPE","AFWA_MLCAPE"],"mucapeWrf2":["MUCAPE","MCAPE","AFWA_CAPE"]}
    nsrc={}
    for f,names in native_map.items():
        a,name=native(names); nsrc[f]=name
        if a is not None: out[f]=a; klass[f]="native"
    req=["P","PB","T","QVAPOR","PH","PHB","U","V"]; missing=[x for x in req if x not in ds]
    if missing:
        affected=["srh01","srh03","bulkShear06","effectiveBulkShear","pwat","thetaE850","thetaEAdvection","wind850","wind500","vorticity500","thickness","kIndex","totalTotals","stp","scp"]
        note(affected,"Perfis verticais ausentes: "+", ".join(missing))
        for f in ("sbcape","cin","mlcape","mucapeWrf2"):
            if nsrc[f] is None: note([f],"Campo nativo ausente e perfil vertical insuficiente para derivação.")
    else:
        p=s3(np.asarray(ds["P"].isel(Time=0))+np.asarray(ds["PB"].isel(Time=0)),r,c); th=s3(np.asarray(ds["T"].isel(Time=0))+300,r,c); t=th*(p/100000)**K; q=s3(np.asarray(ds["QVAPOR"].isel(Time=0)),r,c)
        ph=np.asarray(ds["PH"].isel(Time=0))+np.asarray(ds["PHB"].isel(Time=0)); zs=s3(ph/G,r,c); z=.5*(zs[:-1]+zs[1:]); zagl=z-hgt[None] if hgt is not None else np.full_like(z,np.nan)
        ur=np.asarray(ds["U"].isel(Time=0));vr=np.asarray(ds["V"].isel(Time=0));u=s3(.5*(ur[:,:,:-1]+ur[:,:,1:]),r,c);v=s3(.5*(vr[:,:-1,:]+vr[:,1:,:]),r,c)
        t850,t700,t500=[interp(p,t,x,True) for x in (85000,70000,50000)]; q850,q700=[interp(p,q,x,True) for x in (85000,70000)]
        u850,v850,u500,v500=[interp(p,a,lev,True) for a,lev in ((u,85000),(v,85000),(u,50000),(v,50000))];z500=interp(p,z,50000,True)
        out["wind850"],out["wind500"]=np.hypot(u850,v850),np.hypot(u500,v500); out["thetaE850"]=thetae(t850,q850,np.full_like(t850,85000));out["pwat"]=-np.trapezoid(q,p,axis=0)/G
        dx,dy=float(ds.attrs.get("DX",4000)),float(ds.attrs.get("DY",4000));out["thetaEAdvection"]=-(u850*np.gradient(out["thetaE850"],dx,axis=1)+v850*np.gradient(out["thetaE850"],dy,axis=0))*3600;out["vorticity500"]=np.gradient(v500,dx,axis=1)-np.gradient(u500,dy,axis=0)
        if "W" in ds:
            wr=np.asarray(ds["W"].isel(Time=0));w=s3(.5*(wr[:-1]+wr[1:]),r,c);w700=interp(p,w,70000,True);rho=70000/(RD*(t700*(1+.61*q700)));out["omega700"]=-rho*G*w700
        else: note(["omega700"],"W ausente no wrfout.")
        d850,d700=td(q850,np.full_like(q850,85000)),td(q700,np.full_like(q700,70000));out["kIndex"]=(t850-273.15)-(t500-273.15)+(d850-273.15)-((t700-273.15)-(d700-273.15));out["totalTotals"]=(t850-273.15)+(d850-273.15)-2*(t500-273.15)
        if all(x is not None for x in (hgt,t2,q2,psfc)):
            tv=t2*(1+.61*q2);z1000=hgt+RD*tv/G*np.log(psfc/100000);out["thickness"]=(z500-z1000)/10
        else: note(["thickness"],"HGT/T2/Q2/PSFC incompletos.")
        if hgt is not None and u10 is not None and v10 is not None:
            hs=[0.]+[float(x) for x in range(250,6001,250)];up,vp=profile(zagl,u,v,u10,v10,hs);su,sv=bunkers(lat,up,vp,hs);out["srh01"]=srh(up,vp,hs,su,sv,1000);out["srh03"]=srh(up,vp,hs,su,sv,3000);out["bulkShear06"]=np.hypot(up[-1]-u10,vp[-1]-v10);out["effectiveBulkShear"]=out["bulkShear06"].copy()
        else: note(["srh01","srh03","bulkShear06","effectiveBulkShear","stp","scp"],"HGT/U10/V10 incompletos; perfil AGL indisponível.")
        if all(x is not None for x in (psfc,t2,q2,hgt)):
            z0=hgt+2; sb,sbcin=cape_cin(p,t,q,z,psfc,t2,q2,z0)
            if nsrc["sbcape"] is None:out["sbcape"]=sb
            if nsrc["cin"] is None:out["cin"]=sbcin
            mp,mt,mq,mz=mixed(p,t,q,psfc,z0);ml,mlcin=cape_cin(p,t,q,z,mp,mt,mq,mz)
            if nsrc["mlcape"] is None:out["mlcape"]=ml
            mup,mut,muq,muz=most_unstable(p,t,q,z,psfc);mu,_=cape_cin(p,t,q,z,mup,mut,muq,muz)
            if nsrc["mucapeWrf2"] is None:out["mucapeWrf2"]=mu
            sr1=np.where(lat<0,np.maximum(0,-out["srh01"]),np.maximum(0,out["srh01"]));sh=out["bulkShear06"]
            ok=np.isfinite(out["mlcape"])&np.isfinite(out["lclHeight"])&np.isfinite(out["srh01"])&np.isfinite(sh)&np.isfinite(mlcin)
            cand=np.clip(np.clip(out["mlcape"]/1500,0,4)*np.clip((2000-out["lclHeight"])/1000,0,1)*np.clip(sr1/150,0,3)*np.where(sh<12.5,0,np.where(sh>30,1.5,sh/20))*np.where(mlcin>=-50,1,np.where(mlcin<=-200,0,(200+mlcin)/150)),0,10);out["stp"]=np.where(ok,cand,np.nan)
            sr3=np.where(lat<0,np.maximum(0,-out["srh03"]),np.maximum(0,out["srh03"]));es=out["effectiveBulkShear"];ok=np.isfinite(out["mucapeWrf2"])&np.isfinite(out["srh03"])&np.isfinite(es);cand=np.clip(out["mucapeWrf2"]/1000*np.clip(sr3/50,0,6)*np.clip(es/20,0,2.5),0,50);out["scp"]=np.where(ok,cand,np.nan)
        else: note(["sbcape","cin","mlcape","mucapeWrf2","stp","scp"],"PSFC/T2/Q2/HGT incompletos para parcelas.")
    variables={}
    for f in FIELDS:
        n=count(out[f]); k=klass[f] if n else "unavailable"; variables[f]={"classification":k,"status":"available" if n else "unavailable","finiteValueCount":n}
        if nsrc.get(f):variables[f]["nativeSource"]=nsrc[f]
        if why[f]:variables[f]["diagnostics"]=why[f]
    methods=dict(METHODS);methods["cape"]={f:{"classification":variables[f]["classification"],"nativeSource":nsrc.get(f)} for f in ("sbcape","mlcape","mucapeWrf2","cin")}
    out["lat"],out["lon"]=lat,lon
    return out,variables,methods,{"missingWrfVariables":missing,"variables":{f:why[f] for f in FIELDS if why[f]}}

def payload(model,date,cycle,init,fh,valid,fields,vars,methods,dx,dy):
    lat,lon=fields["lat"],fields["lon"]
    specs={"stp":(2,0,10),"scp":(2,0,50),"srh01":(0,-1000,1000),"srh03":(0,-1500,1500),"bulkShear06":(1,0,100),"effectiveBulkShear":(1,0,100),"lclHeight":(0,0,5000),"cin":(0,-600,0),"sbcape":(0,0,8000),"mlcape":(0,0,8000),"mucapeWrf2":(0,0,8000),"pwat":(1,0,100),"thetaE850":(1,250,400),"thetaEAdvection":(2,-20,20),"wind850":(1,0,100),"wind500":(1,0,120),"vorticity500":(7,-.002,.002),"omega700":(3,-20,20),"mslp":(1,850,1080),"thickness":(1,450,650),"dewpoint2m":(1,-80,40),"kIndex":(1,-50,70),"totalTotals":(1,-20,80)}
    fld={"lat":flat(lat,4),"lon":flat(lon,4)}
    for f,(d,lo,hi) in specs.items():fld[f]=flat(fields[f],d,lo,hi)
    return {"schema":"sideral-wrf2-severe-grid-v2","model":model,"source":f"WRF 2 Sudeste 4 km {model.upper()} · diagnósticos severos","runDate":date,"runCycle":f"{cycle}Z" if cycle else None,"initTime":init,"forecastHour":fh,"validTime":valid,"dxMeters":round(dx),"dyMeters":round(dy),"gridX":lat.shape[1],"gridY":lat.shape[0],"bounds":{"south":round(float(np.nanmin(lat)),4),"west":round(float(np.nanmin(lon)),4),"north":round(float(np.nanmax(lat)),4),"east":round(float(np.nanmax(lon)),4)},"variables":vars,"diagnosticMethods":methods,"fields":fld}

def main():
    ap=argparse.ArgumentParser();ap.add_argument("--run-dir",default="wrf_work/run");ap.add_argument("--run-env",default="wrf_diagnostics/run.env");ap.add_argument("--output-dir",default="wrf2_publish");ap.add_argument("--grid-x",type=int,default=220);ap.add_argument("--grid-y",type=int,default=180);ap.add_argument("--model",choices=("gfs","icon","ecmwf"),default="gfs");ap.add_argument("--interval-hours",type=int,default=3);ap.add_argument("--expected-start",type=int);ap.add_argument("--expected-end",type=int);a=ap.parse_args()
    out=Path(a.output_dir);shutil.rmtree(out,ignore_errors=True);out.mkdir(parents=True); env=runenv(Path(a.run_env));date,cycle=env.get("RUN_DATE"),env.get("RUN_CYCLE");init=None
    if date and cycle:
        try:init=dt.datetime.strptime(date+cycle,"%Y%m%d%H").replace(tzinfo=dt.timezone.utc)
        except ValueError:pass
    diag={"schema":"sideral-wrf2-severe-diagnostics-v1","model":a.model,"generatedAt":now(),"frameErrors":[],"missingFrames":[],"notes":["Ausência nunca é convertida em zero.","Valores não calculáveis são null.","REFL_10CM_NATIVE é independente e não é alterada por este módulo."]}
    try: files=sorted(Path(a.run_dir).glob("wrfout_d01_*"),key=validtime)
    except Exception as e: files=[];diag["frameErrors"].append({"file":None,"error":str(e)})
    if init is None and files:
        try:init=validtime(files[0]);date,cycle=init.strftime("%Y%m%d"),init.strftime("%H")
        except Exception as e:diag["frameErrors"].append({"file":files[0].name,"error":str(e)})
    init_s=init.isoformat().replace("+00:00","Z") if init else None; selected=[]
    for path in files:
        try:
            vt=validtime(path);fh=max(0,round((vt-init).total_seconds()/3600)) if init else 0
            if fh%a.interval_hours==0:selected.append((path,int(fh),vt))
        except Exception as e:diag["frameErrors"].append({"file":path.name,"error":str(e)})
    expected=[]
    if a.expected_start is not None and a.expected_end is not None:
        first=a.expected_start if a.expected_start%a.interval_hours==0 else a.expected_start+(a.interval_hours-a.expected_start%a.interval_hours);expected=list(range(first,a.expected_end+1,a.interval_hours))
    r=c=None;frames=[];hist={f:[] for f in FIELDS};methods=dict(METHODS)
    for path,fh,vt in selected:
        try:
            with xr.open_dataset(path,engine="netcdf4",decode_times=False) as ds:
                if r is None:
                    ny,nx=ds["XLAT"].isel(Time=0).shape;r,c=idx(ny,a.grid_y),idx(nx,a.grid_x)
                fields,vars,methods,detail=compute(ds,r,c);dx,dy=float(ds.attrs.get("DX",4000)),float(ds.attrs.get("DY",4000))
            pl=payload(a.model,date,cycle,init_s,fh,vt.isoformat().replace("+00:00","Z"),fields,vars,methods,dx,dy);rel=f"severe/{a.model}/f{fh:03d}.json.gz";write_gz(out/rel,pl);frames.append({"index":len(frames),"forecastHour":fh,"validTime":pl["validTime"],"file":rel,"gridX":pl["gridX"],"gridY":pl["gridY"]})
            for f in FIELDS:hist[f].append(vars[f])
            if detail["missingWrfVariables"]:diag.setdefault("frameDiagnostics",[]).append({"forecastHour":fh,**detail})
        except Exception as e:diag["frameErrors"].append({"forecastHour":fh,"file":path.name,"error":str(e),"traceback":traceback.format_exc(limit=4)})
    actual=sorted(x["forecastHour"] for x in frames);expected_all=sorted(set(expected or [x[1] for x in selected]));missing=sorted(set(expected_all)-set(actual));diag["missingFrames"]=[f"f{x:03d}.json.gz" for x in missing];diag["expectedForecastHours"]=expected_all;diag["publishedForecastHours"]=actual
    vm={}
    for f in FIELDS:
        h=hist[f]
        if not h:vm[f]={"classification":"unavailable","status":"unavailable","reason":"Nenhum frame severo produzido."};continue
        available=sum(x.get("status")=="available" for x in h);classes={x.get("classification","unavailable") for x in h};cl="derived" if "derived" in classes else ("native" if "native" in classes else "unavailable");vm[f]={"classification":cl if available else "unavailable","status":"available" if available else "unavailable","availableFrames":available,"totalFrames":len(h)}
        reasons=sorted({y for x in h for y in x.get("diagnostics",[])});
        if reasons:vm[f]["diagnostics"]=reasons
    status="unavailable" if not frames else ("partial" if missing or diag["frameErrors"] else "complete")
    meta={"schema":"sideral-wrf2-severe-metadata-v2","model":a.model,"source":f"WRF 2 Sudeste 4 km {a.model.upper()} · diagnósticos severos","runDate":date,"runCycle":f"{cycle}Z" if cycle else None,"initTime":init_s,"generatedAt":now(),"status":status,"frameCount":len(frames),"temporalResolutionMinutes":a.interval_hours*60,"variables":vm,"diagnosticMethods":methods,"diagnostics":{"file":"diagnostics.json","missingFrameCount":len(missing),"frameErrorCount":len(diag["frameErrors"])},"reflectivity":{"status":"independent","source":"REFL_10CM_NATIVE","note":"Os diagnósticos severos não alteram a refletividade nativa."},"expectedFrames":[f"severe/{a.model}/f{x:03d}.json.gz" for x in expected_all],"frames":frames}
    diag["status"]=status;diag["metadata"]={"runDate":date,"runCycle":f"{cycle}Z" if cycle else None,"initTime":init_s};write_json(out/"metadata.json",meta);write_json(out/"diagnostics.json",diag);print(json.dumps(meta,ensure_ascii=False,indent=2))
if __name__=="__main__":main()
