from __future__ import annotations

import numpy as np

from .field import Field
from .multimodel import _validate


def probability_exceedance(fields: list[Field], threshold: float, minimum: int = 2) -> tuple[Field, np.ndarray]:
    _validate(fields, minimum)
    if not np.isfinite(threshold):
        raise ValueError("Limiar inválido.")
    stack=np.stack([field.values for field in fields],axis=0)
    finite=np.isfinite(stack)
    valid_members=np.sum(finite,axis=0)
    exceedances=np.sum((stack > threshold) & finite,axis=0)
    values=np.full(valid_members.shape,np.nan,dtype=float)
    usable=valid_members >= minimum
    values[usable]=exceedances[usable] / valid_members[usable] * 100.0
    if not np.isfinite(values).any():
        raise ValueError("Nenhuma célula possui membros suficientes para probabilidade.")
    base=fields[0]
    result=Field(lat=base.lat,lon=base.lon,values=values,unit="%",valid_time=base.valid_time,model="multimodel",product=base.product,run=base.run,forecast_hour=base.forecast_hour)
    return result,valid_members
