from __future__ import annotations

import datetime as dt
import threading
import time
from typing import Any

import server_legacy as legacy
from stations_common import safe_float, station

INMET_BULK_URL = "https://apitempo.inmet.gov.br/estacao/dados/{date}"
INMET_AUTO_STATIONS_URL = "https://apitempo.inmet.gov.br/estacoes/T"
CACHE_TTL = 300
STALE_TTL = 3 * 3600
_lock = threading.RLock()
_cache: dict[str, Any] = {"saved_at": 0.0, "rows": None, "last_error": None}
_catalog_cache: dict[str, Any] = {"saved_at": 0.0, "rows": None}


def _valid_number(value: Any) -> float | None:
    value = safe_float(value)
    if value is None or abs(value) >= 9990:
        return None
    return value


def _fetch_json(url: str, timeout: int = 40) -> Any:
    """Use the backend's INMET client so INMET_TOKEN, headers and retries remain centralized."""
    return legacy.fetch_inmet_json(url, timeout=timeout)


def _catalog() -> list[dict[str, Any]]:
    now = time.monotonic()
    with _lock:
        if _catalog_cache["rows"] is not None and now - _catalog_cache["saved_at"] < 3600:
            return _catalog_cache["rows"]

    try:
        data = legacy.get_inmet_station_catalog()
    except Exception:
        data = _fetch_json(INMET_AUTO_STATIONS_URL, timeout=30)

    rows = [x for x in data if isinstance(x, dict)] if isinstance(data, list) else []
    if not rows:
        raise RuntimeError("INMET: catálogo de estações automáticas vazio")

    with _lock:
        _catalog_cache.update(saved_at=time.monotonic(), rows=rows)
    return rows


def _download_observations(now: dt.datetime) -> list[dict[str, Any]]:
    """Fetch several recent UTC dates because INMET observations can lag a day boundary."""
    rows: list[dict[str, Any]] = []
    errors: list[str] = []

    # Current day first, then two previous UTC days. Never treat an empty/204
    # response as a valid replacement for a previous successful cache.
    for days_back in range(3):
        date = (now - dt.timedelta(days=days_back)).date().isoformat()
        try:
            data = _fetch_json(INMET_BULK_URL.format(date=date), timeout=45)
            if isinstance(data, list):
                rows.extend(x for x in data if isinstance(x, dict))
            elif data not in (None, ""):
                errors.append(f"{date}: resposta INMET inesperada")
        except Exception as exc:
            errors.append(f"{date}: {type(exc).__name__}: {exc}")

    if rows:
        with _lock:
            _cache.update(saved_at=time.monotonic(), rows=rows, last_error="; ".join(errors) or None)
        return rows

    with _lock:
        old = _cache.get("rows")
        age = now.timestamp() - float(_cache.get("saved_at", 0.0)) if old else float("inf")
        if old and age <= STALE_TTL:
            _cache["last_error"] = "; ".join(errors) or "INMET sem observações novas"
            return old

    raise RuntimeError("INMET não retornou observações recentes" + (": " + "; ".join(errors) if errors else ""))


def _record_time(row: dict[str, Any]) -> dt.datetime | None:
    try:
        return legacy.inmet_record_datetime_utc(row)
    except Exception:
        return None


def load() -> list[dict[str, Any]]:
    now = dt.datetime.now(dt.timezone.utc)
    catalog = _catalog()

    with _lock:
        cached = _cache.get("rows")
        saved = float(_cache.get("saved_at", 0.0))
    if cached is not None and time.monotonic() - saved < CACHE_TTL:
        rows = cached
    else:
        rows = _download_observations(now)

    latest: dict[str, tuple[dt.datetime, dict[str, Any]]] = {}
    rain24: dict[str, float] = {}
    cutoff = now - dt.timedelta(hours=24)

    for row in rows:
        code = str(row.get("CD_ESTACAO") or "").upper().strip()
        measured = _record_time(row)
        if not code or measured is None:
            continue
        if measured >= cutoff:
            rain = _valid_number(row.get("CHUVA"))
            if rain is not None and 0 <= rain < 1000:
                rain24[code] = rain24.get(code, 0.0) + rain
        if code not in latest or measured > latest[code][0]:
            latest[code] = (measured, row)

    out: list[dict[str, Any]] = []
    for meta in catalog:
        code = str(meta.get("CD_ESTACAO") or "").upper().strip()
        lat = _valid_number(meta.get("VL_LATITUDE"))
        lon = _valid_number(meta.get("VL_LONGITUDE"))
        if not code or lat is None or lon is None or not (-35.8 <= lat <= 6.8 and -75.5 <= lon <= -30):
            continue

        measured, obs = latest.get(code, (None, {}))
        wind = _valid_number(obs.get("VEN_VEL"))
        gust = _valid_number(obs.get("VEN_RAJ"))
        rain = _valid_number(obs.get("CHUVA"))

        out.append(station(
            network="INMET",
            code=code,
            name=str(meta.get("DC_NOME") or code).strip(),
            latitude=lat,
            longitude=lon,
            uf=str(meta.get("SG_ESTADO") or "").upper() or None,
            altitude=_valid_number(meta.get("VL_ALTITUDE")),
            status=str(meta.get("CD_SITUACAO") or "").strip() or None,
            observed_at=measured.isoformat().replace("+00:00", "Z") if measured else None,
            temperature=_valid_number(obs.get("TEM_INS")),
            humidity=_valid_number(obs.get("UMD_INS")),
            pressure=_valid_number(obs.get("PRE_INS")),
            dewpoint=_valid_number(obs.get("PTO_INS")),
            wind_speed=round(wind * 3.6, 2) if wind is not None else None,
            wind_gust=round(gust * 3.6, 2) if gust is not None else None,
            wind_direction=_valid_number(obs.get("VEN_DIR")),
            rain_1h=rain,
            rain_24h=round(rain24[code], 2) if code in rain24 else None,
            radiation=_valid_number(obs.get("RAD_GLO")),
            extra={
                "entity": meta.get("SG_ENTIDADE"),
                "stationType": meta.get("TP_ESTACAO"),
                "dataSource": "INMET",
            },
        ))

    # Keep the station catalogue even when some stations have no observation.
    # Never fabricate values for missing sensors/communications.
    return out
