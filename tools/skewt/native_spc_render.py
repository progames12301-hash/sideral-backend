"""White SPC-style multi-panel Skew-T renderer for Sideral SHARPpy profiles.

The meteorological profile and parcel diagnostics are supplied by SHARPpy. This
module only draws the sounding and the associated diagnostic panels.
"""
from __future__ import annotations

from datetime import timedelta
from pathlib import Path
import math

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.collections import LineCollection
from matplotlib.ticker import FixedLocator, FuncFormatter

W, H, DPI = 1344, 1152, 100
SKEW = 15.5
RED = "#ff2020"
BLUE = "#063cff"
GREEN = "#168b32"
INK = "#101010"


def _arr(value):
    try:
        a = np.ma.asarray(value, dtype=float).filled(np.nan)
        return np.asarray(a, dtype=float).reshape(-1)
    except Exception:
        return np.asarray([], dtype=float)


def _clean_profile(prof):
    p = _arr(getattr(prof, "pres", []))
    t = _arr(getattr(prof, "tmpc", []))
    td = _arr(getattr(prof, "dwpc", []))
    z = _arr(getattr(prof, "hght", []))
    u = _arr(getattr(prof, "u", []))
    v = _arr(getattr(prof, "v", []))
    om = _arr(getattr(prof, "omeg", []))
    n = min(map(len, (p, t, td, z, u, v))) if all(len(x) for x in (p, t, td, z, u, v)) else 0
    if n < 2:
        raise RuntimeError("Perfil SHARPpy insuficiente para desenhar o Skew-T")
    p, t, td, z, u, v = (x[:n] for x in (p, t, td, z, u, v))
    om = om[:n] if len(om) >= n else np.full(n, np.nan)
    valid = np.isfinite(p) & (p > 0) & np.isfinite(t) & np.isfinite(td) & np.isfinite(z) & np.isfinite(u) & np.isfinite(v)
    p, t, td, z, u, v, om = (x[valid] for x in (p, t, td, z, u, v, om))
    order = np.argsort(p)[::-1]
    p, t, td, z, u, v, om = (x[order] for x in (p, t, td, z, u, v, om))
    keep = np.r_[True, np.abs(np.diff(p)) > 0.01]
    p, t, td, z, u, v, om = (x[keep] for x in (p, t, td, z, u, v, om))
    return p, t, td, z, u, v, om


def _xskew(temp_c, pressure_hpa):
    p = np.maximum(np.asarray(pressure_hpa, dtype=float), 1.0)
    return np.asarray(temp_c, dtype=float) + SKEW * np.log(1000.0 / p)


def _interp_pressure(values, pressure, target):
    values, pressure = np.asarray(values, dtype=float), np.asarray(pressure, dtype=float)
    good = np.isfinite(values) & np.isfinite(pressure) & (pressure > 0)
    if good.sum() < 2:
        return np.nan
    idx = np.argsort(pressure[good])
    return float(np.interp(np.log(target), np.log(pressure[good][idx]), values[good][idx]))


def _interp_height(values, height, target):
    values, height = np.asarray(values, dtype=float), np.asarray(height, dtype=float)
    good = np.isfinite(values) & np.isfinite(height)
    if good.sum() < 2:
        return np.nan
    idx = np.argsort(height[good])
    return float(np.interp(target, height[good][idx], values[good][idx]))


def _finite(value):
    try:
        if value is None or np.ma.is_masked(value):
            return None
        result = float(value)
        return result if math.isfinite(result) else None
    except Exception:
        return None


def _fmt(value, digits=1, missing="--"):
    value = _finite(value)
    return missing if value is None else f"{value:.{digits}f}"


def _attr(obj, names, digits=1, suffix="", missing="--"):
    for name in names:
        value = _finite(getattr(obj, name, None))
        if value is not None:
            return f"{value:.{digits}f}{suffix}"
    return missing


def _vapor_pressure_from_td(td_c):
    return 6.112 * np.exp((17.67 * td_c) / (td_c + 243.5))


