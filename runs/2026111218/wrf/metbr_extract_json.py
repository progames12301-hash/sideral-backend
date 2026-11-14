#!/usr/bin/env python3
import argparse
import gzip
import json
from pathlib import Path

import numpy as np
from netCDF4 import Dataset
import xarray as xr

from extract_wrf_severe_core import FIELDS as SEVERE_FIELDS, METHODS as SEVERE_METHODS, compute as compute_severe

REFLECTIVITY_NAMES = (
    "REFL_10CM", "REFL_10CM_NATIVE", "REFLECTIVITY_10CM", "REFLECTIVITY",
    "REFL", "DBZ10", "DBZ", "ZH",
)


def read_time(ds):
    times = ds.variables.get("Times")
    if times is None:
        return ""
    raw = np.asarray(times[:])
    chars = raw[0] if raw.ndim == 2 else raw
    text = "".join(x.decode() if isinstance(x, bytes) else str(x) for x in chars)
    return text.replace("_", "T") + "Z"


def describe_variables(ds):
    names = sorted(ds.variables.keys())
    radar = [n for n in names if any(k in n.upper() for k in ("REFL", "REFLECT", "DBZ", "ZH"))]
    return names, radar


def _as_2d_reflectivity(value, name, expected_shape=None):
    a = np.ma.asarray(value)
    if np.ma.isMaskedArray(a):
        a = a.filled(np.nan)
    a = np.asarray(a, dtype=np.float32)
    if a.ndim < 2:
        return None
    if expected_shape is not None and tuple(a.shape[-2:]) != tuple(expected_shape):
        return None
    if a.ndim > 2:
        reduce_axes = tuple(range(a.ndim - 2))
        with np.errstate(all="ignore"):
            a = np.nanmax(a, axis=reduce_axes)
    if a.ndim != 2 or not np.isfinite(a).any():
        return None
    return np.asarray(a, dtype=np.float32)


def try_reflectivity_file(path, expected_shape=None):
    try:
        with Dataset(path) as ds:
            names, radar_names = describe_variables(ds)
            print(f"[METBR][DIAG] {path.name}: {len(names)} variaveis")
            print(f"[METBR][DIAG] {path.name}: candidatas de refletividade: {radar_names or 'nenhuma'}")
            ordered = [n for n in REFLECTIVITY_NAMES if n in ds.variables]
            for n in radar_names:
                if n not in ordered:
                    ordered.append(n)
            for name in ordered:
                var = ds.variables[name]
                print(f"[METBR][DIAG] {path.name}:{name}: dimensoes={getattr(var, 'dimensions', ())} shape={getattr(var, 'shape', ())}")
                refl = _as_2d_reflectivity(var[:], name, expected_shape)
                if refl is None:
                    continue
                print(f"[METBR] refletividade nativa encontrada: {path.name}:{name} shape={refl.shape}")
                return refl, name, path.name
    except Exception as exc:
        print(f"[METBR][DIAG] falha lendo {path}: {exc}")
    return None, None, None


def find_native_reflectivity(run_dir, expected_shape=None):
    root = Path(run_dir)
    candidates = sorted(root.glob("wrfout_d01_*"))
    others = []
    for pattern in ("*.nc", "*.nc4", "*.cdf", "*reflect*", "*refl*", "*dbz*"):
        for p in root.glob(pattern):
            if p not in candidates and p.is_file():
                others.append(p)
    candidates += sorted(set(others))
    if not candidates:
        raise RuntimeError(f"Nenhum NetCDF/WRFOUT encontrado em {run_dir}")
    for path in candidates:
        refl, source, source_file = try_reflectivity_file(path, expected_shape)
        if refl is not None:
            return refl, source, source_file
    raise RuntimeError("Nenhuma variável de refletividade nativa foi encontrada. Nenhuma aproximação dBZ será usada.")


def grid_shape(ds):
    lat = ds.variables.get("XLAT")
    lon = ds.variables.get("XLONG")
    if lat is None or lon is None:
        raise RuntimeError("XLAT/XLONG ausentes no WRFOUT")
    shape = np.asarray(lat[:]).shape[-2:]
    if shape != np.asarray(lon[:]).shape[-2:]:
        raise RuntimeError("XLAT/XLONG com grades diferentes")
    return int(shape[1]), int(shape[0])


def clean_array(a, expected_size):
    arr = np.asarray(a, dtype=np.float32)
    flat = np.nan_to_num(arr.reshape(-1), nan=-9999.0, posinf=-9999.0, neginf=-9999.0)
    if flat.size != expected_size:
        raise RuntimeError(f"REFL_10CM incompleta: {flat.size} valores; esperados {expected_size}")
    return flat.tolist()


def validate_frames(frames, nx, ny):
    expected = int(nx) * int(ny)
    for frame in frames:
        values = frame.get("reflectivityDbz")
        actual = len(values) if isinstance(values, list) else 0
        if actual != expected:
            raise RuntimeError(f"Frame {frame.get('file', '?')} possui {actual} valores; esperados {expected} ({ny}x{nx})")
    print(f"[METBR][VALID] {len(frames)} frames validados: {expected} valores/frame ({ny}x{nx})")


def native_grid_fields(ds, nx, ny):
    """Preserva as coordenadas XLAT/XLONG reais da grade METBR uma única vez."""
    result = {}
    for source_name, output_name in (("XLAT", "lat"), ("XLONG", "lon")):
        variable = ds.variables.get(source_name)
        if variable is None:
            raise RuntimeError(f"{source_name} ausente no WRFOUT METBR")
        values = np.asarray(variable[:], dtype=np.float64)
        while values.ndim > 2:
            values = values[0]
        if values.shape != (ny, nx):
            raise RuntimeError(f"{source_name} tem grade {values.shape}; esperada {(ny, nx)}")
        if not np.isfinite(values).all():
            raise RuntimeError(f"{source_name} contém coordenadas não finitas")
        result[output_name] = np.round(values, 5).reshape(-1).tolist()
    return result




FIELD_PRECISION = {
    "reflectivity": 1, "precipitation": 2, "temperature": 2, "humidity": 2,
    "mucape": 1, "windSpeed": 2, "windDirection": 1, "bulkShear": 2,
    "vorticity850": 9, "waterVapor": 2,
}


