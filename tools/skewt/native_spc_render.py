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


def _clean_profile(prof, ground_m=None):
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
    ground = _finite(ground_m)
    if ground is not None:
        # WRF geopotential heights are MSL; exclude any interpolated/model
        # points below the actual terrain while preserving the surface row.
        valid &= z >= ground - 2.0
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
    if good.sum() < 2 or not math.isfinite(float(target)) or target <= 0:
        return np.nan
    p_good = pressure[good]
    # Never extrapolate below station pressure or above the model top. This
    # prevents elevated stations being labelled at fictitious 1000-hPa height.
    if target < np.min(p_good) - 1e-6 or target > np.max(p_good) + 1e-6:
        return np.nan
    idx = np.argsort(p_good)
    return float(np.interp(np.log(target), np.log(p_good[idx]), values[good][idx]))


def _interp_height(values, height, target):
    values, height = np.asarray(values, dtype=float), np.asarray(height, dtype=float)
    good = np.isfinite(values) & np.isfinite(height)
    if good.sum() < 2 or not math.isfinite(float(target)):
        return np.nan
    h_good = height[good]
    # No extrapolation to heights the WRF sounding does not cover.
    if target < np.min(h_good) - 1e-6 or target > np.max(h_good) + 1e-6:
        return np.nan
    idx = np.argsort(h_good)
    return float(np.interp(target, h_good[idx], values[good][idx]))


def _finite(value):
    try:
        if value is None or np.ma.is_masked(value):
            return None
        result = float(value)
        # SHARPpy uses -9999 as its missing-data sentinel. Treating it as a
        # real index creates physically meaningless values in the diagnostics.
        if not math.isfinite(result) or math.isclose(result, -9999.0, abs_tol=0.5) or abs(result) >= 1e25:
            return None
        return result
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


def _parcel_curve(prof, p, t, td):
    """Interpolate SHARPpy's native surface-parcel virtual-temperature trace."""
    target_p = np.asarray(p, dtype=float)
    result = np.full_like(target_p, np.nan, dtype=float)
    try:
        from sharppy.sharptab import thermo
        parcel = getattr(prof, "sfcpcl", None)
        ptrace = _arr(getattr(parcel, "ptrace", [])) if parcel is not None else np.asarray([])
        ttrace = _arr(getattr(parcel, "ttrace", [])) if parcel is not None else np.asarray([])
        good = np.isfinite(ptrace) & (ptrace > 0) & np.isfinite(ttrace)
        if good.sum() >= 2:
            pressure = ptrace[good]
            temperature = ttrace[good]
            order = np.argsort(pressure)
            pressure, temperature = pressure[order], temperature[order]
            unique = np.r_[True, np.diff(pressure) > 0.01]
            pressure, temperature = pressure[unique], temperature[unique]
            inside = (target_p >= pressure.min()) & (target_p <= pressure.max())
            result[inside] = np.interp(
                np.log(target_p[inside]), np.log(pressure), temperature
            )
            if np.isfinite(result).sum() >= 2:
                return result

        # Defensive fallback for profiles where SHARPpy did not produce a
        # usable SFC trace. Lift the parcel dry with conserved mixing ratio to
        # LCL, then pseudoadiabatically above LCL; never extrapolate below SFC.
        ps, ts, tds = float(target_p[0]), float(t[0]), float(td[0])
        lclp, lclt = thermo.drylift(ps, ts, tds)
        lclp, lclt = float(lclp), float(lclt)
        e0 = float(_vapor_pressure_from_td(np.asarray([tds]))[0])
        mixing_ratio = 0.622 * e0 / max(ps - e0, 0.1)
        for i, pi in enumerate(target_p):
            if not (float(target_p.min()) <= pi <= float(target_p.max())):
                continue
            if pi >= lclp:
                parcel_t = (ts + 273.15) * (float(pi) / ps) ** 0.2854 - 273.15
                vapor = float(pi) * mixing_ratio / (0.622 + mixing_ratio)
                loge = math.log(max(vapor, 1e-5) / 6.112)
                parcel_td = 243.5 * loge / (17.67 - loge)
            else:
                parcel_t = float(thermo.wetlift(lclp, lclt, float(pi)))
                parcel_td = parcel_t
            result[i] = float(thermo.virtemp(float(pi), parcel_t, parcel_td))
        return result
    except Exception:
        return result


def _storm_motion(prof, h_agl, u, v):
    """Return pressure-weighted mean wind and SHARPpy Bunkers RM/LM vectors (kt)."""
    from sharppy.sharptab import interp, winds

    sfc_p = _finite(prof.pres[prof.sfc])
    p6 = _finite(interp.pres(prof, interp.to_msl(prof, 6000.0)))
    if sfc_p is None or p6 is None:
        raise RuntimeError("Não foi possível determinar a camada de vento 0–6 km AGL.")
    mw_raw = winds.mean_wind(prof, pbot=sfc_p, ptop=p6)
    mw = (_finite(mw_raw[0]), _finite(mw_raw[1]))
    if any(value is None for value in mw):
        raise RuntimeError("Vento médio 0–6 km inválido no perfil SHARPpy.")

    motion = getattr(prof, "srwind", None)
    if motion is None or len(motion) < 4:
        from sharppy.sharptab import params
        motion = params.bunkers_storm_motion(prof)
    vals = [_finite(value) for value in motion[:4]]
    if len(vals) < 4 or any(value is None for value in vals):
        raise RuntimeError("SHARPpy não conseguiu calcular os movimentos Bunkers RM/LM.")
    right = (vals[0], vals[1])
    left = (vals[2], vals[3])
    return mw, right, left


def _srh(prof, lower, upper, storm):
    """SHARPpy storm-relative helicity for a layer expressed in metres AGL."""
    try:
        from sharppy.sharptab import winds
        value = winds.helicity(prof, lower, upper, stu=storm[0], stv=storm[1], exact=True)[0]
        return _finite(value)
    except Exception:
        return np.nan


