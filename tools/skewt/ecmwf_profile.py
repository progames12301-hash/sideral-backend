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
# The ECMWF API exposes pressure-level IFS 0.25° fields through Open-Meteo.
# No direct ECMWF OpenData/eccodes retrieval is used here.

PRESSURE_LEVELS = [1000, 925, 850, 700, 600, 500, 400, 300, 250, 200, 150, 100, 50]
USER_AGENT = "SideralMeteorologia/2.0 (ECMWF Skew-T via Open-Meteo)"
LIVE_URL = "https://api.open-meteo.com/v1/ecmwf"
SINGLE_RUN_URL = "https://single-runs-api.open-meteo.com/v1/forecast"


def finite(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def get_json(url: str, params: dict, retries: int = 4) -> dict:
    full = url + "?" + urlencode(params, doseq=True)
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


def latest_cycle(now: dt.datetime) -> dt.datetime:
    """Latest ECMWF global cycle with normal dissemination margin."""
    now = now.replace(minute=0, second=0, microsecond=0)
    # ECMWF global cycles are 00/06/12/18 UTC. Leave a conservative
    # 5-hour margin because the run must be processed before Single Runs
    # makes it available.
    candidate = now - dt.timedelta(hours=5)
    hour = (candidate.hour // 6) * 6
    return candidate.replace(hour=hour)


def requested_run(cycle: str | None) -> dt.datetime | None:
    if not cycle:
        return None
    hour = int(cycle)
    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    run = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    if run > now:
        run -= dt.timedelta(days=1)
    return run


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


def nearest_index(times: list[str], valid: dt.datetime) -> int:
    if not times:
        raise RuntimeError("Open-Meteo não retornou horários")
    best_i = 0
    best_d = None
    for i, raw in enumerate(times):
        try:
            t = dt.datetime.fromisoformat(raw.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            continue
        d = abs((t - valid).total_seconds())
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
    # Meteorological direction: direction FROM which the wind blows.
    # U/V here are the vector components TOWARD east/north.
    speed_ms = speed / 3.6
    rad = math.radians(direction)
    return -speed_ms * math.sin(rad), -speed_ms * math.cos(rad)


def make_request(lat: float, lon: float, fh: int, cycle: str | None):
    hourly = build_hourly_variables()
    run = requested_run(cycle)

    if run is not None:
        params = {
            "latitude": lat,
            "longitude": lon,
            "hourly": hourly,
            "temperature_unit": "celsius",
            "wind_speed_unit": "kmh",
            "timeformat": "iso8601",
            "forecast_days": 10,
            "run": run.strftime("%Y-%m-%dT%H:%M"),
        }
        return get_json(SINGLE_RUN_URL, params), run

    # Live Open-Meteo ECMWF endpoint. It always represents the latest
    # available forecast; do not substitute another provider/model.
    params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": hourly,
        "temperature_unit": "celsius",
        "wind_speed_unit": "kmh",
        "timeformat": "iso8601",
        "forecast_days": 15,
    }
    return get_json(LIVE_URL, params), None


def extract(payload: dict, lat: float, lon: float, fh: int, requested: dt.datetime | None):
    hourly = payload.get("hourly") or {}
    times = hourly.get("time") or []
    if requested is not None:
        valid_target = requested + dt.timedelta(hours=fh)
    else:
        # Live forecast time starts at today's 00 UTC. For the browser product
        # forecast hour is therefore the lead from that available time axis.
        valid_target = dt.datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0) + dt.timedelta(hours=fh)
    i = nearest_index(times, valid_target)

    def value(name):
        arr = hourly.get(name)
        if not isinstance(arr, list) or i >= len(arr):
            return None
        return finite(arr[i])

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
        # Open-Meteo provides RH at each pressure level. Dew point is derived
        # here using the Magnus relation, preserving the ECMWF pressure-level T/RH.
        rh = max(0.1, min(100.0, rh))
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

    valid = dt.datetime.fromisoformat(times[i].replace("Z", "+00:00"))
    return rows, valid


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lat", type=float, required=True)
    ap.add_argument("--lon", type=float, required=True)
    ap.add_argument("--fh", "--forecast-hour", type=int, default=0)
    ap.add_argument("--cycle", choices=["00", "06", "12", "18"])
    ap.add_argument("--cache", default="/tmp/sideral-skewt-profile")
    args = ap.parse_args()

    if not (-34 <= args.lat <= 6 and -75 <= args.lon <= -33):
        raise SystemExit("coordenada fora do domínio brasileiro")
    if args.fh < 0 or args.fh > 240:
        raise SystemExit("forecast_hour inválido: use F000–F240")
    if args.fh % 3:
        raise SystemExit("forecast_hour deve ser múltiplo de 3")

    requested = requested_run(args.cycle)
    payload, _ = make_request(args.lat, args.lon, args.fh, args.cycle)
    rows, valid = extract(payload, args.lat, args.lon, args.fh, requested)

    run_dt = requested
    if run_dt is None:
        # Open-Meteo's live endpoint does not expose a model-run timestamp as a
        # separate field. Keep this explicitly marked instead of fabricating it.
        run_label = "latest-open-meteo"
    else:
        run_label = run_dt.strftime("%Y-%m-%d %HZ")

    root = Path(args.cache)
    root.mkdir(parents=True, exist_ok=True)
    tag = f"{valid:%Y%m%d%H}_f{args.fh:03d}_{args.lat:+07.2f}_{args.lon:+07.2f}".replace("+", "p").replace("-", "m").replace(".", "p")
    out = root / tag
    out.mkdir(parents=True, exist_ok=True)
    payload_path = out / "profile.json"
    result = {
        "schema": "sideral-skewt-openmeteo-ecmwf-v2",
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
        "run_utc": run_label,
        "valid_utc": valid.strftime("%Y-%m-%d %H:%MZ"),
        "source": "Open-Meteo ECMWF API",
        "profile": rows,
    }
    payload_path.write_text(json.dumps(result, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
    print(payload_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
