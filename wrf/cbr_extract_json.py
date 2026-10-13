#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import json
import re
from pathlib import Path

import numpy as np
from netCDF4 import Dataset

EPS = 0.622


def arr2d(ds, name):
    if name not in ds.variables:
        return None
    a = np.asarray(ds.variables[name][0], dtype=np.float32)
    while a.ndim > 2:
        a = a[0]
    if a.ndim != 2:
        return None
    return a


def flatten(a, decimals=2):
    if a is None:
        return None
    x = np.asarray(a, dtype=np.float32)
    x = np.round(x, decimals)
    return [float(v) if np.isfinite(v) else None for v in x.reshape(-1)]


def scalar_meta(a):
    if a is None:
        return None
    x = np.asarray(a, dtype=np.float32)
    finite = x[np.isfinite(x)]
    if finite.size == 0:
        return {"min": None, "max": None}
    return {"min": float(np.min(finite)), "max": float(np.max(finite))}


def read_time(ds):
    var = ds.variables.get("Times")
    if var is None:
        return None
    raw = np.asarray(var[0])
    text = "".join(x.decode() if isinstance(x, bytes) else str(x) for x in raw)
    text = text.replace("_", " ")
    try:
        return dt.datetime.strptime(text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=dt.timezone.utc)
    except ValueError:
        return None


def read_run_init(path: Path):
    """Read the authoritative initialization time from the checkpoint's cbr-run.env."""
    if not path.exists():
        return None

    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")

    run_date = values.get("RUN_DATE", "").replace("-", "")
    run_cycle = values.get("RUN_CYCLE", "")
    if not re.fullmatch(r"\d{8}", run_date) or not re.fullmatch(r"\d{2}", run_cycle):
        raise SystemExit(
            f"[CBR] cbr-run.env inválido: RUN_DATE={run_date!r}, RUN_CYCLE={run_cycle!r}"
        )
    if run_cycle not in {"00", "06", "12", "18"}:
        raise SystemExit(f"[CBR] ciclo inválido em cbr-run.env: {run_cycle!r}")
    try:
        return dt.datetime.strptime(run_date + run_cycle, "%Y%m%d%H").replace(
            tzinfo=dt.timezone.utc
        )
    except ValueError as exc:
        raise SystemExit(f"[CBR] data/ciclo inválido em cbr-run.env: {exc}") from exc


def native_reflectivity(ds):
    if "REFL_10CM" in ds.variables:
        a = np.asarray(ds.variables["REFL_10CM"][0], dtype=np.float32)
    elif "REFL_10CM_MAX" in ds.variables:
        a = np.asarray(ds.variables["REFL_10CM_MAX"][0], dtype=np.float32)
        while a.ndim > 2:
            a = a[0]
        return a, a
    else:
        return None, None

    while a.ndim > 3:
        a = a[0]
    if a.ndim == 3:
        return a[0], np.nanmax(a, axis=0)
    if a.ndim == 2:
        return a, a
    return None, None


def dewpoint_rh(t2, q2, psfc):
    q = np.maximum(np.asarray(q2, dtype=np.float32), 1e-8)
    p = np.asarray(psfc, dtype=np.float32)
    t = np.asarray(t2, dtype=np.float32)
    vapor = q * p / (EPS + q)
    vapor = np.maximum(vapor, 1.0)
    l = np.log(vapor / 611.2)
    td = 243.5 * l / (17.67 - l)
    tc = t - 273.15
    es = 611.2 * np.exp(17.67 * tc / (tc + 243.5))
    rh = np.clip(100.0 * vapor / es, 0.0, 100.0)
    return td.astype(np.float32), rh.astype(np.float32)


