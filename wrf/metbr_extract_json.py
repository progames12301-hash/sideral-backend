#!/usr/bin/env python3
import argparse
import gzip
import json
from pathlib import Path

import numpy as np
from netCDF4 import Dataset

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
        a[(~np.isfinite(a)) | (a <= -100.0)] = np.nan
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
    nz = coordinate.shape[0]
    result = np.full(expected_shape, np.nan, dtype=np.float64)
    for k in range(nz - 1):
        c0, c1 = coordinate[k], coordinate[k + 1]
        v0, v1 = values[k], values[k + 1]
        valid = (
            np.isfinite(c0) & np.isfinite(c1) &
            np.isfinite(v0) & np.isfinite(v1) &
            (np.abs(c1 - c0) > 1e-6) &
            ((c0 - target) * (c1 - target) <= 0)
        )
        if not valid.any():
            continue
        frac = np.zeros(expected_shape, dtype=np.float64)
        frac[valid] = (target - c0[valid]) / (c1[valid] - c0[valid])
        result[valid] = v0[valid] + frac[valid] * (v1[valid] - v0[valid])
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


def extract_available_fields(ds, expected_shape, reflectivity):
    """Extracts fields from this WRFOUT only; missing fields stay absent."""
    fields = {"reflectivity": np.asarray(reflectivity, dtype=np.float64)}
    sources = {"reflectivity": "REFL_10CM nativo; máximo vertical da coluna"}
    native_shape = expected_shape
    expected_size = int(expected_shape[0] * expected_shape[1])

    # Precipitation accumulation since model initialization: RAINC + RAINNC.
    rain_parts = []
    rain_names = []
    for name in ("RAINC", "RAINNC", "RAINSH"):
        arr, found = _read_2d(ds, (name,), native_shape)
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


def write_gzip_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf-8")
    with path.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, compresslevel=9, mtime=0) as compressed:
            compressed.write(body)


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
        "gridFile": "grid.json", "framePathTemplate": "frames/f{forecastHour:03d}.json.gz",
        "frameCount": len(frames), "temporalResolutionMinutes": 60,
        "reflectivitySource": sources, "nativeGrid": True, "initTime": init_time,
        "availableVariables": available_all, "availableVariablesAnyFrame": available_any,
        "nativeVariables": native_variables, "fieldSources": field_sources_serialized,
    }
    metadata = {
        "schemaVersion": "1.2", "model": "METBR WRF 4 km", "modelKey": "metbr_wrf",
        "resolutionKm": 4, "grid": {"nx": nx, "ny": ny},
        "gridFile": "grid.json", "framePathTemplate": "frames/f{forecastHour:03d}.json.gz",
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

    # Validate serialized JSON, native coordinate grid, reflectivity and every
    # multi-variable frame. Missing model variables are reported, not faked.
    serialized = json.loads(json_path.read_text(encoding="utf-8"))
    validate_frames(serialized["frames"], nx, ny)
    grid_saved = json.loads(grid_path.read_text(encoding="utf-8"))
    n_grid = nx * ny
    assert len(grid_saved["fields"]["lat"]) == n_grid and len(grid_saved["fields"]["lon"]) == n_grid
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
