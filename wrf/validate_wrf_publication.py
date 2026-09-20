#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate a native Sideral WRF publication")
    parser.add_argument("--json", required=True)
    parser.add_argument("--metadata", required=True)
    parser.add_argument("--resolution-km", type=float, required=True)
    parser.add_argument("--nx", type=int, required=True)
    parser.add_argument("--ny", type=int, required=True)
    parser.add_argument("--frames", type=int, required=True)
    parser.add_argument("--require-reflectivity-source", required=True)
    parser.add_argument("--require-native-grid", action="store_true")
    parser.add_argument("--temporal-resolution-minutes", type=int, required=True)
    args = parser.parse_args()

    json_path = Path(args.json)
    meta_path = Path(args.metadata)
    if not json_path.is_file() or json_path.stat().st_size == 0:
        raise SystemExit(f"JSON de publicacao ausente/vazio: {json_path}")
    if not meta_path.is_file() or meta_path.stat().st_size == 0:
        raise SystemExit(f"metadata ausente/vazio: {meta_path}")

    payload = json.loads(json_path.read_text(encoding="utf-8"))
    metadata = json.loads(meta_path.read_text(encoding="utf-8"))

    def first(mapping, *keys):
        for key in keys:
            if key in mapping:
                return mapping[key]
        return None

    resolution = first(metadata, "resolutionKm", "resolution_km")
    if resolution is None:
        resolution = first(payload, "resolutionKm", "resolution_km")
    if resolution is None or abs(float(resolution) - args.resolution_km) > 1e-6:
        raise SystemExit(f"resolutionKm invalida: esperado {args.resolution_km}, recebido {resolution}")

    source = first(metadata, "reflectivitySource", "reflectivity_source")
    if source is None:
        source = first(payload, "reflectivitySource", "reflectivity_source")
    if source != args.require_reflectivity_source:
        raise SystemExit(f"reflectivitySource invalido: esperado {args.require_reflectivity_source}, recebido {source}")

    native = first(metadata, "nativeGrid", "native_grid")
    if native is None:
        native = first(payload, "nativeGrid", "native_grid")
    if args.require_native_grid and native is not True:
        raise SystemExit(f"nativeGrid invalido: esperado true, recebido {native}")

    frames = first(metadata, "frames")
    if frames is None:
        frames = first(payload, "frames")
    if not isinstance(frames, list):
        raise SystemExit("frames ausente ou invalido")
    if len(frames) != args.frames:
        raise SystemExit(f"Quantidade de frames invalida: esperado {args.frames}, recebido {len(frames)}")

    for index, frame in enumerate(frames):
        if not isinstance(frame, dict):
            raise SystemExit(f"Frame {index} nao e objeto JSON")
        gx = first(frame, "gridX", "grid_x", "nx")
        gy = first(frame, "gridY", "grid_y", "ny")
        if int(gx) != args.nx or int(gy) != args.ny:
            raise SystemExit(f"Frame {index}: grade invalida; esperado {args.nx}x{args.ny}, recebido {gx}x{gy}")

        dt = first(frame, "temporalResolutionMinutes", "temporal_resolution_minutes", "intervalMinutes")
        if dt is not None and int(dt) != args.temporal_resolution_minutes:
            raise SystemExit(f"Frame {index}: intervalo temporal invalido: esperado {args.temporal_resolution_minutes}, recebido {dt}")

    print(
        f"VALIDACAO OK: resolutionKm={args.resolution_km:g}; "
        f"grid={args.nx}x{args.ny}; frames={len(frames)}; "
        f"reflectivitySource={args.require_reflectivity_source}; nativeGrid={bool(native)}; "
        f"interval={args.temporal_resolution_minutes}min"
    )


if __name__ == "__main__":
    main()
