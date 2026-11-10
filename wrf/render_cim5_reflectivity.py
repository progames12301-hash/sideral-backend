#!/usr/bin/env python3
"""Render native CIM WRF 5 km composite reflectivity from a published NPZ checkpoint."""
# Uses the released native-grid composite dBZ array; it does not synthesize echoes.
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import BoundaryNorm, ListedColormap
from matplotlib.ticker import MultipleLocator
import numpy as np


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--npz", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--run-init", required=True, help="ISO UTC, e.g. 2026-11-09T06:00:00Z")
    parser.add_argument("--forecast-hour", required=True, type=int)
    args = parser.parse_args()

    with np.load(args.npz, allow_pickle=False) as data:
        required = {"lat", "lon", "reflectivity_composite_dbz"}
        missing = required.difference(data.files)
        if missing:
            raise SystemExit(f"NPZ sem campos necessários: {', '.join(sorted(missing))}")
        lat = np.asarray(data["lat"], dtype=np.float32)
        lon = np.asarray(data["lon"], dtype=np.float32)
        dbz = np.asarray(data["reflectivity_composite_dbz"], dtype=np.float32)

    if lat.ndim != 2 or lon.shape != lat.shape or dbz.shape != lat.shape:
        raise SystemExit(f"Grade incompatível: lat={lat.shape}, lon={lon.shape}, dBZ={dbz.shape}")
    if lat.shape != (374, 446):
        raise SystemExit(f"Grade não corresponde à malha útil do CIM 5 km (374x446): {lat.shape}")

    valid = dbz[np.isfinite(dbz)]
    if valid.size == 0 or float(np.nanmax(valid)) <= 0:
        raise SystemExit("Campo de refletividade vazio ou sem ecos.")
    dbz = np.clip(dbz, 0.0, 75.0)
    masked = np.ma.masked_where(~np.isfinite(dbz) | (dbz < 5.0), dbz)

    bounds = [5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55, 60, 65, 70, 75]
    colors = [
        "#91dfff", "#269bff", "#1648c8", "#00c9ef",
        "#00a86b", "#47c51d", "#c9dc00", "#ffcb00",
        "#ff8b00", "#f04416", "#c90016", "#f00091",
        "#b000a9", "#632a91",
    ]
    cmap = ListedColormap(colors, name="sideral_reflectivity")
    cmap.set_bad((0, 0, 0, 0))
    norm = BoundaryNorm(bounds, cmap.N, clip=True)

    init = datetime.fromisoformat(args.run_init.replace("Z", "+00:00"))
    if init.tzinfo is None:
        init = init.replace(tzinfo=timezone.utc)
    valid_time = init.astimezone(timezone.utc) + timedelta(hours=args.forecast_hour)
    init_label = init.astimezone(timezone.utc).strftime("%d/%m/%Y %H UTC")
    valid_label = valid_time.strftime("%d/%m/%Y %H UTC")
    finite = dbz[np.isfinite(dbz)]
    max_dbz = float(np.max(finite))
    p95_dbz = float(np.percentile(finite, 95))

    plt.rcParams.update({
        "font.family": "DejaVu Sans",
        "axes.labelcolor": "#d7e2eb",
        "xtick.color": "#c4d0da",
        "ytick.color": "#c4d0da",
    })
    fig = plt.figure(figsize=(16, 10), facecolor="#08131d")
    ax = fig.add_axes([0.075, 0.125, 0.82, 0.72], facecolor="#0b1a26")
    mesh = ax.pcolormesh(lon, lat, masked, cmap=cmap, norm=norm, shading="auto", rasterized=True)

    ax.set_xlim(float(np.nanmin(lon)), float(np.nanmax(lon)))
    ax.set_ylim(float(np.nanmin(lat)), float(np.nanmax(lat)))
    ax.set_aspect(1.12, adjustable="box")
    ax.xaxis.set_major_locator(MultipleLocator(4))
    ax.yaxis.set_major_locator(MultipleLocator(3))
    ax.grid(True, color="#a9bac7", alpha=0.22, linewidth=0.55, linestyle="--")
    ax.set_xlabel("Longitude", labelpad=10, fontsize=10)
    ax.set_ylabel("Latitude", labelpad=10, fontsize=10)
    for spine in ax.spines.values():
        spine.set_color("#607382")
        spine.set_linewidth(0.8)

    fig.text(0.075, 0.94, "CIM WRF 5 KM", color="#f3f7fa", fontsize=24, weight="bold")
    fig.text(0.075, 0.895, "REFLETIVIDADE COMPOSTA  |  dBZ", color="#c6d4df", fontsize=13, weight="bold")
    fig.text(
        0.075, 0.86,
        f"Rodada: {init_label}     Válido: {valid_label}     Previsão: F{args.forecast_hour:03d}",
        color="#9fb3c2", fontsize=10,
    )
    fig.text(
        0.91, 0.82,
        f"MÁX.\n{max_dbz:.1f} dBZ\n\nP95\n{p95_dbz:.1f} dBZ",
        ha="right", va="top", color="#f4f7f9", fontsize=10,
        bbox={"boxstyle": "round,pad=0.55", "facecolor": "#142635", "edgecolor": "#405565", "alpha": 0.95},
    )

    cax = fig.add_axes([0.91, 0.20, 0.018, 0.54])
    colorbar = fig.colorbar(mesh, cax=cax, ticks=bounds, extend="max")
    colorbar.set_label("Refletividade (dBZ)", color="#e1eaf0", labelpad=12, fontsize=10)
    colorbar.ax.tick_params(colors="#d0dbe3", labelsize=8, length=3)
    colorbar.outline.set_edgecolor("#708391")

    fig.text(
        0.075, 0.055,
        "Grade WRF nativa • 5 km • REFL_10CM / máximo vertical • "
        "SIDERALMETEOROLOGIA.COM.BR",
        color="#a9bbc8", fontsize=9,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(args.output, dpi=180, facecolor=fig.get_facecolor(), bbox_inches="tight")
    plt.close(fig)
    print(
        f"PNG={args.output} | grid={lat.shape[1]}x{lat.shape[0]} | "
        f"valid={valid_label} | max={max_dbz:.1f} dBZ"
    )


if __name__ == "__main__":
    main()
