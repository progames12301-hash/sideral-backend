#!/usr/bin/env python3
"""Create a fast MP4 bulletin from native WRF TARC 3 km reflectivity NPZ frames."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.animation import FFMpegWriter, FuncAnimation
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
INK = "#191f28"
MUTED = "#536273"
BLUE = "#1557a5"
RED = "#e31b23"


def parse_utc(value: str) -> dt.datetime:
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def read_frame(path: Path, init: dt.datetime):
    stamp = dt.datetime.strptime(path.stem, "%Y%m%d_%H%M%S").replace(tzinfo=dt.timezone.utc)
    with np.load(path, allow_pickle=False) as data:
        if "lat" not in data.files or "lon" not in data.files:
            raise ValueError(f"{path.name}: faltam lat/lon")
        lat = np.asarray(data["lat"], dtype=np.float32)
        lon = np.asarray(data["lon"], dtype=np.float32)
        if "reflectivity_composite_dbz" in data.files:
            refl = np.asarray(data["reflectivity_composite_dbz"], dtype=np.float32)
            field = "REFL_10CM — máximo vertical"
        elif "reflectivity_native_dbz" in data.files:
            refl = np.asarray(data["reflectivity_native_dbz"], dtype=np.float32)
            field = "REFL_10CM — nível inferior"
        else:
            raise ValueError(f"{path.name}: não contém REFL_10CM nativa")
    if lat.ndim != 2 or lon.shape != lat.shape or refl.shape != lat.shape:
        raise ValueError(f"{path.name}: grades incompatíveis {lat.shape}/{lon.shape}/{refl.shape}")
    valid_coords = np.isfinite(lat) & np.isfinite(lon)
    refl = np.where(valid_coords & np.isfinite(refl) & (refl >= 5), refl, np.nan)
    return {
        "path": path,
        "valid": stamp,
        "fh": int(round((stamp - init).total_seconds() / 3600)),
        "lat": lat, "lon": lon, "refl": refl, "field": field,
        "max_dbz": float(np.nanmax(refl)) if np.isfinite(refl).any() else None,
    }


def render(args):
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg não está instalado.")
    init = parse_utc(args.init_time)
    paths = sorted(args.input_dir.glob("*.npz"))
    if not paths:
        raise RuntimeError(f"Nenhum NPZ encontrado em {args.input_dir}")
    frames = []
    for path in paths:
        try:
            frame = read_frame(path, init)
            if frame["fh"] >= 0:
                frames.append(frame)
        except Exception as exc:
            print(f"[SKIP] {path.name}: {exc}")
    frames.sort(key=lambda frame: frame["valid"])
    if not frames:
        raise RuntimeError("Nenhum quadro válido com refletividade nativa.")
    shape = frames[0]["refl"].shape
    frames = [f for f in frames if f["refl"].shape == shape]
    if not frames:
        raise RuntimeError("Os quadros não compartilham a grade nativa.")
    # Keep coordinates from the first frame; only the reflectivity array changes.
    lat, lon = frames[0]["lat"], frames[0]["lon"]
    finite_coords = np.isfinite(lat) & np.isfinite(lon)
    lat_min, lat_max = float(np.nanmin(lat[finite_coords])), float(np.nanmax(lat[finite_coords]))
    lon_min, lon_max = float(np.nanmin(lon[finite_coords])), float(np.nanmax(lon[finite_coords]))
    nx, ny = int(shape[1]), int(shape[0])
    all_max = [f["max_dbz"] for f in frames if f["max_dbz"] is not None]

    plt.rcParams.update({
        "font.family": "DejaVu Sans",
        "font.size": 9,
        "figure.facecolor": "white",
        "savefig.facecolor": "white",
        "animation.ffmpeg_path": shutil.which("ffmpeg"),
    })
    fig = plt.figure(figsize=(16, 10), dpi=80, facecolor="white")
    # Sideral X mark.
    from matplotlib.lines import Line2D
    fig.add_artist(Line2D([0.031, 0.050], [0.942, 0.980], transform=fig.transFigure,
                          color=RED, linewidth=5.0, solid_capstyle="butt"))
    fig.add_artist(Line2D([0.031, 0.050], [0.980, 0.942], transform=fig.transFigure,
                          color=RED, linewidth=5.0, solid_capstyle="butt"))
    fig.text(0.062, 0.968, "SIDERAL METEOROLOGIA", fontsize=12, weight="bold",
             color=INK, ha="left", va="center")
    fig.text(0.062, 0.936, "BOLETIM ANIMADO DE REFLETIVIDADE NATIVA", fontsize=16,
             weight="bold", color=INK, ha="left", va="center")
    fig.text(0.062, 0.906,
             f"WRF TARC 3 km  •  {frames[0]['field']}  •  ZOOM REGIONAL / COBERTURA NATIVA",
             fontsize=9, color=MUTED, ha="left", va="center")
    cycle_text = fig.text(0.966, 0.966, "", fontsize=17, weight="bold",
                          color=BLUE, ha="right", va="center")
    init_text = fig.text(0.966, 0.934, f"Init: {init:%H}Z {init:%d/%m/%Y}",
                         fontsize=9, color=INK, ha="right", va="center")
    valid_text = fig.text(0.966, 0.908, "", fontsize=9, color=INK, ha="right", va="center")
    fig.text(0.966, 0.880, "sideralmeteorologia.com.br", fontsize=8.5, weight="bold",
             color=BLUE, ha="right", va="center")
    fig.add_artist(plt.Line2D([0.025, 0.975], [0.889, 0.889], transform=fig.transFigure,
                              color=BLUE, linewidth=1.4))

    projection = ccrs.PlateCarree()
    ax = fig.add_axes([0.035, 0.145, 0.93, 0.725], projection=projection)
    ax.set_facecolor("#f7f9fc")
    # Close-up regional framing derived from the true native TARC grid bounds.
    pad_lon = max(0.8, (lon_max - lon_min) * 0.08)
    pad_lat = max(0.7, (lat_max - lat_min) * 0.08)
    extent = [
        max(-180.0, lon_min - pad_lon),
        min(180.0, lon_max + pad_lon),
        max(-90.0, lat_min - pad_lat),
        min(90.0, lat_max + pad_lat),
    ]
    ax.set_extent(extent, crs=projection)
    ax.add_feature(cfeature.LAND.with_scale("10m"), facecolor="#fffefb", edgecolor="none", zorder=0)
    ax.add_feature(cfeature.OCEAN.with_scale("10m"), facecolor="#f7f9fc", edgecolor="none", zorder=0)
    try:
        provinces = cfeature.NaturalEarthFeature(
            category="cultural", name="admin_1_states_provinces_lines",
            scale="10m", facecolor="none",
        )
        ax.add_feature(provinces, edgecolor="#5a6068", linewidth=0.42, alpha=0.8, zorder=3)
    except Exception as exc:
        print(f"[WARN] Limites estaduais indisponíveis: {exc}")
    cmap = ListedColormap(DBZ_COLORS, name="sideral_native_dbz")
    norm = BoundaryNorm(BOUNDS, cmap.N, clip=True)
    first_refl = np.ma.masked_invalid(frames[0]["refl"])
    mesh = ax.pcolormesh(
        lon, lat, first_refl, cmap=cmap, norm=norm, shading="auto",
        transform=projection, rasterized=True, antialiased=False, zorder=2,
    )
    # True perimeter of the native grid, not an expanded/interpolated footprint.
    edge_lon = np.concatenate((lon[0, :], lon[1:, -1], lon[-1, -2::-1], lon[-2:0:-1, 0]))
    edge_lat = np.concatenate((lat[0, :], lat[1:, -1], lat[-1, -2::-1], lat[-2:0:-1, 0]))
    ax.plot(edge_lon, edge_lat, transform=projection, color="white", linewidth=2.6, zorder=7)
    ax.plot(edge_lon, edge_lat, transform=projection, color="#17212a",
            linewidth=1.25, linestyle=(0, (5, 2)), zorder=8)
    ax.text(0.015, 0.965, "CONTORNO: DOMÍNIO NATIVO TARC 3 km", transform=ax.transAxes,
            ha="left", va="top", fontsize=8, color="#18202a", weight="bold",
            bbox={"facecolor":"white","edgecolor":"#bec7d2","alpha":0.95,"pad":3}, zorder=9)
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
    info_text = ax.text(0.012, 0.016, "", transform=ax.transAxes, ha="left", va="bottom",
                        fontsize=7.4, color="#3f4853",
                        bbox={"facecolor":"white","edgecolor":"#d8dee6","alpha":0.94,"pad":3},
                        zorder=9)
    cax = fig.add_axes([0.075, 0.070, 0.70, 0.027])
    cb = fig.colorbar(mesh, cax=cax, orientation="horizontal", ticks=BOUNDS, spacing="proportional")
    cb.outline.set_edgecolor("#a8b1bc")
    cb.outline.set_linewidth(0.6)
    cb.ax.tick_params(labelsize=7.5, length=2.5, colors=INK, pad=2)
    cb.set_label("REFLETIVIDADE NATIVA [dBZ]", fontsize=8.5, weight="bold", labelpad=4, color=INK)
    fig.text(0.966, 0.082, "WRF TARC 3 km", ha="right", va="center",
             fontsize=10, color=INK, weight="bold")
    fig.text(0.966, 0.060, "Ecos limitados à grade nativa do modelo",
             ha="right", va="center", fontsize=6.8, color=MUTED)
    fig.text(0.966, 0.040, "REFL_10CM do modelo — não é radar observado",
             ha="right", va="center", fontsize=6.8, color=MUTED)

    def update(index):
        frame = frames[index]
        mesh.set_array(np.ma.masked_invalid(frame["refl"]).ravel())
        cycle_text.set_text(f"F{frame['fh']:03d}")
        valid_text.set_text(f"Válido: {frame['valid']:%H}Z {frame['valid']:%d/%m/%Y}")
        peak = frame["max_dbz"]
        info_text.set_text(
            f"Grade nativa: {nx} × {ny}  •  Pico: {peak:.1f} dBZ" if peak is not None
            else f"Grade nativa: {nx} × {ny}  •  Sem ecos ≥ 5 dBZ"
        )
        return mesh, cycle_text, valid_text, info_text

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    poster = output.with_suffix(".png")
    update(0)
    fig.savefig(poster, dpi=80, facecolor="white")
    writer = FFMpegWriter(
        fps=args.fps, codec="libx264", bitrate=1600,
        metadata={"title": "Sideral — Boletim de Refletividade Nativa WRF TARC 3 km",
                  "comment": f"Checkpoint {args.source_tag}; REFL_10CM nativa"},
        extra_args=["-pix_fmt", "yuv420p", "-movflags", "+faststart"],
    )
    anim = FuncAnimation(fig, update, frames=len(frames), interval=1000 / args.fps,
                         blit=False, repeat=False)
    anim.save(output, writer=writer, dpi=80)
    plt.close(fig)

    meta = {
        "sourceRelease": args.source_tag,
        "model": "WRF TARC 3 km",
        "nativeField": "REFL_10CM",
        "reflectivityProduct": frames[0]["field"],
        "nativeGrid": {"nx": nx, "ny": ny},
        "viewBounds": {"west": round(extent[0], 5), "east": round(extent[1], 5), "south": round(extent[2], 5), "north": round(extent[3], 5)},
        "initializationUTC": init.isoformat(),
        "firstValidUTC": frames[0]["valid"].isoformat(),
        "lastValidUTC": frames[-1]["valid"].isoformat(),
        "firstForecastHour": frames[0]["fh"],
        "lastForecastHour": frames[-1]["fh"],
        "frameCount": len(frames),
        "forecastHours": [f["fh"] for f in frames],
        "fps": args.fps,
        "videoResolution": [1280, 800],
        "nativeReflectivityPeakDbz": round(max(all_max), 2) if all_max else None,
        "output": output.name,
        "poster": poster.name,
        "note": "Refletividade nativa do modelo; não representa radar observado.",
    }
    output.with_suffix(".json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[MP4] {output} ({output.stat().st_size} bytes), {len(frames)} frames, "
          f"F{frames[0]['fh']:03d}-F{frames[-1]['fh']:03d}; grid={nx}x{ny}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", required=True, type=Path)
    parser.add_argument("--output", required=True)
    parser.add_argument("--init-time", required=True)
    parser.add_argument("--source-tag", required=True)
    parser.add_argument("--fps", type=int, default=8)
    args = parser.parse_args()
    render(args)


if __name__ == "__main__":
    main()
