from __future__ import annotations
import json,re
from typing import Any
import server_legacy as legacy
from stations_common import safe_float, as_iso, station

def _payload(url:str)->dict[str,Any]:
    r=legacy.requests.get(url,headers={'User-Agent':legacy.INMET_HEADERS['User-Agent'],'Accept':'application/json,text/javascript,*/*'},timeout=40); r.raise_for_status()
    m=re.fullmatch(r'\s*estacoes\((.*)\)\s*;?\s*',r.content.decode('utf-8',errors='replace'),re.DOTALL)
    if not m: raise ValueError('CEMADEN retornou formato inesperado')
    raw=json.loads(m.group(1)); return raw[0] if isinstance(raw,list) and raw and isinstance(raw[0],dict) else {}

def _base(kind_id:int,network:str,kind:str,url:str)->list[dict[str,Any]]:
    block=_payload(url); observed=as_iso(block.get('atualizado')); out=[]; seen=set()
    for x in block.get('estacao',[]):
        if not isinstance(x,dict): continue
        try:
            if int(x.get('idtipoestacao') or 0)!=kind_id or int(x.get('status') or 0)!=0: continue
        except (TypeError,ValueError): continue
        lat=safe_float(x.get('latitude')); lon=safe_float(x.get('longitude')); code=str(x.get('codestacao') or x.get('idestacao') or '').strip()
        if lat is None or lon is None or not code or code in seen or not (-35.8<=lat<=6.8 and -75.5<=lon<=-30): continue
        seen.add(code); level=None
        if kind_id==3:
            raw=safe_float(x.get('nivel')); off=safe_float(x.get('offset'))
            level=round(max(0.0,off-raw),3) if raw is not None and off is not None and off>0 and 0<raw<35 else None
        out.append(station(network=network,code=code,name=str(x.get('nomeestacao') or ('Pluviômetro CEMADEN' if kind_id==1 else 'Estação hidrológica CEMADEN')).strip(),
            latitude=lat,longitude=lon,city=str(x.get('cidade') or '').title() or None,uf=str(x.get('uf') or '').upper() or None,
            kind=kind,status='operante',observed_at=observed,rain_24h=safe_float(x.get('acumulado')),river_level=level,
            extra={'cemadenId':str(x.get('idestacao') or code),'attentionLevel':safe_float(x.get('cotaatencao')),'alertLevel':safe_float(x.get('cotaalerta')),'overflowLevel':safe_float(x.get('cotatransbordamento'))}))
    return out

def load_pluvio(): return _base(1,'CEMADEN','pluviometro',legacy.CEMADEN_PLUVIOMETERS_URL)
def load_hydro(): return _base(3,'CEMADEN-HIDRO','hidrologica',legacy.CEMADEN_HYDROLOGICAL_URL)
