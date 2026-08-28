from __future__ import annotations

import datetime as dt
import gzip
import json
import re
import threading
import time
from dataclasses import asdict
from typing import Any

import numpy as np
import requests

from ..catalog import PRODUCT_BY_ID
from ..processing.field import Field


FRAME_NAME_RE = re.compile(r"^[a-z0-9_-]+/f\d{3}\.json\.gz$")
MAX_COMPRESSED_BYTES = 12 * 1024 * 1024
MAX_DECOMPRESSED_BYTES = 80 * 1024 * 1024

# Campo remoto, unidade publicada e fator para a unidade do catalogo.
REMOTE_PRODUCTS: dict[str, tuple[str, str, float]] = {
    "qpf1": ("precipitation", "mm", 1.0),
    "qpf3": ("precipitation", "mm", 1.0),
    "qpf6": ("precipitation", "mm", 1.0),
    "mucape": ("mucape", "J/kg", 1.0),
    "temp2m": ("temperature", "Â°C", 1.0),
    "humidity2m": ("humidity", "%", 1.0),
    "wind10m": ("windSpeed", "m/s", 1.0 / 3.6),
    "pwat": ("waterVapor", "mm", 1.0),
}

CAPABILITY_BY_PRODUCT = {
    "qpf1": "precipitation",
    "qpf3": "precipitation",
    "qpf6": "precipitation",
    "qpf24": "precipitation",
    "mucape": "mucape",
    "temp2m": "temperature",
    "humidity2m": "humidity",
    "wind10m": "wind",
    "pwat": "waterVapor",
}


