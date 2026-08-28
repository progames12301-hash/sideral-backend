#!/usr/bin/env python3
"""Publica grades reais e leves para o backend de modelos.

Fontes sem chave:
- GFS 0.25 grau: NOAA/NCEP NOMADS;
- AIFS single 0.25 grau: ECMWF Open Data.

O script nao executa WRF e nao acessa os diretorios do WRF.
"""

from __future__ import annotations

import argparse
import datetime as dt
import gzip
import json
import tempfile
from pathlib import Path
from typing import Any
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
SOUTH, NORTH, WEST, EAST = -60.0, 15.0, -85.0, -30.0
GRID_X, GRID_Y = 221, 301
UA = "SideralMeteorologia/3.0 (+https://sideralmeteorologiabrasil.web.app)"
GFS_FILTER_URL = "https://nomads.ncep.noaa.gov/cgi-bin/filter_gfs_0p25.pl"
GFS_AWS_BASE = "https://noaa-gfs-bdp-pds.s3.amazonaws.com"
AIFS_PARAMS = ["2t", "2d", "10u", "10v", "tp"]


def iso_z(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def flatten(values: np.ndarray, decimals: int) -> list[float]:
    array = np.asarray(values, dtype=float)
    if not np.isfinite(array).any():
        raise ValueError("Campo sem valores finitos.")
    return np.round(np.nan_to_num(array, nan=0.0, posinf=0.0, neginf=0.0), decimals).reshape(-1).tolist()


def write_gzip_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    with path.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, compresslevel=9, mtime=0) as compressed:
            compressed.write(body)


def read_grib(path: Path) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    with path.open("rb") as stream:
        while True:
            gid = codes_grib_new_from_file(stream)
            if gid is None:
                break
            try:
                item: dict[str, Any] = {
                    "shortName": str(codes_get(gid, "shortName")),
                    "values": np.asarray(codes_get_values(gid), dtype=np.float64),
                }
                for key in ("startStep", "endStep", "forecastTime", "typeOfLevel", "level", "units"):
                    try:
                        item[key] = codes_get(gid, key)
                    except Exception:
                        item[key] = None
                try:
                    item["latitudes"] = np.asarray(codes_get_array(gid, "latitudes"), dtype=np.float64)
                    item["longitudes"] = np.asarray(codes_get_array(gid, "longitudes"), dtype=np.float64)
                except Exception:
                    item["latitudes"], item["longitudes"] = None, None
                messages.append(item)
            finally:
                codes_release(gid)
    if not messages:
        raise RuntimeError(f"Nenhuma mensagem GRIB em {path}.")
    return messages


def read_grib_bytes(body: bytes) -> list[dict[str, Any]]:
    if not body.startswith(b"GRIB"):
        raise RuntimeError("A fonte nao retornou GRIB2.")
    with tempfile.NamedTemporaryFile(suffix=".grib2", delete=False) as temporary:
        temporary.write(body)
        path = Path(temporary.name)
    try:
        return read_grib(path)
    finally:
        path.unlink(missing_ok=True)


def target_grid() -> tuple[np.ndarray, np.ndarray]:
    lat = np.linspace(SOUTH, NORTH, GRID_Y)
    lon = np.linspace(WEST, EAST, GRID_X)
    lon2, lat2 = np.meshgrid(lon, lat)
    return lat2, lon2


def nearest_indices(source_lat: np.ndarray, source_lon: np.ndarray, target_lat: np.ndarray, target_lon: np.ndarray) -> np.ndarray:
    longitude = ((np.asarray(source_lon, dtype=float) + 180.0) % 360.0) - 180.0
    latitude = np.asarray(source_lat, dtype=float)
    margin = 1.0
    mask = (
        (latitude >= SOUTH - margin) & (latitude <= NORTH + margin)
        & (longitude >= WEST - margin) & (longitude <= EAST + margin)
    )
    source_index = np.flatnonzero(mask)
    if source_index.size < 100:
        raise RuntimeError(f"Cobertura insuficiente no dominio: {source_index.size} pontos.")
    tree = cKDTree(np.column_stack([latitude[source_index], longitude[source_index]]))
    _, local = tree.query(np.column_stack([target_lat.ravel(), target_lon.ravel()]), k=1)
    return source_index[np.asarray(local, dtype=int)]


