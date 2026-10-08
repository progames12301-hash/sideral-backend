#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import re
from io import BytesIO
from pathlib import Path
from urllib.parse import quote, urljoin, urlparse

import numpy as np
import requests
from PIL import Image

BASE = "https://www.defesacivil.sc.gov.br/"
MONITORING = "https://www.defesacivil.sc.gov.br/categoria/monitoramento/"
JINA_PREFIX = "https://r.jina.ai/"
ALLORIGINS_PREFIX = "https://api.allorigins.win/raw?url="
CORSPROXY_PREFIX = "https://corsproxy.io/?url="
WRSRV_PREFIX = "https://wsrv.nl/?url="
SCHEMA = "sideral-sdcsc-chapeco-ppi-z-v1"

def text_clean(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(value or "")).strip()

def fetch(session, url, timeout=(15,60), image=False):
    headers={
        "User-Agent":"Sideral-SDC-SC-PPI-Z/1.2",
        "Accept":"image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8" if image
               else "text/html,application/xhtml+xml,*/*;q=0.8",
    }
    errors=[]

    # Primeiro tenta a fonte oficial diretamente.
    for attempt in range(3):
        try:
            r=session.get(url,timeout=timeout,headers=headers)
            r.raise_for_status()
            return r
        except requests.RequestException as exc:
            errors.append(f"direct:{type(exc).__name__}:{exc}")
            if attempt < 2:
                import time
                time.sleep(2*(attempt+1))

    # GitHub-hosted runners podem não alcançar a SDC diretamente.
    if image:
        fallbacks=[
            WRSRV_PREFIX + quote(url,safe=""),
            CORSPROXY_PREFIX + quote(url,safe=""),
        ]
    else:
        fallbacks=[
            ALLORIGINS_PREFIX + quote(url,safe=""),
            CORSPROXY_PREFIX + quote(url,safe=""),
            JINA_PREFIX + url,
        ]

    for fallback in fallbacks:
        try:
            r=session.get(fallback,timeout=(20,90),headers=headers)
            r.raise_for_status()
            return r
        except requests.RequestException as exc:
            errors.append(f"fallback:{fallback.split('?')[0]}:{type(exc).__name__}:{exc}")
            continue

    raise requests.RequestException(
        "Falha ao acessar a fonte SDC/SC após tentativas diretas e fallbacks: "
        + " | ".join(errors)[-1800:]
    )

def find_post_urls(index_html: str, base_url: str) -> list[str]:
    found=[]
    hrefs = re.findall(r"href=['\"]([^'\"]+)['\"]",index_html,re.I)
    hrefs += re.findall(r"\\]\((https?://[^)]+)\\)",index_html,re.I)
    for href in hrefs:
        u=urljoin(base_url,html.unescape(href))
        p=urlparse(u)
        if p.hostname != urlparse(base_url).hostname:
            continue
        if not p.path or "/categoria/" in p.path or "/tag/" in p.path or "/author/" in p.path:
            continue
        if re.search(r"/20\d{2}/\d{2}/\d{2}/",p.path) and u not in found:
            found.append(u)
    return found

def relevant_image(img):
    attrs=" ".join(img)
    low=attrs.lower()
    radar=("radar" in low or "mppi" in low or "reflectividade" in low or "dbz" in low)
    chapeco="chapec" in low
    return radar and (chapeco or "ppi" in low or "reflet" in low)

def extract_candidates(page_html: str, page_url: str):
    candidates=[]
    # img tags
    for match in re.findall(r"<img\b([^>]+)>",page_html,re.I|re.S):
        src_m=re.search(r"(?:src|data-src|data-lazy-src)=['\"]([^'\"]+)['\"]",match,re.I)
        if not src_m:
            continue
        src=urljoin(page_url,html.unescape(src_m.group(1)))
        attrs=text_clean(re.sub(r"<.*?>"," ",match))
        alt_m=re.search(r"alt=['\"]([^'\"]*)['\"]",match,re.I)
        alt=text_clean(alt_m.group(1)) if alt_m else ""
        candidates.append((src,alt+" "+attrs,relevant_image((match,alt))))
    # linked images
    for a in re.findall(r"<a\b([^>]+)>(.*?)</a>",page_html,re.I|re.S):
        img=re.search(r"(?:src|href)=['\"]([^'\"]+\.(?:png|jpe?g|webp))['\"]",a[1],re.I)
        if img:
            src=urljoin(page_url,html.unescape(img.group(1)))
            candidates.append((src,text_clean(re.sub(r"<.*?>"," ",a[1])),False))
    # Markdown images returned by text proxies such as Jina Reader.
    for alt,src0 in re.findall(r"!\[([^\]]*)\]\((https?://[^)]+)\)",page_html,re.I):
        src=urljoin(page_url,html.unescape(src0))
        candidates.append((src,text_clean(alt),False))
    # score using surrounding article text and image attributes
    return candidates

