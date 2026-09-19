"""Compose the already-rendered SHARPpy figures into one operational product.

No meteorological values are calculated here. The input JSON and native
SHARPpy plots are produced by the upstream SHARPpy generator.
"""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
import json, os

FONT='/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
BOLD='/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'

def ft(size,bold=False):
    try: return ImageFont.truetype(BOLD if bold else FONT,size)
    except Exception: return ImageFont.load_default()

def main():
    skew=Path(os.environ['SHARP_SKEWT'])
    hodo=Path(os.environ['SHARP_HODO'])
    meta=json.loads(Path(os.environ['SHARP_META']).read_text())
    out=Path(os.environ['SHARP_FULL'])
    W,H=1800,1100
    im=Image.new('RGB',(W,H),(8,9,12)); d=ImageDraw.Draw(im)
    # Clean operational frame around the actual SHARPpy figures.
    d.rectangle((0,0,W,78),fill=(12,14,19)); d.line((0,78,W,78),fill=(58,62,70),width=1)
    d.text((28,18),'SIDERAL HUB  /  SKEW-T',fill='white',font=ft(25,True))
    d.text((28,48),str(meta.get('location','Brasil')),fill=(170,176,185),font=ft(13))
    d.text((W-650,18),'ECMWF IFS 0.25°  •  SHARPpy',fill='white',font=ft(15,True))
    d.text((W-650,45),f"{meta.get('run_utc','--')}  →  {meta.get('valid_utc','--')}",fill=(170,176,185),font=ft(13))
    a=Image.open(skew).convert('RGB'); a.thumbnail((1200,900),Image.Resampling.LANCZOS)
    im.paste(a,(25,95))
    if hodo.exists():
        b=Image.open(hodo).convert('RGB'); b.thumbnail((520,380),Image.Resampling.LANCZOS)
        im.paste(b,(1250,105))
    # Only display values already calculated by SHARPpy.
    blocks=[('PARCELAS',meta.get('parcels',{})),('TERMODINÂMICA',meta.get('thermodynamics',{})),('CINEMÁTICA',meta.get('kinematics',{})),('SEVERE',meta.get('severe',{}))]
    y=505
    for title,vals in blocks:
        d.text((1260,y),title,fill=(180,186,195),font=ft(12,True)); y+=23
        for k,v in vals.items():
            d.text((1260,y),str(k),fill=(235,238,242),font=ft(12))
            d.text((1690,y),str(v),fill='white',font=ft(12,True)); y+=20
            if y>985: break
        y+=10
    d.text((25,1040),'ECMWF Open Data  •  cálculos e figuras meteorológicas: SHARPpy  •  produto pré-renderizado no GitHub',fill=(145,151,160),font=ft(11))
    im.save(out)

if __name__=='__main__': main()
