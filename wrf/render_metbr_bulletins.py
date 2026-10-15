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
from PIL import Image

MODEL = "METBR WRF 4 km"
COUNTRY_KEYS = {"Argentina", "Brazil", "Uruguay", "Paraguay", "Bolivia", "Chile"}

VARIABLES = {
    "reflectivity": {
        "label": "Refletividade máxima", "short": "Refletividade", "unit": "dBZ",
        "mode": "reflectivity",
    },
    "precipitation": {
        "label": "Precipitação acumulada", "short": "Precipitação", "unit": "mm",
        "cmap": "turbo", "min": 0.0, "max": 450.0,
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
        "cmap": "turbo", "min": 0.0, "max": 4000.0, 
    },
    "windSpeed": {
        "label": "Velocidade do vento a 10 m", "short": "Vento 10 m", "unit": "km/h",
        "cmap": "turbo", "min": 0.0, "max": 120.0, 
    },
    "windDirection": {
        "label": "Direção do vento a 10 m", "short": "Direção do vento", "unit": "°",
        "cmap": "twilight_shifted", "min": 0.0, "max": 360.0,
        "ticks": [0, 90, 180, 270, 360],
    },
    "bulkShear": {
        "label": "Cisalhamento profundo 0–6 km", "short": "Bulk shear 0–6 km", "unit": "m/s",
        "cmap": "turbo", "min": 15.0, "max": 50.0,
    },
    "vorticity850": {
        "label": "Vorticidade relativa em 850 hPa", "short": "Vorticidade 850 hPa", "unit": "10⁻⁵ s⁻¹",
        "cmap": "RdBu_r", "min": -30.0, "max": 30.0, "mode": "vorticity",
    },
    "waterVapor": {
        "label": "Água precipitável", "short": "Água precipitável", "unit": "mm",
        "cmap": "YlGnBu", "min": 0.0, "max": 70.0, 
    },
}

# Palette patterned after the reflectivity bulletin supplied as the reference.
REFL_BOUNDS = np.arange(0.0, 80.0, 5.0)
REFL_COLORS = [
    "#ffffff", "#20e8f0", "#04a5f4", "#1239f5", "#17c92b", "#55be24",
    "#e0e722", "#ffe21a", "#ffb000", "#ff6a00", "#ed1c24", "#9d0008",
    "#fa16da", "#d343ed", "#8650c6",
]
REFL_CMAP = mcolors.ListedColormap(REFL_COLORS, name="sideral_reflectivity")
REFL_CMAP.set_bad((1.0, 1.0, 1.0, 0.0))
REFL_NORM = mcolors.BoundaryNorm(REFL_BOUNDS, REFL_CMAP.N, clip=True)

# Sigma-like accumulation palette: dry cells stay white, trace amounts gray,
# then green/blue/yellow/orange/red and high-end muted/purple shades.
PRECIP_BOUNDS = np.asarray([
    0.2, 1.2, 2.5, 5.0, 7.5, 12.5, 17.5, 22.5, 30.0, 40.0,
    50.0, 75.0, 100.0, 150.0, 200.0, 250.0, 300.0, 350.0, 400.0, 450.0,
], dtype=np.float32)
PRECIP_COLORS = [
    "#c7c7c7", "#a5a5a5", "#727272", "#d4ffcc", "#8be68b",
    "#24b63b", "#0e7e19", "#3e91f2", "#dff4a0", "#ffb23e",
    "#f06b20", "#bb1018", "#7b271d", "#a0786d", "#b9abab",
    "#d5c8d8", "#b98bd6", "#d33ba9", "#8d35b5",
]
PRECIP_CMAP = mcolors.ListedColormap(PRECIP_COLORS, name="sideral_precipitation")
PRECIP_CMAP.set_bad((1.0, 1.0, 1.0, 0.0))
PRECIP_NORM = mcolors.BoundaryNorm(PRECIP_BOUNDS, PRECIP_CMAP.N, clip=True)


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
    arr[(~np.isfinite(arr)) | (arr <= -900.0) | (np.abs(arr) > 1e20)] = np.nan
    return arr


