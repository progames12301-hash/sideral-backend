"""Render official SMN Argentina WRF NetCDF fields as PNG for Sideral."""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from io import BytesIO
import threading, time
import h5py
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap, PowerNorm
import numpy as np
import requests

_BUCKETS = ("https://smn-ar-wrf.s3.amazonaws.com", "https://smn-ar-wrf.s3-us-west-2.amazonaws.com")
_FIELDS = {
 "PSFC": ("Pressão à superfície", "hPa", 800, 1040, ["#252a69","#3f58aa","#258fc2","#36b68a","#e7d749","#ef853c","#c52f43"]),
 "T2": ("Temperatura a 2 m", "°C", -5, 40, ["#2d3896","#288fd1","#3fc08b","#efdc41","#ef762f","#c52330"]),
 "PP": ("Precipitação", "mm", 0, 30, ["#151d31","#286ecf","#26c7e1","#50d27c","#f3e13e","#f57b2e","#ce222f"]),
 "HR2": ("Umidade relativa a 2 m", "%", 0, 100, ["#913830","#d58337","#e0d150","#41b778","#2498d1","#483a9f"]),
 "magViento10": ("Vento a 10 m", "m/s", 0, 35, ["#232d46","#2f7fc4","#3dc7af","#f2da42","#f0732d","#c72335"]),
 "Freezing_level": ("Nível de 0 °C", "m", 0, 5000, ["#30429f","#26a1cf","#38bd84","#f3da45","#f47d2b","#c72134"]),
}
_LOCK = threading.RLock()
_CACHE = {}

class SmnMapError(RuntimeError):
    pass

def _dates(cycle):
    now=datetime.now(timezone.utc); today=now.date()
    start=today if now.hour >= int(cycle)+3 else today-timedelta(days=1)
    return [start-timedelta(days=i) for i in range(4)]

def _fetch(cycle, fh):
    session=requests.Session()
    session.headers.update({"User-Agent":"SideralMeteorologia/1.0","Accept":"application/octet-stream,*/*"})
    errors=[]
    for day in _dates(cycle):
        ymd=day.strftime("%Y%m%d")
        path=f"/DATA/WRF/DET/{day:%Y/%m/%d}/{cycle}/WRFDETAR_01H_{ymd}_{cycle}_{fh:03d}.nc"
        for base in _BUCKETS:
            try:
                response=session.get(base+path, timeout=(5,18))
                if response.status_code==404:
                    errors.append(f"{ymd}:404"); continue
                response.raise_for_status(); data=response.content
                sig=data[:8]
                if len(data)<100000 or not (sig.startswith(b"\x89HDF\r\n\x1a\n") or sig.startswith((b"CDF\x01",b"CDF\x02",b"CDF\x05"))):
                    errors.append(f"{ymd}:arquivo inválido"); continue
                return data,ymd
            except Exception as exc:
                errors.append(str(exc)[:90])
    raise SmnMapError("SMN sem arquivo para este ciclo/prazo ("+", ".join(errors[-4:])+").")

def _array(nc, key):
    if key not in nc: raise SmnMapError(f"Variável {key} ausente no NetCDF.")
    ds=nc[key]; value=np.asarray(ds[...]).squeeze().astype(np.float32,copy=False)
    fill=ds.attrs.get("_FillValue")
    if fill is not None:
        try: value=np.where(value==float(np.asarray(fill).reshape(-1)[0]),np.nan,value)
        except Exception: pass
    return np.where(np.isfinite(value)&(np.abs(value)<1e20)&(value>-9000),value,np.nan)

