from __future__ import annotations
import math
from typing import Any
import server_legacy as legacy
from stations_common import safe_float, as_iso, station
DCRS='https://redehidrometeorologica.defesacivil.rs.gov.br/graphql'; AWC='https://aviationweather.gov/api/data/metar'

def deep(root:Any,*path:str):
    v=root
    for key in path:
        if not isinstance(v,dict): return None
        v=v.get(key)
    if isinstance(v,dict) and 'value' in v: v=v.get('value')
    return safe_float(v)

def load_dcrs():
    q='''query SideralStations { tags_data(clients:["casa-militar-defesa-civil-rs"], filters:{localizacao:[{codigos:["43"],tipo:UNIDADE_FEDERATIVA}]}) { qualle_meteorologia { codigo name { prefix general local } timestamp position { bacia latitude longitude regiao altitude } data { rio { rio_nome { value } rio_nivel { value } } chuva { acumulado { h001 { value } h024 { value } } } temperatura { atual { value } } umidade { atual { value } } pressaoatmos { atual { value } } radiacaosolar { atual { value } } vento { velocidade_media { value } velocidade_maxima { value } direcao { value } } } } } }'''
    r=legacy.requests.post(DCRS,json={'query':q},headers={'User-Agent':'SideralMeteorologia/1.0','Accept':'application/json'},timeout=35); r.raise_for_status(); p=r.json()
    if p.get('errors'): raise ValueError(str(p['errors'][0].get('message') or p['errors'][0]))
    tags=(p.get('data') or {}).get('tags_data'); groups=tags if isinstance(tags,list) else [tags] if isinstance(tags,dict) else []; rows=[]
    for g in groups:
        v=g.get('qualle_meteorologia') if isinstance(g,dict) else None
        if isinstance(v,list): rows.extend(x for x in v if isinstance(x,dict))
        elif isinstance(v,dict): rows.append(v)
    out=[]; seen=set()
    for row in rows:
        code=str(row.get('codigo') or '').strip(); pos=row.get('position') or {}; lat=safe_float(pos.get('latitude')); lon=safe_float(pos.get('longitude'))
        if not code or code in seen or lat is None or lon is None: continue
        seen.add(code); names=row.get('name') or {}; data=row.get('data') or {}; rio=data.get('rio') if isinstance(data,dict) else None; rio_name=None
        if isinstance(rio,dict) and isinstance(rio.get('rio_nome'),dict): rio_name=rio['rio_nome'].get('value')
        solar=deep(data,'radiacaosolar','atual'); solar_kjm2=round(solar*3600,3) if solar is not None else None
        out.append(station(network='DCRS',code=code,name=str(names.get('local') or names.get('general') or names.get('prefix') or code).strip(),latitude=lat,longitude=lon,
            uf='RS',city=str(pos.get('regiao') or '').strip() or None,altitude=safe_float(pos.get('altitude')),kind='hidrometeorologica',status='operante',observed_at=as_iso(row.get('timestamp')),
            temperature=deep(data,'temperatura','atual'),humidity=deep(data,'umidade','atual'),pressure=deep(data,'pressaoatmos','atual'),wind_speed=deep(data,'vento','velocidade_media'),
            wind_gust=deep(data,'vento','velocidade_maxima'),wind_direction=deep(data,'vento','direcao'),rain_1h=deep(data,'chuva','acumulado','h001'),rain_24h=deep(data,'chuva','acumulado','h024'),
            radiation=solar_kjm2,river_level=deep(data,'rio','rio_nivel'),extra={'basin':pos.get('bacia'),'river':rio_name}))
    if not out: raise ValueError('Defesa Civil RS não retornou estações')
    return out

def _rh(t,d):
    if t is None or d is None: return None
    try:
        a,b=17.625,243.04; return round(max(0,min(100,100*math.exp((a*d)/(b+d)-(a*t)/(b+t)))),1)
    except (ValueError,ZeroDivisionError,OverflowError): return None

