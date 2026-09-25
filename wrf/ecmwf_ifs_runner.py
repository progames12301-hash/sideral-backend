#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import json
import shutil
import tempfile
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
from eccodes import codes_get, codes_get_array, codes_get_values, codes_grib_new_from_file, codes_release
from scipy.spatial import cKDTree

BRT = ZoneInfo("America/Sao_Paulo")
SOUTH, NORTH, WEST, EAST = -60.0, 15.0, -85.0, -30.0
GRID_X, GRID_Y = 221, 301
LEVELS = [925, 850, 700, 500, 300, 200]
SFC_PARAMS = ["2t", "2d", "10u", "10v", "10fg", "msl", "sp", "tprate", "tp", "ptype", "mucape", "tcwv", "tcc"]
PL_PARAMS = ["gh", "t", "u", "v", "r", "w", "vo"]


def iso_z(v: dt.datetime) -> str:
    return v.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def flatten(a: np.ndarray, decimals: int) -> list[float]:
    a = np.nan_to_num(np.asarray(a, dtype=float), nan=0.0, posinf=0.0, neginf=0.0)
    return np.round(a, decimals).reshape(-1).tolist()


def write_gz(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
    with path.open("wb") as fh, gzip.GzipFile(filename="", mode="wb", fileobj=fh, compresslevel=9, mtime=0) as gz:
        gz.write(raw)


def read_grib(path: Path) -> list[dict]:
    out = []
    with path.open("rb") as fh:
        while True:
            gid = codes_grib_new_from_file(fh)
            if gid is None:
                break
            try:
                item = {"shortName": str(codes_get(gid, "shortName")), "values": np.asarray(codes_get_values(gid), dtype=float)}
                for key in ("endStep", "forecastTime", "level", "typeOfLevel", "units"):
                    try:
                        item[key] = codes_get(gid, key)
                    except Exception:
                        item[key] = None
                try:
                    item["latitudes"] = np.asarray(codes_get_array(gid, "latitudes"), dtype=float)
                    item["longitudes"] = np.asarray(codes_get_array(gid, "longitudes"), dtype=float)
                except Exception:
                    item["latitudes"] = item["longitudes"] = None
                out.append(item)
            finally:
                codes_release(gid)
    if not out:
        raise RuntimeError(f"GRIB vazio: {path}")
    return out


def grib_step(m: dict) -> int:
    value = m.get("endStep") if m.get("endStep") is not None else m.get("forecastTime")
    return int(value)


def rh_from_t_td(t_c, td_c):
    a, b = 17.625, 243.04
    return np.clip(100.0 * np.exp(a * td_c / (b + td_c) - a * t_c / (b + t_c)), 0, 100)


def td_from_t_rh(t_c, rh):
    a, b = 17.625, 243.04
    rh = np.clip(rh, 1e-3, 100)
    gamma = np.log(rh / 100) + a * t_c / (b + t_c)
    return b * gamma / (a - gamma)


def wind_dir(u, v):
    return (270.0 - np.degrees(np.arctan2(v, u))) % 360.0


def theta_e(temp_k, rh, pressure_hpa):
    temp_c = temp_k - 273.15
    td_c = td_from_t_rh(temp_c, rh)
    e = 6.112 * np.exp(17.67 * td_c / (td_c + 243.5))
    tl = 1.0 / (1.0 / (td_c + 273.15 - 56.0) + np.log(np.maximum(temp_k / (td_c + 273.15), 1.0)) / 800.0) + 56.0
    theta = temp_k * (1000.0 / pressure_hpa) ** 0.2854
    return theta * np.exp((3376.0 / tl - 2.54) * (e / pressure_hpa) * (1.0 + 0.81 * (e / pressure_hpa)))


def target_grid():
    ys = np.linspace(SOUTH, NORTH, GRID_Y)
    xs = np.linspace(WEST, EAST, GRID_X)
    lon2, lat2 = np.meshgrid(xs, ys)
    return lat2, lon2


def prepare_nearest(src_lat, src_lon):
    lat2, lon2 = target_grid()
    src_lon = ((src_lon + 180.0) % 360.0) - 180.0
    mask = ((src_lat >= SOUTH - 1.0) & (src_lat <= NORTH + 1.0) & (src_lon >= WEST - 1.0) & (src_lon <= EAST + 1.0))
    idx = np.flatnonzero(mask)
    if idx.size < 100:
        raise RuntimeError(f"Poucos pontos ECMWF no dominio: {idx.size}")
    tree = cKDTree(np.column_stack([src_lat[idx], src_lon[idx]]))
    _, near = tree.query(np.column_stack([lat2.ravel(), lon2.ravel()]), k=1)
    return lat2, lon2, idx[np.asarray(near, dtype=int)]


def retrieve(client, run, steps, params, path, levtype=None, levelist=None):
    request = {
        "date": run.strftime("%Y%m%d"),
        "time": int(run.strftime("%H")),
        "stream": "oper",
        "type": "fc",
        "step": steps,
        "param": params,
        "target": str(path),
    }
    if levtype:
        request["levtype"] = levtype
    if levelist is not None:
        request["levelist"] = levelist
    client.retrieve(**request)


def choose_run(client):
    latest = client.latest(stream="oper", type="fc", step=120, param=["2t", "msl"])
    if not isinstance(latest, dt.datetime):
        latest = dt.datetime.fromisoformat(str(latest))
    run = latest.replace(tzinfo=dt.timezone.utc) if latest.tzinfo is None else latest.astimezone(dt.timezone.utc)
    if run.hour not in (0, 12):
        raise RuntimeError(f"Rodada IFS inesperada: {run.isoformat()}")
    return run


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", required=True)
    args = ap.parse_args()
    out = Path(args.output)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    from ecmwf.opendata import Client
    client = Client(source="ecmwf", model="ifs", resol="0p25", preserve_request_order=False, infer_stream_keyword=False)
    run = choose_run(client)
    steps = list(range(3, 121, 3))
    print(f"ECMWF IFS selecionado: {run:%Y-%m-%d %H}Z; frames={len(steps)}; dominio={SOUTH},{WEST},{NORTH},{EAST}")

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        sfc_path, pl_path = tmp / "surface.grib2", tmp / "pressure.grib2"
        retrieve(client, run, steps, SFC_PARAMS, sfc_path, "sfc")
        retrieve(client, run, steps, PL_PARAMS, pl_path, "pl", LEVELS)
        sfc = read_grib(sfc_path)
        pl = read_grib(pl_path)

    first = next(m for m in sfc if m["latitudes"] is not None)
    lat2, lon2, nearest = prepare_nearest(first["latitudes"], first["longitudes"])
    by_sfc = {(m["shortName"], grib_step(m)): m for m in sfc}
    by_pl = {(m["shortName"], int(m["level"]), grib_step(m)): m for m in pl}

    def s(name, step):
        key = (name, step)
        if key not in by_sfc:
            raise RuntimeError(f"IFS sem campo de superficie {name} no F{step:03d}")
        return np.asarray(by_sfc[key]["values"], float)[nearest].reshape(GRID_Y, GRID_X)

    def p(name, level, step):
        key = (name, level, step)
        if key not in by_pl:
            raise RuntimeError(f"IFS sem {name} em {level} hPa no F{step:03d}")
        return np.asarray(by_pl[key]["values"], float)[nearest].reshape(GRID_Y, GRID_X)

    tp_cache = {step: np.maximum(s("tp", step), 0.0) * 1000.0 for step in steps}
    frames = []
    for step in steps:
        t2, td2 = s("2t", step) - 273.15, s("2d", step) - 273.15
        rh2 = rh_from_t_td(t2, td2)
        u10, v10 = s("10u", step), s("10v", step)
        precip = np.maximum(s("tprate", step), 0.0) * 3600.0
        tp = tp_cache[step]
        def qpf(hours):
            previous = tp_cache.get(step - hours)
            return np.maximum(tp - (previous if previous is not None else 0.0), 0.0)
        mslp, sp = s("msl", step) / 100.0, s("sp", step) / 100.0
        gust = s("10fg", step) * 3.6
        valid = run + dt.timedelta(hours=step)

        upper = {}
        for lev in LEVELS:
            tk = p("t", lev, step)
            rh = np.clip(p("r", lev, step), 0.0, 100.0)
            u, v = p("u", lev, step), p("v", lev, step)
            upper[str(lev)] = {
                "geopotentialHeight": flatten(p("gh", lev, step), 0),
                "temperature": flatten(tk - 273.15, 1),
                "windSpeed": flatten(np.hypot(u, v) * 3.6, 1),
                "windDirection": flatten(wind_dir(u, v), 0),
                "humidity": flatten(rh, 0),
                "vorticity": flatten(p("vo", lev, step), 7),
                "verticalVelocity": flatten(p("w", lev, step), 3),
                "thetaE": flatten(theta_e(tk, rh, lev), 1),
            }
            if lev == 850:
                upper[str(lev)]["dewpoint"] = flatten(td_from_t_rh(tk - 273.15, rh), 1)

        shear = np.hypot(p("u", 500, step) - p("u", 925, step), p("v", 500, step) - p("v", 925, step))
        fields = {
            "lat": flatten(lat2, 4), "lon": flatten(lon2, 4),
            "temperature": flatten(t2, 1), "dewpoint": flatten(td2, 1), "humidity": flatten(rh2, 0),
            "windSpeed": flatten(np.hypot(u10, v10) * 3.6, 1), "windDirection": flatten(wind_dir(u10, v10), 0),
            "windGust": flatten(gust, 1), "pressure": flatten(mslp, 1), "mslp": flatten(mslp, 1),
            "surfacePressure": flatten(sp, 1), "precipitation": flatten(precip, 2),
            "precipitationType": flatten(s("ptype", step), 0),
            "qpf3h": flatten(qpf(3), 2), "qpf6h": flatten(qpf(6), 2),
            "qpf24h": flatten(qpf(24), 2), "qpf120h": flatten(qpf(120), 2), "qpfTotal": flatten(tp, 2),
            "waterVapor": flatten(np.maximum(s("tcwv", step), 0), 1),
            "cloudCover": flatten(np.clip(s("tcc", step) * 100.0, 0, 100), 0),
            "mucape": flatten(np.maximum(s("mucape", step), 0), 0),
            "bulkShear": flatten(shear, 1), "vorticity850": flatten(p("vo", 850, step), 7),
            "upperAir": upper,
        }
        payload = {
            "schema": "sideral-ifs-v2", "model": "ecmwf-ifs", "provider": "ECMWF",
            "source": "ECMWF IFS HRES Open Data 0.25 degree", "resolutionKm": 9, "publishedGridDegrees": 0.25,
            "forecastHour": step, "initTime": iso_z(run), "runDate": run.strftime("%Y%m%d"),
            "runCycle": f"{run:%H}Z", "validTime": iso_z(valid), "gridX": GRID_X, "gridY": GRID_Y,
            "bounds": {"south": SOUTH, "west": WEST, "north": NORTH, "east": EAST},
            "units": {"temperature": "degC", "humidity": "%", "windSpeed": "km/h", "pressure": "hPa", "precipitation": "mm/h", "qpf": "mm", "waterVapor": "kg/m2", "bulkShear": "m/s", "mucape": "J/kg"},
            "capabilities": {"reflectivity": False, "isobars": True, "temperature": True, "humidity": True, "pressure": True, "precipitation": True, "qpf3h": True, "qpf6h": True, "qpf24h": True, "qpf120h": True, "wind": True, "gust": True, "mucape": True, "bulkShear": True, "upperAir": True, "vorticity850": True, "waterVapor": True, "frontogenesis": False, "lcl": False, "mlcape": False},
            "fields": fields,
        }
        write_gz(out / f"ecmwf/f{step:03d}.json.gz", payload)
        frames.append({"index": len(frames), "forecastHour": step, "validTime": payload["validTime"], "localValidTime": valid.astimezone(BRT).isoformat(), "file": f"ecmwf/f{step:03d}.json.gz", "gridX": GRID_X, "gridY": GRID_Y})

    metadata = {
        "schema": "sideral-model-metadata-v2", "model": "ecmwf-ifs", "provider": "ECMWF", "resolutionKm": 9,
        "publishedGridDegrees": 0.25, "runDate": run.strftime("%Y%m%d"), "runCycle": f"{run:%H}Z", "initTime": iso_z(run),
        "generatedAt": iso_z(dt.datetime.now(dt.timezone.utc)), "forecastHours": steps, "frameCount": len(frames),
        "temporalResolutionMinutes": 180, "timezone": "America/Sao_Paulo", "scope": "0-120h", "frames": frames,
        "grid": {"x": GRID_X, "y": GRID_Y, "south": SOUTH, "north": NORTH, "west": WEST, "east": EAST},
        "upperLevels": LEVELS,
        "capabilities": {"temperature": True, "humidity": True, "dewpoint": True, "pressure": True, "isobars": True, "wind": True, "gust": True, "precipitation": True, "qpf3h": True, "qpf6h": True, "qpf24h": True, "qpf120h": True, "qpfTotal": True, "ptype": True, "waterVapor": True, "cloudCover": True, "mucape": True, "bulkShear": True, "vorticity850": True, "upperAir": True, "reflectivity": False, "frontogenesis": False, "lcl": False, "mlcape": False},
        "note": "IFS Open Data nao fornece refletividade radar nativa. bulkShear e o modulo da diferenca vetorial dos ventos entre 925 e 500 hPa, usado como proxy de cisalhamento profundo.",
    }
    (out / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
