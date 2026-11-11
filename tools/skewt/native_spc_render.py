"""White SPC-style multi-panel Skew-T renderer for Sideral SHARPpy profiles.

The meteorological profile and parcel diagnostics are supplied by SHARPpy. This
module only draws the sounding and the associated diagnostic panels.
"""
from __future__ import annotations

from datetime import timedelta
from pathlib import Path
import math

import numpy as np
from PIL import Image
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.collections import LineCollection
from matplotlib.ticker import FixedLocator, FuncFormatter, NullFormatter

W, H, DPI = 1500, 1000, 100
SKEW = 15.5
RED = "#ff3030"
BLUE = "#168cff"
GREEN = "#00ff55"
INK = "#f2f2f2"


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


def _densify_profile(p, t, td, z, u, v, omega, count=55):
    """Interpolate the drawing profile to 55 pressure levels; physics stays native SHARPpy."""
    if len(p) >= count:
        return p, t, td, z, u, v, omega
    target_p = np.geomspace(float(p[0]), float(p[-1]), count)
    target_logp = np.log(target_p)

    def interp(values):
        values = np.asarray(values, dtype=float)
        good = np.isfinite(values) & np.isfinite(p)
        if good.sum() < 2:
            return np.full(count, np.nan, dtype=float)
        return np.interp(target_logp, np.log(p[good][::-1]), values[good][::-1])

    return (target_p, interp(t), interp(td), interp(z), interp(u), interp(v), interp(omega))


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
    surface_pressure = float(p[0])
    ax.set_ylim(surface_pressure, 100)
    ax.set_xlim(-55, 79)
    ax.set_facecolor("#000000")
    ax.spines[:].set_color("#555555")
    ax.spines[:].set_linewidth(0.85)
    ax.tick_params(axis="both", labelsize=8, colors=INK, direction="out", length=3, pad=2)
    # Reference image uses temperature tick labels without an extra axis title.
    # Pressure levels are identified by their tick labels only.

    # Pressure grid: major every 100 hPa, supporting levels every 25 hPa.
    p_ticks = [1000, 850, 700, 500, 300, 200, 100]
    p_minor = [float(pp) for pp in np.arange(125, 1050, 25)
               if pp not in p_ticks and pp <= surface_pressure]
    ax.yaxis.set_major_locator(FixedLocator(p_ticks))
    ax.yaxis.set_major_formatter(FuncFormatter(lambda val, pos: f"{int(val)}" if any(abs(val-x)<0.5 for x in p_ticks) else ""))
    ax.yaxis.set_minor_locator(FixedLocator(p_minor))
    ax.yaxis.set_minor_formatter(NullFormatter())
    ax.set_xticks(np.arange(-50, 56, 10))
    ax.set_xticks(np.arange(-50, 56, 5), minor=True)
    ax.tick_params(axis="x", which="minor", length=2, color="#5a5a5a")
    ps = np.geomspace(100, max(100.0, surface_pressure), 240)
    for pp in p_ticks:
        if pp <= surface_pressure + 0.1:
            ax.axhline(pp, color="#4c4c4c", lw=0.52, zorder=0)
    for pp in p_minor:
        ax.axhline(pp, color="#252525", lw=0.30, zorder=0)

    # Isotherms every 5 C; every 10 C is emphasized.
    for temp in np.arange(-90, 66, 5):
        major = (int(temp) % 10 == 0)
        ax.plot(_xskew(np.full_like(ps, temp), ps), ps,
                ls="-", color="#6c3c2e" if major else "#3e2823",
                lw=0.48 if major else 0.30, alpha=0.78 if major else 0.58, zorder=0)

    # Dry adiabats every 5 K (10 K emphasized), giving a denser thermodynamic mesh.
    for theta_k in np.arange(250, 506, 5):
        tt = theta_k * (ps / 1000.0) ** 0.2854 - 273.15
        major = (int(theta_k) % 10 == 0)
        ax.plot(_xskew(tt, ps), ps, color="#78432f" if major else "#4b3025",
                ls="-", lw=0.54 if major else 0.32,
                alpha=0.80 if major else 0.56, zorder=0)

    # Moist adiabats every 5 C. Vectorized SHARPpy wet lifting is much faster
    # than evaluating each point separately for each of the 459 generated frames.
    try:
        from sharppy.sharptab import thermo
        pp_grid = np.geomspace(1000, 100, 100)
        visible_moist = pp_grid <= surface_pressure + 0.01
        for start_t in np.arange(-35, 56, 5):
            # SHARPpy's vectorized satlift requires the initial parcel
            # temperature array to have the same shape as the target pressures.
            start_profile = np.full_like(pp_grid, float(start_t), dtype=float)
            curve = _arr(thermo.wetlift(1000.0, start_profile, pp_grid))
            if len(curve) == len(pp_grid):
                major = (int(start_t) % 10 == 0)
                ax.plot(_xskew(curve[visible_moist], pp_grid[visible_moist]), pp_grid[visible_moist],
                        color="#34723a" if major else "#24482b",
                        ls="-", lw=0.52 if major else 0.32,
                        alpha=0.76 if major else 0.54, zorder=0)
    except Exception:
        pass

    # Mixing-ratio lines, in g/kg, with the lower-value lines kept subtle.
    for r in [0.1, 0.2, 0.4, 0.6, 0.8, 1, 1.5, 2, 3, 4, 5, 6, 8, 10, 12, 16, 20, 24, 28, 32]:
        e = (r * ps) / (622.0 + r)
        loge = np.log(np.maximum(e, 0.01) / 6.112)
        tdline = 243.5 * loge / (17.67 - loge)
        major = r in (0.4, 1, 2, 4, 8, 16, 24, 32)
        ax.plot(_xskew(tdline, ps), ps, color="#386b39" if major else "#203b25",
                ls=(0, (1, 2)), lw=0.48 if major else 0.30,
                alpha=0.70 if major else 0.48, zorder=0)
    h_agl = z - z[0]
    ax.axhline(surface_pressure, color="#555555", lw=0.95, ls=(0, (4, 2)), alpha=0.95, zorder=3)
    ax.text(-52.0, surface_pressure, "SFC",
            ha="left", va="bottom", fontsize=6.5, weight="bold",
            color="#ff3b3b", clip_on=True,
            bbox=dict(facecolor="#000000", edgecolor="none", alpha=0.78, pad=0.2))
    # Height markers every 3 km, matching the sparse altitude labels in the reference.
    for height_m in (3000, 6000, 9000, 12000, 15000):
        level_pressure = _interp_height(p, h_agl, float(height_m))
        if math.isfinite(level_pressure) and 100.0 <= level_pressure <= surface_pressure:
            ax.text(-52.0, level_pressure, f"{height_m // 1000} km",
                    ha="left", va="center", fontsize=6.4, color="#ff3b3b",
                    weight="bold", clip_on=True,
                    bbox=dict(facecolor="#000000", edgecolor="none", alpha=0.72, pad=0.15))
    from sharppy.sharptab import thermo
    t_virtual = np.asarray([float(thermo.virtemp(float(pi), float(ti), float(di)))
                            for pi, ti, di in zip(p, t, td)], dtype=float)
    tw_values = []
    for pi, ti, di in zip(p, t, td):
        try:
            tw_values.append(float(thermo.wetbulb(float(pi), float(ti), float(di))))
        except Exception:
            tw_values.append(np.nan)
    t_wet = np.asarray(tw_values, dtype=float)
    # Reference palette: red temperature, green dew point, cyan wet-bulb and distinct parcel traces.
    ax.plot(_xskew(t, p), p, color="#ff3030", lw=1.55, zorder=6, label="Temperatura")
    ax.plot(_xskew(td, p), p, color="#00ff55", lw=1.55, zorder=7, label="Ponto de orvalho")
    ax.plot(_xskew(t_virtual, p), p, color="#ff3030", lw=1.0, ls=(0, (4, 2)), zorder=5, label="Temperatura virtual")
    if np.isfinite(t_wet).sum() >= 3:
        ax.plot(_xskew(t_wet, p), p, color="#00eaff", lw=1.20, zorder=5, label="Temp. bulbo úmido")
    def parcel_trace(parcel_name):
        parcel_obj = getattr(pcl, parcel_name, None)
        trace_p = _arr(getattr(parcel_obj, "ptrace", [])) if parcel_obj is not None else np.asarray([])
        trace_t = _arr(getattr(parcel_obj, "ttrace", [])) if parcel_obj is not None else np.asarray([])
        good = np.isfinite(trace_p) & (trace_p > 0) & np.isfinite(trace_t)
        if int(good.sum()) < 2:
            return np.full_like(p, np.nan, dtype=float)
        trace_p, trace_t = trace_p[good], trace_t[good]
        order = np.argsort(trace_p)
        trace_p, trace_t = trace_p[order], trace_t[order]
        unique = np.r_[True, np.diff(trace_p) > 0.01]
        trace_p, trace_t = trace_p[unique], trace_t[unique]
        result = np.full_like(p, np.nan, dtype=float)
        inside = (p >= trace_p.min()) & (p <= trace_p.max())
        result[inside] = np.interp(np.log(p[inside]), np.log(trace_p), trace_t)
        return result

    parcel_specs = [
        ("sfcpcl", "Parcela Descendente", "#ff34f4", (0, (2, 2))),
        ("mlpcl", "Parcela Efetiva", "#ffe600", (0, (4, 2))),
        ("mupcl", "Parcela Most Instable", "#f1f1f1", (0, (2, 2))),
    ]
    for parcel_name, parcel_label, parcel_color, parcel_style in parcel_specs:
        parcel_trace_values = parcel_trace(parcel_name)
        if parcel_name == "sfcpcl" and np.isfinite(parcel_trace_values).sum() < 3:
            parcel_trace_values = _parcel_curve(p, t, td)
        if np.isfinite(parcel_trace_values).sum() > 3:
            ax.plot(_xskew(parcel_trace_values, p), p, color=parcel_color,
                    lw=1.25 if parcel_name == "sfcpcl" else 1.05,
                    ls=parcel_style, zorder=5, label=parcel_label)
    # Mark LCL/LFC/EL using whichever SHARPpy parcel contains each value.
    parcel_objects = [getattr(pcl, n, None) for n in ("sfcpcl", "mlpcl", "mupcl", "fcstpcl")]
    markers = (("lclpres", "LCL"), ("lfcpres", "LFC"), ("elpres", "EL"))
    marker_colors = {"LCL": "#3dff62", "LFC": "#ffe600", "EL": "#ff35f4"}
    marker_fills = {"LCL": "#000000", "LFC": "#000000", "EL": "#000000"}
    marker_pressures = {}
    for attr, short in markers:
        pressure_value = next((_finite(getattr(parcel, attr, None))
                               for parcel in parcel_objects if parcel is not None
                               and _finite(getattr(parcel, attr, None)) is not None), None)
        if pressure_value is None and short == "LCL":
            try:
                from sharppy.sharptab import thermo
                pressure_value = float(thermo.drylift(float(p[0]), float(t[0]), float(td[0]))[0])
            except Exception:
                pressure_value = None
        marker_pressures[short] = pressure_value
        if pressure_value is not None and 100 <= pressure_value <= 1050:
            marker_color = marker_colors[short]
            # Short colored level tick and compact label; avoid drawing across the full chart.
            ax.plot([43.0, 48.0], [pressure_value, pressure_value],
                    color=marker_color, lw=1.35, zorder=8, solid_capstyle="round")
            ax.text(49.2, pressure_value, short, fontsize=7.1, weight="bold",
                    ha="left", va="center", color=marker_color,
                    bbox=dict(facecolor="#000000", edgecolor="none", alpha=0.82, pad=0.1),
                    clip_on=True, zorder=10)

    # Freezing level, a common sounding reference mark.
    freeze_idx = np.where(np.isfinite(t[:-1]) & np.isfinite(t[1:]) & (t[:-1] * t[1:] <= 0))[0]
    if len(freeze_idx):
        i = int(freeze_idx[0])
        if abs(t[i + 1] - t[i]) > 1e-6:
            frac = -t[i] / (t[i + 1] - t[i])
            p0 = float(np.exp(np.log(p[i]) + frac * (np.log(p[i + 1]) - np.log(p[i]))))
            if 100 <= p0 <= 1050:
                ax.axhline(p0, color="#6f6f6f", lw=0.6, ls=(0, (1, 3)), alpha=0.8, zorder=2)
                ax.text(43.0, p0, "0°C", fontsize=6.6, color="#00dfff",
                        ha="left", va="bottom", bbox=dict(facecolor="#000000", edgecolor="none", alpha=0.65, pad=0.25))

    # Frequent wind barbs, as in an operational sounding.
    idx = np.unique(np.linspace(0, len(p) - 1, min(50, len(p))).round().astype(int))
    barb_pressure = np.geomspace(float(p[0]), float(p[-1]), 55)
    barb_u = np.asarray([_interp_pressure(u, p, value) for value in barb_pressure])
    barb_v = np.asarray([_interp_pressure(v, p, value) for value in barb_pressure])
    barb_good = np.isfinite(barb_u) & np.isfinite(barb_v)
    if int(barb_good.sum()) < 50:
        raise RuntimeError(f"Somente {int(barb_good.sum())} barbelas de vento válidas; esperadas pelo menos 50.")
    # Slim, restrained wind barbs: preserve 55 levels without oversized feathers.
    ax.barbs(np.full(int(barb_good.sum()), 66.0), barb_pressure[barb_good],
             barb_u[barb_good], barb_v[barb_good], length=5.2,
             linewidth=0.62, sizes={"spacing": 0.11, "height": 0.25,
                                    "width": 0.18, "emptybarb": 0.12},
             barb_increments={"half": 5, "full": 10, "flag": 50},
             pivot="middle", color=INK, clip_on=True, zorder=9)
    legend = ax.legend(loc="upper right", bbox_to_anchor=(0.88, 0.995), fontsize=7.0,
                      frameon=True, framealpha=0.96, fancybox=False, borderpad=0.35,
                      labelspacing=0.25, handlelength=1.45)
    if legend is not None:
        legend.get_frame().set_facecolor("#000000")
        legend.get_frame().set_edgecolor("#4c4c4c")


