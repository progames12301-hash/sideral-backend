"""SIGMA's public menu/logs and official PNG+PGW products, cached server-side."""
import datetime as dt
import hashlib
import html
import io
import json
import math
import re
import threading
import time
import unicodedata
from urllib.parse import urlparse
import requests
import numpy as np
from PIL import Image

BASE='https://sigma.cptec.inpe.br'

def get(url, limit=8*1024*1024):
    parsed=urlparse(url)
    if parsed.scheme!='https' or not (parsed.hostname or '').endswith('.cptec.inpe.br'):
        raise ValueError('Fonte CPTEC inválida')
    with requests.get(url,timeout=(5,15),stream=True,allow_redirects=False) as response:
        response.raise_for_status()
        result=bytearray()
        for chunk in response.iter_content(65536):
            result.extend(chunk)
            if len(result)>limit: raise ValueError('Produto excessivo')
        return bytes(result)

def products_in_menu(item):
    if isinstance(item,list):
        for value in item: yield from products_in_menu(value)
    elif isinstance(item,dict):
        name=html.unescape(item.get('nome',''))
        if re.match(r'^(CAPPI|VENTO) ',name) and 'codigo' in item:
            yield item | {'nome':name}
        for value in item.values():
            if isinstance(value,(dict,list)): yield from products_in_menu(value)

def reproject_png(png, pgw):
    """CPTEC geographic worldfile, pixel CENTERS; nearest sampling into Mercator.

    Only axis-aligned lon/lat PGWs accepted. Do not assign approximate radar bounds.
    """
    a,d,b,e,c,f=map(float,pgw.split())
    if not all(math.isfinite(v) for v in (a,d,b,e,c,f)) or a<=0 or e>=0 or d!=0 or b!=0:
        raise ValueError('Georreferenciamento CPTEC não suportado')
    with Image.open(io.BytesIO(png)) as image:
        w,h=image.size
        if w*h>4000000: raise ValueError('Raster excessivo')
        pixels=np.asarray(image.convert('RGBA'))
    west,north=c-a/2,f-e/2;east,south=west+a*w,north+e*h
    if not(-180<=west<east<=180 and -85<south<north<85): raise ValueError('PGW não geográfico')
    if not np.any(pixels[:,:,3]==0):
        raise ValueError('Produto sem máscara transparente; não ocultar cores arbitrariamente')
    if not np.any(pixels[:,:,3]>0): raise ValueError('Imagem sem dados visíveis')
    merc=lambda lat: math.log(math.tan(math.pi/4+math.radians(lat)/2))
    ys=merc(north)+(np.arange(h)+.5)/h*(merc(south)-merc(north))
    lats=np.degrees(2*np.arctan(np.exp(ys))-math.pi/2)
    rows=np.clip(np.rint((lats-f)/e).astype(int),0,h-1)
    output=io.BytesIO();Image.fromarray(pixels[rows,:,:]).save(output,format='PNG')
    return output.getvalue(), [[west,north],[east,north],[east,south],[west,south]]

class Adapter:
    def __init__(self, cache):
        self.cache=cache;self.catalog=[];self.expires=0;self.lock=threading.Lock();self.probed={}

    def radars(self):
        with self.lock:
            if time.time()<self.expires: return self.catalog
            grouped={}
            try:
                for item in products_in_menu(json.loads(get(BASE+'/json/menu.json'))):
                    title=item['nome'].split(' ',1)[1];name,_,state=title.rpartition(' - ')
                    if not name: continue
                    slug=re.sub('[^a-z0-9]+','-',unicodedata.normalize('NFKD',name).encode('ascii','ignore').decode().lower()).strip('-')
                    radar=grouped.setdefault('cptec-'+slug,dict(id='cptec-'+slug,name=name,state=state,source='CPTEC/INPE — SIGMA',products=[],advertisedProducts=[],codes={}))
                    product='velocity' if item['nome'].startswith('VENTO ') else 'reflectivity'
                    radar['codes'][product]=str(item['codigo']);radar['advertisedProducts'].append(product)
                self.catalog=sorted(grouped.values(),key=lambda r:(r['id']!='cptec-chapeco',r['name']));self.expires=time.time()+3600
            except (requests.RequestException,ValueError):
                self.expires=time.time()+60
            return self.catalog

    def frames(self, radar, product):
        record=next((r for r in self.radars() if r['id']==radar),None)
        if not record or product not in record['codes']: return []
        with self.lock:
            cachekey=(radar,product);entry=self.probed.get(cachekey)
            if entry and time.time()<entry[0]: return entry[1]
            result=[]
            try:
                rows=json.loads(get(BASE+'/logs/'+record['codes'][product]+'/10'))
                for row in rows[:3]:
                    stamp=dt.datetime.fromisoformat(row['fileDate']+'T'+row['fileTime']).replace(tzinfo=dt.timezone.utc)
                    if (dt.datetime.now(dt.timezone.utc)-stamp).total_seconds()>48*3600: continue
                    url=row['url']
                    if not url.endswith('.png'): continue
                    key=hashlib.sha256(f'cptec|{radar}|{stamp.isoformat()}|{product}|mercator-v1'.encode()).hexdigest()
                    meta_path=self.cache/(key+'.json');image_path=self.cache/(key+'.png')
                    if not meta_path.exists() or not image_path.exists():
                        binary,coordinates=reproject_png(get(url),get(url[:-4]+'.pgw',4096).decode('utf-8'))
                        metadata=dict(frameId=key,radar=radar,source=record['source'],product=product,kind='raster',
                            timestamp=stamp.isoformat(),coordinates=coordinates,crs='EPSG:3857',sourceCrs='EPSG:4326',
                            longitude=(coordinates[0][0]+coordinates[1][0])/2,latitude=(coordinates[0][1]+coordinates[2][1])/2,
                            unit='source-palette',quantitative=False,sourceUrl=url,georeferenceUrl=url[:-4]+'.pgw',
                            dataUrl='/api/radar/v3/image?id='+key,
                            legendUrl=BASE+'/dsaimg/legendas/'+('legenda-vento.png' if product=='velocity' else 'legenda-cappi.png'))
                        tmp=image_path.with_suffix('.tmp');tmp.write_bytes(binary);tmp.replace(image_path)
                        tmp=meta_path.with_suffix('.tmp');tmp.write_text(json.dumps(metadata),encoding='utf-8');tmp.replace(meta_path)
                    result.append(json.loads(meta_path.read_text('utf-8')))
                if result and product not in record['products']: record['products'].append(product)
            except (requests.RequestException,ValueError,KeyError,OSError):
                result=entry[1] if entry else []
            # Retain already downloaded frames across refreshes and process restarts.
            by_id={frame['frameId']:frame for frame in result}
            for path in self.cache.glob('*.json'):
                try:
                    frame=json.loads(path.read_text('utf-8'))
                    if frame.get('radar')!=radar or frame.get('product')!=product or frame.get('kind')!='raster': continue
                    age=(dt.datetime.now(dt.timezone.utc)-dt.datetime.fromisoformat(frame['timestamp'])).total_seconds()
                    if 0<=age<=48*3600 and (self.cache/(frame['frameId']+'.png')).exists(): by_id[frame['frameId']]=frame
                except (ValueError,KeyError,OSError): continue
            result=sorted(by_id.values(),key=lambda x:x['timestamp'])
            if result and product not in record['products']: record['products'].append(product)
            if not result and product in record['products']: record['products'].remove(product)
            self.probed[cachekey]=(time.time()+120,result)
            return result
