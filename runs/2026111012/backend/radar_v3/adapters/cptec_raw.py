"""CPTEC/INPE public volumetric radar downloader and polar NetCDF -> ODIM HDF5 bridge.

The downloader deliberately keeps the native polar geometry. It refuses files that
cannot expose elevation/azimuth/range dimensions, rather than fabricating a grid.
"""
from __future__ import annotations
import datetime as dt
import re
from pathlib import Path
from urllib.parse import urljoin, urlparse
import requests
import numpy as np
import xarray as xr
import h5py

BASE = "https://ftp.cptec.inpe.br/nowcasting/DADOS/radar_volumetrico/"
FILE_RE = re.compile(r"R(?P<radar>\d+)_(?P<stamp>\d{12})\.nc$", re.I)


def _get(url, timeout=(10, 45)):
    parsed = urlparse(url)
    if parsed.scheme != "https" or not (parsed.hostname or "").endswith(".cptec.inpe.br"):
        raise ValueError("Fonte CPTEC inválida")
    response = requests.get(url, timeout=timeout)
    response.raise_for_status()
    return response


def list_files(limit=96):
    text = _get(BASE).text
    names = re.findall(r'href=["\']([^"\']+\.nc)["\']', text, flags=re.I)
    rows = []
    for name in names:
        filename = Path(name).name
        match = FILE_RE.fullmatch(filename)
        if not match:
            continue
        stamp = dt.datetime.strptime(match.group("stamp"), "%Y%m%d%H%M").replace(tzinfo=dt.timezone.utc)
        rows.append((stamp, match.group("radar"), filename))
    rows.sort(reverse=True)
    return rows[:max(1, int(limit))]


def download_file(filename, destination):
    filename = Path(filename).name
    if not FILE_RE.fullmatch(filename):
        raise ValueError("Arquivo CPTEC inválido")
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    target = destination / filename
    if not target.exists() or target.stat().st_size == 0:
        response = _get(urljoin(BASE, filename), timeout=(10, 120))
        tmp = target.with_suffix(".tmp")
        tmp.write_bytes(response.content)
        tmp.replace(target)
    return target


def _name_score(name, aliases):
    n = str(name).lower()
    return max((len(alias) for alias in aliases if alias in n), default=0)


def _find_name(names, aliases):
    ranked = [(_name_score(name, aliases), name) for name in names]
    ranked = [item for item in ranked if item[0]]
    return max(ranked)[1] if ranked else None


def _coord(ds, name):
    if name in ds.coords:
        return np.asarray(ds.coords[name].values)
    if name in ds.variables:
        return np.asarray(ds[name].values)
    return None


def _scalar(ds, aliases, attrs=()):
    name = _find_name(list(ds.variables) + list(ds.attrs), aliases)
    value = ds.attrs.get(name) if name in ds.attrs else (ds[name].values if name in ds.variables else None)
    if value is None:
        for key in attrs:
            if key in ds.attrs:
                value = ds.attrs[key]
                break
    try:
        return float(np.asarray(value).reshape(-1)[0])
    except Exception:
        return None


def _axis_name(ds, dims, aliases):
    candidates = []
    for dim in dims:
        score = _name_score(dim, aliases)
        if score:
            candidates.append((score + 2, dim))
    for name in ds.variables:
        if ds[name].ndim == 1 and ds[name].dims[0] in dims:
            score = _name_score(name, aliases)
            if score:
                candidates.append((score + 3, name))
    if not candidates:
        return None, None
    name = max(candidates)[1]
    return name, _coord(ds, name)


def _units_factor(units):
    u = str(units or "").lower().replace(" ", "")
    if u in ("km", "kilometer", "kilometers"):
        return 1000.0
    return 1.0


def _timestamp(path):
    match = FILE_RE.fullmatch(path.name)
    if not match:
        return dt.datetime.now(dt.timezone.utc)
    return dt.datetime.strptime(match.group("stamp"), "%Y%m%d%H%M").replace(tzinfo=dt.timezone.utc)


def _azimuth_edges(azimuths):
    az = np.mod(np.asarray(azimuths, dtype=float).reshape(-1), 360.0)
    if az.size < 1 or not np.all(np.isfinite(az)):
        raise ValueError("Azimutes inválidos")
    if az.size == 1:
        width = 1.0
        return np.array([(az[0] - width / 2) % 360]), np.array([(az[0] + width / 2) % 360]), width
    deltas = np.mod(np.roll(az, -1) - az, 360.0)
    good = deltas[(deltas > 0.001) & (deltas <= 10)]
    if not good.size:
        raise ValueError("Azimutes não formam uma varredura polar válida")
    width = float(np.median(good))
    return np.mod(az - width / 2, 360), np.mod(az + width / 2, 360), width


def _find_field(ds, product, azimuth_name, range_name):
    aliases = ("velocity", "vrad", "radial", "doppler") if product == "velocity" else ("dbzh", "reflectivity", "reflect", "dbz")
    choices = []
    for name, var in ds.data_vars.items():
        if not np.issubdtype(var.dtype, np.number) or azimuth_name not in var.dims or range_name not in var.dims:
            continue
        score = _name_score(name, aliases)
        if score:
            choices.append((score, name))
    if not choices:
        raise LookupError("Campo polar compatível não encontrado")
    return max(choices)[1]


