#!/usr/bin/env python3
from __future__ import annotations

import pathlib
import sys

MARKER = "SIDERAL_WRF_GITHUB_REMOTE_V1"

CONFIG_BLOCK = r'''

# SIDERAL_WRF_GITHUB_REMOTE_V1
# O WRF pesado roda no GitHub Actions. O Render baixa apenas JSON gzip compacto
# publicado na branch wrf-data.
WRF_GITHUB_RAW_BASE = os.environ.get(
    "WRF_GITHUB_RAW_BASE",
    "https://raw.githubusercontent.com/progames12301-hash/sideral-backend/wrf-data",
).rstrip("/")
WRF_REMOTE_CACHE_SECONDS = int(os.environ.get("WRF_REMOTE_CACHE_SECONDS", "300"))
wrf_remote_cache: dict[str, Any] = {"metadata": None, "frames": {}}
wrf_remote_lock = threading.Lock()
'''

HELPER_BLOCK = r'''

def _wrf_remote_metadata() -> dict[str, Any]:
    now = time.monotonic()
    cached = wrf_remote_cache.get("metadata")
    if isinstance(cached, dict) and now - float(cached.get("saved_at", 0.0)) < WRF_REMOTE_CACHE_SECONDS:
        data = cached.get("data")
        if isinstance(data, dict):
            return data

    with wrf_remote_lock:
        cached = wrf_remote_cache.get("metadata")
        if isinstance(cached, dict) and now - float(cached.get("saved_at", 0.0)) < WRF_REMOTE_CACHE_SECONDS:
            data = cached.get("data")
            if isinstance(data, dict):
                return data

        url = f"{WRF_GITHUB_RAW_BASE}/metadata.json?ts={int(time.time() // 60)}"
        response = requests.get(
            url,
            headers={"User-Agent": "SideralMeteorologia/1.0", "Accept": "application/json"},
            timeout=20,
        )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict) or data.get("schema") != "sideral-wrf-metadata-v1":
            raise RuntimeError("Metadata WRF remoto em formato inesperado.")
        frames = data.get("frames")
        if not isinstance(frames, list) or not frames:
            raise RuntimeError("Metadata WRF remoto não contém quadros.")
        wrf_remote_cache["metadata"] = {"saved_at": now, "data": data}
        return data


def _wrf_remote_frame(filename: str) -> dict[str, Any]:
    safe_name = str(filename or "")
    if not re.fullmatch(r"gfs/f\d{3}\.json\.gz", safe_name):
        raise ValueError("Nome de quadro WRF remoto inválido.")

    now = time.monotonic()
    frames_cache = wrf_remote_cache.setdefault("frames", {})
    cached = frames_cache.get(safe_name)
    if isinstance(cached, dict) and now - float(cached.get("saved_at", 0.0)) < WRF_REMOTE_CACHE_SECONDS:
        data = cached.get("data")
        if isinstance(data, dict):
            return data

    url = f"{WRF_GITHUB_RAW_BASE}/{safe_name}?ts={int(time.time() // 60)}"
    response = requests.get(
        url,
        headers={"User-Agent": "SideralMeteorologia/1.0", "Accept": "application/gzip,application/octet-stream"},
        timeout=35,
    )
    response.raise_for_status()
    try:
        decoded = zlib.decompress(response.content, 16 + zlib.MAX_WBITS)
        data = json.loads(decoded.decode("utf-8"))
    except Exception as exc:
        raise RuntimeError(f"Falha ao descompactar quadro WRF remoto: {exc}") from exc
    if not isinstance(data, dict) or data.get("schema") != "sideral-wrf-grid-v1":
        raise RuntimeError("Quadro WRF remoto em formato inesperado.")

    frames_cache[safe_name] = {"saved_at": now, "data": data}
    while len(frames_cache) > 12:
        frames_cache.pop(next(iter(frames_cache)))
    return data


def build_remote_wrf_cells(
    bounds: dict[str, float],
    hours: int,
    grid_x: int,
    grid_y: int,
    model_key: str = "gfs",
) -> dict[str, Any]:
    if (model_key or "gfs").lower() != "gfs":
        raise FileNotFoundError(f"WRF remoto ainda não publicado para {model_key}.")

    metadata = _wrf_remote_metadata()
    frames = metadata.get("frames") or []
    frame_index = min(max(0, int(hours)), len(frames) - 1)
    frame_meta = frames[frame_index]
    frame = _wrf_remote_frame(str(frame_meta.get("file") or ""))

    source_bounds = frame.get("bounds") or {}
    margin = 0.15
    try:
        if (
            bounds["south"] < float(source_bounds["south"]) - margin
            or bounds["north"] > float(source_bounds["north"]) + margin
            or bounds["west"] < float(source_bounds["west"]) - margin
            or bounds["east"] > float(source_bounds["east"]) + margin
        ):
            raise WRFDomainError(
                "Os dados WRF 4 km publicados não cobrem toda a área solicitada: "
                f"{source_bounds.get('south')}..{source_bounds.get('north')} lat / "
                f"{source_bounds.get('west')}..{source_bounds.get('east')} lon."
            )
    except KeyError as exc:
        raise RuntimeError("Bounds ausentes no quadro WRF remoto.") from exc

    source_grid_x = int(frame.get("gridX") or 0)
    source_grid_y = int(frame.get("gridY") or 0)
    fields = frame.get("fields")
    if source_grid_x < 1 or source_grid_y < 1 or not isinstance(fields, dict):
        raise RuntimeError("Grade WRF remota inválida.")

    expected = source_grid_x * source_grid_y
    latitudes = fields.get("lat")
    longitudes = fields.get("lon")
    if not isinstance(latitudes, list) or not isinstance(longitudes, list) or len(latitudes) != expected or len(longitudes) != expected:
        raise RuntimeError("Coordenadas WRF remotas incompletas.")

    row_hits: list[int] = []
    col_hits: list[int] = []
    for row in range(source_grid_y):
        hit = False
        base = row * source_grid_x
        for col in range(source_grid_x):
            idx = base + col
            lat = float(latitudes[idx])
            lon = float(longitudes[idx])
            if bounds["south"] <= lat <= bounds["north"] and bounds["west"] <= lon <= bounds["east"]:
                hit = True
                break
        if hit:
            row_hits.append(row)

    for col in range(source_grid_x):
        hit = False
        for row in range(source_grid_y):
            idx = row * source_grid_x + col
            lat = float(latitudes[idx])
            lon = float(longitudes[idx])
            if bounds["south"] <= lat <= bounds["north"] and bounds["west"] <= lon <= bounds["east"]:
                hit = True
                break
        if hit:
            col_hits.append(col)

    if not row_hits or not col_hits:
        raise WRFDomainError("A área solicitada não cruza a grade WRF 4 km publicada.")

    row_start, row_end = min(row_hits), max(row_hits)
    col_start, col_end = min(col_hits), max(col_hits)
    out_grid_y = row_end - row_start + 1
    out_grid_x = col_end - col_start + 1

    field_names = (
        "reflectivity", "precipitation", "windSpeed", "windDirection",
        "bulkShear", "vorticity850", "temperature", "humidity", "mucape", "waterVapor",
    )
    for name in field_names:
        values = fields.get(name)
        if not isinstance(values, list) or len(values) != expected:
            raise RuntimeError(f"Campo WRF remoto incompleto: {name}.")

    cells: list[dict[str, float]] = []
    for row in range(row_start, row_end + 1):
        base = row * source_grid_x
        for col in range(col_start, col_end + 1):
            idx = base + col
            cells.append({
                "lat": float(latitudes[idx]),
                "lon": float(longitudes[idx]),
                "reflectivity": max(0.0, float(fields["reflectivity"][idx])),
                "precipitation": max(0.0, float(fields["precipitation"][idx])),
                "cloudCover": 0.0,
                "windSpeed": float(fields["windSpeed"][idx]),
                "windDirection": float(fields["windDirection"][idx]),
                "bulkShear": float(fields["bulkShear"][idx]),
                "vorticity850": float(fields["vorticity850"][idx]),
                "temperature": float(fields["temperature"][idx]),
                "humidity": float(fields["humidity"][idx]),
                "mucape": float(fields["mucape"][idx]),
                "waterVapor": float(fields["waterVapor"][idx]),
            })

    available_frames = [
        {
            "index": index,
            "validTime": item.get("validTime"),
            "forecastHour": item.get("forecastHour"),
        }
        for index, item in enumerate(frames)
    ]
    return {
        "cells": cells,
        "gridX": out_grid_x,
        "gridY": out_grid_y,
        "source": str(frame.get("source") or "WRF 4 km GFS via GitHub Actions"),
        "model": "gfs",
        "nativeGrid": False,
        "remoteGrid": True,
        "resolutionKm": metadata.get("resolutionKm", 4),
        "runDate": metadata.get("runDate"),
        "runCycle": metadata.get("runCycle"),
        "initTime": metadata.get("initTime"),
        "forecastHour": frame.get("forecastHour"),
        "frameIndex": frame_index,
        "frameCount": len(frames),
        "validTime": frame.get("validTime"),
        "availableFrames": available_frames,
    }
'''