def _read_2d(ds, aliases, expected_shape):
    """Reads a surface/grid field only when its horizontal shape matches this WRFOUT."""
    for name in aliases:
        var = ds.variables.get(name)
        if var is None:
            continue
        try:
            a = np.ma.asarray(var[:])
            if np.ma.isMaskedArray(a):
                a = a.filled(np.nan)
            a = np.asarray(a, dtype=np.float64)
            while a.ndim > 2:
                a = a[0]
            if a.ndim != 2 or tuple(a.shape) != tuple(expected_shape):
                continue
            a = a.copy()
            a[(~np.isfinite(a)) | (a <= -100.0)] = np.nan
            if np.isfinite(a).any():
                return a, name
        except Exception as exc:
            print(f"[METBR][VAR] não foi possível ler {name}: {exc}")
    return None, None


def _read_3d(ds, name, expected_shape):
    """Returns a WRF mass/staggered 3-D variable with its Time axis removed."""
    var = ds.variables.get(name)
    if var is None:
        return None
    try:
        a = np.ma.asarray(var[:])
        if np.ma.isMaskedArray(a):
            a = a.filled(np.nan)
        a = np.asarray(a, dtype=np.float64)
        while a.ndim > 3:
            a = a[0]
        if a.ndim != 3:
            return None
        dims = list(getattr(var, "dimensions", ()))
        if dims and dims[0].lower() == "time":
            dims = dims[1:]
        # Destagger U/V horizontally to the WRF mass grid.
        if a.shape[-1] == expected_shape[1] + 1 and (
            not dims or "west_east_stag" in dims[-1].lower()
        ):
            a = 0.5 * (a[..., :-1] + a[..., 1:])
        elif a.shape[-1] == expected_shape[1] + 1:
            a = 0.5 * (a[..., :-1] + a[..., 1:])
        if a.shape[-2] == expected_shape[0] + 1:
            a = 0.5 * (a[..., :-1, :] + a[..., 1:, :])
        if tuple(a.shape[-2:]) != tuple(expected_shape):
            return None
        # Do not discard negative values here: WRF pressure perturbation P
        # can legitimately be negative and is added to PB to obtain pressure.
        a[~np.isfinite(a)] = np.nan
        return a
    except Exception as exc:
        print(f"[METBR][VAR] não foi possível ler volume {name}: {exc}")
        return None


def _field_list(values, expected_size, name):
    arr = np.asarray(values, dtype=np.float64).reshape(-1)
    if arr.size != expected_size:
        raise RuntimeError(f"Campo METBR {name}: {arr.size} valores; esperados {expected_size}")
    precision = FIELD_PRECISION.get(name, 3)
    output = []
    for value in arr:
        if not np.isfinite(value) or value <= -100.0:
            output.append(None)
        else:
            output.append(round(float(value), precision))
    return output


def _mass_wind(ds, name, expected_shape):
    var = ds.variables.get(name)
    if var is None:
        return None
    a = _read_3d(ds, name, expected_shape)
    if a is None:
        return None
    # U/V have one fewer point after horizontal destaggering. Do not assume
    # that a same-sized field is valid unless it still has a vertical axis.
    return a if a.ndim == 3 else None


def _interp_vertical(coordinate, values, target, expected_shape):
    """Linear vertical interpolation to one pressure (Pa) or AGL height (m)."""
    if coordinate is None or values is None or coordinate.shape != values.shape:
        return None
    if coordinate.ndim != 3 or tuple(coordinate.shape[-2:]) != tuple(expected_shape):
        return None
    c0, c1 = coordinate[:-1], coordinate[1:]
    v0, v1 = values[:-1], values[1:]
    crossing = (
        np.isfinite(c0) & np.isfinite(c1) &
        np.isfinite(v0) & np.isfinite(v1) &
        (np.abs(c1 - c0) > 1e-6) &
        ((c0 - target) * (c1 - target) <= 0)
    )
    has_crossing = crossing.any(axis=0)
    if not has_crossing.any():
        return None
    # One crossing segment per column for normal WRF height/pressure profiles.
    # Choosing the first keeps interpolation deterministic and avoids a
    # Python-level loop allocating multiple 2-D arrays for each level/frame.
    k = np.argmax(crossing, axis=0)[None, :, :]
    c0k = np.take_along_axis(c0, k, axis=0)[0]
    c1k = np.take_along_axis(c1, k, axis=0)[0]
    v0k = np.take_along_axis(v0, k, axis=0)[0]
    v1k = np.take_along_axis(v1, k, axis=0)[0]
    frac = (target - c0k) / (c1k - c0k)
    result = v0k + frac * (v1k - v0k)
    result[~has_crossing | ~np.isfinite(result)] = np.nan
    return result if np.isfinite(result).any() else None


def _derived_bulk_shear(ds, expected_shape, u10, v10):
    u = _mass_wind(ds, "U", expected_shape)
    v = _mass_wind(ds, "V", expected_shape)
    ph = _read_3d(ds, "PH", expected_shape)
    phb = _read_3d(ds, "PHB", expected_shape)
    hgt, _ = _read_2d(ds, ("HGT",), expected_shape)
    if u is None or v is None or ph is None or phb is None or hgt is None:
        return None
    if u.shape != v.shape or ph.shape != phb.shape or ph.shape[0] != u.shape[0] + 1:
        return None
    height_msl = 0.5 * (ph[:-1] + ph[1:] + phb[:-1] + phb[1:]) / 9.80665
    height_agl = height_msl - hgt[None, :, :]
    u6 = _interp_vertical(height_agl, u, 6000.0, expected_shape)
    v6 = _interp_vertical(height_agl, v, 6000.0, expected_shape)
    if u6 is None or v6 is None or u10 is None or v10 is None:
        return None
    shear = np.sqrt((u6 - u10) ** 2 + (v6 - v10) ** 2)
    return shear if np.isfinite(shear).any() else None


