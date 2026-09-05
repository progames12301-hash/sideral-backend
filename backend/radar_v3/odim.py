"""ODIM H5 2.4 polar reader. Never derives velocity from reflectivity images."""
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
    if not values.size: return None
    return float(values.max() - values.min())

def text(value):
    return value.decode('utf-8') if isinstance(value, bytes) else str(value)

def read_volume(path, radar, product):
    import h5py
    if product not in PRODUCTS: raise ValueError('Produto inválido')
    with h5py.File(path, 'r') as volume:
        if 'where' not in volume or not all(k in volume['where'].attrs for k in ('lat','lon','height')):
            raise ValueError('Estrutura polar ODIM não reconhecida; inspecione o volume antes de importar.')
        where = volume['where'].attrs
        sweeps = []
        for key in volume:
            if not key.startswith('dataset'): continue
            sweep = volume[key]
            if 'where' not in sweep: continue
            for field in sweep:
                if not field.startswith('data') or 'what' not in sweep[field]: continue
                quantity = text(sweep[field]['what'].attrs.get('quantity', ''))
                if quantity in PRODUCTS[product]: sweeps.append((float(sweep['where'].attrs['elangle']), key, field))
        if not sweeps: raise LookupError('Velocidade Doppler indisponível para esta varredura.' if product == 'velocity' else 'Refletividade indisponível para esta varredura.')
        elevation, key, field = min(sweeps)
        sweep = volume[key]; geometry = sweep['where'].attrs
        dataset = sweep[field]['data']; attrs = sweep[field]['what'].attrs
        rays, gates = int(geometry['nrays']), int(geometry['nbins'])
        if dataset.shape != (rays, gates) or rays*gates > 2000000 or rays < 1 or gates < 1:
            raise ValueError('Dimensões polares inválidas ou volume excessivo')
        raw = dataset[:]
        valid = np.isfinite(raw) & (raw != attrs['nodata']) & (raw != attrs['undetect'])
        values = raw.astype(np.float32)*float(attrs['gain']) + float(attrs['offset'])
        if product == 'velocity':
            unit = text(attrs.get('unit', attrs.get('units', 'm/s'))).lower().strip()
            factors = {'m/s':1., 'ms-1':1., 'm s-1':1., 'kts':.5144444444, 'kt':.5144444444, 'knots':.5144444444, 'km/h':1/3.6}
            if unit not in factors: raise ValueError('Unidade de velocidade não reconhecida')
            values *= factors[unit]
        if product == 'reflectivity': valid &= values >= 5
        # ODIM VRAD uses positive away from the radar, in m/s.
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
        gate_size = float(geometry['rscale']); start_range = float(geometry['rstart'])*1000
        lat, lon, altitude = (float(where[k]) for k in ('lat','lon','height'))
        if not (-85 < lat < 85 and -180 <= lon <= 180 and gate_size > 0 and start_range >= 0 and np.isfinite(altitude)):
            raise ValueError('Georreferenciamento inválido')
        when = sweep['what'].attrs
        stamp = dt.datetime.strptime(text(when['startdate'])+text(when['starttime']), '%Y%m%d%H%M%S').replace(tzinfo=dt.timezone.utc)
        metadata = dict(radar=radar, latitude=lat, longitude=lon, altitude=altitude,
            elevation=elevation, rayCount=rays, gateCount=gates, gateSize=gate_size,
            rangeStart=start_range, maxRange=start_range+gates*gate_size,
            rayResolution=float(np.median(widths)), azimuthStart=starts.tolist(), azimuthEnd=stops.tolist(),
            product=product, unit='dBZ' if product=='reflectivity' else 'm/s', timestamp=stamp.isoformat(),
            encoding='int16-le', scale=.01, nodata=NODATA, quantity=text(attrs['quantity']),
            velocityConvention='positive-away', source='ODIM HDF5', sweep=key)
        metadata['kind'] = 'polar'
        metadata['dealiasedBySideral'] = False
        for source_attrs in (volume['how'].attrs if 'how' in volume else {}, how):
            for original, normalized in (('NI','nyquistVelocity'),('highprf','highPRF'),('lowprf','lowPRF'),('wavelength','wavelengthCm')):
                if original in source_attrs:
                    value = np.asarray(source_attrs[original], dtype=float)
                    if np.all(np.isfinite(value)): metadata[normalized] = value.tolist()
        return metadata, encoded.tobytes()
