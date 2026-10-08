#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import re
from pathlib import Path
from urllib.parse import urljoin, urlparse

import netCDF4
import numpy as np
import requests
from PIL import Image

CPTEC_INDEX = "https://ftp.cptec.inpe.br/nowcasting/DADOS/radar_volumetrico/"
SCHEMA = "sideral-cptec-ppi-z-v1"
RADAR_CODES = {"R13537460": "São Roque", "R12132246": "Gama"}

PALETTE = np.array([
    [0,0,0,0], [5,4,233,231], [10,1,159,244], [20,3,0,244],
    [30,2,253,2], [40,1,197,1], [45,0,142,0], [50,254,254,0],
    [55,253,149,0], [60,253,0,0], [65,212,0,0], [70,188,0,0],
    [75,248,0,253]
], dtype=np.float32)

def nc_text(v) -> str:
    if v is None: return ""
    if isinstance(v, bytes): return v.decode("utf-8","ignore")
    if isinstance(v, np.ndarray):
        if v.size == 1: return nc_text(v.reshape(-1)[0])
        return " ".join(nc_text(x) for x in v.reshape(-1))
    return str(v)

def latest_nc(session, index_url, explicit_url):
    if explicit_url: return explicit_url
    r=session.get(index_url,timeout=(15,45)); r.raise_for_status()
    links=re.findall(r'href=["\']([^"\']+\.nc)["\']',r.text,re.I)
    urls=[]
    for link in links:
        u=urljoin(index_url,html.unescape(link))
        if urlparse(u).hostname == urlparse(index_url).hostname: urls.append(u)
    if not urls: raise RuntimeError("Nenhum NetCDF volumétrico CPTEC encontrado.")
    return sorted(urls)[-1]

def download(session,url,path):
    with session.get(url,timeout=(15,120),stream=True) as r:
        r.raise_for_status()
        with path.open("wb") as f:
            for chunk in r.iter_content(1024*1024):
                if chunk: f.write(chunk)

def find_reflectivity(ds):
    ranked=[]
    for name,var in ds.variables.items():
        text=" ".join([name,nc_text(getattr(var,"standard_name","")),
                       nc_text(getattr(var,"long_name","")),
                       nc_text(getattr(var,"description","")),
                       nc_text(getattr(var,"units",""))]).lower()
        score=0
        if "reflectivity" in text or "refletividade" in text: score+=100
        if "dbz" in text: score+=80
        if re.search(r"(^|[_-])(dbz|refz|z)([_-]|$)",name.lower()): score+=90
        if "velocity" in text or "radial" in text: score-=100
        if "rain" in text or "precip" in text: score-=80
        if var.ndim>=2: score+=10
        if score>0: ranked.append((score,name,var))
    if not ranked:
        raise RuntimeError("Variável de refletividade não encontrada. Variáveis: "+", ".join(ds.variables.keys()))
    ranked.sort(key=lambda x:(-x[0],x[1]))
    return ranked[0][1],ranked[0][2]

def find_coord(ds,names,keywords):
    for n in names:
        if n in ds.variables: return ds.variables[n]
    ranked=[]
    for n,v in ds.variables.items():
        t=" ".join([n,nc_text(getattr(v,"standard_name","")),nc_text(getattr(v,"long_name",""))]).lower()
        score=sum(20 for k in keywords if k in t)
        if score and v.ndim<=2: ranked.append((score,n,v))
    ranked.sort(key=lambda x:(-x[0],x[1]))
    return ranked[0][2] if ranked else None

def scalar(ds,names):
    for n in names:
        if n in ds.variables:
            try:
                a=np.asarray(ds.variables[n][:]).squeeze()
                if a.size==1: return float(a.reshape(-1)[0])
            except Exception: pass
        if hasattr(ds,n):
            try: return float(getattr(ds,n))
            except Exception: pass
    return None

