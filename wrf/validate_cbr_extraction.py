#!/usr/bin/env python3
"""Validate compact WRF CBR JSON output before it is published."""
from __future__ import annotations

import argparse
import gzip
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path


def fail(message: str) -> None:
    raise SystemExit(f"[CBR][VALIDAÇÃO] {message}")


def load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"não foi possível ler JSON {path}: {exc}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--metadata", default="cbr_json/metadata.json")
    ap.add_argument("--catalog", default="wrf/cbr_variable_catalog.json")
    ap.add_argument("--expected-start", type=int, default=0)
    ap.add_argument("--expected-end", type=int, default=42)
    ap.add_argument(
        "--allow-partial",
        action="store_true",
        help="aceita metadados parciais, validando os horários ausentes em vez de exigir o ciclo inteiro",
    )
    args = ap.parse_args()

    if args.expected_start < 0 or args.expected_end < args.expected_start:
        fail(f"intervalo solicitado inválido: F{args.expected_start}-F{args.expected_end}")

    metadata_path = Path(args.metadata)
    metadata = load_json(metadata_path)
    catalog = load_json(Path(args.catalog))
    try:
        domain = catalog["domain"]
        e_we, e_sn = int(domain["e_we"]), int(domain["e_sn"])
    except (KeyError, TypeError, ValueError) as exc:
        fail(f"catálogo sem domain.e_we/e_sn válidos: {exc}")

    # The configured WRF dimensions include staggered points. Surface scalar
    # variables in wrfout (including XLAT/XLONG and reflectivity) use mass points.
    expected_grid = {"nx": e_we - 1, "ny": e_sn - 1}
    expected_hours = list(range(args.expected_start, args.expected_end + 1))
    expected_count = len(expected_hours)
    cells = expected_grid["nx"] * expected_grid["ny"]

    if metadata.get("model") != "WRF CBR 4 KM":
        fail(f"model inesperado: {metadata.get('model')!r}")
    if metadata.get("modelKey") != "cbr_wrf_4km":
        fail(f"modelKey inesperado: {metadata.get('modelKey')!r}")
    if metadata.get("resolutionKm") != 4:
        fail(f"resolução declarada inválida: {metadata.get('resolutionKm')!r}")
    if metadata.get("grid") != expected_grid:
        fail(
            f"grade mass-point inválida: metadata={metadata.get('grid')!r}; "
            f"esperado={expected_grid!r} para e_we={e_we}, e_sn={e_sn}"
        )
    if metadata.get("forecastHours") != [args.expected_start, args.expected_end]:
        fail(f"forecastHours inválido: {metadata.get('forecastHours')!r}")
    entries = metadata.get("frames")
    if not isinstance(entries, list):
        fail("metadata.frames não é uma lista")
    if any(not isinstance(entry, dict) for entry in entries):
        fail("metadata.frames contém itens que não são objetos")
    actual_hours = [entry.get("forecastHour") for entry in entries]
    if any(not isinstance(hour, int) for hour in actual_hours):
        fail(f"horários de frames inválidos: {actual_hours!r}")
    if actual_hours != sorted(set(actual_hours)):
        fail(f"horários duplicados ou fora de ordem: {actual_hours!r}")
    outside = [hour for hour in actual_hours if hour < args.expected_start or hour > args.expected_end]
    if outside:
        fail(f"frames fora do intervalo declarado: {outside!r}")

    actual_set = set(actual_hours)
    missing_expected = [hour for hour in expected_hours if hour not in actual_set]
    if metadata.get("missingForecastHours") != missing_expected:
        fail(
            f"lista de horários faltantes inconsistente: metadata={metadata.get('missingForecastHours')!r}, "
            f"calculado={missing_expected!r}"
        )
    if missing_expected and not args.allow_partial:
        fail(f"horários faltantes: {missing_expected!r}")
    expected_status = "partial" if missing_expected else "complete"
    if metadata.get("status") != expected_status:
        fail(f"status de metadata divergente: observado={metadata.get('status')!r}, esperado={expected_status!r}")
    if metadata.get("frameCount") != len(entries):
        fail(
            f"frameCount divergente: metadata={metadata.get('frameCount')!r}, "
            f"lista={len(entries)}"
        )
    if not entries:
        fail("não há frames para validar")

    try:
        init_text = str(metadata["initTime"])
        init = datetime.fromisoformat(init_text.replace("Z", "+00:00"))
        if init.tzinfo is None:
            init = init.replace(tzinfo=timezone.utc)
    except (KeyError, TypeError, ValueError) as exc:
        fail(f"initTime ausente ou inválido: {exc}")

    root = metadata_path.parent.resolve()
    for entry in entries:
        hour = int(entry["forecastHour"])
        rel = Path(str(entry.get("file", "")))
        if rel.is_absolute() or ".." in rel.parts:
            fail(f"caminho de frame inválido em F{hour:03d}: {rel}")
        frame_path = (metadata_path.parent / rel).resolve()
        if root not in frame_path.parents:
            fail(f"caminho de frame sai do diretório de saída: {rel}")
        if not frame_path.is_file() or frame_path.stat().st_size == 0:
            fail(f"arquivo do frame F{hour:03d} ausente/vazio: {frame_path}")

        try:
            with gzip.open(frame_path, "rt", encoding="utf-8") as fp:
                frame = json.load(fp)
        except (OSError, EOFError, gzip.BadGzipFile, json.JSONDecodeError) as exc:
            fail(f"frame F{hour:03d} gzip/JSON inválido ({frame_path.name}): {exc}")

        if frame.get("modelKey") != "cbr_wrf_4km" or frame.get("resolutionKm") != 4:
            fail(f"identificação/resolução inválida no frame F{hour:03d}")
        if frame.get("forecastHour") != hour:
            fail(f"forecastHour do conteúdo diverge do índice em F{hour:03d}")
        if frame.get("nativeReflectivity") != "REFL_10CM":
            fail(f"REFL_10CM nativa não declarada no frame F{hour:03d}")
        if frame.get("grid") != expected_grid or entry.get("grid") != expected_grid:
            fail(
                f"grade divergente no frame F{hour:03d}: conteúdo={frame.get('grid')!r}, "
                f"índice={entry.get('grid')!r}, esperado={expected_grid!r}"
            )

        try:
            valid = datetime.fromisoformat(str(frame["time"]).replace("Z", "+00:00"))
        except (KeyError, TypeError, ValueError) as exc:
            fail(f"valid time inválido no frame F{hour:03d}: {exc}")
        expected_valid = init + timedelta(hours=hour)
        if valid.tzinfo is None:
            valid = valid.replace(tzinfo=timezone.utc)
        if abs((valid - expected_valid).total_seconds()) > 1:
            fail(
                f"tempo do frame F{hour:03d} não corresponde ao ciclo: "
                f"observado={valid.isoformat()}, esperado={expected_valid.isoformat()}"
            )

        fields = frame.get("fields")
        if not isinstance(fields, dict):
            fail(f"fields ausente/inválido no frame F{hour:03d}")
        for key in ("lat", "lon", "reflectivityDbz", "reflectivityCompositeDbz"):
            if not isinstance(fields.get(key), list):
                fail(f"campo obrigatório {key} ausente no frame F{hour:03d}")
            if len(fields[key]) != cells:
                fail(
                    f"campo {key} em F{hour:03d} tem {len(fields[key])} valores; "
                    f"esperado {cells} ({expected_grid['nx']}x{expected_grid['ny']})"
                )
        for key, values in fields.items():
            if values is not None and isinstance(values, list) and len(values) != cells:
                fail(
                    f"campo {key} em F{hour:03d} tem {len(values)} valores; esperado {cells}"
                )

    print(
        f"[CBR][VALIDAÇÃO] OK: {len(entries)}/{expected_count} frames disponíveis, "
        f"F{actual_hours[0]:03d}-F{actual_hours[-1]:03d}, ausentes={len(missing_expected)}, "
        f"grade de massa {expected_grid['nx']}x{expected_grid['ny']}, "
        "REFL_10CM nativa e horários alinhados."
    )


if __name__ == "__main__":
    main()
