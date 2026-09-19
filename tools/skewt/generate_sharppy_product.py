from __future__ import annotations
import argparse, datetime as dt, json, math, os, shutil
from pathlib import Path

import numpy as np
from ecmwf.opendata import Client
from eccodes import codes_grib_new_from_file, codes_get, codes_grib_find_nearest, codes_release

LEVELS = [1000, 975, 950, 925, 900, 850, 800, 700, 600, 500, 400, 300, 250, 200, 150, 100, 70, 50, 30, 20, 10]


def atomic_retrieve(client, target: Path, **req):
    tmp = target.with_suffix(target.suffix + '.part')
    if tmp.exists():
        tmp.unlink()
    client.retrieve(target=str(tmp), **req)
    tmp.replace(target)


def read_fields(path: Path, lat: float, lon: float):
    fields = {}
    with path.open('rb') as f:
        while True:
            gid = codes_grib_new_from_file(f)
            if gid is None:
                break
            try:
                short = str(codes_get(gid, 'shortName'))
                try:
                    level = int(codes_get(gid, 'level'))
                except Exception:
                    level = 0
                nearest = codes_grib_find_nearest(gid, lat, lon)[0]
                value = float(getattr(nearest, 'value'))
                nlat = float(getattr(nearest, 'lat', lat))
                nlon = float(getattr(nearest, 'lon', lon))
                fields[(short, level)] = (value, nlat, nlon)
            finally:
                codes_release(gid)
    return fields


def make_profile(pl, sfc, lat, lon, valid_dt):
    sp = sfc.get(('sp', 0), (None, lat, lon))[0]
    sfc_t = sfc.get(('2t', 0), (None, lat, lon))[0]
    sfc_td = sfc.get(('2d', 0), (None, lat, lon))[0]
    sfc_z = sfc.get(('z', 0), (None, lat, lon))[0]
    psfc = float(sp) / 100.0 if sp is not None else 1000.0
    sfc_h = float(sfc_z) / 9.80665 if sfc_z is not None else 0.0

    pres, hght, tmp, dwpt, u, v, omega = [], [], [], [], [], [], []
    if sfc_t is not None and sfc_td is not None:
        pres.append(psfc); hght.append(sfc_h)
        tmp.append(float(sfc_t) - 273.15); dwpt.append(float(sfc_td) - 273.15)
        u.append(float(sfc.get(('10u', 0), (0, 0, 0))[0] or 0) * 1.943844)
        v.append(float(sfc.get(('10v', 0), (0, 0, 0))[0] or 0) * 1.943844)
        omega.append(np.nan)

    for lev in LEVELS:
        if lev > psfc + 1.0:
            continue
        t = pl.get(('t', lev)); r = pl.get(('r', lev)); gh = pl.get(('gh', lev))
        uu = pl.get(('u', lev)); vv = pl.get(('v', lev)); ww = pl.get(('w', lev))
        if not all(x is not None for x in (t, r, gh, uu, vv)):
            continue
        tc = float(t[0]) - 273.15
        rh = max(0.1, min(100.0, float(r[0])))
        a, b = 17.625, 243.04
        gamma = math.log(rh / 100.0) + (a * tc) / (b + tc)
        td = b * gamma / (a - gamma)
        pres.append(float(lev)); hght.append(float(gh[0]) / 9.80665)
        tmp.append(tc); dwpt.append(td)
        u.append(float(uu[0]) * 1.943844); v.append(float(vv[0]) * 1.943844)
        omega.append(float(ww[0]) if ww is not None else np.nan)

    order = np.argsort(np.asarray(pres))[::-1]
    pres = np.asarray(pres)[order]; hght = np.asarray(hght)[order]
    tmp = np.asarray(tmp)[order]; dwpt = np.asarray(dwpt)[order]
    u = np.asarray(u)[order]; v = np.asarray(v)[order]; omega = np.asarray(omega)[order]

    from sharppy.sharptab import profile as shp_profile
    return shp_profile.create_profile(
        profile='convective', pres=pres, hght=hght, tmpc=tmp, dwpc=dwpt,
        u=u, v=v, omeg=np.ma.masked_invalid(omega), strictQC=False,
        latitude=lat, date=valid_dt, location='SID',
    )


