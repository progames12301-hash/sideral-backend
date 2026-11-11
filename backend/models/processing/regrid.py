from __future__ import annotations

import numpy as np

from ..catalog import REGIONS
from .field import Field


def normalize_longitudes(longitudes: np.ndarray) -> np.ndarray:
    values = np.asarray(longitudes, dtype=float)
    return ((values + 180.0) % 360.0) - 180.0


def common_grid(region: str, resolution: float = 0.25) -> tuple[np.ndarray, np.ndarray]:
    if region not in REGIONS:
        raise ValueError("Região inválida.")
    if not 0.05 <= resolution <= 2.0:
        raise ValueError("Resolução da grade comum inválida.")
    bounds = REGIONS[region]
    lat = np.arange(bounds["south"], bounds["north"] + resolution * 0.5, resolution, dtype=float)
    lon = np.arange(bounds["west"], bounds["east"] + resolution * 0.5, resolution, dtype=float)
    return lat, lon


def _rectilinear(field: Field, target_lat: np.ndarray, target_lon: np.ndarray) -> np.ndarray:
    from scipy.interpolate import RegularGridInterpolator

    lat = np.asarray(field.lat, dtype=float)
    lon = normalize_longitudes(field.lon)
    values = np.asarray(field.values, dtype=float)
    lat_order = np.argsort(lat)
    lon_order = np.argsort(lon)
    lat = lat[lat_order]
    lon = lon[lon_order]
    values = values[np.ix_(lat_order, lon_order)]

    lat_unique, lat_index = np.unique(lat, return_index=True)
    lon_unique, lon_index = np.unique(lon, return_index=True)
    values = values[np.ix_(lat_index, lon_index)]
    interpolator = RegularGridInterpolator((lat_unique, lon_unique), values, method="linear", bounds_error=False, fill_value=np.nan)
    target_lon_mesh, target_lat_mesh = np.meshgrid(target_lon, target_lat)
    points = np.column_stack((target_lat_mesh.ravel(), target_lon_mesh.ravel()))
    return interpolator(points).reshape(target_lat_mesh.shape)


def _curvilinear(field: Field, target_lat: np.ndarray, target_lon: np.ndarray) -> np.ndarray:
    from scipy.interpolate import griddata

    lat = np.asarray(field.lat, dtype=float).ravel()
    lon = normalize_longitudes(field.lon).ravel()
    values = np.asarray(field.values, dtype=float).ravel()
    finite = np.isfinite(lat) & np.isfinite(lon) & np.isfinite(values)
    if finite.sum() < 4:
        raise ValueError("Cobertura insuficiente para regridding.")
    target_lon_mesh, target_lat_mesh = np.meshgrid(target_lon, target_lat)
    return griddata(np.column_stack((lat[finite], lon[finite])), values[finite], (target_lat_mesh, target_lon_mesh), method="linear", fill_value=np.nan)


def regrid_to_common_grid(field: Field, target_lat: np.ndarray, target_lon: np.ndarray) -> Field:
    if field.lat.ndim == field.lon.ndim == 1:
        values = _rectilinear(field, target_lat, target_lon)
    elif field.lat.ndim == field.lon.ndim == 2:
        values = _curvilinear(field, target_lat, target_lon)
    else:
        raise ValueError("lat e lon devem usar a mesma dimensionalidade.")
    if not np.isfinite(values).any():
        raise ValueError("O domínio do campo não cobre a grade escolhida.")
    return Field(lat=target_lat, lon=target_lon, values=values, unit=field.unit, valid_time=field.valid_time, model=field.model, product=field.product, run=field.run, forecast_hour=field.forecast_hour)