def choose_ppi_image(page_html: str, page_url: str):
    page_text=text_clean(re.sub(r"<script.*?</script>|<style.*?</style>"," ",page_html,flags=re.I|re.S))
    scores=[]
    for src,meta,strong in extract_candidates(page_html,page_url):
        if urlparse(src).scheme not in {"https","http"}:
            continue
        low=(meta+" "+page_text[:12000]).lower()
        score=0
        if "chapec" in low: score+=50
        if "radar meteorol" in low or "radar meteorológico" in low: score+=35
        if "mppi" in low: score+=30
        if "refletividade" in low: score+=30
        if "dbz" in low: score+=25
        if "velocidade" in low or "mppi-v" in low: score-=35
        if strong: score+=20
        if re.search(r"radar|mppi|reflect|dbz|chapec",src.lower()): score+=10
        scores.append((score,src,meta))
    if not scores:
        return None
    scores.sort(key=lambda x:(-x[0],x[1]))
    return scores[0]

def get_latest_ppi(session, category_url):
    r=fetch(session,category_url)
    urls=find_post_urls(r.text,category_url)
    # Search several recent posts because the newest post is not necessarily a radar post.
    best=None
    for post_url in urls[:20]:
        try:
            pr=fetch(session,post_url)
            low=pr.text.lower()
            if "chapec" not in low or ("radar" not in low and "mppi" not in low):
                continue
            candidate=choose_ppi_image(pr.text,post_url)
            if candidate:
                score,src,meta=candidate
                if best is None or score>best[0]:
                    best=(score,post_url,src,meta,pr.text)
        except requests.RequestException:
            continue
    if best is None:
        raise RuntimeError("Não foi encontrada publicação recente com PPI/MPPI de Chapecó.")
    return best

def radar_echo_rgba(image):
    rgba=np.asarray(image.convert("RGBA"),dtype=np.uint8)
    rgb=rgba[...,:3].astype(np.int16)
    hi=rgb.max(axis=2); lo=rgb.min(axis=2); sat=hi-lo
    # SC/Defesa Civil radar palettes commonly use cyan/blue/green/yellow/orange/red.
    palette=np.array([
        [0,220,255],[0,120,255],[0,255,255],[0,255,0],[80,255,0],
        [255,255,0],[255,180,0],[255,100,0],[255,0,0],[190,0,0],[255,0,255]
    ],dtype=np.int16)
    d=((rgb[...,None,:]-palette[None,None,:,:])**2).sum(axis=3)
    mask=(d.min(axis=2)<19000)&(hi-lo>25)&(hi>70)
    # Avoid obvious white/grey map, labels and black frame.
    mask &= ~((hi>235)&(sat<22))
    mask &= ~(hi<25)
    out=rgba.copy()
    out[...,3]=np.where(mask, np.minimum(255,rgba[...,3].astype(np.int16)),0).astype(np.uint8)
    return Image.fromarray(out,"RGBA"),mask

def make_uhd(img,width=3840,height=2160):
    scale=min(width/img.width,height/img.height)
    w=max(1,round(img.width*scale)); h=max(1,round(img.height*scale))
    fit=img.resize((w,h),Image.Resampling.LANCZOS)
    out=Image.new("RGBA",(width,height),(0,0,0,0))
    out.alpha_composite(fit,((width-w)//2,(height-h)//2))
    fit.close()
    return out

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--category",default=MONITORING)
    ap.add_argument("--output",default="sdc-sc-chapeco-ppi-z")
    ap.add_argument("--width",type=int,default=3840)
    ap.add_argument("--height",type=int,default=2160)
    args=ap.parse_args()

    out=Path(args.output); out.mkdir(parents=True,exist_ok=True)
    session=requests.Session()
    score,post_url,image_url,meta,page_html=get_latest_ppi(session,args.category)

    ir=fetch(session,image_url,timeout=(15,90),image=True)
    with Image.open(BytesIO(ir.content)) as source:
        source=source.convert("RGBA")
        transparent,mask=radar_echo_rgba(source)
        uhd=make_uhd(transparent,args.width,args.height)
        uhd_name="chapeco-ppi-z-uhd.png"
        source_name="chapeco-ppi-z-source.png"
        source.save(out/source_name,"PNG",optimize=True,compress_level=6)
        uhd.save(out/uhd_name,"PNG",optimize=True,compress_level=6)
        source_size=source.size
        uhd_size=uhd.size

    # Preserve the official source image and the extracted transparent radar layer.
    manifest={
        "schema":SCHEMA,
        "provider":"Defesa Civil de Santa Catarina",
        "radar":"Chapecó",
        "product":"PPI Z / MPPI Refletividade",
        "sourcePost":post_url,
        "sourceImage":image_url,
        "sourceImageSize":{"width":source_size[0],"height":source_size[1]},
        "output":{"file":uhd_name,"width":uhd_size[0],"height":uhd_size[1],"format":"PNG","transparentEchoLayer":True},
        "processing":{
            "mode":"official-image-extraction",
            "inventedDbz":False,
            "inventedCells":False,
            "note":"A camada é extraída da imagem PPI oficial; não recupera valores que não estejam representados pela imagem."
        },
        "generatedAt":dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00","Z"),
        "echoPixels":int(mask.sum()),
        "selectionScore":score
    }
    (out/"manifest.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(manifest,ensure_ascii=False))

if __name__=="__main__":
    raise SystemExit(main())
