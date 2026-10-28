#!/usr/bin/env python3
"""Render native Skew-T profiles for 27 capitals from SIDERAL WRF Brasil 21 km WRFOUTs."""
from __future__ import annotations

import argparse
import datetime as dt
import gc
import importlib.util
import json
import math
import re
import tempfile
from pathlib import Path

import numpy as np
from netCDF4 import Dataset
from sharppy.sharptab import profile as shp_profile

ROOT = Path(__file__).resolve().parents[1]
RENDERER_PATH = ROOT / "tools" / "skewt" / "native_spc_render.py"
G = 9.80665
RD = 287.05
KAPPA = 0.2854
MS_TO_KT = 1.943844
HOURS = list(range(0, 49, 3))
GRID_SPACING_KM = 21.0

CAPITALS = [
    ("Rio Branco", "rio-branco", -9.9754, -67.8249),
    ("Maceió", "maceio", -9.6658, -35.7353),
    ("Macapá", "macapa", 0.0349, -51.0694),
    ("Manaus", "manaus", -3.1190, -60.0217),
    ("Salvador", "salvador", -12.9777, -38.5016),
    ("Fortaleza", "fortaleza", -3.7319, -38.5267),
    ("Brasília", "brasilia", -15.7939, -47.8828),
    ("Vitória", "vitoria", -20.3155, -40.3128),
    ("Goiânia", "goiania", -16.6869, -49.2648),
    ("São Luís", "sao-luis", -2.5307, -44.3068),
    ("Cuiabá", "cuiaba", -15.6014, -56.0979),
    ("Campo Grande", "campo-grande", -20.4697, -54.6201),
    ("Belo Horizonte", "belo-horizonte", -19.9167, -43.9345),
    ("Belém", "belem", -1.4558, -48.5039),
    ("João Pessoa", "joao-pessoa", -7.1195, -34.8450),
    ("Curitiba", "curitiba", -25.4284, -49.2733),
    ("Recife", "recife", -8.0476, -34.8770),
    ("Teresina", "teresina", -5.0892, -42.8019),
    ("Rio de Janeiro", "rio-de-janeiro", -22.9068, -43.1729),
    ("Natal", "natal", -5.7945, -35.2110),
    ("Porto Alegre", "porto-alegre", -30.0346, -51.2177),
    ("Porto Velho", "porto-velho", -8.7608, -63.8999),
    ("Boa Vista", "boa-vista", 2.8235, -60.6758),
    ("Florianópolis", "florianopolis", -27.5949, -48.5482),
    ("São Paulo", "sao-paulo", -23.5505, -46.6333),
    ("Aracaju", "aracaju", -10.9472, -37.0731),
    ("Palmas", "palmas", -10.1840, -48.3336),
]


def finite(value):
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def data_slice(ds: Dataset, name: str, indices):
    if name not in ds.variables:
        raise RuntimeError(f"WRFOUT sem variável obrigatória {name}")
    var = ds.variables[name]
    dims = var.dimensions
    if dims and dims[0].lower() == "time":
        key = (0,) + tuple(indices)
    else:
        key = tuple(indices)
    value = var[key]
    if np.ma.isMaskedArray(value):
        value = value.filled(np.nan)
    return np.asarray(value, dtype=float)


def grid2d(ds: Dataset, name: str) -> np.ndarray:
    var = ds.variables.get(name)
    if var is None:
        raise RuntimeError(f"WRFOUT sem grade de coordenadas {name}")
    value = var[0, :, :] if var.dimensions and var.dimensions[0].lower() == "time" else var[:, :]
    if np.ma.isMaskedArray(value):
        value = value.filled(np.nan)
    return np.asarray(value, dtype=float)


def valid_time(ds: Dataset) -> dt.datetime:
    var = ds.variables.get("Times")
    if var is None:
        raise RuntimeError("WRFOUT sem variável Times")
    raw = var[0] if var.dimensions and var.dimensions[0].lower() == "time" else var[:]
    text = "".join(
        part.decode("ascii") if isinstance(part, (bytes, np.bytes_)) else str(part)
        for part in np.asarray(raw).reshape(-1)
    ).strip()
    try:
        return dt.datetime.strptime(text, "%Y-%m-%d_%H:%M:%S")
    except ValueError as exc:
        raise RuntimeError(f"Timestamp WRF inválido em Times: {text!r}") from exc


