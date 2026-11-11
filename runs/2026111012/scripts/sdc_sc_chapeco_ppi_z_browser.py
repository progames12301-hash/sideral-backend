#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import re
from pathlib import Path
from urllib.parse import urlparse

from PIL import Image
import numpy as np
from playwright.async_api import async_playwright

MAP_URL = "https://monitoramento.defesacivil.sc.gov.br/mapa"
SCHEMA = "sideral-sdcsc-chapeco-ppi-z-browser-v1"

KEYWORDS = (
    "radar", "ppi", "mppi", "reflect", "reflet", "dbz",
    "chapec", "chapeco", "zscan", "rainbow"
)


PALETTE=np.array([
    [0,220,255],[0,120,255],[0,255,255],[0,255,0],[80,255,0],
    [255,255,0],[255,180,0],[255,100,0],[255,0,0],[190,0,0],[255,0,255]
],dtype=np.int16)

def extract_echo(image):
    rgba=np.asarray(image.convert("RGBA"),dtype=np.uint8)
    rgb=rgba[...,:3].astype(np.int16)
    hi=rgb.max(axis=2); lo=rgb.min(axis=2); sat=hi-lo
    dist=((rgb[...,None,:]-PALETTE[None,None,:,:])**2).sum(axis=3)
    mask=(dist.min(axis=2)<19000)&(sat>25)&(hi>70)
    mask &= ~((hi>235)&(sat<22))
    mask &= ~(hi<25)
    out=rgba.copy()
    out[...,3]=np.where(mask,rgba[...,3],0)
    return Image.fromarray(out,"RGBA")