def _shear(prof, layer):
    """Surface-to-layer vector shear from SHARPpy's pressure/height interpolation."""
    try:
        from sharppy.sharptab import interp, winds
        surface_pressure = _finite(prof.pres[prof.sfc])
        layer_pressure = _finite(interp.pres(prof, interp.to_msl(prof, float(layer))))
        if surface_pressure is None or layer_pressure is None:
            return np.nan
        du, dv = winds.wind_shear(prof, pbot=surface_pressure, ptop=layer_pressure)
        du, dv = _finite(du), _finite(dv)
        if du is None or dv is None:
            return np.nan
        return math.hypot(du, dv)
    except Exception:
        return np.nan


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
    """Precipitable water from specific humidity, integrated in pressure coordinates."""
    e = _vapor_pressure_from_td(td)
    mixing_ratio = 0.622 * e / np.maximum(np.asarray(p, dtype=float) - e, 0.1)
    # Hydrostatic precipitable water integrates specific humidity q, not
    # mixing ratio r. Convert r -> q=r/(1+r) before integrating dp/g.
    specific_humidity = mixing_ratio / (1.0 + mixing_ratio)
    order = np.argsort(p)
    pp, qq = np.asarray(p)[order] * 100.0, np.asarray(specific_humidity)[order]
    if len(pp) < 2 or not np.isfinite(qq).all():
        return np.nan
    return float(abs(np.trapz(qq, pp)) / 9.80665)


_MOIST_ADIABATS_CACHE = None


def _moist_adiabat_curves():
    """Calculate the thermodynamic reference curves once per rendering process."""
    global _MOIST_ADIABATS_CACHE
    if _MOIST_ADIABATS_CACHE is None:
        from sharppy.sharptab import thermo
        pressure = np.geomspace(1000.0, 100.0, 100)
        curves = []
        for start_t in np.arange(-30, 41, 5):
            curve = np.asarray(
                [float(thermo.wetlift(1000.0, float(start_t), float(pp))) for pp in pressure],
                dtype=float,
            )
            if not np.isfinite(curve).all():
                raise RuntimeError(f"SHARPpy gerou uma adiabat úmida inválida a {start_t:.0f} °C.")
            curves.append((float(start_t), curve))
        _MOIST_ADIABATS_CACHE = (pressure, curves)
    return _MOIST_ADIABATS_CACHE


