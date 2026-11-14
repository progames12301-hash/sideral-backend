"""Passive composition helper: only uses native SHARPpy images and SHARPpy JSON."""
from pathlib import Path
from PIL import Image,ImageDraw,ImageFont
import json,os
FONT='/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf';BOLD='/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'
def f(n,b=False):
 try:return ImageFont.truetype(BOLD if b else FONT,n)
 except:return ImageFont.load_default()
def main():
 s=Path(os.environ['SHARP_SKEWT']);h=Path(os.environ['SHARP_HODO']);m=json.loads(Path(os.environ['SHARP_META']).read_text());o=Path(os.environ['SHARP_FULL'])
 im=Image.new('RGB',(1800,1100),(8,9,12));d=ImageDraw.Draw(im);d.rectangle((0,0,1800,78),fill=(12,14,19));d.line((0,78,1800,78),fill=(58,62,70),width=1)
 d.text((28,18),'SIDERAL HUB / SKEW-T',fill='white',font=f(25,True));d.text((28,48),str(m.get('location','Brasil')),fill=(170,176,185),font=f(13));d.text((1150,18),'ECMWF IFS 0.25° • SHARPpy',fill='white',font=f(15,True));d.text((1150,45),f"{m.get('run_utc','--')} → {m.get('valid_utc','--')}",fill=(170,176,185),font=f(13))
 a=Image.open(s).convert('RGB');a.thumbnail((1200,900),Image.Resampling.LANCZOS);im.paste(a,(25,95))
 if h.exists():b=Image.open(h).convert('RGB');b.thumbnail((520,350),Image.Resampling.LANCZOS);im.paste(b,(1250,105))
 y=490
 for title,vals in [('PARCELAS',m.get('parcels',{})),('TERMODINÂMICA',m.get('thermodynamics',{})),('CINEMÁTICA',m.get('kinematics',{})),('SEVERE',m.get('severe',{}))]:
  d.text((1260,y),title,fill=(180,186,195),font=f(12,True));y+=23
  for k,v in vals.items():d.text((1260,y),str(k),fill=(235,238,242),font=f(12));d.text((1690,y),str(v),fill='white',font=f(12,True));y+=20
  y+=10
 d.text((25,1040),'ECMWF Open Data • cálculos e figuras: SHARPpy • produto pré-renderizado no GitHub',fill=(145,151,160),font=f(11));im.save(o)
if __name__=='__main__':main()