def _theta_profiles(p, t, td):
    tk, tdk = t + 273.15, td + 273.15
    theta = tk * (1000.0 / p) ** 0.2854
    e = _vapor_pressure_from_td(td)
    r = 0.622 * e / np.maximum(p - e, 0.1)
    with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
        tlcl = 1.0 / (1.0 / np.maximum(tdk - 56.0, 1.0) + np.log(np.maximum(tk, 1.0) / np.maximum(tdk, 1.0)) / 800.0) + 56.0
        thetae = theta * np.exp((3376.0 / np.maximum(tlcl, 1.0) - 2.54) * r * (1.0 + 0.81 * r))
        es = _vapor_pressure_from_td(t)
        rs = 0.622 * es / np.maximum(p - es, 0.1)
        thetaes = theta * np.exp((3376.0 / np.maximum(tk, 1.0) - 2.54) * rs * (1.0 + 0.81 * rs))
    return theta, thetae, thetaes


def _parcel_curve(p, t, td):
    try:
        from sharppy.sharptab import thermo
        ps, ts, tds = float(p[0]), float(t[0]), float(td[0])
        lclp, lclt = thermo.drylift(ps, ts, tds)
        lclp, lclt = float(lclp), float(lclt)
        result = np.full_like(p, np.nan, dtype=float)
        for i, pi in enumerate(p):
            if pi >= lclp:
                result[i] = (ts + 273.15) * (pi / ps) ** 0.2854 - 273.15
            else:
                result[i] = float(thermo.wetlift(lclp, lclt, float(pi)))
        return result
    except Exception:
        return np.full_like(p, np.nan, dtype=float)


def _storm_motion(h_agl, u, v):
    z, uu, vv = np.asarray(h_agl, dtype=float), np.asarray(u, dtype=float), np.asarray(v, dtype=float)
    good = np.isfinite(z) & np.isfinite(uu) & np.isfinite(vv)
    if good.sum() < 2:
        return (0.0, 0.0), (0.0, 0.0), (0.0, 0.0)
    order = np.argsort(z[good])
    z, uu, vv = z[good][order], uu[good][order], vv[good][order]
    zz = np.linspace(0, min(6000.0, max(1000.0, float(np.nanmax(z)))), 40)
    mw = (float(np.mean(np.interp(zz, z, uu))), float(np.mean(np.interp(zz, z, vv))))
    u0, v0 = _interp_height(uu, z, 0), _interp_height(vv, z, 0)
    u6, v6 = _interp_height(uu, z, 6000), _interp_height(vv, z, 6000)
    du, dv = u6 - u0, v6 - v0
    mag = math.hypot(du, dv)
    if not math.isfinite(mag) or mag < 0.1:
        return mw, mw, mw
    offset = 14.58
    right = (mw[0] + offset * dv / mag, mw[1] - offset * du / mag)
    left = (mw[0] - offset * dv / mag, mw[1] + offset * du / mag)
    return mw, right, left


def _srh(h_agl, u, v, lower, upper, storm):
    z, uu, vv = np.asarray(h_agl), np.asarray(u), np.asarray(v)
    good = np.isfinite(z) & np.isfinite(uu) & np.isfinite(vv)
    if good.sum() < 2:
        return np.nan
    order = np.argsort(z[good])
    z, uu, vv = z[good][order], uu[good][order], vv[good][order]
    sample_z = np.unique(np.r_[lower, z[(z >= lower) & (z <= upper)], upper])
    sample_z = sample_z[(sample_z >= z.min()) & (sample_z <= z.max())]
    if len(sample_z) < 2:
        return np.nan
    ui, vi = np.interp(sample_z, z, uu), np.interp(sample_z, z, vv)
    su, sv = storm
    srh_kt2 = np.sum((ui[:-1] - su) * np.diff(vi) - (vi[:-1] - sv) * np.diff(ui))
    return float(srh_kt2 * (0.514444 ** 2))


def _shear(h_agl, u, v, layer):
    u0, v0 = _interp_height(u, h_agl, 0), _interp_height(v, h_agl, 0)
    u1, v1 = _interp_height(u, h_agl, layer), _interp_height(v, h_agl, layer)
    if not all(math.isfinite(x) for x in (u0, v0, u1, v1)):
        return np.nan
    return math.hypot(u1 - u0, v1 - v0)


