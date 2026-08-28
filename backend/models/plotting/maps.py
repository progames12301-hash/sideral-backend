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
    spec=palette(product.palette, probability=probability)
    colormap=ListedColormap(spec["colors"])
    norm=BoundaryNorm(spec["levels"],colormap.N,clip=False)
    lon_mesh,lat_mesh=np.meshgrid(field.lon,field.lat) if field.lon.ndim == field.lat.ndim == 1 else (field.lon,field.lat)
    bounds=REGIONS[region]

    with _PLOT_LOCK:
        figure=plt.figure(figsize=(14,9),dpi=130,facecolor="#f4f5f2")
        axis=figure.add_axes((0.055,0.13,0.89,0.72),facecolor="#e9eef0")
        image=axis.pcolormesh(lon_mesh,lat_mesh,field.values,cmap=colormap,norm=norm,shading="auto")
        axis.set_xlim(bounds["west"],bounds["east"]);axis.set_ylim(bounds["south"],bounds["north"])
        axis.set_xlabel("Longitude");axis.set_ylabel("Latitude")
        axis.grid(True,color="#50616b",alpha=.22,linewidth=.45)
        axis.tick_params(labelsize=8)
        for spine in axis.spines.values():spine.set_color("#192832");spine.set_linewidth(1.0)

        try:
            import cartopy.crs as ccrs
            import cartopy.feature as cfeature
            # Cartopy exige um GeoAxes; quando disponível em implantação, o produto
            # pode ser pré-processado com ele. O fallback cartesiano continua fiel à grade.
            _ = (ccrs,cfeature)
        except ImportError:
            pass

        model_list=", ".join(models_used)
        center=f"{title_prefix}{f' · {statistic.upper()}' if statistic else ''}"
        figure.text(.055,.94,f"Init: {field.run[:8]} {field.run[8:10]}Z",fontsize=9,family="monospace",weight="bold")
        figure.text(.5,.94,"SIDERAL METEOROLOGIA",ha="center",fontsize=13,weight="bold",color="#0b5568")
        figure.text(.945,.94,f"FH {field.forecast_hour:03d}",ha="right",fontsize=9,family="monospace",weight="bold")
        figure.text(.055,.895,center,fontsize=16,weight="bold")
        figure.text(.055,.866,product.name,fontsize=11,color="#364b58")
        if model_list:figure.text(.945,.875,f"Modelos utilizados: {model_list}",ha="right",fontsize=8,color="#364b58")
        colorbar=figure.colorbar(image,ax=axis,orientation="horizontal",fraction=.07,pad=.09,aspect=45,ticks=spec["levels"])
        colorbar.ax.tick_params(labelsize=8);colorbar.set_label(product.unit or field.unit,fontsize=9)
        valid=field.valid_time or "não informado"
        figure.text(.055,.035,f"Previsão válida: {valid}",fontsize=9,family="monospace")
        figure.text(.945,.035,f"RES: 0.25° · variável: {product.id} · unidade: {field.unit}",ha="right",fontsize=8,family="monospace")
        buffer=io.BytesIO();figure.savefig(buffer,format="png",facecolor=figure.get_facecolor(),bbox_inches=None);plt.close(figure)
        return buffer.getvalue()