def _derived_vorticity850(ds, expected_shape):
    u = _mass_wind(ds, "U", expected_shape)
    v = _mass_wind(ds, "V", expected_shape)
    p = _read_3d(ds, "P", expected_shape)
    pb = _read_3d(ds, "PB", expected_shape)
    if u is None or v is None or p is None or pb is None or u.shape != v.shape:
        return None
    pressure = p + pb
    if pressure.shape != u.shape:
        return None
    u850 = _interp_vertical(pressure, u, 85000.0, expected_shape)
    v850 = _interp_vertical(pressure, v, 85000.0, expected_shape)
    if u850 is None or v850 is None:
        return None

    # Rotate grid-relative WRF winds to earth-relative components when the
    # model output includes the native rotation coefficients.
    cosa, _ = _read_2d(ds, ("COSALPHA",), expected_shape)
    sina, _ = _read_2d(ds, ("SINALPHA",), expected_shape)
    if cosa is None:
        cosa = np.ones(expected_shape, dtype=np.float64)
    if sina is None:
        sina = np.zeros(expected_shape, dtype=np.float64)
    east = u850 * cosa - v850 * sina
    north = u850 * sina + v850 * cosa

    try:
        dx = float(getattr(ds, "DX"))
        dy = float(getattr(ds, "DY"))
    except Exception:
        dx = dy = 4000.0
    mapfac, _ = _read_2d(ds, ("MAPFAC_M",), expected_shape)
    if mapfac is None:
        mapfac = np.ones(expected_shape, dtype=np.float64)
    mapfac = np.where(np.isfinite(mapfac) & (mapfac > 0), mapfac, 1.0)
    dx_local = dx / mapfac
    dy_local = dy / mapfac
    dv_dx = np.gradient(north, axis=1) / dx_local
    du_dy = np.gradient(east, axis=0) / dy_local
    vort = dv_dx - du_dy
    vort[~(np.isfinite(dv_dx) & np.isfinite(du_dy))] = np.nan
    return vort if np.isfinite(vort).any() else None



def _derived_pressure_level_fields(ds, expected_shape, pressure_target_pa=85000.0):
    """Derive 850-hPa temperature and earth-relative wind from native WRF levels."""
    u = _mass_wind(ds, "U", expected_shape)
    v = _mass_wind(ds, "V", expected_shape)
    p = _read_3d(ds, "P", expected_shape)
    pb = _read_3d(ds, "PB", expected_shape)
    theta_pert = _read_3d(ds, "T", expected_shape)
    if any(value is None for value in (u, v, p, pb, theta_pert)):
        return {}, {}
    if not (u.shape == v.shape == p.shape == pb.shape == theta_pert.shape):
        return {}, {}
    pressure = p + pb
    theta = theta_pert + 300.0
    with np.errstate(invalid="ignore", divide="ignore", over="ignore"):
        temperature_k = theta * (np.maximum(pressure, 1.0) / 100000.0) ** 0.2854
    t_target = _interp_vertical(pressure, temperature_k, pressure_target_pa, expected_shape)
    u_target = _interp_vertical(pressure, u, pressure_target_pa, expected_shape)
    v_target = _interp_vertical(pressure, v, pressure_target_pa, expected_shape)
    if t_target is None or u_target is None or v_target is None:
        return {}, {}
    cosa, _ = _read_2d(ds, ("COSALPHA",), expected_shape)
    sina, _ = _read_2d(ds, ("SINALPHA",), expected_shape)
    if cosa is None:
        cosa = np.ones(expected_shape, dtype=np.float64)
    if sina is None:
        sina = np.zeros(expected_shape, dtype=np.float64)
    east = u_target * cosa - v_target * sina
    north = u_target * sina + v_target * cosa
    speed = np.sqrt(east ** 2 + north ** 2) * 3.6
    direction = (270.0 - np.degrees(np.arctan2(north, east))) % 360.0
    fields = {
        "temperature850": t_target - 273.15,
        "windSpeed850": speed,
        "windDirection850": direction,
    }
    p_hpa = pressure_target_pa / 100.0
    sources = {
        "temperature850": "WRF T+300 interpolada em pressão para 850 hPa; °C",
        "windSpeed850": "U/V destaggerados e rotacionados, interpolados em 850 hPa; km/h",
        "windDirection850": "U/V destaggerados e rotacionados, interpolados em 850 hPa; graus meteorológicos",
    }
    return fields, sources


def _derived_omega700(ds, expected_shape):
    """Derive pressure vertical velocity at 700 hPa from W and native thermodynamics."""
    w_stag = _read_3d(ds, "W", expected_shape)
    p = _read_3d(ds, "P", expected_shape)
    pb = _read_3d(ds, "PB", expected_shape)
    theta_pert = _read_3d(ds, "T", expected_shape)
    if any(value is None for value in (w_stag, p, pb, theta_pert)):
        return None
    if w_stag.shape[0] != p.shape[0] + 1 or p.shape != pb.shape or p.shape != theta_pert.shape:
        return None
    w = 0.5 * (w_stag[:-1] + w_stag[1:])
    pressure = p + pb
    theta = theta_pert + 300.0
    with np.errstate(invalid="ignore", divide="ignore", over="ignore"):
        temperature = theta * (np.maximum(pressure, 1.0) / 100000.0) ** 0.2854
    w700 = _interp_vertical(pressure, w, 70000.0, expected_shape)
    t700 = _interp_vertical(pressure, temperature, 70000.0, expected_shape)
    if w700 is None or t700 is None:
        return None
    density = 70000.0 / (287.05 * np.maximum(t700, 150.0))
    omega = -density * 9.80665 * w700
    omega[~(np.isfinite(w700) & np.isfinite(t700))] = np.nan
    return omega if np.isfinite(omega).any() else None