def _lapse_height(t, h, low, high):
    a, b = _interp_height(t, h, low), _interp_height(t, h, high)
    if not math.isfinite(a) or not math.isfinite(b) or high <= low:
        return np.nan
    return (a - b) / ((high - low) / 1000.0)


def _lapse_pressure(t, h, p, p_low, p_high):
    t1, t2 = _interp_pressure(t, p, p_low), _interp_pressure(t, p, p_high)
    h1, h2 = _interp_pressure(h, p, p_low), _interp_pressure(h, p, p_high)
    if not all(math.isfinite(x) for x in (t1, t2, h1, h2)) or h2 <= h1:
        return np.nan
    return (t1 - t2) / ((h2 - h1) / 1000.0)


def _pwat_mm(p, td):
    e = _vapor_pressure_from_td(td)
    q = 0.622 * e / np.maximum(p - e, 0.1)
    order = np.argsort(p)
    pp, qq = np.asarray(p)[order] * 100.0, np.asarray(q)[order]
    if len(pp) < 2:
        return np.nan
    return float(abs(np.trapz(qq, pp)) / 9.80665)


def _make_skew_axes(ax, p, t, td, z, u, v, pcl, station, date_text):
    ax.set_yscale("log")
    ax.set_ylim(1050, 100)
    ax.set_xlim(-43, 57)
    ax.set_facecolor("white")
    ax.spines[:].set_color(INK)
    ax.spines[:].set_linewidth(0.85)
    ax.tick_params(axis="both", labelsize=8, colors=INK, direction="out", length=3, pad=2)
    ax.set_xlabel("Temperature (°C)", fontsize=9, labelpad=4)
    ax.set_ylabel("Pressure (hPa)", fontsize=9, labelpad=8)
    p_ticks = [1000, 900, 800, 700, 600, 500, 400, 300, 200, 100]
    ax.yaxis.set_major_locator(FixedLocator(p_ticks))
    ax.yaxis.set_major_formatter(FuncFormatter(lambda val, pos: f"{int(val)}" if val in p_ticks else ""))
    ax.set_xticks(np.arange(-40, 56, 10))
    ps = np.geomspace(100, 1050, 150)
    for pp in p_ticks:
        ax.axhline(pp, color="#b8b8b8", lw=0.65, zorder=0)
    for pp in [950, 850, 750, 650, 550, 450, 350, 250, 150]:
        ax.axhline(pp, color="#e0e0e0", lw=0.45, zorder=0)
    for temp in np.arange(-80, 71, 10):
        ax.plot(_xskew(np.full_like(ps, temp), ps), ps, ls=(0, (2, 3)),
                color="#9e9e9e" if temp % 20 else "#6c8ccd", lw=0.52, alpha=0.75, zorder=0)
    for theta_k in np.arange(260, 451, 10):
        tt = theta_k * (ps / 1000.0) ** 0.2854 - 273.15
        ax.plot(_xskew(tt, ps), ps, color="#c49a5a", ls=(0, (2, 3)), lw=0.58, alpha=0.8, zorder=0)
    try:
        from sharppy.sharptab import thermo
        pp_grid = np.geomspace(1000, 100, 70)
        for start_t in [-10, 0, 10, 20, 30, 40]:
            curve = np.full_like(pp_grid, np.nan)
            for i, pi in enumerate(pp_grid):
                curve[i] = float(thermo.wetlift(1000.0, float(start_t), float(pi)))
            ax.plot(_xskew(curve, pp_grid), pp_grid, color="#64a878", ls=(0, (2, 3)), lw=0.58, alpha=0.77, zorder=0)
    except Exception:
        pass
    for r in [0.4, 1, 2, 4, 8, 12, 16, 24, 32]:
        e = (r * ps) / (622.0 + r)
        loge = np.log(np.maximum(e, 0.01) / 6.112)
        tdline = 243.5 * loge / (17.67 - loge)
        ax.plot(_xskew(tdline, ps), ps, color="#86b68e", ls=(0, (1, 3)), lw=0.55, alpha=0.72, zorder=0)
    h_agl = z - z[0]
    for pp in [1000, 900, 800, 700, 600, 500, 400, 300, 200, 100]:
        height = _interp_pressure(h_agl, p, pp)
        if math.isfinite(height):
            ax.text(-41.8, pp, f"{height:.1f} m", ha="left", va="center", fontsize=6.6, color="#303030", clip_on=True)
    ax.plot(_xskew(t, p), p, color=INK, lw=1.65, zorder=5, label="T")
    ax.plot(_xskew(td, p), p, color=BLUE, lw=1.85, zorder=6, label="Td")
    parcel = _parcel_curve(p, t, td)
    if np.isfinite(parcel).sum() > 3:
        ax.plot(_xskew(parcel, p), p, color=RED, lw=1.25, ls="--", zorder=4)
    parcel_objects = [getattr(pcl, n, None) for n in ("sfcpcl", "mlpcl", "mupcl", "fcstpcl")]
    sfc = next((x for x in parcel_objects if x is not None), None)
    if sfc is not None:
        for attr, short in (("lclpres", "LCL"), ("lfcpres", "LFC"), ("elpres", "EL")):
            pp = _finite(getattr(sfc, attr, None))
            if pp is not None and 100 <= pp <= 1050:
                ax.axhline(pp, color="#333333", lw=0.55, ls=(0, (3, 3)), alpha=0.65)
                ax.text(56.2, pp, short, fontsize=7, ha="left", va="center", color="#555555", clip_on=False)
    idx = np.unique(np.linspace(0, len(p) - 1, min(22, len(p))).round().astype(int))
    ax.barbs(np.full(len(idx), 50.4), p[idx], u[idx], v[idx], length=4.4,
             linewidth=0.55, barb_increments={"half": 5, "full": 10, "flag": 50},
             pivot="middle", color=INK, zorder=7)
    ax.set_title(f"Station: {station}\nDate: {date_text}", fontsize=10.5, loc="left", pad=6, color=INK)