def _make_theta_axes(ax, p, t, td):
    theta, thetae, thetaes = _theta_profiles(p, t, td)
    surface_pressure = float(p[0])
    ax.set_yscale("log"); ax.set_ylim(surface_pressure, 100); ax.set_xlim(245, 380)
    ax.set_facecolor("#000000")
    ax.spines[:].set_color("#555555"); ax.spines[:].set_linewidth(0.85)
    ax.tick_params(axis="both", labelsize=7, colors=INK, direction="out", length=3, pad=2)
    ax.set_xticks([245, 270, 295, 320, 345, 370])
    ax.set_yticks([1000, 900, 800, 700, 600, 500, 400, 300, 200, 100])
    ax.set_yticklabels(["1000", "900", "800", "700", "600", "500", "400", "300", "200", "100"])
    ax.yaxis.set_minor_formatter(NullFormatter())
    for pp in [1000,900,800,700,600,500,400,300,200,100]:
        if pp <= surface_pressure + 0.1:
            ax.axhline(pp, color="#454545", lw=0.48, zorder=0)
    ax.plot(theta, p, color=GREEN, lw=1.3, label=r"$\theta$")
    ax.plot(thetae, p, color=RED, lw=1.2, label=r"$\theta_e$")
    ax.plot(thetaes, p, color=BLUE, lw=1.2, label=r"$\theta_{es}$")
    leg = ax.legend(loc="upper left", fontsize=7, frameon=True, framealpha=0.9, borderpad=0.25, handlelength=2)
    leg.get_frame().set_facecolor("#000000"); leg.get_frame().set_edgecolor("#4c4c4c")


