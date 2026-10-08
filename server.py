from __future__ import annotations
import json, os, io
from pathlib import Path
from urllib.parse import urlparse, parse_qs
import server_legacy as legacy
import stations_sideral
from PIL import Image, ImageFilter

ROOT = Path(__file__).resolve().parent

def cors(h):
    h.send_header('Access-Control-Allow-Origin', '*')
    h.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
    h.send_header('Access-Control-Allow-Headers', 'Content-Type, Accept, Range, Origin')
    h.send_header('Access-Control-Expose-Headers', 'Content-Length, Content-Range, Accept-Ranges, Content-Type')

def send_binary(h, status, payload, content_type='application/octet-stream', extra_headers=None):
    h.send_response(status)
    h.send_header('Content-Type', content_type)
    cors(h)
    h.send_header('Cache-Control', 'public, max-age=60')
    h.send_header('Content-Length', str(len(payload)))
    for k,v in (extra_headers or {}).items(): h.send_header(k, str(v))
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
        if p.path in {'/api/metbr/metadata', '/api/metbr/wrfout'}:
            return send_json(self, 410, {
                'status': False,
                'provider': 'METBR',
                'error': 'METBR movido para a publicação JSON temporária; use a branch metbr-json-data.'
            })
        if p.path == '/api/redemet/radar':
            return self._redemet_radar(parse_qs(p.query))
        if p.path == '/api/redemet/image':
            return self._redemet_image(parse_qs(p.query))
        if p.path == '/api/radar/superres':
            return self._radar_superres(parse_qs(p.query))
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
            return send_json(self, 400, {'status': False, 'provider': 'REDEMET / DECEA', 'error': 'URL de imagem REDEMET inválida.'})
        try:
            response = legacy.requests.get(raw_url, headers={'User-Agent': 'SideralMeteorologia/1.0 (REDEMET image proxy)', 'Accept': 'image/png,image/jpeg,*/*'}, timeout=35)
            response.raise_for_status()
            content_type = response.headers.get('Content-Type', 'image/png').split(';', 1)[0].strip().lower()
            if content_type not in ('image/png', 'image/jpeg', 'image/webp'): content_type = 'image/png'
            return send_binary(self, 200, response.content, content_type)
        except Exception as exc:
            return send_json(self, 502, {'status': False, 'provider': 'REDEMET / DECEA', 'error': 'Não foi possível obter a imagem oficial da REDEMET.', 'details': str(exc)[:500]})

    def _radar_superres(self, query):
        """Experimental PNG->SuperRes visualizer.

        This is deliberately an image-resolution enhancement, not recovery of
        lost radar variables. It never invents dBZ values or meteorological cells.
        The input is currently restricted to official REDEMET radar images.
        """
        raw_url = str(query.get('url', [''])[0]).strip()
        product = str(query.get('product', ['03km'])[0]).strip().lower()
        try: scale = max(1, min(8, int(query.get('scale', ['4'])[0])))
        except (TypeError, ValueError): scale = 4

        if raw_url:
            parsed = urlparse(raw_url)
            if parsed.scheme != 'https' or parsed.hostname != 'estatico-redemet.decea.mil.br' or not parsed.path.startswith('/radar/'):
                return send_json(self, 400, {'status': False, 'error': 'Apenas imagens oficiais da REDEMET são aceitas no teste SuperRes.'})
        else:
            key = str(os.environ.get('REDEMET_API_KEY', '') or getattr(legacy, 'REDEMET_API_KEY', '')).strip()
            if not key:
                return send_json(self, 503, {'status': False, 'error': 'REDEMET_API_KEY não configurada no Render.'})
            allowed = {'03km','05km','07km','10km','maxcappi'}
            if product not in allowed: return send_json(self, 400, {'status': False, 'error': 'Produto REDEMET inválido.'})
            try:
                upstream = f"{legacy.REDEMET_API_URL.rstrip('/')}/produtos/radar/{product}"
                response = legacy.requests.get(upstream, params={'api_key':key,'anima':'1'}, headers={'User-Agent':'SideralMeteorologia/1.0','Accept':'application/json'}, timeout=35)
                response.raise_for_status()
                payload = response.json()
                frames = (((payload.get('data') or {}).get('radar')) if isinstance(payload.get('data'),dict) else None) or []
                if not frames: raise RuntimeError('REDEMET respondeu sem quadro de radar.')
                frame = frames[0] if isinstance(frames[0],dict) else {}
                raw_url = frame.get('url') or frame.get('imagem') or frame.get('image') or frame.get('arquivo') or ''
                if not raw_url: raise RuntimeError('Quadro da REDEMET não contém URL de imagem.')
                if raw_url.startswith('/'): raw_url = 'https://estatico-redemet.decea.mil.br' + raw_url
                parsed=urlparse(raw_url)
                if parsed.hostname != 'estatico-redemet.decea.mil.br' or not parsed.path.startswith('/radar/'): raise RuntimeError('URL retornada pela REDEMET não é um arquivo de radar permitido.')
            except Exception as exc:
                return send_json(self, 502, {'status':False,'provider':'REDEMET / DECEA','error':'Falha ao obter o quadro para o SuperRes.','details':str(exc)[:500]})

        try:
            r = legacy.requests.get(raw_url, headers={'User-Agent':'SideralMeteorologia/1.0 (SuperRes test)','Accept':'image/png,image/jpeg,image/webp,*/*'}, timeout=35)
            r.raise_for_status()
            img = Image.open(io.BytesIO(r.content)).convert('RGBA')
            # Preserve transparent radar pixels. Lanczos is used only to resample
            # the existing raster; a tiny edge-preserving sharpen improves display.
            out = img.resize((img.width*scale, img.height*scale), Image.Resampling.LANCZOS)
            out = out.filter(ImageFilter.UnsharpMask(radius=1.0, percent=65, threshold=3))
            buf=io.BytesIO(); out.save(buf, format='PNG', optimize=True)
            return send_binary(self,200,buf.getvalue(),'image/png',{
                'X-Sideral-SuperRes':'png-resample',
                'X-Sideral-SuperRes-Scale':scale,
                'X-Sideral-Input-Width':img.width,
                'X-Sideral-Input-Height':img.height,
                'X-Sideral-Note':'Visual enhancement only; no meteorological values invented.'
            })
        except Exception as exc:
            return send_json(self,502,{'status':False,'error':'Falha ao processar PNG do radar.','details':str(exc)[:500]})

    def _redemet_radar(self, query):
        key = str(os.environ.get('REDEMET_API_KEY', '') or getattr(legacy, 'REDEMET_API_KEY', '')).strip()
        if not key: return send_json(self, 503, {'status': False, 'provider': 'REDEMET / DECEA', 'error': 'REDEMET_API_KEY não configurada no Render.'})
        product = str(query.get('product', ['03km'])[0]).strip().lower()
        allowed = {'03km', '05km', '07km', '10km', 'maxcappi'}
        if product not in allowed: return send_json(self, 400, {'status': False, 'provider': 'REDEMET / DECEA', 'error': 'Produto REDEMET inválido.'})
        try: anima = max(1, min(15, int(query.get('anima', ['6'])[0])))
        except (TypeError, ValueError): return send_json(self, 400, {'status': False, 'provider': 'REDEMET / DECEA', 'error': 'Quantidade de quadros inválida.'})
        params = {'api_key': key, 'anima': str(anima)}
        for name in ('data', 'area'):
            value = str(query.get(name, [''])[0]).strip()
            if value: params[name] = value

        upstream = f"{legacy.REDEMET_API_URL.rstrip('/')}/produtos/radar/{product}"
        headers = {
            'X-Api-Key': key,
            'User-Agent': 'SideralMeteorologia/1.0',
            'Accept': 'application/json',
        }

        # Uma única consulta longa à REDEMET. A implementação anterior fazia
        # duas consultas de 35 s, o que podia manter o endpoint do Render
        # ocupado por ~70 s e estourar o timeout do GitHub Actions.
        try:
            response = legacy.requests.get(
                upstream,
                params=params,
                headers=headers,
                timeout=(10, 55),
            )
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise RuntimeError('A REDEMET retornou JSON inválido.')
            if payload.get('status') not in (True, 1, 'true', '1'):
                raise RuntimeError(
                    str(payload.get('message') or payload.get('error') or 'Resposta sem status=True')
                )

            data = payload.get('data')
            normalized = dict(data) if isinstance(data, dict) else {
                'radar': data if isinstance(data, list) else []
            }
            radar = normalized.get('radar')
            normalized['radar'] = radar if isinstance(radar, list) else []
            if not normalized['radar']:
                raise RuntimeError('A REDEMET respondeu sem quadros de radar.')

            normalized['product'] = product
            normalized['provider'] = 'REDEMET / DECEA'
            normalized['source'] = upstream
            return send_json(
                self,
                200,
                {
                    'status': True,
                    'message': payload.get('message', 200),
                    'provider': 'REDEMET / DECEA',
                    'data': normalized,
                },
            )
        except Exception as exc:
            cached = legacy.redemet_cached_radar_payload(product)
            if cached:
                return send_json(self, 200, cached)
            safe = str(exc).replace(key, '[REDACTED]')
            return send_json(
                self,
                502,
                {
                    'status': False,
                    'provider': 'REDEMET / DECEA',
                    'error': 'Falha ao consultar a API oficial da REDEMET.',
                    'details': safe[:500],
                },
            )

    def do_POST(self): return super().do_POST()

def main():
    server = legacy.ThreadingHTTPServer((legacy.DEFAULT_HOST, legacy.DEFAULT_PORT), Handler)
    server.serve_forever()

if __name__ == '__main__': main()
