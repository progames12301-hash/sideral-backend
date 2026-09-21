from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import server_legacy as legacy
import stations_sideral

ROOT = Path(__file__).resolve().parent
SKEWT_RUNNER = ROOT / 'tools' / 'skewt' / 'generate_sharppy_product_operational.py'
CACHE_ROOT = Path(os.getenv('SKEWT_CACHE_DIR', '/tmp/sideral-skewt-cache'))
CACHE_ROOT.mkdir(parents=True, exist_ok=True)


class Handler(legacy.Handler):
    """Sideral stations API + on-demand ECMWF/SHARPpy requests."""

    def do_GET(self) -> None:
        parsed_path = urlparse(self.path).path
        if parsed_path in {'/api/estacoes/sideral', '/api/stations/sideral'}:
            try:
                stations_sideral.handle(self)
            except Exception as exc:
                self.send_json(502, {
                    'status': False,
                    'error': 'Estações Sideral temporariamente indisponíveis.',
                    'details': f'{type(exc).__name__}: {exc}',
                })
            return
        super().do_GET()

    def do_POST(self) -> None:
        if urlparse(self.path).path == '/api/skewt/request':
            self._skewt_request()
            return
        super().do_POST()

    @staticmethod
    def _cache_key(lat: float, lon: float, fh: int, cycle: str | None) -> str:
        c = cycle or 'latest'
        return f'{lat:.2f}_{lon:.2f}_f{fh:03d}_{c}'.replace('-', 'm').replace('.', 'p')

    @staticmethod
    def _data_url(path: Path) -> str:
        mime = 'image/png' if path.suffix.lower() == '.png' else 'image/gif'
        return f'data:{mime};base64,' + base64.b64encode(path.read_bytes()).decode('ascii')

    def _skewt_request(self) -> None:
        try:
            length = int(self.headers.get('Content-Length', '0'))
            body = json.loads(self.rfile.read(length) or b'{}')
            lat = float(body['lat'])
            lon = float(body['lon'])
            fh = int(body.get('forecast_hour', 0))
            label = str(body.get('label') or 'Ponto selecionado')[:80]
            cycle = body.get('cycle')
            if cycle is not None:
                cycle = str(cycle).zfill(2)
                if cycle not in {'00', '06', '12', '18'}:
                    raise ValueError('cycle deve ser 00, 06, 12 ou 18')
        except Exception as exc:
            self.send_json(400, {'status': False, 'error': f'JSON inválido: {exc}'})
            return

        if not (-34 <= lat <= 6 and -75 <= lon <= -33):
            self.send_json(400, {
                'status': False,
                'error': 'Coordenada fora do domínio brasileiro.'
            })
            return
        if fh < 0 or fh > 72 or fh % 3 != 0:
            self.send_json(400, {
                'status': False,
                'error': 'Forecast deve ser um horário ECMWF múltiplo de 3 entre F000 e F072.'
            })
            return
        if not SKEWT_RUNNER.exists():
            self.send_json(500, {'status': False, 'error': 'Renderer SHARPpy não encontrado no backend.'})
            return

        key = self._cache_key(lat, lon, fh, cycle)
        cached = CACHE_ROOT / key
        required = [cached / 'skewt.png', cached / 'hodograph.png', cached / 'full.png', cached / 'variables.json']
        if all(p.is_file() and p.stat().st_size > 0 for p in required):
            return self._send_product(cached, cached / 'variables.json', cached=True)

        with tempfile.TemporaryDirectory(prefix='sideral-skewt-') as tmp:
            out = Path(tmp)
            cmd = [
                sys.executable,
                str(SKEWT_RUNNER),
                '--lat', str(round(lat, 2)),
                '--lon', str(round(lon, 2)),
                '--label', label,
                '--out', str(out),
                '--hours', str(fh),
            ]
            if cycle:
                cmd += ['--cycle', cycle]
            try:
                proc = subprocess.run(
                    cmd,
                    cwd=str(ROOT),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    timeout=180,
                    check=False,
                    env={**os.environ, 'QT_QPA_PLATFORM': 'offscreen', 'QT_API': 'pyside2'},
                )
            except subprocess.TimeoutExpired:
                self.send_json(504, {
                    'status': False,
                    'error': 'SHARPpy/ECMWF excedeu 180 s. Tente novamente.'
                })
                return

            detail = proc.stdout[-6000:] if proc.stdout else ''
            if proc.returncode != 0:
                print('[Sideral Skew-T] renderer failed:\n' + detail, file=sys.stderr, flush=True)
                self.send_json(502, {
                    'status': False,
                    'error': 'Falha ao gerar Skew-T com SHARPpy.',
                    'details': detail,
                })
                return

            frame = out / f'f{fh:03d}'
            if not all((frame / name).is_file() for name in ('skewt.png', 'hodograph.png', 'full.png', 'variables.json')):
                print('[Sideral Skew-T] renderer produced incomplete output:\n' + detail, file=sys.stderr, flush=True)
                self.send_json(502, {
                    'status': False,
                    'error': 'SHARPpy terminou sem produzir todos os componentes do produto.',
                    'details': detail,
                })
                return

            cached.mkdir(parents=True, exist_ok=True)
            for name in ('skewt.png', 'hodograph.png', 'full.png', 'variables.json'):
                (cached / name).write_bytes((frame / name).read_bytes())
            return self._send_product(cached, cached / 'variables.json', cached=False)

    def _send_product(self, product_dir: Path, variables_path: Path, cached: bool) -> None:
        try:
            meta = json.loads(variables_path.read_text(encoding='utf-8'))
        except Exception as exc:
            self.send_json(502, {'status': False, 'error': f'JSON SHARPpy inválido: {exc}'})
            return
        payload = {
            'status': True,
            'cached': cached,
            'renderer': 'SHARPpy',
            'browser_rendering': False,
            'meta': meta,
            'images': {
                'skewt': self._data_url(product_dir / 'skewt.png'),
                'hodograph': self._data_url(product_dir / 'hodograph.png'),
                'full': self._data_url(product_dir / 'full.png'),
            },
        }
        self.send_json(200, payload)


def main() -> None:
    legacy.Handler = Handler
    legacy.main()


if __name__ == '__main__':
    main()
