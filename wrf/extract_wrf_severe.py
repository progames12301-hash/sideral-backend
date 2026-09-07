from __future__ import annotations
import argparse
import datetime as dt
import gzip
import json
import math
import re
from pathlib import Path
import numpy as np
import xarray as xr
G = 9.80665
RD = 287.05
CP = 1004.0
EPS = 0.622
KAPPA = RD / CP
LV = 2500000.0

def parse_run_env(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    if not path.exists():
        return result
    for line in path.read_text(encoding='utf-8', errors='ignore').splitlines():
        if '=' not in line:
            continue
        key, value = line.split('=', 1)
        result[key.strip()] = value.strip()
    return result

def parse_valid_time(path: Path) -> dt.datetime:
    match = re.search('wrfout_d01_(\\d{4}-\\d{2}-\\d{2})_(\\d{2})[-:](\\d{2})[-:](\\d{2})', path.name)
    if not match:
        raise ValueError(f'Nome wrfout inesperado: {path.name}')
    return dt.datetime.strptime(f'{match.group(1)} {match.group(2)}:{match.group(3)}:{match.group(4)}', '%Y-%m-%d %H:%M:%S').replace(tzinfo=dt.timezone.utc)

def write_gzip_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(payload, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')
    with path.open('wb') as raw:
        with gzip.GzipFile(filename='', mode='wb', fileobj=raw, compresslevel=9, mtime=0) as zipped:
            zipped.write(body)

def sample_indices(size: int, target: int) -> np.ndarray:
    target = max(1, min(target, size))
    return np.rint(np.linspace(0, size - 1, target)).astype(int)

def sample2d(values: np.ndarray, rows: np.ndarray, cols: np.ndarray) -> np.ndarray:
    return np.asarray(values)[np.ix_(rows, cols)]

def sample3d(values: np.ndarray, rows: np.ndarray, cols: np.ndarray) -> np.ndarray:
    arr = np.asarray(values)
    return arr[:, rows, :][:, :, cols]

def clean(values: np.ndarray, lo: float | None=None, hi: float | None=None) -> np.ndarray:
    arr = np.asarray(values, dtype=np.float64)
    arr = np.nan_to_num(arr, nan=0.0, posinf=0.0, neginf=0.0)
    if lo is not None or hi is not None:
        arr = np.clip(arr, -np.inf if lo is None else lo, np.inf if hi is None else hi)
    return arr

def flat_round(values: np.ndarray, decimals: int, lo: float | None=None, hi: float | None=None) -> list[float]:
    arr = clean(values, lo, hi)
    return np.round(arr, decimals).reshape(-1).tolist()

def dewpoint_from_qp(q: np.ndarray, p_pa: np.ndarray) -> np.ndarray:
    q = np.clip(np.asarray(q, dtype=np.float64), 1e-08, 0.08)
    p = np.clip(np.asarray(p_pa, dtype=np.float64), 1000.0, 110000.0)
    e = np.clip(q * p / (EPS + q), 1.0, p * 0.99)
    ln = np.log(e / 611.2)
    td_c = 243.5 * ln / (17.67 - ln)
    return td_c + 273.15

def saturation_vapor_pressure_pa(t_k: np.ndarray) -> np.ndarray:
    tc = np.asarray(t_k, dtype=np.float64) - 273.15
    return 611.2 * np.exp(17.67 * tc / (tc + 243.5))

def saturation_mixing_ratio(p_pa: np.ndarray, t_k: np.ndarray) -> np.ndarray:
    p = np.asarray(p_pa, dtype=np.float64)
    es = np.clip(saturation_vapor_pressure_pa(t_k), 1.0, p * 0.98)
    return np.clip(EPS * es / np.maximum(1.0, p - es), 0.0, 0.08)

def theta_e_bolton(t_k: np.ndarray, q: np.ndarray, p_pa: np.ndarray) -> np.ndarray:
    t = np.clip(np.asarray(t_k, dtype=np.float64), 180.0, 340.0)
    p = np.clip(np.asarray(p_pa, dtype=np.float64), 1000.0, 110000.0)
    r = np.clip(np.asarray(q, dtype=np.float64), 1e-08, 0.08)
    td = np.clip(dewpoint_from_qp(r, p), 170.0, t)
    tl = 1.0 / (1.0 / (td - 56.0) + np.log(t / td) / 800.0) + 56.0
    theta = t * (100000.0 / p) ** (0.2854 * (1.0 - 0.28 * r))
    return theta * np.exp((3376.0 / tl - 2.54) * r * (1.0 + 0.81 * r))

def lcl_height_m(t_k: np.ndarray, q: np.ndarray, p_pa: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    t = np.clip(np.asarray(t_k, dtype=np.float64), 180.0, 340.0)
    td = np.clip(dewpoint_from_qp(q, p_pa), 170.0, t)
    tl = 1.0 / (1.0 / (td - 56.0) + np.log(t / td) / 800.0) + 56.0
    dz = np.maximum(0.0, (t - tl) / (G / CP))
    p_lcl = np.asarray(p_pa, dtype=np.float64) * (tl / t) ** (1.0 / KAPPA)
    return (dz, tl, p_lcl)

def interp_vertical(coord: np.ndarray, values: np.ndarray, target: float, log_coord: bool=False) -> np.ndarray:
    c = np.asarray(coord, dtype=np.float64)
    v = np.asarray(values, dtype=np.float64)
    if log_coord:
        c = np.log(np.clip(c, 1.0, None))
        tgt = math.log(target)
    else:
        tgt = float(target)
    out = np.full(c.shape[1:], np.nan, dtype=np.float64)
    for k in range(c.shape[0] - 1):
        c0, c1 = (c[k], c[k + 1])
        v0, v1 = (v[k], v[k + 1])
        between = ((tgt - c0) * (tgt - c1) <= 0.0) & np.isfinite(c0) & np.isfinite(c1)
        denom = c1 - c0
        frac = np.divide(tgt - c0, denom, out=np.zeros_like(c0), where=np.abs(denom) > 1e-12)
        candidate = v0 + frac * (v1 - v0)
        fill = between & ~np.isfinite(out)
        out[fill] = candidate[fill]
    return out

def wind_profile_at_heights(z_agl: np.ndarray, u: np.ndarray, v: np.ndarray, u10: np.ndarray, v10: np.ndarray, heights: list[float]) -> tuple[np.ndarray, np.ndarray]:
    us, vs = ([], [])
    for h in heights:
        if h <= 1.0:
            us.append(np.asarray(u10, dtype=np.float64))
            vs.append(np.asarray(v10, dtype=np.float64))
        else:
            us.append(interp_vertical(z_agl, u, h))
            vs.append(interp_vertical(z_agl, v, h))
    return (np.stack(us, axis=0), np.stack(vs, axis=0))

def bunkers_cyclonic_motion(lat: np.ndarray, u_prof: np.ndarray, v_prof: np.ndarray, heights: list[float]) -> tuple[np.ndarray, np.ndarray]:
    hs = np.asarray(heights)
    mean_mask = hs <= 6000.0
    low_mask = hs <= 500.0
    high_mask = hs >= 5500.0
    u_mean = np.nanmean(u_prof[mean_mask], axis=0)
    v_mean = np.nanmean(v_prof[mean_mask], axis=0)
    u_low = np.nanmean(u_prof[low_mask], axis=0)
    v_low = np.nanmean(v_prof[low_mask], axis=0)
    u_high = np.nanmean(u_prof[high_mask], axis=0)
    v_high = np.nanmean(v_prof[high_mask], axis=0)
    du = u_high - u_low
    dv = v_high - v_low
    mag = np.hypot(du, dv)
    mag = np.where(mag < 1e-06, np.nan, mag)
    sign = np.where(np.asarray(lat) < 0.0, -1.0, 1.0)
    d = 7.5
    cu = u_mean + sign * d * (dv / mag)
    cv = v_mean - sign * d * (du / mag)
    cu = np.where(np.isfinite(cu), cu, u_mean)
    cv = np.where(np.isfinite(cv), cv, v_mean)
    return (cu, cv)

def srh_from_profile(u_prof: np.ndarray, v_prof: np.ndarray, heights: list[float], storm_u: np.ndarray, storm_v: np.ndarray, top_m: float) -> np.ndarray:
    hs = np.asarray(heights)
    idx = np.where(hs <= top_m)[0]
    u = u_prof[idx]
    v = v_prof[idx]
    total = np.zeros_like(storm_u, dtype=np.float64)
    for k in range(len(idx) - 1):
        term = (u[k + 1] - storm_u) * (v[k] - storm_v) - (u[k] - storm_u) * (v[k + 1] - storm_v)
        total += np.nan_to_num(term, nan=0.0)
    return total

def parcel_cape_cin(p3: np.ndarray, t3: np.ndarray, q3: np.ndarray, z3: np.ndarray, p0: np.ndarray, t0: np.ndarray, q0: np.ndarray, z0: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    p0 = np.asarray(p0, dtype=np.float64)
    t0 = np.asarray(t0, dtype=np.float64)
    q0 = np.asarray(q0, dtype=np.float64)
    z0 = np.asarray(z0, dtype=np.float64)
    lcl_dz, tlcl, _ = lcl_height_m(t0, q0, p0)
    zlcl = z0 + lcl_dz
    zdelta = z3 - z0[None, :, :]
    dry = t0[None, :, :] - G / CP * zdelta
    moist = tlcl[None, :, :] - 0.006 * (z3 - zlcl[None, :, :])
    tpar = np.where(z3 <= zlcl[None, :, :], dry, moist)
    tpar = np.clip(tpar, 170.0, 350.0)
    qsat = saturation_mixing_ratio(p3, tpar)
    qpar = np.where(z3 <= zlcl[None, :, :], q0[None, :, :], qsat)
    tv_par = tpar * (1.0 + 0.61 * qpar)
    tv_env = t3 * (1.0 + 0.61 * np.clip(q3, 0.0, 0.08))
    buoy = G * (tv_par - tv_env) / np.maximum(180.0, tv_env)
    valid = (z3 >= z0[None, :, :]) & (p3 <= p0[None, :, :] * 1.01) & np.isfinite(buoy) & np.isfinite(z3)
    buoy = np.where(valid, buoy, np.nan)
    cape = np.zeros_like(p0, dtype=np.float64)
    cin = np.zeros_like(p0, dtype=np.float64)
    positive_seen = np.zeros_like(p0, dtype=bool)
    for k in range(z3.shape[0] - 1):
        dz = z3[k + 1] - z3[k]
        b0, b1 = (buoy[k], buoy[k + 1])
        area = 0.5 * (b0 + b1) * dz
        ok = np.isfinite(area) & (dz > 0.0) & (dz < 3000.0)
        pos = ok & (area > 0.0)
        neg_before_lfc = ok & (area < 0.0) & ~positive_seen
        cape += np.where(pos, area, 0.0)
        cin += np.where(neg_before_lfc, area, 0.0)
        positive_seen |= pos
    return (np.clip(cape, 0.0, 8000.0), np.clip(cin, -600.0, 0.0))

def mixed_layer_parcel(p3: np.ndarray, t3: np.ndarray, q3: np.ndarray, psfc: np.ndarray, z0: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    theta3 = t3 * (100000.0 / np.clip(p3, 1000.0, None)) ** KAPPA
    mask = (p3 <= psfc[None, :, :] + 1000.0) & (p3 >= psfc[None, :, :] - 10000.0)
    count = np.maximum(1, np.sum(mask, axis=0))
    theta_mean = np.sum(np.where(mask, theta3, 0.0), axis=0) / count
    q_mean = np.sum(np.where(mask, q3, 0.0), axis=0) / count
    t0 = theta_mean * (psfc / 100000.0) ** KAPPA
    return (psfc, t0, np.clip(q_mean, 1e-08, 0.08), z0)

def most_unstable_parcel(p3: np.ndarray, t3: np.ndarray, q3: np.ndarray, z3: np.ndarray, psfc: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    thetae = theta_e_bolton(t3, q3, p3)
    mask = (p3 <= psfc[None, :, :] + 1000.0) & (p3 >= psfc[None, :, :] - 30000.0)
    score = np.where(mask, thetae, -np.inf)
    idx = np.argmax(score, axis=0)
    yy, xx = np.indices(psfc.shape)
    p0 = p3[idx, yy, xx]
    t0 = t3[idx, yy, xx]
    q0 = q3[idx, yy, xx]
    z0 = z3[idx, yy, xx]
    return (p0, t0, q0, z0)

def native_2d(dataset: xr.Dataset, names: list[str], rows: np.ndarray, cols: np.ndarray) -> np.ndarray | None:
    for name in names:
        if name not in dataset:
            continue
        raw = np.asarray(dataset[name].isel(Time=0).to_numpy(), dtype=np.float64)
        if raw.ndim == 2:
            return sample2d(raw, rows, cols)
    return None

def compute_frame(dataset: xr.Dataset, rows: np.ndarray, cols: np.ndarray) -> tuple[dict[str, np.ndarray], dict]:
    def raw(name: str) -> np.ndarray:
        if name not in dataset:
            raise RuntimeError(f'Variavel WRF ausente para WRF2: {name}')
        return dataset[name].isel(Time=0).to_numpy()
    lat = sample2d(raw('XLAT'), rows, cols)
    lon = sample2d(raw('XLONG'), rows, cols)
    t2 = sample2d(raw('T2'), rows, cols)
    q2 = sample2d(raw('Q2'), rows, cols)
    psfc = sample2d(raw('PSFC'), rows, cols)
    hgt = sample2d(raw('HGT'), rows, cols) if 'HGT' in dataset else np.zeros_like(t2)
    u10 = sample2d(raw('U10'), rows, cols)
    v10 = sample2d(raw('V10'), rows, cols)
    pressure = sample3d(raw('P') + raw('PB'), rows, cols)
    theta = sample3d(raw('T') + 300.0, rows, cols)
    temp = theta * (pressure / 100000.0) ** KAPPA
    qv = np.clip(sample3d(raw('QVAPOR'), rows, cols), 0.0, 0.08)
    ph = raw('PH') + raw('PHB')
    z_stag = sample3d(ph / G, rows, cols)
    z_mass = 0.5 * (z_stag[:-1] + z_stag[1:])
    z_agl = z_mass - hgt[None, :, :]
    u_native = 0.5 * (raw('U')[:, :, :-1] + raw('U')[:, :, 1:])
    v_native = 0.5 * (raw('V')[:, :-1, :] + raw('V')[:, 1:, :])
    u = sample3d(u_native, rows, cols)
    v = sample3d(v_native, rows, cols)
    if 'W' in dataset:
        w_raw = raw('W')
        w_mass_native = 0.5 * (w_raw[:-1] + w_raw[1:])
        w = sample3d(w_mass_native, rows, cols)
    else:
        w = np.zeros_like(temp)
    t850 = interp_vertical(pressure, temp, 85000.0, log_coord=True)
    t700 = interp_vertical(pressure, temp, 70000.0, log_coord=True)
    t500 = interp_vertical(pressure, temp, 50000.0, log_coord=True)
    q850 = interp_vertical(pressure, qv, 85000.0, log_coord=True)
    q700 = interp_vertical(pressure, qv, 70000.0, log_coord=True)
    u850 = interp_vertical(pressure, u, 85000.0, log_coord=True)
    v850 = interp_vertical(pressure, v, 85000.0, log_coord=True)
    u500 = interp_vertical(pressure, u, 50000.0, log_coord=True)
    v500 = interp_vertical(pressure, v, 50000.0, log_coord=True)
    z500 = interp_vertical(pressure, z_mass, 50000.0, log_coord=True)
    w700 = interp_vertical(pressure, w, 70000.0, log_coord=True)
    td2 = dewpoint_from_qp(q2, psfc)
    td850 = dewpoint_from_qp(q850, np.full_like(q850, 85000.0))
    td700 = dewpoint_from_qp(q700, np.full_like(q700, 70000.0))
    lcl_h, _, _ = lcl_height_m(t2, q2, psfc)
    thetae850 = theta_e_bolton(t850, q850, np.full_like(t850, 85000.0))
    dx = float(dataset.attrs.get('DX', 4000.0))
    dy = float(dataset.attrs.get('DY', 4000.0))
    dthdx = np.gradient(thetae850, dx, axis=1)
    dthdy = np.gradient(thetae850, dy, axis=0)
    thetae_adv = -(u850 * dthdx + v850 * dthdy) * 3600.0
    dvdx500 = np.gradient(v500, dx, axis=1)
    dudy500 = np.gradient(u500, dy, axis=0)
    vort500 = dvdx500 - dudy500
    tv700 = t700 * (1.0 + 0.61 * np.clip(q700, 0.0, 0.08))
    rho700 = 70000.0 / (RD * np.maximum(180.0, tv700))
    omega700 = -rho700 * G * w700
    pwat = -np.trapezoid(qv, pressure, axis=0) / G
    pwat = np.clip(pwat, 0.0, 100.0)
    heights = [0.0] + [float(h) for h in range(250, 6001, 250)]
    up, vp = wind_profile_at_heights(z_agl, u, v, u10, v10, heights)
    storm_u, storm_v = bunkers_cyclonic_motion(lat, up, vp, heights)
    srh01 = srh_from_profile(up, vp, heights, storm_u, storm_v, 1000.0)
    srh03 = srh_from_profile(up, vp, heights, storm_u, storm_v, 3000.0)
    u6 = up[-1]
    v6 = vp[-1]
    shear06 = np.hypot(u6 - u10, v6 - v10)
    z0 = hgt + 2.0
    sbcape_native = native_2d(dataset, ['SBCAPE', 'AFWA_SBCAPE'], rows, cols)
    cin_native = native_2d(dataset, ['SBCIN', 'CIN', 'AFWA_CIN'], rows, cols)
    mlcape_native = native_2d(dataset, ['MLCAPE', 'AFWA_MLCAPE'], rows, cols)
    mucape_native = native_2d(dataset, ['MUCAPE', 'MCAPE', 'AFWA_CAPE'], rows, cols)
    sbcape_derived, sbcin_derived = parcel_cape_cin(pressure, temp, qv, z_mass, psfc, t2, q2, z0)
    ml_p, ml_t, ml_q, ml_z = mixed_layer_parcel(pressure, temp, qv, psfc, z0)
    mlcape_derived, mlcin_derived = parcel_cape_cin(pressure, temp, qv, z_mass, ml_p, ml_t, ml_q, ml_z)
    mu_p, mu_t, mu_q, mu_z = most_unstable_parcel(pressure, temp, qv, z_mass, psfc)
    mucape_derived, _ = parcel_cape_cin(pressure, temp, qv, z_mass, mu_p, mu_t, mu_q, mu_z)
    sbcape = sbcape_native if sbcape_native is not None else sbcape_derived
    cin = cin_native if cin_native is not None else sbcin_derived
    mlcape = mlcape_native if mlcape_native is not None else mlcape_derived
    mucape = mucape_native if mucape_native is not None else mucape_derived
    effective_shear = shear06.copy()
    cyclonic_srh01 = np.where(lat < 0.0, np.maximum(0.0, -srh01), np.maximum(0.0, srh01))
    cyclonic_srh03 = np.where(lat < 0.0, np.maximum(0.0, -srh03), np.maximum(0.0, srh03))
    lcl_term = np.clip((2000.0 - lcl_h) / 1000.0, 0.0, 1.0)
    srh_term = np.clip(cyclonic_srh01 / 150.0, 0.0, 3.0)
    cape_term = np.clip(mlcape / 1500.0, 0.0, 4.0)
    shear_term = np.where(shear06 < 12.5, 0.0, np.where(shear06 > 30.0, 1.5, shear06 / 20.0))
    cin_term = np.where(mlcin_derived >= -50.0, 1.0, np.where(mlcin_derived <= -200.0, 0.0, (200.0 + mlcin_derived) / 150.0))
    stp = np.clip(cape_term * lcl_term * srh_term * shear_term * cin_term, 0.0, 10.0)
    scp = np.clip(mucape / 1000.0 * np.clip(cyclonic_srh03 / 50.0, 0.0, 6.0) * np.clip(effective_shear / 20.0, 0.0, 2.5), 0.0, 50.0)
    tv2 = t2 * (1.0 + 0.61 * np.clip(q2, 0.0, 0.08))
    mslp = psfc * np.exp(G * np.maximum(-500.0, hgt) / (RD * np.maximum(220.0, tv2))) / 100.0
    z1000 = hgt + RD * np.maximum(220.0, tv2) / G * np.log(np.clip(psfc, 1000.0, None) / 100000.0)
    thickness = (z500 - z1000) / 10.0
    t850c = t850 - 273.15
    t700c = t700 - 273.15
    t500c = t500 - 273.15
    td850c = td850 - 273.15
    td700c = td700 - 273.15
    k_index = t850c - t500c + td850c - (t700c - td700c)
    total_totals = t850c + td850c - 2.0 * t500c
    fields = {'lat': lat, 'lon': lon, 'stp': stp, 'scp': scp, 'srh01': srh01, 'srh03': srh03, 'bulkShear06': shear06, 'effectiveBulkShear': effective_shear, 'lclHeight': lcl_h, 'cin': cin, 'sbcape': sbcape, 'mlcape': mlcape, 'mucapeWrf2': mucape, 'pwat': pwat, 'thetaE850': thetae850, 'thetaEAdvection': thetae_adv, 'wind850': np.hypot(u850, v850), 'wind500': np.hypot(u500, v500), 'vorticity500': vort500, 'omega700': omega700, 'mslp': mslp, 'thickness': thickness, 'dewpoint2m': td2 - 273.15, 'kIndex': k_index, 'totalTotals': total_totals}
    methods = {'stp': 'derived_fixed_layer_STP_with_hemisphere_adjusted_cyclonic_SRH', 'scp': 'derived_SCP_using_MUCAPE_SRH03_and_effective_shear_proxy', 'srh': 'Bunkers_7.5mps_cyclonic_motion_250m_hodograph', 'bulkShear06': '10m_to_6km_AGL_vector_difference', 'effectiveBulkShear': 'proxy_bulk_shear_0_6km', 'lclHeight': 'Bolton_surface_parcel', 'cape': {'sbcape': 'native' if sbcape_native is not None else 'derived_vectorized_parcel_profile', 'mlcape': 'native' if mlcape_native is not None else 'derived_lowest_100hPa_mixed_layer', 'mucape': 'native' if mucape_native is not None else 'derived_max_thetae_lowest_300hPa', 'cin': 'native' if cin_native is not None else 'derived_surface_parcel'}, 'pwat': 'vertical_integral_QVAPOR_dp_over_g', 'thetaE850': 'Bolton_1980_interpolated_850hPa', 'thetaEAdvection': 'minus_V_dot_grad_thetaE_850', 'vorticity500': 'dvdx_minus_dudy_500hPa', 'omega700': 'hydrostatic_conversion_minus_rho_g_w_700hPa', 'mslp': 'hypsometric_reduction_from_PSFC_T2_HGT', 'thickness': 'Z500_minus_extrapolated_Z1000', 'kIndex': 'T850_T500_Td850_T700_Td700', 'totalTotals': 'T850_plus_Td850_minus_2T500'}
    return (fields, methods)

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--run-dir', default='wrf_work/run')
    parser.add_argument('--run-env', default='wrf_diagnostics/run.env')
    parser.add_argument('--output-dir', default='wrf2_publish')
    parser.add_argument('--grid-x', type=int, default=220)
    parser.add_argument('--grid-y', type=int, default=180)
    parser.add_argument('--model', default='gfs', choices=('gfs', 'icon', 'ecmwf'))
    parser.add_argument('--interval-hours', type=int, default=3)
    args = parser.parse_args()
    run_dir = Path(args.run_dir)
    files = sorted(run_dir.glob('wrfout_d01_*'), key=parse_valid_time)
    if not files:
        raise SystemExit(f'Nenhum wrfout encontrado em {run_dir}')
    env = parse_run_env(Path(args.run_env))
    run_date = env.get('RUN_DATE', '')
    run_cycle = env.get('RUN_CYCLE', '')
    if run_date and run_cycle:
        init_time = dt.datetime.strptime(f'{run_date}{run_cycle}', '%Y%m%d%H').replace(tzinfo=dt.timezone.utc)
    else:
        init_time = parse_valid_time(files[0])
        run_date = init_time.strftime('%Y%m%d')
        run_cycle = init_time.strftime('%H')
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    frames: list[dict] = []
    method_summary = None
    rows = cols = None
    for path in files:
        valid_time = parse_valid_time(path)
        fh = max(0, int(round((valid_time - init_time).total_seconds() / 3600.0)))
        if args.interval_hours > 1 and fh % args.interval_hours != 0:
            continue
        print(f'[WRF2] Extraindo {path.name} => F{fh:03d}')
        with xr.open_dataset(path, engine='netcdf4', decode_times=False) as dataset:
            if rows is None or cols is None:
                lats_native = dataset['XLAT'].isel(Time=0).to_numpy()
                native_y, native_x = lats_native.shape
                rows = sample_indices(native_y, args.grid_y)
                cols = sample_indices(native_x, args.grid_x)
            fields, methods = compute_frame(dataset, rows, cols)
            method_summary = methods
            dx = float(dataset.attrs.get('DX', 4000.0))
            dy = float(dataset.attrs.get('DY', 4000.0))
        lat = fields['lat']
        lon = fields['lon']
        payload = {'schema': 'sideral-wrf2-severe-grid-v1', 'model': args.model, 'source': f'WRF 2 Sudeste 4 km {args.model.upper()} · diagnósticos severos', 'runDate': run_date, 'runCycle': f'{run_cycle}Z', 'initTime': init_time.isoformat().replace('+00:00', 'Z'), 'forecastHour': fh, 'validTime': valid_time.isoformat().replace('+00:00', 'Z'), 'dxMeters': int(round(dx)), 'dyMeters': int(round(dy)), 'gridX': len(cols), 'gridY': len(rows), 'bounds': {'south': round(float(np.nanmin(lat)), 4), 'west': round(float(np.nanmin(lon)), 4), 'north': round(float(np.nanmax(lat)), 4), 'east': round(float(np.nanmax(lon)), 4)}, 'diagnosticMethods': methods, 'fields': {'lat': flat_round(fields['lat'], 4), 'lon': flat_round(fields['lon'], 4), 'stp': flat_round(fields['stp'], 2, 0.0, 10.0), 'scp': flat_round(fields['scp'], 2, 0.0, 50.0), 'srh01': flat_round(fields['srh01'], 0, -1000.0, 1000.0), 'srh03': flat_round(fields['srh03'], 0, -1500.0, 1500.0), 'bulkShear06': flat_round(fields['bulkShear06'], 1, 0.0, 100.0), 'effectiveBulkShear': flat_round(fields['effectiveBulkShear'], 1, 0.0, 100.0), 'lclHeight': flat_round(fields['lclHeight'], 0, 0.0, 5000.0), 'cin': flat_round(fields['cin'], 0, -600.0, 0.0), 'sbcape': flat_round(fields['sbcape'], 0, 0.0, 8000.0), 'mlcape': flat_round(fields['mlcape'], 0, 0.0, 8000.0), 'mucapeWrf2': flat_round(fields['mucapeWrf2'], 0, 0.0, 8000.0), 'pwat': flat_round(fields['pwat'], 1, 0.0, 100.0), 'thetaE850': flat_round(fields['thetaE850'], 1, 250.0, 400.0), 'thetaEAdvection': flat_round(fields['thetaEAdvection'], 2, -20.0, 20.0), 'wind850': flat_round(fields['wind850'], 1, 0.0, 100.0), 'wind500': flat_round(fields['wind500'], 1, 0.0, 120.0), 'vorticity500': flat_round(fields['vorticity500'], 7, -0.002, 0.002), 'omega700': flat_round(fields['omega700'], 3, -20.0, 20.0), 'mslp': flat_round(fields['mslp'], 1, 850.0, 1080.0), 'thickness': flat_round(fields['thickness'], 1, 450.0, 650.0), 'dewpoint2m': flat_round(fields['dewpoint2m'], 1, -80.0, 40.0), 'kIndex': flat_round(fields['kIndex'], 1, -50.0, 70.0), 'totalTotals': flat_round(fields['totalTotals'], 1, -20.0, 80.0)}}
        rel = f'severe/{args.model}/f{fh:03d}.json.gz'
        write_gzip_json(output_dir / rel, payload)
        frames.append({'index': len(frames), 'forecastHour': fh, 'validTime': payload['validTime'], 'file': rel, 'gridX': payload['gridX'], 'gridY': payload['gridY']})
    if not frames:
        raise SystemExit('Nenhum frame WRF2 selecionado')
    metadata = {'schema': 'sideral-wrf2-severe-metadata-v1', 'model': args.model, 'resolutionKm': 4, 'runDate': run_date, 'runCycle': f'{run_cycle}Z', 'initTime': init_time.isoformat().replace('+00:00', 'Z'), 'generatedAt': dt.datetime.now(dt.timezone.utc).isoformat().replace('+00:00', 'Z'), 'frameCount': len(frames), 'forecastHourStart': min((int(x['forecastHour']) for x in frames)), 'forecastHourEnd': max((int(x['forecastHour']) for x in frames)), 'temporalResolutionMinutes': args.interval_hours * 60, 'fields': ['stp', 'scp', 'srh01', 'srh03', 'bulkShear06', 'effectiveBulkShear', 'lclHeight', 'cin', 'sbcape', 'mlcape', 'mucapeWrf2', 'pwat', 'thetaE850', 'thetaEAdvection', 'wind850', 'wind500', 'vorticity500', 'omega700', 'mslp', 'thickness', 'dewpoint2m', 'kIndex', 'totalTotals'], 'diagnosticMethods': method_summary or {}, 'frames': frames}
    (output_dir / 'metadata.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(metadata, ensure_ascii=False, indent=2))
if __name__ == '__main__':
    main()
