#!/usr/bin/env python3
from __future__ import annotations

import pathlib
import re
import sys

MARKER_V1 = "SIDERAL_WRF_GITHUB_REMOTE_V1"
MARKER_V2 = "SIDERAL_MODEL_GITHUB_REMOTE_V2"

CONFIG_BLOCK = r'''
# SIDERAL_MODEL_GITHUB_REMOTE_V2
# Os produtos leves publicados pelo GitHub Actions sao lidos pelo Render.
# GFS usa WRF 4 km/REFL_10CM; ICON e ECMWF usam campos oficiais diretos.
MODEL_REMOTE_BASES = {
    "gfs": os.environ.get(
        "WRF_GITHUB_RAW_BASE",
        "https://raw.githubusercontent.com/progames12301-hash/sideral-backend/wrf-data",
    ).rstrip("/"),
    "icon": os.environ.get(
        "ICON_GITHUB_RAW_BASE",
        "https://raw.githubusercontent.com/progames12301-hash/sideral-backend/icon-data",
    ).rstrip("/"),
    "ecmwf": os.environ.get(
        "ECMWF_GITHUB_RAW_BASE",
        "https://raw.githubusercontent.com/progames12301-hash/sideral-backend/ecmwf-data",
    ).rstrip("/"),
}
MODEL_REMOTE_CACHE_SECONDS = int(os.environ.get("MODEL_REMOTE_CACHE_SECONDS", "120"))
model_remote_cache: dict[str, dict[str, Any]] = {
    key: {"metadata": None, "frames": {}} for key in MODEL_REMOTE_BASES
}
model_remote_lock = threading.Lock()
'''

