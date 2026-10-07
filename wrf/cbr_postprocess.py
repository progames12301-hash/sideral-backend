#!/usr/bin/env python3
import argparse
import glob
import json
import os
from pathlib import Path

import numpy as np
from netCDF4 import Dataset

EPS = 0.622

def read2d(ds, name):
    if name not in ds.variables:
        return None
    a = np.asarray(ds.variables[name][0], dtype=np.float32)
    while a.ndim > 2:
        a = a[0]
    return a

def native_reflectivity(ds):
    if "REFL_10CM" in ds.variables:
        a = np.asarray(ds.variables["REFL_10CM"][0], dtype=np.float32)
    elif "REFL_10CM_MAX" in ds.variables:
        a = np.asarray(ds.variables["REFL_10CM_MAX"][0], dtype=np.float32)
        return a
    else:
        return None
    while a.ndim > 3:
        a = a[0]
    if a.ndim == 3:
        surface = a[0]
        composite = np.nanmax(a, axis=0)
        return surface, composite
    if a.ndim == 2:
        return a, a
    return None

def dewpoint_and_rh(t2_k, q2, psfc_pa):
    t2_c = t2_k - 273.15
    q2 = np.maximum(q2, 0.0)
    psfc_hpa = psfc_pa / 100.0
    vapor_hpa = q2 * psfc_hpa / (EPS + q2)
    vapor_hpa = np.maximum(vapor_hpa, 0.01)
    td_c = 243.5 * np.log(vapor_hpa / 6.112) / (17.67 - np.log(vapor_hpa / 6.112))
    es = 6.112 * np.exp((17.67 * t2_c) / (t2_c + 243.5))
    rh = np.clip(100.0 * vapor_hpa / es, 0.0, 100.0)
    return td_c.astype(np.float32), rh.astype(np.float32)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--output-dir", default="cbr_products")
    args = ap.parse_args()
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    index = []

    for path in sorted(args.files):
        with Dataset(path) as ds:
            ds.set_auto_maskandscale(True)
            times = ds.variables.get("Times")
            stamp = Path(path).name.replace("wrfout_d01_", "").replace(":", "")
            if times is not None:
                raw = times[0]
                stamp = "".join(x.decode() if isinstance(x, bytes) else str(x) for x in raw)

            lat = read2d(ds, "XLAT")
            lon = read2d(ds, "XLONG")
            t2 = read2d(ds, "T2")
            q2 = read2d(ds, "Q2")
            psfc = read2d(ds, "PSFC")
            u10 = read2d(ds, "U10")
            v10 = read2d(ds, "V10")

            variables = {}
            if lat is not None: variables["lat"] = lat
            if lon is not None: variables["lon"] = lon
            ref = native_reflectivity(ds)
            if ref is not None:
                variables["reflectivity_native_dbz"] = ref[0]
                variables["reflectivity_composite_dbz"] = ref[1]
            if u10 is not None: variables["u10_ms"] = u10
            if v10 is not None: variables["v10_ms"] = v10
            if u10 is not None and v10 is not None:
                variables["wind10_ms"] = np.hypot(u10, v10).astype(np.float32)
                variables["wind10_dir_deg"] = ((np.degrees(np.arctan2(-u10, -v10)) + 360.0) % 360.0).astype(np.float32)
            if t2 is not None: variables["t2_c"] = (t2 - 273.15).astype(np.float32)
            if q2 is not None: variables["q2_kgkg"] = q2.astype(np.float32)
            if psfc is not None: variables["psfc_hpa"] = (psfc / 100.0).astype(np.float32)
            if t2 is not None and q2 is not None and psfc is not None:
                td2, rh2 = dewpoint_and_rh(t2, q2, psfc)
                variables["td2_c"] = td2
                variables["rh2_pct"] = rh2
            for name, key in [
                ("SLP", "slp_hpa"),
                ("RAINC", "rainc_mm"),
                ("RAINNC", "rainnc_mm"),
                ("RAINSH", "rainsh_mm"),
                ("SNOWNC", "snownc_mm"),
                ("GRAUPELNC", "graupelnc_mm"),
                ("HAILNC", "hailnc_mm"),
                ("CLDFRA", "cloud_fraction"),
            ]:
                a = read2d(ds, name)
                if a is not None:
                    variables[key] = a
            if "rainc_mm" in variables and "rainnc_mm" in variables:
                variables["rain_total_mm"] = (variables["rainc_mm"] + variables["rainnc_mm"]).astype(np.float32)

            safe = stamp.replace("-", "").replace(":", "").replace(".", "_")
            output = out / (safe + ".npz")
            np.savez_compressed(output, **variables)
            index.append({
                "file": os.path.basename(path),
                "time": stamp,
                "product": output.name,
                "shape": list(lat.shape) if lat is not None else None,
                "variables": sorted(variables.keys()),
            })

    manifest = out / "metadata.json"
    manifest.write_text(json.dumps({
        "model": "WRF CBR 4 KM",
        "resolutionKm": 4,
        "nativeReflectivity": "REFL_10CM",
        "projection": "WRF Lambert",
        "domain": {"e_we": 361, "e_sn": 445, "approxLon": [-59.0, -48.0], "approxLat": [-34.0, -22.0]},
        "frames": index,
        "products": {
            "reflectivity_native_dbz": "native REFL_10CM, lowest model level",
            "reflectivity_composite_dbz": "maximum native REFL_10CM through the column",
            "wind10_ms": "derived from U10/V10",
            "wind10_dir_deg": "derived from U10/V10; frontend draws barbs",
            "td2_c": "derived from T2/Q2/PSFC",
            "rh2_pct": "derived from T2/Q2/PSFC",
            "rain_total_mm": "RAINC + RAINNC",
        },
    }, ensure_ascii=False, indent=2), encoding="utf-8")

if __name__ == "__main__":
    main()