def load_metar():
    r=legacy.requests.get(AWC,params={'bbox':'-35.8,-75.5,6.8,-30.0','format':'json'},headers={'User-Agent':'SideralMeteorologia/1.0 (station-map)','Accept':'application/json'},timeout=35)
    if r.status_code==204: return []
    r.raise_for_status(); raw=r.json()
    if not isinstance(raw,list): raise ValueError('Aviation Weather retornou formato inesperado')
    out=[]; seen=set()
    for x in raw:
        if not isinstance(x,dict): continue
        code=str(x.get('icaoId') or '').upper().strip(); lat=safe_float(x.get('lat')); lon=safe_float(x.get('lon'))
        if not code or code in seen or lat is None or lon is None or not (-35.8<=lat<=6.8 and -75.5<=lon<=-30): continue
        seen.add(code); t=safe_float(x.get('temp')); d=safe_float(x.get('dewp')); ws=safe_float(x.get('wspd')); wg=safe_float(x.get('wgst'))
        out.append(station(network='METAR',code=code,name=str(x.get('name') or code).strip(),latitude=lat,longitude=lon,altitude=safe_float(x.get('elev')),kind='aeronautica',status=str(x.get('fltCat') or '').upper() or None,
            observed_at=as_iso(x.get('obsTime') if x.get('obsTime') is not None else x.get('reportTime')),temperature=t,humidity=_rh(t,d),pressure=safe_float(x.get('slp')) or safe_float(x.get('altim')),dewpoint=d,
            wind_speed=round(ws*1.852,2) if ws is not None else None,wind_gust=round(wg*1.852,2) if wg is not None else None,wind_direction=safe_float(x.get('wdir')),extra={'rawMetar':x.get('rawOb'),'flightCategory':x.get('fltCat')}))
    return out

def _redemet_json(path:str,params:dict[str,str]|None=None)->dict[str,Any]:
    key=str(getattr(legacy,'REDEMET_API_KEY','') or '').strip()
    if not key: raise RuntimeError('REDEMET_API_KEY não configurada no Render')
    query=dict(params or {}); query['api_key']=key
    r=legacy.requests.get(f"{legacy.REDEMET_API_URL}{path}",params=query,headers={'X-Api-Key':key,'User-Agent':'SideralMeteorologia/1.0 (station-map)','Accept':'application/json'},timeout=30)
    r.raise_for_status(); payload=r.json()
    if not isinstance(payload,dict) or payload.get('status') is not True: raise ValueError('REDEMET retornou resposta inválida')
    return payload

def load_redemet():
    """Lista aeródromos brasileiros em duas chamadas, sem consultar um a um.

    As observações METAR continuam vindo do NOAA/AWC em lote. O agregador remove
    duplicatas pelo ICAO e usa REDEMET principalmente para aeródromos que não
    apareceram no lote METAR atual e para completar metadados.
    """
    status_payload=_redemet_json('/aerodromos/status/pais/BRASIL')
    detail_payload=_redemet_json('/aerodromos/',{'pais':'BRASIL'})
    status_rows=status_payload.get('data'); detail_rows=detail_payload.get('data')
    if not isinstance(status_rows,list): raise ValueError('Catálogo REDEMET em formato inesperado')
    details={str(x.get('cod') or '').upper().strip():x for x in (detail_rows if isinstance(detail_rows,list) else []) if isinstance(x,dict) and x.get('cod')}
    out=[]; seen=set()
    for row in status_rows:
        if not isinstance(row,list) or len(row)<5: continue
        code=str(row[0] or '').upper().strip(); lat=safe_float(row[2]); lon=safe_float(row[3])
        if len(code)!=4 or not code.isalnum() or code in seen or lat is None or lon is None or not (-35.8<=lat<=6.8 and -75.5<=lon<=-30): continue
        seen.add(code); detail=details.get(code,{})
        city=str(detail.get('cidade') or '').strip() or None; uf=None
        if city and '/' in city:
            tail=city.rsplit('/',1)[-1].strip().upper()
            if len(tail)==2: uf=tail; city=city.rsplit('/',1)[0].strip() or None
        raw_status=str(row[4] or '').strip().lower(); status={'g':'verde','green':'verde','verde':'verde','y':'amarelo','yellow':'amarelo','amarelo':'amarelo','r':'vermelho','red':'vermelho','vermelho':'vermelho'}.get(raw_status,'cinza')
        out.append(station(network='REDEMET',code=code,name=str(detail.get('nome') or row[1] or code).strip(),latitude=lat,longitude=lon,uf=uf,city=city,
            altitude=safe_float(detail.get('altitude_metros')),kind='aeronautica',status=status,extra={'provider':'DECEA / REDEMET'}))
    return out
