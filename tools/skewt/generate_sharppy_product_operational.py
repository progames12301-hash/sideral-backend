"""Operational SHARPpy wrapper used by GitHub Actions.

The browser receives finished PNG/GIF/JSON products. This wrapper keeps all
meteorological calculations in SHARPpy and builds the final presentation image
inside the GitHub runner, including the calculated parameter panels.
"""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
from tools.skewt import generate_sharppy_product as g

# ECMWF Open Data does not reliably expose pressure-level omega in the same
# published index used for the operational profile. It is not required for the
# thermodynamic/kinematic SHARPpy product, so keep the stable published set.
g.PL_PARAMS = ["t", "r", "u", "v", "gh"]

_original_render = g.render_with_sharppy

def _font(size=13):
    for name in ("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            pass
    return ImageFont.load_default()

def _val(v, digits=0):
    try:
        if v is None:
            return "--"
        x=float(v)
        if x != x or abs(x) == float("inf"):
            return "--"
        return f"{x:.{digits}f}"
    except Exception:
        return "--"

def render_complete(prof, out_dir: Path, meta: dict):
    # SHARPpy itself renders the Skew-T and hodograph first.
    _original_render(prof, out_dir, meta)

    # Build the same variables JSON that the generator publishes, then render
    # the information panel here in the runner. Nothing is calculated in HTML.
    data = g.build_variables(prof, 0.0, 0.0, meta["location"], prof.date, meta["fh"], g.LEVELS)
    vars_ = data.get("variables", {})
    parcels = data.get("parcels", {})
    storm = data.get("storm_motion", {})

    base = Image.open(out_dir / "full.png").convert("RGB")
    W, H = base.size
    panel_h = 300
    canvas = Image.new("RGB", (W, H + panel_h), (0, 0, 0))
    canvas.paste(base, (0, 0))
    d = ImageDraw.Draw(canvas)
    title_font = _font(15)
    head_font = _font(12)
    body_font = _font(11)
    small_font = _font(9)

    y0 = H
    d.rectangle((0, y0, W, H + panel_h), fill=(4, 4, 6), outline=(93, 50, 184), width=1)
    d.text((12, y0 + 10), "SIDERAL • SHARPpy / ECMWF IFS 0.25° • PARÂMETROS CALCULADOS NO RUNNER", font=title_font, fill=(255,255,255))

    cols = [0, W//4, W//2, 3*W//4, W]
    for x in cols[1:-1]:
        d.line((x, y0 + 42, x, H + panel_h), fill=(55, 45, 70), width=1)
    d.line((0, y0 + 40, W, y0 + 40), fill=(93,50,184), width=1)

    sections = [
        ("PARCELAS / INSTABILIDADE", [
            f"SFC CAPE { _val(parcels.get('surface',{}).get('bplus')) } J/kg",
            f"ML  CAPE { _val(parcels.get('mixed_layer',{}).get('bplus')) } J/kg",
            f"MU  CAPE { _val(parcels.get('most_unstable',{}).get('bplus')) } J/kg",
            f"SFC CIN  { _val(parcels.get('surface',{}).get('bminus')) } J/kg",
            f"LCL { _val(parcels.get('surface',{}).get('lclhght')) } m",
            f"LFC { _val(parcels.get('surface',{}).get('lfchght')) } m",
            f"EL  { _val(parcels.get('surface',{}).get('elhght')) } m",
        ]),
        ("TERMODINÂMICA", [
            f"PWAT {_val(vars_.get('pwat'),1)}",
            f"K Index {_val(vars_.get('k_idx'),1)}",
            f"TT {_val(vars_.get('t_totals'),1)}",
            f"DCAPE {_val(vars_.get('dcape'))} J/kg",
            f"700–500 LR {_val(vars_.get('lapserate_700_500'),1)} °C/km",
            f"850–500 LR {_val(vars_.get('lapserate_850_500'),1)} °C/km",
            f"Freezing {_val(vars_.get('freezinglevel'))} m",
        ]),
        ("CISALHAMENTO / SRH", [
            f"SRH 0–1 {_val(vars_.get('srh1km'))} m²/s²",
            f"SRH 0–3 {_val(vars_.get('srh3km'))} m²/s²",
            f"Shear 0–1 {_val(vars_.get('sfc_1km_shear'),1)} kt",
            f"Shear 0–3 {_val(vars_.get('sfc_3km_shear'),1)} kt",
            f"Shear 0–6 {_val(vars_.get('sfc_6km_shear'),1)} kt",
            f"Eff BWD {_val(vars_.get('ebwd'),1)} kt",
            f"Crit angle {_val(vars_.get('critical_angle'))}°",
        ]),
        ("STORM MOTION / CAMADAS", [
            f"Eff bottom {_val(storm.get('effective_bottom_m'))} m",
            f"Eff top {_val(storm.get('effective_top_m'))} m",
            f"Bunkers {storm.get('bunkers') or '--'}",
            f"PBL {_val(vars_.get('pblhght'))} m",
            f"Melt {_val(vars_.get('meltlevel'))} m",
            f"SIG Severe {_val(vars_.get('sig_severe'))}",
            "Renderer: SHARPpy",
        ]),
    ]

    for i, (title, lines) in enumerate(sections):
        x = cols[i] + 12
        d.text((x, y0 + 55), title, font=head_font, fill=(169,118,255))
        yy = y0 + 80
        for line in lines:
            d.text((x, yy), line, font=body_font, fill=(235,232,240))
            yy += 25

    d.text((12, H + panel_h - 14), "Produto pré-renderizado no GitHub • ECMWF IFS + SHARPpy • O navegador apenas exibe PNG/GIF/JSON", font=small_font, fill=(150,145,160))
    canvas.save(out_dir / "full.png", "PNG", optimize=True)

# Replace the generator renderer before main() is entered.
g.render_with_sharppy = render_complete

g.main()
