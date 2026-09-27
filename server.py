from __future__ import annotations
import json, os, re, subprocess, sys
from pathlib import Path
from urllib.parse import urlparse, parse_qs, quote
import server_legacy as legacy
import stations_sideral

ROOT = Path(__file__).resolve().parent
NATIVE_PROFILE_RUNNER = ROOT / 'tools' / 'skewt' / 'ecmwf_profile.py'
NATIVE_PROFILE_TIMEOUT = max(180, int(os.getenv('SKEWT_NATIVE_TIMEOUT', '420')))

METBR_REPO = 'progames12301-hash/sideral-backend'
METBR_RELEASES_API = f'https://api.github.com/repos/{METBR_REPO}/releases?per_page=20'
METBR_RELEASE_PREFIX = 'metbr-wrf-4km-checkpoint-'
METBR_CACHE_SECONDS = 60
_metbr_release_cache = {'expires': 0.0, 'release': None}

class Handler(legacy.Handler):
    def _cors(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, Accept, Range')
        self.send_header('Access-Control-Expose-Headers', 'Content-Length, Content-Range, Accept-Ranges, Content-Type')

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self._cors()
        self.send_header('Access-Control-Max-Age', '86400')
        self.end_headers()

    def send_json(self, code, payload):
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self._cors()
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        parsed_path = parsed.path
        if parsed_path in {'/api/estacoes/sideral', '/api/stations/sideral'}:
            try: stations_sideral.handle(self)
            except Exception as exc: self.send_json(502, {'status': False, 'error': 'Estações Sideral temporariamente indisponíveis.', 'details': f'{type(exc).__name__}: {exc}'})
            return
        if parsed_path == '/api/redemet/radar': self._redemet_radar(parse_qs(parsed.query)); return
        if parsed_path == '/api/skewt/profile': self._skewt_profile(parse_qs(parsed.query)); return
        if parsed_path == '/api/metbr/metadata': self._metbr_metadata(); return
        if parsed_path == '/api/metbr/wrfout': self._metbr_wrfout(parse_qs(parsed.query)); return
        super().do_GET()

    def do_POST(self) -> None:
        if urlparse(self.path).path == '/api/skewt/request': self._skewt_request(); return
        super().do_POST()

    def _redemet_radar(self, query):
        key = str(getattr(legacy, 'REDEMET_API_KEY', '') or os.environ.get('REDEMET_API_KEY', '')).strip()
        if not key: self.send_json(503, {'status': False, 'provider': 'REDEMET / DECEA', 'error': 'REDEMET_API_KEY não configurada no Render.'}); return
        product = str(query.get('product', ['03km'])[0]).strip().lower()
        if product not in {'03km','05km','07km','10km','maxcappi'}: self.send_json(400, {'status': False, 'error': 'Produto REDEMET inválido.'}); return
        try: anima=max(1,min(15,int(query.get('anima',['6'])[0])))
        except (TypeError,ValueError): self.send_json(400, {'status':False,'error':'Quantidade de quadros inválida.'}); return
        params={'api_key':key,'anima':str(anima)}
        for name in ('data','area'):
            value=str(query.get(name,[''])[0]).strip()
            if value: params[name]=value
        try:
            response=legacy.requests.get(f"{legacy.REDEMET_API_URL.rstrip('/')}/produtos/radar/{product}",params=params,headers={'X-Api-Key':key,'User-Agent':'SideralMeteorologia/2.0 (Render radar proxy)','Accept':'application/json'},timeout=35); response.raise_for_status(); payload=response.json()
            if not isinstance(payload,dict) or payload.get('status') is not True: raise RuntimeError(str(payload.get('message') if isinstance(payload,dict) else 'Resposta inválida da REDEMET'))
            data=payload.get('data')
            if isinstance(data,dict):
                radar=data.get('radar'); radar=data.get('data') if radar is None and isinstance(data.get('data'),list) else radar; normalized=dict(data); normalized['radar']=radar if isinstance(radar,list) else []
            elif isinstance(data,list): normalized={'radar':data}
            else: normalized={'radar':[]}
            if not isinstance(normalized.get('radar'),list) or not normalized['radar']: self.send_json(502,{'status':False,'provider':'REDEMET / DECEA','error':'A REDEMET respondeu sem quadros de radar.','data':normalized}); return
            normalized['product']=product; normalized['provider']='REDEMET / DECEA'; normalized['source']='https://api-redemet.decea.mil.br/produtos/radar/'+product
            self.send_json(200,{'status':True,'message':payload.get('message',200),'provider':'REDEMET / DECEA','data':normalized})
        except Exception as exc:
            self.send_json(502,{'status':False,'provider':'REDEMET / DECEA','error':'Falha ao consultar a API oficial da REDEMET.','details':str(exc).replace(key,'[REDACTED]')[:500]})

    def _metbr_release(self):
        import time
        now = time.time()
        if _metbr_release_cache['release'] is not None and _metbr_release_cache['expires'] > now:
            return _metbr_release_cache['release']
        response = legacy.requests.get(METBR_RELEASES_API, headers={'Accept':'application/vnd.github+json','User-Agent':'SideralMeteorologia/2.0 (Render METBR proxy)'}, timeout=20)
        response.raise_for_status()
        releases = response.json()
        candidates = [r for r in releases if isinstance(r,dict) and str(r.get('tag_name','')).startswith(METBR_RELEASE_PREFIX) and not r.get('draft')]
        if not candidates: raise RuntimeError('Nenhuma release METBR WRF 4 km encontrada.')
        candidates.sort(key=lambda r:str(r.get('created_at') or r.get('published_at') or ''), reverse=True)
        release=candidates[0]
        _metbr_release_cache.update({'release':release,'expires':now+METBR_CACHE_SECONDS})
        return release

    def _metbr_metadata(self):
        try:
            release=self._metbr_release()
            assets=[{'name':a.get('name'),'size':a.get('size',0),'updated_at':a.get('updated_at'),'download_url':f'/api/metbr/wrfout?name={quote(str(a.get("name")))}'} for a in (release.get('assets') or []) if re.match(r'^wrfout_d01_',str(a.get('name') or ''))]
            assets.sort(key=lambda a:a['name'])
            if not assets: raise RuntimeError(f'Nenhum wrfout_d01 publicado na release {release.get("tag_name")}.')
            self.send_json(200,{'status':True,'provider':'METBR','model':'WRF METBR','resolutionKm':4,'release':release.get('tag_name'),'releaseId':release.get('id'),'publishedAt':release.get('published_at'),'frames':[{'time':self._metbr_time_from_name(a['name']),'forecastHour':i,'file':a['name'],'downloadUrl':a['download_url']} for i,a in enumerate(assets)]})
        except Exception as exc:
            self.send_json(502,{'status':False,'provider':'METBR','error':'Falha ao localizar os WRFOUT publicados pelo METBR.','details':str(exc)[:1000]})

    @staticmethod
    def _metbr_time_from_name(name):
        match=re.match(r'^wrfout_d01_(\d{4}-\d{2}-\d{2})_(\d{2})[.:](\d{2})[.:](\d{2})$',name)
        return f'{match.group(1)}T{match.group(2)}:{match.group(3)}:{match.group(4)}Z' if match else None

    def _metbr_wrfout(self, query):
        name=str(query.get('name',[''])[0]).strip()
        if not re.match(r'^wrfout_d01_\d{4}-\d{2}-\d{2}_\d{2}[.:]\d{2}[.:]\d{2}$',name):
            self.send_json(400,{'status':False,'error':'Nome de WRFOUT inválido.'}); return
        try:
            release=self._metbr_release()
            asset=next((a for a in (release.get('assets') or []) if a.get('name')==name),None)
            if not asset: self.send_json(404,{'status':False,'error':f'WRFOUT não encontrado na release METBR atual: {name}'}); return
            download_url=asset.get('browser_download_url')
            if not download_url: raise RuntimeError('Asset METBR sem URL de download.')
            response=legacy.requests.get(download_url,headers={'User-Agent':'SideralMeteorologia/2.0 (Render METBR proxy)','Accept':'application/octet-stream'},timeout=120,stream=True)
            response.raise_for_status()
            self.send_response(200)
            self._cors()
            self.send_header('Content-Type','application/octet-stream')
            self.send_header('Content-Disposition',f'inline; filename="{name}"')
            self.send_header('Cache-Control','public, max-age=120')
            self.send_header('Accept-Ranges','bytes')
            if response.headers.get('Content-Length'): self.send_header('Content-Length',response.headers['Content-Length'])
            self.end_headers()
            for chunk in response.iter_content(chunk_size=1024*1024):
                if chunk: self.wfile.write(chunk)
        except Exception as exc:
            try: self.send_json(502,{'status':False,'provider':'METBR','error':'Falha ao baixar WRFOUT pelo proxy Render.','details':str(exc)[:1000]})
            except Exception: pass

    def _parse_skewt_args(self,query):
        lat=float(query.get('lat',[''])[0]); lon=float(query.get('lon',[''])[0]); fh=int(query.get('forecast_hour',['0'])[0]); cycle=str(query.get('cycle',[''])[0]).zfill(2) if query.get('cycle',[''])[0] else None
        if cycle and cycle not in {'00','06','12','18'}: raise ValueError('cycle deve ser 00, 06, 12 ou 18')
        if not (-34<=lat<=6 and -75<=lon<=-33): raise ValueError('Coordenada fora do domínio brasileiro.')
        if fh<0 or fh>240 or fh%3!=0: raise ValueError('Forecast deve ser múltiplo de 3 entre F000 e F240.')
        return lat,lon,fh,cycle

    def _run_openmeteo_profile(self,lat,lon,fh,cycle=None):
        if not NATIVE_PROFILE_RUNNER.exists(): raise RuntimeError('Provider Open-Meteo ECMWF não encontrado no backend.')
        cmd=[sys.executable,str(NATIVE_PROFILE_RUNNER),'--lat',str(lat),'--lon',str(lon),'--fh',str(fh)]
        if cycle: cmd += ['--cycle',cycle]
        proc=subprocess.run(cmd,cwd=str(ROOT),stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=NATIVE_PROFILE_TIMEOUT,check=False,env=dict(os.environ))
        if proc.returncode!=0: raise RuntimeError(proc.stdout[-5000:] or 'Provider Open-Meteo ECMWF retornou erro.')
        try: payload=json.loads(proc.stdout)
        except Exception as exc: raise RuntimeError('Provider Open-Meteo ECMWF retornou JSON inválido.') from exc
        payload.update({'provider':'Open-Meteo','provider_url':'https://open-meteo.com/','model':'ECMWF IFS 0.25°','renderer':'browser-canvas','browser_rendering':True,'sharpy':False})
        return payload

    def _skewt_profile(self,query):
        try:
            lat,lon,fh,cycle=self._parse_skewt_args(query); self.send_json(200,self._run_openmeteo_profile(lat,lon,fh,cycle))
        except subprocess.TimeoutExpired: self.send_json(504,{'status':False,'provider':'Open-Meteo','error':f'Open-Meteo ECMWF excedeu {NATIVE_PROFILE_TIMEOUT} s.'})
        except Exception as exc: self.send_json(502,{'status':False,'provider':'Open-Meteo','model':'ECMWF IFS 0.25°','error':'Falha ao obter perfil ECMWF via Open-Meteo.','details':str(exc)[:5000]})

    def _skewt_request(self):
        try:
            length=int(self.headers.get('Content-Length','0')); body=json.loads(self.rfile.read(length) or b'{}')
            lat,lon,fh,cycle=self._parse_skewt_args({'lat':[body['lat']],'lon':[body['lon']],'forecast_hour':[body.get('forecast_hour',0)],'cycle':[body.get('cycle','')]})
            payload=self._run_openmeteo_profile(lat,lon,fh,cycle); payload['label']=str(body.get('label') or 'Ponto selecionado')[:80]; self.send_json(200,payload)
        except subprocess.TimeoutExpired: self.send_json(504,{'status':False,'provider':'Open-Meteo','error':f'Open-Meteo ECMWF excedeu {NATIVE_PROFILE_TIMEOUT} s.'})
        except Exception as exc: self.send_json(502,{'status':False,'provider':'Open-Meteo','model':'ECMWF IFS 0.25°','error':'Falha ao obter perfil ECMWF via Open-Meteo.','details':str(exc)[:5000]})

def main()->None:
    legacy.Handler=Handler; legacy.main()
if __name__=='__main__': main()
