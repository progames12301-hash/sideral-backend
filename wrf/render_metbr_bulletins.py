#!/usr/bin/env python3
"""Render the already-published METBR WRF JSON fields as bulletin PNGs.

This script reads the existing metbr-json-data contract without modifying it.
Output is a separate image package intended for the metbr-boletins-imagens branch.
"""
from __future__ import annotations

import argparse
import gzip
import json
import math
import shutil
from datetime import datetime, timezone
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import colors as mcolors
from matplotlib import patheffects
import numpy as np

MODEL = "METBR WRF 4 km"
COUNTRY_KEYS = {"Argentina", "Brazil", "Uruguay", "Paraguay", "Bolivia", "Chile"}

VARIABLES = {
    "reflectivity": {
        "label": "Refletividade máxima", "short": "Refletividade", "unit": "dBZ",
        "mode": "reflectivity",
    },
    "precipitation": {
        "label": "Precipitação acumulada", "short": "Precipitação", "unit": "mm",
        "cmap": "turbo", "min": 0.0, "max": 80.0, "mode": "dynamic-upper",
    },
    "temperature": {
        "label": "Temperatura a 2 m", "short": "Temperatura 2 m", "unit": "°C",
        "cmap": "coolwarm", "min": -10.0, "max": 40.0,
    },
    "humidity": {
        "label": "Umidade relativa a 2 m", "short": "Umidade 2 m", "unit": "%",
        "cmap": "YlGnBu", "min": 0.0, "max": 100.0,
    },
    "mucape": {
        "label": "CAPE de parcela mais instável", "short": "MUCAPE", "unit": "J/kg",
        "cmap": "turbo", "min": 0.0, "max": 4000.0, "mode": "dynamic-upper",
    },
    "windSpeed": {
        "label": "Velocidade do vento a 10 m", "short": "Vento 10 m", "unit": "km/h",
        "cmap": "turbo", "min": 0.0, "max": 120.0, "mode": "dynamic-upper",
    },
    "windDirection": {
        "label": "Direção do vento a 10 m", "short": "Direção do vento", "unit": "°",
        "cmap": "twilight_shifted", "min": 0.0, "max": 360.0,
        "ticks": [0, 90, 180, 270, 360],
    },
    "bulkShear": {
        "label": "Cisalhamento profundo 0–6 km", "short": "Bulk shear 0–6 km", "unit": "m/s",
        "cmap": "plasma", "min": 0.0, "max": 50.0, "mode": "dynamic-upper",
    },
    "vorticity850": {
        "label": "Vorticidade relativa em 850 hPa", "short": "Vorticidade 850 hPa", "unit": "10⁻⁵ s⁻¹",
        "cmap": "RdBu_r", "min": -30.0, "max": 30.0, "mode": "vorticity",
    },
    "waterVapor": {
        "label": "Água precipitável", "short": "Água precipitável", "unit": "mm",
        "cmap": "YlGnBu", "min": 0.0, "max": 70.0, "mode": "dynamic-upper",
    },
}

# Palette patterned after the reflectivity bulletin supplied as the reference.
REFL_BOUNDS = np.arange(5.0, 80.0, 5.0)
REFL_COLORS = [
    "#20e8f0", "#04a5f4", "#1239f5", "#17c92b", "#55be24", "#e0e722",
    "#ffe21a", "#ffb000", "#ff6a00", "#ed1c24", "#9d0008", "#fa16da",
    "#d343ed", "#8650c6",
]
REFL_CMAP = mcolors.ListedColormap(REFL_COLORS, name="sideral_reflectivity")
REFL_NORM = mcolors.BoundaryNorm(REFL_BOUNDS, REFL_CMAP.N, clip=True)


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(timezone.utc)


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def read_frame(path: Path):
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        return json.load(stream)


