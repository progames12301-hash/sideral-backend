#!/usr/bin/env python3
"""Render a Sideral-style bulletin from a native TARC WRF NPZ release asset."""
from __future__ import annotations

import argparse
import datetime as dt
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import BoundaryNorm, ListedColormap
from matplotlib.ticker import MultipleLocator
import cartopy.crs as ccrs
import cartopy.feature as cfeature


BOUNDS = np.array([5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55, 60, 65, 70, 75, 80, 87.5], dtype=float)
DBZ_COLORS = [
    "#5e70b8", "#496bbd", "#438ed2", "#43b8d2",
    "#2cc6a4", "#25bd52", "#078b27", "#0b701f",
    "#c8dc00", "#f4e300", "#ffb400", "#ff6a00",
    "#e51c00", "#a90b38", "#d43d9a", "#ef8dde",
]
MODEL_BLUE = "#1557a5"
RED = "#e31b23"
INK = "#191f28"
MUTED = "#536273"


def parse_utc(value: str) -> dt.datetime:
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def load_native_frame(path: Path):
    with np.load(path, allow_pickle=False) as data:
        required = ("lat", "lon")
        missing = [key for key in required if key not in data.files]
        if missing:
            raise RuntimeError(f"NPZ não contém as coordenadas nativas: {missing}")
        lat = np.asarray(data["lat"], dtype=np.float32)
        lon = np.asarray(data["lon"], dtype=np.float32)

        if "reflectivity_composite_dbz" in data.files:
            refl = np.asarray(data["reflectivity_composite_dbz"], dtype=np.float32)
            field_name = "REFL_10CM nativa — máximo vertical"
        elif "reflectivity_native_dbz" in data.files:
            refl = np.asarray(data["reflectivity_native_dbz"], dtype=np.float32)
            field_name = "REFL_10CM nativa — nível inferior"
        else:
            raise RuntimeError("NPZ sem REFL_10CM nativa (campo composto ou nível inferior).")

        u10 = np.asarray(data["u10_ms"], dtype=np.float32) if "u10_ms" in data.files else None
        v10 = np.asarray(data["v10_ms"], dtype=np.float32) if "v10_ms" in data.files else None

    if lat.ndim != 2 or lon.shape != lat.shape or refl.shape != lat.shape:
        raise RuntimeError(f"Grade incompatível: lat={lat.shape}, lon={lon.shape}, refl={refl.shape}")
    if u10 is not None and v10 is not None and (u10.shape != lat.shape or v10.shape != lat.shape):
        raise RuntimeError(f"Grade de vento incompatível: u10={u10.shape}, v10={v10.shape}")
    finite_coords = np.isfinite(lat) & np.isfinite(lon)
    if int(finite_coords.sum()) < 100:
        raise RuntimeError("As coordenadas geográficas do NPZ são inválidas.")
    refl = np.where(np.isfinite(refl) & (refl >= 5.0) & finite_coords, refl, np.nan)
    if not np.isfinite(refl).any():
        raise RuntimeError("A refletividade nativa não contém ecos >= 5 dBZ.")
    return lat, lon, refl, u10, v10, field_name


def rotate_grid_winds_to_east_north(lat, lon, u_grid, v_grid):
    """Approximate the local WRF grid-to-earth rotation from native lat/lon."""
    lat_rad = np.radians(lat.astype(np.float64))
    dlat_di = np.gradient(lat.astype(np.float64), axis=1)
    dlon_di = np.gradient(lon.astype(np.float64), axis=1)
    dlon_di = (dlon_di + 180.0) % 360.0 - 180.0
    dx = 6371000.0 * np.maximum(np.cos(lat_rad), 1e-4) * np.radians(dlon_di)
    dy = 6371000.0 * np.radians(dlat_di)
    angle = np.arctan2(dy, dx)
    ue = u_grid * np.cos(angle) - v_grid * np.sin(angle)
    vn = u_grid * np.sin(angle) + v_grid * np.cos(angle)
    return ue, vn