def _make_hodo(ax, z, u, v, station_text, motion):
    h_agl = z - z[0]
    ax.set_facecolor("#000000"); ax.set_aspect("equal", adjustable="box")
    ax.set_xlim(-75, 75); ax.set_ylim(-75, 75)
    ax.spines[:].set_color("#555555"); ax.spines[:].set_linewidth(0.9)
    ax.set_xticks([]); ax.set_yticks([])
    for r in [10, 20, 30, 40, 50, 60, 70]:
        ax.add_patch(plt.Circle((0, 0), r, fill=False, lw=0.8 if r % 20 == 0 else 0.45,
                                edgecolor="#3a3a3a" if r % 20 == 0 else "#252525", linestyle=(0, (3, 3)), zorder=0))
        ax.text(-r, 1.5, f"{r}", fontsize=6.3, color="#b7b7b7", ha="center", va="bottom")
        ax.text(1.8, r, f"{r}", fontsize=6.3, color="#b7b7b7", ha="left", va="center")
    ax.axhline(0, color="#9aa4b0", lw=0.85, zorder=1); ax.axvline(0, color="#9aa4b0", lw=0.85, zorder=1)
    order = np.argsort(h_agl)
    hh, uu, vv = h_agl[order], u[order], v[order]
    pts = np.column_stack((uu, vv)).reshape(-1, 1, 2)
    if len(pts) >= 2:
        segments = np.concatenate([pts[:-1], pts[1:]], axis=1)
        mids = (hh[:-1] + hh[1:]) / 2.0
        bounds = [500, 3000, 6000, 12000, np.inf]
        colors = ["#ff3030", "#ff7a00", "#ffb000", "#ffe600", "#ffe600"]
        seg_colors = [colors[min(np.searchsorted(bounds[1:], hz, side="right"), len(colors)-1)] for hz in mids]
        ax.add_collection(LineCollection(segments, colors=seg_colors, linewidths=1.45, zorder=4))
        label_offsets = [(4, 4), (4, -5), (-5, 4), (5, 4), (-5, -5),
                         (4, -5), (-5, 4), (5, 4), (-5, -5), (4, 4), (-5, 4)]
        for km, (dx, dy) in enumerate(label_offsets):
            target = km * 1000
            if hh.min() <= target <= min(hh.max(), 10000.0):
                px, py = float(np.interp(target, hh, uu)), float(np.interp(target, hh, vv))
                ax.plot(px, py, "o", ms=3.0, markerfacecolor="white",
                        markeredgecolor="#777777", markeredgewidth=0.55, zorder=5)
                ax.annotate(str(km), (px, py), xytext=(dx, dy), textcoords="offset points",
                            fontsize=5.5, weight="bold", color="#222222", ha="center", va="center",
                            bbox=dict(boxstyle="circle,pad=0.10", facecolor="white",
                                      edgecolor="#777777", linewidth=0.45), zorder=6)
    mw, rm, lm = motion
    for sx, sy, label in [(lm[0], lm[1], "LM"), (mw[0], mw[1], "MW"), (rm[0], rm[1], "RM")]:
        ax.annotate("", xy=(sx, sy), xytext=(0, 0),
                    arrowprops=dict(arrowstyle="-|>", lw=0.85, color={"LM":"#4a9cff","MW":"#dddddd","RM":"#ff4545"}[label], shrinkA=0, shrinkB=0), zorder=2)
        ax.plot(sx, sy, marker="o", ms=3.8, mfc="white", mec={"LM":"#476fbd","MW":"#84909d","RM":"#c94f4f"}[label], mew=0.8, zorder=5)
        ax.text(sx + 2.5, sy + 2.0, label, fontsize=7.2, weight="bold", color={"LM":"#4a9cff","MW":"#eeeeee","RM":"#ff4545"}[label], zorder=6)
    ax.set_title("Hodógrafa — vento (kt)", fontsize=8.0, loc="left", pad=3, color=INK)
    legend_handles = [
        plt.Line2D([0], [0], color="#ff3030", lw=1.6, label="0–0,5 km"),
        plt.Line2D([0], [0], color="#ff7a00", lw=1.6, label="0,5–3 km"),
        plt.Line2D([0], [0], color="#ffb000", lw=1.6, label="3–6 km"),
        plt.Line2D([0], [0], color="#ffe600", lw=1.6, label="6–12 km"),
    ]
    ax.legend(handles=legend_handles, loc="upper right", bbox_to_anchor=(0.99, 1.005),
              ncol=4, fontsize=5.5, frameon=False, labelcolor="#e6e6e6",
              handlelength=1.0, handletextpad=0.25, columnspacing=0.6)


