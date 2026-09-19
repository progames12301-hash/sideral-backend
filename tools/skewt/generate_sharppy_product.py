from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import shutil
from pathlib import Path

import numpy as np
from ecmwf.opendata import Client
from eccodes import codes_grib_new_from_file, codes_get, codes_grib_find_nearest, codes_release

# IFS Open Data pressure levels actually published by ECMWF in 2026.
LEVELS = [1000, 925, 850, 700, 600, 500, 400, 300, 250, 200, 150, 100, 50, 10]
PL_PARAMS = ["t", "r", "u", "v", "w", "gh"]
SFC_PARAMS = ["sp", "2t", "2d", "10u", "10v"]


def atomic_retrieve(client, target: Path, **req):
    tmp = target.with_suffix(target.suffix + ".part")
    if tmp.exists():
        tmp.unlink()
    client.retrieve(target=str(tmp), **req)
    tmp.replace(target)


def read_fields(path: Path, lat: float, lon: float):
    fields = {}
    with path.open("rb") as f:
        while True:
            gid = codes_grib_new_from_file(f)
            if gid is None:
                break
            try:
                short = str(codes_get(gid, "shortName"))
                try:
                    level = int(codes_get(gid, "level"))
                except Exception:
                    level = 0
                nearest = codes_grib_find_nearest(gid, lat, lon)[0]
                value = float(getattr(nearest, "value"))
                nlat = float(getattr(nearest, "lat", lat))
                nlon = float(getattr(nearest, "lon", lon))
                fields[(short, level)] = (value, nlat, nlon)
            finally:
                codes_release(gid)
    return fields