def _make_skew_axes(ax, p, t, td, z, u, v, pcl, station, date_text, ground_m):
    ax.set_yscale("log")
    ax.set_ylim(1050, 100)
    # Reserve enough right margin for large wind barbs and separated parcel labels.
    ax.set_xlim(-43, 72)
    ax.set_facecolor("white")
    ax.spines[:].set_color(INK)
    ax.spines[:].set_linewidth(0.85)
    ax.tick_params(axis="both", labelsize=8, colors=INK, direction="out", length=3, pad=2)
    ax.set_xlabel("Temperature (°C)", fontsize=9, labelpad=4)
    ax.set_ylabel("Pressure (hPa)", fontsize=9, labelpad=8)

    # Pressure levels below the actual station pressure remain blank: no
    # extrapolated environmental curve or synthetic grid is drawn underground.
    surface_pressure = float(p[0])
    p_ticks = [1050, 1000, 900, 800, 700, 600, 500, 400, 300, 200, 100]
    p_minor = [
        float(pp) for pp in np.arange(125, 1000, 25)
        if pp not in p_ticks and pp <= surface_pressure
    ]
    ax.yaxis.set_major_locator(FixedLocator(p_ticks))
    ax.yaxis.set_major_formatter(FuncFormatter(lambda val, pos: f"{int(val)}" if any(abs(val-x)<0.5 for x in p_ticks) else ""))
    ax.yaxis.set_minor_locator(FixedLocator(p_minor))
    ax.yaxis.set_minor_formatter(NullFormatter())
    ax.set_xticks(np.arange(-40, 56, 10))
    ax.set_xticks(np.arange(-40, 56, 5), minor=True)
    ax.tick_params(axis="x", which="minor", length=2, color="#777777")
    # Every thermodynamic reference line ends at the true surface pressure.
    ps = np.geomspace(100.0, max(100.0, min(1050.0, surface_pressure)), 240)
    for pp in p_ticks:
        if pp <= surface_pressure + 0.1:
            ax.axhline(pp, color="#a7a7a7", lw=0.62, zorder=0)
    for pp in p_minor:
        ax.axhline(pp, color="#e4e4e4", lw=0.34, zorder=0)
    if 100.0 <= surface_pressure <= 1050.0:
        ax.axhline(surface_pressure, color="#555555", lw=0.95,
                   ls=(0, (4, 2)), alpha=0.95, zorder=3)

    # Isotherms every 5 C; every 10 C is emphasized.
    for temp in np.arange(-90, 66, 5):
        major = (int(temp) % 10 == 0)
        ax.plot(_xskew(np.full_like(ps, temp), ps), ps,
                ls="-", color="#7290b7" if major else "#b3bdc9",
                lw=0.70 if major else 0.43, alpha=0.86 if major else 0.72, zorder=0)

    # Dry adiabats every 5 K (10 K emphasized), giving a denser thermodynamic mesh.
    for theta_k in np.arange(250, 506, 5):
        tt = theta_k * (ps / 1000.0) ** 0.2854 - 273.15
        major = (int(theta_k) % 10 == 0)
        ax.plot(_xskew(tt, ps), ps, color="#b28b55" if major else "#dbc8a9",
                ls="-", lw=0.66 if major else 0.43,
                alpha=0.86 if major else 0.69, zorder=0)

    # Moist adiabats (pseudoadiabats) every 5 C, calculated with SHARPpy.
    # Reuse these shared reference curves across all forecast frames to keep the
    # 27-city operational batch from repeating thousands of identical lifts.
    pp_grid, moist_curves = _moist_adiabat_curves()
    visible_moist = pp_grid <= surface_pressure + 0.01
    for start_t, curve in moist_curves:
        major = (int(start_t) % 10 == 0)
        ax.plot(_xskew(curve[visible_moist], pp_grid[visible_moist]), pp_grid[visible_moist],
                color="#377e4c" if major else "#9abc9e",
                ls="-", lw=0.74 if major else 0.46,
                alpha=0.92 if major else 0.70, zorder=0)

    # Mixing-ratio lines, in g/kg, with the lower-value lines kept subtle.
    for r in [0.1, 0.2, 0.4, 0.6, 0.8, 1, 1.5, 2, 3, 4, 5, 6, 8, 10, 12, 16, 20, 24, 28, 32]:
        e = (r * ps) / (622.0 + r)
        loge = np.log(np.maximum(e, 0.01) / 6.112)
        tdline = 243.5 * loge / (17.67 - loge)
        major = r in (0.4, 1, 2, 4, 8, 16, 24, 32)
        ax.plot(_xskew(tdline, ps), ps, color="#86b68e" if major else "#b2ceb5",
                ls=(0, (1, 3)), lw=0.56 if major else 0.38,
                alpha=0.78 if major else 0.62, zorder=0)

    # Do not hatch or shade the sub-surface pressure interval. It is left plain
    # white, with only the surface-pressure boundary marking where the profile starts.

    h_agl = z - float(ground_m)
    h_agl[np.abs(h_agl) < 2.0] = 0.0
    surface_pressure = float(p[0])
    for pp in [1050, 1000, 900, 800, 700, 600, 500, 400, 300, 200, 100]:
        height = _interp_pressure(h_agl, p, pp)
        if (math.isfinite(height) and pp <= surface_pressure + 0.1
                and abs(pp - surface_pressure) >= 25.0):
            ax.text(-41.8, pp, f"{height:.0f} m AGL", ha="left", va="center",
                    fontsize=6.2, color="#303030", clip_on=True,
                    bbox=dict(facecolor="white", edgecolor="none", alpha=0.55, pad=0.15))
    ax.text(-41.8, surface_pressure, f"SFC {surface_pressure:.0f} hPa / 0 m AGL",
            ha="left", va="bottom", fontsize=6.5, weight="bold",
            color="#303030", clip_on=True,
            bbox=dict(facecolor="white", edgecolor="none", alpha=0.78, pad=0.2))
    # Add the standard sounding traces in addition to T and Td: virtual
    # temperature and wet-bulb temperature. SHARPpy documents both as part of
    # the classic SPC Skew-T view; failures at isolated model levels are masked
    # rather than replaced with invented values.
    from sharppy.sharptab import thermo
    t_virtual = np.asarray([
        float(thermo.virtemp(float(pi), float(ti), float(di)))
        for pi, ti, di in zip(p, t, td)
    ], dtype=float)
    tw_values = []
    for pi, ti, di in zip(p, t, td):
        try:
            tw_values.append(float(thermo.wetbulb(float(pi), float(ti), float(di))))
        except Exception:
            tw_values.append(np.nan)
    t_wet = np.asarray(tw_values, dtype=float)

    # SHARPpy's standard color convention: T red, Td green, virtual T dashed
    # red, wet-bulb T cyan, and lifted surface parcel dashed dark.
    ax.plot(_xskew(t, p), p, color="#d62728", lw=1.8, zorder=6, label="T")
    ax.plot(_xskew(td, p), p, color="#238b45", lw=1.8, zorder=7, label="Td")
    ax.plot(_xskew(t_virtual, p), p, color="#bf3b3b", lw=1.0,
            ls="--", zorder=5, label="Tv")
    if np.isfinite(t_wet).sum() >= 3:
        ax.plot(_xskew(t_wet, p), p, color="#13a6ac", lw=1.15,
                ls="-", zorder=5, label="Tw")
    parcel = _parcel_curve(pcl, p, t, td)
    parcel_good = np.isfinite(parcel) & np.isfinite(t_virtual) & np.isfinite(p)
    surface_parcel = getattr(pcl, "sfcpcl", None)
    lfc_for_shading = _finite(getattr(surface_parcel, "lfcpres", None)) if surface_parcel is not None else None
    el_for_shading = _finite(getattr(surface_parcel, "elpres", None)) if surface_parcel is not None else None
    env_x = _xskew(t_virtual, p)
    parcel_x = _xskew(parcel, p)
    # CAPE/CIN shading compares the virtual temperature of the SHARPpy surface
    # parcel with the environmental virtual temperature, limited to the LFC/EL
    # pressure interval. Missing LFC/EL values intentionally suppress shading.
    if np.isfinite(parcel_x).sum() > 3:
        cin_layer = parcel_good & (parcel < t_virtual)
        if lfc_for_shading is not None:
            cin_layer &= p >= lfc_for_shading
        else:
            cin_layer[:] = False
        if cin_layer.sum() > 1:
            ax.fill_betweenx(p, env_x, parcel_x, where=cin_layer,
                             interpolate=True, color="#3b6fd8", alpha=0.17,
                             linewidth=0, zorder=1)
        if lfc_for_shading is not None and el_for_shading is not None:
            cape_layer = (parcel_good & (parcel > t_virtual)
                          & (p <= lfc_for_shading) & (p >= el_for_shading))
            if cape_layer.sum() > 1:
                ax.fill_betweenx(p, env_x, parcel_x, where=cape_layer,
                                 interpolate=True, color="#d64545", alpha=0.16,
                                 linewidth=0, zorder=1)
        ax.plot(_xskew(parcel, p), p, color="#292929", lw=1.35,
                ls="--", zorder=5, label="Parcela SFC")
    # The plotted parcel path is surface-based, so LCL/LFC/EL markers must all
    # come from that same parcel. Never mix pressure levels from different parcel
    # definitions or draw a marker below the actual surface.
    surface_parcel = getattr(pcl, "sfcpcl", None)
    markers = (
        ("lclpres", "LCL", "#178447", 43.0),
        ("lfcpres", "LFC", "#b8860b", 47.4),
        ("elpres", "EL", "#8147a8", 51.8),
    )
    for attr, short, marker_color, label_x in markers:
        pressure_value = _finite(getattr(surface_parcel, attr, None)) if surface_parcel is not None else None
        if pressure_value is None and short == "LCL":
            try:
                from sharppy.sharptab import thermo
                pressure_value = float(thermo.drylift(float(p[0]), float(t[0]), float(td[0]))[0])
            except Exception:
                pressure_value = None
        if pressure_value is None or not (100.0 <= pressure_value <= np.nanmax(p) + 0.1):
            continue
        if pressure_value < np.nanmin(p) - 0.1 or pressure_value > surface_pressure + 0.1:
            # A parcel level below the local ground is physically outside this
            # sounding; never draw its marker in the blank sub-surface region.
            continue
        if pressure_value >= surface_pressure - 0.75:
            # A genuinely surface-based LCL is identified at the surface line,
            # not as a long dashed line inside the below-ground plotting area.
            ax.text(label_x, surface_pressure, f"{short}≈SFC", fontsize=6.3,
                    weight="bold", ha="left", va="bottom", color=marker_color,
                    bbox=dict(facecolor="white", edgecolor="none", alpha=0.84, pad=0.35),
                    clip_on=True, zorder=9)
            continue
        ax.axhline(pressure_value, color=marker_color, lw=0.85,
                   ls=(0, (3, 2)), alpha=0.92, zorder=3)
        ax.text(label_x, pressure_value, short, fontsize=7.0, weight="bold",
                ha="left", va="center", color=marker_color,
                bbox=dict(facecolor="white", edgecolor="none", alpha=0.86, pad=0.5),
                clip_on=False, zorder=9)

    # Freezing level, a common sounding reference mark.
    freeze_idx = np.where(np.isfinite(t[:-1]) & np.isfinite(t[1:]) & (t[:-1] * t[1:] <= 0))[0]
    if len(freeze_idx):
        i = int(freeze_idx[0])
        if abs(t[i + 1] - t[i]) > 1e-6:
            frac = -t[i] / (t[i + 1] - t[i])
            p0 = float(np.exp(np.log(p[i]) + frac * (np.log(p[i + 1]) - np.log(p[i]))))
            if 100 <= p0 <= 1050:
                ax.axhline(p0, color="#5d75a5", lw=0.6, ls=(0, (1, 3)), alpha=0.8, zorder=2)
                ax.text(36.5, p0, "0°C", fontsize=6.6, color="#526991",
                        ha="left", va="bottom", bbox=dict(facecolor="white", edgecolor="none", alpha=0.65, pad=0.25))

    # Draw 55 wind staffs at evenly spaced log-pressure levels. The interpolation
    # is display-only; the header still reports the number of native model levels.
    # Log spacing avoids stacking barbs near the top of a pressure-coordinate plot.
    barb_pressure = np.geomspace(float(p[0]), float(p[-1]), 55)
    barb_u = np.asarray([_interp_pressure(u, p, value) for value in barb_pressure])
    barb_v = np.asarray([_interp_pressure(v, p, value) for value in barb_pressure])
    barb_good = np.isfinite(barb_u) & np.isfinite(barb_v)
    if int(barb_good.sum()) < 50:
        raise RuntimeError(f"Somente {int(barb_good.sum())} barbelas de vento válidas; esperadas pelo menos 50.")
    ax.barbs(np.full(int(barb_good.sum()), 58.0), barb_pressure[barb_good],
             barb_u[barb_good], barb_v[barb_good], length=7.0,
             linewidth=0.95, barb_increments={"half": 5, "full": 10, "flag": 50},
             pivot="middle", color=INK, zorder=7)
    # Keep both header lines inside the canvas and outside the plotting frame.
    # A compact legend uses an otherwise clear upper-right pocket; labels make
    # the physical traces distinguishable without changing the panel layout.
    ax.legend(loc="upper center", bbox_to_anchor=(0.68, 0.985), ncol=2,
              fontsize=5.8, frameon=True, framealpha=0.88,
              borderpad=0.25, handlelength=1.5, handletextpad=0.35,
              labelspacing=0.2, columnspacing=0.65)
    ax.text(0.0, 1.050, f"Skew-T | {station}", transform=ax.transAxes,
            fontsize=9.0, weight="bold", ha="left", va="bottom",
            color=INK, clip_on=False)
    ax.text(0.0, 1.006, date_text, transform=ax.transAxes,
            fontsize=6.5, ha="left", va="bottom",
            color="#333333", clip_on=False)