def _derived_surface_thermodynamics(ds, expected_shape):
    """Derive surface dewpoint, equivalent potential temperature, LCL and pressure maps."""
    t2, _ = _read_2d(ds, ("T2",), expected_shape)
    q2, _ = _read_2d(ds, ("Q2",), expected_shape)
    psfc, _ = _read_2d(ds, ("PSFC",), expected_shape)
    terrain, _ = _read_2d(ds, ("HGT",), expected_shape)
    if any(value is None for value in (t2, q2, psfc)):
        return {}, {}
    # WRF Q2 is normally kg/kg; tolerate legacy outputs stored as g/kg.
    q = np.asarray(q2, dtype=np.float64).copy()
    q = np.where(q > 0.1, q / 1000.0, q)
    q = np.clip(q, 1.0e-8, 0.5)
    mixing_ratio = q / np.maximum(1.0 - q, 1.0e-8)
    temp_k = t2 if np.nanmedian(t2) > 150 else t2 + 273.15
    psfc_pa = psfc if np.nanmedian(psfc) > 2000 else psfc * 100.0
    psfc_hpa = psfc_pa / 100.0
    vapor_pa = mixing_ratio * psfc_pa / (0.622 + mixing_ratio)
    vapor_hpa = vapor_pa / 100.0
    with np.errstate(invalid="ignore", divide="ignore", over="ignore"):
        loge = np.log(np.maximum(vapor_hpa, 1.0e-5) / 6.112)
        dewpoint_c = 243.5 * loge / (17.67 - loge)
        dewpoint_c = np.minimum(dewpoint_c, temp_k - 273.15)
        dewpoint_k = dewpoint_c + 273.15
        tlcl_k = 1.0 / (1.0 / (dewpoint_k - 56.0) + np.log(temp_k / dewpoint_k) / 800.0) + 56.0
        # Bolton equivalent potential temperature, using vapor-corrected pressure.
        theta_e = temp_k * (1000.0 / np.maximum(psfc_hpa - vapor_hpa, 1.0)) ** (0.2854 * (1.0 - 0.28 * mixing_ratio))
        theta_e *= np.exp((3376.0 / tlcl_k - 2.54) * mixing_ratio * (1.0 + 0.81 * mixing_ratio))
        p_lcl_hpa = np.maximum(psfc_hpa - vapor_hpa, 1.0) * (tlcl_k / temp_k) ** (1.0 / 0.2854)
        lcl_m = (287.05 * (temp_k + tlcl_k) / 2.0 / 9.80665) * np.log(np.maximum(psfc_hpa, 1.0) / np.maximum(p_lcl_hpa, 1.0))
    fields = {
        "dewpoint2m": dewpoint_c,
        "thetaE2m": theta_e,
        "lcl": lcl_m,
        "surfacePressure": psfc_hpa,
    }
    sources = {
        "dewpoint2m": "Q2 e PSFC; ponto de orvalho calculado pela relação de vapor",
        "thetaE2m": "T2, Q2 e PSFC; temperatura potencial equivalente (Bolton)",
        "lcl": "T2, ponto de orvalho e PSFC; altura de LCL estimada em m AGL",
        "surfacePressure": "PSFC nativo convertido para hPa",
    }
    if terrain is not None:
        terrain_m = np.asarray(terrain, dtype=np.float64)
        mean_temp_k = temp_k + 0.00325 * terrain_m
        virtual_temp_k = np.maximum(mean_temp_k * (1.0 + 0.61 * q), 180.0)
        with np.errstate(invalid="ignore", divide="ignore", over="ignore"):
            slp = psfc_hpa * np.exp(9.80665 * terrain_m / (287.05 * virtual_temp_k))
        # Do not publish implausible numerical blow-ups from invalid high terrain or pressure.
        slp[~(np.isfinite(slp) & np.isfinite(psfc_hpa) & (psfc_hpa > 0) & (slp >= 800) & (slp <= 1150))] = np.nan
        if np.isfinite(slp).any():
            fields["seaLevelPressure"] = slp
            sources["seaLevelPressure"] = "PSFC reduzida ao nível do mar por aproximação hipsométrica com T2, Q2 e HGT"
    return fields, sources

