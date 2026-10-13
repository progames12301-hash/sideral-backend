from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import math
import os
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[2]
BASE_SCRIPT = ROOT / "tools" / "skewt" / "generate_capitals_sharppy.py"
spec = importlib.util.spec_from_file_location("sideral_skewt_capitals_base", BASE_SCRIPT)
if spec is None or spec.loader is None:
    raise RuntimeError(f"Não foi possível importar {BASE_SCRIPT}")
base = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = base
spec.loader.exec_module(base)

# Model identifiers used by the Open-Meteo Single Runs API.
MODELS = {
    "icon": {
        "api_model": "icon_global",
        "label": "DWD ICON Global ~11 km",
        "tag": "skewt-icon-global",
        "resolution": "0.1° (~11 km)",
    },
    "ukmo10km": {
        "api_model": "ukmo_global_deterministic_10km",
        "label": "UK Met Office Global 10 km",
        "tag": "skewt-ukmo-global10km",
        "resolution": "0.09° (~10 km)",
    },
    "ecmwf-hybrid": {
        "api_model": None,
        "label": "ECMWF HRES ~9 km híbrido + IFS 0.25° em níveis de pressão",
        "tag": "skewt-ecmwf-hres9km-hybrid",
        "resolution": "Superfície HRES nativo ~9 km + coluna vertical IFS 0.25°",
    },
    "nems10": {
        "api_model": None,
        "label": "meteoblue NEMS10 NEMSSA 10 km",
        "tag": "skewt-nems10-nemssa-live",
        "resolution": "10 km; domínio NEMSSA; previsão horária até 180 h",
    },
}

NEMS_LEVELS = [1000, 950, 900, 850, 800, 750, 700, 650, 600, 550, 500, 450, 400, 350, 300, 250, 200, 150]
NEMS_API_URL = "https://my.meteoblue.com/dataset/query"
NEMS_LEVEL_KEYS = {
    "temperature": 11,
    "relative_humidity": 52,
    "wind_u": 33,
    "wind_v": 34,
    "geopotential_height": 7,
}


SURFACE_VARIABLES = [
    "surface_pressure", "temperature_2m", "dew_point_2m",
    "wind_speed_10m", "wind_direction_10m",
]


def pressure_variables():
    result = []
    for level in base.LEVELS:
        result += [
            base.hourly_name("temperature", level),
            base.hourly_name("relative_humidity", level),
            base.hourly_name("wind_speed", level),
            base.hourly_name("wind_direction", level),
            base.hourly_name("geopotential_height", level),
            base.hourly_name("vertical_velocity", level),
        ]
    return result


def request_model(model: str, run: dt.datetime, variables: list[str]):
    latitudes = [c[2] for c in base.CAPITALS]
    longitudes = [c[3] for c in base.CAPITALS]
    params = {
        "latitude": ",".join(map(str, latitudes)),
        "longitude": ",".join(map(str, longitudes)),
        "hourly": ",".join(variables),
        "models": model,
        "run": run.strftime("%Y-%m-%dT%H:%M"),
        "forecast_hours": "49",
        "wind_speed_unit": "kn",
        "temperature_unit": "celsius",
        "timeformat": "iso8601",
        "timezone": "UTC",
        "cell_selection": "nearest",
    }
    print(f"[Single Runs] modelo={model}; run={run:%Y-%m-%d %HZ}; variáveis={len(variables)}", flush=True)
    payload = base.get_json(params, retries=6, delay=20)
    locations = [payload] if isinstance(payload, dict) else payload if isinstance(payload, list) else None
    if locations is None:
        raise RuntimeError(f"Resposta inesperada da API para {model}")
    if len(locations) != len(base.CAPITALS):
        raise RuntimeError(f"{model}: API retornou {len(locations)} locais; esperados {len(base.CAPITALS)}")
    return locations


def _nems_level(level: int) -> str:
    return f"{level} mb"