def render_smn_wrf_map(cycle="00", field="PSFC", fh=24):
    cycle=str(cycle).zfill(2)
    try: fh=int(fh)
    except (TypeError,ValueError): raise SmnMapError("Prazo inválido.")
    if cycle not in {"00","12"}: raise SmnMapError("Escolha o ciclo 00Z ou 12Z.")
    if field not in _FIELDS: raise SmnMapError("Variável inválida.")
    if not 0<=fh<=72: raise SmnMapError("Prazo disponível: F000 a F072.")
    with _LOCK:
        now=time.time()
        for key,entry in list(_CACHE.items()):
            if now-entry[0]>240: _CACHE.pop(key,None)
        cache_key=(datetime.now(timezone.utc).date().isoformat(),cycle,field,fh)
        if cache_key in _CACHE: return _CACHE[cache_key][1],_CACHE[cache_key][2]
        payload,run_date=_fetch(cycle,fh)
        try:
            with h5py.File(BytesIO(payload),"r") as nc:
                lon=_array(nc,"lon"); lat=_array(nc,"lat"); val=_array(nc,field)
            if lon.ndim!=2 or lat.ndim!=2 or val.ndim!=2 or lon.shape!=lat.shape or val.shape!=lon.shape:
                raise SmnMapError(f"Dimensões não reconhecidas: lon {lon.shape}, lat {lat.shape}, {field} {val.shape}.")
            if field=="PSFC" and np.nanmedian(val)>2000: val=val/100.0
            if field=="T2" and np.nanmedian(val)>100: val=val-273.15
            if field=="HR2" and np.nanmax(val)<=1.5: val=val*100.0
        except SmnMapError: raise
        except Exception as exc: raise SmnMapError("Falha ao ler NetCDF do SMN: "+str(exc)[:200]) from exc
        valid=np.isfinite(lon)&np.isfinite(lat)&np.isfinite(val)&(lon>-180)&(lon<180)&(lat>-90)&(lat<90)
        if int(valid.sum())<100: raise SmnMapError("Campo SMN sem dados válidos.")
        label,unit,vmin,vmax,colors=_FIELDS[field]
        if field=="PP":
            wet=val[valid&(val>0)]
            if wet.size: vmax=min(30.0,max(1.0,float(np.nanpercentile(wet,99))))
        cmap=LinearSegmentedColormap.from_list("sideral_smn",colors,N=256)
        fig=plt.figure(figsize=(12,8),dpi=100,facecolor="#15181e")
        ax=fig.add_axes([.04,.08,.83,.84],facecolor="#15181e")
        norm=PowerNorm(gamma=.65,vmin=.00001,vmax=vmax) if field=="PP" else None
        mesh=ax.pcolormesh(lon,lat,np.ma.masked_where(~valid,val),cmap=cmap,vmin=None if norm else vmin,vmax=None if norm else vmax,norm=norm,shading="auto",rasterized=True)
        ax.set_xlim(float(np.nanpercentile(lon[valid],.2)),float(np.nanpercentile(lon[valid],99.8)))
        ax.set_ylim(float(np.nanpercentile(lat[valid],.2)),float(np.nanpercentile(lat[valid],99.8)))
        ax.set_xlabel("Longitude",color="#cbd4e0",fontsize=8); ax.set_ylabel("Latitude",color="#cbd4e0",fontsize=8)
        ax.tick_params(colors="#aeb8c7",labelsize=7); ax.grid(color="#aeb8c7",alpha=.18,linewidth=.45)
        ax.set_title(f"WRF-SMN · {label} · F{fh:03d} · GFS {cycle}Z · {run_date}",color="#f3f5f8",fontsize=12,pad=10,weight="bold")
        cax=fig.add_axes([.895,.16,.018,.65]); cb=fig.colorbar(mesh,cax=cax); cb.set_label(unit,color="#edf1f7",fontsize=9); cb.ax.tick_params(colors="#edf1f7",labelsize=8)
        out=BytesIO(); fig.savefig(out,format="png",dpi=100,facecolor=fig.get_facecolor()); plt.close(fig); png=out.getvalue()
        if len(png)<10000: raise SmnMapError("Imagem SMN incompleta.")
        _CACHE[cache_key]=(time.time(),png,run_date)
        if len(_CACHE)>48: _CACHE.pop(next(iter(_CACHE)))
        return png,run_date
