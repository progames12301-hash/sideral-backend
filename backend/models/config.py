from __future__ import annotations

import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
MODEL_DATA_DIR = Path(os.environ.get("SIDERAL_MODEL_DATA_DIR", PROJECT_ROOT / "model_data")).resolve()
MODEL_CACHE_DIR = Path(os.environ.get("SIDERAL_MODEL_CACHE_DIR", PROJECT_ROOT / "model_cache")).resolve()
COMMON_GRID_DEGREES = float(os.environ.get("SIDERAL_COMMON_GRID_DEGREES", "0.25"))
MIN_MULTIMODEL_MEMBERS = max(2, int(os.environ.get("SIDERAL_MIN_MULTIMODEL_MEMBERS", "2")))
MAX_FIELD_BYTES = max(1_000_000, int(os.environ.get("SIDERAL_MAX_FIELD_BYTES", str(250 * 1024 * 1024))))
MAX_CACHE_AGE_DAYS = max(1, int(os.environ.get("SIDERAL_MODEL_CACHE_DAYS", "3")))


def ensure_backend_directories() -> None:
    MODEL_DATA_DIR.mkdir(parents=True, exist_ok=True)
    MODEL_CACHE_DIR.mkdir(parents=True, exist_ok=True)