def render_with_sharppy(prof, out_png: Path, meta: dict):
    os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
    from PySide2.QtWidgets import QApplication, QWidget, QHBoxLayout, QVBoxLayout, QLabel
    from sharppy.sharptab.prof_collection import ProfCollection
    from sharppy.viz.skew import plotSkewT
    from sharppy.viz.hodo import plotHodo

    app = QApplication.instance() or QApplication([])
    pc = ProfCollection({'ECMWF IFS': [prof]}, [prof.date], highlight='ECMWF IFS', location=meta['location'])
    root = QWidget(); root.setStyleSheet('background:#000;color:#fff;'); root.resize(1600, 980)
    layout = QVBoxLayout(root); layout.setContentsMargins(8, 8, 8, 8); layout.setSpacing(6)
    header = QLabel(f"SIDERAL SKEW-T  |  ECMWF IFS 0.25°  |  {meta['location']}  |  F{meta['fh']:03d}  |  VALID {meta['valid']}")
    header.setStyleSheet('color:white;background:#000;font:700 16px Consolas;padding:8px;border-bottom:1px solid #444;')
    layout.addWidget(header)
    top = QWidget(); top_layout = QHBoxLayout(top); top_layout.setContentsMargins(0, 0, 0, 0); top_layout.setSpacing(6)
    skew = plotSkewT(plot_omega=True); hodo = plotHodo()
    skew.setMinimumSize(1000, 650); hodo.setMinimumSize(520, 650)
    top_layout.addWidget(skew, 2); top_layout.addWidget(hodo, 1); layout.addWidget(top, 1)
    bottom = QLabel(); bottom.setStyleSheet('color:white;background:#050505;font:11px Consolas;padding:6px;border:1px solid #333;')
    vals = []
    for name, obj in [
        ('SBCAPE', getattr(prof.sfcpcl, 'bplus', np.nan)), ('MLCAPE', getattr(prof.mlpcl, 'bplus', np.nan)),
        ('MUCAPE', getattr(prof.mupcl, 'bplus', np.nan)), ('SBCIN', getattr(prof.sfcpcl, 'bminus', np.nan)),
        ('MLCIN', getattr(prof.mlpcl, 'bminus', np.nan)), ('MUCIN', getattr(prof.mupcl, 'bminus', np.nan)),
        ('LCL', getattr(prof.sfcpcl, 'lclhght', np.nan)), ('LFC', getattr(prof.sfcpcl, 'lfchght', np.nan)),
        ('EL', getattr(prof.sfcpcl, 'elhght', np.nan)), ('PWAT', getattr(prof, 'pwv', np.nan)),
        ('SRH 0-1', getattr(prof, 'srh01', np.nan)), ('SRH 0-3', getattr(prof, 'srh03', np.nan)),
    ]:
        try: vals.append(f'{name}={float(obj):.0f}')
        except Exception: vals.append(f'{name}=--')
    bottom.setText('   '.join(vals)); layout.addWidget(bottom)
    root.show(); app.processEvents()
    skew.addProfileCollection(pc); hodo.addProfileCollection(pc)
    app.processEvents(); root.grab().save(str(out_png), 'PNG')
    root.close(); app.processEvents()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--lat', type=float, default=-15.78)
    ap.add_argument('--lon', type=float, default=-47.93)
    ap.add_argument('--label', default='Brasil')
    ap.add_argument('--out', default='skewt-out')
    ap.add_argument('--hours', default=','.join(str(h) for h in range(0, 73, 3)))
    args = ap.parse_args()
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    hours = [int(x) for x in args.hours.split(',') if x.strip()]
    client = Client(source='ecmwf', model='ifs', resol='0p25')
    probe = out / '_probe.grib'
    first = client.retrieve(type='fc', step=hours[0], param='t', target=str(probe))
    run_dt = first.datetime.replace(tzinfo=None); probe.unlink(missing_ok=True)
    frames = []
    for fh in hours:
        stem = f'f{fh:03d}'; pl = out / f'{stem}_pl.grib'; sf = out / f'{stem}_sfc.grib'; oro = out / f'{stem}_oro.grib'
        common = dict(date=run_dt.strftime('%Y-%m-%d'), time=run_dt.hour, type='fc', stream='oper', step=fh,
                      grid='0.1/0.1', area=[args.lat + 0.30, args.lon - 0.30, args.lat - 0.30, args.lon + 0.30])
        atomic_retrieve(client, pl, **common, levtype='pl', levelist='/'.join(map(str, LEVELS)), param='130.128/131.128/132.128/157.128/156.128')
        atomic_retrieve(client, sf, **common, levtype='sfc', param='134.128/167.128/168.128/165.128/166.128')
        atomic_retrieve(client, oro, **common, levtype='sfc', param='129.128')
        fields = read_fields(pl, args.lat, args.lon)
        sflds = read_fields(sf, args.lat, args.lon); sflds.update(read_fields(oro, args.lat, args.lon))
        prof = make_profile(fields, sflds, args.lat, args.lon, run_dt + dt.timedelta(hours=fh))
        png = out / f'{stem}.png'
        render_with_sharppy(prof, png, {'location': args.label, 'fh': fh, 'valid': (run_dt + dt.timedelta(hours=fh)).strftime('%Y-%m-%d %H:%MZ')})
        frames.append(png)
        for x in (pl, sf, oro): x.unlink(missing_ok=True)

    from PIL import Image
    ims = [Image.open(p).convert('P', palette=Image.Palette.ADAPTIVE, colors=256) for p in frames]
    ims[0].save(out / 'animation.gif', save_all=True, append_images=ims[1:], duration=700, loop=0, optimize=False)
    meta = {
        'model': 'ECMWF IFS', 'resolution': '0.25°', 'lat': args.lat, 'lon': args.lon,
        'location': args.label, 'run': run_dt.strftime('%Y-%m-%d %HZ'), 'forecast_hours': hours,
        'png_pattern': 'f{fh:03d}.png', 'gif': 'animation.gif',
        'renderer': 'SHARPpy viz.plotSkewT + SHARPpy viz.plotHodo', 'browser_rendering': False,
        'domain': 'Brasil', 'levels_hPa': LEVELS,
    }
    (out / 'latest.json').write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding='utf-8')
    shutil.copy2(frames[0], out / 'latest.png')


if __name__ == '__main__':
    main()