def extract_available_fields(ds, expected_shape, reflectivity):
    """Extracts fields from this WRFOUT only; missing fields stay absent."""
    fields = {"reflectivity": np.asarray(reflectivity, dtype=np.float64)}
    sources = {"reflectivity": "REFL_10CM nativo; máximo vertical da coluna"}
    native_shape = expected_shape
    expected_size = int(expected_shape[0] * expected_shape[1])

    # Precipitation accumulation since model initialization: RAINC + RAINNC.
    rain_parts = []
    rain_names = []
    for name in ("RAINC", "RAINNC"):
        arr, found = _read_2d(ds, (name,), native_shape)
        if arr is not None:
            rain_parts.append(np.nan_to_num(arr, nan=0.0))
            rain_names.append(found)
    # RAINSH is used only as a fallback when the usual convective/nonconvective
    # accumulators are both absent; do not silently add it to RAINC+RAINNC.
    if not rain_parts:
        arr, found = _read_2d(ds, ("RAINSH",), native_shape)
        if arr is not None:
            rain_parts.append(np.nan_to_num(arr, nan=0.0))
            rain_names.append(found)
    if rain_parts:
        precip = np.zeros(native_shape, dtype=np.float64)
        for arr in rain_parts:
            precip += arr
        fields["precipitation"] = precip
        sources["precipitation"] = " + ".join(rain_names) + " (acumulado desde o início da rodada, mm)"
    else:
        arr, found = _read_2d(ds, ("APCP", "PRECIP", "PRECIPITATION", "ACCUM_PRECIP"), native_shape)
        if arr is not None:
            fields["precipitation"] = arr
            sources["precipitation"] = found

    # 2-m temperature.
    t2, t2_name = _read_2d(ds, ("T2", "TEMP2", "T_2M"), native_shape)
    if t2 is not None:
        unit = str(getattr(ds.variables[t2_name], "units", "K")).strip().lower()
        if unit in ("c", "degc", "degree_celsius", "degrees c", "celsius", "°c"):
            fields["temperature"] = t2
            sources["temperature"] = f"{t2_name} nativo ({unit})"
        else:
            fields["temperature"] = t2 - 273.15
            sources["temperature"] = f"{t2_name} nativo convertido de K para °C"

    # 2-m relative humidity. Prefer native RH2; otherwise derive it from Q2,
    # PSFC and T2 using the vapor-pressure relationship.
    rh, rh_name = _read_2d(ds, ("RH2", "RELHUM2", "RH_2M"), native_shape)
    if rh is not None:
        finite_rh = rh[np.isfinite(rh)]
        if finite_rh.size and np.nanmax(finite_rh) <= 1.5:
            rh = rh * 100.0
        fields["humidity"] = np.clip(rh, 0.0, 100.0)
        sources["humidity"] = f"{rh_name} nativo (%)"
    elif t2 is not None:
        q2, q2_name = _read_2d(ds, ("Q2",), native_shape)
        psfc, psfc_name = _read_2d(ds, ("PSFC",), native_shape)
        if q2 is not None and psfc is not None:
            q = np.where(q2 > 0.1, q2 / 1000.0, q2)
            temp_c = t2 - 273.15
            es_pa = 611.2 * np.exp((17.67 * temp_c) / (temp_c + 243.5))
            e_pa = q * psfc / (0.622 + q)
            rh = 100.0 * e_pa / es_pa
            rh[~(np.isfinite(temp_c) & np.isfinite(q) & np.isfinite(psfc) & (es_pa > 0))] = np.nan
            fields["humidity"] = np.clip(rh, 0.0, 100.0)
            sources["humidity"] = f"derivada de {q2_name}, {psfc_name} e T2; equação de vapor"

    # 10-m wind speed and direction (UI contract: km/h and meteorological degrees).
    u10, u10_name = _read_2d(ds, ("U10",), native_shape)
    v10, v10_name = _read_2d(ds, ("V10",), native_shape)
    if u10 is not None and v10 is not None:
        fields["windSpeed"] = np.sqrt(u10 ** 2 + v10 ** 2) * 3.6
        fields["windDirection"] = (270.0 - np.degrees(np.arctan2(v10, u10))) % 360.0
        sources["windSpeed"] = f"{u10_name}/{v10_name}; m/s convertido para km/h"
        sources["windDirection"] = f"{u10_name}/{v10_name}; direção meteorológica em graus"

    # Column precipitable water, integrated from QVAPOR and pressure when a
    # native PWAT/TCWV field is not provided.
    pw, pw_name = _read_2d(ds, ("PWAT", "PW", "TCWV", "IWV", "TPW", "PRECIPITABLE_WATER"), native_shape)
    if pw is not None:
        fields["waterVapor"] = pw
        sources["waterVapor"] = f"{pw_name} nativo"
    else:
        qv = _read_3d(ds, "QVAPOR", native_shape)
        p = _read_3d(ds, "P", native_shape)
        pb = _read_3d(ds, "PB", native_shape)
        if qv is not None and p is not None and pb is not None and qv.shape == p.shape == pb.shape:
            pressure = p + pb
            dp = np.abs(np.gradient(pressure, axis=0))
            pw = np.nansum(qv * dp, axis=0) / 9.80665
            pw[~np.isfinite(qv).any(axis=0)] = np.nan
            if np.isfinite(pw).any():
                fields["waterVapor"] = pw
                sources["waterVapor"] = "QVAPOR integrado verticalmente com P+PB; água precipitável estimada em mm"

    # CAPE is published only when the WRFOUT contains an appropriate native
    # 2-D diagnostic; the first slice of CAPE_2D corresponds to MCAPE.
    cape, cape_name = _read_2d(ds, ("MUCAPE", "MCAPE", "MUCAPE_2D", "MCAPE_2D", "CAPE_2D"), native_shape)
    if cape is not None:
        fields["mucape"] = np.maximum(cape, 0.0)
        sources["mucape"] = f"{cape_name} nativo; unidade assumida J/kg"

    # Bulk shear: prefer an explicit published field; otherwise compute 0–6 km
    # bulk shear from U/V plus geopotential and terrain fields.
    shear, shear_name = _read_2d(ds, ("BULK_SHEAR", "BULKSHEAR", "SHEAR06", "SHEAR_6KM", "SHEAR_0_6KM", "SHR6"), native_shape)
    if shear is not None:
        fields["bulkShear"] = shear
        sources["bulkShear"] = f"{shear_name} nativo (m/s, unidade confirmada pelo WRF)"
    else:
        shear = _derived_bulk_shear(ds, native_shape, u10, v10)
        if shear is not None:
            fields["bulkShear"] = shear
            sources["bulkShear"] = "derivada de U/V a 10 m e 6 km AGL com PH+PHB e HGT (m/s)"

    # Relative vorticity at 850 hPa: use a dedicated field if present; else
    # interpolate native U/V to 850 hPa and calculate dv/dx - du/dy.
    vort, vort_name = _read_2d(ds, ("VORTICITY850", "REL_VORT850", "RV850", "VORT850"), native_shape)
    if vort is not None:
        fields["vorticity850"] = vort
        sources["vorticity850"] = f"{vort_name} nativo; unidade do campo original preservada"
    else:
        vort = _derived_vorticity850(ds, native_shape)
        if vort is not None:
            fields["vorticity850"] = vort
            sources["vorticity850"] = "derivada de U/V interpolados em 850 hPa; dv/dx - du/dy (s⁻¹)"

    surface_fields, surface_sources = _derived_surface_thermodynamics(ds, native_shape)
    fields.update(surface_fields)
    sources.update(surface_sources)

    pressure_fields, pressure_sources = _derived_pressure_level_fields(ds, native_shape, 85000.0)
    fields.update(pressure_fields)
    sources.update(pressure_sources)

    omega700 = _derived_omega700(ds, native_shape)
    if omega700 is not None:
        fields["omega700"] = omega700
        sources["omega700"] = "W destaggerado e interpolado para 700 hPa; conversão para omega (Pa/s)"

    # Keep only arrays with the native WRF horizontal dimensions and at least
    # one usable value. No proxy data or values from other models are used.
    result = {}
    for name, arr in fields.items():
        if arr is None:
            continue
        arr = np.asarray(arr, dtype=np.float64)
        if arr.shape != native_shape or arr.size != expected_size:
            print(f"[METBR][VAR] descartado {name}: shape {arr.shape}, esperado {native_shape}")
            continue
        arr[(~np.isfinite(arr)) | (arr <= -100.0)] = np.nan
        if np.isfinite(arr).any():
            result[name] = arr
    return result, sources



