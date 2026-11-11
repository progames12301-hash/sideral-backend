"""Run with python -m backend.radar_v3.server.

The legacy V3 API still exposes one lowest-elevation sweep per scan. The
volume endpoints preserve every physical ODIM sweep so Brasil Scope V5 can
request the real elevation stack for 3-D/vertical-section work.
"""
import gzip
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
from .odim import read_volume, read_volume_sweeps

LOG = logging.getLogger('BRASIL-SCOPE-V3')
SAFE = re.compile(r'^[a-z0-9_-]{1,64}$')
HEX64 = re.compile(r'^[a-f0-9]{64}$')


def _atomic_bytes(path, payload):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_bytes(payload)
    temporary.replace(path)


def _atomic_json(path, payload):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
    temporary.replace(path)


class Store:
    def __init__(self, root, cache, cptec=False):
        self.root, self.cache = Path(root).resolve(), Path(cache).resolve()
        self.cache.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.cptec = None
        if cptec:
            from .adapters.cptec import Adapter
            self.cptec = Adapter(self.cache)

    def prune_cache(self):
        files = sorted(
            (p for p in self.cache.iterdir() if re.fullmatch(r'[a-f0-9]{64}\.(?:bin\.gz|png)', p.name)),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        total = 0
        for path in files:
            stat = path.stat(); total += stat.st_size
            if time.time()-stat.st_mtime > 48*3600 or total > 256*1024*1024:
                path.unlink(missing_ok=True)
                path.with_name(path.name.split('.')[0]+'.json').unlink(missing_ok=True)
        for manifest_path in self.cache.glob('*.volume.json'):
            try:
                manifest = json.loads(manifest_path.read_text('utf-8'))
                too_old = time.time()-manifest_path.stat().st_mtime > 48*3600
                missing = any(not (self.cache/f"{s['frameId']}.bin.gz").exists() for s in manifest.get('sweeps', []))
                if too_old or missing:
                    manifest_path.unlink(missing_ok=True)
            except (OSError, ValueError, KeyError, TypeError):
                manifest_path.unlink(missing_ok=True)

    def radars(self):
        result = []
        if self.root.exists():
            for p in sorted(self.root.iterdir()):
                if p.is_dir() and SAFE.fullmatch(p.name) and any(p.glob('*.h5')):
                    available = [product for product in ('reflectivity', 'velocity') if self.frames(p.name, product)]
                    result.append(dict(id=p.name, name=p.name.replace('-', ' ').title(), source='Volume polar', products=available, volume=True))
        if self.cptec:
            result += [{k:v for k,v in r.items() if k != 'codes'} for r in self.cptec.radars()]
        return result

    def products(self, radar):
        available = []; latest = {}; volumetric = {}
        for product in ('reflectivity', 'velocity'):
            frames = self.frames(radar, product)
            if frames:
                available.append(product); latest[product] = frames[-1]['timestamp']
            directory = (self.root/radar).resolve() if SAFE.fullmatch(radar or '') else None
            volumetric[product] = bool(directory and directory.is_relative_to(self.root) and directory.exists() and any(directory.glob('*.h5')))
        return dict(radar=radar, products=available, latest=latest, volumetric=volumetric)

    def _directory(self, radar):
        if not SAFE.fullmatch(radar):
            raise ValueError('Radar inválido')
        directory = (self.root/radar).resolve()
        if not directory.is_relative_to(self.root):
            raise ValueError('Radar inválido')
        return directory

    def _provider(self, directory):
        provider = 'odim'
        manifest = directory/'radar.json'
        if manifest.exists():
            try:
                if json.loads(manifest.read_text('utf-8')).get('source') == 'CEMADEN':
                    provider = 'cemaden'
            except (OSError, ValueError, TypeError):
                pass
        return provider

    def frames(self, radar, product):
        if not SAFE.fullmatch(radar) or product not in ('reflectivity', 'velocity'):
            raise ValueError('Radar ou produto inválido')
        directory = self._directory(radar)
        if self.cptec and radar.startswith('cptec-') and not any(directory.glob('*.h5')):
            with self.lock:
                self.prune_cache()
            return self.cptec.frames(radar, product)

        provider = self._provider(directory)
        reader = read_volume
        if provider == 'cemaden':
            from .adapters.cemaden import read
            reader = read

        frames = []
        with self.lock:
            self.prune_cache()
            files = sorted(directory.glob('*.h5'), key=lambda p:p.stat().st_mtime, reverse=True)[:576]
            for path in files:
                stat = path.stat()
                key = hashlib.sha256(f'{provider}-v2:{radar}:{path}:{stat.st_size}:{stat.st_mtime_ns}:{product}'.encode()).hexdigest()
                meta_path, binary_path = self.cache/f'{key}.json', self.cache/f'{key}.bin.gz'
                if meta_path.exists() and binary_path.exists():
                    metadata = json.loads(meta_path.read_text('utf-8'))
                else:
                    try:
                        metadata, binary = reader(path, radar, product)
                    except LookupError:
                        continue
                    except (ValueError, KeyError, OSError):
                        LOG.warning('[RADAR-ERROR] Volume rejeitado: %s', path.name)
                        continue
                    metadata['frameId'] = key
                    _atomic_bytes(binary_path, gzip.compress(binary, compresslevel=5))
                    _atomic_json(meta_path, metadata)
                    LOG.info('[RADAR-CACHE] %s %s %s', radar, product, metadata['timestamp'])
                frames.append(metadata)
        if not frames and self.cptec and radar.startswith('cptec-'):
            return self.cptec.frames(radar, product)
        return sorted(frames, key=lambda m:m['timestamp'])

    def _volume_cache_id(self, provider, radar, path, stat, product):
        return hashlib.sha256(
            f'{provider}-volume-v1:{radar}:{path}:{stat.st_size}:{stat.st_mtime_ns}:{product}'.encode()
        ).hexdigest()

    def _build_volume(self, path, radar, product, provider):
        stat = path.stat()
        volume_id = self._volume_cache_id(provider, radar, path, stat, product)
        manifest_path = self.cache/f'{volume_id}.volume.json'
        if manifest_path.exists():
            try:
                manifest = json.loads(manifest_path.read_text('utf-8'))
                if manifest.get('volumeId') == volume_id and manifest.get('sweeps') and all(
                    (self.cache/f"{sweep['frameId']}.bin.gz").exists() and (self.cache/f"{sweep['frameId']}.json").exists()
                    for sweep in manifest['sweeps']
                ):
                    return manifest
            except (OSError, ValueError, KeyError, TypeError):
                pass

        decoded = read_volume_sweeps(path, radar, product)
        if provider == 'cemaden':
            for metadata, _binary in decoded:
                metadata['source'] = 'CEMADEN'
        if not decoded:
            raise LookupError('Nenhuma elevação válida neste volume.')

        timestamp = min(metadata['timestamp'] for metadata, _binary in decoded)
        sweep_summaries = []
        for index, (metadata, binary) in enumerate(decoded):
            sweep_id = hashlib.sha256(
                f"{volume_id}:{index}:{metadata.get('sweep','')}:{metadata['elevation']}".encode()
            ).hexdigest()
            metadata = dict(metadata)
            metadata.update(
                frameId=sweep_id,
                volumeId=volume_id,
                volumeTimestamp=timestamp,
                sweepIndex=index,
                sweepCount=len(decoded),
                dataUrl='/api/radar/v3/data?id='+sweep_id,
            )
            binary_path = self.cache/f'{sweep_id}.bin.gz'
            meta_path = self.cache/f'{sweep_id}.json'
            _atomic_bytes(binary_path, gzip.compress(binary, compresslevel=5))
            _atomic_json(meta_path, metadata)
            sweep_summaries.append({
                'frameId': sweep_id,
                'sweepIndex': index,
                'elevation': metadata['elevation'],
                'rayCount': metadata['rayCount'],
                'gateCount': metadata['gateCount'],
                'gateSize': metadata['gateSize'],
                'rangeStart': metadata['rangeStart'],
                'maxRange': metadata['maxRange'],
                'rayResolution': metadata['rayResolution'],
                'quantity': metadata['quantity'],
                'timestamp': metadata['timestamp'],
                'dataUrl': metadata['dataUrl'],
                'metadataUrl': '/api/radar/v3/metadata?id='+sweep_id,
            })

        first = decoded[0][0]
        manifest = {
            'volumeId': volume_id,
            'kind': 'volume',
            'radar': radar,
            'product': product,
            'source': 'CEMADEN' if provider == 'cemaden' else first.get('source', 'ODIM HDF5'),
            'timestamp': timestamp,
            'latitude': first['latitude'],
            'longitude': first['longitude'],
            'altitude': first['altitude'],
            'sweepCount': len(sweep_summaries),
            'elevations': [sweep['elevation'] for sweep in sweep_summaries],
            'sweeps': sweep_summaries,
            'volumeUrl': '/api/radar/v3/volume?id='+volume_id,
        }
        _atomic_json(manifest_path, manifest)
        LOG.info('[RADAR-VOLUME] %s %s %s · %d sweeps', radar, product, timestamp, len(sweep_summaries))
        return manifest

    def volumes(self, radar, product):
        if not SAFE.fullmatch(radar) or product not in ('reflectivity', 'velocity'):
            raise ValueError('Radar ou produto inválido')
        directory = self._directory(radar)
        if not directory.exists() or not any(directory.glob('*.h5')):
            return []
        provider = self._provider(directory)
        limit = max(1, min(576, int(os.environ.get('RADAR_V3_VOLUME_FILES', '96'))))
        result = []
        with self.lock:
            self.prune_cache()
            files = sorted(directory.glob('*.h5'), key=lambda p:p.stat().st_mtime, reverse=True)[:limit]
            for path in files:
                try:
                    result.append(self._build_volume(path, radar, product, provider))
                except LookupError:
                    continue
                except (ValueError, KeyError, OSError):
                    LOG.warning('[RADAR-ERROR] Volume multi-sweep rejeitado: %s', path.name)
        return sorted(result, key=lambda item:item['timestamp'])

    def volume(self, volume_id):
        if not HEX64.fullmatch(volume_id or ''):
            raise ValueError('Volume inválido')
        manifest_path = self.cache/f'{volume_id}.volume.json'
        if not manifest_path.exists():
            return None
        try:
            manifest = json.loads(manifest_path.read_text('utf-8'))
            detailed = dict(manifest)
            detailed_sweeps = []
            for summary in manifest.get('sweeps', []):
                frame_id = summary['frameId']
                meta_path = self.cache/f'{frame_id}.json'
                binary_path = self.cache/f'{frame_id}.bin.gz'
                if not meta_path.exists() or not binary_path.exists():
                    manifest_path.unlink(missing_ok=True)
                    return None
                metadata = json.loads(meta_path.read_text('utf-8'))
                metadata['dataUrl'] = '/api/radar/v3/data?id='+frame_id
                detailed_sweeps.append(metadata)
            detailed['sweeps'] = detailed_sweeps
            return detailed
        except (OSError, ValueError, KeyError, TypeError):
            manifest_path.unlink(missing_ok=True)
            return None

    def find_volume(self, radar, product, stamp=None):
        volumes = self.volumes(radar, product)
        if stamp:
            volumes = [volume for volume in volumes if volume.get('timestamp') == stamp]
        if not volumes:
            return None
        return self.volume(volumes[-1]['volumeId'])


def handler_for(store):
    class Handler(BaseHTTPRequestHandler):
        def end_headers(self):
            origin = self.headers.get('Origin', ''); parsed = urlparse(origin)
            allowed = set(os.environ.get('RADAR_V3_ORIGINS', 'https://sideralmeteorologiabrasil.web.app').split(','))
            if origin in allowed or (parsed.scheme == 'http' and parsed.hostname in ('localhost', '127.0.0.1')):
                self.send_header('Access-Control-Allow-Origin', origin)
                self.send_header('Vary', 'Origin')
            self.send_header('X-Content-Type-Options', 'nosniff')
            super().end_headers()

        def reply(self, data, status=200):
            body = json.dumps(data, ensure_ascii=False).encode()
            self.send_response(status); self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Cache-Control', 'no-store'); self.send_header('Content-Length', str(len(body)))
            self.end_headers(); self.wfile.write(body)

        def do_OPTIONS(self):
            self.send_response(204); self.send_header('Access-Control-Allow-Methods', 'GET, OPTIONS')
            self.send_header('Access-Control-Allow-Headers', 'Content-Type'); self.end_headers()

        def do_GET(self):
            route = urlparse(self.path); query = parse_qs(route.query)
            try:
                if route.path in ('/health', '/api/radar/v3/health'):
                    return self.reply(dict(status='ok', service='Brasil Scope V3', volumeApi='multi-sweep-v1'))
                if route.path == '/api/radar/v3/radars':
                    return self.reply(dict(radars=store.radars()))
                if route.path == '/api/radar/v3/products':
                    return self.reply(store.products(query.get('radar', [''])[0]))
                if route.path == '/api/radar/v3/image':
                    key = query.get('id', [''])[0]
                    if not HEX64.fullmatch(key):
                        raise ValueError('Quadro inválido')
                    path = store.cache/(key+'.png')
                    if not path.exists():
                        return self.reply(dict(error='Quadro indisponível'), 404)
                    body = path.read_bytes(); self.send_response(200)
                    self.send_header('Content-Type', 'image/png'); self.send_header('Cache-Control', 'public, max-age=86400, immutable')
                    self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body); return
                if route.path == '/api/radar/v3/metadata' and 'id' in query:
                    key = query['id'][0]
                    if not HEX64.fullmatch(key):
                        raise ValueError('Quadro inválido')
                    path = store.cache/f'{key}.json'
                    if not path.exists():
                        return self.reply(dict(error='Quadro indisponível'), 404)
                    metadata = json.loads(path.read_text('utf-8'))
                    if metadata.get('kind') == 'raster':
                        metadata.pop('legendUrl', None)
                    if metadata.get('kind') != 'raster':
                        metadata['dataUrl'] = '/api/radar/v3/data?id='+key
                    return self.reply(metadata)
                if route.path == '/api/radar/v3/data':
                    key = query.get('id', [''])[0]
                    if not HEX64.fullmatch(key):
                        raise ValueError('Quadro inválido')
                    path = store.cache/f'{key}.bin.gz'
                    if not path.exists():
                        return self.reply(dict(error='Quadro indisponível'), 404)
                    body = path.read_bytes(); self.send_response(200)
                    self.send_header('Content-Type', 'application/octet-stream'); self.send_header('Content-Encoding', 'gzip')
                    self.send_header('Cache-Control', 'public, max-age=86400, immutable')
                    self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body); return

                if route.path == '/api/radar/v3/volumes':
                    radar = query.get('radar', [''])[0]
                    product = query.get('product', ['reflectivity'])[0]
                    volumes = store.volumes(radar, product)
                    compact = []
                    for volume in volumes:
                        compact.append({k:v for k,v in volume.items() if k != 'sweeps'} | {
                            'sweeps': [{k:v for k,v in sweep.items() if k not in ('azimuthStart', 'azimuthEnd')} for sweep in volume.get('sweeps', [])]
                        })
                    return self.reply(dict(volumes=compact))
                if route.path == '/api/radar/v3/volume':
                    if 'id' in query:
                        volume = store.volume(query['id'][0])
                    else:
                        radar = query.get('radar', [''])[0]
                        product = query.get('product', ['reflectivity'])[0]
                        stamp = query.get('time', [None])[0]
                        volume = store.find_volume(radar, product, stamp)
                    if not volume:
                        return self.reply(dict(error='Volume multi-elevação indisponível para esta varredura.'), 404)
                    return self.reply(volume)

                if route.path not in ('/api/radar/v3/latest', '/api/radar/v3/metadata', '/api/radar/v3/frame', '/api/radar/v3/frames'):
                    return self.reply(dict(error='Rota não encontrada'), 404)
                radar, product = query.get('radar', [''])[0], query.get('product', ['reflectivity'])[0]
                frames = store.frames(radar, product)
                for frame in frames:
                    if frame.get('kind') != 'raster':
                        frame['dataUrl'] = '/api/radar/v3/data?id='+frame['frameId']
                if route.path.endswith('/frames'):
                    return self.reply(dict(frames=[{k:v for k,v in f.items() if k not in ('azimuthStart', 'azimuthEnd')} for f in frames]))
                stamp = query.get('time', [None])[0]
                if stamp:
                    frames = [f for f in frames if f['timestamp'] == stamp]
                if not frames:
                    return self.reply(dict(error='Velocidade Doppler indisponível para esta varredura.' if product == 'velocity' else 'Refletividade indisponível para esta varredura.'), 404)
                return self.reply(frames[-1])
            except ValueError as error:
                self.reply(dict(error=str(error)), 400)
            except Exception:
                LOG.exception('[RADAR-ERROR] Falha na consulta'); self.reply(dict(error='Radar temporariamente indisponível'), 503)

        def log_message(self, fmt, *args):
            LOG.info('[RADAR-METADATA] '+fmt, *args)
    return Handler


def main():
    logging.basicConfig(level=logging.INFO)
    store = Store(os.environ.get('RADAR_V3_INPUT', 'radar_v3_data'), os.environ.get('RADAR_V3_CACHE', 'radar_v3_cache'), cptec=True)
    feeds = os.environ.get('RADAR_V3_FEEDS')
    if feeds:
        from .ingest import poll_feeds
        threading.Thread(target=poll_feeds, args=(store.root, feeds), daemon=True).start()
    server = ThreadingHTTPServer((os.environ.get('HOST', '127.0.0.1'), int(os.environ.get('PORT', '8773'))), handler_for(store))
    LOG.info('Brasil Scope V3 iniciado'); server.serve_forever()


if __name__ == '__main__':
    main()