def _make_theta_axes(ax, p, t, td):
    theta, thetae, thetaes = _theta_profiles(p, t, td)
    ax.set_yscale("log"); ax.set_ylim(1050, 100); ax.set_xlim(245, 380)
    ax.set_facecolor("white")
    ax.spines[:].set_color(INK); ax.spines[:].set_linewidth(0.85)
    ax.tick_params(axis="both", labelsize=6.5, colors=INK, direction="out", length=3, pad=2)
    ax.set_xticks([250, 280, 310, 340, 370])
    ax.set_yticks([1000, 900, 800, 700, 600, 500, 400, 300, 200, 100])
    ax.tick_params(axis="y", labelleft=False, left=False)
    surface_pressure = float(p[0])
    for pp in [1000,900,800,700,600,500,400,300,200,100]:
        if pp <= surface_pressure + 0.1:
            ax.axhline(pp, color="#dddddd", lw=0.48, zorder=0)
    ax.plot(theta, p, color=GREEN, lw=1.3, label=r"$\theta$")
    ax.plot(thetae, p, color=RED, lw=1.2, label=r"$\theta_e$")
    ax.plot(thetaes, p, color=BLUE, lw=1.2, label=r"$\theta_{es}$")
    leg = ax.legend(loc="upper left", fontsize=7, frameon=True, framealpha=0.9, borderpad=0.25, handlelength=2)
    leg.get_frame().set_facecolor("white"); leg.get_frame().set_edgecolor("#bcbcbc")


