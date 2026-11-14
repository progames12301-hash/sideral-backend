"""Secure bridge between the METBR HTML, Render, and the isolated GitHub batch workflow.

The existing /api/wrf/sounding/render endpoint is deliberately untouched.
Only the new batch request/status/image routes use this module.
"""
from __future__ import annotations

import json
import os
import re
import uuid
import time
import threading
from typing import Any

import requests

GITHUB_REPOSITORY = "slaksjsjjjjjj-dev/multimodelsss"
WORKFLOW_FILE = "METBR_CLICK_TO_POINT_STANDALONE.yml"
GITHUB_API = "https://api.github.com"
GITHUB_RELEASE_DOWNLOAD = "https://github.com/slaksjsjjjjjj-dev/multimodelsss/releases/download"
REQUEST_ID_RE = re.compile(r"^[a-f0-9]{32}$")
ALLOWED_CYCLES = {"latest", "00", "06", "12", "18", "21"}

# Each HTML sends its own model_key. Keep the legacy METBR route for older pages,
# but never silently route an unknown WRF model to METBR.
MODEL_RUNNERS: dict[str, dict[str, Any]] = {
    "metbr": {
        "workflow": WORKFLOW_FILE,
        "release_prefix": "metbr-skewt-click",
        "max_forecast_hour": 42,
        "uses_cycle": True,
    },
    "tarc_wrf_3km": {
        "workflow": "wrf-skewt-clickpoint-tarc-3km.yml",
        "release_prefix": "tarc-skewt-click",
        "max_forecast_hour": 48,
        "uses_cycle": False,
    },
    "sideral_wrf_4km_sudeste": {
        "workflow": "wrf-skewt-clickpoint-sideral-4km-sudeste.yml",
        "release_prefix": "sideral-4km-sudeste-skewt-click",
        "max_forecast_hour": 48,
        "uses_cycle": False,
    },
    "sideral_wrf_5km_sudeste": {
        "workflow": "wrf-skewt-clickpoint-sideral-5km-sudeste.yml",
        "release_prefix": "sideral-5km-sudeste-skewt-click",
        "max_forecast_hour": 48,
        "uses_cycle": False,
    },
    "cbr_wrf_4km": {
        "workflow": "wrf-skewt-clickpoint-cbr-4km.yml",
        "release_prefix": "cbr-skewt-click",
        "max_forecast_hour": 42,
        "uses_cycle": False,
    },
    # Global conventional NWP models. Keep these identifiers aligned with the
    # three workflow_dispatch files and the nwp-data manifest model_key.
    "gfs": {
        "workflow": "GLOBAL_GFS_SKEWT_CLICKPOINT.yml",
        "release_prefix": "gfs-skewt-click",
        "max_forecast_hour": 384,
        "uses_cycle": False,
        "uses_forecast_hour": True,
    },
    "icon_dwd": {
        "workflow": "GLOBAL_ICON_DWD_SKEWT_CLICKPOINT.yml",
        "release_prefix": "icon-dwd-skewt-click",
        "max_forecast_hour": 180,
        "uses_cycle": False,
        "uses_forecast_hour": True,
    },
    "ecmwf_ifs": {
        "workflow": "GLOBAL_ECMWF_IFS_SKEWT_CLICKPOINT.yml",
        "release_prefix": "ecmwf-ifs-skewt-click",
        "max_forecast_hour": 360,
        "uses_cycle": False,
        "uses_forecast_hour": True,
    },
}


def _model_config(model_key: str | None) -> tuple[str, dict[str, Any]]:
    key = str(model_key or "metbr").strip().lower()
    config = MODEL_RUNNERS.get(key)
    if config is None:
        raise ValueError(f"Modelo Click-to-Point não reconhecido: {key or '(vazio)'}.")
    return key, config


class BatchRequestError(RuntimeError):
    """A safe, user-displayable error raised by the isolated batch feature."""


class BatchRateLimitError(BatchRequestError):
    """A caller exceeded the lightweight public batch endpoint throttle."""


_dispatch_lock = threading.Lock()
_dispatch_ip_last: dict[str, float] = {}
_dispatch_times: list[float] = []


def _check_dispatch_rate(client_ip: str) -> None:
    now = time.monotonic()
    key = str(client_ip or "unknown")[:80]
    with _dispatch_lock:
        for address, stamp in list(_dispatch_ip_last.items()):
            if now - stamp > 180:
                _dispatch_ip_last.pop(address, None)
        _dispatch_times[:] = [stamp for stamp in _dispatch_times if now - stamp < 60]
        previous = _dispatch_ip_last.get(key)
        if previous is not None and now - previous < 45:
            raise BatchRateLimitError("Aguarde 45 segundos antes de solicitar outro lote neste navegador.")
        if len(_dispatch_times) >= 2:
            raise BatchRateLimitError("O limite temporário de dois lotes por minuto foi atingido. Tente novamente em um minuto.")
        _dispatch_ip_last[key] = now
        _dispatch_times.append(now)


