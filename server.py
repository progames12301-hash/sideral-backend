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

def send_binary(h, status, payload, content_type='application/octet-stream'):
    h.send_response(status)
    h.send_header('Content-Type', content_type)
    cors(h)
    h.send_header('Cache-Control', 'public, max-age=60')
    h.send_header('Content-Length', str(len(payload)))
    h.end_headers()
    h.wfile.write(payload)

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
        if p.path == '/api/redemet/image':
            return self._redemet_image(parse_qs(p.query))
        if p.path in {'/api/estacoes/sideral', '/api/stations/sideral'}:
            try:
                return stations_sideral.handle(self)
            except Exception as e:
                return send_json(self, 502, {'status': False, 'error': str(e)[:500]})
        return super().do_GET()

    def _redemet_image(self, query):
        raw_url = str(query.get('url', [''])[0]).strip()
        parsed = urlparse(raw_url)
        if parsed.scheme != 'https' or parsed.hostname != 'estatico-redemet.decea.mil.br' or not parsed.path.startswith('/radar/'):
            return send_json(self, 400, {
                'status': False,
                'provider': 'REDEMET / DECEA',
                'error': 'URL de imagem REDEMET inválida.'
            })
        try:
            response = legacy.requests.get(
                raw_url,
                headers={
                    'User-Agent': 'SideralMeteorologia/1.0 (REDEMET image proxy)',
                    'Accept': 'image/png,image/jpeg,*/*',
                },
                timeout=35,
            )
            response.raise_for_status()
            content_type = response.headers.get('Content-Type', 'image/png').split(';', 1)[0].strip().lower()
            if content_type not in ('image/png', 'image/jpeg', 'image/webp'):
                content_type = 'image/png'
            return send_binary(self, 200, response.content, content_type)
        except Exception as exc:
            return send_json(self, 502, {
                'status': False,
                'provider': 'REDEMET / DECEA',
                'error': 'Não foi possível obter a imagem oficial da REDEMET.',
                'details': str(exc)[:500],
            })

    def _redemet_radar(self, query):
        # A chave pode ter sido atualizada no Render depois do import de server_legacy.
        # Leia o ambiente a cada requisição para não depender do valor capturado no startup.
        key = str(
            os.environ.get('REDEMET_API_KEY', '')
            or getattr(legacy, 'REDEMET_API_KEY', '')
        ).strip()
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
                'provider': 'REDEMET / DECEA',
                'error': 'Produto REDEMET inválido.'
            })

        try:
            anima = max(1, min(15, int(query.get('anima', ['6'])[0])))
        except (TypeError, ValueError):
            return send_json(self, 400, {
                'status': False,
                'provider': 'REDEMET / DECEA',
                'error': 'Quantidade de quadros inválida.'
            })

        params = {'api_key': key, 'anima': str(anima)}
        for name in ('data', 'area'):
            value = str(query.get(name, [''])[0]).strip()
            if value:
                params[name] = value

        upstream = f"{legacy.REDEMET_API_URL.rstrip('/')}/produtos/radar/{product}"
        last_error = None

        # A documentação oficial usa api_key na query string. Fazemos essa forma
        # primeiro e tentamos também X-Api-Key para compatibilidade com gateways.
        for headers in (
            {
                'User-Agent': 'SideralMeteorologia/1.0',
                'Accept': 'application/json',
            },
            {
                'X-Api-Key': key,
                'User-Agent': 'SideralMeteorologia/1.0',
                'Accept': 'application/json',
            },
        ):
            try:
                response = legacy.requests.get(
                    upstream,
                    params=params,
                    headers=headers,
                    timeout=35,
                )
                response.raise_for_status()
                payload = response.json()

                if not isinstance(payload, dict):
                    raise RuntimeError('A REDEMET retornou JSON inválido.')

                # Não exigir formato rígido demais: a API oficial documenta data.radar,
                # mas instalações/versões podem variar sem alterar os registros.
                status_value = payload.get('status')
                if status_value not in (True, 1, 'true', '1'):
                    message = payload.get('message') or payload.get('error') or 'Resposta sem status=True'
                    raise RuntimeError(str(message))

                data = payload.get('data')
                if isinstance(data, dict):
                    radar = data.get('radar')
                    if radar is None and isinstance(data.get('data'), list):
                        radar = data.get('data')
                    normalized = dict(data)
                    normalized['radar'] = radar if isinstance(radar, list) else []
                elif isinstance(data, list):
                    normalized = {'radar': data}
                else:
                    normalized = {'radar': []}

                frames = normalized.get('radar') or []
                if not isinstance(frames, list) or not frames:
                    raise RuntimeError('A REDEMET respondeu sem quadros de radar.')

                normalized['product'] = product
                normalized['provider'] = 'REDEMET / DECEA'
                normalized['source'] = upstream
                return send_json(self, 200, {
                    'status': True,
                    'message': payload.get('message', 200),
                    'provider': 'REDEMET / DECEA',
                    'data': normalized,
                })
            except Exception as exc:
                last_error = exc

        cached = legacy.redemet_cached_radar_payload(product)
        if cached:
            return send_json(self, 200, cached)

        safe = str(last_error or 'erro desconhecido').replace(key, '[REDACTED]')
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
