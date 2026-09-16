"""ODIM H5 2.4 polar reader.

V3 keeps the legacy single-sweep reader for backwards compatibility.
V5 can request every physical sweep/elevation from the same volume without
inventing data from rendered CAPPI/PNG products.
"""
import datetime as dt
import numpy as np

PRODUCTS = {'reflectivity': ('DBZH', 'DBZV', 'TH', 'TV'), 'velocity': ('VRADH', 'VRADV', 'VRAD', 'VRADHC')}
NODATA = -32768


def beamHeight(distance, elevation, radarAltitude):
    earth = 6371000.0 * 4 / 3
    r = np.asarray(distance)
    return np.sqrt(r*r + earth*earth + 2*r*earth*np.sin(np.deg2rad(elevation))) - earth + radarAltitude


def radialVelocity(u, v, w, azimuth, elevation):
    """m/s; positive away, negative toward radar. Inputs must be colocated physical winds."""
    a, e = np.deg2rad(azimuth), np.deg2rad(elevation)
    return (u*np.sin(a) + v*np.cos(a))*np.cos(e) + w*np.sin(e)


def calculateVelocityDelta(values):
    values = np.asarray(values, dtype=float)
    values = values[np.isfinite(values)]
    if not values.size:
        return None
    return float(values.max() - values.min())


def text(value):
    return value.decode('utf-8') if isinstance(value, bytes) else str(value)


def _validate_root(volume):
    if 'where' not in volume or not all(k in volume['where'].attrs for k in ('lat', 'lon', 'height')):
        raise ValueError('Estrutura polar ODIM não reconhecida; inspecione o volume antes de importar.')


def _candidate_sweeps(volume, product):
    """Return one preferred field per physical dataset/sweep.

    Some ODIM files may contain both DBZH and TH (or more than one VRAD
    variant) in the same dataset. They are alternate quantities for the same
    elevation, not extra elevations, so only the highest-priority supported
    quantity is selected for each dataset.
    """
    if product not in PRODUCTS:
        raise ValueError('Produto inválido')
    priority = {quantity: index for index, quantity in enumerate(PRODUCTS[product])}
    result = []
    for key in volume:
        if not key.startswith('dataset'):
            continue
        sweep = volume[key]
        if 'where' not in sweep:
            continue
        try:
            elevation = float(sweep['where'].attrs['elangle'])
        except (KeyError, TypeError, ValueError):
            continue
        best = None
        for field in sweep:
            if not field.startswith('data') or 'what' not in sweep[field]:
                continue
            quantity = text(sweep[field]['what'].attrs.get('quantity', ''))
            if quantity not in priority:
                continue
            candidate = (priority[quantity], field, quantity)
            if best is None or candidate[0] < best[0]:
                best = candidate
        if best is not None:
            result.append((elevation, key, best[1], best[2]))
    result.sort(key=lambda item: (item[0], item[1], item[2]))
    return result


