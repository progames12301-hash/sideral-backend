from __future__ import annotations
import datetime as dt, math
from typing import Any
import server_legacy as legacy

def safe_float(value: Any) -> float | None:
    return legacy.inmet_safe_float(value)

def as_iso(value: Any) -> str | None:
    if value is None or value == "": return None
    if isinstance(value, (int,float)) and math.isfinite(float(value)):
        try: return dt.datetime.fromtimestamp(float(value),tz=dt.timezone.utc).isoformat().replace('+00:00','Z')
        except (OverflowError,OSError,ValueError): return None
    text=str(value).strip()
    if not text: return None
    if text.endswith('Z'): return text
    try:
        parsed=dt.datetime.fromisoformat(text.replace('Z','+00:00'))
        if parsed.tzinfo is None: parsed=parsed.replace(tzinfo=dt.timezone.utc)
        return parsed.astimezone(dt.timezone.utc).isoformat().replace('+00:00','Z')
    except ValueError: return text

def age_minutes(value: str | None) -> int | None:
    if not value: return None
    try:
        parsed=dt.datetime.fromisoformat(value.replace('Z','+00:00'))
        if parsed.tzinfo is None: parsed=parsed.replace(tzinfo=dt.timezone.utc)
        return max(0,int((dt.datetime.now(dt.timezone.utc)-parsed.astimezone(dt.timezone.utc)).total_seconds()//60))
    except (ValueError,TypeError): return None

def station(*, network:str, code:str, name:str, latitude:float, longitude:float, uf=None, city=None, altitude=None,
            kind='meteorologica', status=None, observed_at=None, temperature=None, humidity=None, pressure=None,
            dewpoint=None, wind_speed=None, wind_gust=None, wind_direction=None, rain_1h=None, rain_24h=None,
            radiation=None, river_level=None, extra=None) -> dict[str,Any]:
    return {'id':f'{network}:{code}','network':network,'source':network,'code':code,'name':name or code,'city':city,'uf':uf,
            'lat':round(float(latitude),6),'lon':round(float(longitude),6),'altitude':altitude,'kind':kind,'status':status,
            'observedAt':observed_at,'ageMinutes':age_minutes(observed_at),'temperature':temperature,'humidity':humidity,
            'pressure':pressure,'dewpoint':dewpoint,'windSpeed':wind_speed,'windGust':wind_gust,'windDirection':wind_direction,
            'rain1h':rain_1h,'rain24h':rain_24h,'radiation':radiation,'riverLevel':river_level,'extra':extra or {}}