def load_admin1(bounds):
    """Load Natural Earth admin-1 geometries and province/state labels."""
    import cartopy.io.shapereader as shpreader
    from shapely.geometry import box

    shp_path = shpreader.natural_earth(
        resolution="50m", category="cultural", name="admin_1_states_provinces"
    )
    reader = shpreader.Reader(shp_path)
    bbox = box(bounds[0], bounds[2], bounds[1], bounds[3])
    geometries = []
    labels = []
    for record in reader.records():
        geometry = record.geometry
        if geometry is None or geometry.is_empty or not geometry.intersects(bbox):
            continue
        attrs = record.attributes
        admin = str(attrs.get("admin") or attrs.get("geonunit") or attrs.get("admin_name") or "")
        adm0 = str(attrs.get("adm0_a3") or attrs.get("sov_a3") or "")
        if admin not in COUNTRY_KEYS and adm0 not in {"ARG", "BRA", "URY", "PRY", "BOL", "CHL"}:
            continue
        clipped = geometry.intersection(bbox)
        if clipped.is_empty:
            continue
        geometries.append(clipped)
        point = geometry.representative_point()
        name = str(attrs.get("name_en") or attrs.get("name") or attrs.get("gn_name") or "").strip()
        if not name or name.lower() in {"<null>", "null", "-99"}:
            continue
        if bounds[0] <= point.x <= bounds[1] and bounds[2] <= point.y <= bounds[3]:
            labels.append((point.x, point.y, name))
    return geometries, labels


def variable_values(frame, variable, expected_size):
    fields = frame.get("fields") or {}
    raw = fields.get(variable)
    if variable == "reflectivity":
        raw = fields.get("reflectivity")
        if raw is None:
            raw = frame.get("reflectivityDbz")
    if not isinstance(raw, list) or len(raw) != expected_size:
        return None
    try:
        arr = np.asarray([np.nan if v is None else float(v) for v in raw], dtype=np.float32)
    except (TypeError, ValueError):
        return None
    arr[(~np.isfinite(arr)) | (arr <= -1000.0) | (np.abs(arr) > 1e20)] = np.nan
    return arr


def color_setup(variable, field):
    spec = VARIABLES.get(variable, {
        "label": variable, "short": variable, "unit": "", "cmap": "viridis",
        "min": 0.0, "max": 1.0,
    })
    arr = np.asarray(field, dtype=np.float32)
    finite = arr[np.isfinite(arr)]
    if finite.size == 0:
        return spec, None, None, arr

    if variable == "reflectivity":
        # Values under 5 dBZ are white, like the reference bulletin.
        arr = np.ma.masked_where((~np.isfinite(arr)) | (arr < 5.0), arr)
        return spec, REFL_CMAP, REFL_NORM, arr

    mode = spec.get("mode")
    if mode == "vorticity":
        arr = arr * 1e5
        finite = arr[np.isfinite(arr)]
        robust = float(np.nanpercentile(np.abs(finite), 98)) if finite.size else 1.0
        vmax = max(5.0, min(60.0, robust))
        return spec, plt.get_cmap(spec["cmap"]), mcolors.TwoSlopeNorm(vmin=-vmax, vcenter=0, vmax=vmax), arr

    vmin = float(spec.get("min", np.nanpercentile(finite, 2)))
    vmax = float(spec.get("max", np.nanpercentile(finite, 98)))
    if mode == "dynamic-upper":
        q98 = float(np.nanpercentile(finite, 98))
        vmax = max(vmin + 1.0, min(vmax, q98 if q98 > vmin else vmax))
    if variable == "precipitation":
        vmax = max(5.0, vmax)
    if not math.isfinite(vmin):
        vmin = 0.0
    if not math.isfinite(vmax) or vmax <= vmin:
        vmax = vmin + 1.0
    return spec, plt.get_cmap(spec.get("cmap", "viridis")), mcolors.Normalize(vmin=vmin, vmax=vmax, clip=True), arr


