from __future__ import annotations

import datetime as dt
import hashlib
from dataclasses import asdict
from pathlib import Path
from typing import Any

import numpy as np

from .cache import FileCache
from .catalog import MODEL_BY_ID, MODELS, MULTIMODEL_STATS, PRODUCT_BY_ID, PRODUCTS, REGIONS
from .config import COMMON_GRID_DEGREES, MAX_CACHE_AGE_DAYS, MIN_MULTIMODEL_MEMBERS, MODEL_CACHE_DIR, MODEL_DATA_DIR, ensure_backend_directories
from .plotting import PlottingUnavailable, render_field_png
from .processing import combine_fields, common_grid, probability_exceedance, regrid_to_common_grid
from .processing.field import Field
from .processing.units import normalize_units
from .sources import ModelStorage

PLOT_SCHEMA = "south-america-v4"


class ServiceError(RuntimeError):
    status = 500
    code = "MODEL_SERVICE_ERROR"


class BadRequest(ServiceError):
    status = 400
    code = "BAD_REQUEST"


class NotFound(ServiceError):
    status = 404
    code = "DATA_NOT_AVAILABLE"


class Unavailable(ServiceError):
    status = 503
    code = "PROCESSING_UNAVAILABLE"


class Conflict(ServiceError):
    status = 409
    code = "INSUFFICIENT_MEMBERS"


