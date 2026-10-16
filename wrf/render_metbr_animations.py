#!/usr/bin/env python3
"""Create downloadable slow/ultra-fast GIF and MP4 animations from published METBR maps."""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from datetime import datetime

from PIL import Image, ImageChops, ImageDraw, ImageFont


GIF_SIZE = 720
VIDEO_WIDTH = 3840
VIDEO_HEIGHT = 2160
VIDEO_MAP_SIZE = 2160
GIF_COLORS = 128
SPEEDS = {
    "slow": {"gif_ms": 500, "fps": "2"},
    "ultra-fast": {"gif_ms": 80, "fps": "12"},
}


def font(size: int, bold: bool = False):
    candidates = (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold
        else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    )
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size=size)
        except OSError:
            pass
    return ImageFont.load_default()


def text_wrap(draw: ImageDraw.ImageDraw, text: str, xy: tuple[int, int],
              use_font, fill, max_width: int, line_spacing: int = 8,
              max_lines: int = 4) -> int:
    words = str(text or "").split()
    x, y = xy
    lines = []
    current = ""
    for word in words:
        trial = f"{current} {word}".strip()
        if current and draw.textbbox((0, 0), trial, font=use_font)[2] > max_width:
            lines.append(current)
            current = word
        else:
            current = trial
    if current:
        lines.append(current)
    lines = lines[:max_lines]
    for line in lines:
        draw.text((x, y), line, font=use_font, fill=fill)
        y += draw.textbbox((0, 0), line, font=use_font)[3] + line_spacing
    return y


def crop_to_native_map(source: Image.Image) -> Image.Image:
    """Trim the page margins to the map panel; retain the alpha cutout outside WRF coverage."""
    rgba = source.convert("RGBA")
    width, height = rgba.size
    # Transparent corners are made by the renderer outside the native WRF polygon.
    transparent = ImageChops.invert(rgba.getchannel("A")).getbbox()
    if transparent and transparent[2] - transparent[0] > width * 0.45 and transparent[3] - transparent[1] > height * 0.45:
        bbox = transparent
    else:
        # Fallback for older maps with no detectable alpha mask.
        bbox = (round(width * 0.08), round(height * 0.10),
                round(width * 0.92), round(height * 0.85))
    left, top, right, bottom = bbox
    left = max(0, min(left, width - 2))
    top = max(0, min(top, height - 2))
    right = max(left + 1, min(right, width))
    bottom = max(top + 1, min(bottom, height))
    return rgba.crop((left, top, right, bottom))


