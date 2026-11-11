from __future__ import annotations
import datetime as dt,gzip,json,threading,time
from urllib.parse import parse_qs,urlparse
from concurrent.futures import ThreadPoolExecutor,as_completed
from stations_inmet import load as load_inmet
from stations_cemaden import load_pluvio,load_hydro
from stations_ana import load as load_ana
from stations_extra import load_dcrs,load_metar,load_redemet
TTL=300; STALE=6*3600; lock=threading.RLock(); cache={'saved_at':0.0,'payload':None,'json':None,'gzip':None}; sources_cache={}

def cached_source(name,ttl,loader):
    now=time.monotonic()
    with lock:
        old=sources_cache.get(name)
        if old and now-old['saved_at']<ttl: return old['stations'],True,None
    try:
        stations=loader()
        with lock: sources_cache[name]={'saved_at':now,'stations':stations}
        return stations,False,None
    except Exception as exc:
        with lock: old=sources_cache.get(name)
        if old and now-old['saved_at']<STALE: return old['stations'],True,f'{type(exc).__name__}: {exc}'
        return [],False,f'{type(exc).__name__}: {exc}'

def _dedupe_airports(rows):
    """NOAA e REDEMET podem representar o mesmo aeródromo pelo mesmo ICAO.

    Mantemos o METAR quando existe observação corrente e só conservamos o
    catálogo REDEMET para ICAOs que não vieram no lote NOAA atual.
    """
    metar_codes={str(x.get('code') or '').upper() for x in rows if x.get('network')=='METAR'}
    return [x for x in rows if not (x.get('network')=='REDEMET' and str(x.get('code') or '').upper() in metar_codes)]

def build(force=False):
    now=time.monotonic()
    with lock:
        if cache['payload'] is not None and not force and now-cache['saved_at']<TTL:
            out=dict(cache['payload']); out['cache']=True; out['cacheAgeSeconds']=int(now-cache['saved_at']); return out
    specs=[
        ('INMET',600,load_inmet),
        ('CEMADEN',300,load_pluvio),
        ('CEMADEN-HIDRO',300,load_hydro),
        ('DCRS',120,load_dcrs),
        ('METAR',300,load_metar),
        ('ANA',1800,load_ana),
        ('REDEMET',600,load_redemet),
    ]
    all_rows=[]; meta={}
    with ThreadPoolExecutor(max_workers=len(specs),thread_name_prefix='stations') as pool:
        futures={pool.submit(cached_source,*spec):spec[0] for spec in specs}
        for future in as_completed(futures):
            name=futures[future]
            try: rows,from_cache,error=future.result()
            except Exception as exc: rows,from_cache,error=[],False,f'{type(exc).__name__}: {exc}'
            all_rows.extend(rows); meta[name]={'count':len(rows),'cache':from_cache,'error':error}
    all_rows=_dedupe_airports(all_rows)
    unique={x['id']:x for x in all_rows}; stations=sorted(unique.values(),key=lambda x:(x.get('network') or '',x.get('uf') or '',x.get('name') or ''))
    counts={}; with_values=0
    for x in stations:
        counts[x['network']]=counts.get(x['network'],0)+1
        if any(x.get(k) is not None for k in ('temperature','humidity','pressure','windSpeed','windGust','rain1h','rain24h','riverLevel')): with_values+=1
    payload={'status':True,'provider':'Estações Sideral','updatedAt':dt.datetime.now(dt.timezone.utc).isoformat().replace('+00:00','Z'),'cache':False,'count':len(stations),'withValues':with_values,'counts':counts,'sources':meta,'stations':stations,
             'credits':['INMET — Instituto Nacional de Meteorologia','CEMADEN/MCTI — rede pluviométrica e hidrológica','Defesa Civil do Rio Grande do Sul — Rede Hidrometeorológica','ANA — estações telemétricas do antigo Telemetria1WS (fail-soft)','NOAA Aviation Weather Center — METAR','DECEA / REDEMET — catálogo de aeródromos quando a chave está configurada']}
    raw=json.dumps(payload,ensure_ascii=False,separators=(',',':')).encode(); gz=gzip.compress(raw,compresslevel=5)
    with lock: cache.update({'saved_at':time.monotonic(),'payload':payload,'json':raw,'gzip':gz})
    return payload

def send(handler,payload):
    with lock:
        same=cache['payload'] is not None and payload.get('stations') is cache['payload'].get('stations'); raw=cache['json'] if same else None; gz=cache['gzip'] if same else None
    if raw is None: raw=json.dumps(payload,ensure_ascii=False,separators=(',',':')).encode(); gz=gzip.compress(raw,compresslevel=5)
    use_gzip='gzip' in (handler.headers.get('Accept-Encoding') or '').lower(); body=gz if use_gzip else raw
    handler.send_response(200); handler.send_header('Content-Type','application/json; charset=utf-8'); handler.send_header('Content-Length',str(len(body))); handler.send_header('X-Sideral-Stations',str(payload.get('count',0)))
    if use_gzip: handler.send_header('Content-Encoding','gzip'); handler.send_header('Vary','Accept-Encoding')
    handler.end_headers()
    try: handler.wfile.write(body)
    except (BrokenPipeError,ConnectionResetError): pass

def handle(handler):
    q=parse_qs(urlparse(handler.path).query); send(handler,build(q.get('refresh',['0'])[0].lower() in {'1','true','yes'}))
