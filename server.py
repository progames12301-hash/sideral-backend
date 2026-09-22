from __future__ import annotations
import base64,json,os,subprocess,sys,tempfile
from pathlib import Path
from urllib.parse import urlparse,parse_qs
import server_legacy as legacy
import stations_sideral
ROOT=Path(__file__).resolve().parent
SKEWT_RUNNER=ROOT/'tools'/'skewt'/'generate_sharppy_product_operational.py'
NATIVE_PROFILE_RUNNER=ROOT/'tools'/'skewt'/'ecmwf_profile.py'
CACHE_ROOT=Path(os.getenv('SKEWT_CACHE_DIR','/tmp/sideral-skewt-cache'));CACHE_ROOT.mkdir(parents=True,exist_ok=True)
SKEWT_TIMEOUT_SECONDS=max(300,int(os.getenv('SKEWT_TIMEOUT_SECONDS','600')))
NATIVE_PROFILE_TIMEOUT=max(180,int(os.getenv('SKEWT_NATIVE_TIMEOUT','420')))
class Handler(legacy.Handler):
    def do_GET(self)->None:
        parsed=urlparse(self.path); parsed_path=parsed.path
        if parsed_path in {'/api/estacoes/sideral','/api/stations/sideral'}:
            try: stations_sideral.handle(self)
            except Exception as exc: self.send_json(502,{'status':False,'error':'Estações Sideral temporariamente indisponíveis.','details':f'{type(exc).__name__}: {exc}'})
            return
        if parsed_path=='/api/redemet/radar': self._redemet_radar(parse_qs(parsed.query)); return
        if parsed_path=='/api/skewt/profile': self._skewt_profile(parse_qs(parsed.query)); return
        super().do_GET()
    def do_POST(self)->None:
        if urlparse(self.path).path=='/api/skewt/request': self._skewt_request(); return
        super().do_POST()
    def _redemet_radar(self,query):
        key=str(getattr(legacy,'REDEMET_API_KEY','') or os.environ.get('REDEMET_API_KEY','')).strip()
        if not key: self.send_json(503,{'status':False,'provider':'REDEMET / DECEA','error':'REDEMET_API_KEY não configurada no Render.'});return
        product=str(query.get('product',['03km'])[0]).strip().lower();allowed={'03km','05km','07km','10km','maxcappi'}
        if product not in allowed:self.send_json(400,{'status':False,'error':'Produto REDEMET inválido.'});return
        try: anima=max(1,min(15,int(query.get('anima',['6'])[0])))
        except (TypeError,ValueError): self.send_json(400,{'status':False,'error':'Quantidade de quadros inválida.'});return
        params={'api_key':key,'anima':str(anima)}
        for name in ('data','area'):
            value=str(query.get(name,[''])[0]).strip()
            if value:params[name]=value
        try:
            response=legacy.requests.get(f"{legacy.REDEMET_API_URL.rstrip('/')}/produtos/radar/{product}",params=params,headers={'X-Api-Key':key,'User-Agent':'SideralMeteorologia/1.0 (Render radar proxy)','Accept':'application/json'},timeout=35);response.raise_for_status();payload=response.json()
            if not isinstance(payload,dict) or payload.get('status') is not True:raise RuntimeError(str(payload.get('message') if isinstance(payload,dict) else 'Resposta inválida da REDEMET'))
            data=payload.get('data')
            if isinstance(data,dict):
                radar=data.get('radar'); radar=data.get('data') if radar is None and isinstance(data.get('data'),list) else radar;normalized=dict(data);normalized['radar']=radar if isinstance(radar,list) else []
            elif isinstance(data,list):normalized={'radar':data}
            else:normalized={'radar':[]}
            frames=normalized.get('radar',[])
            if not isinstance(frames,list) or not frames:self.send_json(502,{'status':False,'provider':'REDEMET / DECEA','error':'A REDEMET respondeu sem quadros de radar.','data':normalized});return
            normalized['product']=product;normalized['provider']='REDEMET / DECEA';normalized['source']='https://api-redemet.decea.mil.br/produtos/radar/'+product
            self.send_json(200,{'status':True,'message':payload.get('message',200),'provider':'REDEMET / DECEA','data':normalized})
        except Exception as exc:
            safe=str(exc).replace(key,'[REDACTED]');self.send_json(502,{'status':False,'provider':'REDEMET / DECEA','error':'Falha ao consultar a API oficial da REDEMET.','details':safe[:500]})
    def _skewt_profile(self,query):
        try:
            lat=float(query.get('lat',[''])[0]);lon=float(query.get('lon',[''])[0]);fh=int(query.get('forecast_hour',['0'])[0]);cycle=str(query.get('cycle',[''])[0]).zfill(2) if query.get('cycle',[''])[0] else None
            if cycle and cycle not in {'00','06','12','18'}:raise ValueError('cycle deve ser 00, 06, 12 ou 18')
        except Exception as exc:self.send_json(400,{'status':False,'error':f'Parâmetros inválidos: {exc}'});return
        if not(-34<=lat<=6 and -75<=lon<=-33):self.send_json(400,{'status':False,'error':'Coordenada fora do domínio brasileiro.'});return
        if fh<0 or fh>72 or fh%3!=0:self.send_json(400,{'status':False,'error':'Forecast deve ser múltiplo de 3 entre F000 e F072.'});return
        if not NATIVE_PROFILE_RUNNER.exists():self.send_json(500,{'status':False,'error':'Provider ECMWF nativo não encontrado no backend.'});return
        cmd=[sys.executable,str(NATIVE_PROFILE_RUNNER),'--lat',str(lat),'--lon',str(lon),'--fh',str(fh)]
        if cycle:cmd+=['--cycle',cycle]
        try:
            proc=subprocess.run(cmd,cwd=str(ROOT),stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=NATIVE_PROFILE_TIMEOUT,check=False,env=dict(os.environ))
        except subprocess.TimeoutExpired:self.send_json(504,{'status':False,'error':f'ECMWF excedeu {NATIVE_PROFILE_TIMEOUT} s.'});return
        if proc.returncode!=0:self.send_json(502,{'status':False,'error':'Falha ao obter perfil ECMWF nativo.','details':proc.stdout[-5000:]});return
        try:payload=json.loads(proc.stdout)
        except Exception:self.send_json(502,{'status':False,'error':'Provider ECMWF retornou JSON inválido.','details':proc.stdout[-3000:]});return
        self.send_json(200,payload)
    def _cache_key(self,lat,lon,fh,cycle):
        c=cycle or 'latest';return f'{lat:.2f}_{lon:.2f}_f{fh:03d}_{c}'.replace('-','m').replace('.','p')
    @staticmethod
    def _data_url(path):
        mime='image/png' if path.suffix.lower()=='.png' else 'image/gif';return f'data:{mime};base64,'+base64.b64encode(path.read_bytes()).decode('ascii')
    def _skewt_request(self):
        try:
            length=int(self.headers.get('Content-Length','0'));body=json.loads(self.rfile.read(length) or b'{}');lat=float(body['lat']);lon=float(body['lon']);fh=int(body.get('forecast_hour',0));label=str(body.get('label') or 'Ponto selecionado')[:80];cycle=body.get('cycle')
            if cycle is not None:
                cycle=str(cycle).zfill(2)
                if cycle not in {'00','06','12','18'}:raise ValueError('cycle deve ser 00, 06, 12 ou 18')
        except Exception as exc:self.send_json(400,{'status':False,'error':f'JSON inválido: {exc}'});return
        if not(-34<=lat<=6 and -75<=lon<=-33):self.send_json(400,{'status':False,'error':'Coordenada fora do domínio brasileiro.'});return
        if fh<0 or fh>72 or fh%3!=0:self.send_json(400,{'status':False,'error':'Forecast deve ser um horário ECMWF múltiplo de 3 entre F000 e F072.'});return
        if not SKEWT_RUNNER.exists():self.send_json(500,{'status':False,'error':'Renderer SHARPpy não encontrado no backend.'});return
        key=self._cache_key(lat,lon,fh,cycle);cached=CACHE_ROOT/key;required=[cached/'skewt.png',cached/'hodograph.png',cached/'full.png',cached/'variables.json']
        if all(p.is_file() and p.stat().st_size>0 for p in required):return self._send_product(cached,cached/'variables.json',True)
        with tempfile.TemporaryDirectory(prefix='sideral-skewt-') as tmp:
            out=Path(tmp);cmd=[sys.executable,str(SKEWT_RUNNER),'--lat',str(round(lat,2)),'--lon',str(round(lon,2)),'--label',label,'--out',str(out),'--hours',str(fh)]
            if cycle:cmd+=['--cycle',cycle]
            try:proc=subprocess.run(cmd,cwd=str(ROOT),stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,timeout=SKEWT_TIMEOUT_SECONDS,check=False,env={**os.environ,'QT_QPA_PLATFORM':'offscreen','QT_API':'pyqt5'})
            except subprocess.TimeoutExpired:self.send_json(504,{'status':False,'error':f'SHARPpy/ECMWF excedeu {SKEWT_TIMEOUT_SECONDS} s. Tente novamente.'});return
            detail=proc.stdout[-6000:] if proc.stdout else ''
            if proc.returncode!=0:self.send_json(502,{'status':False,'error':'Falha ao gerar Skew-T com SHARPpy.','details':detail});return
            frame=out/f'f{fh:03d}'
            if not all((frame/name).is_file() for name in ('skewt.png','hodograph.png','full.png','variables.json')):self.send_json(502,{'status':False,'error':'SHARPpy terminou sem produzir todos os componentes do produto.','details':detail});return
            cached.mkdir(parents=True,exist_ok=True)
            for name in ('skewt.png','hodograph.png','full.png','variables.json'):(cached/name).write_bytes((frame/name).read_bytes())
            return self._send_product(cached,cached/'variables.json',False)
    def _send_product(self,product_dir,variables_path,cached):
        try:meta=json.loads(variables_path.read_text(encoding='utf-8'))
        except Exception as exc:self.send_json(502,{'status':False,'error':f'JSON SHARPpy inválido: {exc}'});return
        self.send_json(200,{'status':True,'cached':cached,'renderer':'SHARPpy','browser_rendering':False,'meta':meta,'images':{'skewt':self._data_url(product_dir/'skewt.png'),'hodograph':self._data_url(product_dir/'hodograph.png'),'full':self._data_url(product_dir/'full.png')}})
def main()->None:
    legacy.Handler=Handler;legacy.main()
if __name__=='__main__':main()
