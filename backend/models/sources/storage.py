from __future__ import annotations

import datetime as dt
import json
import re
from dataclasses import asdict
from pathlib import Path
from typing import Any

from ..catalog import MODEL_BY_ID, PRODUCT_BY_ID


RUN_RE = re.compile(r"^\d{10}$")
FRAME_RE = re.compile(r"^f(\d{3})\.(png|npz)$", re.IGNORECASE)


class ModelStorage:
    """Descobre somente arquivos já publicados no armazenamento do backend."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

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
        if not directory.is_dir():
            return []
        return sorted((item.name for item in directory.iterdir() if item.is_dir() and RUN_RE.fullmatch(item.name)), reverse=True)

    def manifest(self, model: str, run: str) -> dict[str, Any]:
        path = self.run_dir(model, run) / "manifest.json"
        if not path.is_file():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            return payload if isinstance(payload, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

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
        return [
            {**asdict(PRODUCT_BY_ID[product]), "forecast_hours": sorted(hours)}
            for product, hours in sorted(found.items())
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

    def status(self, model: str, now: dt.datetime | None = None) -> dict[str, Any]:
        now = now or dt.datetime.now(dt.timezone.utc)
        directory = self.model_dir(model)
        if (directory / ".processing").exists():
            return {"id": model, "status": "processing", "run": self.list_runs(model)[0] if self.list_runs(model) else None}
        runs = self.list_runs(model)
        if not runs:
            return {"id": model, "status": "unavailable", "run": None}
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