def axes(var):
    dims=list(var.dimensions); names=[x.lower() for x in dims]
    def pick(keys):
        for k in keys:
            for i,n in enumerate(names):
                if k in n: return i
        return None
    elev=pick(("elev","elevation","sweep","tilt","fixed"))
    az=pick(("azimuth","azim","azi","ray"))
    rg=pick(("range","gate","bin","distance"))
    if var.ndim==2: return -1, (az if az is not None else 0), (rg if rg is not None else 1)
    if var.ndim<3: raise RuntimeError(f"Refletividade precisa ter >=2 dimensões; recebeu {var.ndim}.")
    if az is None:
        choices=[i for i in range(var.ndim) if i!=rg]
        az=max(choices,key=lambda i:var.shape[i])
    if rg is None:
        choices=[i for i in range(var.ndim) if i!=az]
        rg=min(choices,key=lambda i:var.shape[i])
    if elev is None:
        choices=[i for i in range(var.ndim) if i not in {az,rg}]
        elev=choices[0]
    return elev,az,rg

def elevation_values(ds,var,elev_axis):
    if elev_axis<0: return np.array([0.0])
    dim=var.dimensions[elev_axis]
    if dim in ds.variables:
        a=np.asarray(ds.variables[dim][:]).astype(float).squeeze()
        if a.size: return np.atleast_1d(a)
    c=find_coord(ds,("elevation","elev","fixed_angle","tilt"),("elevation","fixed"))
    if c is not None:
        a=np.asarray(c[:]).astype(float).squeeze()
        if a.size: return np.atleast_1d(a)
    return np.arange(var.shape[elev_axis],dtype=float)

def ranges(ds,var,range_axis,n):
    dim=var.dimensions[range_axis]
    for name in ("range","ranges","gate","distance",dim):
        if name in ds.variables:
            c=ds.variables[name]
            try:
                a=np.asarray(c[:]).astype(float).squeeze()
                if a.size==n:
                    units=nc_text(getattr(c,"units","")).lower()
                    if "km" not in units and units.endswith("m"): a=a/1000.0
                    return a
            except Exception: pass
    return np.arange(n,dtype=float)

def azimuth(ds,var,n):
    c=find_coord(ds,("azimuth","azim","azi","az"),("azimuth","azim"))
    if c is not None:
        try:
            a=np.asarray(c[:]).astype(float).squeeze()
            if a.size==n: return np.mod(a,360.0)
        except Exception: pass
    return np.linspace(0,360,n,endpoint=False)

def choose_sweep(elevs,target):
    return int(np.nanargmin(np.abs(elevs-target))) if elevs.size else 0

def sweep_to_az_range(data,var,elev_axis,az_axis,range_axis,sweep):
    if var.ndim==2: return np.asarray(np.moveaxis(data,(az_axis,range_axis),(0,1)))
    sl=[slice(None)]*data.ndim; sl[elev_axis]=sweep
    reduced=data[tuple(sl)]
    newdims=[i for i in range(var.ndim) if i!=elev_axis]
    azpos=newdims.index(az_axis); rgpos=newdims.index(range_axis)
    return np.asarray(np.moveaxis(reduced,(azpos,rgpos),(0,1)))

def palette(dbz):
    out=np.zeros(dbz.shape+(4,),dtype=np.uint8)
    good=np.isfinite(dbz)&(dbz>=5)
    if not good.any(): return out
    x=np.clip(dbz[good],5,75)
    for j in range(4): out[good,j]=np.clip(np.interp(x,PALETTE[:,0],PALETTE[:,j]),0,255).astype(np.uint8)
    return out

def render_ppi(z,az,rng,width,height,max_range):
    if z.shape!=(az.size,rng.size): raise RuntimeError(f"Dimensões incompatíveis: Z={z.shape}, az={az.size}, range={rng.size}")
    max_range=float(max_range if max_range else np.nanmax(rng))
    side=min(width,height)
    physical_x=max_range*(width/side)
    xx=np.linspace(-physical_x,physical_x,width,dtype=np.float32)
    yy=np.linspace(max_range,-max_range,height,dtype=np.float32)
    X,Y=np.meshgrid(xx,yy)
    radius=np.hypot(X,Y)
    theta=np.mod(np.degrees(np.arctan2(X,Y)),360.0)
    ri=np.clip(np.searchsorted(rng,radius),0,rng.size-1)
    ai=np.mod(np.rint(theta/360.0*az.size).astype(np.int32),az.size)
    field=z[ai,ri]
    field=np.where(radius<=max_range,field,np.nan)
    return Image.fromarray(palette(field),"RGBA")

