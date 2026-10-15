#!/usr/bin/env python3
"""Create downloadable slow/ultra-fast GIF and MP4 animations from published METBR maps."""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image


SIZE = 720
GIF_COLORS = 128
SPEEDS = {
    "slow": {"gif_ms": 500, "fps": "2"},
    "ultra-fast": {"gif_ms": 80, "fps": "12"},
}


def open_opaque_thumbnail(path: Path) -> Image.Image:
    with Image.open(path) as source:
        rgba = source.convert("RGBA")
        rgba.thumbnail((SIZE, SIZE), Image.Resampling.LANCZOS)
        canvas = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        canvas.alpha_composite(rgba)
        if canvas.size != (SIZE, SIZE):
            pad = Image.new("RGBA", (SIZE, SIZE), (255, 255, 255, 255))
            pad.alpha_composite(canvas, ((SIZE - canvas.width) // 2, (SIZE - canvas.height) // 2))
            canvas = pad
        return canvas.convert("RGB")


def animation_source(frame: dict, variable: str) -> str | None:
    # WebP is much lighter than the 4K original; keep PNG as a safe fallback.
    return (frame.get("previewFiles") or {}).get(variable) or (frame.get("files") or {}).get(variable)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    root = args.output_dir.resolve()
    metadata_path = root / "metadata.json"
    if not metadata_path.is_file():
        raise SystemExit("metadata.json ausente para gerar animações METBR.")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    catalog = metadata.get("variableCatalog") or {}
    frames = sorted(metadata.get("frames") or [], key=lambda item: int(item.get("forecastHour", -1)))
    if not frames:
        raise SystemExit("Nenhum frame publicado para criar GIF/MP4.")

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise SystemExit("ffmpeg indisponível; os MP4 não podem ser gerados.")
    animations: dict[str, dict] = {}
    animation_root = root / "animations"
    animation_root.mkdir(parents=True, exist_ok=True)

    for variable in catalog:
        entries = []
        for frame in frames:
            relative = animation_source(frame, variable)
            if not relative:
                continue
            source = root / relative
            if source.is_file() and source.stat().st_size > 0:
                entries.append(source)
        if len(entries) < 2:
            print(f"[METBR ANIMATION] {variable}: apenas {len(entries)} frame(s), sem animação")
            continue

        animations[variable] = {}
        with tempfile.TemporaryDirectory(prefix=f"metbr-{variable}-") as temporary:
            temp = Path(temporary)
            gif_frames = []
            for index, source in enumerate(entries):
                frame_image = open_opaque_thumbnail(source)
                jpeg_path = temp / f"frame_{index:04d}.jpg"
                frame_image.save(jpeg_path, format="JPEG", quality=88, optimize=True)
                gif_frames.append(frame_image.quantize(colors=GIF_COLORS, method=Image.Quantize.MEDIANCUT))

            for speed, spec in SPEEDS.items():
                gif_rel = f"animations/{variable}-{speed}.gif"
                gif_path = root / gif_rel
                gif_frames[0].save(
                    gif_path, format="GIF", save_all=True, append_images=gif_frames[1:],
                    duration=spec["gif_ms"], loop=0, optimize=True, disposal=2,
                )
                if gif_path.stat().st_size < 5000:
                    raise RuntimeError(f"GIF inválido: {gif_path}")

                mp4_rel = f"animations/{variable}-{speed}.mp4"
                mp4_path = root / mp4_rel
                command = [
                    ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                    "-framerate", spec["fps"], "-i", str(temp / "frame_%04d.jpg"),
                    "-vf", f"scale={SIZE}:{SIZE}:flags=lanczos,format=yuv420p",
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                    "-movflags", "+faststart", "-an", str(mp4_path),
                ]
                subprocess.run(command, check=True)
                if mp4_path.stat().st_size < 5000:
                    raise RuntimeError(f"MP4 inválido: {mp4_path}")
                animations[variable].setdefault(speed, {})["gif"] = gif_rel
                animations[variable][speed]["mp4"] = mp4_rel

        print(f"[METBR ANIMATION] {variable}: {len(entries)} frames; GIF/MP4 lento e ultra-rápido")

    if not animations:
        raise RuntimeError("Nenhuma variável tem frames suficientes para animação.")
    metadata["animations"] = animations
    metadata["animationProfiles"] = {
        "slow": {"gifFrameMs": 500, "mp4Fps": 2},
        "ultra-fast": {"gifFrameMs": 80, "mp4Fps": 12},
        "animationResolutionPx": [SIZE, SIZE],
        "source": "frames PNG/WebP já publicados; sem recalcular campos meteorológicos",
    }
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


if __name__ == "__main__":
    main()