def make_uhd(image,width=3840,height=2160):
    scale=min(width/image.width,height/image.height)
    w=max(1,round(image.width*scale))
    h=max(1,round(image.height*scale))
    fit=image.resize((w,h),Image.Resampling.LANCZOS)
    out=Image.new("RGBA",(width,height),(0,0,0,0))
    out.alpha_composite(fit,((width-w)//2,(height-h)//2))
    fit.close()
    return out

async def main_async(args):
    out=Path(args.output)
    out.mkdir(parents=True,exist_ok=True)
    events=[]
    candidates=[]

    async with async_playwright() as p:
        browser=await p.chromium.launch(
            headless=True,
            args=["--disable-dev-shm-usage","--no-sandbox"]
        )
        page=await browser.new_page(viewport={"width":1920,"height":1080},device_scale_factor=1)

        async def on_response(response):
            try:
                url=response.url
                low=url.lower()
                ctype=(response.headers.get("content-type") or "").lower()

                # JSON/text can be filtered by radar keywords. Images are NOT
                # filtered by URL because the real radar file may use an opaque path.
                is_image=("image/" in ctype or response.request.resource_type in {"image","img"})
                if not is_image and not any(k in low for k in KEYWORDS):
                    return

                event={
                    "url":url,
                    "status":response.status,
                    "contentType":ctype,
                    "resourceType":response.request.resource_type,
                }
                events.append(event)

                if response.status != 200:
                    return

                if is_image:
                    try:
                        body=await response.body()
                        if len(body) < 10000:
                            return
                        try:
                            with Image.open(BytesIO(body)) as im:
                                iw,ih=im.size
                        except Exception:
                            return

                        # Ignore browser UI assets and tiny generic map tiles.
                        if iw < 400 or ih < 400:
                            return

                        candidate=out/f"network-{len(candidates):03d}.png"
                        candidate.write_bytes(body)
                        item={
                            "file":str(candidate),
                            "url":url,
                            "contentType":ctype or "image/unknown",
                            "bytes":len(body),
                            "width":iw,
                            "height":ih,
                        }
                        candidates.append(item)
                        event["width"]=iw
                        event["height"]=ih
                    except Exception:
                        pass

                elif "json" in ctype:
                    try:
                        body_text=(await response.text())[:1000000]
                        if re.search(r"chapec|chapeco|ppi|mppi|reflect|reflet|dbz|radar",body_text,re.I):
                            candidate=out/f"network-{len(candidates):03d}.json"
                            candidate.write_text(body_text,encoding="utf-8")
                            candidates.append({
                                "file":str(candidate),
                                "url":url,
                                "contentType":ctype,
                                "bytes":len(body_text),
                            })
                    except Exception:
                        pass
            except Exception:
                pass

        page.on("response",on_response)

        await page.goto(MAP_URL,wait_until="domcontentloaded",timeout=90000)
        await page.wait_for_timeout(15000)

        # Read the rendered page text only for diagnostics.
        texts=await page.locator("body").inner_text(timeout=15000)
        (out/"page-text.txt").write_text(texts[:200000],encoding="utf-8")

        # Attempt common radar controls.
        patterns=[
            re.compile(r"chapec[oó]",re.I),
            re.compile(r"ppi",re.I),
            re.compile(r"refletividade|reflectivity|dBZ",re.I),
            re.compile(r"radar",re.I),
        ]
        for pattern in patterns:
            try:
                loc=page.get_by_text(pattern).first
                if await loc.count():
                    await loc.click(timeout=2000)
                    await page.wait_for_timeout(2000)
            except Exception:
                pass

        await page.wait_for_timeout(15000)

        # Capture text again after controls are opened.
        texts2=await page.locator("body").inner_text(timeout=15000)
        (out/"page-text-after.txt").write_text(texts2[:200000],encoding="utf-8")

        # Inspect actual <img> elements too. Some radar products are exposed as
        # images in the DOM even when their URL has no "radar" keyword.
        try:
            dom_images=await page.locator("img").evaluate_all(
                "(els) => els.map(e => ({src:e.currentSrc || e.src || '', width:e.naturalWidth || e.width || 0, height:e.naturalHeight || e.height || 0})).filter(x => x.src)"
            )
            (out/"dom-images.json").write_text(
                json.dumps(dom_images,ensure_ascii=False,indent=2),encoding="utf-8"
            )
            for item in dom_images:
                u=item.get("src","")
                w=int(item.get("width") or 0)
                h=int(item.get("height") or 0)
                if u.startswith(("http://","https://")) and w >= 400 and h >= 400:
                    events.append({"url":u,"status":200,"contentType":"image/dom","resourceType":"img","width":w,"height":h})
        except Exception:
            pass

        await browser.close()

    # Prefer a real radar image over UI assets/map tiles.
    scored=[]
    for item in candidates:
        low=item["url"].lower()
        score=0
        if "chapec" in low or "chapeco" in low: score+=100
        if "ppi" in low or "mppi" in low: score+=100
        if "reflect" in low or "reflet" in low or "dbz" in low: score+=100
        if "radar" in low: score+=35
        if "png" in low or "jpeg" in low or "jpg" in low: score+=5
        if item.get("width",0) >= 500: score+=25
        if item.get("height",0) >= 500: score+=25
        if item.get("width",0) and item.get("height",0):
            ratio=item["width"]/item["height"]
            if 0.80 <= ratio <= 1.25: score+=35
        if item.get("bytes",0) >= 50000: score+=20
        # 256x256/512x512 generic tiles are penalized unless the URL explicitly
        # identifies the radar product.
        if item.get("width") in (256,512) and item.get("height") in (256,512):
            if not any(k in low for k in ("radar","ppi","mppi","dbz")):
                score-=80
        scored.append((score,item))
    scored.sort(key=lambda x:(-x[0],x[1]["url"]))

    selected=None
    for score,item in scored:
        if score>=80 and item["file"].lower().endswith((".png",".jpg")):
            selected=item
            selected["score"]=score
            break

    output_files={}
    if selected:
        try:
            with Image.open(selected["file"]) as source:
                source=source.convert("RGBA")
                source.save(out/"chapeco-ppi-z-source.png","PNG",optimize=True,compress_level=6)
                echo=extract_echo(source)
                uhd=make_uhd(echo,3840,2160)
                uhd.save(out/"chapeco-ppi-z-uhd.png","PNG",optimize=True,compress_level=6)
                output_files={
                    "source":"chapeco-ppi-z-source.png",
                    "uhd":"chapeco-ppi-z-uhd.png",
                    "width":3840,
                    "height":2160
                }
                echo.close()
        except Exception as exc:
            selected["processingError"]=f"{type(exc).__name__}: {exc}"

    manifest={
        "schema":SCHEMA,
        "provider":"Defesa Civil de Santa Catarina",
        "radar":"Chapecó",
        "product":"PPI Z / MPPI Refletividade",
        "mapUrl":MAP_URL,
        "generatedAt":dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00","Z"),
        "selected":selected,
        "output":output_files,
        "networkEvents":events[-300:],
        "candidates":candidates,
        "note":"Captura feita no aplicativo web oficial para identificar e obter o recurso efetivamente usado pelo mapa."
    }
    (out/"manifest.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")

    print(json.dumps({
        "selected":selected,
        "candidates":len(candidates),
        "networkEvents":len(events),
        "radarImage":selected.get("file") if selected else None
    },ensure_ascii=False))

if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--output",default="sdc-sc-chapeco-ppi-z")
    asyncio.run(main_async(parser.parse_args()))
