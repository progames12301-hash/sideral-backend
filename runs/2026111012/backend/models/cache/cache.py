from __future__ import annotations

import hashlib
import os
import threading
import time
from pathlib import Path


class FileCache:
    """Cache em disco com escrita atômica e limpeza por idade."""

    def __init__(self, root: Path, max_age_days: int = 3) -> None:
        self.root = root.resolve()
        self.max_age_seconds = max(1, max_age_days) * 86400
        self._lock = threading.RLock()
        self.root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def digest(parts: list[str] | tuple[str, ...]) -> str:
        canonical = "\x1f".join(str(part) for part in parts)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def path_for(self, namespace: str, key: str, suffix: str) -> Path:
        safe_namespace = "".join(char for char in namespace.lower() if char.isalnum() or char in "-_") or "default"
        safe_suffix = suffix if suffix.startswith(".") else f".{suffix}"
        target = (self.root / safe_namespace / f"{key}{safe_suffix}").resolve()
        if self.root not in target.parents:
            raise ValueError("Caminho de cache inválido.")
        return target

    def read(self, namespace: str, key: str, suffix: str) -> bytes | None:
        path = self.path_for(namespace, key, suffix)
        try:
            if not path.is_file() or time.time() - path.stat().st_mtime > self.max_age_seconds:
                return None
            return path.read_bytes()
        except OSError:
            return None

    def write(self, namespace: str, key: str, suffix: str, content: bytes) -> Path:
        target = self.path_for(namespace, key, suffix)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(f"{target.suffix}.{os.getpid()}.{threading.get_ident()}.tmp")
        with self._lock:
            temporary.write_bytes(content)
            os.replace(temporary, target)
        return target

    def cleanup(self) -> int:
        removed = 0
        cutoff = time.time() - self.max_age_seconds
        with self._lock:
            for path in self.root.rglob("*"):
                try:
                    if path.is_file() and path.stat().st_mtime < cutoff:
                        path.unlink()
                        removed += 1
                except OSError:
                    continue
        return removed