def _make_theta_axes(ax, p, t, td):
    theta, thetae, thetaes = _theta_profiles(p, t, td)
    ax.set_yscale("log"); ax.set_ylim(1050, 100); ax.set_xlim(245, 380)
    ax.set_facecolor("white")
    ax.spines[:].set_color(INK); ax.spines[:].set_linewidth(0.85)
    ax.tick_params(axis="both", labelsize=7, colors=INK, direction="out", length=3, pad=2)
    ax.set_xticks([245, 270, 295, 320, 345, 370])
    ax.set_yticks([1000, 900, 800, 700, 600, 500, 400, 300, 200, 100])
    ax.set_yticklabels(["1000", "900", "800", "700", "600", "500", "400", "300", "200", "100"])
    for pp in [1000,900,800,700,600,500,400,300,200,100]:
        ax.axhline(pp, color="#dddddd", lw=0.48, zorder=0)
    ax.plot(theta, p, color=GREEN, lw=1.3, label=r"$\theta$")
    ax.plot(thetae, p, color=RED, lw=1.2, label=r"$\theta_e$")
    ax.plot(thetaes, p, color=BLUE, lw=1.2, label=r"$\theta_{es}$")
    leg = ax.legend(loc="upper left", fontsize=7, frameon=True, framealpha=0.9, borderpad=0.25, handlelength=2)
    leg.get_frame().set_facecolor("white"); leg.get_frame().set_edgecolor("#bcbcbc")


