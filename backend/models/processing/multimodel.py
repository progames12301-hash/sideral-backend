from __future__ import annotations

import numpy as np

from .field import Field


PERCENTILES = {"p10": 10, "p25": 25, "p50": 50, "p75": 75, "p90": 90}


def _validate(fields: list[Field], minimum: int) -> None:
    if len(fields) < minimum:
        raise ValueError(f"São necessários pelo menos {minimum} modelos válidos.")
    valid_times = {field.valid_time for field in fields if field.valid_time}
    if len(valid_times) > 1:
        raise ValueError("Os campos não representam o mesmo horário válido.")
    products = {field.product for field in fields}
    units = {field.unit for field in fields}
    shapes = {field.values.shape for field in fields}
    if len(products) != 1 or len(units) != 1 or len(shapes) != 1:
        raise ValueError("Variável, unidade ou grade incompatível entre os modelos.")


def combine_fields(fields: list[Field], statistic: str, minimum: int = 2) -> tuple[Field, np.ndarray]:
    _validate(fields, minimum)
    stack = np.stack([field.values for field in fields], axis=0)
    valid_members = np.sum(np.isfinite(stack), axis=0)
    with np.errstate(all="ignore"):
        if statistic == "mean": values = np.nanmean(stack, axis=0)
        elif statistic == "median": values = np.nanmedian(stack, axis=0)
        elif statistic == "min": values = np.nanmin(stack, axis=0)
        elif statistic == "max": values = np.nanmax(stack, axis=0)
        elif statistic == "spread": values = np.nanmax(stack, axis=0) - np.nanmin(stack, axis=0)
        elif statistic == "stddev": values = np.nanstd(stack, axis=0)
        elif statistic in PERCENTILES: values = np.nanpercentile(stack, PERCENTILES[statistic], axis=0)
        else: raise ValueError("Estatística multi-modelo inválida.")
    values=np.asarray(values,dtype=float)
    values[valid_members < minimum] = np.nan
    if not np.isfinite(values).any():
        raise ValueError("Nenhuma célula possui membros suficientes.")
    base=fields[0]
    result=Field(lat=base.lat,lon=base.lon,values=values,unit=base.unit,valid_time=base.valid_time,model="multimodel",product=base.product,run=base.run,forecast_hour=base.forecast_hour)
    return result,valid_members
