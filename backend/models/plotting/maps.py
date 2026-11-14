from __future__ import annotations

import io
import json
import threading
import datetime as dt
from pathlib import Path
from typing import Iterable

import numpy as np

from ..catalog import MODEL_BY_ID, ProductDefinition, REGIONS
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
    axis=figure.add_axes((0.028,0.17,0.944,0.70),facecolor="#bdbdbd")
    axis.set_xlim(bounds["west"],bounds["east"]);axis.set_ylim(bounds["south"],bounds["north"])
    axis.set_xlabel("");axis.set_ylabel("")
    axis.grid(True,color="#343434",alpha=.18,linewidth=.45)
    axis.tick_params(labelsize=8,direction="out",length=3)
    _draw_local_boundaries(axis)
    return axis,None


def _map_axis(figure, bounds):
    return _plain_axis(figure,bounds)


def _time_label(value: str) -> str:
    try:
        parsed=dt.datetime.fromisoformat(value.replace("Z","+00:00"))
        return parsed.strftime("%HZ %a %d%b%Y").upper()
    except (TypeError,ValueError):
        return value or "—"


def _period_label(valid: str, product: ProductDefinition) -> str:
    try:
        end=dt.datetime.fromisoformat(valid.replace("Z","+00:00"))
        hours=int(product.id[3:]) if product.id.startswith("qpf") and product.id[3:].isdigit() else 0
        if hours:
            return f"{_time_label((end-dt.timedelta(hours=hours)).isoformat())} a {_time_label(valid)}"
    except (TypeError,ValueError):
        pass
    return _time_label(valid)


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
        # 1600×1000 mantém texto e contornos nítidos sem o pico de memória de
        # uma tela 2400×1500, importante nas instâncias pequenas do Render.
        figure=plt.figure(figsize=(16,10),dpi=100,facecolor="#ffffff")
        axis,projection=_map_axis(figure,bounds)
        plot_options={"cmap":colormap,"norm":norm,"levels":spec["levels"],"zorder":1,"extend":"max","antialiased":False,"nchunk":8}
        if projection is not None:plot_options["transform"]=projection
        image=axis.contourf(lon_mesh,lat_mesh,field.values,**plot_options)
        for spine in axis.spines.values():spine.set_color("#111111");spine.set_linewidth(1.0)

        model_list=" + ".join(name.upper() for name in models_used)
        center=f"{title_prefix}{f' · {statistic.upper()}' if statistic else ''}"
        valid=field.valid_time or "não informado"
        is_multi=field.model=="multimodel"
        resolution="GRADE 0.25°" if is_multi else MODEL_BY_ID.get(field.model).resolution if field.model in MODEL_BY_ID else "—"
        init_value=_time_label(valid) if is_multi else f"{field.run[8:10]}Z {field.run[6:8]}/{field.run[4:6]}/{field.run[:4]}"
        figure.text(.028,.955,f"{'Horário válido' if is_multi else 'Init'}: {init_value}",fontsize=9.5,family="monospace")
        header_name="SIDERAL MULTI-MODEL" if is_multi else (model_list or field.model.upper())
        figure.text(.5,.955,f"{header_name} · {resolution} — SIDERAL METEOROLOGIA",ha="center",fontsize=12.5,family="monospace")
        figure.text(.972,.955,f"{len(tuple(models_used))} MODELOS" if is_multi else f"FH {field.forecast_hour:03d}",ha="right",fontsize=9.5,family="monospace")
        figure.text(.5,.91,f"{center} · {product.name} ({product.unit or field.unit})",ha="center",fontsize=13,family="monospace")
        colorbar=figure.colorbar(image,ax=axis,orientation="horizontal",fraction=.055,pad=.07,aspect=58,ticks=spec["levels"])
        colorbar.ax.tick_params(labelsize=8);colorbar.outline.set_edgecolor("#111111");colorbar.outline.set_linewidth(.8)
        colorbar.set_label(product.unit or field.unit,fontsize=9,family="monospace")
        figure.text(.028,.035,f"Previsão: {_period_label(valid,product)}",fontsize=9,family="monospace")
        footer_models=f" · {model_list}" if is_multi and model_list else ""
        figure.text(.972,.035,f"{resolution} · {field.model.upper()}{footer_models}{'' if is_multi else f' · FH {field.forecast_hour:03d}'}",ha="right",fontsize=8.5,family="monospace")
        buffer=io.BytesIO();figure.savefig(buffer,format="png",facecolor=figure.get_facecolor(),bbox_inches=None);plt.close(figure)
        return buffer.getvalue()