HELPER_BLOCK = r'''
def _model_remote_key(model_key: str) -> str:
    key = (model_key or "gfs").lower().strip()
    if key not in MODEL_REMOTE_BASES:
        raise ValueError(f"Modelo remoto invalido: {model_key}")
    return key


def _wrf_remote_metadata(model_key: str = "gfs") -> dict[str, Any]:
    key = _model_remote_key(model_key)
    now = time.monotonic()
    cache = model_remote_cache[key]
    cached = cache.get("metadata")
    if isinstance(cached, dict) and now - float(cached.get("saved_at", 0.0)) < MODEL_REMOTE_CACHE_SECONDS:
        data = cached.get("data")
        if isinstance(data, dict):
            return data

    with model_remote_lock:
        cached = cache.get("metadata")
        if isinstance(cached, dict) and now - float(cached.get("saved_at", 0.0)) < MODEL_REMOTE_CACHE_SECONDS:
            data = cached.get("data")
            if isinstance(data, dict):
                return data

        url = f"{MODEL_REMOTE_BASES[key]}/metadata.json?ts={int(time.time() // 60)}"
        response = requests.get(
            url,
            headers={"User-Agent": "SideralMeteorologia/2.0", "Accept": "application/json"},
            timeout=20,
        )
        response.raise_for_status()
        data = response.json()
        allowed_schemas = {"sideral-wrf-metadata-v1", "sideral-model-metadata-v1"}
        if not isinstance(data, dict) or data.get("schema") not in allowed_schemas:
            raise RuntimeError(f"Metadata remoto de {key} em formato inesperado.")
        if str(data.get("model") or key).lower() != key:
            raise RuntimeError(f"Metadata remoto pertence a outro modelo: {data.get('model')}")
        frames = data.get("frames")
        if not isinstance(frames, list) or not frames:
            raise RuntimeError(f"Metadata remoto de {key} nao contem quadros.")
        cache["metadata"] = {"saved_at": now, "data": data}
        return data


def _wrf_remote_frame(model_key: str, filename: str) -> dict[str, Any]:
    key = _model_remote_key(model_key)
    safe_name = str(filename or "")
    if not re.fullmatch(rf"{re.escape(key)}/f\d{{3}}\.json\.gz", safe_name):
        raise ValueError(f"Nome de quadro remoto invalido para {key}: {safe_name}")

    now = time.monotonic()
    frames_cache = model_remote_cache[key].setdefault("frames", {})
    cached = frames_cache.get(safe_name)
    if isinstance(cached, dict) and now - float(cached.get("saved_at", 0.0)) < MODEL_REMOTE_CACHE_SECONDS:
        data = cached.get("data")
        if isinstance(data, dict):
            return data

    url = f"{MODEL_REMOTE_BASES[key]}/{safe_name}?ts={int(time.time() // 60)}"
    response = requests.get(
        url,
        headers={"User-Agent": "SideralMeteorologia/2.0", "Accept": "application/gzip,application/octet-stream"},
        timeout=35,
    )
    response.raise_for_status()
    try:
        decoded = zlib.decompress(response.content, 16 + zlib.MAX_WBITS)
        data = json.loads(decoded.decode("utf-8"))
    except Exception as exc:
        raise RuntimeError(f"Falha ao descompactar quadro remoto {key}: {exc}") from exc

    if not isinstance(data, dict) or data.get("schema") not in {"sideral-wrf-grid-v1", "sideral-model-grid-v1"}:
        raise RuntimeError(f"Quadro remoto {key} em formato inesperado.")
    if str(data.get("model") or key).lower() != key:
        raise RuntimeError(f"Quadro remoto pertence a outro modelo: {data.get('model')}")

    frames_cache[safe_name] = {"saved_at": now, "data": data}
    while len(frames_cache) > 40:
        frames_cache.pop(next(iter(frames_cache)))
    return data


def build_remote_wrf_cells(
    bounds: dict[str, float],
    hours: int,
    grid_x: int,
    grid_y: int,
    model_key: str = "gfs",
) -> dict[str, Any]:
    key = _model_remote_key(model_key)
    metadata = _wrf_remote_metadata(key)
    frames = metadata.get("frames") or []
    requested_hour = max(0, int(hours))
    frame_index = min(
        range(len(frames)),
        key=lambda index: abs(int(frames[index].get("forecastHour") or 0) - requested_hour),
    )
    frame_meta = frames[frame_index]
    frame = _wrf_remote_frame(key, str(frame_meta.get("file") or ""))

    source_bounds = frame.get("bounds") or {}
    margin = 0.20
    try:
        if (
            bounds["south"] < float(source_bounds["south"]) - margin
            or bounds["north"] > float(source_bounds["north"]) + margin
            or bounds["west"] < float(source_bounds["west"]) - margin
            or bounds["east"] > float(source_bounds["east"]) + margin
        ):
            raise WRFDomainError(
                f"Os dados {key.upper()} publicados cobrem apenas "
                f"{source_bounds.get('south')}..{source_bounds.get('north')} lat / "
                f"{source_bounds.get('west')}..{source_bounds.get('east')} lon."
            )
    except KeyError as exc:
        raise RuntimeError(f"Bounds ausentes no quadro remoto {key}.") from exc

    source_grid_x = int(frame.get("gridX") or 0)
    source_grid_y = int(frame.get("gridY") or 0)
    fields = frame.get("fields")
    if source_grid_x < 1 or source_grid_y < 1 or not isinstance(fields, dict):
        raise RuntimeError(f"Grade remota {key} invalida.")

    expected = source_grid_x * source_grid_y
    latitudes = fields.get("lat")
    longitudes = fields.get("lon")
    if (
        not isinstance(latitudes, list)
        or not isinstance(longitudes, list)
        or len(latitudes) != expected
        or len(longitudes) != expected
    ):
        raise RuntimeError(f"Coordenadas remotas {key} incompletas.")

    row_hits: list[int] = []
    col_hits: list[int] = []
    for row in range(source_grid_y):
        base = row * source_grid_x
        if any(
            bounds["south"] <= float(latitudes[base + col]) <= bounds["north"]
            and bounds["west"] <= float(longitudes[base + col]) <= bounds["east"]
            for col in range(source_grid_x)
        ):
            row_hits.append(row)
    for col in range(source_grid_x):
        if any(
            bounds["south"] <= float(latitudes[row * source_grid_x + col]) <= bounds["north"]
            and bounds["west"] <= float(longitudes[row * source_grid_x + col]) <= bounds["east"]
            for row in range(source_grid_y)
        ):
            col_hits.append(col)

    if not row_hits or not col_hits:
        raise WRFDomainError(f"A area solicitada nao cruza a grade remota {key}.")

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
            raise RuntimeError(f"Campo remoto {key} incompleto: {name}.")

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
            "localValidTime": item.get("localValidTime"),
            "forecastHour": item.get("forecastHour"),
        }
        for index, item in enumerate(frames)
    ]
    default_capabilities = {
        "reflectivity": key == "gfs",
        "precipitation": True,
        "wind": True,
        "temperature": True,
        "humidity": True,
        "mucape": True,
        "bulkShear": key == "gfs",
        "vorticity850": key == "gfs",
        "waterVapor": key in {"gfs", "ecmwf"},
    }
    capabilities = metadata.get("capabilities")
    if not isinstance(capabilities, dict):
        capabilities = default_capabilities

    return {
        "cells": cells,
        "gridX": out_grid_x,
        "gridY": out_grid_y,
        "source": str(frame.get("source") or metadata.get("provider") or key.upper()),
        "model": key,
        "nativeGrid": False,
        "remoteGrid": True,
        "resolutionKm": metadata.get("resolutionKm"),
        "runDate": metadata.get("runDate"),
        "runCycle": metadata.get("runCycle"),
        "initTime": metadata.get("initTime"),
        "forecastHour": frame.get("forecastHour"),
        "frameIndex": frame_index,
        "frameCount": len(frames),
        "validTime": frame.get("validTime"),
        "availableFrames": available_frames,
        "capabilities": capabilities,
        "forecastLocalDate": metadata.get("forecastLocalDate"),
        "timezone": metadata.get("timezone"),
        "scope": metadata.get("scope"),
        "temporalResolutionMinutes": metadata.get("temporalResolutionMinutes"),
        "note": metadata.get("note"),
    }
'''