def _decode_sweep(volume, root_where, radar, product, elevation, key, field):
    sweep = volume[key]
    geometry = sweep['where'].attrs
    dataset = sweep[field]['data']
    attrs = sweep[field]['what'].attrs
    rays, gates = int(geometry['nrays']), int(geometry['nbins'])
    if dataset.shape != (rays, gates) or rays*gates > 2000000 or rays < 1 or gates < 1:
        raise ValueError('Dimensões polares inválidas ou volume excessivo')

    raw = dataset[:]
    valid = np.isfinite(raw) & (raw != attrs['nodata']) & (raw != attrs['undetect'])
    values = raw.astype(np.float32)*float(attrs['gain']) + float(attrs['offset'])
    if product == 'velocity':
        unit = text(attrs.get('unit', attrs.get('units', 'm/s'))).lower().strip()
        factors = {'m/s': 1., 'ms-1': 1., 'm s-1': 1., 'kts': .5144444444, 'kt': .5144444444,
                   'knots': .5144444444, 'km/h': 1/3.6}
        if unit not in factors:
            raise ValueError('Unidade de velocidade não reconhecida')
        values *= factors[unit]
    if product == 'reflectivity':
        valid &= values >= 5

    encoded = np.full(raw.shape, NODATA, dtype='<i2')
    valid &= np.isfinite(values) & (values >= -327.67) & (values <= 327.67)
    encoded[valid] = np.rint(values[valid]*100).astype('<i2')

    how = sweep['how'].attrs if 'how' in sweep else {}
    if 'startazA' not in how or 'stopazA' not in how:
        raise ValueError('O volume não informa os limites reais dos azimutes')
    starts = np.asarray(how['startazA'], dtype=float) % 360
    stops = np.asarray(how['stopazA'], dtype=float) % 360
    widths = (stops-starts) % 360
    if starts.shape != (rays,) or stops.shape != (rays,) or not np.all(np.isfinite(starts+stops)) or np.any(widths <= 0) or np.any(widths > 10):
        raise ValueError('Geometria angular inválida')

    gate_size = float(geometry['rscale'])
    start_range = float(geometry['rstart'])*1000
    lat, lon, altitude = (float(root_where[k]) for k in ('lat', 'lon', 'height'))
    if not (-85 < lat < 85 and -180 <= lon <= 180 and gate_size > 0 and start_range >= 0 and np.isfinite(altitude)):
        raise ValueError('Georreferenciamento inválido')

    when = sweep['what'].attrs
    stamp = dt.datetime.strptime(text(when['startdate'])+text(when['starttime']), '%Y%m%d%H%M%S').replace(tzinfo=dt.timezone.utc)
    metadata = dict(
        radar=radar, latitude=lat, longitude=lon, altitude=altitude,
        elevation=float(elevation), rayCount=rays, gateCount=gates, gateSize=gate_size,
        rangeStart=start_range, maxRange=start_range+gates*gate_size,
        rayResolution=float(np.median(widths)), azimuthStart=starts.tolist(), azimuthEnd=stops.tolist(),
        product=product, unit='dBZ' if product == 'reflectivity' else 'm/s', timestamp=stamp.isoformat(),
        encoding='int16-le', scale=.01, nodata=NODATA, quantity=text(attrs['quantity']),
        velocityConvention='positive-away', source='ODIM HDF5', sweep=key,
        kind='polar', dealiasedBySideral=False,
    )
    for source_attrs in (volume['how'].attrs if 'how' in volume else {}, how):
        for original, normalized in (('NI', 'nyquistVelocity'), ('highprf', 'highPRF'), ('lowprf', 'lowPRF'), ('wavelength', 'wavelengthCm')):
            if original in source_attrs:
                value = np.asarray(source_attrs[original], dtype=float)
                if np.all(np.isfinite(value)):
                    metadata[normalized] = value.tolist()
    return metadata, encoded.tobytes()


def read_volume_sweeps(path, radar, product):
    """Decode every supported sweep/elevation in one ODIM volume.

    Returns a list of ``(metadata, binary)`` tuples sorted by elevation. No
    vertical interpolation is performed here; the browser receives the real
    individual sweeps and can render 2-D PPI, a volumetric stack, or a vertical
    section from the same source volume.
    """
    import h5py
    if product not in PRODUCTS:
        raise ValueError('Produto inválido')
    with h5py.File(path, 'r') as volume:
        _validate_root(volume)
        root_where = volume['where'].attrs
        candidates = _candidate_sweeps(volume, product)
        if not candidates:
            raise LookupError('Velocidade Doppler indisponível para esta varredura.' if product == 'velocity' else 'Refletividade indisponível para esta varredura.')
        decoded = [_decode_sweep(volume, root_where, radar, product, elevation, key, field)
                   for elevation, key, field, _quantity in candidates]

    count = len(decoded)
    volume_timestamp = min(metadata['timestamp'] for metadata, _binary in decoded)
    for index, (metadata, _binary) in enumerate(decoded):
        metadata['sweepIndex'] = index
        metadata['sweepCount'] = count
        metadata['volumeTimestamp'] = volume_timestamp
    return decoded


def read_volume(path, radar, product):
    """Legacy V3 reader: decode only the lowest elevation sweep."""
    import h5py
    if product not in PRODUCTS:
        raise ValueError('Produto inválido')
    with h5py.File(path, 'r') as volume:
        _validate_root(volume)
        candidates = _candidate_sweeps(volume, product)
        if not candidates:
            raise LookupError('Velocidade Doppler indisponível para esta varredura.' if product == 'velocity' else 'Refletividade indisponível para esta varredura.')
        elevation, key, field, _quantity = min(candidates, key=lambda item: item[0])
        metadata, binary = _decode_sweep(volume, volume['where'].attrs, radar, product, elevation, key, field)
        metadata['sweepIndex'] = 0
        metadata['sweepCount'] = len(candidates)
        metadata['volumeTimestamp'] = metadata['timestamp']
        return metadata, binary