def convert_netcdf_to_odim(source, destination, product):
    source, destination = Path(source), Path(destination)
    with xr.open_dataset(source, decode_times=False, mask_and_scale=True) as ds:
        dims = list(ds.dims)
        elevation_name, elevations = _axis_name(ds, dims, ("elevation", "elev", "tilt", "sweep"))
        azimuth_name, azimuths = _axis_name(ds, dims, ("azimuth", "azi", "bearing"))
        range_name, ranges = _axis_name(ds, dims, ("range", "gate", "distance"))
        if not elevation_name or not azimuth_name or not range_name:
            raise ValueError("CPTEC NetCDF não expõe elevação + azimute + range; recusado para modo polar")
        elevations = np.asarray(elevations, dtype=float).reshape(-1)
        azimuths = np.asarray(azimuths, dtype=float).reshape(-1)
        ranges = np.asarray(ranges, dtype=float).reshape(-1)
        if not elevations.size or not azimuths.size or not ranges.size:
            raise ValueError("Coordenadas polares vazias")
        units = ds[range_name].attrs.get("units", "") if range_name in ds.variables else ""
        ranges_m = ranges * _units_factor(units)
        gate_size = float(np.median(np.diff(ranges_m))) if ranges_m.size > 1 else 250.0
        if not np.isfinite(gate_size) or gate_size <= 0:
            raise ValueError("Espaçamento de gates inválido")
        range_start = max(0.0, float(ranges_m[0] - gate_size / 2))
        startaz, stopaz, _ = _azimuth_edges(azimuths)
        variable = _find_field(ds, product, azimuth_name, range_name)
        field = ds[variable]
        field_units = field.attrs.get("units", field.attrs.get("unit", ""))
        values_by_elevation = []
        for index in range(len(elevations)):
            da = field
            if elevation_name in da.dims:
                da = da.isel({elevation_name: index})
            for dim in list(da.dims):
                if da.sizes[dim] == 1 and dim not in (azimuth_name, range_name):
                    da = da.isel({dim: 0})
            if azimuth_name not in da.dims or range_name not in da.dims:
                raise ValueError("Campo não preserva azimute/range")
            values = np.asarray(da.transpose(azimuth_name, range_name).values, dtype=np.float32)
            if values.shape != (len(azimuths), len(ranges)):
                raise ValueError("Dimensões polares inconsistentes")
            if product == "velocity":
                unit = str(field_units).lower().replace(" ", "")
                if unit in ("km/h", "kmh"):
                    values = values / 3.6
                elif unit in ("kt", "kts", "knots"):
                    values = values * 0.5144444444
                elif unit not in ("", "m/s", "ms-1", "ms^-1", "m.s-1"):
                    raise ValueError(f"Unidade de velocidade não reconhecida: {field_units}")
            values_by_elevation.append(values)
        lat = _scalar(ds, ("latitude", "lat", "radar_lat"), ("latitude", "lat"))
        lon = _scalar(ds, ("longitude", "lon", "radar_lon"), ("longitude", "lon"))
        alt = _scalar(ds, ("altitude", "height", "radar_height", "elevation_m"), ("altitude", "height")) or 0.0
        if lat is None or lon is None:
            raise ValueError("Latitude/longitude do radar não encontradas")
        timestamp = _timestamp(source)
        destination.parent.mkdir(parents=True, exist_ok=True)
        tmp = destination.with_suffix(".tmp")
        with h5py.File(tmp, "w") as out:
            out["where"].attrs.update(lat=float(lat), lon=float(lon), height=float(alt))
            out["what"].attrs.update(object="PVOL", version="H5rad 2.4", date=timestamp.strftime("%Y%m%d"), time=timestamp.strftime("%H%M%S"), source="CPTEC/INPE")
            for index, elevation in enumerate(elevations):
                data = values_by_elevation[index]
                dataset = out.create_group(f"dataset{index + 1}")
                dataset["where"].attrs.update(nrays=len(azimuths), nbins=len(ranges), rscale=gate_size, rstart=range_start / 1000.0, elangle=float(elevation))
                dataset["what"].attrs.update(startdate=timestamp.strftime("%Y%m%d"), starttime=timestamp.strftime("%H%M%S"))
                dataset["how"].attrs.update(startazA=startaz.tolist(), stopazA=stopaz.tolist())
                valid = np.isfinite(data)
                encoded = np.full(data.shape, -32768, dtype=np.int16)
                encoded[valid] = np.rint(np.clip(data[valid], -327.67, 327.67) * 100).astype(np.int16)
                group = dataset.create_group("data1")
                group["what"].attrs.update(quantity="VRAD" if product == "velocity" else "DBZH", gain=0.01, offset=0.0, nodata=-32768, undetect=-32768, unit="m/s" if product == "velocity" else "dBZ")
                group.create_dataset("data", data=encoded, compression="gzip", compression_opts=4)
        tmp.replace(destination)
    return destination


def ensure_recent(root, max_files=24):
    """Download recent CPTEC volumes and convert only files that are truly polar."""
    root = Path(root)
    result = []
    for stamp, code, filename in list_files(max_files):
        directory = root / f"cptec-raw-{code}"
        directory.mkdir(parents=True, exist_ok=True)
        raw = directory / filename
        try:
            download_file(filename, directory)
            for product in ("reflectivity", "velocity"):
                target = directory / f"{raw.stem}_{product}.h5"
                if target.exists() and target.stat().st_mtime >= raw.stat().st_mtime:
                    continue
                try:
                    convert_netcdf_to_odim(raw, target, product)
                except LookupError:
                    target.unlink(missing_ok=True)
                except (ValueError, OSError, RuntimeError):
                    target.unlink(missing_ok=True)
            if any(directory.glob(f"{raw.stem}_*.h5")):
                result.append((f"cptec-raw-{code}", stamp, raw))
        except (requests.RequestException, ValueError, OSError):
            continue
    return result