def _make_hodo(ax, z, u, v, title_text, motion, ground_m, critical_angle, preferred_label):
    h_agl = z - float(ground_m)
    h_agl[np.abs(h_agl) < 2.0] = 0.0
    order = np.argsort(h_agl)
    hh, uu, vv = h_agl[order], u[order], v[order]
    mw, rm, lm = motion
    valid_uv = np.isfinite(uu) & np.isfinite(vv)
    wind_speeds = np.hypot(uu[valid_uv], vv[valid_uv])
    motion_speeds = np.asarray([
        math.hypot(*mw), math.hypot(*rm), math.hypot(*lm)
    ], dtype=float)
    all_speeds = np.r_[wind_speeds, motion_speeds[np.isfinite(motion_speeds)]]
    max_radius = float(np.max(all_speeds)) if all_speeds.size else 0.0
    # Hodograph rings are radial wind-speed distances (kt), not component limits.
    # Keep the outer speed ring inside the frame with a 10-kt visual margin,
    # as on operational hodographs.
    radius = max(60, int(math.ceil((max_radius + 10.0) / 10.0) * 10))
    plot_limit = radius + 10
    ax.set_facecolor("white"); ax.set_aspect("equal", adjustable="box")
    ax.set_xlim(-plot_limit, plot_limit); ax.set_ylim(-plot_limit, plot_limit)
    ax.spines[:].set_color(INK); ax.spines[:].set_linewidth(0.9)
    ax.set_xticks([]); ax.set_yticks([])
    for r in range(10, radius + 1, 10):
        ax.add_patch(plt.Circle((0, 0), r, fill=False, lw=0.8 if r % 20 == 0 else 0.45,
                                edgecolor="#4c4c4c" if r % 20 == 0 else "#c9c9c9", zorder=0))
        # Label every 10-kt ring on both axes, matching the Sigma-style
        # polar hodograph reference instead of omitting alternate values.
        ax.text(-r, 1.5, f"{r}", fontsize=6.5, color="#a9a9a9", ha="center", va="bottom")
        ax.text(1.8, r, f"{r}", fontsize=6.5, color="#a9a9a9", ha="left", va="center")
    ax.axhline(0, color="#5c5c5c", lw=0.9, zorder=1); ax.axvline(0, color="#5c5c5c", lw=0.9, zorder=1)
    # Filter missing or duplicate heights before connecting wind vectors.
    valid = np.isfinite(hh) & np.isfinite(uu) & np.isfinite(vv) & (hh >= 0)
    hh, uu, vv = hh[valid], uu[valid], vv[valid]
    if len(hh):
        unique = np.r_[True, np.diff(hh) > 0.5]
        hh, uu, vv = hh[unique], uu[unique], vv[unique]
    pts = np.column_stack((uu, vv)).reshape(-1, 1, 2)
    if len(pts) >= 2:
        segments = np.concatenate([pts[:-1], pts[1:]], axis=1)
        mids = (hh[:-1] + hh[1:]) / 2.0
        bounds = [0, 2000, 4000, 9000, 11000, np.inf]
        colors = ["#003cff", "#a900d8", "#ff8a00", "#ff3030", "#ff3030"]
        seg_colors = [colors[min(np.searchsorted(bounds[1:], hz, side="right"), len(colors)-1)] for hz in mids]
        ax.add_collection(LineCollection(segments, colors=seg_colors, linewidths=2.2, zorder=4))
        # Show the individual sampled winds as well as the connected trace.
        ax.plot(uu, vv, linestyle="none", marker="o", markersize=2.0,
                markerfacecolor="#111111", markeredgewidth=0, alpha=0.72, zorder=5)
        for target, label in [(1000, "1"), (3000, "3"), (6000, "6"), (9000, "9"), (12000, "12")]:
            if hh.min() <= target <= hh.max():
                px, py = np.interp(target, hh, uu), np.interp(target, hh, vv)
                ax.plot(px, py, "o", ms=2.5, color="#111111", zorder=5)
                ax.text(px + 1.3, py + 1.3, label, fontsize=7, weight="bold", color=INK, zorder=6)
    for sx, sy, label in [(lm[0], lm[1], "LM"), (mw[0], mw[1], "MW"), (rm[0], rm[1], "RM")]:
        ax.annotate("", xy=(sx, sy), xytext=(0, 0),
                    arrowprops=dict(arrowstyle="-|>", lw=1.0, color="#c3c3c3", shrinkA=0, shrinkB=0), zorder=2)
        ax.text(sx + 2.5, sy + 2.0, label, fontsize=8, weight="bold", color=INK, zorder=6)
    ca = "--" if critical_angle is None else f"{critical_angle:.0f}°"
    # Put diagnostic text in the title area, not over the wind trace.
    hemisphere_short = "Sul" if "sul" in preferred_label.lower() else "Norte"
    mover_short = "LM" if "LM" in preferred_label else "RM"
    ax.set_title(
        f"{title_text}\nReferência: {mover_short} ({hemisphere_short}) • Ângulo crítico: {ca}",
        fontsize=7.8, loc="left", pad=3, color=INK,
    )


def _inferred_temp_advection(prof, latitude):
    """SHARPpy-style geostrophic thermal-advection estimate with corrected layer mean.

    This is an inferred sounding diagnostic, not the WRF model's horizontal
    temperature-advection field. SHARPpy's upstream routine multiplies the
    temperature sum by 2 instead of computing the arithmetic mean.
    """
    from sharppy.sharptab import interp, thermo, utils, winds

    pressure_profile = _arr(getattr(prof, "pres", []))
    sfc_pressure = _finite(prof.pres[prof.sfc])
    available = pressure_profile[np.isfinite(pressure_profile) & (pressure_profile >= 100.0)]
    if sfc_pressure is None or len(available) < 2:
        return np.asarray([]), np.empty((0, 2), dtype=float)

    top_pressure = float(available[-1])
    pressure = np.arange(float(sfc_pressure), top_pressure, -100.0, dtype=float)
    if len(pressure) < 2:
        return np.asarray([]), np.empty((0, 2), dtype=float)

    temperature_k = _arr(thermo.ctok(interp.temp(prof, pressure)))
    height_m = _arr(interp.hght(prof, pressure))
    direction = _arr(interp.vec(prof, pressure)[0])
    n = min(len(pressure), len(temperature_k), len(height_m), len(direction))
    pressure, temperature_k, height_m, direction = (
        arr[:n] for arr in (pressure, temperature_k, height_m, direction)
    )
    if n < 2:
        return np.asarray([]), np.empty((0, 2), dtype=float)

    coriolis_frequency = 2.0 * (2.0 * math.pi / 86164.0) * math.sin(math.radians(float(latitude)))
    multiplier = (coriolis_frequency / 9.81) * (math.pi / 180.0)
    values = np.full(n - 1, np.nan, dtype=float)
    bounds = np.full((n - 1, 2), np.nan, dtype=float)

    for i in range(1, n):
        p_bottom, p_top = float(pressure[i - 1]), float(pressure[i])
        z_bottom, z_top = float(height_m[i - 1]), float(height_m[i])
        t_bottom, t_top = float(temperature_k[i - 1]), float(temperature_k[i])
        d_bottom, d_top = float(direction[i - 1]), float(direction[i])
        bounds[i - 1] = (p_bottom, p_top)
        if not all(math.isfinite(v) for v in (
            p_bottom, p_top, z_bottom, z_top, t_bottom, t_top, d_bottom, d_top
        )):
            continue
        if z_top <= z_bottom:
            continue

        # Preserve SHARPpy's directional-angle unwrap and geostrophic-wind
        # estimate, but use the true arithmetic mean temperature for the layer.
        d_bottom %= 360.0
        d_top %= 360.0
        adjusted_top_direction = d_top + (180.0 - d_bottom)
        if adjusted_top_direction < 0.0:
            adjusted_top_direction += 360.0
        elif adjusted_top_direction >= 360.0:
            adjusted_top_direction -= 360.0
        directional_change = adjusted_top_direction - 180.0

        mean_u, mean_v = winds.mean_wind(prof, pbot=p_bottom, ptop=p_top)
        mean_speed_kt = _finite(utils.comp2vec(mean_u, mean_v)[1])
        if mean_speed_kt is None:
            continue
        mean_speed_ms = float(utils.KTS2MS(mean_speed_kt))
        mean_temperature_k = 0.5 * (t_bottom + t_top)
        values[i - 1] = (
            multiplier * mean_speed_ms ** 2 * mean_temperature_k
            * (directional_change / (z_top - z_bottom)) * 3600.0
        )

    return values, bounds


