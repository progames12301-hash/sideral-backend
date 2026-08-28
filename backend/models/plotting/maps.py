from __future__ import annotations

import io
import threading
from typing import Iterable

import numpy as np

from ..catalog import ProductDefinition, REGIONS
from ..processing.field import Field
from .palettes import palette


class PlottingUnavailable(RuntimeError):
    pass


_PLOT_LOCK = threading.RLock()


def _plain_axis(figure, bounds):
    axis=figure.add_axes((0.035,0.15,0.93,0.72),facecolor="#dedede")
    axis.set_xlim(bounds["west"],bounds["east"]);axis.set_ylim(bounds["south"],bounds["north"])
    axis.set_xlabel("Longitude");axis.set_ylabel("Latitude")
    axis.grid(True,color="#505050",alpha=.25,linewidth=.4)
    axis.tick_params(labelsize=8)
    return axis,None


def _map_axis(figure, bounds):
    try:
        import cartopy.crs as ccrs
        import cartopy.feature as cfeature

        projection=ccrs.PlateCarree()
        land=cfeature.LAND.with_scale("50m")
        ocean=cfeature.OCEAN.with_scale("50m")
        coastline=cfeature.COASTLINE.with_scale("50m")
        borders=cfeature.BORDERS.with_scale("50m")
        states=cfeature.STATES.with_scale("50m")
        # Resolve os arquivos Natural Earth aqui; se não estiverem disponíveis,
        # o mapa cartesiano continua sendo gerado normalmente.
        for feature in (land,ocean,coastline,borders,states):
            next(iter(feature.geometries()),None)
        axis=figure.add_axes((0.035,0.15,0.93,0.72),projection=projection,facecolor="#dadada")
        axis.set_extent([bounds["west"],bounds["east"],bounds["south"],bounds["north"]],crs=projection)
        axis.add_feature(land,facecolor="#d6d6d6",edgecolor="none",zorder=0)
        axis.add_feature(ocean,facecolor="#ffffff",edgecolor="none",zorder=0)
        axis.add_feature(coastline,edgecolor="#111111",linewidth=.8,zorder=3)
        axis.add_feature(borders,edgecolor="#111111",linewidth=.65,zorder=3)
        axis.add_feature(states,edgecolor="#333333",linewidth=.35,facecolor="none",zorder=3)
        gridlines=axis.gridlines(crs=projection,draw_labels=True,linewidth=.35,color="#5d5d5d",alpha=.28,linestyle="-")
        gridlines.top_labels=False;gridlines.right_labels=False
        gridlines.xlabel_style={"size":7};gridlines.ylabel_style={"size":7}
        return axis,projection
    except (ImportError,OSError):
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