class GitHubModelSource:
    """Le campos reais compactados publicados por GitHub Actions.

    O adaptador valida schema, modelo, rodada, passo e dimensoes antes de
    entregar qualquer valor ao processamento. Uma branch ausente apenas torna
    aquele modelo indisponivel; ela nunca gera dados substitutos.
    """

    def __init__(self, base_urls: dict[str, str], cache_seconds: int = 120, timeout: int = 35) -> None:
        self.base_urls = {key: value.rstrip("/") for key, value in base_urls.items() if value}
        self.cache_seconds = max(30, int(cache_seconds))
        self.timeout = max(5, int(timeout))
        self._metadata: dict[str, tuple[float, dict[str, Any] | None]] = {}
        self._frames: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}
        self._lock = threading.RLock()

    @staticmethod
    def _run_id(payload: dict[str, Any]) -> str:
        run_date = str(payload.get("runDate") or "")
        cycle = re.sub(r"\D", "", str(payload.get("runCycle") or ""))[:2].zfill(2)
        run = run_date + cycle
        if not re.fullmatch(r"\d{10}", run):
            raise ValueError("Metadata remoto sem rodada valida.")
        return run

    def metadata(self, model: str, *, refresh: bool = False) -> dict[str, Any] | None:
        if model not in self.base_urls:
            return None
        now = time.monotonic()
        with self._lock:
            cached = self._metadata.get(model)
            if not refresh and cached and now - cached[0] < self.cache_seconds:
                return cached[1]
        try:
            response = requests.get(
                f"{self.base_urls[model]}/metadata.json",
                headers={"User-Agent": "SideralMeteorologia/3.0", "Accept": "application/json"},
                timeout=self.timeout,
            )
            if response.status_code == 404:
                payload = None
            else:
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict) or payload.get("schema") != "sideral-model-metadata-v1":
                    raise ValueError("Schema de metadata remoto invalido.")
                if str(payload.get("model") or "").lower() != model:
                    raise ValueError("Metadata remoto pertence a outro modelo.")
                self._run_id(payload)
                frames = payload.get("frames")
                if not isinstance(frames, list) or not frames:
                    raise ValueError("Metadata remoto sem quadros.")
        except (requests.RequestException, ValueError, json.JSONDecodeError):
            payload = None
        with self._lock:
            self._metadata[model] = (now, payload)
        return payload

    def list_runs(self, model: str) -> list[str]:
        payload = self.metadata(model)
        return [self._run_id(payload)] if payload else []

    def manifest(self, model: str, run: str) -> dict[str, Any]:
        payload = self.metadata(model)
        return payload if payload and self._run_id(payload) == run else {}

    @staticmethod
    def _frame_records(payload: dict[str, Any]) -> list[dict[str, Any]]:
        frames = [item for item in payload.get("frames", []) if isinstance(item, dict)]
        return sorted(frames, key=lambda item: int(item.get("forecastHour") or -1))

    @staticmethod
    def _interval_hours(payload: dict[str, Any]) -> float:
        try:
            return max(0.25, float(payload.get("temporalResolutionMinutes") or 60) / 60.0)
        except (TypeError, ValueError):
            return 1.0

    def product_hours(self, model: str, run: str, product: str) -> list[int]:
        payload = self.manifest(model, run)
        if not payload or product not in CAPABILITY_BY_PRODUCT:
            return []
        capabilities = payload.get("capabilities") or {}
        if not capabilities.get(CAPABILITY_BY_PRODUCT[product], False):
            return []
        hours = [int(item.get("forecastHour")) for item in self._frame_records(payload) if str(item.get("forecastHour", "")).isdigit()]
        if product in {"qpf1", "qpf3", "qpf6"}:
            published = str(payload.get("precipitationProduct") or "qpf1").lower()
            if product != published:
                return []
        if product == "qpf24":
            covered = len(hours) * self._interval_hours(payload)
            return [hours[-1]] if hours and covered >= 23.5 else []
        return hours

    def available_products(self, model: str, run: str) -> list[dict[str, Any]]:
        found = []
        for product in CAPABILITY_BY_PRODUCT:
            hours = self.product_hours(model, run, product)
            if hours:
                found.append({**asdict(PRODUCT_BY_ID[product]), "forecast_hours": hours, "source": "github-actions"})
        return found

    def forecast_hours(self, model: str, run: str) -> list[int]:
        payload = self.manifest(model, run)
        if not payload:
            return []
        return sorted({int(item.get("forecastHour")) for item in self._frame_records(payload) if str(item.get("forecastHour", "")).isdigit()})

    def _frame(self, model: str, filename: str) -> dict[str, Any]:
        if model not in self.base_urls or not FRAME_NAME_RE.fullmatch(filename) or not filename.startswith(f"{model}/"):
            raise ValueError("Nome de quadro remoto invalido.")
        key = (model, filename)
        now = time.monotonic()
        with self._lock:
            cached = self._frames.get(key)
            if cached and now - cached[0] < self.cache_seconds:
                return cached[1]
        response = requests.get(
            f"{self.base_urls[model]}/{filename}",
            headers={"User-Agent": "SideralMeteorologia/3.0", "Accept": "application/gzip"},
            timeout=self.timeout,
        )
        response.raise_for_status()
        if len(response.content) > MAX_COMPRESSED_BYTES:
            raise ValueError("Quadro remoto excede o limite compactado.")
        decoded = gzip.decompress(response.content)
        if len(decoded) > MAX_DECOMPRESSED_BYTES:
            raise ValueError("Quadro remoto excede o limite descompactado.")
        payload = json.loads(decoded.decode("utf-8"))
        if not isinstance(payload, dict) or payload.get("schema") != "sideral-model-grid-v1":
            raise ValueError("Schema de quadro remoto invalido.")
        if str(payload.get("model") or "").lower() != model:
            raise ValueError("Quadro remoto pertence a outro modelo.")
        with self._lock:
            self._frames[key] = (now, payload)
            # Cada JSON remoto contem varias matrizes e objetos Python. Manter
            # dezenas deles derruba instancias pequenas do Render por memoria.
            # Dois quadros preservam a navegacao adjacente sem reter uma rodada.
            while len(self._frames) > 2:
                self._frames.pop(next(iter(self._frames)))
        return payload

    def _field_from_frame(self, model: str, run: str, product: str, record: dict[str, Any]) -> Field:
        filename = str(record.get("file") or "")
        frame = self._frame(model, filename)
        forecast_hour = int(record.get("forecastHour"))
        if int(frame.get("forecastHour") or -1) != forecast_hour:
            raise ValueError("Forecast hour remoto divergente.")
        grid_x, grid_y = int(frame.get("gridX") or 0), int(frame.get("gridY") or 0)
        fields = frame.get("fields")
        if grid_x < 2 or grid_y < 2 or not isinstance(fields, dict):
            raise ValueError("Grade remota invalida.")
        remote_name, unit, factor = REMOTE_PRODUCTS[product]
        expected = grid_x * grid_y
        lat = np.asarray(fields.get("lat"), dtype=float)
        lon = np.asarray(fields.get("lon"), dtype=float)
        values = np.asarray(fields.get(remote_name), dtype=float)
        if lat.size != expected or lon.size != expected or values.size != expected:
            raise ValueError("Dimensoes do quadro remoto invalidas.")
        values = values.reshape(grid_y, grid_x) * factor
        if not np.isfinite(values).any():
            raise ValueError("Campo remoto sem valores finitos.")
        return Field(
            lat=lat.reshape(grid_y, grid_x),
            lon=lon.reshape(grid_y, grid_x),
            values=values,
            unit=unit,
            valid_time=str(frame.get("validTime") or record.get("validTime") or ""),
            model=model,
            product=product,
            run=run,
            forecast_hour=forecast_hour,
        )

    def field(self, model: str, run: str, product: str, forecast_hour: int) -> Field | None:
        payload = self.manifest(model, run)
        if not payload:
            return None
        records = self._frame_records(payload)
        selected = next((item for item in records if int(item.get("forecastHour") or -1) == forecast_hour), None)
        if not selected:
            return None
        if product == "qpf24":
            allowed = self.product_hours(model, run, product)
            if forecast_hour not in allowed:
                return None
            precipitation_product = str(payload.get("precipitationProduct") or "qpf1").lower()
            if precipitation_product not in {"qpf1", "qpf3", "qpf6"}:
                return None
            fields = [self._field_from_frame(model, run, precipitation_product, item) for item in records]
            interval = self._interval_hours(payload)
            multiplier = interval if payload.get("precipitationIsRate", True) else 1.0
            values = np.sum([item.values * multiplier for item in fields], axis=0)
            last = fields[-1]
            return Field(last.lat, last.lon, values, "mm", last.valid_time, model, product, run, forecast_hour)
        if product not in REMOTE_PRODUCTS or forecast_hour not in self.product_hours(model, run, product):
            return None
        return self._field_from_frame(model, run, product, selected)

    def fingerprint(self, model: str, run: str, product: str, forecast_hour: int) -> str:
        payload = self.manifest(model, run)
        generated = str(payload.get("generatedAt") or "") if payload else ""
        return f"github:{model}:{run}:{product}:{forecast_hour}:{generated}"

    def status(self, model: str, now: dt.datetime | None = None) -> dict[str, Any] | None:
        payload = self.metadata(model)
        if not payload:
            return None
        run = self._run_id(payload)
        current = now or dt.datetime.now(dt.timezone.utc)
        try:
            generated = dt.datetime.fromisoformat(str(payload.get("generatedAt") or "").replace("Z", "+00:00"))
            age_hours = max(0.0, (current - generated).total_seconds() / 3600.0)
        except (TypeError, ValueError):
            initial = dt.datetime.strptime(run, "%Y%m%d%H").replace(tzinfo=dt.timezone.utc)
            age_hours = max(0.0, (current - initial).total_seconds() / 3600.0)
        return {
            "id": model,
            "status": "online" if age_hours <= 30 else "delayed",
            "run": run,
            "age_hours": round(age_hours, 1),
            "updated": payload.get("generatedAt"),
            "source": "github-actions",
        }