def _make_hodo(ax, z, u, v, station_text, motion):
    h_agl = z - z[0]
    ax.set_facecolor("white"); ax.set_aspect("equal", adjustable="box")
    ax.set_xlim(-60, 60); ax.set_ylim(-60, 60)
    ax.spines[:].set_color(INK); ax.spines[:].set_linewidth(0.9)
    ax.set_xticks([]); ax.set_yticks([])
    for r in [10, 20, 30, 40, 50, 60]:
        ax.add_patch(plt.Circle((0, 0), r, fill=False, lw=0.8 if r % 20 == 0 else 0.45,
                                edgecolor="#4c4c4c" if r % 20 == 0 else "#c9c9c9", zorder=0))
        if r in (10, 30, 50):
            ax.text(-r, 1.5, f"{r}", fontsize=7, color="#bdbdbd", ha="center", va="bottom")
            ax.text(1.8, r, f"{r}", fontsize=7, color="#bdbdbd", ha="left", va="center")
    ax.axhline(0, color="#5c5c5c", lw=0.9, zorder=1); ax.axvline(0, color="#5c5c5c", lw=0.9, zorder=1)
    order = np.argsort(h_agl)
    hh, uu, vv = h_agl[order], u[order], v[order]
    pts = np.column_stack((uu, vv)).reshape(-1, 1, 2)
    if len(pts) >= 2:
        segments = np.concatenate([pts[:-1], pts[1:]], axis=1)
        mids = (hh[:-1] + hh[1:]) / 2.0
        bounds = [0, 2000, 4000, 9000, 11000, np.inf]
        colors = ["#003cff", "#a900d8", "#ff8a00", "#ff3030", "#ff3030"]
        seg_colors = [colors[min(np.searchsorted(bounds[1:], hz, side="right"), len(colors)-1)] for hz in mids]
        ax.add_collection(LineCollection(segments, colors=seg_colors, linewidths=1.65, zorder=4))
        for target, label in [(1000, "1"), (3000, "3"), (6000, "6"), (9000, "9"), (12000, "12")]:
            if hh.min() <= target <= hh.max():
                px, py = np.interp(target, hh, uu), np.interp(target, hh, vv)
                ax.plot(px, py, "o", ms=2.5, color="#111111", zorder=5)
                ax.text(px + 1.3, py + 1.3, label, fontsize=7, weight="bold", color=INK, zorder=6)
    mw, rm, lm = motion
    for sx, sy, label in [(lm[0], lm[1], "LM"), (mw[0], mw[1], "MW"), (rm[0], rm[1], "RM")]:
        ax.annotate("", xy=(sx, sy), xytext=(0, 0),
                    arrowprops=dict(arrowstyle="-|>", lw=1.0, color="#c3c3c3", shrinkA=0, shrinkB=0), zorder=2)
        ax.text(sx + 2.5, sy + 2.0, label, fontsize=8, weight="bold", color=INK, zorder=6)
    ax.set_title(station_text, fontsize=9.2, loc="left", pad=6, color=INK)
    ax.text(0.0, 0.01, "[kt]\nCritical Angle: --", transform=ax.transAxes, fontsize=8.5,
            ha="left", va="bottom", color=INK)


def _make_advection(ax, p, t, omega):
    ax.set_facecolor("white"); ax.set_yscale("log"); ax.set_ylim(1050, 100); ax.set_xlim(-5, 5)
    ax.spines[:].set_color(INK); ax.spines[:].set_linewidth(0.85)
    ax.tick_params(axis="both", labelsize=7, colors=INK, direction="out", length=3, pad=2)
    ax.set_xlabel("Inf. Temp.\nAdvec. (C/hr)", fontsize=8.5, labelpad=3)
    ax.set_xticks([-5, -2.5, 0, 2.5, 5])
    ax.set_yticks([1000,900,800,700,600,500,400,300,200,100])
    ax.set_yticklabels(["1000","900","800","700","600","500","400","300","200","100"])
    ax.axvline(0, color=INK, lw=1.0)
    for pp in [1000,800,600,400,200,100]:
        ax.axhline(pp, color="#e3e3e3", lw=0.45, zorder=0)
    valid = np.isfinite(p) & np.isfinite(t) & np.isfinite(omega)
    if valid.sum() >= 4:
        pp, tt, om = p[valid], t[valid], omega[valid]
        order = np.argsort(pp)[::-1]; pp, tt, om = pp[order], tt[order], om[order]
        dp_pa = np.gradient(pp * 100.0)
        dt_dp = np.divide(np.gradient(tt), dp_pa, out=np.zeros_like(tt), where=np.abs(dp_pa) > 0)
        adv = np.clip(-om * dt_dp * 3600.0, -5, 5)
        good = np.isfinite(adv) & (pp >= 100) & (pp <= 1050)
        if good.sum() >= 3:
            ax.fill_betweenx(pp[good], 0, adv[good], color=BLUE, alpha=0.95, linewidth=0, zorder=2)
            ax.plot(adv[good], pp[good], color=BLUE, lw=0.65, zorder=3)


