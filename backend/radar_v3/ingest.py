"""Import verified ODIM volumes; no guessed provider URLs or generated weather data."""
import argparse
import hashlib
import logging
import os
from pathlib import Path
import tempfile
import time
import json
from urllib.request import Request, urlopen
from urllib.parse import urlparse
from .odim import read_volume
from .server import SAFE

def ingest(radar, root, source=None, url=None, provider='odim'):
    if not SAFE.fullmatch(radar): raise ValueError('Identificador do radar inválido')
    root=Path(root).resolve();directory=root/radar;directory.mkdir(parents=True,exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=directory,suffix='.partial',delete=False) as temporary:
        staged=Path(temporary.name)
        try:
            if url:
                if urlparse(url).scheme!='https': raise ValueError('Use uma fonte HTTPS')
                headers={'User-Agent':'Sideral-BrasilScopeV3/1.0'}
                token=os.environ.get('RADAR_V3_SOURCE_TOKEN')
                if token: headers['Authorization']='Bearer '+token
                response=urlopen(Request(url,headers=headers),timeout=30)
            else: response=open(source,'rb')
            with response:
                count=0
                while chunk:=response.read(1024*1024):
                    count+=len(chunk)
                    if count>128*1024*1024: raise ValueError('Volume excede 128 MB')
                    temporary.write(chunk)
            temporary.flush()
        except Exception:
            temporary.close();staged.unlink(missing_ok=True);raise
    try:
        reader=read_volume
        if provider=='cemaden':
            from .adapters.cemaden import read
            reader=read
        try: metadata,_=reader(staged,radar,'reflectivity')
        except LookupError: metadata,_=reader(staged,radar,'velocity')
        digest=hashlib.sha256()
        with staged.open('rb') as stream:
            while chunk:=stream.read(1024*1024):digest.update(chunk)
        stamp=metadata['timestamp'].replace(':','').replace('+','_')
        destination=directory/f'{stamp}-{digest.hexdigest()[:12]}.h5'
        if not destination.exists():staged.replace(destination)
        if provider=='cemaden':
            manifest=directory/'radar.json';temporary=manifest.with_suffix('.tmp')
            temporary.write_text(json.dumps({'source':'CEMADEN'}),encoding='utf-8');temporary.replace(manifest)
        return destination
    finally: staged.unlink(missing_ok=True)

def poll_feeds(root, config):
    log=logging.getLogger('BRASIL-SCOPE-V3')
    # Only explicitly configured feeds; source URL and credentials stay server-side.
    while True:
        try:
            feeds=json.loads(Path(config).read_text('utf-8'))
            for radar, url in feeds.items():
                try:ingest(radar,root,url=url)
                except Exception:log.warning('[RADAR-ERROR] Fonte indisponível: %s',radar)
        except Exception:log.warning('[RADAR-ERROR] Configuração de fontes indisponível')
        time.sleep(max(60,int(os.environ.get('RADAR_V3_POLL_SECONDS','120'))))

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--radar',required=True)
    inputs=parser.add_mutually_exclusive_group(required=True);inputs.add_argument('--file');inputs.add_argument('--url')
    parser.add_argument('--provider',choices=['odim','cemaden'],default='odim')
    args=parser.parse_args()
    print(ingest(args.radar,os.environ.get('RADAR_V3_INPUT','radar_v3_data'),args.file,args.url,args.provider))
