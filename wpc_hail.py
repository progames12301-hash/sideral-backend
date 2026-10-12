"""Small server-side resolver for public WPC international hail graphics."""
from __future__ import annotations
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse
import requests

_ALLOWED_HOST = "www.wpc.ncep.noaa.gov"
_PREFIX = "/international/wng/"
_IMG_SUFFIXES = (".gif", ".png", ".jpg", ".jpeg", ".webp")
PAGES = {"csa": "09_CSA", "tsa": "12_TSA"}

class _PageParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows = []
        self.row = None
        self.cell = None
        self.anchor = None
    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag.lower() == "tr":
            self.row = []
        elif tag.lower() in ("td", "th") and self.row is not None:
            self.cell = {"text": "", "urls": []}
            self.row.append(self.cell)
        elif self.cell is not None and tag.lower() == "a":
            self.anchor = a.get("href")
            if self.anchor: self.cell["urls"].append(self.anchor)
        elif self.cell is not None and tag.lower() in ("img", "source"):
            src = a.get("src") or a.get("href")
            if src: self.cell["urls"].append(src)
    def handle_data(self, data):
        if self.cell is not None: self.cell["text"] += data + " "
    def handle_endtag(self, tag):
        if tag.lower() in ("td", "th"): self.cell = None; self.anchor = None
        elif tag.lower() == "tr":
            if self.row is not None: self.rows.append(self.row)
            self.row = None; self.cell = None

def _valid_image(raw, page_url):
    url = urljoin(page_url, raw.strip().replace("\\/", "/"))
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != _ALLOWED_HOST:
        return None
    if not parsed.path.startswith(_PREFIX) or not parsed.path.lower().endswith(_IMG_SUFFIXES):
        return None
    name = parsed.path.rsplit("/",1)[-1].lower()
    if any(x in name for x in ("logo","header","footer","button","arrow","play","stop","legend","loading","noaa.gif","wpc.gif")):
        return None
    return url

def get_hail_frames(algorithm="gr02", cycle="00", domain="csa"):
    algorithm = str(algorithm).lower()
    cycle = str(cycle).zfill(2)
    domain = str(domain).lower()
    if algorithm not in {"gr02", "gr02t"}: raise ValueError("Algoritmo inválido.")
    if cycle not in {"00", "12"}: raise ValueError("Ciclo inválido.")
    if domain not in PAGES: raise ValueError("Domínio inválido.")
    if domain == "tsa" and algorithm == "gr02":
        raise ValueError("O GR02 está publicado para o domínio Cone Sul.")
    page_url = f"https://{_ALLOWED_HOST}{_PREFIX}{PAGES[domain]}/index.shtml"
    response = requests.get(page_url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/128 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Referer": f"https://{_ALLOWED_HOST}{_PREFIX}"
    }, timeout=(7, 18))
    response.raise_for_status()
    html = response.text
    parser = _PageParser()
    parser.feed(html)
    target = "gr02t.cmd" if algorithm == "gr02t" else "gr02.cmd"
    frames = []
    row_text = ""
    for row in parser.rows:
        text = " ".join(c["text"] for c in row).lower()
        if target not in text and algorithm not in text:
            continue
        row_text = text
        # WPC algorithm table: label, GFS 00Z cell, GFS 12Z cell.
        idx = 1 if cycle == "00" else 2
        cells = [row[idx]] if idx < len(row) else row[1:]
        for cell in cells:
            for raw in cell["urls"]:
                url = _valid_image(raw, page_url)
                if url: frames.append(url)
    # Also handle pages whose source contains output URLs in scripts rather than IMG tags.
    if not frames:
        import re
        pattern = re.compile(r"""['"]([^'"]+\.(?:gif|png|jpe?g|webp)(?:\?[^'"]*)?)['"]""", re.I)
        for raw in pattern.findall(html):
            url = _valid_image(raw, page_url)
            if url and any(k in url.lower() for k in (algorithm, "gr02", "hail")):
                frames.append(url)
    unique = sorted(set(frames), key=lambda x: (x.lower(), x))
    if not unique:
        low = html.lower()
        if "idle" in low:
            raise RuntimeError(f"O WPC marcou {algorithm.upper()} GFS {cycle}Z como Idle; não há imagem publicada neste ciclo.")
        if response.status_code == 200:
            raise RuntimeError("O WPC não forneceu endereços de imagem para este produto.")
    return unique
