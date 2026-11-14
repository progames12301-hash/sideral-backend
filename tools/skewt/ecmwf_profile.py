from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import time
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

# Open-Meteo is the ONLY upstream used by this provider.
# Pressure-level IFS 0.25° fields are requested from Open-Meteo's ECMWF API.
# No direct ECMWF OpenData/eccodes retrieval is used by the Skew-T provider.

PRESSURE_LEVELS = [1000, 925, 850, 700, 600, 500, 400, 300, 250, 200, 150, 100, 50]
USER_AGENT = "SideralMeteorologia/2.0 (ECMWF Skew-T via Open-Meteo)"
OPEN_METEO_URL = "https://api.open-meteo.com/v1/ecmwf"


def finite(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def get_json(params: dict, retries: int = 4) -> dict:
    full = OPEN_METEO_URL + "?" + urlencode(params, doseq=True)
    last = None
    for attempt in range(retries):
        try:
            req = Request(full, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
            with urlopen(req, timeout=45) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if not isinstance(payload, dict):
                raise RuntimeError("Open-Meteo retornou JSON inválido")
            if payload.get("error"):
                raise RuntimeError(str(payload.get("reason", "Erro da API Open-Meteo")))
            return payload
        except Exception as exc:
            last = exc
            if attempt + 1 < retries:
                time.sleep(2 ** attempt)
    raise RuntimeError(f"Open-Meteo indisponível: {last}")


def build_hourly_variables():
    names = ["temperature_2m", "dewpoint_2m", "surface_pressure", "wind_speed_10m", "wind_direction_10m"]
    for lev in PRESSURE_LEVELS:
        names += [
            f"temperature_{lev}hPa",
            f"relative_humidity_{lev}hPa",
            f"wind_speed_{lev}hPa",
            f"wind_direction_{lev}hPa",
            f"geopotential_height_{lev}hPa",
        ]
    return ",".join(names)


def parse_time(raw: str) -> dt.datetime:
    return dt.datetime.fromisoformat(raw.replace("Z", "+00:00")).replace(tzinfo=None)


def nearest_index(times: list[str], valid: dt.datetime) -> int:
    if not times:
        raise RuntimeError("Open-Meteo não retornou horários")
    best_i, best_d = 0, None
    for i, raw in enumerate(times):
        try:
            d = abs((parse_time(raw) - valid).total_seconds())
        except ValueError:
            continue
        if best_d is None or d < best_d:
            best_i, best_d = i, d
    if best_d is None or best_d > 90 * 60:
        raise RuntimeError("Não foi possível localizar o horário solicitado no Open-Meteo")
    return best_i


def wind_uv(speed_kmh, direction_deg):
    speed = finite(speed_kmh)
    direction = finite(direction_deg)
    if speed is None or direction is None:
        return None, None
    speed_ms = speed / 3.6
    rad = math.radians(direction)
    # Meteorological direction is FROM; U/V are vector components TOWARD east/north.
    return -speed_ms * math.sin(rad), -speed_ms * math.cos(rad)


def fetch_open_meteo(lat: float, lon: float):
    params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": build_hourly_variables(),
        "temperature_unit": "celsius",
        "wind_speed_unit": "kmh",
        "timeformat": "iso8601",
        "forecast_days": 15,
        "cell_selection": "nearest",
    }
    return get_json(params)


def extract(payload: dict, fh: int):
    hourly = payload.get("hourly") or {}
    times = hourly.get("time") or []
    # The live Open-Meteo ECMWF endpoint exposes the latest continuously updated
    # forecast. F000 is the first available forecast hour on its time axis.
    first = parse_time(times[0])
    valid_target = first + dt.timedelta(hours=fh)
    i = nearest_index(times, valid_target)

    def value(name):
        arr = hourly.get(name)
        return finite(arr[i]) if isinstance(arr, list) and i < len(arr) else None

    rows = []
    sp = value("surface_pressure")
    t2 = value("temperature_2m")
    td2 = value("dewpoint_2m")
    s_speed = value("wind_speed_10m")
    s_dir = value("wind_direction_10m")
    su, sv = wind_uv(s_speed, s_dir)

    if sp is not None and t2 is not None and td2 is not None:
        rows.append({
            "pressure_hpa": sp,
            "height_m": None,
            "temperature_c": t2,
            "dewpoint_c": td2,
            "wind_u_kt": su * 1.94384449244 if su is not None else None,
            "wind_v_kt": sv * 1.94384449244 if sv is not None else None,
            "wind_speed_kt": s_speed / 1.852 if s_speed is not None else None,
            "wind_direction_deg": s_dir,
        })

    for lev in PRESSURE_LEVELS:
        p = float(lev)
        if sp is not None and p > sp + 2:
            continue
        temp = value(f"temperature_{lev}hPa")
        rh = value(f"relative_humidity_{lev}hPa")
        speed = value(f"wind_speed_{lev}hPa")
        direction = value(f"wind_direction_{lev}hPa")
        height = value(f"geopotential_height_{lev}hPa")
        if temp is None or rh is None or speed is None or direction is None:
            continue
        u, v = wind_uv(speed, direction)
        if u is None or v is None:
            continue
        rh = max(0.1, min(100.0, rh))
        # Magnus relation: dew point from the ECMWF pressure-level T/RH supplied by Open-Meteo.
        a, b = 17.625, 243.04
        gamma = math.log(rh / 100.0) + a * temp / (b + temp)
        dew = b * gamma / (a - gamma)
        rows.append({
            "pressure_hpa": p,
            "height_m": height,
            "temperature_c": temp,
            "dewpoint_c": dew,
            "wind_u_kt": u * 1.94384449244,
            "wind_v_kt": v * 1.94384449244,
            "wind_speed_kt": speed / 1.852,
            "wind_direction_deg": direction,
        })

    rows.sort(key=lambda r: r["pressure_hpa"], reverse=True)
    if len(rows) < 6:
        raise RuntimeError(f"Perfil ECMWF/Open-Meteo insuficiente: {len(rows)} níveis")
    return rows, parse_time(times[i])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lat", type=float, required=True)
    ap.add_argument("--lon", type=float, required=True)
    ap.add_argument("--fh", "--forecast-hour", type=int, default=0)
    # Kept only for frontend/backward compatibility. Open-Meteo's live ECMWF
    # endpoint determines the latest available run itself; we never substitute
    # another source merely to honor a cycle selector.
    ap.add_argument("--cycle", choices=["00", "06", "12", "18"])
    ap.add_argument("--cache", default="/tmp/sideral-skewt-profile")
    args = ap.parse_args()

    if not (-34 <= args.lat <= 6 and -75 <= args.lon <= -33):
        raise SystemExit("coordenada fora do domínio brasileiro")
    if args.fh < 0 or args.fh > 240:
        raise SystemExit("forecast_hour inválido: use F000–F240")
    if args.fh % 3:
        raise SystemExit("forecast_hour deve ser múltiplo de 3")

    payload = fetch_open_meteo(args.lat, args.lon)
    rows, valid = extract(payload, args.fh)

    root = Path(args.cache)
    root.mkdir(parents=True, exist_ok=True)
    tag = f"{valid:%Y%m%d%H}_f{args.fh:03d}_{args.lat:+07.2f}_{args.lon:+07.2f}".replace("+", "p").replace("-", "m").replace(".", "p")
    out = root / tag
    out.mkdir(parents=True, exist_ok=True)
    payload_path = out / "profile.json"

    result = {
        "schema": "sideral-skewt-openmeteo-ecmwf-v3",
        "provider": "Open-Meteo",
        "provider_url": "https://open-meteo.com/",
        "model": "ECMWF IFS 0.25°",
        "resolution": "0.25°",
        "renderer": "browser-canvas",
        "browser_rendering": True,
        "latitude": args.lat,
        "longitude": args.lon,
        "forecast_hour": args.fh,
        "cycle_requested": args.cycle,
        "run_utc": "latest-open-meteo",
        "valid_utc": valid.strftime("%Y-%m-%d %H:%MZ"),
        "source": "Open-Meteo ECMWF API",
        "profile": rows,
    }
    payload_path.write_text(json.dumps(result, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
    print(payload_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