def next_local_day_steps(run: dt.datetime, interval_hours: int, maximum: int = 72) -> tuple[dt.date, list[int]]:
    # O produto e sempre para amanha em Brasilia. Usar a data local da rodada
    # fazia uma inicializacao 00Z ainda pertencer ao dia anterior em BRT e
    # republicava uma previsao vencida.
    target = dt.datetime.now(BRT).date() + dt.timedelta(days=1)
    steps = [
        step for step in range(0, maximum + 1, interval_hours)
        if (run + dt.timedelta(hours=step)).astimezone(BRT).date() == target
    ]
    expected = 24 // interval_hours
    if len(steps) != expected:
        raise RuntimeError(f"Rodada {iso_z(run)} nao cobre o dia local completo: {steps}.")
    return target, steps


def cycle_candidates(hours: int = 54) -> list[dt.datetime]:
    now = dt.datetime.now(dt.timezone.utc)
    base = now.replace(hour=(now.hour // 6) * 6, minute=0, second=0, microsecond=0)
    return [base - dt.timedelta(hours=offset) for offset in range(0, hours + 1, 6)]


def wind_direction(u: np.ndarray, v: np.ndarray) -> np.ndarray:
    value = (270.0 - np.degrees(np.arctan2(v, u))) % 360.0
    return np.where((np.abs(u) + np.abs(v)) < 1e-8, 0.0, value)


def relative_humidity(t_c: np.ndarray, td_c: np.ndarray) -> np.ndarray:
    a, b = 17.625, 243.04
    return np.clip(100.0 * np.exp((a * td_c / (b + td_c)) - (a * t_c / (b + t_c))), 0.0, 100.0)


def message_step(message: dict[str, Any]) -> int:
    value = message.get("endStep")
    if value is None:
        value = message.get("forecastTime")
    return int(value)


def select_message(messages: list[dict[str, Any]], names: tuple[str, ...], *, step: int | None = None) -> dict[str, Any]:
    matches = [item for item in messages if str(item.get("shortName")) in names and (step is None or message_step(item) == step)]
    if not matches:
        available = sorted({str(item.get("shortName")) for item in messages})
        raise RuntimeError(f"Campo {names} ausente; disponiveis: {available}.")
    return matches[0]


def base_payload(model: str, source: str, run: dt.datetime, step: int, target_lat: np.ndarray, target_lon: np.ndarray, fields: dict[str, np.ndarray], capabilities: dict[str, bool]) -> dict[str, Any]:
    valid = run + dt.timedelta(hours=step)
    published_fields = {
        "lat": flatten(target_lat, 4),
        "lon": flatten(target_lon, 4),
    }
    decimals = {"precipitation": 2, "windSpeed": 1, "windDirection": 0, "temperature": 1, "humidity": 0, "mucape": 0, "waterVapor": 1}
    for name, values in fields.items():
        published_fields[name] = flatten(values, decimals.get(name, 2))
    return {
        "schema": "sideral-model-grid-v1",
        "model": model,
        "source": source,
        "runDate": run.strftime("%Y%m%d"),
        "runCycle": f"{run:%H}Z",
        "initTime": iso_z(run),
        "forecastHour": step,
        "validTime": iso_z(valid),
        "gridX": GRID_X,
        "gridY": GRID_Y,
        "bounds": {"south": SOUTH, "west": WEST, "north": NORTH, "east": EAST},
        "capabilities": capabilities,
        "fields": published_fields,
    }


def publish_metadata(output: Path, model: str, provider: str, resolution_km: int, run: dt.datetime, target_date: dt.date, interval_minutes: int, frames: list[dict[str, Any]], capabilities: dict[str, bool], note: str, precipitation_product: str, precipitation_is_rate: bool) -> None:
    metadata = {
        "schema": "sideral-model-metadata-v1",
        "model": model,
        "provider": provider,
        "resolutionKm": resolution_km,
        "runDate": run.strftime("%Y%m%d"),
        "runCycle": f"{run:%H}Z",
        "initTime": iso_z(run),
        "generatedAt": iso_z(dt.datetime.now(dt.timezone.utc)),
        "forecastLocalDate": target_date.isoformat(),
        "timezone": "America/Sao_Paulo",
        "scope": "next_local_day",
        "temporalResolutionMinutes": interval_minutes,
        "frameCount": len(frames),
        "frames": frames,
        "capabilities": capabilities,
        "precipitationProduct": precipitation_product,
        "precipitationIsRate": precipitation_is_rate,
        "note": note,
    }
    (output / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")


def discover_gfs() -> tuple[dt.datetime, dt.date, list[int]]:
    for run in cycle_candidates():
        try:
            # GFS publica APCP em janelas sinoticas de 3 h. Usar os passos de
            # 3 h evita sobrepor acumulados de 1 h e 3 h no mesmo produto.
            target_date, steps = next_local_day_steps(run, 3, 72)
        except RuntimeError:
            continue
        last = steps[-1]
        url = f"{GFS_AWS_BASE}/gfs.{run:%Y%m%d}/{run:%H}/atmos/gfs.t{run:%H}z.pgrb2.0p25.f{last:03d}"
        try:
            response = requests.head(url, headers={"User-Agent": UA}, timeout=20)
            if response.status_code in {200, 206}:
                return run, target_date, steps
        except requests.RequestException:
            continue
    raise RuntimeError("Nenhuma rodada GFS completa encontrada.")


def download_gfs(run: dt.datetime, step: int) -> list[dict[str, Any]]:
    params = {
        "file": f"gfs.t{run:%H}z.pgrb2.0p25.f{step:03d}",
        "dir": f"/gfs.{run:%Y%m%d}/{run:%H}/atmos",
        "subregion": "",
        "leftlon": str(WEST - 1), "rightlon": str(EAST + 1),
        "bottomlat": str(SOUTH - 1), "toplat": str(NORTH + 1),
        "lev_2_m_above_ground": "on", "lev_10_m_above_ground": "on",
        "lev_surface": "on", "lev_180-0_mb_above_ground": "on",
        "lev_entire_atmosphere_(considered_as_a_single_layer)": "on",
        "var_TMP": "on", "var_DPT": "on", "var_UGRD": "on", "var_VGRD": "on",
        "var_APCP": "on", "var_CAPE": "on", "var_PWAT": "on",
    }
    response = requests.get(GFS_FILTER_URL, params=params, headers={"User-Agent": UA}, timeout=180)
    response.raise_for_status()
    return read_grib_bytes(response.content)


def build_gfs(output: Path) -> None:
    run, target_date, steps = discover_gfs()
    target_lat, target_lon = target_grid()
    frames: list[dict[str, Any]] = []
    nearest: np.ndarray | None = None
    capabilities = {"reflectivity": False, "precipitation": True, "wind": True, "temperature": True, "humidity": True, "mucape": True, "bulkShear": False, "vorticity850": False, "waterVapor": True}
    for step in steps:
        print(f"GFS F{step:03d}", flush=True)
        messages = download_gfs(run, step)
        coordinate_message = next((item for item in messages if item.get("latitudes") is not None), None)
        if coordinate_message is None:
            raise RuntimeError("GFS sem coordenadas.")
        if nearest is None:
            nearest = nearest_indices(coordinate_message["latitudes"], coordinate_message["longitudes"], target_lat, target_lon)

        def values(names: tuple[str, ...]) -> np.ndarray:
            return np.asarray(select_message(messages, names)["values"], dtype=float)[nearest].reshape(GRID_Y, GRID_X)

        temperature = values(("2t", "t")) - 273.15
        dewpoint = values(("2d", "dpt")) - 273.15
        u, v = values(("10u", "u")), values(("10v", "v"))
        precip_message = select_message(messages, ("tp", "apcp"))
        precipitation = np.maximum(np.asarray(precip_message["values"], dtype=float)[nearest].reshape(GRID_Y, GRID_X), 0.0)
        cape = np.maximum(values(("cape",)), 0.0)
        water = np.maximum(values(("pwat", "tcwv")), 0.0)
        payload = base_payload(
            "gfs", "NOAA/NCEP GFS 0.25° via NOMADS", run, step, target_lat, target_lon,
            {"precipitation": precipitation, "windSpeed": np.hypot(u, v) * 3.6, "windDirection": wind_direction(u, v), "temperature": temperature, "humidity": relative_humidity(temperature, dewpoint), "mucape": cape, "waterVapor": water},
            capabilities,
        )
        filename = f"gfs/f{step:03d}.json.gz"
        write_gzip_json(output / filename, payload)
        valid = run + dt.timedelta(hours=step)
        frames.append({"index": len(frames), "forecastHour": step, "validTime": payload["validTime"], "localValidTime": valid.astimezone(BRT).isoformat(), "file": filename, "gridX": GRID_X, "gridY": GRID_Y})
    publish_metadata(output, "gfs", "NOAA/NCEP", 28, run, target_date, 180, frames, capabilities, "GFS oficial 0.25 grau; nao representa refletividade de radar.", "qpf3", False)


def build_aifs(output: Path) -> None:
    from ecmwf.opendata import Client

    client = Client(source="ecmwf", model="aifs-single", resol="0p25", infer_stream_keyword=False)
    latest = client.latest(type="fc", stream="oper", step=36, param=AIFS_PARAMS)
    run = latest if isinstance(latest, dt.datetime) else dt.datetime.fromisoformat(str(latest))
    run = run.replace(tzinfo=dt.timezone.utc) if run.tzinfo is None else run.astimezone(dt.timezone.utc)
    # AIFS Single Open Data publica os passos operacionais em 6 h.
    target_date, steps = next_local_day_steps(run, 6, 90)
    with tempfile.TemporaryDirectory() as directory:
        target = Path(directory) / "aifs.grib2"
        retrieve_steps = sorted({max(0, steps[0] - 6), *steps})
        client.retrieve(date=run.strftime("%Y%m%d"), time=int(run.strftime("%H")), stream="oper", type="fc", step=retrieve_steps, param=AIFS_PARAMS, target=str(target))
        messages = read_grib(target)

    coordinate_message = next((item for item in messages if item.get("latitudes") is not None), None)
    if coordinate_message is None:
        raise RuntimeError("AIFS sem coordenadas.")
    target_lat, target_lon = target_grid()
    nearest = nearest_indices(coordinate_message["latitudes"], coordinate_message["longitudes"], target_lat, target_lon)
    frames: list[dict[str, Any]] = []
    capabilities = {"reflectivity": False, "precipitation": True, "wind": True, "temperature": True, "humidity": True, "mucape": False, "bulkShear": False, "vorticity850": False, "waterVapor": False}
    for step in steps:
        print(f"AIFS F{step:03d}", flush=True)

        def values(names: tuple[str, ...]) -> np.ndarray:
            return np.asarray(select_message(messages, names, step=step)["values"], dtype=float)[nearest].reshape(GRID_Y, GRID_X)

        temperature = values(("2t",)) - 273.15
        dewpoint = values(("2d",)) - 273.15
        u, v = values(("10u", "u10")), values(("10v", "v10"))
        total = values(("tp",))
        previous_message = select_message(messages, ("tp",), step=max(0, step - 6))
        previous = np.asarray(previous_message["values"], dtype=float)[nearest].reshape(GRID_Y, GRID_X)
        units = str(select_message(messages, ("tp",), step=step).get("units") or "").lower()
        precipitation = np.maximum(total - previous, 0.0)
        if units in {"m", "metre", "metres"} or np.nanmax(total) < 5.0:
            precipitation = precipitation * 1000.0
        payload = base_payload(
            "aifs", "ECMWF AIFS Single 0.25° Open Data", run, step, target_lat, target_lon,
            {"precipitation": precipitation, "windSpeed": np.hypot(u, v) * 3.6, "windDirection": wind_direction(u, v), "temperature": temperature, "humidity": relative_humidity(temperature, dewpoint)},
            capabilities,
        )
        filename = f"aifs/f{step:03d}.json.gz"
        write_gzip_json(output / filename, payload)
        valid = run + dt.timedelta(hours=step)
        frames.append({"index": len(frames), "forecastHour": step, "validTime": payload["validTime"], "localValidTime": valid.astimezone(BRT).isoformat(), "file": filename, "gridX": GRID_X, "gridY": GRID_Y})
    publish_metadata(output, "aifs", "ECMWF", 28, run, target_date, 360, frames, capabilities, "AIFS Single oficial 0.25 grau; nao representa refletividade de radar.", "qpf6", False)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", choices=("gfs", "aifs"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        import shutil
        shutil.rmtree(args.output)
    args.output.mkdir(parents=True, exist_ok=True)
    (build_gfs if args.model == "gfs" else build_aifs)(args.output)
    metadata = json.loads((args.output / "metadata.json").read_text(encoding="utf-8"))
    if metadata.get("model") != args.model or not metadata.get("frames"):
        raise RuntimeError("Publicacao incompleta.")
    print(json.dumps({"model": args.model, "run": metadata["initTime"], "frames": metadata["frameCount"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()