class ModelService:
    def __init__(self, data_dir: Path = MODEL_DATA_DIR, cache_dir: Path = MODEL_CACHE_DIR, remote_enabled: bool | None = None) -> None:
        ensure_backend_directories()
        self.storage=ModelStorage(data_dir,remote_enabled=remote_enabled)
        self.cache=FileCache(cache_dir,MAX_CACHE_AGE_DAYS)
        self.cache.cleanup()

    @staticmethod
    def _operational_models() -> list[str]:
        return [model.id for model in MODELS if not model.planned]

    @staticmethod
    def _validate_model(model: str) -> str:
        model=str(model or "").lower().strip()
        definition=MODEL_BY_ID.get(model)
        if not definition or definition.planned:
            raise BadRequest("Modelo inválido ou ainda não operacional.")
        return model

    @staticmethod
    def _validate_product(product: str) -> str:
        product=str(product or "").lower().strip()
        if product not in PRODUCT_BY_ID:
            raise BadRequest("Produto inválido.")
        return product

    @staticmethod
    def _validate_region(region: str) -> str:
        region=str(region or "brazil").lower().strip()
        if region not in REGIONS:
            raise BadRequest("Região inválida.")
        return region

    @staticmethod
    def _validate_run(run: str) -> str:
        run=str(run or "").strip()
        if len(run) != 10 or not run.isdigit():
            raise BadRequest("Rodada inválida. Use YYYYMMDDHH.")
        try:dt.datetime.strptime(run,"%Y%m%d%H")
        except ValueError as exc:raise BadRequest("Rodada inválida. Use YYYYMMDDHH.") from exc
        return run

    @staticmethod
    def _validate_fh(value: Any) -> int:
        try:forecast_hour=int(value)
        except (TypeError,ValueError) as exc:raise BadRequest("Forecast hour inválido.") from exc
        if forecast_hour < 0 or forecast_hour > 384:
            raise BadRequest("Forecast hour fora do intervalo permitido.")
        return forecast_hour

    @staticmethod
    def _valid_time(run: str, forecast_hour: int) -> str:
        initial=dt.datetime.strptime(run,"%Y%m%d%H").replace(tzinfo=dt.timezone.utc)
        return (initial+dt.timedelta(hours=forecast_hour)).isoformat().replace("+00:00","Z")

    def catalog(self) -> dict[str, Any]:
        return {
            "models":[{**asdict(model),**self.storage.status(model.id)} for model in MODELS],
            "regions":[{"id":key,**value} for key,value in REGIONS.items()],
            "common_grid_degrees":COMMON_GRID_DEGREES,
            "minimum_multimodel_members":MIN_MULTIMODEL_MEMBERS,
            "data_policy":"Somente arquivos reais publicados no armazenamento do backend.",
        }

    def statuses(self) -> dict[str, Any]:
        return {"models":[self.storage.status(model.id) for model in MODELS],"generated_at":dt.datetime.now(dt.timezone.utc).isoformat()}

    def runs(self, model: str) -> dict[str, Any]:
        model=self._validate_model(model)
        runs=self.storage.list_runs(model)[:5]
        latest=runs[0] if runs else None
        return {"model":model,"runs":runs,"forecast_hours":self.storage.forecast_hours(model,latest) if latest else []}

    def products(self, model: str, run: str | None) -> dict[str, Any]:
        model=self._validate_model(model)
        if not run:
            runs=self.storage.list_runs(model);run=runs[0] if runs else None
        if not run:
            return {"model":model,"run":None,"products":[],"catalog":[asdict(product) for product in PRODUCTS]}
        run=self._validate_run(run)
        return {"model":model,"run":run,"products":self.storage.available_products(model,run),"catalog":[asdict(product) for product in PRODUCTS]}

    def multimodel_runs(self, product: str = "qpf24") -> dict[str, Any]:
        product=self._validate_product(product)
        common=self.storage.common_valid_times(self._operational_models(),product,MIN_MULTIMODEL_MEMBERS)[:12]
        return {
            "runs":common,
            "forecast_hours":[0] if common else [],
            "minimum_members":MIN_MULTIMODEL_MEMBERS,
            "time_reference":"valid",
            "product":product,
        }

    def _single_field(self, model: str, run: str, product: str, region: str, forecast_hour: int) -> tuple[Field,str]:
        try:loaded=self.storage.field(model,run,product,region,forecast_hour)
        except Exception as exc:raise Unavailable("A fonte real deste campo está temporariamente indisponível.") from exc
        if not loaded:raise NotFound("Campo numérico ainda não disponível.")
        field,fingerprint=loaded
        definition=PRODUCT_BY_ID[product]
        try:
            if not field.valid_time:
                field=Field(lat=field.lat,lon=field.lon,values=field.values,unit=field.unit,valid_time=self._valid_time(run,forecast_hour),model=field.model,product=field.product,run=field.run,forecast_hour=field.forecast_hour)
            if definition.unit and "/" not in definition.unit:
                field=normalize_units(field,definition.variable_kind,definition.unit)
            return field,fingerprint
        except ValueError as exc:raise Unavailable(str(exc)) from exc

    def frame(self, model: str, run: str, product: str, region: str, forecast_hour: int, metadata_only: bool = False) -> tuple[dict[str,Any] | bytes,str,dict[str,str]]:
        model=self._validate_model(model);run=self._validate_run(run);product=self._validate_product(product);region=self._validate_region(region);forecast_hour=self._validate_fh(forecast_hour)
        png=self.storage.frame_path(model,run,product,region,forecast_hour,"png")
        if metadata_only:
            npz=self.storage.frame_path(model,run,product,region,forecast_hour,"npz")
            remote_available=forecast_hour in self.storage.product_hours(model,run,product)
            if not png and not npz and not remote_available:raise NotFound("Dado indisponível para esta seleção.")
            source="local" if (png or npz) else "github-actions"
            return {"model":model,"run":run,"product":product,"region":region,"forecast_hour":forecast_hour,"valid_time":self._valid_time(run,forecast_hour),"png_available":bool(png),"field_available":bool(npz) or remote_available,"source":source},"application/json",{}
        if png:
            body=png.read_bytes()
            return body,"image/png",{"X-Sideral-Source":"preprocessed","ETag":hashlib.sha256(body).hexdigest()}
        field,fingerprint=self._single_field(model,run,product,region,forecast_hour)
        key=self.cache.digest([PLOT_SCHEMA,"single",model,run,product,region,str(forecast_hour),fingerprint])
        cached=self.cache.read("models",key,"png")
        if cached:return cached,"image/png",{"X-Sideral-Source":"cache","ETag":hashlib.sha256(cached).hexdigest()}
        try:body=render_field_png(field,PRODUCT_BY_ID[product],region,title_prefix=MODEL_BY_ID[model].name,models_used=[MODEL_BY_ID[model].name])
        except PlottingUnavailable as exc:raise Unavailable(str(exc)) from exc
        self.cache.write("models",key,"png",body)
        return body,"image/png",{"X-Sideral-Source":"generated","ETag":hashlib.sha256(body).hexdigest()}

    def _multimodel_fields(self,run: str,product: str,region: str,forecast_hour: int) -> tuple[list[Field],list[str],list[str]]:
        definition=PRODUCT_BY_ID[product]
        if not definition.unit or "/" in definition.unit:
            raise BadRequest("Este produto composto ainda não pode ser combinado numericamente.")
        target_lat,target_lon=common_grid(region,COMMON_GRID_DEGREES)
        accepted: list[Field]=[];models_used: list[str]=[];fingerprints: list[str]=[]
        valid_target=self._valid_time(run,forecast_hour)
        valid_id=valid_target[:13].replace("-","").replace("T","")
        for model in self._operational_models():
            try:
                loaded=self.storage.field_at_valid_time(model,valid_id,product,region)
                if not loaded:continue
                field,fingerprint=loaded
                if field.valid_time and field.valid_time[:13] != valid_target[:13]:continue
                if not field.valid_time:
                    field=Field(lat=field.lat,lon=field.lon,values=field.values,unit=field.unit,valid_time=valid_target,model=field.model,product=field.product,run=field.run,forecast_hour=field.forecast_hour)
                field=normalize_units(field,definition.variable_kind,definition.unit)
                field=regrid_to_common_grid(field,target_lat,target_lon)
                if np.isfinite(field.values).mean() < .50:continue
                accepted.append(field);models_used.append(model);fingerprints.append(f"{model}:{fingerprint}")
            except Exception:continue
        if len(accepted) < MIN_MULTIMODEL_MEMBERS:
            raise Conflict(f"Modelos válidos insuficientes: {len(accepted)}/{MIN_MULTIMODEL_MEMBERS}.")
        return accepted,models_used,fingerprints

    def multimodel_frame(self,run: str,product: str,region: str,forecast_hour: int,statistic: str) -> tuple[bytes,str,dict[str,str]]:
        run=self._validate_run(run);product=self._validate_product(product);region=self._validate_region(region);forecast_hour=self._validate_fh(forecast_hour);statistic=str(statistic or "median").lower()
        if statistic not in MULTIMODEL_STATS:raise BadRequest("Estatística multi-modelo inválida.")
        fields,models_used,fingerprints=self._multimodel_fields(run,product,region,forecast_hour)
        try:combined,_=combine_fields(fields,statistic,MIN_MULTIMODEL_MEMBERS)
        except ValueError as exc:raise Conflict(str(exc)) from exc
        combined=Field(lat=combined.lat,lon=combined.lon,values=combined.values,unit=combined.unit,valid_time=self._valid_time(run,forecast_hour),model="multimodel",product=product,run=run,forecast_hour=forecast_hour)
        key=self.cache.digest([PLOT_SCHEMA,"multimodel",run,product,region,str(forecast_hour),statistic,*fingerprints])
        cached=self.cache.read("multimodel",key,"png")
        headers={"X-Sideral-Models":",".join(models_used),"X-Sideral-Model-Count":str(len(models_used))}
        if cached:return cached,"image/png",{**headers,"X-Sideral-Source":"cache","ETag":hashlib.sha256(cached).hexdigest()}
        try:body=render_field_png(combined,PRODUCT_BY_ID[product],region,title_prefix="SIDERAL MULTI-MODEL",models_used=[MODEL_BY_ID[item].name for item in models_used],statistic=statistic)
        except PlottingUnavailable as exc:raise Unavailable(str(exc)) from exc
        self.cache.write("multimodel",key,"png",body)
        return body,"image/png",{**headers,"X-Sideral-Source":"generated","ETag":hashlib.sha256(body).hexdigest()}

    def probability(self,run: str,variable: str,region: str,forecast_hour: int,threshold: float,period: int | None = None) -> tuple[bytes,str,dict[str,str]]:
        if period is not None and period not in {6,12,24,48,72}:
            raise BadRequest("Período probabilístico inválido.")
        if str(variable).lower().strip() == "precip":
            if period is None:raise BadRequest("Informe o período da precipitação probabilística.")
            variable=f"qpf{period}"
        run=self._validate_run(run);variable=self._validate_product(variable);region=self._validate_region(region);forecast_hour=self._validate_fh(forecast_hour)
        if not np.isfinite(threshold):raise BadRequest("Limiar inválido.")
        fields,models_used,fingerprints=self._multimodel_fields(run,variable,region,forecast_hour)
        try:result,_=probability_exceedance(fields,float(threshold),MIN_MULTIMODEL_MEMBERS)
        except ValueError as exc:raise Conflict(str(exc)) from exc
        result=Field(lat=result.lat,lon=result.lon,values=result.values,unit=result.unit,valid_time=self._valid_time(run,forecast_hour),model="multimodel",product=variable,run=run,forecast_hour=forecast_hour)
        period_label=f"/{period}h" if period else ""
        key=self.cache.digest([PLOT_SCHEMA,"probability",run,variable,region,str(forecast_hour),str(threshold),str(period),*fingerprints])
        cached=self.cache.read("probability",key,"png")
        headers={"X-Sideral-Models":",".join(models_used),"X-Sideral-Model-Count":str(len(models_used))}
        if cached:return cached,"image/png",{**headers,"X-Sideral-Source":"cache","ETag":hashlib.sha256(cached).hexdigest()}
        definition=PRODUCT_BY_ID[variable]
        probability_product=type(definition)(definition.id,f"Probabilidade de {definition.name} > {threshold:g}{period_label}","%",definition.palette,definition.category,definition.variable_kind)
        try:body=render_field_png(result,probability_product,region,title_prefix="SIDERAL MULTI-MODEL · PROBABILIDADE",models_used=[MODEL_BY_ID[item].name for item in models_used],probability=True)
        except PlottingUnavailable as exc:raise Unavailable(str(exc)) from exc
        self.cache.write("probability",key,"png",body)
        return body,"image/png",{**headers,"X-Sideral-Source":"generated","ETag":hashlib.sha256(body).hexdigest()}

