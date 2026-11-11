#!/usr/bin/env python3
"""Sideral raw radar collector.

Downloads openly exposed raw/quantitative radar files from official sources.
For CEMADEN, the runner walks public indexes and only downloads direct file
links. CAPTCHA/authentication is never solved, submitted, or bypassed.
"""
from __future__ import annotations
import argparse, json, re, sys
from html import unescape
from pathlib import Path
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen

INPE_SOURCES = {
    "inpe_radar_raw": "https://ftp.cptec.inpe.br/nowcasting/radar/",
    "inpe_radar_volume": "https://ftp.cptec.inpe.br/nowcasting/DADOS/radar_volumetrico/",
    "inpe_radar_velocity": "https://ftp.cptec.inpe.br/nowcasting/DADOS/velocidade_radial/",
}
CEMADEN_BASE = "https://mapainterativo.cemaden.gov.br"
CEMADEN_RADARS = ("almenara","jaraguari","maceio","natal","petrolina","salvador","santa_teresa","sao_francisco","tres_marias")
ALLOWED_EXTENSIONS = {".raw",".nc",".h5",".hdf5",".vol",".bufr",".bin"}
RADAR_EXTENSIONS = ALLOWED_EXTENSIONS


def get(url: str, max_bytes: int | None = None) -> bytes:
    req = Request(url, headers={"User-Agent":"Sideral-Radar-Runner/1.2","Accept":"*/*"})
    with urlopen(req, timeout=60) as r:
        if max_bytes is None:
            return r.read()
        data = r.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise RuntimeError(f"resposta excede limite de {max_bytes} bytes")
        return data


def get_html(url: str) -> str:
    return get(url, 16 * 1024 * 1024).decode("utf-8", errors="replace")


def list_links(index_url: str) -> list[tuple[str,str]]:
    html = get_html(index_url)
    links = re.findall(r'href=["\']([^"\']+)["\']', html, re.I)
    out=[]
    for href in links:
        href=unescape(href.strip())
        if href.startswith(("#","?","javascript:")): continue
        absolute=urljoin(index_url,href)
        name=Path(urlparse(absolute).path).name
        if name and name not in {".",".."}: out.append((name,absolute))
    return out


def file_links(index_url: str) -> list[tuple[str,str]]:
    return [(n,u) for n,u in list_links(index_url) if Path(n).suffix.lower() in ALLOWED_EXTENSIONS]


def extract_timestamp(name: str) -> int:
    # Supports YYYYMMDDHHMM, YYYYMMDDHHMMSS and common radar filename variants.
    candidates=re.findall(r"(?:19|20)\d{10,12}",name)
    vals=[]
    for x in candidates:
        try: vals.append(int(x))
        except ValueError: pass
    return max(vals, default=0)


def sort_recent(files: list[tuple[str,str]]) -> list[tuple[str,str]]:
    # Timestamp first; directory indexes can be reverse/lexicographic and must not
    # be trusted. Unknown timestamps go last but remain available as fallback.
    return sorted(files,key=lambda x:(extract_timestamp(x[0]),x[0].lower()),reverse=True)


def download_latest(source: str,url: str,output: Path,count: int) -> dict:
    files=sort_recent(file_links(url))
    if not files: raise RuntimeError(f"Nenhum arquivo bruto encontrado em {url}")
    selected=files[:count]
    d=output/source; d.mkdir(parents=True,exist_ok=True)
    downloaded=[]
    for name,file_url in selected:
        target=d/name; data=get(file_url); target.write_bytes(data)
        downloaded.append({"name":name,"url":file_url,"bytes":len(data),"path":str(target)})
        print(f"[OK] {source}: {name} ({len(data)} bytes)")
    return {"source":source,"index":url,"count":len(downloaded),"files":downloaded}


def is_direct_file_url(url:str)->bool:
    path=urlparse(url).path.lower()
    return "downradarescaptcha.php" not in path and Path(path).suffix.lower() in RADAR_EXTENSIONS