def extract_native_vertical_profile_grid(ds, expected_shape):
    """Pack the full native METBR WRF F000 vertical column at every grid cell.

    Surface values are prepended to the native mass-level columns, preserving
    the same input variables and thermodynamic/wind conversions as
    wrf/metbr_skewt_south.py. Arrays are float32 and stored level-major in NPZ
    so Render can mmap individual fields instead of loading a full JSON object.
    """
    import math

    nx, ny = int(expected_shape[1]), int(expected_shape[0])
    expected_size = nx * ny
    required_2d = ("HGT", "PSFC", "T2", "Q2", "U10", "V10")
    surface = {}
    missing = []
    for name in required_2d:
        value, found = _read_2d(ds, (name,), expected_shape)
        if value is None:
            missing.append(name)
        else:
            surface[name] = value
    required_3d = ("P", "PB", "T", "QVAPOR", "U", "V", "PH", "PHB")
    fields = {name: _read_3d(ds, name, expected_shape) for name in required_3d}
    missing.extend(name for name, value in fields.items() if value is None)
    if missing:
        raise RuntimeError("METBR F000 sem campos verticais obrigatórios para Skew-T: " + ", ".join(sorted(set(missing))))

    p_pa = fields["P"] + fields["PB"]
    theta_k = fields["T"] + 300.0
    if p_pa.shape != theta_k.shape or p_pa.shape != fields["QVAPOR"].shape:
        raise RuntimeError("P/PB/T/QVAPOR não possuem a mesma grade vertical METBR.")
    if p_pa.shape != fields["U"].shape or p_pa.shape != fields["V"].shape:
        raise RuntimeError("U/V não correspondem à grade horizontal e vertical METBR.")
    if fields["PH"].shape != fields["PHB"].shape or fields["PH"].shape[0] != p_pa.shape[0] + 1:
        raise RuntimeError("PH/PHB não fornecem interfaces verticais compatíveis com a grade METBR.")

    pressure_hpa = p_pa / 100.0
    with np.errstate(invalid="ignore", divide="ignore", over="ignore"):
        temp_k = theta_k * (np.maximum(p_pa, 1.0) / 100000.0) ** 0.2854
        temperature_c = temp_k - 273.15
        qv = np.maximum(fields["QVAPOR"], 1.0e-8)
        vapor_hpa = np.minimum(pressure_hpa * qv / (0.622 + qv), pressure_hpa * 0.98)
        loge = np.log(np.maximum(vapor_hpa, 1.0e-5) / 6.112)
        dewpoint_c = 243.5 * loge / (17.67 - loge)
        dewpoint_c = np.minimum(dewpoint_c, temperature_c)

    # WRF's PH/PHB are staggered vertically; average adjacent interfaces to
    # obtain native mass-level height MSL, then use HGT for AGL filtering.
    height_m = 0.5 * (fields["PH"][:-1] + fields["PH"][1:] +
                      fields["PHB"][:-1] + fields["PHB"][1:]) / 9.80665
    terrain = surface["HGT"]
    psfc_hpa = surface["PSFC"] / 100.0

    u = fields["U"]
    v = fields["V"]
    cosa, _ = _read_2d(ds, ("COSALPHA",), expected_shape)
    sina, _ = _read_2d(ds, ("SINALPHA",), expected_shape)
    if cosa is None:
        cosa = np.ones(expected_shape, dtype=np.float64)
    if sina is None:
        sina = np.zeros(expected_shape, dtype=np.float64)
    east_kt = (u * cosa[None, :, :] - v * sina[None, :, :]) * 1.943844
    north_kt = (v * cosa[None, :, :] + u * sina[None, :, :]) * 1.943844

    # Preserve the vertical-motion diagnostic used by the existing METBR
    # sounding path when native W exists; missing W is represented as NaN.
    w = _read_3d(ds, "W", expected_shape)
    if w is not None and w.shape[0] == p_pa.shape[0] + 1:
        w_mass = 0.5 * (w[:-1] + w[1:])
        with np.errstate(invalid="ignore", divide="ignore", over="ignore"):
            density = p_pa / (287.05 * np.maximum(temp_k, 150.0))
            omega_pa_s = -w_mass * density * 9.80665
    else:
        omega_pa_s = np.full(p_pa.shape, np.nan, dtype=np.float64)

    # Match the METBR soundings: a surface row from PSFC/T2/Q2/U10/V10,
    # followed by native model mass levels above the terrain.
    t2_c = surface["T2"] - 273.15
    q2 = np.maximum(surface["Q2"], 1.0e-8)
    with np.errstate(invalid="ignore", divide="ignore", over="ignore"):
        e2 = np.minimum(psfc_hpa * q2 / (0.622 + q2), psfc_hpa * 0.98)
        l2 = np.log(np.maximum(e2, 1.0e-5) / 6.112)
        td2_c = np.minimum(t2_c, 243.5 * l2 / (17.67 - l2))
    u10_grid = surface["U10"]
    v10_grid = surface["V10"]
    u10_kt = (u10_grid * cosa - v10_grid * sina) * 1.943844
    v10_kt = (v10_grid * cosa + u10_grid * sina) * 1.943844

    vertical_valid = (
        np.isfinite(pressure_hpa) & (pressure_hpa > 0) &
        np.isfinite(temperature_c) & np.isfinite(dewpoint_c) &
        np.isfinite(height_m) & (height_m > terrain[None, :, :] + 5.0) &
        (pressure_hpa < psfc_hpa[None, :, :] - 1.0) &
        np.isfinite(east_kt) & np.isfinite(north_kt)
    )
    surface_valid = (
        np.isfinite(psfc_hpa) & (psfc_hpa > 0) &
        np.isfinite(t2_c) & np.isfinite(td2_c) &
        np.isfinite(terrain) & np.isfinite(u10_kt) & np.isfinite(v10_kt)
    )

    def clean(a, valid=None, dtype=np.float32):
        arr = np.asarray(a, dtype=dtype).copy()
        if valid is not None:
            arr[~valid] = np.nan
        arr[~np.isfinite(arr)] = np.nan
        return arr

    pressure_all = np.concatenate([psfc_hpa[None, :, :], pressure_hpa], axis=0)
    temperature_all = np.concatenate([t2_c[None, :, :], temperature_c], axis=0)
    dewpoint_all = np.concatenate([td2_c[None, :, :], dewpoint_c], axis=0)
    height_all = np.concatenate([terrain[None, :, :], height_m], axis=0)
    u_all = np.concatenate([u10_kt[None, :, :], east_kt], axis=0)
    v_all = np.concatenate([v10_kt[None, :, :], north_kt], axis=0)
    omega_all = np.concatenate([np.full((1, ny, nx), np.nan), omega_pa_s], axis=0)
    all_valid = np.concatenate([surface_valid[None, :, :], vertical_valid], axis=0)

    # If a mandatory profile variable is invalid at a level, leave the row
    # absent as a coherent unit instead of publishing a partially fabricated row.
    return {
        "schema_version": np.asarray([1], dtype=np.int16),
        "grid_nx": np.asarray([nx], dtype=np.int32),
        "grid_ny": np.asarray([ny], dtype=np.int32),
        "lat": clean(np.asarray(ds.variables["XLAT"][:])[0] if np.asarray(ds.variables["XLAT"][:]).ndim == 3 else ds.variables["XLAT"][:]),
        "lon": clean(np.asarray(ds.variables["XLONG"][:])[0] if np.asarray(ds.variables["XLONG"][:]).ndim == 3 else ds.variables["XLONG"][:]),
        "pressure_hpa": clean(pressure_all, all_valid),
        "temperature_c": clean(temperature_all, all_valid, np.float16),
        "dewpoint_c": clean(dewpoint_all, all_valid, np.float16),
        "height_m": clean(height_all, all_valid),
        "u_kt": clean(u_all, all_valid, np.float16),
        "v_kt": clean(v_all, all_valid, np.float16),
        "omega_pa_s": clean(omega_all, all_valid, np.float16),
        "terrain_m": clean(terrain),
    }


