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
REMOTE_MODELS_ENABLED = os.environ.get("SIDERAL_REMOTE_MODELS_ENABLED", "1").strip().lower() not in {"0", "false", "no"}
REMOTE_MODEL_CACHE_SECONDS = max(30, int(os.environ.get("SIDERAL_REMOTE_MODEL_CACHE_SECONDS", "120")))
REMOTE_MODEL_TIMEOUT_SECONDS = max(5, int(os.environ.get("SIDERAL_REMOTE_MODEL_TIMEOUT_SECONDS", "35")))
REMOTE_MODEL_BASE_URLS = {
    "ecmwf": os.environ.get(
        "SIDERAL_ECMWF_DATA_URL",
        "https://raw.githubusercontent.com/progames12301-hash/sideral-backend/ecmwf-data",
    ).rstrip("/"),
    "icon": os.environ.get(
        "SIDERAL_ICON_DATA_URL",
        "https://raw.githubusercontent.com/progames12301-hash/sideral-backend/icon-data",
    ).rstrip("/"),
    "gfs": os.environ.get(
        "SIDERAL_GFS_DATA_URL",
        "https://raw.githubusercontent.com/progames12301-hash/sideral-backend/gfs-model-data",
    ).rstrip("/"),
    "aifs": os.environ.get(
        "SIDERAL_AIFS_DATA_URL",
        "https://raw.githubusercontent.com/progames12301-hash/sideral-backend/aifs-data",
    ).rstrip("/"),
}


def ensure_backend_directories() -> None:
    MODEL_DATA_DIR.mkdir(parents=True, exist_ok=True)
    MODEL_CACHE_DIR.mkdir(parents=True, exist_ok=True)
