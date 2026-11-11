from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np

from ..config import MAX_FIELD_BYTES


@dataclass(frozen=True, slots=True)
class Field:
    lat: np.ndarray
    lon: np.ndarray
    values: np.ndarray
    unit: str
    valid_time: str
    model: str
    product: str
    run: str
    forecast_hour: int

    def with_values(self, values: np.ndarray, unit: str | None = None) -> "Field":
        return replace(self, values=np.asarray(values, dtype=float), unit=unit or self.unit)


def _scalar_text(archive: np.lib.npyio.NpzFile, name: str, fallback: str = "") -> str:
    if name not in archive.files:
        return fallback
    value = archive[name]
    if value.size != 1:
        raise ValueError(f"Metadado {name} precisa ser escalar.")
    return str(value.item())


def load_field(path: Path, *, model: str, product: str, run: str, forecast_hour: int) -> Field:
    if path.stat().st_size > MAX_FIELD_BYTES:
        raise ValueError("Campo excede o tamanho máximo permitido.")
    with np.load(path, allow_pickle=False) as archive:
        required = {"lat", "lon", "values"}
        if not required.issubset(archive.files):
            raise ValueError("Campo NPZ precisa conter lat, lon e values.")
        lat = np.asarray(archive["lat"], dtype=float)
        lon = np.asarray(archive["lon"], dtype=float)
        values = np.asarray(archive["values"], dtype=float)
        unit = _scalar_text(archive, "unit")
        valid_time = _scalar_text(archive, "valid_time")

    if values.ndim != 2 or lat.ndim not in {1, 2} or lon.ndim not in {1, 2}:
        raise ValueError("Grade inválida: values deve ser 2D e lat/lon devem ser 1D ou 2D.")
    if lat.ndim == lon.ndim == 1 and values.shape != (lat.size, lon.size):
        raise ValueError("Dimensões de values não correspondem aos eixos lat/lon.")
    if lat.ndim == lon.ndim == 2 and (lat.shape != values.shape or lon.shape != values.shape):
        raise ValueError("Grades curvilíneas não correspondem a values.")
    if not np.isfinite(lat).any() or not np.isfinite(lon).any() or not np.isfinite(values).any():
        raise ValueError("Campo sem valores finitos.")
    return Field(lat=lat, lon=lon, values=values, unit=unit, valid_time=valid_time, model=model, product=product, run=run, forecast_hour=forecast_hour)