def _make_advection(ax, p, t, omega):
    surface_pressure = float(p[0])
    ax.set_facecolor("#000000"); ax.set_yscale("log"); ax.set_ylim(surface_pressure, 100); ax.set_xlim(-5, 5)
    ax.spines[:].set_color("#555555"); ax.spines[:].set_linewidth(0.85)
    ax.tick_params(axis="both", labelsize=7, colors=INK, direction="out", length=3, pad=2)
    ax.set_xlabel("Inf. Temp.\nAdvec. (C/hr)", fontsize=8.5, labelpad=3)
    ax.set_xticks([-5, -2.5, 0, 2.5, 5])
    ax.set_yticks([1000,900,800,700,600,500,400,300,200,100])
    ax.set_yticklabels(["1000","900","800","700","600","500","400","300","200","100"])
    ax.axvline(0, color=INK, lw=1.0)
    for pp in [1000,800,600,400,200,100]:
        if pp <= surface_pressure + 0.1:
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
    ax.set_facecolor("#000000"); ax.set_xlim(0, 50); ax.set_ylim(0, 16)
    ax.spines[:].set_color("#555555"); ax.spines[:].set_linewidth(0.85)
    ax.tick_params(axis="both", labelsize=7.5, colors=INK, direction="out", length=3, pad=2)
    ax.set_ylabel("Height (km)", fontsize=8, labelpad=3)
    ax.set_xticks([0, 10, 20, 30, 40, 50]); ax.set_yticks([0,2,4,6,8,10,12,14,16])
    for hz in [2,4,6,8,10,12,14,16]:
        ax.axhline(hz, color="#252525", lw=0.42, zorder=0)
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
        plt.Line2D([0],[0], color="#ff3030", lw=1.2, label="0-2 km"),
        plt.Line2D([0],[0], color="#ff7a00", lw=1.2, label="2-4 km"),
        plt.Line2D([0],[0], color="#ffb000", lw=1.2, label="4-9 km"),
        plt.Line2D([0],[0], color="#ffe600", lw=1.2, label="Wind"),
    ], loc="upper left", fontsize=5.8, frameon=True, framealpha=0.8, borderpad=0.2, handlelength=1.7, labelspacing=0.15)


