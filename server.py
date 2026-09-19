from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from urllib.parse import urlparse

import server_legacy as legacy
import stations_sideral

GITHUB_REPO = os.getenv('SIDERAL_GITHUB_REPO', 'progames12301-hash/sideral-backend')
GITHUB_TOKEN = os.getenv('SIDERAL_GITHUB_TOKEN') or os.getenv('GITHUB_TOKEN')


class Handler(legacy.Handler):
    """Sideral stations API + secure on-demand SHARPpy requests."""

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

    def _skewt_request(self) -> None:
        if not GITHUB_TOKEN:
            self.send_json(503, {
                'status': False,
                'error': 'SIDERAL_GITHUB_TOKEN não configurado no backend.'
            })
            return
        try:
            length = int(self.headers.get('Content-Length', '0'))
            body = json.loads(self.rfile.read(length) or b'{}')
            lat = float(body['lat'])
            lon = float(body['lon'])
            label = str(body.get('label') or 'Brasil')[:80]
        except Exception:
            self.send_json(400, {
                'status': False,
                'error': 'JSON inválido. Envie lat, lon e opcionalmente label.'
            })
            return

        # National Brazilian domain used by the Sideral map. The frontend also
        # uses the same bounds before sending a request.
        if not (-34 <= lat <= 6 and -75 <= lon <= -33):
            self.send_json(400, {
                'status': False,
                'error': 'Coordenada fora do domínio brasileiro.'
            })
            return

        payload = {
            'event_type': 'skewt_request',
            'client_payload': {'lat': lat, 'lon': lon, 'label': label},
        }
        req = urllib.request.Request(
            f'https://api.github.com/repos/{GITHUB_REPO}/dispatches',
            data=json.dumps(payload).encode(),
            headers={
                'Accept': 'application/vnd.github+json',
                'Authorization': f'Bearer {GITHUB_TOKEN}',
                'X-GitHub-Api-Version': '2022-11-28',
                'Content-Type': 'application/json',
                'User-Agent': 'Sideral-SkewT',
            },
            method='POST',
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as response:
                if response.status not in (200, 204):
                    raise RuntimeError(f'GitHub HTTP {response.status}')
        except urllib.error.HTTPError as exc:
            self.send_json(502, {
                'status': False,
                'error': 'GitHub recusou a solicitação.',
                'details': f'HTTP {exc.code}',
            })
            return
        except Exception as exc:
            self.send_json(502, {
                'status': False,
                'error': 'Não foi possível iniciar o GitHub Actions.',
                'details': str(exc),
            })
            return

        key = f'{lat:.2f}_{lon:.2f}'.replace('-', 'm').replace('.', 'p')
        self.send_json(202, {
            'status': True,
            'key': key,
            'message': 'GitHub Actions iniciado; o produto será publicado quando o SHARPpy terminar.',
        })


def main() -> None:
    legacy.Handler = Handler
    legacy.main()


if __name__ == '__main__':
    main()
