from __future__ import annotations
import datetime as dt
from typing import Any
import server_legacy as legacy
from stations_common import safe_float, station
INMET_BULK_URL='https://apitempo.inmet.gov.br/estacao/dados/{date}'

def load() -> list[dict[str,Any]]:
    catalog=legacy.get_inmet_station_catalog(); now=dt.datetime.now(dt.timezone.utc); rows=[]
    for date in (now.date(),(now-dt.timedelta(days=1)).date()):
        data=legacy.fetch_inmet_json(INMET_BULK_URL.format(date=date.isoformat()),timeout=40)
        if isinstance(data,list): rows.extend(x for x in data if isinstance(x,dict))
    latest={}; rain24={}; seen_rain=set(); cutoff=now-dt.timedelta(hours=24)
    for row in rows:
        code=str(row.get('CD_ESTACAO') or '').upper().strip(); measured=legacy.inmet_record_datetime_utc(row)
        if not code: continue
        if measured and measured>=cutoff:
            rain=safe_float(row.get('CHUVA'))
            if rain is not None and 0<=rain<1000: rain24[code]=rain24.get(code,0.0)+rain; seen_rain.add(code)
        if measured and (code not in latest or measured>latest[code][0]): latest[code]=(measured,row)
    out=[]
    for meta in catalog:
        if not isinstance(meta,dict): continue
        code=str(meta.get('CD_ESTACAO') or '').upper().strip(); lat=safe_float(meta.get('VL_LATITUDE')); lon=safe_float(meta.get('VL_LONGITUDE'))
        if not code or lat is None or lon is None or not (-35.8<=lat<=6.8 and -75.5<=lon<=-30): continue
        measured,obs=latest.get(code,(None,{})); wind=safe_float(obs.get('VEN_VEL')); gust=safe_float(obs.get('VEN_RAJ'))
        out.append(station(network='INMET',code=code,name=str(meta.get('DC_NOME') or code).strip(),latitude=lat,longitude=lon,
            uf=str(meta.get('SG_ESTADO') or '').upper() or None,altitude=safe_float(meta.get('VL_ALTITUDE')),
            status=str(meta.get('CD_SITUACAO') or '').strip() or None,observed_at=measured.isoformat().replace('+00:00','Z') if measured else None,
            temperature=safe_float(obs.get('TEM_INS')),humidity=safe_float(obs.get('UMD_INS')),pressure=safe_float(obs.get('PRE_INS')),
            dewpoint=safe_float(obs.get('PTO_INS')),wind_speed=round(wind*3.6,2) if wind is not None else None,
            wind_gust=round(gust*3.6,2) if gust is not None else None,wind_direction=safe_float(obs.get('VEN_DIR')),
            rain_1h=safe_float(obs.get('CHUVA')),rain_24h=round(rain24[code],2) if code in seen_rain else None,
            radiation=safe_float(obs.get('RAD_GLO')),extra={'entity':meta.get('SG_ENTIDADE'),'stationType':meta.get('TP_ESTACAO')}))
    return out
