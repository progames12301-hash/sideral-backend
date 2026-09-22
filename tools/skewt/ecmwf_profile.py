from __future__ import annotations
import argparse, datetime as dt, json, math, os
from pathlib import Path
from ecmwf.opendata import Client
from eccodes import codes_grib_new_from_file, codes_get, codes_grib_find_nearest, codes_release

LEVELS=[1000,925,850,700,600,500,400,300,250,200,150,100,50,10]
PL_PARAMS=['t','r','u','v','w','gh']
SFC_PARAMS=['sp','2t','2d','10u','10v']

def atomic(client,target,**req):
    part=target.with_suffix(target.suffix+'.part')
    part.unlink(missing_ok=True)
    client.retrieve(target=str(part),**req)
    part.replace(target)

def read_fields(path,lat,lon):
    out={}
    with path.open('rb') as f:
        while True:
            gid=codes_grib_new_from_file(f)
            if gid is None: break
            try:
                short=str(codes_get(gid,'shortName'))
                try: level=int(codes_get(gid,'level'))
                except Exception: level=0
                n=codes_grib_find_nearest(gid,lat,lon)[0]
                out[(short,level)]=(float(getattr(n,'value')),float(getattr(n,'lat',lat)),float(getattr(n,'lon',lon)))
            finally: codes_release(gid)
    return out

def finite(x):
    try:
        x=float(x); return x if math.isfinite(x) else None
    except Exception: return None

def snap025(x): return math.floor(x*4+0.5)/4

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--lat',type=float,required=True); ap.add_argument('--lon',type=float,required=True); ap.add_argument('--fh',type=int,default=0); ap.add_argument('--cycle',choices=['00','06','12','18']); ap.add_argument('--cache',default='/tmp/sideral-skewt-profile'); args=ap.parse_args()
    lat,lon=snap025(args.lat),snap025(args.lon)
    if not(-34<=lat<=6 and -75<=lon<=-33): raise SystemExit('coordenada fora do domínio brasileiro')
    if args.fh<0 or args.fh>72 or args.fh%3: raise SystemExit('forecast_hour inválido')
    root=Path(args.cache); root.mkdir(parents=True,exist_ok=True)
    client=Client(source=os.getenv('ECMWF_SOURCE','aws'),model='ifs',resol='0p25',maximum_retries=8,retry_after=(10,90,2),use_server_retry_after=True)
    probe=root/'probe.grib2'; kw={'type':'fc','step':args.fh,'levtype':'pl','levelist':850,'param':'t'}
    if args.cycle: kw.update(date=dt.datetime.utcnow().strftime('%Y-%m-%d'),time=int(args.cycle))
    result=client.retrieve(target=str(probe),**kw); run_dt=result.datetime.replace(tzinfo=None); probe.unlink(missing_ok=True)
    tag=f'{run_dt:%Y%m%d%H}_f{args.fh:03d}_{lat:+07.2f}_{lon:+07.2f}'.replace('+','p').replace('-','m').replace('.','p')
    out=root/tag; out.mkdir(parents=True,exist_ok=True); payload=out/'profile.json'
    if payload.is_file() and payload.stat().st_size>100: print(payload.read_text()); return
    oro=out/'oro.grib2'
    if not oro.exists(): atomic(client,oro,date=run_dt.strftime('%Y-%m-%d'),time=run_dt.hour,type='fc',stream='oper',step=0,levtype='sfc',param='z')
    of=read_fields(oro,lat,lon); pl=out/'pl.grib2'; sf=out/'sfc.grib2'; common=dict(date=run_dt.strftime('%Y-%m-%d'),time=run_dt.hour,type='fc',stream='oper',step=args.fh)
    if not pl.exists(): atomic(client,pl,**common,levtype='pl',levelist=LEVELS,param=PL_PARAMS)
    if not sf.exists(): atomic(client,sf,**common,levtype='sfc',param=SFC_PARAMS)
    fields=read_fields(pl,lat,lon); sflds=read_fields(sf,lat,lon); sflds.update(of)
    sp=sflds.get(('sp',0),(None,0,0))[0]; t2=sflds.get(('2t',0),(None,0,0))[0]; td2=sflds.get(('2d',0),(None,0,0))[0]; z0=sflds.get(('z',0),(None,0,0))[0]
    psfc=float(sp)/100 if sp is not None else 1000; h0=float(z0)/9.80665 if z0 is not None else 0; rows=[]
    if t2 is not None and td2 is not None:
        u10=float(sflds.get(('10u',0),(0,0,0))[0] or 0)*1.94384449244; v10=float(sflds.get(('10v',0),(0,0,0))[0] or 0)*1.94384449244
        rows.append({'pressure_hpa':psfc,'height_m':h0,'temperature_c':float(t2)-273.15,'dewpoint_c':float(td2)-273.15,'wind_u_kt':u10,'wind_v_kt':v10,'omega_pa_s':None})
    for lev in LEVELS:
        if lev>psfc+1: continue
        t=fields.get(('t',lev)); r=fields.get(('r',lev)); gh=fields.get(('gh',lev)); u=fields.get(('u',lev)); v=fields.get(('v',lev)); w=fields.get(('w',lev))
        if not all(x is not None for x in (t,r,gh,u,v)): continue
        tc=float(t[0])-273.15; rh=max(.1,min(100,float(r[0]))); a,b=17.625,243.04; gamma=math.log(rh/100)+(a*tc)/(b+tc); td=b*gamma/(a-gamma)
        rows.append({'pressure_hpa':float(lev),'height_m':float(gh[0]),'temperature_c':tc,'dewpoint_c':td,'wind_u_kt':float(u[0])*1.94384449244,'wind_v_kt':float(v[0])*1.94384449244,'omega_pa_s':finite(w[0]) if w is not None else None})
    rows.sort(key=lambda z:z['pressure_hpa'],reverse=True)
    if len(rows)<5: raise SystemExit(f'perfil insuficiente: {len(rows)} níveis')
    valid=run_dt+dt.timedelta(hours=args.fh)
    outj={'schema':'sideral-skewt-ecmwf-native-v1','model':'ECMWF IFS','resolution':'0.25°','renderer':'browser-canvas','browser_rendering':True,'latitude':args.lat,'longitude':args.lon,'grid_latitude':lat,'grid_longitude':lon,'run_utc':run_dt.strftime('%Y-%m-%d %HZ'),'forecast_hour':args.fh,'valid_utc':valid.strftime('%Y-%m-%d %H:%MZ'),'profile':rows}
    payload.write_text(json.dumps(outj,separators=(',',':')),encoding='utf-8'); print(payload.read_text())
if __name__=='__main__': main()