def color_setup(variable, field):
    """Return fixed, frame-comparable colors and a transparent no-data mask."""
    spec = VARIABLES.get(variable, {
        "label": variable, "short": variable, "unit": "", "cmap": "viridis",
        "min": 0.0, "max": 1.0,
    })
    arr = np.asarray(field, dtype=np.float32).copy()
    arr[(~np.isfinite(arr)) | (arr <= -900.0) | (np.abs(arr) > 1e20)] = np.nan
    finite = arr[np.isfinite(arr)]
    if finite.size == 0:
        return spec, None, None, np.ma.masked_invalid(arr)

    if variable == "reflectivity":
        # Use native WRF values directly. No blur/smoothing: isolated cells,
        # sharp gradients and gaps must remain faithful to REFL_10CM.
        arr = np.ma.masked_where((~np.isfinite(arr)) | (arr < 5.0), arr)
        cmap = REFL_CMAP.copy()
        cmap.set_bad((1.0, 1.0, 1.0, 0.0))
        return spec, cmap, REFL_NORM, arr

    if variable == "precipitation":
        arr = np.ma.masked_where((~np.isfinite(arr)) | (arr < 0.2), arr)
        cmap = PRECIP_CMAP.copy()
        cmap.set_bad((1.0, 1.0, 1.0, 0.0))
        return spec, cmap, PRECIP_NORM, arr

    if spec.get("mode") == "vorticity":
        arr = arr * 1e5
        # Near-zero vorticity is neutral background, not a painted raster.
        arr = np.ma.masked_where((~np.isfinite(arr)) | (np.abs(arr) < 1.0), arr)
        cmap = plt.get_cmap(spec["cmap"]).copy()
        cmap.set_bad((1.0, 1.0, 1.0, 0.0))
        norm = mcolors.TwoSlopeNorm(vmin=-30.0, vcenter=0.0, vmax=30.0)
        return spec, cmap, norm, arr

    # Keep neutral/no-event areas white instead of coloring zero with the first
    # shade of the palette. Thresholds are display-only; source JSON is untouched.
    # Values and color scales remain stable between forecast hours.
    display_floor = {
        "precipitation": 0.2,
        "humidity": 15.0,
        "mucape": 25.0,
        "windSpeed": 1.0,
        "bulkShear": 15.0,
        "waterVapor": 5.0,
    }.get(variable)
    if display_floor is not None:
        arr = np.ma.masked_where((~np.isfinite(arr)) | (arr < display_floor), arr)
    else:
        arr = np.ma.masked_invalid(arr)

    vmin = float(spec.get("min", np.nanpercentile(finite, 2)))
    vmax = float(spec.get("max", np.nanpercentile(finite, 98)))
    if not math.isfinite(vmin):
        vmin = 0.0
    if not math.isfinite(vmax) or vmax <= vmin:
        vmax = vmin + 1.0

    cmap = plt.get_cmap(spec.get("cmap", "viridis")).copy()
    cmap.set_bad((1.0, 1.0, 1.0, 0.0))
    norm = mcolors.Normalize(vmin=vmin, vmax=vmax, clip=True)
    return spec, cmap, norm, arr


