#!/usr/bin/env python3
from __future__ import annotations

import argparse
import gzip
import json
import math
from pathlib import Path

REFLECTIVITY_SOURCE = "PRECIP_ZR_MARSHALL_PALMER_DERIVED"
REFLECTIVITY_METHOD = "Z=200*R^1.6; dBZ=10*log10(Z); R em mm/h"
MIN_RATE_MM_H = 0.05
MAX_DBZ = 75.0


def rate_to_dbz(rate: float) -> float:
    """Converte taxa de precipitacao em refletividade simulada.

    Isto NAO e refletividade nativa do modelo. Usa a relacao Z-R de
    Marshall-Palmer apenas para oferecer uma visualizacao radar-like para
    modelos globais que nao publicam DBZ composto nativo no produto aberto.
    """
    try:
        r = float(rate)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(r) or r < MIN_RATE_MM_H:
        return 0.0
    z = 200.0 * (r ** 1.6)
    if z <= 0.0:
        return 0.0
    dbz = 10.0 * math.log10(z)
    return round(max(0.0, min(MAX_DBZ, dbz)), 1)


def read_gz(path: Path) -> dict:
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return json.load(fh)


def write_gz(path: Path, payload: dict) -> None:
    raw = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    with path.open("wb") as fh:
        with gzip.GzipFile(
            filename="",
            mode="wb",
            fileobj=fh,
            compresslevel=9,
            mtime=0,
        ) as gz:
            gz.write(raw)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    output = Path(args.output)
    metadata_path = output / "metadata.json"
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))

    model = str(metadata.get("model") or "").lower()
    if model not in {"icon", "ecmwf"}:
        raise SystemExit(f"Modelo nao suportado: {model!r}")

    frames = metadata.get("frames") or []
    if not frames:
        raise SystemExit("Metadata sem frames.")

    converted = 0
    maxima: list[float] = []

    for frame in frames:
        frame_path = output / str(frame["file"])
        payload = read_gz(frame_path)
        fields = payload.setdefault("fields", {})
        precip = fields.get("precipitation")
        if not isinstance(precip, list) or not precip:
            raise SystemExit(f"Frame sem precipitation: {frame_path}")

        reflectivity = [rate_to_dbz(value) for value in precip]
        if len(reflectivity) != len(precip):
            raise SystemExit(f"Tamanho invalido de refletividade: {frame_path}")

        fields["reflectivity"] = reflectivity
        caps = payload.setdefault("capabilities", {})
        caps["reflectivity"] = True
        payload["reflectivitySource"] = REFLECTIVITY_SOURCE
        payload["reflectivityMethod"] = REFLECTIVITY_METHOD
        payload["reflectivityNative"] = False
        payload["source"] = (
            f"{payload.get('source', model.upper())}; refletividade simulada derivada "
            "da taxa de precipitacao por Marshall-Palmer"
        )
        payload["reflectivityStats"] = {
            "min": min(reflectivity) if reflectivity else 0.0,
            "max": max(reflectivity) if reflectivity else 0.0,
            "nonZero": sum(1 for value in reflectivity if value > 0.0),
        }
        write_gz(frame_path, payload)
        frame["reflectivitySource"] = REFLECTIVITY_SOURCE
        maxima.append(payload["reflectivityStats"]["max"])
        converted += 1

    metadata.setdefault("capabilities", {})["reflectivity"] = True
    metadata["reflectivitySource"] = REFLECTIVITY_SOURCE
    metadata["reflectivityMethod"] = REFLECTIVITY_METHOD
    metadata["reflectivityNative"] = False
    metadata["note"] = (
        "Refletividade simulada derivada da taxa de precipitacao pela relacao "
        "Marshall-Palmer. Nao e DBZ composto nativo do modelo e nao deve ser "
        "apresentada como observacao de radar."
    )
    metadata["reflectivityStats"] = {
        "frameMaxDbz": max(maxima) if maxima else 0.0,
        "convertedFrames": converted,
    }
    metadata_path.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(
        f"{model.upper()}: {converted} frames com refletividade derivada; "
        f"max={metadata['reflectivityStats']['frameMaxDbz']:.1f} dBZ"
    )


if __name__ == "__main__":
    main()