def _nems_datetime(raw: str) -> dt.datetime:
    text = str(raw).strip()
    for fmt in ("%Y%m%dT%H%M", "%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S"):
        try:
            return dt.datetime.strptime(text, fmt)
        except ValueError:
            pass
    try:
        return dt.datetime.fromisoformat(text.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError as exc:
        raise RuntimeError(f"Data/hora NEMSSA desconhecida: {raw!r}") from exc


def _nems_code(code: int, level: str) -> dict:
    return {"code": code, "level": level}


def _nems_fetch_json(api_key: str, body: dict) -> dict | list:
    # API key is sent only as a query credential and must never be printed.
    url = NEMS_API_URL + "?" + urlencode({"apikey": api_key})
    request = Request(
        url,
        data=json.dumps(body, separators=(",", ":")).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    last_error = None
    for attempt in range(4):
        try:
            with urlopen(request, timeout=180) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if isinstance(payload, dict) and (payload.get("error_message") or payload.get("error")):
                message = str(payload.get("error_message") or payload.get("error"))
                raise RuntimeError(message.replace(api_key, "[redacted]"))
            return payload
        except HTTPError as exc:
            detail = ""
            try:
                raw = exc.read().decode("utf-8", errors="replace")
                parsed = json.loads(raw)
                detail = str(parsed.get("error_message") or parsed.get("message") or parsed.get("error") or "")
            except Exception:
                detail = ""
            last_error = f"HTTP {exc.code}" + (f": {detail.replace(api_key, '[redacted]')}" if detail else "")
            if exc.code not in (408, 429, 500, 502, 503, 504) or attempt == 3:
                break
        except (URLError, TimeoutError, OSError, json.JSONDecodeError, RuntimeError) as exc:
            last_error = str(exc).replace(api_key, "[redacted]")
            if attempt == 3:
                break
        time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"Falha ao consultar meteoblue Dataset API (chave ocultada): {last_error}")


def request_nems10() -> tuple[list[dict], dt.datetime]:
    api_key = os.environ.get("METEOBLUE_APIKEY", "").strip()
    if not api_key:
        raise RuntimeError(
            "NEMS10 não iniciado: configure a chave da meteoblue em GitHub Actions Secrets "
            "com o nome METEOBLUE_APIKEY. A chave não deve ficar no código nem no HTML."
        )

    # The Dataset API exposes the current NEMSSA forecast by valid time, not
    # the fixed ECMWF 06/12/18Z cycle selectors used by the other generators.
    start_utc = dt.datetime.now(dt.timezone.utc).replace(minute=0, second=0, microsecond=0) + dt.timedelta(hours=1)
    end_utc = start_utc + dt.timedelta(hours=max(base.HOURS) + 1)
    time_interval = (
        start_utc.strftime("%Y-%m-%dT%H:%M+00:00") + "/" +
        end_utc.strftime("%Y-%m-%dT%H:%M+00:00")
    )
    coordinates = [[capital[3], capital[2]] for capital in base.CAPITALS]
    location_names = [capital[0] for capital in base.CAPITALS]

    codes = [
        _nems_code(1, "sfc"),
        _nems_code(11, "2 m above gnd"),
        _nems_code(17, "2 m above gnd"),
        _nems_code(33, "10 m above gnd"),
        _nems_code(34, "10 m above gnd"),
    ]
    for level in NEMS_LEVELS:
        lev = _nems_level(level)
        for code in (7, 11, 33, 34, 52):
            codes.append(_nems_code(code, lev))

    body = {
        "units": {"temperature": "C", "velocity": "kn", "length": "metric", "energy": "watts"},
        "geometry": {"type": "MultiPoint", "coordinates": coordinates, "locationNames": location_names},
        "format": "json",
        "timeIntervals": [time_interval],
        "timeIntervalsAlignment": "none",
        "queries": [{
            "domain": "NEMSSA",
            "gapFillDomain": None,
            "timeResolution": "hourly",
            "allowForecast": True,
            "allowHistory": False,
            "codes": codes,
        }],
    }
    print(
        f"[meteoblue] Consulta NEMSSA: 27 capitais, {len(codes)} campos, "
        f"{start_utc:%Y-%m-%d %HZ} a {end_utc:%Y-%m-%d %HZ}; chave ocultada",
        flush=True,
    )
    payload = _nems_fetch_json(api_key, body)
    if isinstance(payload, dict):
        if payload.get("requiresJobQueue"):
            raise RuntimeError("NEMSSA pediu fila de processamento; reduzir o tamanho da consulta ou usar a fila Dataset API.")
        if payload.get("error_message") or payload.get("error"):
            raise RuntimeError(str(payload.get("error_message") or payload.get("error")).replace(api_key, "[redacted]"))
        raise RuntimeError(f"Resposta inesperada do Dataset API: chaves={sorted(payload.keys())}")
    if not isinstance(payload, list):
        raise RuntimeError("Resposta NEMSSA não é uma lista JSON.")

    domain = next((item for item in payload if isinstance(item, dict) and item.get("domain") == "NEMSSA"), None)
    if domain is None:
        raise RuntimeError("A meteoblue não retornou o domínio NEMSSA.")
    interval_groups = domain.get("timeIntervals") or []
    if not interval_groups or not isinstance(interval_groups[0], list):
        raise RuntimeError("A resposta NEMSSA não trouxe o eixo temporal esperado.")
    raw_times = interval_groups[0]
    times = [_nems_datetime(t).strftime("%Y-%m-%dT%H:%M") for t in raw_times]
    start_naive = start_utc.replace(tzinfo=None)
    if len(times) < max(base.HOURS) + 1:
        raise RuntimeError(f"NEMSSA retornou apenas {len(times)} horários; são necessários pelo menos {max(base.HOURS) + 1}.")

    # Exact code/level matching prevents silent field substitution.
    lookup = {}
    for item in domain.get("codes") or []:
        try:
            key = (int(item.get("code")), str(item.get("level", "")).strip().lower())
        except (TypeError, ValueError):
            continue
        lookup[key] = item

    def series(code: int, level: str, city_index: int) -> list:
        item = lookup.get((code, level.strip().lower()))
        if item is None:
            raise RuntimeError(f"NEMSSA sem campo vertical code={code}, level={level}")
        groups = item.get("dataPerTimeInterval") or []
        if not groups or not isinstance(groups[0].get("data"), list):
            raise RuntimeError(f"NEMSSA sem série para code={code}, level={level}")
        data = groups[0]["data"]
        if len(data) != len(base.CAPITALS):
            raise RuntimeError(
                f"NEMSSA retornou {len(data)} células para code={code}, level={level}; esperadas 27."
            )
        values = data[city_index]
        if len(values) < len(times):
            raise RuntimeError(f"NEMSSA série curta para code={code}, level={level}: {len(values)} de {len(times)}.")
        return values

    def finite_series(values: list, divisor: float = 1.0) -> list:
        output = []
        for value in values[:len(times)]:
            number = base.finite(value)
            if number is not None:
                number /= divisor
                output.append(number)
            else:
                output.append(None)
        return output

    def wind_from_uv(u: float | None, v: float | None):
        if u is None or v is None:
            return None, None
        speed = math.hypot(u, v)
        direction = math.degrees(math.atan2(-u, -v)) % 360.0
        return speed, direction

    hourly_by_city = []
    coords = (domain.get("geometry") or {}).get("coordinates") or []
    for city_index, capital in enumerate(base.CAPITALS):
        hourly = {"time": times}
        sp = finite_series(series(1, "sfc", city_index))
        # Some Dataset API units may report pressure in Pa; normalize to hPa.
        if any(v is not None and v > 2000 for v in sp):
            sp = finite_series(series(1, "sfc", city_index), 100.0)
        hourly["surface_pressure"] = sp
        hourly["temperature_2m"] = finite_series(series(11, "2 m above gnd", city_index))
        hourly["dew_point_2m"] = finite_series(series(17, "2 m above gnd", city_index))
        u10 = finite_series(series(33, "10 m above gnd", city_index))
        v10 = finite_series(series(34, "10 m above gnd", city_index))
        wind10 = [wind_from_uv(u, v) for u, v in zip(u10, v10)]
        hourly["wind_speed_10m"] = [pair[0] for pair in wind10]
        hourly["wind_direction_10m"] = [pair[1] for pair in wind10]

        for level in NEMS_LEVELS:
            lev = _nems_level(level)
            hourly[base.hourly_name("temperature", level)] = finite_series(series(11, lev, city_index))
            hourly[base.hourly_name("relative_humidity", level)] = finite_series(series(52, lev, city_index))
            hourly[base.hourly_name("geopotential_height", level)] = finite_series(series(7, lev, city_index))
            u = finite_series(series(33, lev, city_index))
            v = finite_series(series(34, lev, city_index))
            winds = [wind_from_uv(uu, vv) for uu, vv in zip(u, v)]
            hourly[base.hourly_name("wind_speed", level)] = [pair[0] for pair in winds]
            hourly[base.hourly_name("wind_direction", level)] = [pair[1] for pair in winds]
            # NEMSSA does not publish vertical velocity in this API domain.
            hourly[base.hourly_name("vertical_velocity", level)] = [None] * len(times)

        coord = coords[city_index] if city_index < len(coords) else []
        elevation = base.finite(coord[2]) if len(coord) >= 3 else (base.finite(coord[2]) if len(coord) > 2 else 0.0)
        hourly_by_city.append({
            "latitude": base.finite(coord[1]) if len(coord) > 1 else capital[2],
            "longitude": base.finite(coord[0]) if len(coord) > 0 else capital[3],
            "elevation": elevation or 0.0,
            "hourly": hourly,
        })

    print(f"[meteoblue] Recebidas {len(times)} horas válidas de NEMSSA; início {start_naive:%Y-%m-%d %HZ}", flush=True)
    return hourly_by_city, start_naive


def merge_hybrid(surface_obj: dict, pressure_obj: dict, city: str) -> dict:
    surface = surface_obj.get("hourly") or {}
    upper = pressure_obj.get("hourly") or {}
    surface_times = base.as_series(surface, "time")
    upper_times = base.as_series(upper, "time")
    upper_index = {str(t): i for i, t in enumerate(upper_times)}
    shared_times = [str(t) for t in surface_times if str(t) in upper_index]
    run_hours = []
    for raw in shared_times:
        try:
            valid = dt.datetime.fromisoformat(raw.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            continue
        if valid.minute == 0 and valid.second == 0:
            run_hours.append(raw)
    # Render only standard 3-hour forecast frames, using exact matching valid times
    # from both sources; never align the sources by array index alone.
    run_hours = [
        raw for raw in run_hours
        if int((dt.datetime.fromisoformat(raw.replace("Z", "+00:00")).replace(tzinfo=None) -
                dt.datetime.fromisoformat(surface_times[0].replace("Z", "+00:00")).replace(tzinfo=None)).total_seconds() // 3600) in base.HOURS
    ]
    if len(run_hours) < len(base.HOURS):
        raise RuntimeError(f"{city}: HRES/IFS 0.25° só compartilharam {len(run_hours)} horários; esperados {len(base.HOURS)}")
    surface_index = {str(t): i for i, t in enumerate(surface_times)}
    out_hourly = {"time": run_hours}
    surface_names = set(SURFACE_VARIABLES)
    for key in surface_names:
        values = base.as_series(surface, key)
        out_hourly[key] = [values[surface_index[t]] if surface_index[t] < len(values) else None for t in run_hours] if values else []
    for key in pressure_variables():
        values = base.as_series(upper, key)
        out_hourly[key] = [values[upper_index[t]] if upper_index[t] < len(values) else None for t in run_hours] if values else []
    return {
        "latitude": surface_obj.get("latitude", pressure_obj.get("latitude")),
        "longitude": surface_obj.get("longitude", pressure_obj.get("longitude")),
        "elevation": surface_obj.get("elevation", pressure_obj.get("elevation")),
        "hourly": out_hourly,
    }


def render_one(renderer, sounding, capital, model_label, fh, output):
    tmp_dir = output.parent / f".tmp-f{fh:03d}"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    meta = {
        "capital": capital[0],
        "location": capital[0],
        "fh": fh,
        "latitude": capital[2],
        "longitude": capital[3],
        "valid": sounding.date.strftime("%Y-%m-%d %HZ"),
        "title": f"SIDERAL SKEW-T — {model_label} — {capital[0]} — F{fh:03d}",
        "model": model_label,
    }
    result = renderer.render_native_spc(sounding, tmp_dir, meta)
    native = Path(result) if result is not None else tmp_dir / "full.png"
    if not native.exists() or native.stat().st_size < 1000:
        native = next((p for p in (tmp_dir / "full.png", tmp_dir / "skewt.png") if p.exists() and p.stat().st_size >= 1000), native)
    if not native.exists() or native.stat().st_size < 1000:
        raise RuntimeError(f"SHARPpy não produziu PNG válido: {capital[0]} F{fh:03d} ({model_label})")
    native.replace(output)
    for p in tmp_dir.glob("*"):
        try:
            p.unlink()
        except OSError:
            pass
    try:
        tmp_dir.rmdir()
    except OSError:
        pass


def main():
    parser = argparse.ArgumentParser(description="Gera Skew-T multimodelo para as 27 capitais brasileiras.")
    parser.add_argument("--model", required=True, choices=sorted(MODELS))
    parser.add_argument("--cycle", required=True, choices=["06", "12", "18"])
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    config = MODELS[args.model]
    if args.model == "nems10":
        # NEMSSA is selected by valid-time window, not by the ECMWF run-cycle selector.
        base.LEVELS = NEMS_LEVELS
        locations, run = request_nems10()
        provider = "meteoblue Dataset API"
        manifest_cycle = "latest"
    else:
        run = base.run_datetime(args.cycle)
        surface_names = SURFACE_VARIABLES.copy()
        pressure_names = pressure_variables()
        if args.model == "ecmwf-hybrid":
            surface_locations = request_model("ecmwf_ifs", run, surface_names)
            pressure_locations = request_model("ecmwf_ifs025", run, pressure_names)
            locations = [
                merge_hybrid(sfc, upper, capital[0])
                for capital, sfc, upper in zip(base.CAPITALS, surface_locations, pressure_locations)
            ]
        else:
            locations = request_model(config["api_model"], run, surface_names + pressure_names)
        provider = "Open-Meteo Single Runs"
        manifest_cycle = args.cycle

    renderer = base.load_renderer()
    out_root = Path(args.out)
    out_root.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema": "sideral-capitals-skewt-multimodel-v1",
        "provider": provider,
        "model_id": args.model,
        "model": config["label"],
        "resolution": config["resolution"],
        "surface_source": (
            "ECMWF IFS HRES nativo ~9 km" if args.model == "ecmwf-hybrid"
            else config["label"]
        ),
        "pressure_source": (
            "ECMWF IFS 0.25° (níveis de pressão)" if args.model == "ecmwf-hybrid"
            else config["label"]
        ),
        "renderer": "SHARPpy",
        "run_utc": run.strftime("%Y-%m-%d %H:%MZ"),
        "cycle": manifest_cycle,
        "forecast_hours": base.HOURS,
        "cities": [],
    }
    for capital, obj in zip(base.CAPITALS, locations):
        city_dir = out_root / capital[1]
        city_dir.mkdir(parents=True, exist_ok=True)
        manifest["cities"].append({
            "name": capital[0], "slug": capital[1],
            "latitude": capital[2], "longitude": capital[3],
        })
        times = base.as_series(obj.get("hourly") or {}, "time")
        if not times:
            raise RuntimeError(f"Sem eixo temporal para {capital[0]} ({config['label']})")
        parsed_times = [
            dt.datetime.fromisoformat(str(t).replace("Z", "+00:00")).replace(tzinfo=None)
            for t in times
        ]
        for fh in base.HOURS:
            valid = run + dt.timedelta(hours=fh)
            if valid not in parsed_times:
                raise RuntimeError(f"{capital[0]}: F{fh:03d} não existe em {config['label']} para {run:%Y-%m-%d %HZ}")
            idx = parsed_times.index(valid)
            sounding = base.profile_at(obj, idx, capital[2], capital[3], capital[0], valid)
            output = city_dir / f"{capital[1]}-f{fh:03d}.png"
            print(f"[SHARPpy] {config['label']} | {capital[0]} F{fh:03d}", flush=True)
            render_one(renderer, sounding, capital, config["label"], fh, output)
    (out_root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[Sideral] {config['label']} concluído: {len(base.CAPITALS)} capitais × {len(base.HOURS)} frames = {len(base.CAPITALS) * len(base.HOURS)} PNGs", flush=True)


if __name__ == "__main__":
    main()