def frame_payload(path):
    with Dataset(path) as ds:
        ds.set_auto_maskandscale(True)
        lat = arr2d(ds, "XLAT")
        lon = arr2d(ds, "XLONG")
        if lat is None or lon is None:
            raise RuntimeError(f"XLAT/XLONG ausentes em {path.name}")

        refl, comp = native_reflectivity(ds)
        if refl is None or comp is None:
            raise RuntimeError(f"REFL_10CM/REFL_10CM_MAX ausente em {path.name}")
        u10, v10 = arr2d(ds, "U10"), arr2d(ds, "V10")
        t2, q2, psfc = arr2d(ds, "T2"), arr2d(ds, "Q2"), arr2d(ds, "PSFC")
        slp = arr2d(ds, "SLP")
        rainc, rainnc, rainsh = arr2d(ds, "RAINC"), arr2d(ds, "RAINNC"), arr2d(ds, "RAINSH")
        snownc, graupel, hail, cldfra = [arr2d(ds, n) for n in ("SNOWNC", "GRAUPELNC", "HAILNC", "CLDFRA")]

        fields = {}
        fields["lat"] = flatten(lat, 4)
        fields["lon"] = flatten(lon, 4)

        if refl is not None:
            fields["reflectivityDbz"] = flatten(refl, 1)
        if comp is not None:
            fields["reflectivityCompositeDbz"] = flatten(comp, 1)

        if u10 is not None:
            fields["u10Ms"] = flatten(u10, 1)
        if v10 is not None:
            fields["v10Ms"] = flatten(v10, 1)
        if u10 is not None and v10 is not None:
            wind = np.hypot(u10, v10)
            direction = (np.degrees(np.arctan2(-u10, -v10)) + 360.0) % 360.0
            fields["wind10Ms"] = flatten(wind, 1)
            fields["wind10DirDeg"] = flatten(direction, 1)

        if t2 is not None:
            fields["t2C"] = flatten(t2 - 273.15, 1)
        if q2 is not None:
            fields["q2KgKg"] = flatten(q2, 6)
        if psfc is not None:
            fields["psfcHpa"] = flatten(psfc / 100.0, 1)
        if slp is not None:
            fields["slpHpa"] = flatten(slp / 100.0 if np.nanmedian(slp) > 2000 else slp, 1)
        if t2 is not None and q2 is not None and psfc is not None:
            td2, rh2 = dewpoint_rh(t2, q2, psfc)
            fields["td2C"] = flatten(td2, 1)
            fields["rh2Pct"] = flatten(rh2, 1)

        for value, key, decimals in (
            (rainc, "raincMm", 2), (rainnc, "rainncMm", 2), (rainsh, "rainshMm", 2),
            (snownc, "snowncMm", 2), (graupel, "graupelncMm", 2),
            (hail, "hailncMm", 2), (cldfra, "cloudFraction", 3),
        ):
            if value is not None:
                fields[key] = flatten(value, decimals)

        if rainc is not None and rainnc is not None:
            fields["rainTotalMm"] = flatten(rainc + rainnc, 2)

        valid = read_time(ds)
        if valid is None:
            raise RuntimeError(f"Tempo Times ausente/invalido em {path.name}")

        cell_count = int(lat.size)
        for key, value in fields.items():
            if value is not None and len(value) != cell_count:
                raise RuntimeError(
                    f"Campo {key} com {len(value)} células em {path.name}; "
                    f"XLAT/XLONG possuem {cell_count}"
                )

        return {
            "file": path.name,
            "time": valid.isoformat().replace("+00:00", "Z"),
            "grid": {"nx": int(lat.shape[1]), "ny": int(lat.shape[0])},
            "fields": fields,
            "ranges": {
                key: scalar_meta(np.asarray(value, dtype=np.float32).reshape(lat.shape))
                for key, value in (
                    ("reflectivityDbz", refl),
                    ("reflectivityCompositeDbz", comp),
                    ("wind10Ms", np.hypot(u10, v10) if u10 is not None and v10 is not None else None),
                    ("t2C", t2 - 273.15 if t2 is not None else None),
                    ("psfcHpa", psfc / 100.0 if psfc is not None else None),
                )
                if value is not None
            },
        }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True)
    ap.add_argument("--output-dir", required=True)
    ap.add_argument("--expected-start", type=int, default=0)
    ap.add_argument("--expected-end", type=int, default=42)
    ap.add_argument("--run-env", default="cbr-run.env")
    ap.add_argument("--catalog", default="wrf/cbr_variable_catalog.json")
    ap.add_argument(
        "--require-complete",
        action="store_true",
        help="falha se algum horário previsto estiver ausente",
    )
    args = ap.parse_args()

    if args.expected_start < 0 or args.expected_end < args.expected_start:
        raise SystemExit(
            f"[CBR] intervalo de previsão inválido: F{args.expected_start}-F{args.expected_end}"
        )

    catalog_path = Path(args.catalog)
    try:
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        domain = catalog["domain"]
        e_we, e_sn = int(domain["e_we"]), int(domain["e_sn"])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise SystemExit(f"[CBR] catálogo WRF inválido em {catalog_path}: {exc}") from exc

    if e_we < 2 or e_sn < 2:
        raise SystemExit(f"[CBR] dimensões e_we/e_sn inválidas no catálogo: {e_we} x {e_sn}")

    # WRF's e_we/e_sn include staggered points. Scalar fields (XLAT/XLONG, REFL_10CM)
    # live on the unstaggered mass grid, whose dimensions are e_we-1 by e_sn-1.
    expected_grid = {"nx": e_we - 1, "ny": e_sn - 1}

    run_dir = Path(args.run_dir)
    out = Path(args.output_dir)
    frames_dir = out / "frames"
    out.mkdir(parents=True, exist_ok=True)
    frames_dir.mkdir(parents=True, exist_ok=True)
    for stale in frames_dir.glob("f*.json.gz"):
        stale.unlink()

    files = sorted(run_dir.glob("wrfout_d01_*"))
    if not files:
        raise SystemExit(f"[CBR] nenhum WRFOUT encontrado em {run_dir}")

    unique = {}
    for path in files:
        try:
            with Dataset(path) as ds:
                valid = read_time(ds)
        except Exception as exc:
            print(f"[CBR][SKIP] NetCDF ilegível {path.name}: {exc}")
            continue
        if valid is None:
            print(f"[CBR][SKIP] tempo Times ausente/inválido: {path.name}")
            continue
        if valid in unique:
            print(
                f"[CBR][DUPLICADO] {path.name} e {unique[valid].name} têm o mesmo horário; "
                "mantendo o primeiro arquivo em ordem lexical"
            )
            continue
        unique[valid] = path

    selected = sorted(unique.items())
    if not selected:
        raise SystemExit(f"[CBR] nenhum WRFOUT legível com variável Times em {run_dir}")

    init = read_run_init(Path(args.run_env))
    if init is None:
        if args.expected_start != 0:
            raise SystemExit(
                f"[CBR] {args.run_env} ausente: não é seguro calcular F{args.expected_start}+ "
                "a partir do primeiro arquivo de um segmento. Forneça RUN_DATE/RUN_CYCLE."
            )
        init = selected[0][0]
        print(
            "[CBR][AVISO] cbr-run.env ausente; usando o primeiro WRFOUT como F000. "
            "A completude do ciclo será verificada."
        )

    frames = []

    for valid, path in selected:
        delta_hours = (valid - init).total_seconds() / 3600.0
        fh = int(round(delta_hours))
        if abs(delta_hours - fh) > 1e-6:
            raise SystemExit(
                f"[CBR] horário WRF fora da cadência horária: {path.name}, "
                f"init={init.isoformat()}, diferença={delta_hours:.4f} h"
            )
        if fh < 0:
            raise SystemExit(
                f"[CBR] WRFOUT anterior ao ciclo selecionado: {path.name}; "
                f"init={init.isoformat()}, validade={valid.isoformat()}"
            )
        if fh < args.expected_start or fh > args.expected_end:
            continue

        payload = frame_payload(path)
        if payload["grid"] != expected_grid:
            raise SystemExit(
                f"[CBR] grade inválida em {path.name}: observado {payload['grid']}, "
                f"esperado {expected_grid} a partir de e_we={e_we}, e_sn={e_sn} "
                "(grade de massa WRF = e_we-1 x e_sn-1)"
            )
        payload["model"] = "WRF CBR 4 KM"
        payload["modelKey"] = "cbr_wrf_4km"
        payload["resolutionKm"] = 4
        payload["forecastHour"] = fh
        payload["nativeReflectivity"] = "REFL_10CM"
        payload["nativeGrid"] = True

        target = frames_dir / f"f{fh:03d}.json.gz"
        with target.open("wb") as raw:
            with gzip.GzipFile(filename="", mode="wb", fileobj=raw, compresslevel=9, mtime=0) as z:
                z.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8"))

        frames.append({
            "forecastHour": fh,
            "time": payload["time"],
            "file": f"frames/f{fh:03d}.json.gz",
            "grid": payload["grid"],
            "ranges": payload["ranges"],
        })

    expected = list(range(args.expected_start, args.expected_end + 1))
    actual = [x["forecastHour"] for x in frames]
    missing = [x for x in expected if x not in actual]
    first_grid = frames[0]["grid"] if frames else None

    metadata = {
        "schemaVersion": "sideral-cbr-wrf-4km-v1",
        "model": "WRF CBR 4 KM",
        "modelKey": "cbr_wrf_4km",
        "sourceModel": "ICON",
        "resolutionKm": 4,
        "forecastHours": [args.expected_start, args.expected_end],
        "temporalResolutionMinutes": 60,
        "initTime": init.isoformat().replace("+00:00", "Z"),
        "grid": first_grid or expected_grid,
        "namelistGrid": {
            "e_we": e_we,
            "e_sn": e_sn,
            "description": "dimensions include staggered points; scalar fields use e_we-1 by e_sn-1",
        },
        "domain": {"west": -50.0, "east": -30.0, "south": -21.0, "north": 1.0},
        "projection": "Lambert Conformal",
        "nativeReflectivity": "REFL_10CM",
        "reflectivityComposite": "maximum through the native WRF column",
        "wind": {"u": "U10", "v": "V10", "barbs": "frontend renders from U10/V10"},
        "fields": [
            "reflectivityDbz", "reflectivityCompositeDbz",
            "u10Ms", "v10Ms", "wind10Ms", "wind10DirDeg",
            "t2C", "q2KgKg", "td2C", "rh2Pct",
            "psfcHpa", "slpHpa",
            "raincMm", "rainncMm", "rainshMm", "rainTotalMm",
            "snowncMm", "graupelncMm", "hailncMm", "cloudFraction"
        ],
        "frames": frames,
        "frameCount": len(frames),
        "missingForecastHours": missing,
        "status": "complete" if not missing else "partial",
    }

    (out / "metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )

    print(
        f"[CBR] JSON publicado em {frames_dir}: {len(frames)} frames; "
        f"faltantes={missing}; grade de massa={metadata['grid']['nx']}x{metadata['grid']['ny']}"
    )
    if missing and args.require_complete:
        raise SystemExit(
            f"[CBR] extração incompleta: ausentes F{missing[0]:03d}..; "
            f"{len(missing)} horários faltando no intervalo F{args.expected_start}-F{args.expected_end}"
        )


if __name__ == "__main__":
    main()
