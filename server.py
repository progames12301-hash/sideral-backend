from __future__ import annotations
import json, os, re, subprocess, sys, time, tarfile
from pathlib import Path
from urllib.parse import urlparse, parse_qs, quote
import requests
import server_legacy as legacy
import stations_sideral

ROOT = Path(__file__).resolve().parent
NATIVE_PROFILE_RUNNER = ROOT / 'tools' / 'skewt' / 'ecmwf_profile.py'
NATIVE_PROFILE_TIMEOUT = max(180, int(os.getenv('SKEWT_NATIVE_TIMEOUT', '420')))
METBR_REPO = 'progames12301-hash/sideral-backend'
METBR_TAGS_API = f'https://api.github.com/repos/{METBR_REPO}/git/refs/tags?per_page=100'
METBR_RELEASE_PREFIX = 'metbr-wrf-4km-checkpoint-'
_metbr_cache = {'expires': 0.0, 'release': None, 'asset': None}

def cors(h):
    h.send_header('Access-Control-Allow-Origin', '*')
    h.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
    h.send_header('Access-Control-Allow-Headers', 'Content-Type, Accept, Range, Origin')
    h.send_header('Access-Control-Expose-Headers', 'Content-Length, Content-Range, Accept-Ranges, Content-Type')

def send_json(h, status, payload):
    body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    h.send_response(status)
    h.send_header('Content-Type', 'application/json; charset=utf-8')
    cors(h)
    h.send_header('Cache-Control', 'no-store')
    h.send_header('Content-Length', str(len(body)))
    h.end_headers()
    h.wfile.write(body)

def metbr_release():
    now = time.time()
    if _metbr_cache['release'] is not None and _metbr_cache['expires'] > now:
        return _metbr_cache['release'], _metbr_cache['asset']

    r = requests.get(METBR_TAGS_API, headers={
        'Accept': 'application/vnd.github+json',
        'User-Agent': 'Sideral-METBR'
    }, timeout=20)
    r.raise_for_status()
    tags = r.json()
    candidates = []
    for item in tags:
        ref = str(item.get('ref', ''))
        tag = ref.removeprefix('refs/tags/')
        m = re.fullmatch(re.escape(METBR_RELEASE_PREFIX) + r'(\d+)', tag)
        if m:
            candidates.append((int(m.group(1)), tag))
    if not candidates:
        raise RuntimeError('Nenhuma tag METBR WRF 4 km encontrada.')

    _, tag = max(candidates, key=lambda x: x[0])
    rr = requests.get(
        f'https://api.github.com/repos/{METBR_REPO}/releases/tags/{quote(tag)}',
        headers={'Accept': 'application/vnd.github+json', 'User-Agent': 'Sideral-METBR'},
        timeout=20,
    )
    rr.raise_for_status()
    release = rr.json()
    assets = release.get('assets') or []
    asset = next((a for a in assets if str(a.get('name', '')).endswith(('.tar.gz', '.tgz'))), None)
    if asset is None:
        raise RuntimeError(f'Release {tag} não possui o arquivo checkpoint .tar.gz.')

    _metbr_cache.update({'release': release, 'asset': asset, 'expires': now + 60})
    return release, asset

def _wrf_basename(member_name):
    base = Path(member_name).name
    m = re.search(r'(wrfout_d01_\d{4}-\d{2}-\d{2}_\d{2}[:.]\d{2}[:.]\d{2})$', base)
    return m.group(1) if m else None

def _open_checkpoint_stream(asset):
    response = requests.get(
        asset['browser_download_url'],
        headers={'User-Agent': 'Sideral-METBR', 'Accept': 'application/octet-stream'},
        timeout=180,
        stream=True,
    )
    response.raise_for_status()
    return response, tarfile.open(fileobj=response.raw, mode='r|gz')

def _checkpoint_wrfouts(asset):
    response, archive = _open_checkpoint_stream(asset)
    names = []
    try:
        for member in archive:
            if not member.isfile():
                continue
            name = _wrf_basename(member.name)
            if name and name not in names:
                names.append(name)
    finally:
        archive.close()
        response.close()
    names.sort()
    return names

