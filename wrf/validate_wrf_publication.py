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
        if not isinstance(mapping, dict):
            return None
        for key in keys:
            if key in mapping:
                return mapping[key]
        return None

    def get_publication(key, *aliases):
        value = first(metadata, key, *aliases)
        return value if value is not None else first(payload, key, *aliases)

    resolution = get_publication("resolutionKm", "resolution_km")
    if resolution is None or abs(float(resolution) - args.resolution_km) > 1e-6:
        raise SystemExit(f"resolutionKm invalida: esperado {args.resolution_km}, recebido {resolution}")

    source = get_publication("reflectivitySource", "reflectivity_source")
    if source != args.require_reflectivity_source:
        raise SystemExit(f"reflectivitySource invalido: esperado {args.require_reflectivity_source}, recebido {source}")

    native = get_publication("nativeGrid", "native_grid")
    if args.require_native_grid and native is not True:
        raise SystemExit(f"nativeGrid invalido: esperado true, recebido {native}")

    interval = get_publication("temporalResolutionMinutes", "temporal_resolution_minutes")
    if interval is None or int(interval) != args.temporal_resolution_minutes:
        raise SystemExit(
            f"temporalResolutionMinutes invalido: esperado {args.temporal_resolution_minutes}, recebido {interval}"
        )

    frames = first(metadata, "frames")
    if frames is None:
        frames = first(payload, "frames")
    frame_count = first(metadata, "frameCount", "frame_count")
    if frame_count is None:
        frame_count = first(payload, "frameCount", "frame_count")
    if isinstance(frames, list):
        actual_frames = len(frames)
    elif frame_count is not None:
        actual_frames = int(frame_count)
    else:
        raise SystemExit("Nenhuma contagem de frames encontrada")

    if actual_frames != args.frames:
        raise SystemExit(f"Quantidade de frames invalida: esperado {args.frames}, recebido {actual_frames}")

    # Quando os frames estao materializados, conferir que a grade publicada
    # continua sendo exatamente a grade nativa solicitada.
    if isinstance(frames, list):
        for index, frame in enumerate(frames):
            if not isinstance(frame, dict):
                continue
            gx = first(frame, "gridX", "grid_x", "nx")
            gy = first(frame, "gridY", "grid_y", "ny")
            if gx is not None and gy is not None and (int(gx) != args.nx or int(gy) != args.ny):
                raise SystemExit(
                    f"Frame {index}: grade invalida; esperado {args.nx}x{args.ny}, recebido {gx}x{gy}"
                )

    print(
        f"VALIDACAO OK: resolutionKm={args.resolution_km:g}; grid={args.nx}x{args.ny}; "
        f"frames={actual_frames}; reflectivitySource={args.require_reflectivity_source}; "
        f"nativeGrid={bool(native)}; interval={args.temporal_resolution_minutes}min"
    )


if __name__ == "__main__":
    main()
