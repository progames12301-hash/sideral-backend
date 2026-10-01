from __future__ import annotations
import json, os
from pathlib import Path
from urllib.parse import urlparse, parse_qs
import server_legacy as legacy
import stations_sideral

ROOT = Path(__file__).resolve().parent

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

class Handler(legacy.Handler):
    def do_OPTIONS(self):
        self.send_response(204)
        cors(self)
        self.send_header('Access-Control-Max-Age', '86400')
        self.end_headers()

    def do_GET(self):
        p = urlparse(self.path)
        # METBR não é mais processado no Render. O fluxo temporário publica
        # JSON em metbr-json-data e o HTML deve consumi-lo diretamente.
        if p.path in {'/api/metbr/metadata', '/api/metbr/wrfout'}:
            return send_json(self, 410, {
                'status': False,
                'provider': 'METBR',
                'error': 'METBR movido para a publicação JSON temporária; use a branch metbr-json-data.'
            })
        # REDEMET radar must be proxied directly by the Render backend.
        # Do not expose the REDEMET credential to the browser.
        if p.path == '/api/redemet/radar':
            return self._redemet_radar(parse_qs(p.query))
        if p.path in {'/api/estacoes/sideral', '/api/stations/sideral'}:
            try:
                return stations_sideral.handle(self)
            except Exception as e:
                return send_json(self, 502, {'status': False, 'error': str(e)[:500]})
        return super().do_GET()

    def _redemet_radar(self, query):
        key = str(getattr(legacy, 'REDEMET_API_KEY', '') or os.environ.get('REDEMET_API_KEY', '')).strip()
        if not key:
            return send_json(self, 503, {
                'status': False,
                'provider': 'REDEMET / DECEA',
                'error': 'REDEMET_API_KEY não configurada no Render.'
            })

        product = str(query.get('product', ['03km'])[0]).strip().lower()
        allowed = {'03km', '05km', '07km', '10km', 'maxcappi'}
        if product not in allowed:
            return send_json(self, 400, {
                'status': False,
                'error': 'Produto REDEMET inválido.'
            })

        try:
            anima = max(1, min(15, int(query.get('anima', ['6'])[0])))
        except (TypeError, ValueError):
            return send_json(self, 400, {
                'status': False,
                'error': 'Quantidade de quadros inválida.'
            })

        params = {'api_key': key, 'anima': str(anima)}
        for name in ('data', 'area'):
            value = str(query.get(name, [''])[0]).strip()
            if value:
                params[name] = value

        try:
            response = legacy.requests.get(
                f"{legacy.REDEMET_API_URL.rstrip('/')}/produtos/radar/{product}",
                params=params,
                headers={
                    'X-Api-Key': key,
                    'User-Agent': 'SideralMeteorologia/1.0 (Render radar proxy)',
                    'Accept': 'application/json',
                },
                timeout=35,
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict) or payload.get('status') is not True:
                message = payload.get('message') if isinstance(payload, dict) else 'Resposta inválida da REDEMET'
                raise RuntimeError(str(message))

            data = payload.get('data')
            if isinstance(data, dict):
                radar = data.get('radar')
                if radar is None:
                    radar = data.get('data') if isinstance(data.get('data'), list) else []
                normalized = dict(data)
                normalized['radar'] = radar if isinstance(radar, list) else []
            elif isinstance(data, list):
                normalized = {'radar': data}
            else:
                normalized = {'radar': []}

            frames = normalized.get('radar') or []
            if not isinstance(frames, list) or not frames:
                return send_json(self, 502, {
                    'status': False,
                    'provider': 'REDEMET / DECEA',
                    'error': 'A REDEMET respondeu sem quadros de radar.',
                    'data': normalized,
                })

            normalized['product'] = product
            normalized['provider'] = 'REDEMET / DECEA'
            normalized['source'] = f"{legacy.REDEMET_API_URL.rstrip('/')}/produtos/radar/{product}"
            return send_json(self, 200, {
                'status': True,
                'message': payload.get('message', 200),
                'provider': 'REDEMET / DECEA',
                'data': normalized,
            })
        except Exception as exc:
            cached = legacy.redemet_cached_radar_payload(product)
            if cached:
                return send_json(self, 200, cached)
            safe = str(exc).replace(key, '[REDACTED]')
            return send_json(self, 502, {
                'status': False,
                'provider': 'REDEMET / DECEA',
                'error': 'Falha ao consultar a API oficial da REDEMET.',
                'details': safe[:500],
            })

    def do_POST(self):
        return super().do_POST()

def main():
    server = legacy.ThreadingHTTPServer((legacy.DEFAULT_HOST, legacy.DEFAULT_PORT), Handler)
    server.serve_forever()

if __name__ == '__main__':
    main()
