#!/usr/bin/env python3
from __future__ import annotations

import argparse
import bz2
import datetime as dt
import gzip
import json
import re
import tempfile
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import requests
from eccodes import (
    codes_get,
    codes_get_array,
    codes_get_values,
    codes_grib_new_from_file,
    codes_release,
)
from scipy.spatial import cKDTree

BRT = ZoneInfo("America/Sao_Paulo")
SOUTH, NORTH, WEST, EAST = -36.5, -19.0, -62.0, -43.5
GRID_X, GRID_Y = 220, 180
UA = "SideralMeteorologia/1.0 (+https://sideralmeteorologiabrasil.web.app)"

ICON_BASE = "https://opendata.dwd.de/weather/nwp/icon/grib"
ICON_PARAMS = {
    "temperature": ("t_2m", "T_2M"),
    "humidity": ("relhum_2m", "RELHUM_2M"),
    "u10": ("u_10m", "U_10M"),
    "v10": ("v_10m", "V_10M"),
    "precip_total": ("tot_prec", "TOT_PREC"),
    "mucape": ("cape_ml", "CAPE_ML"),
}
ECMWF_SURFACE_PARAMS = ["2t", "2d", "10u", "10v", "tprate", "mucape", "tcwv"]


def iso_z(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def wind_direction_deg(u: np.ndarray, v: np.ndarray) -> np.ndarray:
    direction = (270.0 - np.degrees(np.arctan2(v, u))) % 360.0
    calm = (np.abs(u) < 1e-8) & (np.abs(v) < 1e-8)
    return np.where(calm, 0.0, direction)


def rh_from_t_td(t_c: np.ndarray, td_c: np.ndarray) -> np.ndarray:
    t = np.asarray(t_c, dtype=float)
    td = np.asarray(td_c, dtype=float)
    a, b = 17.625, 243.04
    rh = 100.0 * np.exp((a * td / (b + td)) - (a * t / (b + t)))
    return np.clip(rh, 0.0, 100.0)


def flatten(values: np.ndarray, decimals: int) -> list[float]:
    arr = np.nan_to_num(np.asarray(values, dtype=float), nan=0.0, posinf=0.0, neginf=0.0)
    return np.round(arr, decimals).reshape(-1).tolist()


def write_gz(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    with path.open("wb") as fh:
        with gzip.GzipFile(filename="", mode="wb", fileobj=fh, compresslevel=9, mtime=0) as gz:
            gz.write(raw)


def read_grib_messages(path: Path) -> list[dict]:
    messages: list[dict] = []
    with path.open("rb") as fh:
        while True:
            gid = codes_grib_new_from_file(fh)
            if gid is None:
                break
            try:
                item = {
                    "shortName": str(codes_get(gid, "shortName")),
                    "values": np.asarray(codes_get_values(gid), dtype=np.float64),
                }
                for key in ("endStep", "forecastTime", "level", "typeOfLevel", "units"):
                    try:
                        item[key] = codes_get(gid, key)
                    except Exception:
                        item[key] = None
                try:
                    item["latitudes"] = np.asarray(codes_get_array(gid, "latitudes"), dtype=np.float64)
                    item["longitudes"] = np.asarray(codes_get_array(gid, "longitudes"), dtype=np.float64)
                except Exception:
                    item["latitudes"] = None
                    item["longitudes"] = None
                messages.append(item)
            finally:
                codes_release(gid)
    if not messages:
        raise RuntimeError(f"Nenhuma mensagem GRIB em {path}")
    return messages


def read_single_grib_bytes(body: bytes) -> dict:
    with tempfile.NamedTemporaryFile(suffix=".grib2", delete=False) as tmp:
        tmp.write(body)
        tmp_path = Path(tmp.name)
    try:
        return read_grib_messages(tmp_path)[0]
    finally:
        tmp_path.unlink(missing_ok=True)


def icon_url(run: dt.datetime, step: int, folder: str, token: str) -> str:
    cycle = run.strftime("%H")
    stamp = run.strftime("%Y%m%d%H")
    return (
        f"{ICON_BASE}/{cycle}/{folder}/"
        f"icon_global_icosahedral_single-level_{stamp}_{step:03d}_{token}.grib2.bz2"
    )


def head_ok(url: str) -> bool:
    try:
        with requests.get(
            url,
            headers={"User-Agent": UA, "Range": "bytes=0-0"},
            timeout=30,
            stream=True,
        ) as response:
            return response.status_code in {200, 206}
    except requests.RequestException:
        return False


def synoptic_candidates(now: dt.datetime | None = None) -> list[dt.datetime]:
    current = now or dt.datetime.now(dt.timezone.utc)
    base = current.replace(hour=(current.hour // 6) * 6, minute=0, second=0, microsecond=0)
    return [base - dt.timedelta(hours=6 * idx) for idx in range(0, 8)]


def next_local_day_steps(run: dt.datetime, interval_h: int, max_step: int) -> tuple[dt.date, list[int]]:
    target = run.astimezone(BRT).date() + dt.timedelta(days=1)
    steps = []
    for step in range(0, max_step + 1, interval_h):
        valid = run + dt.timedelta(hours=step)
        if valid.astimezone(BRT).date() == target:
            steps.append(step)
    return target, steps


def discover_icon_run() -> tuple[dt.datetime, dt.date, list[int]]:
    for run in synoptic_candidates():
        target, steps = next_local_day_steps(run, 1, 48)
        if len(steps) != 24:
            continue
        last = steps[-1]
        if head_ok(icon_url(run, last, "t_2m", "T_2M")):
            print("ICON selecionado:", iso_z(run), "ultimo passo:", last)
            return run, target, steps
        print("ICON ainda incompleto:", iso_z(run), "F", last)
    raise RuntimeError("Nenhuma rodada ICON recente possui o proximo dia completo.")


def download_icon_field(run: dt.datetime, step: int, folder: str, token: str) -> np.ndarray:
    url = icon_url(run, step, folder, token)
    response = requests.get(url, headers={"User-Agent": UA}, timeout=180)
    response.raise_for_status()
    data = bz2.decompress(response.content)
    return np.asarray(read_single_grib_bytes(data)["values"], dtype=np.float64)


def icon_time_invariant(cycle: str, name: str) -> np.ndarray:
    folder = f"{ICON_BASE}/{cycle}/{name.lower()}/"
    listing = requests.get(folder, headers={"User-Agent": UA}, timeout=45)
    listing.raise_for_status()
    match = re.search(r'href="([^"]*_' + re.escape(name.upper()) + r'\.grib2\.bz2)"', listing.text, re.I)
    if not match:
        raise RuntimeError(f"Arquivo ICON {name} nao encontrado em {folder}")
    url = folder + match.group(1)
    response = requests.get(url, headers={"User-Agent": UA}, timeout=180)
    response.raise_for_status()
    msg = read_single_grib_bytes(bz2.decompress(response.content))
    values = np.asarray(msg["values"], dtype=np.float64)
    units = str(msg.get("units") or "").lower()
    if "rad" in units or (name.upper() == "CLAT" and np.nanmax(np.abs(values)) <= 3.2):
        values = np.degrees(values)
    if name.upper() == "CLON" and np.nanmax(np.abs(values)) <= 6.4:
        values = np.degrees(values)
    if name.upper() == "CLON":
        values = ((values + 180.0) % 360.0) - 180.0
    return values


def target_grid() -> tuple[np.ndarray, np.ndarray]:
    ys = np.linspace(SOUTH, NORTH, GRID_Y)
    xs = np.linspace(WEST, EAST, GRID_X)
    lon2, lat2 = np.meshgrid(xs, ys)
    return lat2, lon2


def build_icon(output: Path) -> None:
    run, target_date, steps = discover_icon_run()
    cycle = run.strftime("%H")
    clat = icon_time_invariant(cycle, "CLAT")
    clon = icon_time_invariant(cycle, "CLON")
    if clat.shape != clon.shape:
        raise RuntimeError("CLAT/CLON ICON com tamanhos diferentes.")

    margin = 1.0
    mask = (
        (clat >= SOUTH - margin) & (clat <= NORTH + margin)
        & (clon >= WEST - margin) & (clon <= EAST + margin)
    )
    source_idx = np.flatnonzero(mask)
    if source_idx.size < 500:
        raise RuntimeError(f"Poucos pontos ICON no dominio: {source_idx.size}")

    lat2, lon2 = target_grid()
    tree = cKDTree(np.column_stack([clat[source_idx], clon[source_idx]]))
    _, nearest_local = tree.query(np.column_stack([lat2.ravel(), lon2.ravel()]), k=1)
    nearest = source_idx[np.asarray(nearest_local, dtype=int)]

    previous_precip: np.ndarray | None = None
    previous_step: int | None = None
    first_step = steps[0]
    if first_step > 0:
        prev = first_step - 1
        previous_precip = download_icon_field(run, prev, *ICON_PARAMS["precip_total"])
        previous_step = prev

    frames = []
    for step in steps:
        print(f"ICON F{step:03d}")
        t = download_icon_field(run, step, *ICON_PARAMS["temperature"])[nearest] - 273.15
        rh = np.clip(download_icon_field(run, step, *ICON_PARAMS["humidity"])[nearest], 0.0, 100.0)
        u = download_icon_field(run, step, *ICON_PARAMS["u10"])[nearest]
        v = download_icon_field(run, step, *ICON_PARAMS["v10"])[nearest]
        cape = np.maximum(download_icon_field(run, step, *ICON_PARAMS["mucape"])[nearest], 0.0)
        precip_total_full = download_icon_field(run, step, *ICON_PARAMS["precip_total"])
        if previous_precip is None or previous_step is None:
            precip = np.zeros_like(t)
        else:
            elapsed = max(1, step - previous_step)
            precip = np.maximum(0.0, precip_total_full[nearest] - previous_precip[nearest]) / elapsed
        previous_precip = precip_total_full
        previous_step = step

        wind_kmh = np.hypot(u, v) * 3.6
        wind_dir = wind_direction_deg(u, v)
        valid = run + dt.timedelta(hours=step)
        zeros = np.zeros_like(t)
        fields = {
            "lat": flatten(lat2, 4),
            "lon": flatten(lon2, 4),
            "reflectivity": flatten(zeros, 1),
            "precipitation": flatten(precip.reshape(GRID_Y, GRID_X), 2),
            "windSpeed": flatten(wind_kmh.reshape(GRID_Y, GRID_X), 1),
            "windDirection": flatten(wind_dir.reshape(GRID_Y, GRID_X), 0),
            "bulkShear": flatten(zeros, 1),
            "vorticity850": flatten(zeros, 7),
            "temperature": flatten(t.reshape(GRID_Y, GRID_X), 1),
            "humidity": flatten(rh.reshape(GRID_Y, GRID_X), 0),
            "mucape": flatten(cape.reshape(GRID_Y, GRID_X), 0),
            "waterVapor": flatten(zeros, 1),
        }
        payload = {
            "schema": "sideral-model-grid-v1",
            "model": "icon",
            "source": "DWD ICON Global ~13 km (dados oficiais; sem refletividade de radar nativa)",
            "runDate": run.strftime("%Y%m%d"),
            "runCycle": f"{run:%H}Z",
            "initTime": iso_z(run),
            "forecastHour": step,
            "validTime": iso_z(valid),
            "gridX": GRID_X,
            "gridY": GRID_Y,
            "bounds": {"south": SOUTH, "west": WEST, "north": NORTH, "east": EAST},
            "capabilities": {
                "reflectivity": False,
                "precipitation": True,
                "wind": True,
                "temperature": True,
                "humidity": True,
                "mucape": True,
                "bulkShear": False,
                "vorticity850": False,
                "waterVapor": False,
            },
            "fields": fields,
        }
        filename = f"icon/f{step:03d}.json.gz"
        write_gz(output / filename, payload)
        frames.append({
            "index": len(frames),
            "forecastHour": step,
            "validTime": payload["validTime"],
            "localValidTime": valid.astimezone(BRT).isoformat(),
            "file": filename,
            "gridX": GRID_X,
            "gridY": GRID_Y,
        })

    metadata = {
        "schema": "sideral-model-metadata-v1",
        "model": "icon",
        "provider": "DWD",
        "resolutionKm": 13,
        "runDate": run.strftime("%Y%m%d"),
        "runCycle": f"{run:%H}Z",
        "initTime": iso_z(run),
        "generatedAt": iso_z(dt.datetime.now(dt.timezone.utc)),
        "forecastLocalDate": target_date.isoformat(),
        "timezone": "America/Sao_Paulo",
        "scope": "next_local_day",
        "temporalResolutionMinutes": 60,
        "frameCount": len(frames),
        "frames": frames,
        "capabilities": {
            "reflectivity": False,
            "precipitation": True,
            "wind": True,
            "temperature": True,
            "humidity": True,
            "mucape": True,
            "bulkShear": False,
            "vorticity850": False,
            "waterVapor": False,
        },
        "note": "ICON Global nao publica refletividade composta nativa para este dominio; o site nao deve sintetizar dBZ.",
    }
    (output / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")


def discover_ecmwf_run(client) -> tuple[dt.datetime, dt.date, list[int]]:
    latest = client.latest(type="fc", step=36, param=ECMWF_SURFACE_PARAMS)
    if isinstance(latest, dt.datetime):
        run = latest.replace(tzinfo=dt.timezone.utc) if latest.tzinfo is None else latest.astimezone(dt.timezone.utc)
    else:
        parsed = dt.datetime.fromisoformat(str(latest))
        run = parsed.replace(tzinfo=dt.timezone.utc) if parsed.tzinfo is None else parsed.astimezone(dt.timezone.utc)
    target, steps = next_local_day_steps(run, 3, 90)
    if len(steps) != 8:
        raise RuntimeError(f"Rodada ECMWF {run} nao cobre 8 passos do proximo dia: {steps}")
    return run, target, steps


def grib_step(message: dict) -> int:
    value = message.get("endStep")
    if value is None:
        value = message.get("forecastTime")
    try:
        return int(value)
    except Exception as exc:
        raise RuntimeError(f"Passo GRIB invalido: {value}") from exc


def build_ecmwf(output: Path) -> None:
    from ecmwf.opendata import Client

    client = Client(source="ecmwf", model="ifs")
    run, target_date, steps = discover_ecmwf_run(client)
    with tempfile.TemporaryDirectory() as tmpdir:
        target = Path(tmpdir) / "ecmwf.grib2"
        result = client.retrieve(
            date=run.strftime("%Y%m%d"),
            time=int(run.strftime("%H")),
            type="fc",
            step=steps,
            param=ECMWF_SURFACE_PARAMS,
            target=str(target),
        )
        print("ECMWF recuperado:", getattr(result, "datetime", run), target.stat().st_size)
        messages = read_grib_messages(target)

    by_key: dict[tuple[str, int], dict] = {}
    first_coords = None
    for msg in messages:
        key = (str(msg["shortName"]), grib_step(msg))
        by_key[key] = msg
        if first_coords is None and msg.get("latitudes") is not None:
            first_coords = (msg["latitudes"], msg["longitudes"])
    if first_coords is None:
        raise RuntimeError("ECMWF sem coordenadas GRIB.")

    src_lat = np.asarray(first_coords[0], dtype=float)
    src_lon = ((np.asarray(first_coords[1], dtype=float) + 180.0) % 360.0) - 180.0
    margin = 1.0
    mask = (
        (src_lat >= SOUTH - margin) & (src_lat <= NORTH + margin)
        & (src_lon >= WEST - margin) & (src_lon <= EAST + margin)
    )
    source_idx = np.flatnonzero(mask)
    if source_idx.size < 100:
        raise RuntimeError(f"Poucos pontos ECMWF no dominio: {source_idx.size}")
    lat2, lon2 = target_grid()
    tree = cKDTree(np.column_stack([src_lat[source_idx], src_lon[source_idx]]))
    _, nearest_local = tree.query(np.column_stack([lat2.ravel(), lon2.ravel()]), k=1)
    nearest = source_idx[np.asarray(nearest_local, dtype=int)]

    def values(name: str, step: int) -> np.ndarray:
        key = (name, step)
        if key not in by_key:
            candidates = sorted(k for k in by_key if k[1] == step)
            raise RuntimeError(f"ECMWF sem {key}; disponiveis no passo: {candidates}")
        return np.asarray(by_key[key]["values"], dtype=float)[nearest]

    frames = []
    for step in steps:
        print(f"ECMWF F{step:03d}")
        t = values("2t", step) - 273.15
        td = values("2d", step) - 273.15
        rh = rh_from_t_td(t, td)
        u = values("10u", step)
        v = values("10v", step)
        precip = np.maximum(values("tprate", step), 0.0) * 3600.0
        cape = np.maximum(values("mucape", step), 0.0)
        tcwv = np.maximum(values("tcwv", step), 0.0)
        wind_kmh = np.hypot(u, v) * 3.6
        wind_dir = wind_direction_deg(u, v)
        valid = run + dt.timedelta(hours=step)
        zeros = np.zeros_like(t)

        payload = {
            "schema": "sideral-model-grid-v1",
            "model": "ecmwf",
            "source": "ECMWF IFS 0.25° Open Data (dados oficiais; sem refletividade de radar nativa)",
            "runDate": run.strftime("%Y%m%d"),
            "runCycle": f"{run:%H}Z",
            "initTime": iso_z(run),
            "forecastHour": step,
            "validTime": iso_z(valid),
            "gridX": GRID_X,
            "gridY": GRID_Y,
            "bounds": {"south": SOUTH, "west": WEST, "north": NORTH, "east": EAST},
            "capabilities": {
                "reflectivity": False,
                "precipitation": True,
                "wind": True,
                "temperature": True,
                "humidity": True,
                "mucape": True,
                "bulkShear": False,
                "vorticity850": False,
                "waterVapor": True,
            },
            "fields": {
                "lat": flatten(lat2, 4),
                "lon": flatten(lon2, 4),
                "reflectivity": flatten(zeros, 1),
                "precipitation": flatten(precip.reshape(GRID_Y, GRID_X), 2),
                "windSpeed": flatten(wind_kmh.reshape(GRID_Y, GRID_X), 1),
                "windDirection": flatten(wind_dir.reshape(GRID_Y, GRID_X), 0),
                "bulkShear": flatten(zeros, 1),
                "vorticity850": flatten(zeros, 7),
                "temperature": flatten(t.reshape(GRID_Y, GRID_X), 1),
                "humidity": flatten(rh.reshape(GRID_Y, GRID_X), 0),
                "mucape": flatten(cape.reshape(GRID_Y, GRID_X), 0),
                "waterVapor": flatten(tcwv.reshape(GRID_Y, GRID_X), 1),
            },
        }
        filename = f"ecmwf/f{step:03d}.json.gz"
        write_gz(output / filename, payload)
        frames.append({
            "index": len(frames),
            "forecastHour": step,
            "validTime": payload["validTime"],
            "localValidTime": valid.astimezone(BRT).isoformat(),
            "file": filename,
            "gridX": GRID_X,
            "gridY": GRID_Y,
        })

    metadata = {
        "schema": "sideral-model-metadata-v1",
        "model": "ecmwf",
        "provider": "ECMWF",
        "resolutionKm": 28,
        "runDate": run.strftime("%Y%m%d"),
        "runCycle": f"{run:%H}Z",
        "initTime": iso_z(run),
        "generatedAt": iso_z(dt.datetime.now(dt.timezone.utc)),
        "forecastLocalDate": target_date.isoformat(),
        "timezone": "America/Sao_Paulo",
        "scope": "next_local_day",
        "temporalResolutionMinutes": 180,
        "frameCount": len(frames),
        "frames": frames,
        "capabilities": {
            "reflectivity": False,
            "precipitation": True,
            "wind": True,
            "temperature": True,
            "humidity": True,
            "mucape": True,
            "bulkShear": False,
            "vorticity850": False,
            "waterVapor": True,
        },
        "note": "IFS Open Data nao fornece refletividade composta nativa neste produto; o site nao deve sintetizar dBZ.",
    }
    (output / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", choices=["icon", "ecmwf"], required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    output = Path(args.output)
    if output.exists():
        import shutil
        shutil.rmtree(output)
    output.mkdir(parents=True, exist_ok=True)
    if args.model == "icon":
        build_icon(output)
    else:
        build_ecmwf(output)
    metadata = json.loads((output / "metadata.json").read_text(encoding="utf-8"))
    print(json.dumps(metadata, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