def metbr_time(name):
    m = re.match(r'^wrfout_d01_(\d{4}-\d{2}-\d{2})_(\d{2})[:.]?(\d{2})[:.]?(\d{2})$', name)
    return f'{m.group(1)}T{m.group(2)}:{m.group(3)}:{m.group(4)}Z' if m else None

def metbr_metadata(h):
    try:
        release, asset = metbr_release()
        names = _checkpoint_wrfouts(asset)
        if not names:
            raise RuntimeError(f'O checkpoint {asset.get("name")} não contém wrfout_d01.')
        frames = [{
            'time': metbr_time(name),
            'forecastHour': i,
            'file': name,
            'downloadUrl': '/api/metbr/wrfout?name=' + quote(name),
        } for i, name in enumerate(names)]
        send_json(h, 200, {
            'status': True,
            'provider': 'METBR',
            'model': 'WRF METBR',
            'resolutionKm': 4,
            'release': release.get('tag_name'),
            'publishedAt': release.get('published_at'),
            'checkpointAsset': asset.get('name'),
            'frames': frames,
        })
    except Exception as e:
        send_json(h, 502, {
            'status': False,
            'provider': 'METBR',
            'error': 'Falha ao localizar WRFOUT no checkpoint METBR.',
            'details': str(e)[:1000],
        })

def metbr_wrfout(h, q):
    name = str(q.get('name', [''])[0]).strip()
    if not re.match(r'^wrfout_d01_\d{4}-\d{2}-\d{2}_\d{2}[:.]\d{2}[:.]\d{2}$', name):
        return send_json(h, 400, {'status': False, 'error': 'Nome de WRFOUT inválido.'})
    try:
        release, asset = metbr_release()
        response, archive = _open_checkpoint_stream(asset)
        target = None
        for member in archive:
            if member.isfile() and _wrf_basename(member.name) == name:
                target = member
                break
        if target is None:
            archive.close(); response.close()
            return send_json(h, 404, {'status': False, 'error': f'WRFOUT não encontrado no checkpoint METBR: {name}', 'release': release.get('tag_name')})

        source = archive.extractfile(target)
        if source is None:
            archive.close(); response.close()
            return send_json(h, 502, {'status': False, 'error': 'Não foi possível abrir o WRFOUT dentro do checkpoint.'})

        h.send_response(200)
        cors(h)
        h.send_header('Content-Type', 'application/octet-stream')
        h.send_header('Content-Disposition', f'inline; filename="{name}"')
        h.send_header('Cache-Control', 'public, max-age=120')
        h.send_header('Accept-Ranges', 'none')
        h.send_header('Content-Length', str(target.size))
        h.end_headers()
        while True:
            chunk = source.read(1024 * 1024)
            if not chunk:
                break
            h.wfile.write(chunk)
        source.close(); archive.close(); response.close()
    except Exception as e:
        try:
            send_json(h, 502, {'status': False, 'provider': 'METBR', 'error': 'Falha ao extrair WRFOUT pelo Render.', 'details': str(e)[:1000]})
        except Exception:
            pass

class Handler(legacy.Handler):
    def do_OPTIONS(self):
        self.send_response(204)
        cors(self)
        self.send_header('Access-Control-Max-Age', '86400')
        self.end_headers()

    def do_GET(self):
        p = urlparse(self.path)
        if p.path == '/api/metbr/metadata':
            return metbr_metadata(self)
        if p.path == '/api/metbr/wrfout':
            return metbr_wrfout(self, parse_qs(p.query))
        if p.path in {'/api/estacoes/sideral', '/api/stations/sideral'}:
            try:
                return stations_sideral.handle(self)
            except Exception as e:
                return send_json(self, 502, {'status': False, 'error': str(e)[:500]})
        return super().do_GET()

    def do_POST(self):
        return super().do_POST()

def main():
    server = legacy.ThreadingHTTPServer((legacy.DEFAULT_HOST, legacy.DEFAULT_PORT), Handler)
    server.serve_forever()

if __name__ == '__main__':
    main()
