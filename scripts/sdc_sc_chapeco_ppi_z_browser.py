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
from playwright.async_api import async_playwright

MAP_URL = "https://monitoramento.defesacivil.sc.gov.br/mapa"
SCHEMA = "sideral-sdcsc-chapeco-ppi-z-browser-v1"

KEYWORDS = (
    "radar", "ppi", "mppi", "reflect", "reflet", "dbz",
    "chapec", "chapeco", "zscan", "rainbow"
)

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
                if not any(k in low for k in KEYWORDS):
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
                if "image/" in ctype:
                    try:
                        body=await response.body()
                        if len(body) > 5000:
                            ext=".png" if "png" in ctype else ".jpg"
                            candidate=out/f"network-{len(candidates):03d}{ext}"
                            candidate.write_bytes(body)
                            candidates.append({"file":str(candidate),"url":url,"contentType":ctype,"bytes":len(body)})
                    except Exception:
                        pass
                elif "json" in ctype:
                    try:
                        text=(await response.text())[:1000000]
                        if re.search(r"chapec|ppi|mppi|reflect|dbz",text,re.I):
                            (out/f"network-{len(candidates):03d}.json").write_text(text,encoding="utf-8")
                            candidates.append({"file":str(out/f"network-{len(candidates)-1:03d}.json"),"url":url,"contentType":ctype,"bytes":len(text)})
                    except Exception:
                        pass
            except Exception:
                pass

        page.on("response",on_response)

        await page.goto(MAP_URL,wait_until="domcontentloaded",timeout=90000)
        await page.wait_for_timeout(15000)

        # Try to expose radar controls without assuming a specific framework.
        texts=await page.locator("body").inner_text(timeout=15000)
        (out/"page-text.txt").write_text(texts[:200000],encoding="utf-8")

        # Attempt common controls by accessible/text labels.
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
        await page.screenshot(path=str(out/"map-screenshot.png"),full_page=True)

        # Capture text again after controls are opened.
        texts2=await page.locator("body").inner_text(timeout=15000)
        (out/"page-text-after.txt").write_text(texts2[:200000],encoding="utf-8")

        await browser.close()

    # Prefer actual network images whose URL names the radar/product.
    scored=[]
    for item in candidates:
        low=item["url"].lower()
        score=0
        if "chapec" in low: score+=100
        if "ppi" in low or "mppi" in low: score+=80
        if "reflect" in low or "reflet" in low or "dbz" in low: score+=80
        if "radar" in low: score+=30
        if "image/" in item.get("contentType",""): score+=10
        scored.append((score,item))
    scored.sort(key=lambda x:(-x[0],x[1]["url"]))

    selected=None
    for score,item in scored:
        if score>=80 and item["file"].lower().endswith((".png",".jpg")):
            selected=item
            selected["score"]=score
            break

    manifest={
        "schema":SCHEMA,
        "provider":"Defesa Civil de Santa Catarina",
        "radar":"Chapecó",
        "product":"PPI Z / MPPI Refletividade",
        "mapUrl":MAP_URL,
        "generatedAt":dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00","Z"),
        "selected":selected,
        "networkEvents":events[-300:],
        "candidates":candidates,
        "note":"Captura feita no aplicativo web oficial para descobrir/carregar o recurso efetivamente usado pelo mapa."
    }
    (out/"manifest.json").write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")

    print(json.dumps({
        "selected":selected,
        "candidates":len(candidates),
        "networkEvents":len(events),
        "screenshot":str(out/"map-screenshot.png")
    },ensure_ascii=False))

if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--output",default="sdc-sc-chapeco-ppi-z")
    asyncio.run(main_async(parser.parse_args()))
