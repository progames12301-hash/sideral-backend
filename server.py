from __future__ import annotations
import json, os, re, subprocess, sys, time
from pathlib import Path
from urllib.parse import urlparse, parse_qs, quote
import requests
import server_legacy as legacy
import stations_sideral

ROOT = Path(__file__).resolve().parent
NATIVE_PROFILE_RUNNER = ROOT / 'tools' / 'skewt' / 'ecmwf_profile.py'
NATIVE_PROFILE_TIMEOUT = max(180, int(os.getenv('SKEWT_NATIVE_TIMEOUT', '420')))
METBR_REPO='progames12301-hash/sideral-backend'
METBR_RELEASES_API=f'https://api.github.com/repos/{METBR_REPO}/releases?per_page=20'
METBR_RELEASE_PREFIX='metbr-wrf-4km-checkpoint-'
_metbr_cache={'expires':0.0,'release':None}

def cors(h):
    h.send_header('Access-Control-Allow-Origin','*')
    h.send_header('Access-Control-Allow-Methods','GET, POST, OPTIONS')
    h.send_header('Access-Control-Allow-Headers','Content-Type, Accept, Range, Origin')
    h.send_header('Access-Control-Expose-Headers','Content-Length, Content-Range, Accept-Ranges, Content-Type')

def send_json(h,status,payload):
    body=json.dumps(payload,ensure_ascii=False).encode('utf-8')
    h.send_response(status); h.send_header('Content-Type','application/json; charset=utf-8'); cors(h)
    h.send_header('Cache-Control','no-store'); h.send_header('Content-Length',str(len(body))); h.end_headers(); h.wfile.write(body)

def metbr_release():
    now=time.time()
    if _metbr_cache['release'] is not None and _metbr_cache['expires']>now: return _metbr_cache['release']
    r=requests.get(METBR_RELEASES_API,headers={'Accept':'application/vnd.github+json','User-Agent':'Sideral-METBR'},timeout=20); r.raise_for_status()
    candidates=[x for x in r.json() if isinstance(x,dict) and str(x.get('tag_name','')).startswith(METBR_RELEASE_PREFIX) and not x.get('draft')]
    if not candidates: raise RuntimeError('Nenhuma release METBR WRF 4 km encontrada.')
    candidates.sort(key=lambda x:str(x.get('created_at') or x.get('published_at') or ''),reverse=True)
    _metbr_cache['release']=candidates[0]; _metbr_cache['expires']=now+60
    return candidates[0]

def metbr_time(name):
    m=re.match(r'^wrfout_d01_(\d{4}-\d{2}-\d{2})_(\d{2})[.:](\d{2})[.:](\d{2})$',name)
    return f'{m.group(1)}T{m.group(2)}:{m.group(3)}:{m.group(4)}Z' if m else None

def metbr_metadata(h):
    try:
        rel=metbr_release(); assets=sorted([a for a in rel.get('assets',[]) if re.match(r'^wrfout_d01_',str(a.get('name','')))],key=lambda a:a['name'])
        if not assets: raise RuntimeError('A release atual não contém wrfout_d01.')
        frames=[{'time':metbr_time(a['name']),'forecastHour':i,'file':a['name'],'downloadUrl':'/api/metbr/wrfout?name='+quote(a['name'])} for i,a in enumerate(assets)]
        send_json(h,200,{'status':True,'provider':'METBR','model':'WRF METBR','resolutionKm':4,'release':rel.get('tag_name'),'publishedAt':rel.get('published_at'),'frames':frames})
    except Exception as e: send_json(h,502,{'status':False,'provider':'METBR','error':'Falha ao localizar WRFOUT.','details':str(e)[:1000]})

def metbr_wrfout(h,q):
    name=str(q.get('name',[''])[0]).strip()
    if not re.match(r'^wrfout_d01_\d{4}-\d{2}-\d{2}_\d{2}[.:]\d{2}[.:]\d{2}$',name): return send_json(h,400,{'status':False,'error':'Nome de WRFOUT inválido.'})
    try:
        rel=metbr_release(); asset=next((a for a in rel.get('assets',[]) if a.get('name')==name),None)
        if not asset: return send_json(h,404,{'status':False,'error':f'WRFOUT não encontrado: {name}'})
        r=requests.get(asset['browser_download_url'],headers={'User-Agent':'Sideral-METBR','Accept':'application/octet-stream'},timeout=120,stream=True); r.raise_for_status()
        h.send_response(200); cors(h); h.send_header('Content-Type','application/octet-stream'); h.send_header('Content-Disposition',f'inline; filename="{name}"'); h.send_header('Cache-Control','public, max-age=120'); h.send_header('Accept-Ranges','bytes')
        if r.headers.get('Content-Length'): h.send_header('Content-Length',r.headers['Content-Length'])
        h.end_headers()
        for chunk in r.iter_content(1024*1024):
            if chunk: h.wfile.write(chunk)
    except Exception as e: send_json(h,502,{'status':False,'provider':'METBR','error':'Falha ao baixar WRFOUT pelo Render.','details':str(e)[:1000]})

class Handler(legacy.Handler):
    def do_OPTIONS(self):
        self.send_response(204); cors(self); self.send_header('Access-Control-Max-Age','86400'); self.end_headers()
    def do_GET(self):
        p=urlparse(self.path)
        if p.path=='/api/metbr/metadata': return metbr_metadata(self)
        if p.path=='/api/metbr/wrfout': return metbr_wrfout(self,parse_qs(p.query))
        if p.path in {'/api/estacoes/sideral','/api/stations/sideral'}:
            try: return stations_sideral.handle(self)
            except Exception as e: return send_json(self,502,{'status':False,'error':str(e)[:500]})
        return super().do_GET()
    def do_POST(self):
        return super().do_POST()

def main():
    server=legacy.ThreadingHTTPServer((legacy.DEFAULT_HOST,legacy.DEFAULT_PORT),Handler)
    server.serve_forever()

if __name__=='__main__': main()