def _request_id(value: str) -> str:
    request_id = str(value or "").strip().lower()
    if not REQUEST_ID_RE.fullmatch(request_id):
        raise ValueError("Identificador de lote inválido.")
    return request_id


def _release_asset_url(request_id: str, asset_name: str, model_key: str = "metbr") -> str:
    request_id = _request_id(request_id)
    _, config = _model_config(model_key)
    if asset_name not in {"status.json", "manifest.json"}:
        match = re.fullmatch(r"f(\d{3})\.(?:png|json)", asset_name)
        if not match or int(match.group(1)) > int(config["max_forecast_hour"]):
            raise ValueError("Arquivo de sondagem inválido para o modelo selecionado.")
    tag = f"{config['release_prefix']}-{request_id}"
    return f"{GITHUB_RELEASE_DOWNLOAD}/{tag}/{asset_name}"


def dispatch_request(payload: dict[str, Any], client_ip: str = "") -> dict[str, Any]:
    """Dispatch a point-specific batch to the workflow belonging to model_key."""
    try:
        latitude = float(payload.get("latitude"))
        longitude = float(payload.get("longitude"))
    except (TypeError, ValueError):
        raise ValueError("Informe latitude e longitude válidas.") from None
    if not (-90.0 <= latitude <= 90.0 and -180.0 <= longitude <= 180.0):
        raise ValueError("Latitude/longitude fora dos limites geográficos.")

    model_key, config = _model_config(payload.get("model_key"))
    forecast_hour = None
    if config.get("uses_forecast_hour"):
        try:
            raw_hour = payload.get("forecast_hour")
            if isinstance(raw_hour, bool):
                raise ValueError
            forecast_hour = int(raw_hour)
        except (TypeError, ValueError):
            raise ValueError("Informe forecast_hour como uma hora de previsão inteira.") from None
        if forecast_hour < 0 or forecast_hour > int(config["max_forecast_hour"]):
            raise ValueError(
                f"Validade fora do intervalo F000–F{int(config['max_forecast_hour']):03d} "
                f"para {model_key}."
            )

    _check_dispatch_rate(client_ip)

    token = os.environ.get("METBR_SKEWT_GITHUB_TOKEN", "").strip()
    if not token:
        raise BatchRequestError(
            "O disparo do lote ainda não está habilitado no Render. "
            "Configure METBR_SKEWT_GITHUB_TOKEN com permissão Actions: write "
            "no repositório slaksjsjjjjjj-dev/multimodelsss."
        )

    request_id = uuid.uuid4().hex
    workflow = str(config["workflow"])
    url = (
        f"{GITHUB_API}/repos/{GITHUB_REPOSITORY}/actions/workflows/"
        f"{workflow}/dispatches"
    )
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "Sideral-WRF-SkewT-ClickPoint/1.0",
    }
    inputs = {
        "latitude": f"{latitude:.6f}",
        "longitude": f"{longitude:.6f}",
        "request_id": request_id,
    }

    # Legacy METBR requires a cycle; global NWP Click-to-Point workflows
    # require the selected forecast hour. Existing WRF runners remain unchanged.
    if config["uses_cycle"]:
        cycle = str(payload.get("cycle") or "latest").strip().lower()
        if cycle not in ALLOWED_CYCLES:
            raise ValueError("Rodada inválida. Use latest, 00, 06, 12, 18 ou 21.")
        inputs["cycle"] = cycle
    if config.get("uses_forecast_hour"):
        inputs["forecast_hour"] = str(forecast_hour)

    body = {"ref": "main", "inputs": inputs}
    try:
        response = requests.post(url, headers=headers, json=body, timeout=(10, 25))
    except requests.RequestException as exc:
        raise BatchRequestError(
            "Não foi possível contactar o GitHub para iniciar o lote. "
            "O Click-to-Point original continua disponível."
        ) from exc

    if response.status_code != 204:
        detail = ""
        try:
            detail = str((response.json() or {}).get("message") or "")
        except (ValueError, TypeError):
            detail = ""
        if response.status_code in {401, 403, 404}:
            raise BatchRequestError(
                f"O GitHub recusou o workflow {workflow} (HTTP {response.status_code}). "
                "Verifique o token e a permissão Actions: write."
                + (f" Detalhe: {detail[:240]}" if detail else "")
            )
        raise BatchRequestError(
            f"O GitHub não aceitou o workflow {workflow} (HTTP {response.status_code})."
            + (f" {detail[:240]}" if detail else "")
        )

    result = {
        "status": "queued",
        "request_id": request_id,
        "model_key": model_key,
        "latitude": latitude,
        "longitude": longitude,
        "workflow": workflow,
        "release_prefix": config["release_prefix"],
        "message": f"Pedido encaminhado ao runner {workflow}.",
    }
    if config["uses_cycle"]:
        result["cycle"] = inputs["cycle"]
    if config.get("uses_forecast_hour"):
        result["forecast_hour"] = forecast_hour
    return result

