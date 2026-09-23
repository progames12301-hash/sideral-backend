from __future__ import annotations

import argparse
import datetime as dt
import gc
import json
import math
import os
import time
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
RENDERER = ROOT / "tools" / "skewt" / "native_spc_render.py"

CAPITALS = [
    ("Rio Branco", "rio-branco", -9.9754, -67.8249), ("Maceió", "maceio", -9.6658, -35.7353),
    ("Macapá", "macapa", 0.0349, -51.0694), ("Manaus", "manaus", -3.1190, -60.0217),
    ("Salvador", "salvador", -12.9777, -38.5016), ("Fortaleza", "fortaleza", -3.7319, -38.5267),
    ("Brasília", "brasilia", -15.7939, -47.8828), ("Vitória", "vitoria", -20.3155, -40.3128),
    ("Goiânia", "goiania", -16.6869, -49.2648), ("São Luís", "sao-luis", -2.5307, -44.3068),
    ("Cuiabá", "cuiaba", -15.6014, -56.0979), ("Campo Grande", "campo-grande", -20.4697, -54.6201),
    ("Belo Horizonte", "belo-horizonte", -19.9167, -43.9345), ("Belém", "belem", -1.4558, -48.5039),
    ("João Pessoa", "joao-pessoa", -7.1195, -34.8450), ("Curitiba", "curitiba", -25.4284, -49.2733),
    ("Recife", "recife", -8.0476, -34.8770), ("Teresina", "teresina", -5.0892, -42.8019),
    ("Rio de Janeiro", "rio-de-janeiro", -22.9068, -43.1729), ("Natal", "natal", -5.7945, -35.2110),
    ("Porto Alegre", "porto-alegre", -30.0346, -51.2177), ("Porto Velho", "porto-velho", -8.7608, -63.8999),
    ("Boa Vista", "boa-vista", 2.8235, -60.6758), ("Florianópolis", "florianopolis", -27.5949, -48.5482),
    ("São Paulo", "sao-paulo", -23.5505, -46.6333), ("Aracaju", "aracaju", -10.9472, -37.0731),
    ("Palmas", "palmas", -10.1840, -48.3336),
]

LEVELS = [1000, 925, 850, 700, 600, 500, 400, 300, 250, 200, 150, 100, 50]
HOURS = list(range(0, 49, 3))
BASE_URL = "https://single-runs-api.open-meteo.com/v1/forecast"
UA = "SideralMeteorologia/2.0 (SHARPpy capital soundings)"


def finite(v):
    try:
        x = float(v)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def get_json(params: dict, retries: int = 8, delay: int = 45):
    url = BASE_URL + "?" + urlencode(params, doseq=True)
    last = None
    for attempt in range(retries):
        try:
            req = Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
            with urlopen(req, timeout=90) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if isinstance(payload, dict) and payload.get("error"):
                raise RuntimeError(str(payload.get("reason", "Open-Meteo returned an error")))
            return payload
        except Exception as exc:
            last = exc
            if attempt + 1 < retries:
                print(f"[Open-Meteo] tentativa {attempt + 1}/{retries} falhou: {exc}; aguardando {delay}s", flush=True)
                time.sleep(delay)
    raise RuntimeError(f"Open-Meteo Single Runs indisponível após {retries} tentativas: {last}")


def run_datetime(cycle: str) -> dt.datetime:
    now = dt.datetime.now(dt.timezone.utc).replace(minute=0, second=0, microsecond=0)
    hour = int(cycle)
    run = now.replace(hour=hour)
    if run > now:
        run -= dt.timedelta(days=1)
    return run.replace(tzinfo=None)


def hourly_name(prefix: str, level: int | None = None) -> str:
    return f"{prefix}_{level}hPa" if level is not None else prefix


def to_uv(speed_kt, direction_deg):
    speed = finite(speed_kt); direction = finite(direction_deg)
    if speed is None or direction is None:
        return None, None
    rad = math.radians(direction)
    return -speed * math.sin(rad), -speed * math.cos(rad)


def as_series(obj, key):
    value = obj.get(key)
    return value if isinstance(value, list) else []