def _make_srw(ax, h_agl, u, v, rm):
    ax.set_facecolor("white"); ax.set_xlim(0, 50); ax.set_ylim(0, 16)
    ax.spines[:].set_color(INK); ax.spines[:].set_linewidth(0.85)
    ax.tick_params(axis="both", labelsize=7.5, colors=INK, direction="out", length=3, pad=2)
    ax.set_ylabel("Height (km)", fontsize=8, labelpad=3)
    ax.set_xticks([0, 10, 20, 30, 40, 50]); ax.set_yticks([0,2,4,6,8,10,12,14,16])
    for hz in [2,4,6,8,10,12,14,16]:
        ax.axhline(hz, color="#e4e4e4", lw=0.42, zorder=0)
    rel = np.hypot(u - rm[0], v - rm[1])
    good = np.isfinite(h_agl) & np.isfinite(rel)
    if good.sum() >= 2:
        order = np.argsort(h_agl[good]); hh, rr = h_agl[good][order] / 1000.0, rel[good][order]
        pts = np.column_stack((rr, hh)).reshape(-1, 1, 2)
        segments = np.concatenate([pts[:-1], pts[1:]], axis=1)
        mids = (hh[:-1] + hh[1:]) / 2
        color_bands = ["#ff8a00", "#003cff", "#a900d8", "#ff3030"]
        seg_colors = [color_bands[0 if h <= 2 else 1 if h <= 4 else 2 if h <= 9 else 3] for h in mids]
        ax.add_collection(LineCollection(segments, colors=seg_colors, linewidths=1.7, zorder=3))
    ax.set_title("Storm Relative Wind", fontsize=8.5, loc="left", pad=4, color=INK)
    ax.legend(handles=[
        plt.Line2D([0],[0], color="#ff8a00", lw=1.2, label="0-2 km"),
        plt.Line2D([0],[0], color="#003cff", lw=1.2, label="2-4 km"),
        plt.Line2D([0],[0], color="#a900d8", lw=1.2, label="4-9 km"),
        plt.Line2D([0],[0], color="#ff3030", lw=1.2, label="Wind"),
    ], loc="upper left", fontsize=5.8, frameon=True, framealpha=0.8, borderpad=0.2, handlelength=1.7, labelspacing=0.15)


def _parcel_rows(prof, srh01, srh03):
    rows = []
    for label, attr in [("SFC", "sfcpcl"), ("ML", "mlpcl"), ("MU", "mupcl"), ("FCST", "fcstpcl")]:
        pcl = getattr(prof, attr, None)
        if pcl is None:
            rows.append([label, "--", "--", "--", "--", "--", "--", "--", "--"])
            continue
        cape = _finite(getattr(pcl, "bplus", None))
        cin = _finite(getattr(pcl, "bminus", None))
        lcl = _finite(getattr(pcl, "lclhght", None))
        lfc = _finite(getattr(pcl, "lfchght", None))
        el = _finite(getattr(pcl, "elhght", None))
        li = _finite(getattr(pcl, "li5", None))
        ehi1 = cape * srh01 / 160000.0 if cape is not None and math.isfinite(srh01) else None
        ehi3 = cape * srh03 / 160000.0 if cape is not None and math.isfinite(srh03) else None
        rows.append([label, _fmt(cape, 2), _fmt(cin, 2), _fmt(lcl, 2), _fmt(lfc, 2), _fmt(el, 2), _fmt(li, 2), _fmt(ehi1, 2), _fmt(ehi3, 2)])
    return rows