STATUS_BLOCK = r'''        if parsed_path == "/api/wrf/status":
            try:
                status_query = parse_qs(urlparse(self.path).query)
                model_key = str(status_query.get("model", ["gfs"])[0]).lower().strip()
                if model_key not in MODEL_REMOTE_BASES:
                    self.send_json(400, {"status": "error", "error": "Modelo invalido. Use gfs, icon ou ecmwf."})
                    return
                metadata = _wrf_remote_metadata(model_key)
                capabilities = metadata.get("capabilities")
                if not isinstance(capabilities, dict):
                    capabilities = {
                        "reflectivity": model_key == "gfs",
                        "precipitation": True,
                        "wind": True,
                        "temperature": True,
                        "humidity": True,
                        "mucape": True,
                        "bulkShear": model_key == "gfs",
                        "vorticity850": model_key == "gfs",
                        "waterVapor": model_key in {"gfs", "ecmwf"},
                    }
                self.send_json(200, {
                    "status": "ok",
                    "source": "github-model-data",
                    "model": model_key,
                    "resolutionKm": metadata.get("resolutionKm"),
                    "runDate": metadata.get("runDate"),
                    "runCycle": metadata.get("runCycle"),
                    "initTime": metadata.get("initTime"),
                    "generatedAt": metadata.get("generatedAt"),
                    "forecastLocalDate": metadata.get("forecastLocalDate"),
                    "timezone": metadata.get("timezone"),
                    "scope": metadata.get("scope"),
                    "temporalResolutionMinutes": metadata.get("temporalResolutionMinutes"),
                    "frameCount": metadata.get("frameCount"),
                    "frames": metadata.get("frames"),
                    "capabilities": capabilities,
                    "note": metadata.get("note"),
                })
            except Exception as exc:
                self.send_json(502, {"status": "error", "error": f"{type(exc).__name__}: {exc}"})
            return
'''

ROUTE_BLOCK = r'''            elif path == "/api/wrf/cells":
                # Todos os modelos publicados pelo GitHub usam a mesma interface.
                # GFS = WRF 4 km com REFL_10CM. ICON/ECMWF = campos oficiais diretos.
                wrf_payload = build_remote_wrf_cells(bounds, hours, grid_x, grid_y, wrf_model)
                cells = wrf_payload["cells"]
                response_extra = {key: value for key, value in wrf_payload.items() if key != "cells"}
            elif path == "/api/meteoblue/cells":'''


def patch(path: pathlib.Path) -> None:
    text = path.read_text(encoding="utf-8")

    if MARKER_V2 in text:
        print("server.py ja usa SIDERAL_MODEL_GITHUB_REMOTE_V2")
        return
    if MARKER_V1 not in text:
        raise RuntimeError("Integracao remota V1 nao encontrada no server.py")

    config_pattern = re.compile(
        r"# SIDERAL_WRF_GITHUB_REMOTE_V1.*?wrf_remote_lock = threading\.Lock\(\)\n",
        re.S,
    )
    text, count = config_pattern.subn(CONFIG_BLOCK.strip("\n") + "\n", text, count=1)
    if count != 1:
        raise RuntimeError(f"Falha ao substituir configuracao V1: {count}")

    helper_pattern = re.compile(
        r"def _wrf_remote_metadata\(\).*?(?=def build_wrf_cells\()",
        re.S,
    )
    text, count = helper_pattern.subn(HELPER_BLOCK.strip("\n") + "\n\n", text, count=1)
    if count != 1:
        raise RuntimeError(f"Falha ao substituir helpers remotos: {count}")

    status_pattern = re.compile(
        r'        if parsed_path == "/api/wrf/status":\n.*?'
        r'(?=        if parsed_path == "/api/inmet/estacoes":)',
        re.S,
    )
    text, count = status_pattern.subn(STATUS_BLOCK, text, count=1)
    if count != 1:
        raise RuntimeError(f"Falha ao atualizar /api/wrf/status: {count}")

    route_pattern = re.compile(
        r'            elif path == "/api/wrf/cells":\n.*?'
        r'            elif path == "/api/meteoblue/cells":',
        re.S,
    )
    text, count = route_pattern.subn(ROUTE_BLOCK, text, count=1)
    if count != 1:
        raise RuntimeError(f"Falha ao atualizar /api/wrf/cells: {count}")

    text = text.replace(
        'wrf_model = str(payload.get("wrfModel", "icon")).lower()',
        'wrf_model = str(payload.get("wrfModel", "gfs")).lower()',
        1,
    )

    text = "\n".join(line.rstrip() for line in text.splitlines()) + "\n"
    path.write_text(text, encoding="utf-8")
    print("server.py atualizado para GFS + ICON + ECMWF remotos.")


def main() -> None:
    target = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "server.py")
    patch(target)


if __name__ == "__main__":
    main()
