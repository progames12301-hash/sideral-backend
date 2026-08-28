from __future__ import annotations

import datetime as dt
import json
import re
from dataclasses import asdict
from pathlib import Path
from typing import Any

from ..catalog import MODEL_BY_ID, PRODUCT_BY_ID
from ..config import (
    REMOTE_MODEL_BASE_URLS,
    REMOTE_MODEL_CACHE_SECONDS,
    REMOTE_MODEL_TIMEOUT_SECONDS,
    REMOTE_MODELS_ENABLED,
)
from ..processing import load_field
from ..processing.field import Field
from .github import GitHubModelSource


RUN_RE = re.compile(r"^\d{10}$")
FRAME_RE = re.compile(r"^f(\d{3})\.(png|npz)$", re.IGNORECASE)


class ModelStorage:
    """Descobre somente arquivos já publicados no armazenamento do backend."""

    def __init__(self, root: Path, remote_enabled: bool | None = None) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        enabled = REMOTE_MODELS_ENABLED if remote_enabled is None else bool(remote_enabled)
        self.remote = GitHubModelSource(
            REMOTE_MODEL_BASE_URLS,
            cache_seconds=REMOTE_MODEL_CACHE_SECONDS,
            timeout=REMOTE_MODEL_TIMEOUT_SECONDS,
        ) if enabled else None

    def model_dir(self, model: str) -> Path:
        if model not in MODEL_BY_ID:
            raise ValueError("Modelo inválido.")
        return self.root / model

    def run_dir(self, model: str, run: str) -> Path:
        if not RUN_RE.fullmatch(run):
            raise ValueError("Rodada inválida. Use YYYYMMDDHH.")
        return self.model_dir(model) / run

    def list_runs(self, model: str) -> list[str]:
        directory = self.model_dir(model)
        local = {item.name for item in directory.iterdir() if item.is_dir() and RUN_RE.fullmatch(item.name)} if directory.is_dir() else set()
        remote = set(self.remote.list_runs(model)) if self.remote else set()
        return sorted(local | remote, reverse=True)

    def manifest(self, model: str, run: str) -> dict[str, Any]:
        path = self.run_dir(model, run) / "manifest.json"
        if path.is_file():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(payload, dict):
                    return payload
            except (OSError, json.JSONDecodeError):
                pass
        return self.remote.manifest(model, run) if self.remote else {}

    def frame_path(self, model: str, run: str, product: str, region: str, forecast_hour: int, suffix: str) -> Path | None:
        if product not in PRODUCT_BY_ID:
            raise ValueError("Produto inválido.")
        if not re.fullmatch(r"[a-z0-9_]+", region):
            raise ValueError("Região inválida.")
        if forecast_hour < 0 or forecast_hour > 384:
            raise ValueError("Forecast hour inválido.")
        run_dir = self.run_dir(model, run)
        filename = f"f{forecast_hour:03d}.{suffix.lstrip('.')}"
        candidates = (
            run_dir / "frames" / region / product / filename,
            run_dir / "fields" / region / product / filename,
            run_dir / region / product / filename,
        )
        for path in candidates:
            resolved = path.resolve()
            if run_dir.resolve() not in resolved.parents:
                continue
            if resolved.is_file():
                return resolved
        return None

    def available_products(self, model: str, run: str) -> list[dict[str, Any]]:
        run_dir = self.run_dir(model, run)
        found: dict[str, set[int]] = {}
        if run_dir.is_dir():
            for path in run_dir.rglob("f*.*"):
                match = FRAME_RE.fullmatch(path.name)
                if not match or path.suffix.lower() not in {".png", ".npz"}:
                    continue
                product = next((part for part in reversed(path.parts[:-1]) if part in PRODUCT_BY_ID), None)
                if product:
                    found.setdefault(product, set()).add(int(match.group(1)))
        products = {
            product: {**asdict(PRODUCT_BY_ID[product]), "forecast_hours": sorted(hours), "source": "local"}
            for product, hours in found.items()
        }
        if self.remote:
            for item in self.remote.available_products(model, run):
                product = str(item.get("id") or "")
                if product not in PRODUCT_BY_ID:
                    continue
                existing = products.get(product)
                if existing:
                    existing["forecast_hours"] = sorted(set(existing["forecast_hours"]) | set(item.get("forecast_hours") or []))
                    existing["source"] = "local+github-actions"
                else:
                    products[product] = item
        return [
            products[product]
            for product in sorted(products)
        ]

    def forecast_hours(self, model: str, run: str) -> list[int]:
        manifest = self.manifest(model, run)
        declared = manifest.get("forecast_hours") or manifest.get("forecastHours")
        if isinstance(declared, list):
            values = sorted({int(item) for item in declared if str(item).lstrip("-").isdigit() and 0 <= int(item) <= 384})
            if values:
                return values
        values: set[int] = set()
        for product in self.available_products(model, run):
            values.update(product["forecast_hours"])
        return sorted(values)

    def product_hours(self, model: str, run: str, product: str) -> list[int]:
        match = next((item for item in self.available_products(model, run) if item.get("id") == product), None)
        return list(match.get("forecast_hours") or []) if match else []

    def field(self, model: str, run: str, product: str, region: str, forecast_hour: int) -> tuple[Field, str] | None:
        path = self.frame_path(model, run, product, region, forecast_hour, "npz")
        if path:
            value = load_field(path, model=model, product=product, run=run, forecast_hour=forecast_hour)
            stat = path.stat()
            return value, f"local:{stat.st_mtime_ns}:{stat.st_size}"
        if self.remote:
            value = self.remote.field(model, run, product, forecast_hour)
            if value is not None:
                return value, self.remote.fingerprint(model, run, product, forecast_hour)
        return None

    def status(self, model: str, now: dt.datetime | None = None) -> dict[str, Any]:
        now = now or dt.datetime.now(dt.timezone.utc)
        directory = self.model_dir(model)
        if (directory / ".processing").exists():
            return {"id": model, "status": "processing", "run": self.list_runs(model)[0] if self.list_runs(model) else None}
        runs = self.list_runs(model)
        if not runs:
            return {"id": model, "status": "unavailable", "run": None}
        remote_status = self.remote.status(model, now) if self.remote else None
        local_runs = [item.name for item in directory.iterdir() if item.is_dir() and RUN_RE.fullmatch(item.name)] if directory.is_dir() else []
        if remote_status and (not local_runs or remote_status["run"] >= max(local_runs)):
            return remote_status
        latest = runs[0]
        run_time = dt.datetime.strptime(latest, "%Y%m%d%H").replace(tzinfo=dt.timezone.utc)
        age_hours = max(0.0, (now - run_time).total_seconds() / 3600)
        status = "online" if age_hours <= 18 else "delayed"
        return {"id": model, "status": status, "run": latest, "age_hours": round(age_hours, 1)}

    def common_runs(self, models: list[str], minimum: int) -> list[dict[str, Any]]:
        membership: dict[str, list[str]] = {}
        for model in models:
            for run in self.list_runs(model):
                membership.setdefault(run, []).append(model)
        return [
            {"run": run, "models": sorted(members), "model_count": len(members)}
            for run, members in sorted(membership.items(), reverse=True)
            if len(members) >= minimum
        ]