def write_gzip_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
    with path.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, compresslevel=9, mtime=0) as compressed:
            compressed.write(body)



def extract_severe_fields(path, nx, ny):
    """Derive shared WRF severe-weather diagnostics from the exact native frame."""
    with xr.open_dataset(path, engine="netcdf4", decode_times=False) as ds:
        rows = np.arange(ny, dtype=int)
        cols = np.arange(nx, dtype=int)
        result = compute_severe(ds, rows, cols)
    if not isinstance(result, tuple) or not result or not isinstance(result[0], dict):
        raise RuntimeError("Núcleo de diagnósticos severos não retornou os campos METBR esperados")
    output = {}
    expected = nx * ny
    for name in SEVERE_FIELDS:
        value = result[0].get(name)
        if value is None:
            continue
        arr = np.asarray(value, dtype=np.float64)
        if arr.size != expected:
            raise RuntimeError(f"METBR {name}: {arr.size} valores; esperados {expected} ({ny}x{nx})")
        flat = [round(float(v), 4) if np.isfinite(v) else None for v in arr.reshape(-1)]
        if any(v is not None for v in flat):
            output[name] = flat
    return output


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--output-dir", required=True)
    args = ap.parse_args()

    files = sorted(Path(args.run_dir).glob("wrfout_d01_*"))
    if not files:
        raise SystemExit("Nenhum wrfout_d01 encontrado")

    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    frames = []
    nx = ny = None
    init_time = ""
    source_descriptions = set()
    coordinates = None
    native_variables = []
    field_source_values = {}

    for index, path in enumerate(files):
        with Dataset(path) as ds:
            x, y = grid_shape(ds)
            expected_shape = (y, x)
            expected_size = x * y
            if index == 0:
                native_variables = sorted(ds.variables.keys())
                print(f"[METBR][DIAG] WRFOUT inicial tem {len(native_variables)} variáveis:")
                print("[METBR][DIAG] VARS=" + ",".join(native_variables))

            # A field is read from the exact WRFOUT valid time. Never replace
            # a missing field/frame with another hour from the same run.
            refl, source_name, source_file = try_reflectivity_file(path, expected_shape)
            if refl is None:
                raise RuntimeError(f"REFL_10CM nativa ausente/inválida no próprio {path.name}; sem fallback para outro horário.")
            if source_name is None or source_file != path.name:
                raise RuntimeError(f"Fonte de refletividade incompatível para {path.name}: {source_file}:{source_name}")
            source_descriptions.add(f"{source_file}:{source_name}")
            if refl.shape != expected_shape:
                raise RuntimeError(f"Grade inesperada em {path.name}: {refl.shape}, esperado {expected_shape}")

            if nx is None:
                nx, ny = x, y
                coordinates = native_grid_fields(ds, nx, ny)
            elif (nx, ny) != (x, y):
                raise RuntimeError("Os WRFOUTs possuem grades diferentes")
            if index == 0:
                # The Runner already reads every F000 WRFOUT here. Extract its
                # native vertical grid once so Render never needs the >100-MB
                # raw NetCDF during each click.
                vertical_fields = extract_native_vertical_profile_grid(ds, expected_shape)
                vertical_path = out / "vertical-f000.npz"
                np.savez_compressed(vertical_path, **vertical_fields)
                if vertical_path.stat().st_size >= 95 * 1024 * 1024:
                    raise RuntimeError(
                        f"Grade vertical F000 muito grande para publicação GitHub: {vertical_path.stat().st_size} bytes"
                    )
                print(f"[METBR][SKEWT] grade vertical F000: {vertical_path.stat().st_size} bytes")

            print(f"[METBR][REFL] arquivo={path.name}")
            print(f"[METBR][REFL] shape_original={getattr(ds.variables[source_name], 'shape', ())}")
            print(f"[METBR][REFL] shape_horizontal={refl.shape[0]}x{refl.shape[1]}")
            print(f"[METBR][REFL] valores={refl.size}")
            if refl.size != expected_size:
                raise RuntimeError(f"REFL_10CM incompleta: {refl.size} valores; esperados {expected_size} ({y}x{x})")

            t = read_time(ds)
            if not init_time:
                init_time = t
            reflectivity_values = clean_array(refl, expected_size)
            field_arrays, field_sources = extract_available_fields(ds, expected_shape, refl)
            field_values = {
                name: _field_list(arr, expected_size, name)
                for name, arr in field_arrays.items()
            }
            # Add the 23 derived/severe diagnostics used by other Sideral WRF
            # products. Each one comes from this exact WRFOUT time; no fake
            # values are substituted when a field is unavailable.
            severe_values = extract_severe_fields(path, nx, ny)
            for field_name, values in severe_values.items():
                field_values[field_name] = values
                description = SEVERE_METHODS.get(field_name, "diagnóstico WRF derivado")
                if isinstance(description, dict):
                    description = description.get("method") or description.get("classification") or "diagnóstico WRF derivado"
                field_sources[field_name] = str(description)
            frame_available = sorted(
                name for name, values in field_values.items()
                if any(value is not None for value in values)
            )
            for field_name, description in field_sources.items():
                if field_name in field_arrays:
                    field_source_values.setdefault(field_name, set()).add(description)

            data_file = f"frames/f{index:03d}.json.gz"
            frame_entry = {
                "file": path.name,
                "time": t,
                "forecastHour": index,
                "dataFile": data_file,
                "reflectivityDbz": reflectivity_values,
                "availableVariables": frame_available,
            }
            frames.append(frame_entry)
            write_gzip_json(out / data_file, {
                "schema": "sideral-metbr-reflectivity-frame-v1",
                "model": "METBR WRF 4 km",
                "modelKey": "metbr_wrf",
                "resolutionKm": 4,
                "grid": {"nx": nx, "ny": ny},
                "gridX": nx,
                "gridY": ny,
                "reflectivitySource": "REFL_10CM_NATIVE",
                "nativeGrid": True,
                "initTime": init_time,
                "forecastHour": index,
                "validTime": t,
                # Legacy key is retained to avoid breaking current reflectivity clients.
                "reflectivityDbz": reflectivity_values,
                # Other variables are native WRF fields or explicitly labelled derivations.
                "fields": field_values,
                "availableVariables": frame_available,
                "fieldSources": {name: field_sources[name] for name in field_values if name in field_sources},
            })

    validate_frames(frames, nx, ny)
    sources = sorted(source_descriptions)
    available_sets = [set(frame.get("availableVariables", [])) for frame in frames]
    available_any = sorted(set().union(*available_sets)) if available_sets else []
    available_all = sorted(set.intersection(*available_sets)) if available_sets else []
    field_sources_serialized = {name: sorted(values) for name, values in sorted(field_source_values.items())}
    grid_payload = {
        "schema": "sideral-metbr-grid-v1",
        "model": "METBR WRF 4 km",
        "modelKey": "metbr_wrf",
        "resolutionKm": 4,
        "nativeGrid": True,
        "grid": {"nx": nx, "ny": ny},
        "initTime": init_time,
        "fields": coordinates,
    }
    grid_path = out / "grid.json"
    grid_path.write_text(json.dumps(grid_payload, separators=(",", ":"), allow_nan=False), encoding="utf-8")

    # Preserve the legacy root index structure and size: multi-variable arrays
    # live in individual compressed frames, not duplicated into this JSON.
    payload = {
        "schemaVersion": "1.2", "model": "METBR WRF 4 km", "modelKey": "metbr_wrf",
        "resolutionKm": 4, "grid": {"nx": nx, "ny": ny}, "frames": frames,
        "gridFile": "grid.json", "framePathTemplate": "frames/f{forecastHour:03d}.json.gz", "verticalProfileFile": "vertical-f000.npz", "verticalProfileForecastHour": 0,
        "frameCount": len(frames), "temporalResolutionMinutes": 60,
        "reflectivitySource": sources, "nativeGrid": True, "initTime": init_time,
        "availableVariables": available_all, "availableVariablesAnyFrame": available_any,
        "nativeVariables": native_variables, "fieldSources": field_sources_serialized,
    }
    metadata = {
        "schemaVersion": "1.2", "model": "METBR WRF 4 km", "modelKey": "metbr_wrf",
        "resolutionKm": 4, "grid": {"nx": nx, "ny": ny},
        "gridFile": "grid.json", "framePathTemplate": "frames/f{forecastHour:03d}.json.gz", "verticalProfileFile": "vertical-f000.npz", "verticalProfileForecastHour": 0,
        "frames": [{
            "file": f["file"], "time": f["time"], "forecastHour": f["forecastHour"],
            "dataFile": f["dataFile"], "availableVariables": f["availableVariables"]
        } for f in frames],
        "frameCount": len(frames), "temporalResolutionMinutes": 60,
        "reflectivitySource": sources, "nativeGrid": True, "initTime": init_time,
        "availableVariables": available_all, "availableVariablesAnyFrame": available_any,
        "nativeVariables": native_variables, "fieldSources": field_sources_serialized,
    }
    json_path = out / "metbr_wrf_4km.json"
    metadata_path = out / "metadata.json"
    json_path.write_text(json.dumps(payload, separators=(",", ":"), allow_nan=False), encoding="utf-8")
    metadata_path.write_text(json.dumps(metadata, separators=(",", ":"), allow_nan=False), encoding="utf-8")

    # Validate serialized JSON, native coordinate grid, reflectivity, F000 vertical grid and every
    # multi-variable frame. Missing model variables are reported, not faked.
    serialized = json.loads(json_path.read_text(encoding="utf-8"))
    validate_frames(serialized["frames"], nx, ny)
    grid_saved = json.loads(grid_path.read_text(encoding="utf-8"))
    n_grid = nx * ny
    assert len(grid_saved["fields"]["lat"]) == n_grid and len(grid_saved["fields"]["lon"]) == n_grid
    with np.load(out / "vertical-f000.npz", allow_pickle=False) as vertical:
        n_levels = vertical["pressure_hpa"].shape[0]
        assert vertical["pressure_hpa"].shape == (n_levels, ny, nx)
        assert n_levels >= 10
        for key in ("temperature_c", "dewpoint_c", "height_m", "u_kt", "v_kt", "omega_pa_s"):
            assert vertical[key].shape == vertical["pressure_hpa"].shape, (key, vertical[key].shape)
        assert vertical["lat"].shape == (ny, nx) and vertical["lon"].shape == (ny, nx)
        assert vertical["terrain_m"].shape == (ny, nx)
        assert np.isfinite(vertical["pressure_hpa"][0]).any()
        assert np.isfinite(vertical["temperature_c"][0]).any()
    print(f"[METBR][SKEWT] grade vertical validada: {n_levels} níveis incluindo superfície, grid={nx}x{ny}")
    native_found = set()
    for frame in frames:
        with gzip.open(out / frame["dataFile"], "rt", encoding="utf-8") as handle:
            hourly = json.load(handle)
        assert hourly["modelKey"] == "metbr_wrf"
        assert hourly["reflectivitySource"] == "REFL_10CM_NATIVE"
        assert len(hourly["reflectivityDbz"]) == n_grid
        assert int(hourly["gridX"]) == nx and int(hourly["gridY"]) == ny
        assert hourly["forecastHour"] == frame["forecastHour"]
        field_map = hourly.get("fields", {})
        for name, values in field_map.items():
            assert isinstance(values, list) and len(values) == n_grid, (frame["file"], name)
        native_found.update(frame.get("availableVariables", []))
    if "reflectivity" not in native_found:
        raise RuntimeError("Nenhuma variável de refletividade foi publicada")
    print(f"METBR JSON pronto: {len(frames)} frames, grade {nx}x{ny}, coordenadas nativas e quadros GZIP multifield")
    print(f"[METBR] variáveis disponíveis em todos os horários: {', '.join(available_all)}")
    print(f"[METBR] variáveis disponíveis em algum horário: {', '.join(available_any)}")
    print(f"[METBR] fontes nativas de refletividade: {', '.join(sources)}")
    print(f"[METBR] fontes por campo: {json.dumps(field_sources_serialized, ensure_ascii=False, separators=(',', ':'))}")
    unavailable = sorted(set(("precipitation", "temperature", "humidity", "windSpeed", "windDirection",
                              "mucape", "bulkShear", "vorticity850", "waterVapor")) - set(available_any))
    print(f"[METBR] campos não disponíveis nesta rodada (não preenchidos artificialmente): {', '.join(unavailable) or 'nenhum'}")


if __name__ == "__main__":
    main()
