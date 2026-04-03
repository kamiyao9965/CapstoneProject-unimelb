from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from src.config import load_config
from src.models import DownloadResult, ManifestRecord, PDFLink, RateLimitConfig

try:
    import httpx
except ImportError:  # pragma: no cover - optional dependency
    httpx = None


class PDFDownloader:
    """
    Download PDFs with SHA256 de-duplication and manifest logging.
    """

    def __init__(self, base_dir: str | Path, rate_limiter: RateLimitConfig) -> None:
        self.base_dir = Path(base_dir)
        self.rate_limiter = rate_limiter
        self.manifest_path = self.base_dir / "manifest.jsonl"
        self.hash_index = self._load_existing_hashes()
        self._last_request_at = 0.0

    @classmethod
    def for_vertical(cls, vertical: str, rate_limiter: RateLimitConfig) -> "PDFDownloader":
        config = load_config()
        return cls(config.data_dir / vertical / "raw" / "PDFs", rate_limiter)

    def download(self, link: PDFLink) -> DownloadResult:
        if httpx is None:
            return DownloadResult(pdf_link=link, status="failed", error="httpx is not installed")

        for attempt in range(self.rate_limiter.retry_attempts):
            try:
                self._respect_rate_limit()
                with httpx.Client(follow_redirects=True, timeout=60.0) as client:
                    response = client.get(link.url)
                    response.raise_for_status()
                    content = response.content

                content_hash = self._compute_hash(content)
                if content_hash in self.hash_index:
                    return DownloadResult(
                        pdf_link=link,
                        local_path=self.hash_index[content_hash],
                        status="duplicate",
                        content_hash=content_hash,
                    )

                local_path = self._build_local_path(link)
                local_path.parent.mkdir(parents=True, exist_ok=True)
                local_path.write_bytes(content)
                self.hash_index[content_hash] = str(local_path)
                self._append_manifest(
                    ManifestRecord(
                        url=link.url,
                        local_path=str(local_path),
                        hash=content_hash,
                        downloaded_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                        fund_code=link.fund_code,
                        vertical=link.vertical,
                        product_type=link.product_type,
                        source_page=link.source_page,
                        metadata=link.metadata,
                    )
                )
                return DownloadResult(
                    pdf_link=link,
                    local_path=str(local_path),
                    status="success",
                    content_hash=content_hash,
                )
            except Exception as exc:  # pragma: no cover - depends on network
                if attempt == self.rate_limiter.retry_attempts - 1:
                    return DownloadResult(pdf_link=link, status="failed", error=str(exc))
                backoff = self.rate_limiter.backoff_seconds[min(attempt, len(self.rate_limiter.backoff_seconds) - 1)]
                time.sleep(backoff)
        return DownloadResult(pdf_link=link, status="failed", error="Unknown download error")

    def _load_existing_hashes(self) -> dict[str, str]:
        if not self.manifest_path.exists():
            return {}
        hashes: dict[str, str] = {}
        with self.manifest_path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                record = json.loads(line)
                hashes[record["hash"]] = record["local_path"]
        return hashes

    def _append_manifest(self, record: ManifestRecord) -> None:
        self.manifest_path.parent.mkdir(parents=True, exist_ok=True)
        with self.manifest_path.open("a", encoding="utf-8") as handle:
            handle.write(record.model_dump_json())
            handle.write("\n")

    def _respect_rate_limit(self) -> None:
        minimum_interval = 1 / max(self.rate_limiter.requests_per_second, 0.01)
        elapsed = time.time() - self._last_request_at
        if elapsed < minimum_interval:
            time.sleep(minimum_interval - elapsed)
        self._last_request_at = time.time()

    @staticmethod
    def _compute_hash(content: bytes) -> str:
        return hashlib.sha256(content).hexdigest()

    def _build_local_path(self, link: PDFLink) -> Path:
        file_name = Path(link.url).name or f"{link.fund_code}_{link.product_type}.pdf"
        return self.base_dir / link.fund_code / link.product_type / file_name