def _parcel_rows(prof, srh01, srh03):
    """Native SHARPpy parcel metrics, formatted for the reference's compact table."""
    rows = []
    def first_value(obj, names):
        for name in names:
            value = _finite(getattr(obj, name, None)) if obj is not None else None
            if value is not None:
                return value
        return None
    for label, attr in [("Surface-Based", "sfcpcl"), ("Mixed-Layer", "mlpcl"), ("Most Unstable", "mupcl")]:
        pcl = getattr(prof, attr, None)
        ecape = first_value(pcl, ("ecape", "effective_cape"))
        ncape = first_value(pcl, ("ncape", "normalized_cape"))
        cape = first_value(pcl, ("bplus", "cape"))
        cape3 = first_value(pcl, ("cape3km", "cape_3km", "3cape", "bplus3km"))
        cape6 = first_value(pcl, ("cape6km", "cape_6km", "6cape", "bplus6km"))
        cin = first_value(pcl, ("bminus", "cin", "cinh"))
        lcl = first_value(pcl, ("lclhght", "lcl_height"))
        lfc = first_value(pcl, ("lfchght", "lfc_height"))
        el = first_value(pcl, ("elhght", "el_height"))
        rows.append([
            label, _fmt(ecape, 0), _fmt(ncape, 2), _fmt(cape, 0),
            _fmt(cape3, 0), _fmt(cape6, 0), _fmt(cin, 0),
            _fmt(lcl, 0) if lcl is None else f"{lcl:.0f}m",
            _fmt(lfc, 0) if lfc is None else f"{lfc:.0f}m",
            _fmt(el, 0) if el is None else f"{el:.0f}m",
        ])
    return rows