def collect_cemaden(output:Path,count:int)->dict:
    d=output/"cemaden"; d.mkdir(parents=True,exist_ok=True)
    result={"source":"cemaden","index":f"{CEMADEN_BASE}/download/downradares.php","radars":[],"downloaded":[],"captcha_required":[],"errors":[]}
    for radar in CEMADEN_RADARS:
        info={"radar":radar,"products":[]}
        radar_url=f"{CEMADEN_BASE}/download/downradares.php?radar={radar}"
        try: links=list_links(radar_url)
        except Exception as e:
            info["error"]=str(e); result["errors"].append({"radar":radar,"stage":"products","error":str(e)}); result["radars"].append(info); continue
        products=[]; seen=set()
        for name,url in links:
            if "produto=" in url and "radar=" in url and url not in seen:
                seen.add(url); products.append((name,url))
        for pname,purl in products:
            pi={"product":pname,"url":purl,"files_seen":0}
            try: files=sort_recent(file_links(purl))
            except Exception as e:
                pi["error"]=str(e); info["products"].append(pi); continue
            pi["files_seen"]=len(files)
            # Inspect only a bounded recent set per product; do not brute-force the index.
            preferred=("dBZ","V","W","ZDR","KDP","PhiDP","RhoHV")
            def rank(item):
                n=item[0]; p=next((len(preferred)-i for i,t in enumerate(preferred) if t.lower() in n.lower()),0)
                return (p,extract_timestamp(n),n.lower())
            files=sorted(files,key=rank,reverse=True)
            got=0
            for name,furl in files:
                if got>=max(1,count): break
                if not is_direct_file_url(furl):
                    result["captcha_required"].append({"radar":radar,"product":pname,"name":name,"url":furl,"reason":"published link requires CAPTCHA"}); continue
                try:
                    target=d/radar/pname/name; target.parent.mkdir(parents=True,exist_ok=True)
                    data=get(furl); target.write_bytes(data)
                    result["downloaded"].append({"radar":radar,"product":pname,"name":name,"url":furl,"bytes":len(data),"path":str(target)})
                    got+=1; print(f"[OK] CEMADEN {radar}/{pname}: {name} ({len(data)} bytes)")
                except Exception as e:
                    result["errors"].append({"radar":radar,"product":pname,"name":name,"url":furl,"error":str(e)})
            info["products"].append(pi)
        result["radars"].append(info)
    result["count"]=len(result["downloaded"]); result["captcha_count"]=len(result["captcha_required"])
    return result


def main()->int:
    ap=argparse.ArgumentParser(); ap.add_argument("--output",default="radar_raw"); ap.add_argument("--count",type=int,default=2); args=ap.parse_args()
    output=Path(args.output); output.mkdir(parents=True,exist_ok=True)
    manifest={"project":"Sideral Meteorologia","collector":"radar_raw_runner","sources":[],"notes":["Somente dados expostos publicamente sem bypass de CAPTCHA/autenticação.","INPE: seleção por timestamp real do nome do arquivo, não pela ordem do índice.","CEMADEN: percorre radar -> produto -> arquivo e baixa apenas links binários diretos."]}
    failed=[]
    for source,url in INPE_SOURCES.items():
        try: manifest["sources"].append(download_latest(source,url,max(1,args.count),output))
        except Exception as e: failed.append({"source":source,"url":url,"error":str(e)}); print(f"[WARN] {source}: {e}",file=sys.stderr)
    try: manifest["sources"].append(collect_cemaden(output,max(1,args.count)))
    except Exception as e: failed.append({"source":"cemaden","error":str(e)}); print(f"[WARN] cemaden: {e}",file=sys.stderr)
    (output/"manifest.json").write_text(json.dumps({**manifest,"failed":failed},ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({"downloaded_sources":len(manifest["sources"]),"failed":failed,"cemaden_direct_attempted":any(s.get("source")=="cemaden" for s in manifest["sources"])},ensure_ascii=False))
    return 0

if __name__=="__main__": raise SystemExit(main())