def main():
    ap=argparse.ArgumentParser(description="PPI Z real a partir do NetCDF volumétrico CPTEC/INPE.")
    ap.add_argument("--url",default="")
    ap.add_argument("--index",default=CPTEC_INDEX)
    ap.add_argument("--elevation",type=float,default=0.5)
    ap.add_argument("--width",type=int,default=3840)
    ap.add_argument("--height",type=int,default=2160)
    ap.add_argument("--max-range-km",type=float,default=250.0)
    ap.add_argument("--output",default="cptec-ppi-z")
    args=ap.parse_args()

    out=Path(args.output); out.mkdir(parents=True,exist_ok=True)
    session=requests.Session()
    session.headers.update({"User-Agent":"Sideral-CPTEC-PPI-Z/1.0","Accept":"text/html,application/octet-stream,*/*"})
    src=latest_nc(session,args.index,args.url or None)
    source_path=out/"source.nc"; download(session,src,source_path)

    with netCDF4.Dataset(source_path) as ds:
        ref_name,ref=find_reflectivity(ds)
        elev_axis,az_axis,range_axis=axes(ref)
        elevs=elevation_values(ds,ref,elev_axis)
        sweep=choose_sweep(elevs,args.elevation)
        fill=getattr(ref,"_FillValue",None)
        data=np.asarray(ref[:],dtype=np.float32)
        if fill is not None: data[np.isclose(data,float(fill))]=np.nan
        data[~np.isfinite(data)]=np.nan
        data[(data<-50)|(data>100)]=np.nan
        z=sweep_to_az_range(data,ref,elev_axis,az_axis,range_axis,sweep)
        rng=ranges(ds,ref,range_axis,z.shape[1])
        az=azimuth(ds,ref,z.shape[0])
        image=render_ppi(z,az,rng,max(256,args.width),max(256,args.height),args.max_range_km)
        fname=Path(src).name
        m=re.search(r"^(R[A-Za-z0-9_-]+)_(\d{12})\.nc$",fname)
        radar_code=m.group(1) if m else Path(fname).stem.split("_",1)[0]
        stamp=m.group(2) if m else Path(fname).stem
        output_name=f"{radar_code}_{stamp}_ppi-z-{sweep:02d}.png"
        image.save(out/output_name,"PNG",optimize=True,compress_level=6)
        manifest={
            "schema":SCHEMA,"provider":"CPTEC / INPE","product":"PPI Z",
            "sourceUrl":src,"sourceFile":fname,
            "radar":{"code":radar_code,"name":RADAR_CODES.get(radar_code,radar_code),
                     "latitude":scalar(ds,("latitude","lat","radar_lat")),
                     "longitude":scalar(ds,("longitude","lon","radar_lon"))},
            "reflectivity":{"variable":ref_name,
                            "units":nc_text(getattr(ref,"units","")) or "dBZ",
                            "elevationRequestedDeg":args.elevation,
                            "elevationSelectedDeg":float(elevs[sweep]),
                            "sweepIndex":sweep,
                            "minDbz":float(np.nanmin(z)) if np.isfinite(z).any() else None,
                            "maxDbz":float(np.nanmax(z)) if np.isfinite(z).any() else None},
            "geometry":{"azimuthCount":int(z.shape[0]),"rangeGateCount":int(z.shape[1]),
                        "maxRangeKm":float(np.nanmax(rng)),
                        "outputWidth":max(256,args.width),"outputHeight":max(256,args.height)},
            "output":output_name,
            "notes":["PPI Z calculado diretamente da refletividade do NetCDF volumétrico.",
                     "Não é convertido de CAPPI ou PNG.",
                     "Valores inválidos e abaixo de 5 dBZ são transparentes."]
        }
    (out/"manifest.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"source":src,"variable":ref_name,"elevationDeg":float(elevs[sweep]),"output":output_name},ensure_ascii=False))

if __name__=="__main__":
    raise SystemExit(main())