def haversine_km(lat_grid, lon_grid, lat, lon):
    phi1 = np.radians(np.asarray(lat_grid, dtype=float))
    phi2 = math.radians(lat)
    dphi = phi1 - phi2
    dlambda = np.radians(np.asarray(lon_grid, dtype=float) - lon)
    a = np.sin(dphi / 2.0) ** 2 + np.cos(phi2) * np.cos(phi1) * np.sin(dlambda / 2.0) ** 2
    return 6371.0 * 2.0 * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0)))


def nearest_cells(lat_grid, lon_grid):
    cells = []
    for name, slug, lat, lon in CAPITALS:
        distance = haversine_km(lat_grid, lon_grid, lat, lon)
        distance[~np.isfinite(distance)] = np.inf
        if not np.isfinite(distance).any():
            raise RuntimeError(f"Grade WRF sem coordenadas válidas para {name}")
        j, i = np.unravel_index(int(np.argmin(distance)), distance.shape)
        km = float(distance[j, i])
        if not math.isfinite(km) or km > 35.0:
            raise RuntimeError(
                f"{name} está fora da cobertura útil do WRF Brasil 21 km "
                f"(célula mais próxima a {km:.1f} km; limite 35 km). "
                "Não será publicada uma sondagem extrapolada."
            )
        cells.append({
            "name": name, "slug": slug, "latitude": lat, "longitude": lon,
            "j": int(j), "i": int(i), "grid_latitude": float(lat_grid[j, i]),
            "grid_longitude": float(lon_grid[j, i]), "distance_km": km,
        })
    return cells


def dewpoint_from_mixing_ratio(q, pressure_hpa):
    q = max(1.0e-8, float(q))
    e = min(float(pressure_hpa) * q / (0.622 + q), float(pressure_hpa) * 0.98)
    log_e = math.log(max(e, 1.0e-5) / 6.112)
    return 243.5 * log_e / (17.67 - log_e)


def profile_column(ds: Dataset, cell: dict, valid: dt.datetime, fh: int):
    j, i = cell["j"], cell["i"]
    sl = slice(None)

    p_pa = data_slice(ds, "P", (sl, j, i)) + data_slice(ds, "PB", (sl, j, i))
    theta = data_slice(ds, "T", (sl, j, i)) + 300.0
    qv = data_slice(ds, "QVAPOR", (sl, j, i))
    ph = data_slice(ds, "PH", (sl, j, i))
    phb = data_slice(ds, "PHB", (sl, j, i))
    z = 0.5 * (ph[:-1] + ph[1:] + phb[:-1] + phb[1:]) / G

    u_faces = data_slice(ds, "U", (sl, j, slice(i, i + 2)))
    v_faces = data_slice(ds, "V", (sl, slice(j, j + 2), i))
    u = np.nanmean(u_faces, axis=-1) * MS_TO_KT
    v = np.nanmean(v_faces, axis=-2) * MS_TO_KT
    w_stag = data_slice(ds, "W", (sl, j, i))
    w = 0.5 * (w_stag[:-1] + w_stag[1:])

    ps_pa = float(data_slice(ds, "PSFC", (j, i)))
    terrain = float(data_slice(ds, "HGT", (j, i)))
    t2_k = float(data_slice(ds, "T2", (j, i)))
    q2 = float(data_slice(ds, "Q2", (j, i)))
    u10 = float(data_slice(ds, "U10", (j, i))) * MS_TO_KT
    v10 = float(data_slice(ds, "V10", (j, i))) * MS_TO_KT
    if not all(math.isfinite(x) for x in (ps_pa, terrain, t2_k, q2, u10, v10)):
        raise RuntimeError(f"{cell['name']} F{fh:03d}: dados de superfície incompletos")

    ps = ps_pa / 100.0
    rows = [{
        "p": ps, "z": terrain, "t": t2_k - 273.15,
        "td": min(t2_k - 273.15, dewpoint_from_mixing_ratio(q2, ps)),
        "u": u10, "v": v10, "omega": None,
    }]
    with np.errstate(invalid="ignore", divide="ignore", over="ignore"):
        pressure = p_pa / 100.0
        temp_k = theta * np.power(p_pa / 100000.0, KAPPA)
        temp_c = temp_k - 273.15
        dewpoint = np.asarray([
            dewpoint_from_mixing_ratio(q, p) if math.isfinite(float(q)) and math.isfinite(float(p)) else np.nan
            for q, p in zip(qv, pressure)
        ])
        density = p_pa / (RD * np.maximum(temp_k, 150.0))
        omega = -w * density * G

    for p, height, t, td, uu, vv, om in zip(
        pressure, z, temp_c, dewpoint, u, v, omega
    ):
        vals = (p, height, t, td, uu, vv)
        if not all(math.isfinite(float(x)) for x in vals):
            continue
        if float(p) >= ps - 1.0 or float(height) <= terrain + 5.0:
            continue
        rows.append({
            "p": float(p), "z": float(height), "t": float(t),
            "td": min(float(t), float(td)), "u": float(uu), "v": float(vv),
            "omega": float(om) if math.isfinite(float(om)) else None,
        })

    rows.sort(key=lambda row: row["p"], reverse=True)
    clean = []
    for row in rows:
        if clean and (
            abs(row["p"] - clean[-1]["p"]) < 0.05
            or row["z"] <= clean[-1]["z"] + 0.5
        ):
            continue
        clean.append(row)
    if len(clean) < 10:
        raise RuntimeError(f"{cell['name']} F{fh:03d}: apenas {len(clean)} níveis WRF válidos")

    pressure = np.asarray([r["p"] for r in clean], dtype=float)
    height = np.asarray([r["z"] for r in clean], dtype=float)
    temp = np.asarray([r["t"] for r in clean], dtype=float)
    dew = np.asarray([r["td"] for r in clean], dtype=float)
    uu = np.asarray([r["u"] for r in clean], dtype=float)
    vv = np.asarray([r["v"] for r in clean], dtype=float)
    omega = np.asarray([np.nan if r["omega"] is None else r["omega"] for r in clean], dtype=float)
    sounding = shp_profile.create_profile(
        profile="convective", pres=pressure, hght=height, tmpc=temp, dwpc=dew,
        u=uu, v=vv, omeg=np.ma.masked_invalid(omega), strictQC=False,
        latitude=float(cell["latitude"]), date=valid, location=cell["name"],
    )
    return sounding, clean, terrain


