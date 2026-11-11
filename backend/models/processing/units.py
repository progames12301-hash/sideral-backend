from __future__ import annotations

import re

import numpy as np

from .field import Field


def _unit_key(unit: str) -> str:
    return re.sub(r"\s+", "", str(unit or "").strip().lower()).replace("**", "^")


def normalize_units(field: Field, variable_kind: str, target_unit: str) -> Field:
    """Normaliza antes de qualquer combinação. Conversões desconhecidas são recusadas."""
    source = _unit_key(field.unit)
    target = _unit_key(target_unit)
    values = np.asarray(field.values, dtype=float)

    if not target or source == target:
        return field.with_values(values, target_unit or field.unit)

    if variable_kind == "precipitation":
        if source in {"m", "meter", "metre"} and target == "mm":
            return field.with_values(values * 1000.0, "mm")
        if source in {"kg/m2", "kgm-2", "kgm^-2", "mm"} and target == "mm":
            return field.with_values(values, "mm")

    if variable_kind == "temperature":
        if source in {"k", "kelvin"} and target in {"°c", "c", "degc"}:
            return field.with_values(values - 273.15, "°C")

    if variable_kind == "pressure":
        if source in {"pa", "pascal", "pascals"} and target == "hpa":
            return field.with_values(values / 100.0, "hPa")

    if variable_kind == "wind":
        if source in {"km/h", "kmh-1", "kmh^-1"} and target in {"m/s", "ms-1", "ms^-1"}:
            return field.with_values(values / 3.6, "m/s")
        if source in {"kt", "kts", "knot", "knots"} and target in {"m/s", "ms-1", "ms^-1"}:
            return field.with_values(values * 0.514444, "m/s")

    if variable_kind == "geopotential":
        if source in {"m2/s2", "m^2s^-2", "m2s-2"} and target == "dam":
            return field.with_values(values / 9.80665 / 10.0, "dam")
        if source in {"m", "gpm"} and target == "dam":
            return field.with_values(values / 10.0, "dam")

    if source in {"1", "dimensionless", "adimensional", ""} and target in {"1", "dimensionless", "adimensional", ""}:
        return field.with_values(values, target_unit)

    raise ValueError(f"Conversão de unidade não suportada: {field.unit!r} → {target_unit!r}.")