def _bottom_diagnostics(fig, prof, p, t, td, z, u, v, srh01, srh03, srh06, shear01, shear03, shear06, hodo_motion, latitude):
    ax = fig.add_axes([0.007, 0.125, 0.650, 0.095]); ax.axis("off")
    cols = ["PARCEL", "ECAPE", "NCAPE", "CAPE", "3CAPE", "6CAPE", "CINH", "LCL", "LFC", "EL"]
    table = ax.table(cellText=_parcel_rows(prof, srh01, srh03), colLabels=cols, cellLoc="center", colLoc="center", loc="upper left",
                     colWidths=[0.18, 0.08, 0.08, 0.08, 0.08, 0.08, 0.08, 0.10, 0.10, 0.10])
    table.auto_set_font_size(False); table.set_fontsize(6.3); table.scale(1, 1.08)
    level_styles = {7: ("#000000", "#3dff62"), 8: ("#000000", "#ffe600"), 9: ("#000000", "#ff35f4")}
    for (r, c), cell in table.get_celld().items():
        cell.set_edgecolor("#4c4c4c"); cell.set_linewidth(0.45)
        cell.set_facecolor("#151515" if r == 0 else ("#000000" if r % 2 else "#080808"))
        cell.get_text().set_color("#f0f0f0")
        cell.get_text().set_fontweight("bold" if r == 0 else "normal")
        if r > 0 and c in (2, 3, 4, 5):
            cell.get_text().set_color("#ffe600")
            cell.get_text().set_fontweight("bold")
        if r > 0 and c in level_styles:
            fill, ink = level_styles[c]
            cell.set_facecolor(fill)
            cell.set_edgecolor(ink); cell.set_linewidth(0.8)
            cell.get_text().set_color(ink); cell.get_text().set_fontweight("bold")

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
    # Reference-style layer table: boundary-layer shear, storm-relative helicity,
    # storm-relative mean wind and surface-based EHI. All values come from this profile.
    preferred = hodo_motion[2] if (_finite(latitude) is not None and latitude < 0) else hodo_motion[1]
    srh005 = _srh(h_agl, u, v, 0, 500, preferred)
    shear005 = _shear(h_agl, u, v, 500)
    surface_pcl = getattr(prof, "sfcpcl", None)
    surface_cape = _finite(getattr(surface_pcl, "bplus", None)) if surface_pcl is not None else None

    def _mean_srw(top_m):
        valid = (np.isfinite(h_agl) & np.isfinite(u) & np.isfinite(v)
                 & (h_agl >= 0.0) & (h_agl <= float(top_m)))
        if not np.any(valid):
            return None
        relative = np.hypot(u[valid] - preferred[0], v[valid] - preferred[1])
        return float(np.nanmean(relative)) if np.isfinite(relative).any() else None

    wind_specs = [
        ("SFC–500 m", 500, shear005, srh005),
        ("SFC–1 km", 1000, shear01, srh01),
        ("SFC–3 km", 3000, shear03, srh03),
        ("SFC–6 km", 6000, shear06, srh06),
    ]
    wind_rows = []
    for layer, top_m, bwd, helicity in wind_specs:
        srw = _mean_srw(top_m)
        ehi = surface_cape * helicity / 160000.0 if surface_cape is not None and math.isfinite(helicity) else None
        wind_rows.append([layer, _fmt(bwd, 1), _fmt(helicity, 0), _fmt(srw, 1), _fmt(ehi, 2)])

    wind_ax = fig.add_axes([0.665, 0.005, 0.330, 0.215]); wind_ax.axis("off")
    wind_table = wind_ax.table(cellText=wind_rows,
                               colLabels=["LAYER", "BWD [kt]", "SRH", "SRW [kt]", "EHI"],
                               cellLoc="center", colLoc="center", loc="upper left",
                               colWidths=[0.27, 0.17, 0.23, 0.19, 0.14])
    wind_table.auto_set_font_size(False); wind_table.set_fontsize(6.3); wind_table.scale(1, 1.18)
    for (r, c), cell in wind_table.get_celld().items():
        cell.set_edgecolor("#4c4c4c"); cell.set_linewidth(0.48)
        cell.set_facecolor("#151515" if r == 0 else ("#000000" if r % 2 else "#080808"))
        cell.get_text().set_color("#f0f0f0")
        cell.get_text().set_fontweight("bold" if r == 0 else "normal")

    columns = [(0.010, left), (0.140, mid1), (0.270, mid2 + severe),
               (0.400, lapse), (0.530, right)]
    for x, items in columns:
        for j, label in enumerate(items):
            fig.text(x, 0.108 - j * 0.018, label, ha="left", va="center",
                     fontsize=6.0, color="#e6e6e6", fontweight="bold", family="DejaVu Sans Mono", zorder=30)


