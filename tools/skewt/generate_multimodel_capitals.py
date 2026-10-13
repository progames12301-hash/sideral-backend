from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import sys
from pathlib import Path

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
        "api_model": "dwd_icon",
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

    renderer = base.load_renderer()
    out_root = Path(args.out)
    out_root.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema": "sideral-capitals-skewt-multimodel-v1",
        "provider": "Open-Meteo Single Runs",
        "model_id": args.model,
        "model": config["label"],
        "resolution": config["resolution"],
        "surface_source": "ECMWF IFS HRES nativo ~9 km" if args.model == "ecmwf-hybrid" else config["label"],
        "pressure_source": "ECMWF IFS 0.25° (níveis de pressão)" if args.model == "ecmwf-hybrid" else config["label"],
        "renderer": "SHARPpy",
        "run_utc": run.strftime("%Y-%m-%d %H:%MZ"),
        "cycle": args.cycle,
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