def load_renderer():
    spec = importlib.util.spec_from_file_location("sideral_native_spc_render_wrf_brasil", RENDERER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Renderizador Skew-T indisponível: {RENDERER_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def parse_args():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--out-root", required=True)
    ap.add_argument("--run-date", required=True, help="YYYYMMDD do ciclo de início WRF")
    ap.add_argument("--cycle", required=True, choices=["00", "06", "12", "18"])
    ap.add_argument("--source-run-id", required=True, type=int)
    return ap.parse_args()


def main():
    args = parse_args()
    run_dir = Path(args.run_dir).resolve()
    out_root = Path(args.out_root).resolve()
    out_root.mkdir(parents=True, exist_ok=True)
    for old in out_root.iterdir():
        if old.is_dir():
            import shutil
            shutil.rmtree(old)
        elif old.is_file():
            old.unlink()

    files = sorted(run_dir.glob("wrfout_d01_*"))
    if len(files) != 49:
        raise RuntimeError(f"Esperados 49 WRFOUTs horários F000–F048; encontrados {len(files)}")
    times = []
    for file in files:
        with Dataset(str(file), "r") as ds:
            times.append(valid_time(ds))
    if len(set(times)) != 49:
        raise RuntimeError("WRFOUTs contêm timestamps duplicados.")
    if any((times[i + 1] - times[i]).total_seconds() != 3600 for i in range(48)):
        raise RuntimeError("WRFOUTs não formam uma sequência horária contínua.")
    init = times[0]
    expected = dt.datetime.strptime(args.run_date + args.cycle, "%Y%m%d%H")
    if init != expected:
        raise RuntimeError(
            f"Ciclo real do WRF ({init:%Y-%m-%d %H:%MZ}) não corresponde ao ciclo selecionado "
            f"({expected:%Y-%m-%d %H:%MZ}); Skew-Ts não publicados."
        )

    with Dataset(str(files[0]), "r") as ds:
        lat_grid = grid2d(ds, "XLAT")
        lon_grid = grid2d(ds, "XLONG")
    cells = nearest_cells(lat_grid, lon_grid)
    print("[WRF Skew-T] Células nativas selecionadas:", flush=True)
    for cell in cells:
        print(
            f"  {cell['name']}: j={cell['j']} i={cell['i']} "
            f"grade={cell['grid_latitude']:.3f},{cell['grid_longitude']:.3f} "
            f"distância={cell['distance_km']:.1f} km",
            flush=True,
        )

    renderer = load_renderer()
    assets = []
    for fh in HOURS:
        file = files[fh]
        expected_valid = init + dt.timedelta(hours=fh)
        with Dataset(str(file), "r") as ds:
            actual_valid = valid_time(ds)
            if actual_valid != expected_valid:
                raise RuntimeError(
                    f"F{fh:03d}: timestamp {actual_valid:%Y-%m-%d %H:%MZ}, "
                    f"esperado {expected_valid:%Y-%m-%d %H:%MZ}"
                )
            for cell in cells:
                sounding, rows, terrain = profile_column(ds, cell, actual_valid, fh)
                city_dir = out_root / cell["slug"]
                city_dir.mkdir(parents=True, exist_ok=True)
                output_png = city_dir / f"{cell['slug']}-f{fh:03d}.png"
                meta = {
                    "model": "SIDERAL WRF Brasil 21 km",
                    "source": "WRF-ARW native WRFOUT",
                    "location": cell["name"],
                    "station": cell["name"] + " / SIDERAL WRF Brasil 21 km",
                    "latitude": cell["latitude"],
                    "longitude": cell["longitude"],
                    "grid_latitude": cell["grid_latitude"],
                    "grid_longitude": cell["grid_longitude"],
                    "grid_distance_km": cell["distance_km"],
                    "elevation": terrain,
                    "fh": fh,
                    "valid": actual_valid,
                    "run": init,
                }
                with tempfile.TemporaryDirectory(prefix=".render-", dir=str(city_dir)) as temp:
                    rendered = renderer.render_native_spc(sounding, Path(temp), meta)
                    native_png = Path(rendered) if rendered is not None else Path(temp) / "full.png"
                    if not native_png.is_file() or native_png.stat().st_size < 10000:
                        candidates = [Path(temp) / "full.png", Path(temp) / "skewt.png"]
                        native_png = next(
                            (p for p in candidates if p.is_file() and p.stat().st_size >= 10000),
                            native_png,
                        )
                    if not native_png.is_file() or native_png.stat().st_size < 10000:
                        raise RuntimeError(f"Renderizador não gerou PNG válido: {cell['name']} F{fh:03d}")
                    output_png.write_bytes(native_png.read_bytes())
                assets.append({
                    "file": f"{cell['slug']}-f{fh:03d}.png",
                    "city": cell["name"], "slug": cell["slug"],
                    "forecastHour": fh, "valid": actual_valid.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "gridDistanceKm": round(cell["distance_km"], 2),
                })
                print(f"[SHARPpy] {cell['name']} F{fh:03d}", flush=True)
                del sounding, rows
        gc.collect()

    expected_assets = len(CAPITALS) * len(HOURS)
    if len(assets) != expected_assets:
        raise RuntimeError(f"Esperados {expected_assets} PNGs; foram gerados {len(assets)}")

    tag = f"skewt-wrf-brasil21km-live-{args.cycle}"
    manifest = {
        "schema": "sideral-wrf-capitals-skewt-native-v1",
        "model": "SIDERAL WRF Brasil 21 km",
        "modelKey": "wrf-brasil-21km",
        "source": "WRF-ARW native WRFOUT",
        "resolutionKm": 21,
        "sourceRunId": args.source_run_id,
        "run_utc": init.strftime("%Y-%m-%d %H:%MZ"),
        "cycle": args.cycle,
        "forecast_hours": HOURS,
        "cities": [
            {
                "name": c["name"], "slug": c["slug"],
                "latitude": c["latitude"], "longitude": c["longitude"],
                "grid_latitude": c["grid_latitude"], "grid_longitude": c["grid_longitude"],
                "grid_distance_km": round(c["distance_km"], 2),
            }
            for c in cells
        ],
        "frames_per_city": len(HOURS),
        "png_count": len(assets),
        "assets": assets,
    }
    (out_root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )
    publication = {
        "schema": "sideral-wrf-capitals-skewt-publication-v1",
        "model": manifest["model"], "model_id": manifest["modelKey"],
        "tag": tag, "sourceRunId": args.source_run_id,
        "run_utc": manifest["run_utc"], "cycle": args.cycle,
        "cities": len(CAPITALS), "frames_per_city": len(HOURS),
        "asset_count": len(assets), "forecast_hours": HOURS,
        "download_base": (
            "https://github.com/progames12301-hash/sideral-backend/releases/download/"
            + tag + "/"
        ),
    }
    (out_root / "publication.json").write_text(
        json.dumps(publication, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )
    print(
        f"WRF Brasil 21 km Skew-T concluído: {len(CAPITALS)} capitais × {len(HOURS)} validades "
        f"= {len(assets)} PNGs; rodada {args.cycle}Z.",
        flush=True,
    )


if __name__ == "__main__":
    main()
