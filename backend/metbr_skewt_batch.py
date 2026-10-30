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


def _release_asset_url(request_id: str, asset_name: str) -> str:
    request_id = _request_id(request_id)
    if asset_name not in {"status.json", "manifest.json"} and not re.fullmatch(
        r"f(?:00[0-9]|0[1-3][0-9]|04[0-2])\.png", asset_name
    ):
        raise ValueError("Arquivo de sondagem inválido.")
    tag = f"metbr-skewt-click-{request_id}"
    return f"{GITHUB_RELEASE_DOWNLOAD}/{tag}/{asset_name}"


def dispatch_request(payload: dict[str, Any], client_ip: str = "") -> dict[str, Any]:
    """Request one complete point-specific forecast-hour batch through workflow_dispatch."""
    try:
        latitude = float(payload.get("latitude"))
        longitude = float(payload.get("longitude"))
    except (TypeError, ValueError):
        raise ValueError("Informe latitude e longitude válidas.") from None
    if not (-90.0 <= latitude <= 90.0 and -180.0 <= longitude <= 180.0):
        raise ValueError("Latitude/longitude fora dos limites geográficos.")

    _check_dispatch_rate(client_ip)
    cycle = str(payload.get("cycle") or "latest").strip().lower()
    if cycle not in ALLOWED_CYCLES:
        raise ValueError("Rodada inválida. Use latest, 00, 06, 12, 18 ou 21.")

    token = os.environ.get("METBR_SKEWT_GITHUB_TOKEN", "").strip()
    if not token:
        raise BatchRequestError(
            "O disparo do lote ainda não está habilitado no Render. "
            "Configure a variável METBR_SKEWT_GITHUB_TOKEN com um token GitHub "
            "que tenha permissão Actions: write neste repositório."
        )

    request_id = uuid.uuid4().hex
    url = (
        f"{GITHUB_API}/repos/{GITHUB_REPOSITORY}/actions/workflows/"
        f"{WORKFLOW_FILE}/dispatches"
    )
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "Sideral-METBR-SkewT/1.0",
    }
    body = {
        "ref": "main",
        "inputs": {
            "latitude": f"{latitude:.6f}",
            "longitude": f"{longitude:.6f}",
            "cycle": cycle,
            "request_id": request_id,
        },
    }
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
                "O GitHub recusou o disparo do workflow "
                f"(HTTP {response.status_code}). Verifique o token e a permissão Actions: write."
                + (f" Detalhe: {detail[:240]}" if detail else "")
            )
        raise BatchRequestError(
            f"O GitHub não aceitou o lote (HTTP {response.status_code})."
            + (f" {detail[:240]}" if detail else "")
        )

    return {
        "status": "queued",
        "request_id": request_id,
        "latitude": latitude,
        "longitude": longitude,
        "cycle": cycle,
        "workflow": WORKFLOW_FILE,
        "message": "Lote solicitado ao GitHub Actions.",
    }


def _get_json_asset(request_id: str, asset_name: str) -> dict[str, Any] | None:
    url = _release_asset_url(request_id, asset_name)
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
    return value


def get_status(request_id: str) -> dict[str, Any]:
    request_id = _request_id(request_id)
    status = _get_json_asset(request_id, "status.json")
    if status is None:
        return {"status": "queued", "request_id": request_id}

    result = dict(status)
    result.setdefault("request_id", request_id)
    if result.get("status") == "completed":
        manifest = _get_json_asset(request_id, "manifest.json")
        if manifest is not None:
            result["manifest"] = manifest
            result["available_hours"] = manifest.get("forecast_hours") or result.get("available_hours") or []
            result["run_utc"] = manifest.get("run_utc")
            result["release_tag"] = manifest.get("release_tag")
    return result


def get_png(request_id: str, forecast_hour: int) -> bytes:
    request_id = _request_id(request_id)
    try:
        hour = int(forecast_hour)
    except (TypeError, ValueError):
        raise ValueError("Validade inválida.") from None
    if hour < 0 or hour > 42:
        raise ValueError("Validade fora do intervalo F000–F042.")

    status = _get_json_asset(request_id, "status.json")
    if status is None or status.get("status") != "completed":
        raise FileNotFoundError("O lote ainda não foi concluído.")
    available = status.get("available_hours") or []
    if hour not in {int(value) for value in available}:
        raise FileNotFoundError(f"F{hour:03d} não foi gerado neste lote.")

    url = _release_asset_url(request_id, f"f{hour:03d}.png")
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