def _sweat_index_hemispheric(prof, latitude):
    """Miller/Nascimento SWEAT with mirrored directional criteria in the Southern Hemisphere."""
    from sharppy.sharptab import interp, params

    td850 = _finite(interp.dwpt(prof, 850.0))
    vec850, vec500 = interp.vec(prof, 850.0), interp.vec(prof, 500.0)
    dir850, speed850 = _finite(vec850[0]), _finite(vec850[1])
    dir500, speed500 = _finite(vec500[0]), _finite(vec500[1])
    tt = _finite(getattr(prof, "totals_totals", None))
    if tt is None:
        tt = _finite(params.t_totals(prof))
    if any(value is None for value in (td850, dir850, speed850, dir500, speed500, tt)):
        return None

    value = (12.0 * td850 if td850 > 0.0 else 0.0)
    value += (20.0 * (tt - 49.0) if tt >= 49.0 else 0.0)
    value += 2.0 * speed850 + speed500
    dir850 %= 360.0
    dir500 %= 360.0
    direction_term = 0.0

    if latitude < 0.0:
        # Nascimento's Southern Hemisphere adaptation:
        # 850-hPa direction 290–360/0–50°, 500-hPa direction 230–330°,
        # and a negative (wrapped) 500-minus-850 direction difference.
        dir850_for_difference = dir850 + 360.0 if dir850 <= 50.0 else dir850
        difference = dir500 - dir850_for_difference
        direction_ok = (
            (dir850 >= 290.0 or dir850 <= 50.0)
            and 230.0 <= dir500 <= 330.0
            and difference < 0.0
        )
        angle = abs(difference)
    else:
        difference = dir500 - dir850
        direction_ok = (
            130.0 <= dir850 <= 250.0
            and 210.0 <= dir500 <= 310.0
            and difference > 0.0
        )
        angle = abs(difference)

    if direction_ok and speed850 >= 15.0 and speed500 >= 15.0 and angle <= 180.0:
        direction_term = 125.0 * (math.sin(math.radians(angle)) + 0.2)
    return float(value + direction_term)


def _make_advection(ax, prof, latitude):
    ax.set_facecolor("white"); ax.set_yscale("log"); ax.set_ylim(1050, 100); ax.set_xlim(-2, 2)
    ax.spines[:].set_color(INK); ax.spines[:].set_linewidth(0.85)
    ax.tick_params(axis="both", labelsize=7, colors=INK, direction="out", length=3, pad=2)
    ax.set_xlabel("Advecção térmica inferida\n(estimativa, °C/h)", fontsize=6.4, labelpad=2)
    ax.set_xticks([-2, -1, 0, 1, 2])
    ax.set_yticks([1000,900,800,700,600,500,400,300,200,100])
    ax.tick_params(axis="y", labelleft=False, left=False)
    ax.axvline(0, color=INK, lw=0.9)
    surface_pressure = _finite(prof.pres[prof.sfc])
    if surface_pressure is None:
        raise RuntimeError("Pressão de superfície ausente no perfil de advecção inferida.")
    for pp in [1000,800,600,400,200,100]:
        if pp <= surface_pressure + 0.1:
            ax.axhline(pp, color="#e3e3e3", lw=0.45, zorder=0)

    try:
        adv_values, pressure_bounds = _inferred_temp_advection(prof, latitude)
        adv_values = _arr(adv_values)
        pressure_bounds = np.asarray(np.ma.asarray(pressure_bounds).filled(np.nan), dtype=float)
    except Exception as exc:
        raise RuntimeError(f"Falha ao calcular advecção térmica inferida: {exc}") from exc

    if pressure_bounds.ndim != 2 or pressure_bounds.shape[1] != 2:
        return
    n = min(len(adv_values), len(pressure_bounds))
    layers = []
    for value, (p_bottom, p_top) in zip(adv_values[:n], pressure_bounds[:n]):
        if not (math.isfinite(float(value)) and math.isfinite(float(p_bottom))
                and math.isfinite(float(p_top)) and 100 <= p_top < p_bottom <= 1050):
            continue
        value = float(value)
        # Do not cap extreme values: omit them and state the scale limitation.
        if abs(value) > 2.0:
            continue
        p_mid = math.sqrt(float(p_bottom) * float(p_top))
        layers.append((p_mid, value, float(p_bottom), float(p_top)))
    if not layers:
        ax.text(0.5, 0.5, "Sem dados válidos", transform=ax.transAxes, ha="center", va="center",
                fontsize=7.2, color="#555555")
        return

    layers.sort(key=lambda item: item[0], reverse=True)
    mid_pressure = np.asarray([row[0] for row in layers], dtype=float)
    values = np.asarray([row[1] for row in layers], dtype=float)
    # A thin profile trace is easier to read than opaque 100-hPa blocks.
    # Very light signed shading preserves warming/cooling polarity without
    # implying more vertical resolution than SHARPpy's layers provide.
    for p_mid, value, p_bottom, p_top in layers:
        color = "#c94c4c" if value > 0 else "#3266c5"
        ax.fill_betweenx([p_top, p_bottom], 0, value, color=color, alpha=0.12, linewidth=0, zorder=1)
    points = np.column_stack((values, mid_pressure)).reshape(-1, 1, 2)
    if len(points) >= 2:
        segments = np.concatenate([points[:-1], points[1:]], axis=1)
        segment_colors = [
            "#c94c4c" if (values[k] + values[k + 1]) / 2.0 > 0 else "#3266c5"
            for k in range(len(values) - 1)
        ]
        ax.add_collection(LineCollection(segments, colors=segment_colors, linewidths=1.35, zorder=3))
    point_colors = ["#c94c4c" if value > 0 else "#3266c5" for value in values]
    ax.scatter(values, mid_pressure, c=point_colors, s=12, edgecolors="white", linewidths=0.25, zorder=4)
    # Keep the range explicit. Extreme inferred values outside the plotted
    # +/-2 C/h window are omitted rather than silently clipped.
    ax.set_xlim(-2.0, 2.0)