def _get_json_asset(request_id: str, asset_name: str, model_key: str = "metbr") -> dict[str, Any] | None:
    url = _release_asset_url(request_id, asset_name, model_key)
    try:
        response = requests.get(
            url,
            headers={
                "User-Agent": "Sideral-METBR-SkewT/1.0",
                "Accept": "application/json",
            },
            timeout=(8, 20),
        )
    except requests.RequestException as exc:
        raise BatchRequestError("Não foi possível consultar o estado do lote no GitHub.") from exc
    if response.status_code == 404:
        return None
    if response.status_code != 200:
        raise BatchRequestError(
            f"Falha ao consultar a publicação do GitHub (HTTP {response.status_code})."
        )
    if len(response.content) > 2 * 1024 * 1024:
        raise BatchRequestError("O manifesto do lote excedeu o tamanho permitido.")
    try:
        value = response.json()
    except ValueError as exc:
        raise BatchRequestError("O GitHub retornou um manifesto inválido.") from exc
    if not isinstance(value, dict) or value.get("request_id") != request_id:
        raise BatchRequestError("O manifesto retornado não corresponde ao pedido solicitado.")
    expected_model = str(model_key or "metbr").strip().lower()
    returned_model = str(value.get("model_key") or expected_model).strip().lower()
    if returned_model != expected_model:
        raise BatchRequestError(
            f"O GitHub retornou model_key={returned_model!r}; esperado {expected_model!r}."
        )
    return value


def get_status(request_id: str, model_key: str = "metbr") -> dict[str, Any]:
    request_id = _request_id(request_id)
    model_key, _ = _model_config(model_key)
    status = _get_json_asset(request_id, "status.json", model_key)
    if status is None:
        return {"status": "queued", "request_id": request_id}

    result = dict(status)
    result.setdefault("request_id", request_id)
    if result.get("status") == "completed":
        manifest = _get_json_asset(request_id, "manifest.json", model_key)
        if manifest is not None:
            result["manifest"] = manifest
            result["available_hours"] = manifest.get("forecast_hours") or result.get("available_hours") or []
            result["run_utc"] = manifest.get("run_utc")
            result["release_tag"] = manifest.get("release_tag")
    return result


def get_profile(request_id: str, forecast_hour: int, model_key: str = "metbr") -> dict[str, Any]:
    """Return the published profile JSON for one completed validity."""
    request_id = _request_id(request_id)
    model_key, config = _model_config(model_key)
    try:
        hour = int(forecast_hour)
    except (TypeError, ValueError):
        raise ValueError("Validade inválida.") from None
    if hour < 0 or hour > int(config["max_forecast_hour"]):
        raise ValueError(
            f"Validade fora do intervalo F000–F{int(config['max_forecast_hour']):03d}."
        )

    status = _get_json_asset(request_id, "status.json", model_key)
    if status is None or status.get("status") != "completed":
        raise FileNotFoundError("O lote ainda não foi concluído.")
    available = status.get("available_hours") or []
    if hour not in {int(value) for value in available}:
        raise FileNotFoundError(f"F{hour:03d} não foi gerado neste lote.")

    profile = _get_json_asset(request_id, f"f{hour:03d}.json", model_key)
    if profile is None:
        raise FileNotFoundError(f"O perfil JSON F{hour:03d} ainda não foi publicado.")
    if profile.get("model_key") != model_key or profile.get("request_id") != request_id:
        raise BatchRequestError("O perfil publicado não corresponde ao modelo e pedido solicitados.")
    if int(profile.get("forecast_hour", -1)) != hour:
        raise BatchRequestError("A validade do perfil publicado não corresponde à solicitação.")
    return profile


def get_png(request_id: str, forecast_hour: int, model_key: str = "metbr") -> bytes:
    request_id = _request_id(request_id)
    model_key, config = _model_config(model_key)
    try:
        hour = int(forecast_hour)
    except (TypeError, ValueError):
        raise ValueError("Validade inválida.") from None
    if hour < 0 or hour > int(config["max_forecast_hour"]):
        raise ValueError(f"Validade fora do intervalo F000–F{int(config['max_forecast_hour']):03d}.")

    status = _get_json_asset(request_id, "status.json", model_key)
    if status is None or status.get("status") != "completed":
        raise FileNotFoundError("O lote ainda não foi concluído.")
    available = status.get("available_hours") or []
    if hour not in {int(value) for value in available}:
        raise FileNotFoundError(f"F{hour:03d} não foi gerado neste lote.")

    url = _release_asset_url(request_id, f"f{hour:03d}.png", model_key)
    try:
        response = requests.get(
            url,
            headers={
                "User-Agent": "Sideral-METBR-SkewT/1.0",
                "Accept": "image/png",
            },
            timeout=(10, 45),
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        raise BatchRequestError(
            f"Não foi possível carregar o PNG F{hour:03d} publicado pelo GitHub."
        ) from exc
    if len(response.content) < 10000 or len(response.content) > 25 * 1024 * 1024:
        raise BatchRequestError("O PNG publicado está vazio ou excede o tamanho permitido.")
    if not response.content.startswith(b"\x89PNG\r\n\x1a\n"):
        raise BatchRequestError("O arquivo publicado para esta validade não é um PNG válido.")
    return response.content