def open_square_frame(path: Path, size: int) -> Image.Image:
    """Return a square map-only tile; off-domain pixels blend into the video background."""
    with Image.open(path) as source:
        crop = crop_to_native_map(source)
        crop.thumbnail((size, size), Image.Resampling.LANCZOS)
        # Keep the whole native-domain outline, but never show the uncovered
        # polygon corners as pale/gray data. The same background is used by the
        # UHD canvas, so the no-coverage pixels visually disappear into it.
        square = Image.new("RGBA", (size, size), (22, 29, 39, 255))
        square.alpha_composite(crop, ((size - crop.width) // 2, (size - crop.height) // 2))
        return square.convert("RGB")


def formatted_time(value: str) -> str:
    if not value:
        return "—"
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.strftime("%d/%m/%Y %H:%M UTC")
    except (ValueError, TypeError):
        return str(value)


def open_uhd_frame(path: Path, variable: str, frame: dict, metadata: dict,
                   logo_path: Path | None) -> Image.Image:
    """Compose a 3840x2160 UHD frame with a square native-map crop and two information panels."""
    canvas = Image.new("RGB", (VIDEO_WIDTH, VIDEO_HEIGHT), (22, 29, 39))
    draw = ImageDraw.Draw(canvas)
    panel_width = (VIDEO_WIDTH - VIDEO_MAP_SIZE) // 2
    left_x = panel_width
    right_x = panel_width + VIDEO_MAP_SIZE
    accent = (227, 42, 63)
    white = (248, 250, 253)
    muted = (174, 187, 202)
    pad = 58
    left_max = panel_width - pad * 2
    right_pad = right_x + pad

    # The square content fills the entire height of the 4K UHD frame. The
    # native coverage's alpha mask is retained; off-domain corners use a soft
    # neutral background instead of looking like empty white data cells.
    map_square = open_square_frame(path, VIDEO_MAP_SIZE)
    canvas.paste(map_square, (left_x, 0))
    draw.rectangle((0, 0, 12, VIDEO_HEIGHT), fill=accent)
    draw.rectangle((left_x - 5, 0, left_x - 1, VIDEO_HEIGHT), fill=accent)
    draw.rectangle((right_x, 0, right_x + 4, VIDEO_HEIGHT), fill=accent)
    draw.rectangle((VIDEO_WIDTH - 12, 0, VIDEO_WIDTH, VIDEO_HEIGHT), fill=accent)

    draw.text((pad, 260), "SIDERAL METEOROLOGIA", font=font(27, True), fill=muted)
    draw.text((pad, 315), "METBR WRF 4 KM", font=font(38, True), fill=white)
    draw.line((pad, 390, panel_width - pad, 390), fill=accent, width=5)

    if logo_path and logo_path.is_file():
        try:
            with Image.open(logo_path) as logo_source:
                logo = logo_source.convert("RGBA")
                logo.thumbnail((left_max, 120), Image.Resampling.LANCZOS)
                draw.rounded_rectangle((pad, 70, panel_width - pad, 215), radius=8, fill=(255, 255, 255))
                logo_x = pad + (left_max - logo.width) // 2
                logo_y = 70 + (145 - logo.height) // 2
                canvas.paste(logo, (logo_x, logo_y), logo)
        except OSError:
            pass

    catalog = metadata.get("variableCatalog") or {}
    item = catalog.get(variable) or {}
    label = item.get("label") or variable
    unit = item.get("unit") or "—"
    y = text_wrap(draw, label, (pad, 445), font(47, True), white, left_max, 15, 5)
    draw.text((pad, y + 18), f"UNIDADE: {unit}", font=font(29, True), fill=(255, 213, 218))
    draw.text((pad, y + 84), "GRADE NATIVA WRF", font=font(24, True), fill=muted)
    draw.text((pad, y + 128), "RESOLUÇÃO HORIZONTAL: 4 KM", font=font(20), fill=muted)
    draw.text((pad, VIDEO_HEIGHT - 100), "SIDERAL • PREVISÃO NUMÉRICA", font=font(17, True), fill=muted)

    forecast_hour = int(frame.get("forecastHour", 0))
    valid_time = formatted_time(str(frame.get("validTime") or ""))
    init_time = formatted_time(str(metadata.get("initTime") or ""))
    draw.text((right_pad, 260), "HORÁRIO DE PREVISÃO", font=font(25, True), fill=muted)
    draw.text((right_pad, 315), f"F{forecast_hour:03d}", font=font(82, True), fill=white)
    draw.line((right_pad, 430, VIDEO_WIDTH - pad, 430), fill=accent, width=5)
    draw.text((right_pad, 475), "VALIDADE", font=font(25, True), fill=muted)
    y = text_wrap(draw, valid_time, (right_pad, 525), font(30, True), white, panel_width - pad * 2, 12, 3)
    draw.text((right_pad, y + 36), "RODADA", font=font(25, True), fill=muted)
    text_wrap(draw, init_time, (right_pad, y + 86), font(28, True), white, panel_width - pad * 2, 12, 3)
    draw.line((right_pad, VIDEO_HEIGHT - 220, VIDEO_WIDTH - pad, VIDEO_HEIGHT - 220), fill=(66, 79, 96), width=2)
    draw.text((right_pad, VIDEO_HEIGHT - 180), "COBERTURA NATIVA", font=font(22, True), fill=muted)
    draw.text((right_pad, VIDEO_HEIGHT - 137), "CANTOS SEM GRADE RECORTADOS", font=font(15, True), fill=white)

    if canvas.size != (3840, 2160):
        raise RuntimeError(f"Quadro MP4 fora de 4K UHD: {canvas.size}")
    return canvas


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

    try:
        import imageio_ffmpeg
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise SystemExit("FFmpeg indisponível (imageio-ffmpeg/binário de sistema); os MP4 não podem ser gerados.")
    animations: dict[str, dict] = {}
    animation_root = root / "animations"
    animation_root.mkdir(parents=True, exist_ok=True)

    for variable in catalog:
        entries = []
        for frame in frames:
            full_relative = (frame.get("files") or {}).get(variable)
            preview_relative = (frame.get("previewFiles") or {}).get(variable)
            # MP4 must use the original 3840x3840 PNG, not a 1200px WebP preview.
            # GIF can safely use the lighter WebP because it is published at 720x720.
            full_source = root / full_relative if full_relative else None
            preview_source = root / (preview_relative or full_relative) if (preview_relative or full_relative) else None
            if (
                full_source is not None
                and preview_source is not None
                and full_source.is_file()
                and preview_source.is_file()
                and full_source.stat().st_size > 0
                and preview_source.stat().st_size > 0
            ):
                entries.append((preview_source, full_source, frame))
        if len(entries) < 2:
            print(f"[METBR ANIMATION] {variable}: apenas {len(entries)} frame(s), sem animação")
            continue

        animations[variable] = {}
        with tempfile.TemporaryDirectory(prefix=f"metbr-{variable}-") as temporary:
            temp = Path(temporary)
            gif_frames = []
            for index, (gif_source, mp4_source, frame) in enumerate(entries):
                gif_image = open_square_frame(gif_source, GIF_SIZE)
                gif_frames.append(gif_image.quantize(colors=GIF_COLORS, method=Image.Quantize.MEDIANCUT))
                # H.264 uses the original full-resolution PNG; no WebP-preview upscaling.
                logo_path = Path(__file__).resolve().parents[1] / "assets" / "sideral-logo.png"
                video_frame = open_uhd_frame(mp4_source, variable, frame, metadata, logo_path)
                jpeg_path = temp / f"frame_{index:04d}.jpg"
                video_frame.save(jpeg_path, format="JPEG", quality=95, subsampling=0, optimize=False)

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
                    "-vf", "scale=3840:2160:flags=lanczos,setsar=1,format=yuv420p",
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
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
        "compositionVersion": 2,
        "bulletinCropMode": "tile quadrado com margens do boletim removidas e cantos sem cobertura integrados ao fundo do vídeo",
        "animationResolutionPx": [VIDEO_WIDTH, VIDEO_HEIGHT],
        "mp4ResolutionPx": [3840, 2160],
        "gifResolutionPx": [GIF_SIZE, GIF_SIZE],
        "bulletinResolutionPx": [3840, 3840],
        "videoContentResolutionPx": [VIDEO_MAP_SIZE, VIDEO_MAP_SIZE],
        "videoAspectRatio": "16:9 UHD 3840x2160; mapa quadrado recortado à cobertura nativa, com painéis laterais informativos",
        "mp4Encoding": "H.264 / yuv420p / 3840x2160 UHD; escala explícita no encoder",
        "squareMapCrop": "2160x2160, margens externas removidas; cantos fora da grade não aparecem como área cinza",
        "source": "PNG/WebP 4K já publicados; MP4 UHD H.264; sem recalcular campos meteorológicos",
    }
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


if __name__ == "__main__":
    main()
