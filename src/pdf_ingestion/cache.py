from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


class PDFCache:
    """Versioned on-disk cache keyed by PDF content and parser configuration."""

    def __init__(self, cache_dir: str | Path) -> None:
        self.cache_dir = Path(cache_dir)

    def path_for(self, pdf_hash: str, parser_config_hash: str) -> Path:
        return self.cache_dir / pdf_hash[:2] / f"{pdf_hash}.{parser_config_hash}.json"

    def load(self, pdf_hash: str, parser_config_hash: str) -> dict[str, Any] | None:
        cache_path = self.path_for(pdf_hash, parser_config_hash)
        if not cache_path.exists():
            return None
        return json.loads(cache_path.read_text(encoding="utf-8"))

    def write(
        self,
        pdf_hash: str,
        parser_config_hash: str,
        payload: dict[str, Any],
    ) -> Path:
        cache_path = self.path_for(pdf_hash, parser_config_hash)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return cache_path


def sha256_file(path: str | Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stable_config_hash(config: dict[str, Any]) -> str:
    encoded = json.dumps(config, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]