def add_x_mark(fig):
    # Two simple red strokes form the Sideral X mark without importing an image.
    from matplotlib.lines import Line2D
    fig.add_artist(Line2D([0.031, 0.050], [0.942, 0.980], transform=fig.transFigure,
                          color=RED, linewidth=6.0, solid_capstyle="butt"))
    fig.add_artist(Line2D([0.031, 0.050], [0.980, 0.942], transform=fig.transFigure,
                          color=RED, linewidth=6.0, solid_capstyle="butt"))


def render(args):
    lat, lon, refl, u10, v10, field_name = load_native_frame(Path(args.input))
    init = parse_utc(args.init_time)
    valid = dt.datetime.strptime(Path(args.input).stem, "%Y%m%d_%H%M%S").replace(tzinfo=dt.timezone.utc)
    forecast_hour = int(round((valid - init).total_seconds() / 3600.0))
    if forecast_hour < 0:
        raise RuntimeError(f"Tempo válido {valid.isoformat()} antecede a inicialização {init.isoformat()}.")

    valid_data = refl[np.isfinite(refl)]
    dbz_min, dbz_max = float(np.nanmin(valid_data)), float(np.nanmax(valid_data))
    ny, nx = refl.shape
    finite_coords = np.isfinite(lat) & np.isfinite(lon)
    lon_min, lon_max = float(np.nanmin(lon[finite_coords])), float(np.nanmax(lon[finite_coords]))
    lat_min, lat_max = float(np.nanmin(lat[finite_coords])), float(np.nanmax(lat[finite_coords]))

    # Zoom to the native TARC footprint with only a small geographic margin.
    # No continental overview and no reflectivity is fabricated outside the native grid.
    pad_lon = max(0.8, (lon_max - lon_min) * 0.08)
    pad_lat = max(0.7, (lat_max - lat_min) * 0.08)
    extent = [
        max(-180.0, lon_min - pad_lon),
        min(180.0, lon_max + pad_lon),
        max(-90.0, lat_min - pad_lat),
        min(90.0, lat_max + pad_lat),
    ]
    projection = ccrs.PlateCarree()
    cmap = ListedColormap(DBZ_COLORS, name="sideral_native_dbz")
    norm = BoundaryNorm(BOUNDS, cmap.N, clip=True)

    plt.rcParams.update({
        "font.family": "DejaVu Sans",
        "font.size": 9,
        "savefig.facecolor": "white",
        "figure.facecolor": "white",
    })
    fig = plt.figure(figsize=(16, 10), dpi=120, facecolor="white")
    add_x_mark(fig)
    fig.text(0.062, 0.968, "SIDERAL METEOROLOGIA", fontsize=12.5, weight="bold",
             color=INK, ha="left", va="center")
    fig.text(0.062, 0.936, "BOLETIM DE REFLETIVIDADE NATIVA", fontsize=17.5,
             weight="bold", color=INK, ha="left", va="center")
    fig.text(0.062, 0.907,
             f"WRF TARC 3 km  •  {field_name}  •  ZOOM REGIONAL / DOMÍNIO NATIVO TARC",
             fontsize=9.3, color=MUTED, ha="left", va="center")
    fig.text(0.966, 0.966, f"F{forecast_hour:03d}", fontsize=17, weight="bold",
             color=MODEL_BLUE, ha="right", va="center")
    fig.text(0.966, 0.934, f"Init: {init:%H}Z {init:%d/%m/%Y}",
             fontsize=9, color=INK, ha="right", va="center")
    fig.text(0.966, 0.908, f"Válido: {valid:%H}Z {valid:%d/%m/%Y}",
             fontsize=9, color=INK, ha="right", va="center")
    fig.text(0.966, 0.880, "sideralmeteorologia.com.br",
             fontsize=8.5, weight="bold", color=MODEL_BLUE, ha="right", va="center")
    fig.add_artist(plt.Line2D([0.025, 0.975], [0.889, 0.889], transform=fig.transFigure,
                              color=MODEL_BLUE, linewidth=1.4))

    ax = fig.add_axes([0.035, 0.145, 0.93, 0.725], projection=projection)
    ax.set_facecolor("#f7f9fc")
    ax.set_extent(extent, crs=projection)

    # National coast/borders and Brazilian state/province lines, drawn under
    # the reflectivity raster so the storm echoes remain the dominant layer.
    ax.add_feature(cfeature.LAND.with_scale("10m"), facecolor="#fffefb", edgecolor="none", zorder=0)
    ax.add_feature(cfeature.OCEAN.with_scale("10m"), facecolor="#f7f9fc", edgecolor="none", zorder=0)
    try:
        provinces = cfeature.NaturalEarthFeature(
            category="cultural", name="admin_1_states_provinces_lines",
            scale="10m", facecolor="none",
        )
        ax.add_feature(provinces, edgecolor="#5a6068", linewidth=0.42, alpha=0.8, zorder=3)
    except Exception:
        pass

    mesh = ax.pcolormesh(
        lon, lat, np.ma.masked_invalid(refl),
        cmap=cmap, norm=norm, shading="auto",
        transform=projection, rasterized=True, antialiased=False, zorder=2,
    )

    # Mark the perimeter from the outermost native grid coordinates. The outline
    # makes the actual model footprint obvious against the continental context.
    edge_lon = np.concatenate((lon[0, :], lon[1:, -1], lon[-1, -2::-1], lon[-2:0:-1, 0]))
    edge_lat = np.concatenate((lat[0, :], lat[1:, -1], lat[-1, -2::-1], lat[-2:0:-1, 0]))
    ax.plot(edge_lon, edge_lat, transform=projection, color="white",
            linewidth=2.8, linestyle="-", zorder=7)
    ax.plot(edge_lon, edge_lat, transform=projection, color="#17212a",
            linewidth=1.35, linestyle=(0, (5, 2)), zorder=8)
    ax.text(0.015, 0.965, "CONTORNO: DOMÍNIO NATIVO TARC 3 km",
            transform=ax.transAxes, ha="left", va="top", fontsize=8.0,
            color="#18202a", weight="bold",
            bbox={"facecolor":"white","edgecolor":"#bec7d2","alpha":0.95,"pad":3.0},
            zorder=9)

    ax.coastlines(resolution="10m", color="#24282e", linewidth=0.65, zorder=4)
    ax.add_feature(cfeature.BORDERS.with_scale("10m"), edgecolor="#202329",
                   linewidth=0.72, zorder=4)

    gl = ax.gridlines(crs=projection, draw_labels=True, linewidth=0.45,
                      color="#b9c4d0", alpha=0.78, linestyle="-", zorder=5)
    gl.xlocator = MultipleLocator(2)
    gl.ylocator = MultipleLocator(2)
    gl.top_labels = False
    gl.right_labels = False
    gl.xlabel_style = {"size": 7.5, "color": "#566373"}
    gl.ylabel_style = {"size": 7.5, "color": "#566373"}

    # Draw sparsely sampled 10-m wind barbs using the native U10/V10 fields.
    if u10 is not None and v10 is not None:
        ue, vn = rotate_grid_winds_to_east_north(lat, lon, u10, v10)
        stride_y, stride_x = max(1, ny // 14), max(1, nx // 13)
        selection = (slice(stride_y // 2, ny, stride_y), slice(stride_x // 2, nx, stride_x))
        lo, la = lon[selection], lat[selection]
        uu, vv = ue[selection] * 3.6, vn[selection] * 3.6
        good = np.isfinite(lo) & np.isfinite(la) & np.isfinite(uu) & np.isfinite(vv)
        if int(good.sum()) >= 8:
            ax.barbs(
                lo[good], la[good], uu[good], vv[good],
                transform=projection, length=4.1, pivot="middle",
                color="#252a30", linewidth=0.36, alpha=0.78, zorder=6,
                sizes={"spacing": 0.12, "height": 0.27, "width": 0.20, "emptybarb": 0.12},
                barb_increments={"half": 5, "full": 10, "flag": 50},
            )

    ax.text(0.012, 0.016,
            f"Grade real: {nx} × {ny}  •  {lon_min:.2f}°–{lon_max:.2f}°W / {abs(lat_max):.2f}°–{abs(lat_min):.2f}°S  •  Pico {dbz_max:.1f} dBZ",
            transform=ax.transAxes, ha="left", va="bottom", fontsize=7.4, color="#3f4853",
            bbox={"facecolor": "white", "edgecolor": "#d8dee6", "alpha": 0.94, "pad": 3.0},
            zorder=9)

    cax = fig.add_axes([0.075, 0.070, 0.70, 0.027])
    cb = fig.colorbar(mesh, cax=cax, orientation="horizontal", ticks=BOUNDS, spacing="proportional")
    cb.outline.set_edgecolor("#a8b1bc")
    cb.outline.set_linewidth(0.6)
    cb.ax.tick_params(labelsize=7.5, length=2.5, colors=INK, pad=2)
    cb.set_label("REFLETIVIDADE NATIVA [dBZ]", fontsize=8.5, weight="bold", labelpad=4, color=INK)

    fig.text(0.966, 0.082, "WRF TARC 3 km", ha="right", va="center",
             fontsize=10, color=INK, weight="bold")
    fig.text(0.966, 0.060, f"Mapa regional; ecos somente dentro do domínio demarcado",
             ha="right", va="center", fontsize=6.8, color=MUTED)
    fig.text(0.966, 0.040, "REFL_10CM do modelo — não é radar observado",
             ha="right", va="center", fontsize=6.8, color=MUTED)

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, dpi=120, bbox_inches=None, facecolor="white", metadata={
        "Title": "Sideral — Boletim de Refletividade Nativa WRF TARC 3 km",
        "Description": f"Fonte: release nativo TARC; {Path(args.input).name}; campo {field_name}; grade {nx}x{ny}; máximo {dbz_max:.1f} dBZ.",
        "Software": "Sideral native-reflectivity renderer",
    })
    plt.close(fig)

    stats = {
        "sourceRelease": args.source_tag,
        "sourceAsset": Path(args.input).name,
        "model": "WRF TARC 3 km",
        "nativeField": "REFL_10CM",
        "reflectivityProduct": field_name,
        "nativeGrid": {"nx": int(nx), "ny": int(ny)},
        "nativeCoordinateBounds": {
            "west": round(lon_min, 5),
            "east": round(lon_max, 5),
            "south": round(lat_min, 5),
            "north": round(lat_max, 5),
        },
        "contextMapBounds": {"west": round(extent[0], 5), "east": round(extent[1], 5), "south": round(extent[2], 5), "north": round(extent[3], 5)},
        "nativeGridFootprintOutlined": True,
        "initializationUTC": init.isoformat(),
        "validUTC": valid.isoformat(),
        "forecastHour": forecast_hour,
        "reflectivityRangeDisplayedDbz": [5, 87.5],
        "nativeReflectivityMaxDbz": round(dbz_max, 2),
        "nativeReflectivityMinShownDbz": round(dbz_min, 2),
        "output": output.name,
    }
    output.with_suffix(".json").write_text(__import__("json").dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"[BULLETIN] {output} {output.stat().st_size} bytes; grade={nx}x{ny}; "
        f"lon={lon_min:.4f}..{lon_max:.4f}; lat={lat_min:.4f}..{lat_max:.4f}; "
        f"max dBZ={dbz_max:.1f}; F{forecast_hour:03d}"
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--init-time", required=True, help="UTC ISO-8601, e.g. 2026-10-10T06:00:00Z")
    parser.add_argument("--source-tag", required=True)
    args = parser.parse_args()
    render(args)


if __name__ == "__main__":
    main()