def render_one(output_path, variable, values, lat, lon, init_time, valid_time, lead_hour,
               nx, ny, admin_geometries, admin_labels):
    import cartopy.crs as ccrs
    import cartopy.feature as cfeature

    spec, cmap, norm, field = color_setup(variable, values)
    if cmap is None:
        return False

    finite_geo = np.isfinite(lat) & np.isfinite(lon) & (lat > -90) & (lat < 90) & (lon > -180) & (lon < 180)
    if int(finite_geo.sum()) < 100:
        raise RuntimeError("grade geográfica inválida")
    west = float(np.nanmin(lon[finite_geo]))
    east = float(np.nanmax(lon[finite_geo]))
    south = float(np.nanmin(lat[finite_geo]))
    north = float(np.nanmax(lat[finite_geo]))
    pad_lon = max(0.15, (east - west) * 0.012)
    pad_lat = max(0.15, (north - south) * 0.012)
    extent = [west - pad_lon, east + pad_lon, south - pad_lat, north + pad_lat]

    init = parse_time(init_time)
    valid = parse_time(valid_time)
    fig = plt.figure(figsize=(10.24, 7.69), dpi=100, facecolor="white")
    ax = fig.add_axes([0.105, 0.12, 0.79, 0.755], projection=ccrs.PlateCarree())
    ax.set_extent(extent, crs=ccrs.PlateCarree())
    mesh = ax.pcolormesh(
        lon, lat, field, cmap=cmap, norm=norm, shading="auto",
        transform=ccrs.PlateCarree(), rasterized=True, zorder=2,
    )
    ax.add_feature(cfeature.COASTLINE.with_scale("50m"), edgecolor="#343434", linewidth=0.55, zorder=4)
    ax.add_feature(cfeature.BORDERS.with_scale("50m"), edgecolor="#242424", linewidth=0.75, zorder=5)
    if admin_geometries:
        ax.add_geometries(
            admin_geometries, ccrs.PlateCarree(), facecolor="none",
            edgecolor="#303030", linewidth=0.45, zorder=6,
        )
    for x, y, label in admin_labels:
        ax.text(
            x, y, label, transform=ccrs.PlateCarree(), ha="center", va="center",
            fontsize=5.1, color="#202020", zorder=8, clip_on=True,
            path_effects=[patheffects.withStroke(linewidth=1.65, foreground="white", alpha=0.92)],
        )
    gl = ax.gridlines(
        crs=ccrs.PlateCarree(), draw_labels=True, linewidth=0.3,
        color="#5d6570", alpha=0.4, linestyle="--", x_inline=False, y_inline=False,
    )
    gl.top_labels = False
    gl.right_labels = False
    gl.xlabel_style = {"size": 7, "color": "#4b535c"}
    gl.ylabel_style = {"size": 7, "color": "#4b535c"}

    fig.text(0.022, 0.972, f"Rodada: {init:%d/%m/%Y %H}Z", fontsize=10, color="#222222", ha="left", va="top")
    fig.text(0.50, 0.972, "METBR WRF 4 km", fontsize=10.5, fontweight="bold", color="#222222", ha="center", va="top")
    fig.text(0.978, 0.972, f"Previsão: {valid:%d/%m/%Y %H}Z (+{lead_hour:03d}h)", fontsize=9, color="#222222", ha="right", va="top")
    fig.text(0.50, 0.938, f"{spec['label']} ({spec['unit']})", fontsize=10.5, color="#222222", ha="center", va="top")

    cb_ax = fig.add_axes([0.105, 0.067, 0.79, 0.022])
    cb = fig.colorbar(mesh, cax=cb_ax, orientation="horizontal")
    cb.ax.tick_params(labelsize=7, colors="#303030", length=2, pad=2)
    if variable == "reflectivity":
        cb.set_ticks([5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55, 60, 65, 70, 75])
    elif variable == "windDirection":
        cb.set_ticks([0, 90, 180, 270, 360])
    cb.outline.set_edgecolor("#555555")
    cb.outline.set_linewidth(0.5)
    fig.text(0.978, 0.024, "SIDERAL METEOROLOGIA", fontsize=7.5, fontweight="bold", color="#243b63", ha="right", va="bottom")
    fig.text(0.022, 0.024, f"Grade nativa {nx} × {ny}", fontsize=7, color="#4b535c", ha="left", va="bottom")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=100, facecolor="white", format="png", metadata={"Software": "Sideral METBR WRF bulletin renderer"})
    plt.close(fig)
    if output_path.stat().st_size < 5000:
        raise RuntimeError(f"PNG pequeno/inválido: {output_path}")
    return True