def render_native_spc(prof, out_dir: Path, meta: dict):
    """Render one SHARPpy profile as a classic white, multi-panel SPC-style PNG."""
    out_dir = Path(out_dir); out_dir.mkdir(parents=True, exist_ok=True)
    p, t, td, z, u, v, omega = _clean_profile(prof)
    # The native ECMWF input may have only 13 pressure levels. Interpolate the
    # display profiles to 55 levels so thermodynamic curves, hodograph and barbs
    # are continuous while SHARPpy's parcel diagnostics remain unchanged.
    p, t, td, z, u, v, omega = _densify_profile(p, t, td, z, u, v, omega, count=55)
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

    plt.rcParams.update({"font.family": "DejaVu Sans Mono", "font.size": 7, "axes.linewidth": 0.85, "text.color": INK, "axes.labelcolor": INK, "xtick.color": INK, "ytick.color": INK, "savefig.facecolor": "#000000", "figure.facecolor": "#000000"})
    fig = plt.figure(figsize=(W / DPI, H / DPI), dpi=DPI, facecolor="#000000")
    model_label = str(meta.get("model", meta.get("source", "ECMWF IFS")) or "ECMWF IFS")
    header_text = f"{model_label} • {station} • {run_text} • F{fh:03d} • válido {valid_text} • {lat_text}, {lon_text}"
    fig.text(0.007, 0.978, header_text, ha="left", va="center",
             fontsize=7.0, color=INK, fontweight="bold", family="DejaVu Sans Mono")
    fig.add_artist(plt.Line2D([0.007, 0.993], [0.952, 0.952],
                              transform=fig.transFigure, color="#414141", lw=0.75, zorder=10))
    ax_skew = fig.add_axes([0.007, 0.237, 0.504, 0.715])
    ax_hodo = fig.add_axes([0.522, 0.237, 0.471, 0.715])

    _make_skew_axes(ax_skew, p, t, td, z, u, v, prof, station, f"{run_text} - {valid_text}")
    _make_hodo(ax_hodo, z, u, v, station_text, motion)
    _bottom_diagnostics(fig, prof, p, t, td, z, u, v, srh01, srh03, srh06, shear01, shear03, shear06, motion, latitude)

    # Use the exact Sideral logo asset supplied for the brand; do not synthesize a text logo.
    logo_path = Path(__file__).resolve().parents[2] / "assets" / "sideral-logo.png"
    if not logo_path.is_file():
        raise FileNotFoundError(f"Logo oficial Sideral ausente: {logo_path}")
    with Image.open(logo_path) as source_logo:
        logo = source_logo.convert("RGBA")
    logo_ax = fig.add_axes([0.872, 0.958, 0.112, 0.030], zorder=20)
    logo_ax.imshow(logo, interpolation="lanczos")
    logo_ax.set_axis_off()
    fig.savefig(out_dir / "full.png", dpi=DPI, facecolor="#000000", bbox_inches=None)
    for ax, name in ((ax_skew, "skewt.png"), (ax_hodo, "hodograph.png")):
        bbox = ax.get_window_extent().transformed(fig.dpi_scale_trans.inverted())
        fig.savefig(out_dir / name, dpi=DPI, facecolor="#000000", bbox_inches=bbox.expanded(1.03, 1.03), pad_inches=0.02)
    plt.close(fig)
    return out_dir / "full.png"