def profile_at(obj: dict, index: int, lat: float, lon: float, location: str, valid: dt.datetime):
    hourly = obj.get("hourly") or {}
    times = as_series(hourly, "time")
    if index >= len(times):
        raise RuntimeError(f"Horário F não disponível para {location}")
    sp = finite(as_series(hourly, "surface_pressure")[index])
    t2 = finite(as_series(hourly, "temperature_2m")[index])
    td2 = finite(as_series(hourly, "dew_point_2m")[index])
    ws2 = finite(as_series(hourly, "wind_speed_10m")[index])
    wd2 = finite(as_series(hourly, "wind_direction_10m")[index])
    elev = finite(obj.get("elevation")) or 0.0
    su, sv = to_uv(ws2, wd2)
    pres, hght, tmp, dwpt, uu, vv, omg = [], [], [], [], [], [], []
    if sp is not None and t2 is not None and td2 is not None:
        pres.append(sp); hght.append(elev); tmp.append(t2); dwpt.append(td2)
        uu.append(su if su is not None else 0.0); vv.append(sv if sv is not None else 0.0); omg.append(np.nan)
    for level in LEVELS:
        p = float(level)
        if sp is not None and p > sp + 2: continue
        tarr = as_series(hourly, hourly_name("temperature", level))
        tdarr = as_series(hourly, hourly_name("dew_point", level))
        rharr = as_series(hourly, hourly_name("relative_humidity", level)) if not tdarr else []
        wsarr = as_series(hourly, hourly_name("wind_speed", level))
        wdarr = as_series(hourly, hourly_name("wind_direction", level))
        gharr = as_series(hourly, hourly_name("geopotential_height", level))
        warr = as_series(hourly, hourly_name("vertical_velocity", level))
        if not tarr or not wsarr or not wdarr or not gharr: continue
        t = finite(tarr[index]); td = finite(tdarr[index]) if tdarr else None
        if td is None and rharr:
            rh = finite(rharr[index])
            if t is not None and rh is not None:
                a, b = 17.625, 243.04
                gamma = math.log(max(0.1, min(100.0, rh)) / 100.0) + a * t / (b + t)
                td = b * gamma / (a - gamma)
        ws = finite(wsarr[index]); wd = finite(wdarr[index]); gh = finite(gharr[index])
        if t is None or td is None or ws is None or wd is None or gh is None: continue
        u, v = to_uv(ws, wd)
        pres.append(p); hght.append(gh); tmp.append(t); dwpt.append(td)
        uu.append(u if u is not None else 0.0); vv.append(v if v is not None else 0.0)
        omg.append(finite(warr[index]) if warr else np.nan)
    if len(pres) < 6:
        raise RuntimeError(f"Perfil insuficiente para {location}: {len(pres)} níveis")
    order = np.argsort(np.asarray(pres))[::-1]
    from sharppy.sharptab import profile as shp_profile
    return shp_profile.create_profile(profile="convective", pres=np.asarray(pres)[order], hght=np.asarray(hght)[order],
        tmpc=np.asarray(tmp)[order], dwpc=np.asarray(dwpt)[order], u=np.asarray(uu)[order], v=np.asarray(vv)[order],
        omeg=np.ma.masked_invalid(np.asarray(omg)[order]), strictQC=False, latitude=lat, date=valid, location=location)


def load_renderer():
    import importlib.util
    spec = importlib.util.spec_from_file_location("sideral_native_spc_render", RENDERER)
    if spec is None or spec.loader is None: raise RuntimeError(f"Não foi possível carregar renderer: {RENDERER}")
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module); return module