def patch_server(path: pathlib.Path) -> bool:
    text = path.read_text(encoding="utf-8")
    if MARKER in text:
        print("server.py já contém integração WRF GitHub; nenhuma alteração necessária.")
        return False

    config_anchor = 'DEFAULT_PORT = int(os.environ.get("PORT", "8766"))\n'
    if config_anchor not in text:
        raise RuntimeError("Âncora DEFAULT_PORT não encontrada no server.py")
    text = text.replace(config_anchor, config_anchor + CONFIG_BLOCK, 1)

    helper_anchor = 'def build_wrf_cells(bounds: dict[str, float], hours: int, grid_x: int, grid_y: int, model_key: str = "icon") -> list[dict[str, float]]:\n'
    if helper_anchor not in text:
        raise RuntimeError("Âncora build_wrf_cells não encontrada no server.py")
    text = text.replace(helper_anchor, HELPER_BLOCK + "\n" + helper_anchor, 1)

    old_route = '''            elif path == "/api/wrf/cells":\n                wrf_payload = build_wrf_cells(bounds, hours, grid_x, grid_y, wrf_model)\n                cells = wrf_payload["cells"]\n                response_extra = {key: value for key, value in wrf_payload.items() if key != "cells"}\n'''
    new_route = '''            elif path == "/api/wrf/cells":\n                if wrf_model == "gfs":\n                    try:\n                        wrf_payload = build_remote_wrf_cells(bounds, hours, grid_x, grid_y, "gfs")\n                    except Exception as remote_exc:\n                        print(f"[WRF-REMOTE] falha; tentando wrfout local: {type(remote_exc).__name__}: {remote_exc}")\n                        wrf_payload = build_wrf_cells(bounds, hours, grid_x, grid_y, "gfs")\n                else:\n                    wrf_payload = build_wrf_cells(bounds, hours, grid_x, grid_y, wrf_model)\n                cells = wrf_payload["cells"]\n                response_extra = {key: value for key, value in wrf_payload.items() if key != "cells"}\n'''
    if old_route not in text:
        raise RuntimeError("Bloco /api/wrf/cells não encontrado no server.py")
    text = text.replace(old_route, new_route, 1)

    health_anchor = '        if parsed_path == "/api/health": self.send_json(200, {"status": "ok", "service": "sideral", "domain": "sul4km"}); return\n'
    status_block = '''        if parsed_path == "/api/wrf/status":\n            try:\n                metadata = _wrf_remote_metadata()\n                self.send_json(200, {\n                    "status": "ok",\n                    "source": "github-wrf-data",\n                    "resolutionKm": metadata.get("resolutionKm"),\n                    "runDate": metadata.get("runDate"),\n                    "runCycle": metadata.get("runCycle"),\n                    "initTime": metadata.get("initTime"),\n                    "generatedAt": metadata.get("generatedAt"),\n                    "frameCount": metadata.get("frameCount"),\n                    "frames": metadata.get("frames"),\n                })\n            except Exception as exc:\n                self.send_json(502, {"status": "error", "error": f"{type(exc).__name__}: {exc}"})\n            return\n'''
    if health_anchor not in text:
        raise RuntimeError("Âncora /api/health não encontrada no server.py")
    text = text.replace(health_anchor, health_anchor + status_block, 1)

    path.write_text(text, encoding="utf-8")
    print("server.py integrado ao WRF remoto do GitHub.")
    return True


def main() -> None:
    path = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "server.py")
    changed = patch_server(path)
    print("CHANGED=1" if changed else "CHANGED=0")


if __name__ == "__main__":
    main()
