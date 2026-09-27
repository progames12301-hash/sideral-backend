from __future__ import annotations
import json, os
from pathlib import Path
from urllib.parse import urlparse
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