def render_one(renderer, sounding, capital, fh, output):
    tmp_dir = output.parent / f".tmp-f{fh:03d}"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    meta = {"capital": capital[0], "location": capital[0], "fh": fh,
            "valid": sounding.date.strftime("%Y-%m-%d %HZ"), "title": f"SIDERAL SKEW-T — {capital[0]} — F{fh:03d}"}
    result = renderer.render_native_spc(sounding, tmp_dir, meta)
    native = Path(result) if result is not None else tmp_dir / "full.png"
    if not native.exists() or native.stat().st_size < 1000:
        # Native renderer produces full.png even when it intentionally returns None.
        candidates = [tmp_dir / "full.png", tmp_dir / "skewt.png"]
        native = next((p for p in candidates if p.exists() and p.stat().st_size >= 1000), native)
    if not native.exists() or native.stat().st_size < 1000:
        raise RuntimeError(f"SHARPpy não produziu PNG válido para {capital[0]} F{fh:03d}")
    native.replace(output)
    for p in tmp_dir.glob("*"):
        try: p.unlink()
        except OSError: pass
    try: tmp_dir.rmdir()
    except OSError: pass


def main():
    parser = argparse.ArgumentParser(); parser.add_argument("--cycle", required=True, choices=["06", "12", "18"]); parser.add_argument("--out", required=True); args = parser.parse_args()
    run = run_datetime(args.cycle)
    latitudes = [c[2] for c in CAPITALS]; longitudes = [c[3] for c in CAPITALS]
    variables = ["surface_pressure", "temperature_2m", "dew_point_2m", "wind_speed_10m", "wind_direction_10m"]
    for level in LEVELS:
        variables += [hourly_name("temperature", level), hourly_name("dew_point", level), hourly_name("relative_humidity", level),
                      hourly_name("wind_speed", level), hourly_name("wind_direction", level), hourly_name("geopotential_height", level), hourly_name("vertical_velocity", level)]
    params = {"latitude": latitudes, "longitude": longitudes, "hourly": ",".join(variables), "models": "ecmwf_ifs025",
              "run": run.strftime("%Y-%m-%dT%H:%M"), "forecast_hours": "49", "wind_speed_unit": "kn", "temperature_unit": "celsius",
              "timeformat": "iso8601", "timezone": "UTC", "cell_selection": "nearest"}
    print(f"[Sideral] ECMWF IFS 0.25° via Open-Meteo Single Runs: {run:%Y-%m-%d %HZ}", flush=True)
    payload = get_json(params)
    locations = [payload] if isinstance(payload, dict) else payload if isinstance(payload, list) else None
    if locations is None: raise RuntimeError("Resposta Open-Meteo inesperada")
    if len(locations) != len(CAPITALS): raise RuntimeError(f"Open-Meteo retornou {len(locations)} locais; esperado {len(CAPITALS)}")
    renderer = load_renderer(); out_root = Path(args.out); out_root.mkdir(parents=True, exist_ok=True)
    manifest = {"schema": "sideral-capitals-skewt-sharppy-v1", "provider": "Open-Meteo Single Runs", "model": "ECMWF IFS 0.25°", "renderer": "SHARPpy", "run_utc": run.strftime("%Y-%m-%d %H:%MZ"), "cycle": args.cycle, "forecast_hours": HOURS, "cities": []}
    for capital, obj in zip(CAPITALS, locations):
        city_dir = out_root / capital[1]; city_dir.mkdir(parents=True, exist_ok=True)
        manifest["cities"].append({"name": capital[0], "slug": capital[1], "latitude": capital[2], "longitude": capital[3]})
        times = as_series(obj.get("hourly") or {}, "time")
        if not times: raise RuntimeError(f"Sem eixo temporal para {capital[0]}")
        parsed_times = [dt.datetime.fromisoformat(t.replace("Z", "+00:00")).replace(tzinfo=None) for t in times]
        for fh in HOURS:
            valid = run + dt.timedelta(hours=fh)
            if valid not in parsed_times: raise RuntimeError(f"{capital[0]}: F{fh:03d} não está disponível no run {run:%Y-%m-%d %HZ}")
            idx = parsed_times.index(valid); sounding = profile_at(obj, idx, capital[2], capital[3], capital[0], valid)
            output = city_dir / f"{capital[1]}-f{fh:03d}.png"; print(f"[SHARPpy] {capital[0]} F{fh:03d}", flush=True)
            render_one(renderer, sounding, capital, fh, output); gc.collect()
    (out_root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[Sideral] concluído: {len(CAPITALS)} capitais × {len(HOURS)} frames = {len(CAPITALS) * len(HOURS)} PNGs", flush=True)

if __name__ == "__main__": main()
