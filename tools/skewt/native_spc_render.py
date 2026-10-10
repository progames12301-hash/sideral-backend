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


def _make_skew_axes(ax, p, t, td, z, u, v, pcl, station, date_text, ground_m):
    ax.set_yscale("log")
    ax.set_ylim(1050, 100)
    ax.set_xlim(-43, 64)
    ax.set_facecolor("white")
    ax.spines[:].set_color(INK)
    ax.spines[:].set_linewidth(0.85)
    ax.tick_params(axis="both", labelsize=8, colors=INK, direction="out", length=3, pad=2)
    ax.set_xlabel("Temperature (°C)", fontsize=9, labelpad=4)
    ax.set_ylabel("Pressure (hPa)", fontsize=9, labelpad=8)

    # Pressure grid: major every 100 hPa, supporting levels every 25 hPa.
    p_ticks = [1050, 1000, 900, 800, 700, 600, 500, 400, 300, 200, 100]
    p_minor = [float(pp) for pp in np.arange(125, 1000, 25) if pp not in p_ticks]
    ax.yaxis.set_major_locator(FixedLocator(p_ticks))
    ax.yaxis.set_major_formatter(FuncFormatter(lambda val, pos: f"{int(val)}" if any(abs(val-x)<0.5 for x in p_ticks) else ""))
    ax.yaxis.set_minor_locator(FixedLocator(p_minor))
    ax.yaxis.set_minor_formatter(NullFormatter())
    ax.set_xticks(np.arange(-40, 56, 10))
    ax.set_xticks(np.arange(-40, 56, 5), minor=True)
    ax.tick_params(axis="x", which="minor", length=2, color="#777777")
    ps = np.geomspace(100, 1050, 240)
    for pp in p_ticks:
        ax.axhline(pp, color="#a7a7a7", lw=0.62, zorder=0)
    for pp in p_minor:
        ax.axhline(pp, color="#e4e4e4", lw=0.34, zorder=0)
    surface_pressure = float(p[0])
    if 100.0 <= surface_pressure <= 1050.0:
        # Explicitly mark the real station surface; no trace is shown below it.
        ax.axhline(surface_pressure, color="#555555", lw=0.9,
                   ls=(0, (4, 2)), alpha=0.9, zorder=2)

    # Isotherms every 5 C; every 10 C is emphasized.
    for temp in np.arange(-90, 66, 5):
        major = (int(temp) % 10 == 0)
        ax.plot(_xskew(np.full_like(ps, temp), ps), ps,
                ls=(0, (2, 3)), color="#7192c4" if major else "#b7b7b7",
                lw=0.54 if major else 0.38, alpha=0.80 if major else 0.66, zorder=0)

    # Dry adiabats every 5 K (10 K emphasized), giving a denser thermodynamic mesh.
    for theta_k in np.arange(250, 506, 5):
        tt = theta_k * (ps / 1000.0) ** 0.2854 - 273.15
        major = (int(theta_k) % 10 == 0)
        ax.plot(_xskew(tt, ps), ps, color="#c49a5a" if major else "#dfc6a1",
                ls=(0, (2, 3)), lw=0.58 if major else 0.40,
                alpha=0.82 if major else 0.66, zorder=0)

    # Moist adiabats every 5 C. Vectorized SHARPpy wet lifting is much faster
    # than evaluating each point separately for each of the 459 generated frames.
    from sharppy.sharptab import thermo
    pp_grid = np.geomspace(1000.0, 100.0, 100)
    # wetlift accepts scalar p/t/p2. Evaluate it level by level rather than
    # silently swallowing an exception and accidentally omitting moist adiabats.
    for start_t in np.arange(-30, 41, 5):
        curve = np.asarray(
            [float(thermo.wetlift(1000.0, float(start_t), float(pp))) for pp in pp_grid],
            dtype=float,
        )
        if not np.isfinite(curve).all():
            raise RuntimeError(f"SHARPpy gerou uma adiabat úmida inválida a {start_t:.0f} °C.")
        major = (int(start_t) % 10 == 0)
        ax.plot(_xskew(curve, pp_grid), pp_grid,
                color="#3c8752" if major else "#8fb99a",
                ls="-" if major else (0, (2, 3)),
                lw=0.72 if major else 0.48,
                alpha=0.92 if major else 0.76, zorder=0)

    # Mixing-ratio lines, in g/kg, with the lower-value lines kept subtle.
    for r in [0.1, 0.2, 0.4, 0.6, 0.8, 1, 1.5, 2, 3, 4, 5, 6, 8, 10, 12, 16, 20, 24, 28, 32]:
        e = (r * ps) / (622.0 + r)
        loge = np.log(np.maximum(e, 0.01) / 6.112)
        tdline = 243.5 * loge / (17.67 - loge)
        major = r in (0.4, 1, 2, 4, 8, 16, 24, 32)
        ax.plot(_xskew(tdline, ps), ps, color="#86b68e" if major else "#b2ceb5",
                ls=(0, (1, 3)), lw=0.56 if major else 0.38,
                alpha=0.78 if major else 0.62, zorder=0)
    h_agl = z - float(ground_m)
    h_agl[np.abs(h_agl) < 2.0] = 0.0
    for pp in [1050, 1000, 900, 800, 700, 600, 500, 400, 300, 200, 100]:
        height = _interp_pressure(h_agl, p, pp)
        if math.isfinite(height):
            ax.text(-41.8, pp, f"{height:.1f} m", ha="left", va="center", fontsize=6.6, color="#303030", clip_on=True)
    ax.plot(_xskew(t, p), p, color=INK, lw=1.65, zorder=5, label="T")
    ax.plot(_xskew(td, p), p, color=BLUE, lw=1.85, zorder=6, label="Td")
    parcel = _parcel_curve(p, t, td)
    if np.isfinite(parcel).sum() > 3:
        ax.plot(_xskew(parcel, p), p, color=RED, lw=1.25, ls="--", zorder=4)
    # Mark LCL/LFC/EL using whichever SHARPpy parcel contains each value.
    parcel_objects = [getattr(pcl, n, None) for n in ("sfcpcl", "mlpcl", "mupcl", "fcstpcl")]
    markers = (("lclpres", "LCL"), ("lfcpres", "LFC"), ("elpres", "EL"))
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
            ax.axhline(pressure_value, color="#2c2c2c", lw=0.72,
                       ls=(0, (3, 3)), alpha=0.84, zorder=3)
            ax.text(57.0, pressure_value, short, fontsize=7.2, weight="bold",
                    ha="left", va="center", color="#303030",
                    bbox=dict(facecolor="white", edgecolor="none", alpha=0.76, pad=0.5),
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
                ax.text(43.0, p0, "0°C", fontsize=6.6, color="#526991",
                        ha="left", va="bottom", bbox=dict(facecolor="white", edgecolor="none", alpha=0.65, pad=0.25))

    # Frequent wind barbs, as in an operational sounding.
    idx = np.unique(np.linspace(0, len(p) - 1, min(50, len(p))).round().astype(int))
    ax.barbs(np.full(len(idx), 53.5), p[idx], u[idx], v[idx], length=4.4,
             linewidth=0.55, barb_increments={"half": 5, "full": 10, "flag": 50},
             pivot="middle", color=INK, zorder=7)
    ax.set_title(f"Skew-T | {station}\n{date_text}", fontsize=9.0, loc="left", pad=6, color=INK)


def _make_theta_axes(ax, p, t, td):
    theta, thetae, thetaes = _theta_profiles(p, t, td)
    ax.set_yscale("log"); ax.set_ylim(1050, 100); ax.set_xlim(245, 380)
    ax.set_facecolor("white")
    ax.spines[:].set_color(INK); ax.spines[:].set_linewidth(0.85)
    ax.tick_params(axis="both", labelsize=7, colors=INK, direction="out", length=3, pad=2)
    ax.set_xticks([245, 270, 295, 320, 345, 370])
    ax.set_yticks([1000, 900, 800, 700, 600, 500, 400, 300, 200, 100])
    ax.tick_params(axis="y", labelleft=False, left=False)
    for pp in [1000,900,800,700,600,500,400,300,200,100]:
        ax.axhline(pp, color="#dddddd", lw=0.48, zorder=0)
    ax.plot(theta, p, color=GREEN, lw=1.3, label=r"$\theta$")
    ax.plot(thetae, p, color=RED, lw=1.2, label=r"$\theta_e$")
    ax.plot(thetaes, p, color=BLUE, lw=1.2, label=r"$\theta_{es}$")
    leg = ax.legend(loc="upper left", fontsize=7, frameon=True, framealpha=0.9, borderpad=0.25, handlelength=2)
    leg.get_frame().set_facecolor("white"); leg.get_frame().set_edgecolor("#bcbcbc")


def _make_hodo(ax, z, u, v, title_text, motion, ground_m, critical_angle, preferred_label):
    h_agl = z - float(ground_m)
    h_agl[np.abs(h_agl) < 2.0] = 0.0
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
    ax.set_title(title_text, fontsize=9.0, loc="left", pad=6, color=INK)
    ca = "--" if critical_angle is None else f"{critical_angle:.0f}°"
    ax.text(0.018, 0.975, f"Movimento de referência: {preferred_label}\nÂngulo crítico: {ca}",
            transform=ax.transAxes, fontsize=7.3, ha="left", va="top", color=INK,
            bbox=dict(facecolor="white", edgecolor="none", alpha=0.88, pad=1.6), zorder=8)


def _make_advection(ax, prof, latitude):
    ax.set_facecolor("white"); ax.set_yscale("log"); ax.set_ylim(1050, 100); ax.set_xlim(-2, 2)
    ax.spines[:].set_color(INK); ax.spines[:].set_linewidth(0.85)
    ax.tick_params(axis="both", labelsize=7, colors=INK, direction="out", length=3, pad=2)
    ax.set_xlabel("Advecção térmica inferida (°C/h)", fontsize=7.4, labelpad=3)
    ax.set_xticks([-2, -1, 0, 1, 2])
    ax.set_yticks([1000,900,800,700,600,500,400,300,200,100])
    ax.tick_params(axis="y", labelleft=False, left=False)
    ax.axvline(0, color=INK, lw=0.9)
    for pp in [1000,800,600,400,200,100]:
        ax.axhline(pp, color="#e3e3e3", lw=0.45, zorder=0)

    try:
        from sharppy.sharptab import params
        adv_values, pressure_bounds = params.inferred_temp_adv(prof, lat=latitude)
        adv_values = _arr(adv_values)
        pressure_bounds = np.asarray(np.ma.asarray(pressure_bounds).filled(np.nan), dtype=float)
    except Exception as exc:
        raise RuntimeError(f"Falha ao calcular advecção térmica inferida SHARPpy: {exc}") from exc

    if pressure_bounds.ndim != 2 or pressure_bounds.shape[1] != 2:
        return
    n = min(len(adv_values), len(pressure_bounds))
    drawn = 0
    for value, (p_bottom, p_top) in zip(adv_values[:n], pressure_bounds[:n]):
        if not (math.isfinite(float(value)) and math.isfinite(float(p_bottom))
                and math.isfinite(float(p_top)) and 100 <= p_top < p_bottom <= 1050):
            continue
        plotted = float(np.clip(value, -2.0, 2.0))
        color = "#c94c4c" if plotted > 0 else "#3266c5"
        ax.fill_betweenx([p_top, p_bottom], 0, plotted, color=color, alpha=0.72, linewidth=0, zorder=2)
        ax.plot([plotted, plotted], [p_top, p_bottom], color=color, lw=0.55, zorder=3)
        drawn += 1
    if drawn == 0:
        ax.text(0.5, 0.5, "Sem dados", transform=ax.transAxes, ha="center", va="center",
                fontsize=7.2, color="#555555")


def _make_srw(ax, h_agl, u, v, storm, title_text):
    ax.set_facecolor("white"); ax.set_xlim(0, 50); ax.set_ylim(0, 16)
    ax.spines[:].set_color(INK); ax.spines[:].set_linewidth(0.85)
    ax.tick_params(axis="both", labelsize=7.5, colors=INK, direction="out", length=3, pad=2)
    ax.set_ylabel("Altura AGL (km)", fontsize=7.5, labelpad=3)
    ax.set_xlabel("Vento relativo (kt)", fontsize=7.5, labelpad=3)
    ax.set_xticks([0, 10, 20, 30, 40, 50]); ax.set_yticks([0,2,4,6,8,10,12,14,16])
    for hz in [2,4,6,8,10,12,14,16]:
        ax.axhline(hz, color="#e4e4e4", lw=0.42, zorder=0)
    rel = np.hypot(u - storm[0], v - storm[1])
    good = np.isfinite(h_agl) & np.isfinite(rel)
    if good.sum() >= 2:
        order = np.argsort(h_agl[good]); hh, rr = h_agl[good][order] / 1000.0, rel[good][order]
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
    lapse03, lapse36 = _lapse_height(t, h_agl, 0, 3000), _lapse_height(t, h_agl, 3000, 6000)
    lapse8505, lapse7005 = _lapse_pressure(t, z, p, 850, 500), _lapse_pressure(t, z, p, 700, 500)
    pwat_inches = _finite(getattr(prof, "pwat", None))
    pwat = pwat_inches * 25.4 if pwat_inches is not None else _pwat_mm(p, td)
    kidx = _finite(getattr(prof, "k_idx", None))
    if kidx is None:
        t850, t700, t500 = (_interp_pressure(t, p, pp) for pp in (850,700,500))
        td850, td700 = (_interp_pressure(td, p, pp) for pp in (850,700))
        if all(math.isfinite(x) for x in (t850,t700,t500,td850,td700)):
            kidx = t850 - t500 + td850 - t700 + td700
    try:
        from sharppy.sharptab import params
        sweat = _fmt(params.sweat(prof), 1)
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
    dcp_value = _fmt(getattr(prof, "dcp", None), 3)
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
    date_text = f"METBR WRF 4 km • ciclo {run_text} • válido {valid_text} • F{fh:03d} • SFC {p[0]:.0f} hPa • {elevation:.0f} m"

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
    shear01, shear03, shear06 = (_shear(h_agl, u, v, h) for h in (1000, 3000, 6000))

    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 8, "axes.linewidth": 0.85, "savefig.facecolor": "white"})
    fig = plt.figure(figsize=(W / DPI, H / DPI), dpi=DPI, facecolor="white")
    ax_skew = fig.add_axes([0.078, 0.236, 0.402, 0.704])
    ax_theta = fig.add_axes([0.508, 0.236, 0.112, 0.704])
    ax_hodo = fig.add_axes([0.628, 0.550, 0.336, 0.390])
    ax_adv = fig.add_axes([0.650, 0.139, 0.142, 0.400])
    ax_srw = fig.add_axes([0.832, 0.139, 0.140, 0.400])

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