def render_one(output_path, variable, values, lat, lon, init_time, valid_time, lead_hour,
               nx, ny, admin_geometries, admin_labels, sideral_logo,
               wind_speed=None, wind_direction=None):
    import cartopy.crs as ccrs
    import cartopy.feature as cfeature

    spec, cmap, norm, field = color_setup(variable, values)
    if cmap is None:
        return False

    finite_geo = np.isfinite(lat) & np.isfinite(lon) & (lat > -90) & (lat < 90) & (lon > -180) & (lon < 180)

    # Mask the model's two outer grid rows/columns to remove the colored
    # lateral-boundary fringe and avoid rendering invalid edge artifacts.
    display_array = np.ma.asarray(field, dtype=np.float32)
    display_data = np.asarray(np.ma.getdata(display_array), dtype=np.float32)
    display_mask = np.ma.getmaskarray(display_array).copy()
    edge = 2
    display_mask[:edge, :] = True
    display_mask[-edge:, :] = True
    display_mask[:, :edge] = True
    display_mask[:, -edge:] = True
    display_mask |= ~np.isfinite(display_data)
    field = np.ma.array(display_data, mask=display_mask)

    # Crop from the pixels that are actually rendered after no-data/weak-signal
    # masking, rather than from every finite native-grid value.
    crop_mask = finite_geo & ~display_mask
    if int(crop_mask.sum()) < 100:
        # Sparse convection can occupy only a handful of cells. Keep the full
        # native map context in that case instead of aborting the whole render.
        crop_mask = finite_geo.copy()
        crop_mask[:edge, :] = False
        crop_mask[-edge:, :] = False
        crop_mask[:, :edge] = False
        crop_mask[:, -edge:] = False
    if int(crop_mask.sum()) < 100:
        raise RuntimeError("grade geográfica nativa insuficiente para o recorte")
    west = float(np.nanmin(lon[crop_mask]))
    east = float(np.nanmax(lon[crop_mask]))
    south = float(np.nanmin(lat[crop_mask]))
    north = float(np.nanmax(lat[crop_mask]))
    pad_lon = max(0.008, (east - west) * 0.0005)
    pad_lat = max(0.008, (north - south) * 0.0005)
    extent = [west - pad_lon, east + pad_lon, south - pad_lat, north + pad_lat]

    init = parse_time(init_time)
    valid = parse_time(valid_time)
    # Near-square canvas with larger map area, suited to the two-map layout.
    # 4K square export: 3840 × 3840 pixels at 384 dpi. Higher output density
    # sharpens labels/coastlines and gives native 4-km grid points more pixels.
    fig = plt.figure(figsize=(10.0, 10.0), dpi=384, facecolor="white")
    ax = fig.add_axes([0.08, 0.15, 0.84, 0.75], projection=ccrs.PlateCarree())
    ax.set_facecolor("white")
    ax.set_extent(extent, crs=ccrs.PlateCarree())
    if variable == "reflectivity":
        # Native REFL_10CM values; finer draw levels only improve edge rendering.
        refl_levels = np.arange(0.0, 75.1, 1.0)
        mesh = ax.contourf(
            lon, lat, field, levels=refl_levels, cmap=cmap, norm=norm,
            extend="max", antialiased=True, corner_mask=True,
            transform=ccrs.PlateCarree(), zorder=2,
        )
    elif variable == "bulkShear":
        # Dense contour levels smooth the rendered gradients without modifying
        # the model's original values or inventing higher-resolution data.
        shear_levels = np.arange(15.0, 50.01, 0.5)
        mesh = ax.contourf(
            lon, lat, field, levels=shear_levels, cmap=cmap, norm=norm,
            extend="max", antialiased=True, corner_mask=True,
            transform=ccrs.PlateCarree(), zorder=2,
        )
    elif variable != "windDirection":
        # Continuous scalar variables are drawn as dense, antialiased filled
        # contours instead of blocky native-cell pcolormesh tiles.
        if variable == "precipitation":
            scalar_levels = np.linspace(0.2, 450.0, 240)
        elif variable == "vorticity850":
            scalar_levels = np.linspace(-30.0, 30.0, 181)
        elif variable == "mucape":
            scalar_levels = np.linspace(0.0, 4000.0, 161)
        else:
            scalar_levels = np.linspace(float(spec.get("min", 0.0)), float(spec.get("max", 1.0)), 161)
        mesh = ax.contourf(
            lon, lat, field, levels=scalar_levels, cmap=cmap, norm=norm,
            extend="both" if variable in {"temperature", "vorticity850"} else "max",
            antialiased=True, corner_mask=True,
            transform=ccrs.PlateCarree(), zorder=2,
        )
    else:
        # Direction is angular; keep native cells to avoid interpolation
        # artifacts around the 0°/360° discontinuity.
        mesh = ax.pcolormesh(
            lon, lat, field, cmap=cmap, norm=norm, shading="auto",
            transform=ccrs.PlateCarree(), rasterized=True, zorder=2,
        )

    # Overlay native 10-m wind barbs on the shear panel only. UI JSON uses km/h
    # and meteorological direction in degrees; Matplotlib expects knot components.
    if variable == "bulkShear" and wind_speed is not None and wind_direction is not None:
        speed = np.asarray(wind_speed, dtype=np.float32).reshape(ny, nx) / 1.852
        direction = np.asarray(wind_direction, dtype=np.float32).reshape(ny, nx)
        direction_rad = np.deg2rad(direction)
        with np.errstate(invalid="ignore"):
            u_knots = -speed * np.sin(direction_rad)
            v_knots = -speed * np.cos(direction_rad)
        valid_wind = (
            np.isfinite(speed) & np.isfinite(direction) &
            np.isfinite(u_knots) & np.isfinite(v_knots) & (speed >= 3.0 / 1.852)
        )
        # About 70 km between barbs on this 4-km grid, keeping a readable layout.
        stride = 18
        u_plot = np.ma.masked_where(~valid_wind, u_knots)[::stride, ::stride]
        v_plot = np.ma.masked_where(~valid_wind, v_knots)[::stride, ::stride]
        lon_plot = lon[::stride, ::stride]
        lat_plot = lat[::stride, ::stride]
        ax.barbs(
            lon_plot, lat_plot, u_plot, v_plot,
            length=4.3, linewidth=0.55, pivot="middle",
            barbcolor="#202020", flagcolor="#202020",
            barb_increments={"half": 5, "full": 10, "flag": 50},
            transform=ccrs.PlateCarree(), zorder=7, alpha=0.90,
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
            fontsize=6.2, color="#202020", fontweight="medium", zorder=8, clip_on=True,
            path_effects=[patheffects.withStroke(linewidth=1.8, foreground="white", alpha=0.94)],
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

    cb_ax = fig.add_axes([0.085, 0.065, 0.83, 0.018])
    cb = fig.colorbar(mesh, cax=cb_ax, orientation="horizontal")
    cb.ax.tick_params(labelsize=7, colors="#303030", length=2, pad=2)
    if variable == "reflectivity":
        cb.set_ticks([5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55, 60, 65, 70, 75])
    elif variable == "precipitation":
        cb.set_ticks(PRECIP_BOUNDS.tolist())
        cb.ax.tick_params(labelsize=5.5)
    elif variable == "windDirection":
        cb.set_ticks([0, 90, 180, 270, 360])
    elif variable == "bulkShear":
        cb.set_ticks([15, 20, 25, 30, 35, 40, 45, 50])
    cb.outline.set_edgecolor("#555555")
    cb.outline.set_linewidth(0.5)
    # Use the repository's official Sideral artwork, not a generated text logo.
    logo_height = 0.046
    logo_ratio = sideral_logo.shape[1] / max(1, sideral_logo.shape[0])
    logo_width = logo_height * logo_ratio * (fig.get_figheight() / fig.get_figwidth())
    logo_ax = fig.add_axes([0.978 - logo_width, 0.006, logo_width, logo_height])
    logo_ax.imshow(sideral_logo, interpolation="antialiased")
    logo_ax.set_axis_off()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(
        output_path, dpi=384, facecolor="white", format="png",
        bbox_inches=None, pad_inches=0,
        metadata={"Software": "Sideral METBR WRF bulletin renderer"},
    )
    plt.close(fig)
    # Strip the redundant alpha channel and compress the 4K PNG losslessly.
    with Image.open(output_path) as rendered:
        if rendered.size != (3840, 3840):
            raise RuntimeError(f"PNG fora de 4K: {rendered.size}, esperado (3840, 3840)")
        rendered.convert("RGB").save(output_path, format="PNG", optimize=True, compress_level=6)
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

    logo_path = Path(__file__).resolve().parents[1] / "assets" / "sideral-logo.png"
    if not logo_path.is_file():
        raise RuntimeError(f"Logo oficial Sideral não encontrada: {logo_path}")
    with Image.open(logo_path) as logo_source:
        sideral_logo = np.asarray(logo_source.convert("RGBA"))

    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    out_frames = []
    skipped_reflectivity_frames = []
    skipped_empty_frames = []
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
        wind_speed_values = variable_values(frame, "windSpeed", expected)
        wind_direction_values = variable_values(frame, "windDirection", expected)

        # A fully blank reflectivity map is invisible in the reference palette
        # (<5 dBZ is masked), so publishing it between active frames causes
        # flicker. Preserve F000; after F000 omit only its reflectivity PNG,
        # while keeping precipitation/temperature/etc. for that same hour.
        reflectivity_values = variable_values(frame, "reflectivity", expected)
        if reflectivity_values is None or not np.isfinite(reflectivity_values).any():
            raise RuntimeError(f"F{fh:03d}: refletividade ausente ou sem valores numéricos válidos.")
        finite_refl = reflectivity_values[np.isfinite(reflectivity_values)]
        max_dbz = float(np.max(finite_refl))
        skip_reflectivity_png = fh > 0 and not np.any(finite_refl >= 5.0)
        if skip_reflectivity_png:
            skipped_reflectivity_frames.append({
                "forecastHour": fh,
                "validTime": forecast_time,
                "maxReflectivityDbz": round(max_dbz, 3),
                "reason": "sem_ecos_visiveis_abaixo_de_5_dBZ",
            })
            print(
                f"[METBR BOLETINS] F{fh:03d}: refletividade PNG ignorado "
                f"(máximo {max_dbz:.2f} dBZ; F000 é preservado)"
            )

        for variable in variables:
            if variable == "reflectivity" and skip_reflectivity_png:
                continue
            values = reflectivity_values if variable == "reflectivity" else variable_values(frame, variable, expected)
            if values is None or not np.isfinite(values).any():
                continue
            out_path = folder / f"{variable}.png"
            if render_one(out_path, variable, values.reshape(ny, nx), lat, lon,
                          str(meta.get("initTime") or frame.get("initTime") or ""),
                          forecast_time, fh, nx, ny, admin_geometries, admin_labels, sideral_logo,
                          wind_speed=wind_speed_values.reshape(ny, nx) if wind_speed_values is not None else None,
                          wind_direction=wind_direction_values.reshape(ny, nx) if wind_direction_values is not None else None):
                available.append(variable)
                files[variable] = f"frames/f{fh:03d}/{variable}.png"
                rendered_count += 1
        frame_entry = {
            "forecastHour": fh,
            "validTime": forecast_time,
            "availableVariables": available,
            "files": files,
        }
        if skip_reflectivity_png:
            frame_entry["skippedVariables"] = {
                "reflectivity": "no_visible_echo_below_5_dBZ"
            }
        if available:
            out_frames.append(frame_entry)
        else:
            skipped_empty_frames.append({
                "forecastHour": fh,
                "validTime": forecast_time,
                "reason": "nenhum_png_gerado",
            })
        print(f"[METBR BOLETINS] F{fh:03d}: {', '.join(available) or 'sem PNG para publicar'}")

    generated = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    output_meta = {
        "schema": "sideral-metbr-bulletin-images-v1",
        "model": MODEL,
        "displayProcessing": {
            "nativeGrid": True,
            "reflectivitySmoothing": False,
            "fixedColorScales": True,
            "missingValuesTransparent": True,
            "skipBlankReflectivityAfterF000": True,
            "minimumVisibleReflectivityDbz": 5.0,
            "bulkShearDisplayFloorMs": 15.0,
            "windBarbs": "10 m; knots; every 18 native grid points",
            "outerModelBoundaryCellsMasked": 2,
            "renderResolutionPx": [3840, 3840],
            "scalarFieldRendering": "dense antialiased contours; native source grid preserved",
            "logo": "assets/sideral-logo.png",
        },
        "resolutionKm": 4,
        "nativeGrid": True,
        "initTime": meta["initTime"],
        "generatedAt": generated,
        "grid": {"nx": nx, "ny": ny},
        "frameCount": len(out_frames),
        "imageCount": rendered_count,
        "sourceFrameCount": len(frames),
        "skippedReflectivityFrameCount": len(skipped_reflectivity_frames),
        "skippedReflectivityFrames": skipped_reflectivity_frames,
        "skippedEmptyFrames": skipped_empty_frames,
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
    print(
        f"[METBR BOLETINS][OK] rodada={meta['initTime']}; "
        f"frames_publicados={len(out_frames)}/{len(frames)}; imagens={rendered_count}; "
        f"PNG_refletividade_ignorados={len(skipped_reflectivity_frames)}; "
        f"variáveis={','.join(variables)}"
    )
    return output_meta


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    build_bulletins(args.input_dir.resolve(), args.output_dir.resolve())


if __name__ == "__main__":
    main()