def finite(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except Exception:
        return None


def parcel_json(pcl):
    if pcl is None:
        return {}
    out = {}
    for name in (
        "bplus", "bminus", "lclhght", "lfchght", "elhght", "lclpres", "lfcpres", "elpres",
        "brn", "brnshear", "brnu", "brnv", "li5", "li", "mplume", "mupcl", "bplus3km",
    ):
        if hasattr(pcl, name):
            out[name] = finite(getattr(pcl, name))
    return out


def vec_json(value):
    try:
        return [finite(value[0]), finite(value[1])]
    except Exception:
        return [None, None]


def build_variables(prof, lat, lon, label, run_dt, fh, levels):
    # Values are computed by SHARPpy here. The browser receives this JSON and does
    # not calculate thermodynamic or kinematic parameters itself.
    attrs = {}
    for name in (
        "pwv", "pwat", "k_idx", "t_totals", "c_totals", "v_totals", "sig_severe",
        "dcape", "convective_temp", "max_temp", "mean_mixratio", "low_level_rh", "mid_level_rh",
        "lapserate_700_500", "lapserate_850_500", "lapserate_0_3km", "lapserate_0_6km",
        "esrh", "critical_angle", "srh1km", "srh3km", "ebwspd", "right_critical_angle",
        "left_critical_angle", "ebottom", "etop", "pblhght", "freezinglevel", "meltlevel",
        "sfc_1km_shear", "sfc_3km_shear", "sfc_6km_shear", "srwind", "srw_eff", "srw_1km",
        "srw_3km", "srw_6km", "srw_8km", "srw_lcl_el", "mean_eff", "mean_ebw", "eff_shear",
        "ebwd", "ebwspd", "right_esrh", "left_esrh", "right_srw_eff", "left_srw_eff",
        "right_srw_1km", "right_srw_3km", "right_srw_6km", "right_srw_8km", "right_srw_lcl_el",
        "left_srw_1km", "left_srw_3km", "left_srw_6km", "left_srw_8km", "left_srw_lcl_el",
    ):
        if hasattr(prof, name):
            value = getattr(prof, name)
            if isinstance(value, (tuple, list, np.ndarray)):
                try:
                    attrs[name] = [finite(x) for x in value]
                except Exception:
                    attrs[name] = None
            else:
                attrs[name] = finite(value)

    level_rows = []
    for i in range(len(prof.pres)):
        level_rows.append({
            "pressure_hpa": finite(prof.pres[i]),
            "height_m": finite(prof.hght[i]),
            "temperature_c": finite(prof.tmpc[i]),
            "dewpoint_c": finite(prof.dwpc[i]),
            "wind_u_kt": finite(prof.u[i]),
            "wind_v_kt": finite(prof.v[i]),
            "wind_direction_deg": finite(prof.wdir[i]),
            "wind_speed_kt": finite(prof.wspd[i]),
            "omega_pa_s": finite(prof.omeg[i]) if hasattr(prof, "omeg") else None,
        })

    valid = run_dt + dt.timedelta(hours=fh)
    return {
        "schema": "sideral-skewt-sharppy-v2",
        "model": "ECMWF IFS",
        "resolution": "0.25°",
        "renderer": "SHARPpy",
        "browser_rendering": False,
        "latitude": lat,
        "longitude": lon,
        "location": label,
        "run_utc": run_dt.strftime("%Y-%m-%d %HZ"),
        "forecast_hour": fh,
        "valid_utc": valid.strftime("%Y-%m-%d %H:%MZ"),
        "pressure_levels_hpa": levels,
        "parcels": {
            "surface": parcel_json(getattr(prof, "sfcpcl", None)),
            "mixed_layer": parcel_json(getattr(prof, "mlpcl", None)),
            "most_unstable": parcel_json(getattr(prof, "mupcl", None)),
            "forecast": parcel_json(getattr(prof, "fcstpcl", None)),
        },
        "variables": attrs,
        "storm_motion": {
            "bunkers": [finite(x) for x in getattr(prof, "srwind", [])] if hasattr(prof, "srwind") else [],
            "effective_bottom_m": finite(getattr(prof, "ebottom", None)),
            "effective_top_m": finite(getattr(prof, "etop", None)),
        },
        "profile": level_rows,
    }


def render_with_sharppy(prof, out_dir: Path, meta: dict):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide2.QtWidgets import QApplication, QWidget, QHBoxLayout, QVBoxLayout, QLabel
    from sharppy.sharptab.prof_collection import ProfCollection
    from sharppy.viz.skew import plotSkewT
    from sharppy.viz.hodo import plotHodo

    app = QApplication.instance() or QApplication([])
    pc = ProfCollection({"ECMWF IFS": [prof]}, [prof.date], highlight="ECMWF IFS", location=meta["location"])
    root = QWidget()
    root.setStyleSheet("background:#000;color:#fff;")
    root.resize(1600, 980)
    layout = QVBoxLayout(root)
    layout.setContentsMargins(8, 8, 8, 8)
    layout.setSpacing(6)
    header = QLabel(f"SIDERAL SKEW-T  |  ECMWF IFS 0.25°  |  {meta['location']}  |  F{meta['fh']:03d}  |  VALID {meta['valid']}")
    header.setStyleSheet("color:white;background:#000;font:700 16px Consolas;padding:8px;border-bottom:1px solid #444;")
    layout.addWidget(header)
    top = QWidget()
    top_layout = QHBoxLayout(top)
    top_layout.setContentsMargins(0, 0, 0, 0)
    top_layout.setSpacing(6)
    skew = plotSkewT(plot_omega=True)
    hodo = plotHodo()
    skew.setMinimumSize(1000, 650)
    hodo.setMinimumSize(520, 650)
    top_layout.addWidget(skew, 2)
    top_layout.addWidget(hodo, 1)
    layout.addWidget(top, 1)
    bottom = QLabel("ECMWF IFS • SHARPpy • Sideral Meteorologia")
    bottom.setStyleSheet("color:white;background:#050505;font:11px Consolas;padding:6px;border:1px solid #333;")
    layout.addWidget(bottom)
    root.show()
    app.processEvents()
    skew.addProfileCollection(pc)
    hodo.addProfileCollection(pc)
    app.processEvents()

    # The three images are produced by SHARPpy/Qt. The web page only displays them.
    skew.grab().save(str(out_dir / "skewt.png"), "PNG")
    hodo.grab().save(str(out_dir / "hodograph.png"), "PNG")
    root.grab().save(str(out_dir / "full.png"), "PNG")
    root.close()
    app.processEvents()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lat", type=float, required=True)
    ap.add_argument("--lon", type=float, required=True)
    ap.add_argument("--label", default="Brasil")
    ap.add_argument("--out", default="skewt-out")
    ap.add_argument("--hours", default=','.join(str(h) for h in range(0, 73, 3)))
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    hours = [int(x) for x in args.hours.split(',') if x.strip()]

    # Use the public cloud mirror instead of hammering data.ecmwf.int directly.
    # ECMWF documents AWS/Azure/GCP mirrors for reliability and load reduction.
    source = os.getenv("ECMWF_SOURCE", "aws")
    client = Client(
        source=source,
        model="ifs",
        resol="0p25",
        maximum_retries=8,
        retry_after=(10, 90, 2),
        use_server_retry_after=True,
    )

    probe = out / "_probe.grib2"
    probe_result = client.retrieve(type="fc", step=hours[0], levtype="pl", levelist=850, param="t", target=str(probe))
    run_dt = probe_result.datetime.replace(tzinfo=None)
    probe.unlink(missing_ok=True)

    # Surface geopotential is a step-0 field; use it for all forecast hours.
    oro = out / "oro.grib2"
    atomic_retrieve(client, oro, date=run_dt.strftime("%Y-%m-%d"), time=run_dt.hour,
                    type="fc", stream="oper", step=0, levtype="sfc", param="z")
    oro_fields = read_fields(oro, args.lat, args.lon)

    frames = []
    metas = []
    for fh in hours:
        stem = f"f{fh:03d}"
        pl = out / f"{stem}_pl.grib2"
        sf = out / f"{stem}_sfc.grib2"
        common = dict(
            date=run_dt.strftime("%Y-%m-%d"),
            time=run_dt.hour,
            type="fc",
            stream="oper",
            step=fh,
        )
        # No area/grid keywords: ECMWF Open Data does not support MARS spatial
        # subsetting in this client. We download the published global subset and
        # extract the nearest grid point locally with ecCodes.
        atomic_retrieve(client, pl, **common, levtype="pl", levelist=LEVELS, param=PL_PARAMS)
        atomic_retrieve(client, sf, **common, levtype="sfc", param=SFC_PARAMS)

        fields = read_fields(pl, args.lat, args.lon)
        sflds = read_fields(sf, args.lat, args.lon)
        sflds.update(oro_fields)

        pres, hght, tmp, dwpt, u, v, omega = [], [], [], [], [], [], []
        sp = sflds.get(("sp", 0), (None, args.lat, args.lon))[0]
        sfc_t = sflds.get(("2t", 0), (None, args.lat, args.lon))[0]
        sfc_td = sflds.get(("2d", 0), (None, args.lat, args.lon))[0]
        sfc_z = sflds.get(("z", 0), (None, args.lat, args.lon))[0]
        psfc = float(sp) / 100.0 if sp is not None else 1000.0
        sfc_h = float(sfc_z) / 9.80665 if sfc_z is not None else 0.0
        if sfc_t is not None and sfc_td is not None:
            pres.append(psfc); hght.append(sfc_h)
            tmp.append(float(sfc_t) - 273.15); dwpt.append(float(sfc_td) - 273.15)
            u.append(float(sflds.get(("10u", 0), (0, 0, 0))[0] or 0) * 1.943844)
            v.append(float(sflds.get(("10v", 0), (0, 0, 0))[0] or 0) * 1.943844)
            omega.append(np.nan)

        for lev in LEVELS:
            if lev > psfc + 1.0:
                continue
            t = fields.get(("t", lev)); r = fields.get(("r", lev)); gh = fields.get(("gh", lev))
            uu = fields.get(("u", lev)); vv = fields.get(("v", lev)); ww = fields.get(("w", lev))
            if not all(x is not None for x in (t, r, gh, uu, vv)):
                continue
            tc = float(t[0]) - 273.15
            rh = max(0.1, min(100.0, float(r[0])))
            a, b = 17.625, 243.04
            gamma = math.log(rh / 100.0) + (a * tc) / (b + tc)
            td = b * gamma / (a - gamma)
            pres.append(float(lev)); hght.append(float(gh[0]))
            tmp.append(tc); dwpt.append(td)
            u.append(float(uu[0]) * 1.943844); v.append(float(vv[0]) * 1.943844)
            omega.append(float(ww[0]) if ww is not None else np.nan)

        order = np.argsort(np.asarray(pres))[::-1]
        pres = np.asarray(pres)[order]; hght = np.asarray(hght)[order]
        tmp = np.asarray(tmp)[order]; dwpt = np.asarray(dwpt)[order]
        u = np.asarray(u)[order]; v = np.asarray(v)[order]; omega = np.asarray(omega)[order]
        if len(pres) < 5:
            raise RuntimeError(f"Perfil insuficiente no ponto {args.lat},{args.lon}: apenas {len(pres)} níveis válidos.")

        from sharppy.sharptab import profile as shp_profile
        prof = shp_profile.create_profile(
            profile="convective", pres=pres, hght=hght, tmpc=tmp, dwpc=dwpt,
            u=u, v=v, omeg=np.ma.masked_invalid(omega), strictQC=False,
            latitude=args.lat, date=run_dt + dt.timedelta(hours=fh), location="SID",
        )

        meta = {
            "location": args.label, "fh": fh,
            "valid": (run_dt + dt.timedelta(hours=fh)).strftime("%Y-%m-%d %H:%MZ"),
        }
        render_dir = out / stem
        render_dir.mkdir(exist_ok=True)
        render_with_sharppy(prof, render_dir, meta)
        variables = build_variables(prof, args.lat, args.lon, args.label, run_dt, fh, LEVELS)
        (render_dir / "variables.json").write_text(json.dumps(variables, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
        frames.append(render_dir / "full.png")
        metas.append(variables)
        for x in (pl, sf):
            x.unlink(missing_ok=True)

    from PIL import Image
    ims = [Image.open(p).convert("P", palette=Image.Palette.ADAPTIVE, colors=256) for p in frames]
    ims[0].save(out / "animation.gif", save_all=True, append_images=ims[1:], duration=700, loop=0, optimize=False)

    # Latest product is copied from the real SHARPpy frame, never rendered by HTML.
    latest = metas[0]
    (out / "latest.json").write_text(json.dumps(latest, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    shutil.copy2(out / "f000" / "full.png", out / "full.png")
    shutil.copy2(out / "f000" / "skewt.png", out / "skewt.png")
    shutil.copy2(out / "f000" / "hodograph.png", out / "hodograph.png")
    shutil.copy2(out / "f000" / "variables.json", out / "variables.json")

    # Flatten frame products for the existing Sideral UI.
    for fh in hours:
        stem = f"f{fh:03d}"
        for name in ("full.png", "skewt.png", "hodograph.png", "variables.json"):
            shutil.copy2(out / stem / name, out / f"{stem}_{name}")


if __name__ == "__main__":
    main()