def _bottom_diagnostics(fig, prof, p, t, td, z, u, v, srh01, srh03, srh06, shear01, shear03, shear06, hodo_motion):
    ax = fig.add_axes([0.035, 0.073, 0.63, 0.115]); ax.axis("off")
    cols = ["", "CAPE [J/Kg]", "CINe [J/Kg]", "LCL [m AGL]", "LFC [m AGL]", "EL [m AGL]", "LI [°C]", "EHI 0-1", "EHI 0-3"]
    table = ax.table(cellText=_parcel_rows(prof, srh01, srh03), colLabels=cols, cellLoc="center", colLoc="center", loc="upper left",
                     colWidths=[0.07,0.13,0.13,0.12,0.12,0.12,0.08,0.10,0.10])
    table.auto_set_font_size(False); table.set_fontsize(7.3); table.scale(1, 1.26)
    for (r,c), cell in table.get_celld().items():
        cell.set_edgecolor("white"); cell.set_linewidth(0.0); cell.set_facecolor("white")
        cell.get_text().set_color(INK)
        if r == 0:
            cell.get_text().set_fontweight("bold")

    h_agl = z - z[0]
    lapse03, lapse36 = _lapse_height(t, h_agl, 0, 3000), _lapse_height(t, h_agl, 3000, 6000)
    lapse8505, lapse7005 = _lapse_pressure(t, z, p, 850, 500), _lapse_pressure(t, z, p, 700, 500)
    pwat = _finite(getattr(prof, "pwat", None))
    if pwat is not None and pwat < 10: pwat *= 25.4
    if pwat is None: pwat = _pwat_mm(p, td)
    kidx = _finite(getattr(prof, "k_idx", None))
    if kidx is None:
        t850, t700, t500 = (_interp_pressure(t, p, pp) for pp in (850,700,500))
        td850, td700 = (_interp_pressure(td, p, pp) for pp in (850,700))
        if all(math.isfinite(x) for x in (t850,t700,t500,td850,td700)):
            kidx = t850 - t500 + td850 - t700 + td700
    sweat = _attr(prof, ["sweat", "sweat_idx"], 2)
    convt = _attr(prof, ["convective_temp", "ctemp"], 2)
    dcape = _attr(prof, ["dcape"], 2)
    sig = _attr(prof, ["sig_severe", "sigsevere"], 2)
    wndg = _attr(prof, ["wndg", "significant_tornado"], 2)
    effective_srh = _attr(prof, ["effective_srh", "esrh"], 2, suffix=" m²/s²")
    ebwd = _attr(prof, ["ebwd", "ebwspd"], 2, suffix=" kts")
    stp_fixed = _attr(prof, ["stp_fixed", "stp_fix"], 2)
    stp_cin = _attr(prof, ["stp_cin"], 2)
    ship = _attr(prof, ["ship"], 2)
    scp = _attr(prof, ["scp"], 2)
    microburst = _attr(prof, ["microburst", "mburst"], 2, missing="0")
    dcp_value = _attr(prof, ["dcp", "derecho_comp_param"], 3)
    left = [
        f"0-1 km SRH: {srh01:.2f} m²/s²" if math.isfinite(srh01) else "0-1 km SRH: --",
        f"0-1 km Shear: {shear01:.2f} kts" if math.isfinite(shear01) else "0-1 km Shear: --",
        f"0-3 km SRH: {srh03:.2f} m²/s²" if math.isfinite(srh03) else "0-3 km SRH: --",
        f"0-3 km Shear: {shear03:.2f} kts" if math.isfinite(shear03) else "0-3 km Shear: --",
        f"0-6 km Shear: {shear06:.2f} kts" if math.isfinite(shear06) else "0-6 km Shear: --",
    ]
    mid1 = [f"Eff. SRH: {effective_srh}", f"EBWD: {ebwd}", f"PWV: {_fmt(pwat,2)} mm",
            f"Convective Temp: {convt} °C", f"K-index: {_fmt(kidx,2)}"]
    mid2 = [f"STP(fix): {stp_fixed}", f"STP(cin): {stp_cin}", f"SHIP: {ship}", f"SCP: {scp}"]
    lapse = [f"Γ 0-3km: {_fmt(lapse03,2)} °C/km", f"Γ 3-6km: {_fmt(lapse36,2)} °C/km",
             f"Γ 850-500mb: {_fmt(lapse8505,2)} °C/km", f"Γ 700-500mb: {_fmt(lapse7005,2)} °C/km"]
    right = [f"DCAPE: {dcape} J/kg", f"SWEAT: {sweat}", f"µburst: {microburst}", f"dcp: {dcp_value}"]
    severe = [f"SigSevere: {sig} m³/s³", f"Wndg: {wndg}"]
    columns = [(0.037, left), (0.225, mid1), (0.392, mid2), (0.535, lapse), (0.700, right), (0.835, severe)]
    for x, items in columns:
        for j, label in enumerate(items):
            fig.text(x, 0.083 - j * 0.016, label, ha="left", va="center", fontsize=8.4, color=INK)