def _make_srw(ax, h_agl, u, v, storm, title_text):
    height_valid = np.asarray(h_agl, dtype=float)
    height_valid = height_valid[np.isfinite(height_valid)]
    maximum_height_km = float(np.max(height_valid) / 1000.0) if height_valid.size else 0.0
    ymax = max(16, int(math.ceil(maximum_height_km / 2.0) * 2))
    y_ticks = np.arange(0, ymax + 0.1, 2)
    ax.set_facecolor("white"); ax.set_xlim(0, 50); ax.set_ylim(0, ymax)
    ax.spines[:].set_color(INK); ax.spines[:].set_linewidth(0.85)
    ax.tick_params(axis="both", labelsize=7.5, colors=INK, direction="out", length=3, pad=2)
    ax.set_ylabel("Altura AGL (km)", fontsize=7.5, labelpad=3)
    ax.set_xlabel("Vento relativo (kt)", fontsize=7.5, labelpad=3)
    ax.set_xticks([0, 10, 20, 30, 40, 50]); ax.set_yticks(y_ticks)
    for hz in y_ticks[1:]:
        ax.axhline(hz, color="#e4e4e4", lw=0.42, zorder=0)
    rel = np.hypot(u - storm[0], v - storm[1])
    good = np.isfinite(h_agl) & np.isfinite(rel)
    if good.sum() >= 2:
        order = np.argsort(h_agl[good]); hh, rr = h_agl[good][order] / 1000.0, rel[good][order]
        max_relative_wind = float(np.nanmax(rr))
        if max_relative_wind <= 80.0:
            xmax, xstep = max(50, int(math.ceil(max_relative_wind / 10.0) * 10)), 10
        else:
            xmax, xstep = int(math.ceil(max_relative_wind / 20.0) * 20), 20
        ax.set_xlim(0, xmax)
        ax.set_xticks(np.arange(0, xmax + 0.1, xstep))
        pts = np.column_stack((rr, hh)).reshape(-1, 1, 2)
        segments = np.concatenate([pts[:-1], pts[1:]], axis=1)
        mids = (hh[:-1] + hh[1:]) / 2
        color_bands = ["#ff8a00", "#003cff", "#a900d8", "#ff3030"]
        seg_colors = [color_bands[0 if h <= 2 else 1 if h <= 4 else 2 if h <= 9 else 3] for h in mids]
        ax.add_collection(LineCollection(segments, colors=seg_colors, linewidths=1.7, zorder=3))
    ax.set_title(title_text, fontsize=8.4, loc="left", pad=4, color=INK)
    ax.legend(handles=[
        plt.Line2D([0],[0], color="#ff8a00", lw=1.2, label="0-2 km"),
        plt.Line2D([0],[0], color="#003cff", lw=1.2, label="2-4 km"),
        plt.Line2D([0],[0], color="#a900d8", lw=1.2, label="4-9 km"),
        plt.Line2D([0],[0], color="#ff3030", lw=1.2, label="9+ km"),
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
        rows.append([label, _fmt(cape, 0), _fmt(cin, 0), _fmt(lcl, 0), _fmt(lfc, 0), _fmt(el, 0), _fmt(li, 1), _fmt(ehi1, 2), _fmt(ehi3, 2)])
    return rows


def _bottom_diagnostics(fig, prof, p, t, td, z, u, v, srh01, srh03, srh06, shear01, shear03, shear06, latitude):
    ax = fig.add_axes([0.035, 0.073, 0.63, 0.115]); ax.axis("off")
    cols = ["", "CAPE [J/kg]", "CIN [J/kg]", "LCL [m AGL]", "LFC [m AGL]", "EL [m AGL]", "LI [°C]", "EHI 0-1", "EHI 0-3"]
    table = ax.table(cellText=_parcel_rows(prof, srh01, srh03), colLabels=cols, cellLoc="center", colLoc="center", loc="upper left",
                     colWidths=[0.07,0.13,0.13,0.12,0.12,0.12,0.08,0.10,0.10])
    table.auto_set_font_size(False); table.set_fontsize(7.3); table.scale(1, 1.26)
    for (r,c), cell in table.get_celld().items():
        cell.set_edgecolor("white"); cell.set_linewidth(0.0); cell.set_facecolor("white")
        cell.get_text().set_color(INK)
        if r == 0:
            cell.get_text().set_fontweight("bold")

    ground_m = _finite(getattr(prof, "hght", [0])[getattr(prof, "sfc", 0)])
    if ground_m is None:
        ground_m = float(z[0])
    h_agl = z - ground_m
    h_agl[np.abs(h_agl) < 2.0] = 0.0
    lapse03 = _finite(getattr(prof, "lapserate_3km", None))
    lapse36 = _finite(getattr(prof, "lapserate_3_6km", None))
    lapse8505 = _finite(getattr(prof, "lapserate_850_500", None))
    lapse7005 = _finite(getattr(prof, "lapserate_700_500", None))
    # Fallbacks are only used if SHARPpy lacks the corresponding layer value.
    if lapse03 is None:
        lapse03 = _lapse_height(t, h_agl, 0, 3000)
    if lapse36 is None:
        lapse36 = _lapse_height(t, h_agl, 3000, 6000)
    if lapse8505 is None:
        lapse8505 = _lapse_pressure(t, z, p, 850, 500)
    if lapse7005 is None:
        lapse7005 = _lapse_pressure(t, z, p, 700, 500)
    pwat_inches = _finite(getattr(prof, "pwat", None))
    pwat = pwat_inches * 25.4 if pwat_inches is not None else _pwat_mm(p, td)
    kidx = _finite(getattr(prof, "k_idx", None))
    if kidx is None:
        t850, t700, t500 = (_interp_pressure(t, p, pp) for pp in (850,700,500))
        td850, td700 = (_interp_pressure(td, p, pp) for pp in (850,700))
        if all(math.isfinite(x) for x in (t850,t700,t500,td850,td700)):
            kidx = t850 - t500 + td850 - t700 + td700
    try:
        sweat_value = _sweat_index_hemispheric(prof, latitude)
        sweat = _fmt(sweat_value, 1)
    except Exception:
        sweat = "--"
    convt_f = _finite(getattr(prof, "convT", None))
    convt = _fmt((convt_f - 32.0) * (5.0 / 9.0), 1) if convt_f is not None else "--"
    dcape = _fmt(getattr(prof, "dcape", None), 0)
    sig = _fmt(getattr(prof, "sig_severe", None), 1)
    wndg = _fmt(getattr(prof, "wndg", None), 2)
    esrh_name = "left_esrh" if latitude < 0 else "right_esrh"
    esrh_values = getattr(prof, esrh_name, None)
    effective_srh_value = None
    if esrh_values is not None:
        try:
            effective_srh_value = _finite(esrh_values[0])
        except (IndexError, TypeError):
            effective_srh_value = None
    effective_srh = _fmt(effective_srh_value, 1, missing="--")
    ebwd = _attr(prof, ["ebwspd"], 1, suffix=" kt")
    stp_fixed = _fmt(getattr(prof, "stp_fixed", None), 2)
    stp_cin = _fmt(getattr(prof, "stp_cin", None), 2)
    ship = _fmt(getattr(prof, "ship", None), 2)
    scp = _fmt(getattr(prof, "scp", None), 2)
    microburst = _fmt(getattr(prof, "mburst", None), 2, missing="--")
    try:
        from sharppy.sharptab import params
        dcp_value = _fmt(params.dcp(prof), 3)
    except Exception:
        dcp_value = "--"
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
    sweat_label = "SWEAT-S" if latitude < 0 else "SWEAT"
    right = [f"DCAPE: {dcape} J/kg", f"{sweat_label}: {sweat}", f"µburst: {microburst}", f"DCP: {dcp_value}"]
    severe = [f"SigSevere: {sig} m³/s³", f"Wndg: {wndg}"]
    columns = [(0.037, left), (0.225, mid1), (0.392, mid2), (0.535, lapse), (0.700, right), (0.835, severe)]
    for x, items in columns:
        for j, label in enumerate(items):
            fig.text(x, 0.083 - j * 0.016, label, ha="left", va="center", fontsize=7.6, color=INK)


def render_native_spc(prof, out_dir: Path, meta: dict):
    """Render one SHARPpy profile as a classic white, multi-panel SPC-style PNG."""
    out_dir = Path(out_dir); out_dir.mkdir(parents=True, exist_ok=True)
    elevation = _finite(meta.get("elevation"))
    if elevation is None:
        try:
            elevation = _finite(prof.hght[prof.sfc])
        except Exception:
            elevation = None
    if elevation is None:
        raise RuntimeError("Altitude do terreno ausente; recusando calcular níveis AGL com referência presumida.")
    p, t, td, z, u, v, omega = _clean_profile(prof, ground_m=elevation)
    # Preserve native vertical spacing for all calculations. Densification is
    # only a display fallback for coarse profiles and is not used for indices.
    native_level_count = len(p)
    p, t, td, z, u, v, omega = _densify_profile(p, t, td, z, u, v, omega, count=55)
    h_agl = z - elevation
    h_agl[np.abs(h_agl) < 2.0] = 0.0
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
    station_raw = str(meta.get("location", meta.get("station", getattr(prof, "location", "SID"))) or "SID")
    station = station_raw.split("/", 1)[0].strip()
    state = str(meta.get("state", "") or "").strip()
    station_label = f"{station}, {state}" if state else station
    latitude = _finite(meta.get("latitude", getattr(prof, "latitude", None)))
    longitude = _finite(meta.get("longitude", getattr(prof, "longitude", None)))
    if latitude is None:
        raise RuntimeError("Latitude ausente; necessária para os cálculos meteorológicos dependentes do hemisfério.")
    lat_text = "--" if latitude is None else f"{latitude:.2f}{'N' if latitude >= 0 else 'S'}"
    lon_text = "--" if longitude is None else f"{longitude:.2f}{'E' if longitude >= 0 else 'W'}"
    model_hint = " ".join(str(meta.get(key, "")) for key in ("model", "station", "source"))
    model_label = "METBR WRF 4 km" if "METBR" in model_hint.upper() else str(meta.get("model", "ECMWF IFS"))
    if hasattr(valid, "strftime"):
        run_short = (valid_dt - timedelta(hours=fh)).strftime("%d/%m %HZ")
        valid_short = valid_dt.strftime("%d/%m %HZ")
    else:
        run_short, valid_short = run_text, valid_text
    date_text = (f"{model_label} • ciclo {run_short} • válido {valid_short} • F{fh:03d} • "
                 f"SFC {p[0]:.0f} hPa • terreno {elevation:.0f} m • {native_level_count} níveis nativos")

    motion = _storm_motion(prof, h_agl, u, v)
    # The cyclonic Bunkers mover changes hemisphere: RM in the Northern
    # Hemisphere, LM in the Southern Hemisphere. SHARPpy supplies both vectors.
    is_southern = latitude < 0
    preferred = motion[2] if is_southern else motion[1]
    preferred_label = "LM (hemisfério sul)" if is_southern else "RM (hemisfério norte)"
    try:
        from sharppy.sharptab import winds
        critical_angle = _finite(winds.critical_angle(prof, stu=preferred[0], stv=preferred[1]))
    except Exception:
        critical_angle = None
    srh01 = _srh(prof, 0, 1000, preferred)
    srh03 = _srh(prof, 0, 3000, preferred)
    srh06 = _srh(prof, 0, 6000, preferred)
    shear01, shear03, shear06 = (_shear(prof, h) for h in (1000, 3000, 6000))

    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 8, "axes.linewidth": 0.85, "savefig.facecolor": "white"})
    fig = plt.figure(figsize=(W / DPI, H / DPI), dpi=DPI, facecolor="white")
    ax_skew = fig.add_axes([0.078, 0.236, 0.402, 0.704])
    ax_theta = fig.add_axes([0.508, 0.236, 0.112, 0.704])
    ax_hodo = fig.add_axes([0.628, 0.550, 0.336, 0.390])
    ax_adv = fig.add_axes([0.650, 0.139, 0.142, 0.350])
    ax_srw = fig.add_axes([0.832, 0.139, 0.140, 0.350])

    _make_skew_axes(ax_skew, p, t, td, z, u, v, prof, station_label, date_text, elevation)
    _make_theta_axes(ax_theta, p, t, td)
    _make_hodo(ax_hodo, z, u, v, "Hodógrafa (vento em kt)", motion, elevation, critical_angle, preferred_label)
    _make_advection(ax_adv, prof, latitude)
    srw_title = "Vento relativo à tempestade (LM)" if is_southern else "Vento relativo à tempestade (RM)"
    _make_srw(ax_srw, h_agl, u, v, preferred, srw_title)
    _bottom_diagnostics(fig, prof, p, t, td, z, u, v, srh01, srh03, srh06, shear01, shear03, shear06, latitude)

    # Use the exact Sideral logo asset supplied for the brand; do not synthesize a text logo.
    logo_path = Path(__file__).resolve().parents[2] / "assets" / "sideral-logo.png"
    if not logo_path.is_file():
        raise FileNotFoundError(f"Logo oficial Sideral ausente: {logo_path}")
    with Image.open(logo_path) as source_logo:
        logo = source_logo.convert("RGBA")
    logo_ax = fig.add_axes([0.862, 0.008, 0.112, 0.046], zorder=20)
    logo_ax.imshow(logo, interpolation="lanczos")
    logo_ax.set_axis_off()
    fig.savefig(out_dir / "full.png", dpi=DPI, facecolor="white", bbox_inches=None)
    for ax, name in ((ax_skew, "skewt.png"), (ax_hodo, "hodograph.png")):
        bbox = ax.get_window_extent().transformed(fig.dpi_scale_trans.inverted())
        fig.savefig(out_dir / name, dpi=DPI, facecolor="white", bbox_inches=bbox.expanded(1.03, 1.03), pad_inches=0.02)
    plt.close(fig)
    return out_dir / "full.png"
