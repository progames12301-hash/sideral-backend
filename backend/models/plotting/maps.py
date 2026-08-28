from __future__ import annotations

import io
import json
import threading
from pathlib import Path
from typing import Iterable

import numpy as np

from ..catalog import ProductDefinition, REGIONS
from ..processing.field import Field
from .palettes import palette


class PlottingUnavailable(RuntimeError):
    pass


_PLOT_LOCK = threading.RLock()
_ASSETS_DIR = Path(__file__).resolve().parents[1] / "assets"
_BORDER_PATHS = (
    (_ASSETS_DIR / "south_america_countries.geojson",.75),
    (_ASSETS_DIR / "brazil_states.geojson",.32),
)


def _draw_local_boundaries(axis) -> None:
    for path,line_width in _BORDER_PATHS:
        try:
            collection=json.loads(path.read_text(encoding="utf-8"))
            for feature in collection.get("features",[]):
                geometry=feature.get("geometry") or {}
                coordinates=geometry.get("coordinates") or []
                polygons=[coordinates] if geometry.get("type")=="Polygon" else coordinates if geometry.get("type")=="MultiPolygon" else []
                for polygon in polygons:
                    for ring in polygon:
                        if len(ring)<2:continue
                        longitude,latitude=zip(*ring)
                        axis.plot(longitude,latitude,color="#111111",linewidth=line_width,zorder=4)
        except (OSError,ValueError,TypeError,json.JSONDecodeError):
            continue


def _plain_axis(figure, bounds):
    axis=figure.add_axes((0.035,0.15,0.93,0.72),facecolor="#dedede")
    axis.set_xlim(bounds["west"],bounds["east"]);axis.set_ylim(bounds["south"],bounds["north"])
    axis.set_xlabel("Longitude");axis.set_ylabel("Latitude")
    axis.grid(True,color="#505050",alpha=.25,linewidth=.4)
    axis.tick_params(labelsize=8)
    _draw_local_boundaries(axis)
    return axis,None


def _map_axis(figure, bounds):
    return _plain_axis(figure,bounds)


def render_field_png(
    field: Field,
    product: ProductDefinition,
    region: str,
    *,
    title_prefix: str,
    models_used: Iterable[str] = (),
    statistic: str | None = None,
    probability: bool = False,
) -> bytes:
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.colors import BoundaryNorm, ListedColormap
    except ImportError as exc:
        raise PlottingUnavailable("Matplotlib não está instalado no backend.") from exc

    if region not in REGIONS:
        raise ValueError("Região inválida.")
    spec=palette(product.palette,probability=probability)
    colormap=ListedColormap(spec["colors"])
    norm=BoundaryNorm(spec["levels"],colormap.N,clip=False)
    lon_mesh,lat_mesh=np.meshgrid(field.lon,field.lat) if field.lon.ndim == field.lat.ndim == 1 else (field.lon,field.lat)
    bounds=REGIONS[region]

    with _PLOT_LOCK:
        figure=plt.figure(figsize=(14,9),dpi=150,facecolor="#ffffff")
        axis,projection=_map_axis(figure,bounds)
        plot_options={"cmap":colormap,"norm":norm,"shading":"auto","zorder":1,"rasterized":True}
        if projection is not None:plot_options["transform"]=projection
        image=axis.pcolormesh(lon_mesh,lat_mesh,field.values,**plot_options)
        for spine in axis.spines.values():spine.set_color("#111111");spine.set_linewidth(1.0)

        model_list=" + ".join(name.upper() for name in models_used)
        center=f"{title_prefix}{f' · {statistic.upper()}' if statistic else ''}"
        valid=field.valid_time or "não informado"
        figure.text(.035,.955,f"Init: {field.run[8:10]}Z {field.run[6:8]}/{field.run[4:6]}/{field.run[:4]}",fontsize=9.5,family="monospace")
        figure.text(.5,.955,f"{model_list or field.model.upper()} [M] 0.25° — SIDERAL METEOROLOGIA",ha="center",fontsize=12.5,family="monospace")
        figure.text(.965,.955,f"FH {field.forecast_hour:03d}",ha="right",fontsize=9.5,family="monospace")
        figure.text(.5,.91,f"{center} · {product.name} ({product.unit or field.unit}) | Válido: {valid}",ha="center",fontsize=13,family="monospace")
        colorbar=figure.colorbar(image,ax=axis,orientation="horizontal",fraction=.065,pad=.075,aspect=52,ticks=spec["levels"])
        colorbar.ax.tick_params(labelsize=8);colorbar.outline.set_edgecolor("#111111");colorbar.outline.set_linewidth(.8)
        colorbar.set_label(product.unit or field.unit,fontsize=9,family="monospace")
        figure.text(.035,.035,f"Previsão válida: {valid}",fontsize=9,family="monospace")
        figure.text(.965,.035,f"RES: 0.25° · {field.model.upper()} · FH {field.forecast_hour:03d}",ha="right",fontsize=8.5,family="monospace")
        buffer=io.BytesIO();figure.savefig(buffer,format="png",facecolor=figure.get_facecolor(),bbox_inches=None);plt.close(figure)
        return buffer.getvalue()

