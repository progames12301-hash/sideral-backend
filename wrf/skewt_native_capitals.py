#!/usr/bin/env python3
"""Generate native-column Skew-T PNGs for Brazilian capitals inside a WRF model domain."""
from __future__ import annotations

import argparse
import datetime as dt
import gc
import importlib.util
import json
import math
import re
import shutil
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


def read_var(ds: Dataset, name: str, indices):
    if name not in ds.variables:
        raise RuntimeError(f"WRFOUT sem variável vertical obrigatória {name}")
    var = ds.variables[name]
    key = ((0,) + tuple(indices)) if var.dimensions and var.dimensions[0].lower() == "time" else tuple(indices)
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


def choose_capital_cells(lat_grid, lon_grid, resolution_km):
    # A capital is included only if its nearest actual WRF grid point is close.
    # Threshold is about one-and-a-half grid diagonals and prevents extrapolating
    # a regional model beyond its domain.
    max_distance = max(6.0, float(resolution_km) * 1.05)
    selected, excluded = [], []
    for name, slug, lat, lon in CAPITALS:
        distance = haversine_km(lat_grid, lon_grid, lat, lon)
        distance[~np.isfinite(distance)] = np.inf
        if not np.isfinite(distance).any():
            excluded.append({"name": name, "slug": slug, "reason": "grade sem coordenadas válidas"})
            continue
        j, i = np.unravel_index(int(np.argmin(distance)), distance.shape)
        km = float(distance[j, i])
        if not math.isfinite(km) or km > max_distance:
            excluded.append({"name": name, "slug": slug, "nearest_grid_distance_km": round(km, 2) if math.isfinite(km) else None,
                             "reason": "fora da cobertura do domínio WRF"})
            continue
        selected.append({
            "name": name, "slug": slug, "latitude": lat, "longitude": lon,
            "j": int(j), "i": int(i), "grid_latitude": float(lat_grid[j, i]),
            "grid_longitude": float(lon_grid[j, i]), "distance_km": km,
        })
    return selected, excluded, max_distance


def dewpoint_from_mixing_ratio(q, pressure_hpa):
    q = max(1.0e-8, float(q))
    e = min(float(pressure_hpa) * q / (0.622 + q), float(pressure_hpa) * 0.98)
    log_e = math.log(max(e, 1.0e-5) / 6.112)
    return 243.5 * log_e / (17.67 - log_e)


def profile_column(ds: Dataset, cell: dict, valid: dt.datetime, fh: int):
    j, i = cell["j"], cell["i"]
    sl = slice(None)
    p_pa = read_var(ds, "P", (sl, j, i)) + read_var(ds, "PB", (sl, j, i))
    theta = read_var(ds, "T", (sl, j, i)) + 300.0
    qv = read_var(ds, "QVAPOR", (sl, j, i))
    ph = read_var(ds, "PH", (sl, j, i))
    phb = read_var(ds, "PHB", (sl, j, i))
    z = 0.5 * (ph[:-1] + ph[1:] + phb[:-1] + phb[1:]) / G
    u_faces = read_var(ds, "U", (sl, j, slice(i, i + 2)))
    v_faces = read_var(ds, "V", (sl, slice(j, j + 2), i))
    u = np.nanmean(u_faces, axis=-1) * MS_TO_KT
    v = np.nanmean(v_faces, axis=-1) * MS_TO_KT
    w_stag = read_var(ds, "W", (sl, j, i))
    w = 0.5 * (w_stag[:-1] + w_stag[1:])

    ps_pa = float(read_var(ds, "PSFC", (j, i)))
    terrain = float(read_var(ds, "HGT", (j, i)))
    t2_k = float(read_var(ds, "T2", (j, i)))
    q2 = float(read_var(ds, "Q2", (j, i)))
    u10 = float(read_var(ds, "U10", (j, i))) * MS_TO_KT
    v10 = float(read_var(ds, "V10", (j, i))) * MS_TO_KT
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
            dewpoint_from_mixing_ratio(q, p)
            if math.isfinite(float(q)) and math.isfinite(float(p)) and p > 0
            else np.nan
            for q, p in zip(qv, pressure)
        ])
        density = p_pa / (RD * np.maximum(temp_k, 150.0))
        omega = -w * density * G
    for p, height, t, td, uu, vv, om in zip(pressure, z, temp_c, dewpoint, u, v, omega):
        if not all(math.isfinite(float(x)) for x in (p, height, t, td, uu, vv)):
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
        if clean and (abs(row["p"] - clean[-1]["p"]) < 0.05 or row["z"] <= clean[-1]["z"] + 0.5):
            continue
        clean.append(row)
    if len(clean) < 10:
        raise RuntimeError(f"{cell['name']} F{fh:03d}: apenas {len(clean)} níveis WRF válidos")
    p = np.asarray([r["p"] for r in clean], dtype=float)
    h = np.asarray([r["z"] for r in clean], dtype=float)
    t = np.asarray([r["t"] for r in clean], dtype=float)
    td = np.asarray([r["td"] for r in clean], dtype=float)
    u = np.asarray([r["u"] for r in clean], dtype=float)
    v = np.asarray([r["v"] for r in clean], dtype=float)
    omega = np.asarray([np.nan if r["omega"] is None else r["omega"] for r in clean], dtype=float)
    sounding = shp_profile.create_profile(
        profile="convective", pres=p, hght=h, tmpc=t, dwpc=td, u=u, v=v,
        omeg=np.ma.masked_invalid(omega), strictQC=False,
        latitude=float(cell["latitude"]), date=valid, location=cell["name"],
    )
    return sounding, terrain


