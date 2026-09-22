from __future__ import annotations
import json, os, subprocess, sys
from pathlib import Path
from urllib.parse import urlparse, parse_qs
import server_legacy as legacy
import stations_sideral

ROOT = Path(__file__).resolve().parent
NATIVE_PROFILE_RUNNER = ROOT / 'tools' / 'skewt' / 'ecmwf_profile.py'
NATIVE_PROFILE_TIMEOUT = max(180, int(os.getenv('SKEWT_NATIVE_TIMEOUT', '420')))

class Handler(legacy.Handler):
    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        parsed_path = parsed.path
        if parsed_path in {'/api/estacoes/sideral', '/api/stations/sideral'}:
            try: stations_sideral.handle(self)
            except Exception as exc: self.send_json(502, {'status': False, 'error': 'Estações Sideral temporariamente indisponíveis.', 'details': f'{type(exc).__name__}: {exc}'})
            return
        if parsed_path == '/api/redemet/radar': self._redemet_radar(parse_qs(parsed.query)); return
        if parsed_path == '/api/skewt/profile': self._skewt_profile(parse_qs(parsed.query)); return
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

    def _parse_skewt_args(self, query):
        lat=float(query.get('lat',[''])[0]); lon=float(query.get('lon',[''])[0]); fh=int(query.get('forecast_hour',['0'])[0]); cycle=str(query.get('cycle',[''])[0]).zfill(2) if query.get('cycle',[''])[0] else None
        if cycle and cycle not in {'00','06','12','18'}: raise ValueError('cycle deve ser 00, 06, 12 ou 18')
        if not (-34<=lat<=6 and -75<=lon<=-33): raise ValueError('Coordenada fora do domínio brasileiro.')
        if fh<0 or fh>240 or fh%3!=0: raise ValueError('Forecast deve ser múltiplo de 3 entre F000 e F240.')
        return lat,lon,fh,cycle

    def _run_openmeteo_profile(self, lat, lon, fh, cycle=None):
        if not NATIVE_PROFILE_RUNNER.exists(): raise RuntimeError('Provider Open-Meteo ECMWF não encontrado no backend.')
        cmd=[sys.executable,str(NATIVE_PROFILE_RUNNER),'--lat',str(lat),'--lon',str(lon),'--fh',str(fh)]
        if cycle: cmd += ['--cycle',cycle]
        proc=subprocess.run(cmd,cwd=str(ROOT),stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=NATIVE_PROFILE_TIMEOUT,check=False,env=dict(os.environ))
        if proc.returncode!=0: raise RuntimeError(proc.stdout[-5000:] or 'Provider Open-Meteo ECMWF retornou erro.')
        try: payload=json.loads(proc.stdout)
        except Exception as exc: raise RuntimeError('Provider Open-Meteo ECMWF retornou JSON inválido.') from exc
        payload.update({'provider':'Open-Meteo','provider_url':'https://open-meteo.com/','model':'ECMWF IFS 0.25°','renderer':'browser-canvas','browser_rendering':True,'sharpy':False})
        return payload

    def _skewt_profile(self, query):
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