def render_native_spc(prof, out_dir: Path, meta: dict):
    """Render one SHARPpy profile as a classic white, multi-panel SPC-style PNG."""
    out_dir = Path(out_dir); out_dir.mkdir(parents=True, exist_ok=True)
    p, t, td, z, u, v, omega = _clean_profile(prof)
    h_agl = z - z[0]
    fh = int(meta.get("fh", 0))
    valid = getattr(prof, "date", None)
    if valid is None:
        valid = meta.get("valid")
    if hasattr(valid, "strftime"):
        valid_dt = valid
        valid_text = valid_dt.strftime("%Y%m%d%HZ")
        run_text = (valid_dt - timedelta(hours=fh)).strftime("%Y%m%d%HZ")
    else:
        valid_text = str(valid or "--")
        run_text = str(meta.get("run", valid_text))
    station = str(meta.get("station", meta.get("location", getattr(prof, "location", "SID"))) or "SID")
    latitude = _finite(meta.get("latitude", getattr(prof, "latitude", None)))
    longitude = _finite(meta.get("longitude", getattr(prof, "longitude", None)))
    lat_text = "--" if latitude is None else f"{latitude:.2f}{'N' if latitude >= 0 else 'S'}"
    lon_text = "--" if longitude is None else f"{longitude:.2f}{'E' if longitude >= 0 else 'W'}"
    elevation = _finite(meta.get("elevation", z[0]))
    if elevation is None: elevation = 0.0
    station_text = f"Sounding at location: {lat_text}, {lon_text}, {elevation:.0f} m and {len(p)} vertical levels"

    motion = _storm_motion(h_agl, u, v)
    srh01, srh03, srh06 = (_finite(getattr(prof, name, None)) for name in ("srh1km", "srh3km", "srh6km"))
    if srh01 is None: srh01 = _srh(h_agl, u, v, 0, 1000, motion[1])
    if srh03 is None: srh03 = _srh(h_agl, u, v, 0, 3000, motion[1])
    if srh06 is None: srh06 = _srh(h_agl, u, v, 0, 6000, motion[1])
    shear01, shear03, shear06 = (_shear(h_agl, u, v, h) for h in (1000, 3000, 6000))

    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 8, "axes.linewidth": 0.85, "savefig.facecolor": "white"})
    fig = plt.figure(figsize=(W / DPI, H / DPI), dpi=DPI, facecolor="white")
    ax_skew = fig.add_axes([0.078, 0.236, 0.402, 0.704])
    ax_theta = fig.add_axes([0.508, 0.236, 0.112, 0.704])
    ax_hodo = fig.add_axes([0.628, 0.550, 0.336, 0.390])
    ax_adv = fig.add_axes([0.650, 0.139, 0.142, 0.400])
    ax_srw = fig.add_axes([0.832, 0.139, 0.140, 0.400])

    _make_skew_axes(ax_skew, p, t, td, z, u, v, prof, station, f"{run_text} - {valid_text}")
    _make_theta_axes(ax_theta, p, t, td)
    _make_hodo(ax_hodo, z, u, v, station_text, motion)
    _make_advection(ax_adv, p, t, omega)
    _make_srw(ax_srw, h_agl, u, v, motion[1])
    _bottom_diagnostics(fig, prof, p, t, td, z, u, v, srh01, srh03, srh06, shear01, shear03, shear06, motion)

    fig.text(0.935, 0.045, "SIDERAL", ha="center", va="center", fontsize=10, weight="bold", color="#165e99")
    fig.text(0.935, 0.029, "METEOROLOGIA", ha="center", va="center", fontsize=5.8, color="#555555")
    fig.savefig(out_dir / "full.png", dpi=DPI, facecolor="white", bbox_inches=None)
    for ax, name in ((ax_skew, "skewt.png"), (ax_hodo, "hodograph.png")):
        bbox = ax.get_window_extent().transformed(fig.dpi_scale_trans.inverted())
        fig.savefig(out_dir / name, dpi=DPI, facecolor="white", bbox_inches=bbox.expanded(1.03, 1.03), pad_inches=0.02)
    plt.close(fig)
    return out_dir / "full.png"