def build_bulletins(input_dir: Path, output_dir: Path):
    metadata_path = input_dir / "metadata.json"
    grid_path = input_dir / "grid.json"
    if not metadata_path.is_file() or not grid_path.is_file():
        raise RuntimeError("metadata.json/grid.json não encontrados na publicação METBR.")
    meta = read_json(metadata_path)
    grid_json = read_json(grid_path)
    if meta.get("model") != MODEL or int(meta.get("resolutionKm", 0)) != 4:
        raise RuntimeError("A branch de origem não contém METBR WRF 4 km.")
    nx, ny = int(meta["grid"]["nx"]), int(meta["grid"]["ny"])
    grid_fields = grid_json.get("fields") or {}
    lat_raw, lon_raw = grid_fields.get("lat"), grid_fields.get("lon")
    expected = nx * ny
    if not isinstance(lat_raw, list) or not isinstance(lon_raw, list) or len(lat_raw) != expected or len(lon_raw) != expected:
        raise RuntimeError(f"Grade METBR inválida; esperava {expected} latitudes/longitudes.")
    lat = np.asarray(lat_raw, dtype=np.float32).reshape(ny, nx)
    lon = np.asarray(lon_raw, dtype=np.float32).reshape(ny, nx)
    if not np.isfinite(lat).all() or not np.isfinite(lon).all():
        raise RuntimeError("A grade METBR possui coordenadas inválidas.")

    frames = sorted(meta.get("frames") or [], key=lambda x: int(x.get("forecastHour", -1)))
    if not frames:
        raise RuntimeError("METBR metadata não lista horários de previsão.")
    variable_candidates = set(meta.get("availableVariablesAnyFrame") or meta.get("availableVariables") or [])
    variables = [v for v in VARIABLES if v in variable_candidates]
    if "reflectivity" not in variables:
        raise RuntimeError("Refletividade nativa não está disponível na publicação METBR.")

    west = float(np.nanmin(lon))
    east = float(np.nanmax(lon))
    south = float(np.nanmin(lat))
    north = float(np.nanmax(lat))
    bounds = [west, east, south, north]
    admin_geometries, admin_labels = load_admin1(bounds)
    if not admin_geometries:
        raise RuntimeError("Não foram carregadas fronteiras de estados/províncias; cancelando a publicação.")

    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    out_frames = []
    rendered_count = 0
    for item in frames:
        fh = int(item.get("forecastHour", -1))
        data_file = str(item.get("dataFile") or f"frames/f{fh:03d}.json.gz")
        source_path = input_dir / data_file
        if not source_path.is_file() or source_path.stat().st_size <= 0:
            raise RuntimeError(f"Frame METBR ausente: {data_file}")
        frame = read_frame(source_path)
        if int(frame.get("forecastHour", fh)) != fh:
            raise RuntimeError(f"forecastHour inconsistente no arquivo {data_file}.")
        available = []
        files = {}
        forecast_time = str(item.get("time") or frame.get("validTime") or "")
        folder = output_dir / "frames" / f"f{fh:03d}"
        for variable in variables:
            values = variable_values(frame, variable, expected)
            if values is None or not np.isfinite(values).any():
                continue
            out_path = folder / f"{variable}.png"
            if render_one(out_path, variable, values.reshape(ny, nx), lat, lon,
                          str(meta.get("initTime") or frame.get("initTime") or ""),
                          forecast_time, fh, nx, ny, admin_geometries, admin_labels):
                available.append(variable)
                files[variable] = f"frames/f{fh:03d}/{variable}.png"
                rendered_count += 1
        if "reflectivity" not in available:
            raise RuntimeError(f"Frame F{fh:03d} não produziu boletim de refletividade.")
        out_frames.append({
            "forecastHour": fh,
            "validTime": forecast_time,
            "availableVariables": available,
            "files": files,
        })
        print(f"[METBR BOLETINS] F{fh:03d}: {', '.join(available)}")

    generated = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    output_meta = {
        "schema": "sideral-metbr-bulletin-images-v1",
        "model": MODEL,
        "resolutionKm": 4,
        "nativeGrid": True,
        "initTime": meta["initTime"],
        "generatedAt": generated,
        "grid": {"nx": nx, "ny": ny},
        "frameCount": len(out_frames),
        "imageCount": rendered_count,
        "availableVariables": variables,
        "variableCatalog": {key: {"label": VARIABLES[key]["label"], "short": VARIABLES[key]["short"], "unit": VARIABLES[key]["unit"]} for key in variables},
        "frames": out_frames,
    }
    (output_dir / "metadata.json").write_text(json.dumps(output_meta, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    (output_dir / ".nojekyll").write_text("", encoding="utf-8")

    for item in out_frames:
        for variable, relpath in item["files"].items():
            path = output_dir / relpath
            if not path.is_file() or path.stat().st_size < 5000:
                raise RuntimeError(f"Imagem de boletim inválida: {relpath}")
    print(f"[METBR BOLETINS][OK] rodada={meta['initTime']}; frames={len(out_frames)}; imagens={rendered_count}; variáveis={','.join(variables)}")
    return output_meta


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    build_bulletins(args.input_dir.resolve(), args.output_dir.resolve())


if __name__ == "__main__":
    main()