def load_renderer():
    spec = importlib.util.spec_from_file_location("sideral_native_spc_render_general_wrf", RENDERER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Renderizador Skew-T indisponível: {RENDERER_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def parse_args():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--out-root", required=True)
    ap.add_argument("--model-key", required=True, help="ID estável, por exemplo wrf-cbr-4km")
    ap.add_argument("--model-label", required=True)
    ap.add_argument("--resolution-km", type=float, required=True)
    ap.add_argument("--source-run-id", required=True, type=int)
    return ap.parse_args()


def main():
    args = parse_args()
    if not re.fullmatch(r"wrf-[a-z0-9-]+", args.model_key):
        raise SystemExit("model-key deve começar com wrf- e conter apenas letras, números e hífens.")
    if args.resolution_km <= 0:
        raise SystemExit("resolution-km deve ser positiva.")
    run_dir, out_root = Path(args.run_dir).resolve(), Path(args.out_root).resolve()
    files = sorted(run_dir.glob("wrfout_d01_*"))
    if len(files) < 5:
        raise RuntimeError(f"Esperados pelo menos 5 WRFOUTs; encontrados {len(files)}")
    timed_files = []
    for file in files:
        with Dataset(str(file), "r") as ds:
            timed_files.append((valid_time(ds), file))
    timed_files.sort(key=lambda item: item[0])
    times = [item[0] for item in timed_files]
    if len(set(times)) != len(times):
        raise RuntimeError("WRFOUTs contêm timestamps duplicados.")
    if any(times[i + 1] <= times[i] for i in range(len(times) - 1)):
        raise RuntimeError("WRFOUTs não estão em ordem temporal crescente.")
    gaps = {(times[i + 1] - times[i]).total_seconds() / 3600 for i in range(len(times) - 1)}
    if not gaps.issubset({1.0, 3.0}):
        raise RuntimeError(f"Intervalo de WRFOUT não suportado: {sorted(gaps)} h (esperado 1 h ou 3 h)")
    init = times[0]
    selected_frames = [
        (int((valid - init).total_seconds() // 3600), file, valid)
        for valid, file in timed_files
        if int((valid - init).total_seconds()) % 3600 == 0
        and int((valid - init).total_seconds() // 3600) % 3 == 0
        and 0 <= (valid - init).total_seconds() / 3600 <= 48
    ]
    forecast_hours = [frame[0] for frame in selected_frames]
    if len(selected_frames) < 5 or forecast_hours[0] != 0 or len(set(forecast_hours)) != len(forecast_hours):
        raise RuntimeError(f"Sequência insuficiente para Skew-T em passos de 3 h: {forecast_hours}")
    if forecast_hours != list(range(0, forecast_hours[-1] + 1, 3)):
        raise RuntimeError(f"Sequência de validades Skew-T incompleta: {forecast_hours}")

    with Dataset(str(timed_files[0][1]), "r") as ds:
        lat_grid, lon_grid = grid2d(ds, "XLAT"), grid2d(ds, "XLONG")
    cells, excluded, max_distance = choose_capital_cells(lat_grid, lon_grid, args.resolution_km)
    if not cells:
        raise RuntimeError("Nenhuma capital brasileira está dentro do domínio útil deste WRF; nada foi publicado.")
    print(f"[WRF Skew-T] {args.model_label}: {len(cells)}/27 capitais dentro da grade; distância máxima {max_distance:.1f} km.", flush=True)
    for cell in cells:
        print(f"  {cell['name']}: grid {cell['grid_latitude']:.3f},{cell['grid_longitude']:.3f}; {cell['distance_km']:.1f} km", flush=True)

    if out_root.exists():
        shutil.rmtree(out_root)
    out_root.mkdir(parents=True, exist_ok=True)
    renderer = load_renderer()
    assets = []
    for fh, file, valid in selected_frames:
        with Dataset(str(file), "r") as ds:
            if valid_time(ds) != valid:
                raise RuntimeError(f"F{fh:03d}: timestamp mudou ao reabrir {file.name}")
            for cell in cells:
                sounding, terrain = profile_column(ds, cell, valid, fh)
                city_dir = out_root / cell["slug"]
                city_dir.mkdir(parents=True, exist_ok=True)
                output_png = city_dir / f"{cell['slug']}-f{fh:03d}.png"
                meta = {
                    "model": args.model_label, "source": "WRF-ARW native WRFOUT",
                    "location": cell["name"], "station": cell["name"] + " / " + args.model_label,
                    "latitude": cell["latitude"], "longitude": cell["longitude"],
                    "grid_latitude": cell["grid_latitude"], "grid_longitude": cell["grid_longitude"],
                    "grid_distance_km": cell["distance_km"], "elevation": terrain,
                    "fh": fh, "valid": valid, "run": init,
                }
                with tempfile.TemporaryDirectory(prefix=".skewt-render-", dir=str(city_dir)) as temp:
                    rendered = renderer.render_native_spc(sounding, Path(temp), meta)
                    native = Path(rendered) if rendered is not None else Path(temp) / "full.png"
                    if not native.is_file() or native.stat().st_size < 10000:
                        native = next((p for p in (Path(temp) / "full.png", Path(temp) / "skewt.png")
                                       if p.is_file() and p.stat().st_size >= 10000), native)
                    if not native.is_file() or native.stat().st_size < 10000:
                        raise RuntimeError(f"PNG inválido: {cell['name']} F{fh:03d}")
                    output_png.write_bytes(native.read_bytes())
                assets.append({
                    "file": f"{cell['slug']}-f{fh:03d}.png", "city": cell["name"],
                    "slug": cell["slug"], "forecastHour": fh,
                    "valid": valid.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "gridDistanceKm": round(cell["distance_km"], 2),
                })
                print(f"[SHARPpy] {args.model_label} | {cell['name']} F{fh:03d}", flush=True)
                del sounding
        gc.collect()

    expected_count = len(cells) * len(selected_frames)
    if len(assets) != expected_count:
        raise RuntimeError(f"Esperados {expected_count} PNGs; gerados {len(assets)}")
    cycle = init.strftime("%H")
    tag = f"skewt-{args.model_key}-live"
    manifest = {
        "schema": "sideral-wrf-capitals-skewt-native-v1",
        "model": args.model_label, "modelKey": args.model_key,
        "source": "WRF-ARW native WRFOUT", "resolutionKm": args.resolution_km,
        "sourceRunId": args.source_run_id,
        "run_utc": init.strftime("%Y-%m-%d %H:%MZ"), "cycle": cycle,
        "forecast_hours": forecast_hours, "cities": [
            {
                "name": c["name"], "slug": c["slug"],
                "latitude": c["latitude"], "longitude": c["longitude"],
                "grid_latitude": c["grid_latitude"], "grid_longitude": c["grid_longitude"],
                "grid_distance_km": round(c["distance_km"], 2),
            } for c in cells
        ],
        "excluded_capitals": excluded, "max_capital_grid_distance_km": round(max_distance, 2),
        "frames_per_city": len(selected_frames), "png_count": len(assets), "assets": assets,
    }
    (out_root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    publication = {
        "schema": "sideral-wrf-capitals-skewt-publication-v1",
        "model": args.model_label, "model_id": args.model_key, "tag": tag,
        "sourceRunId": args.source_run_id, "run_utc": manifest["run_utc"], "cycle": cycle,
        "cities": len(cells), "frames_per_city": len(selected_frames),
        "asset_count": len(assets), "forecast_hours": forecast_hours,
        "download_base": "https://github.com/progames12301-hash/sideral-backend/releases/download/" + tag + "/",
    }
    (out_root / "publication.json").write_text(json.dumps(publication, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"{args.model_label}: {len(cells)} capitais × {len(selected_frames)} validades = {len(assets)} PNGs; rodada {cycle}Z.", flush=True)


if __name__ == "__main__":
    main()
