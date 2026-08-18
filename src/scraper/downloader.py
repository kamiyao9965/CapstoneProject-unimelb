"""Streaming, content-addressed PDF persistence."""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path


_SAFE_COMPONENT = re.compile(r"^[a-z][a-z0-9_]*$")
_PLAUSIBLE_PDF_TYPES = {
    "",
    "application/pdf",
    "application/x-pdf",
    "application/octet-stream",
    "binary/octet-stream",
}


class PdfValidationError(ValueError):
    def __init__(self, code: str, validation_status: str, message: str) -> None:
        self.code = code
        self.validation_status = validation_status
        super().__init__(message)


@dataclass(frozen=True)
class StoredPdf:
    path: Path
    sha256: str
    size_bytes: int
    content_type: str
    retrieval_status: str
    validation_status: str = "valid_pdf"


class PdfStore:
    """Persist validated PDF byte streams below a controlled data root."""

    def __init__(self, root: str | Path, *, max_bytes: int = 50 * 1024 * 1024) -> None:
        self.root = Path(root)
        if max_bytes < 5:
            raise ValueError("max_bytes must allow at least a PDF signature.")
        self.max_bytes = max_bytes

    def store(
        self,
        chunks: Iterable[bytes],
        *,
        insurer_code: str,
        document_type: str,
        title: str,
        content_type: str | None,
    ) -> StoredPdf:
        _validate_component(insurer_code, "insurer_code")
        _validate_component(document_type, "document_type")
        media_type = (content_type or "").split(";", 1)[0].strip().lower()
        if media_type not in _PLAUSIBLE_PDF_TYPES:
            raise PdfValidationError(
                "unexpected_content_type",
                "not_pdf",
                f"Response content type {media_type!r} is not a PDF type.",
            )

        destination_dir = self.root / insurer_code / document_type
        destination_dir.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            dir=destination_dir,
            prefix=".download-",
            suffix=".part",
        )
        temporary = Path(temporary_name)
        digest = hashlib.sha256()
        signature = bytearray()
        size = 0
        try:
            with os.fdopen(descriptor, "wb") as handle:
                for chunk in chunks:
                    if not isinstance(chunk, bytes):
                        raise TypeError("PDF chunks must be bytes.")
                    if not chunk:
                        continue
                    size += len(chunk)
                    if size > self.max_bytes:
                        raise PdfValidationError(
                            "pdf_size_exceeded",
                            "size_exceeded",
                            f"PDF exceeded the configured {self.max_bytes}-byte limit.",
                        )
                    if len(signature) < 5:
                        signature.extend(chunk[: 5 - len(signature)])
                    digest.update(chunk)
                    handle.write(chunk)
                handle.flush()
                os.fsync(handle.fileno())
            if bytes(signature) != b"%PDF-":
                raise PdfValidationError(
                    "invalid_pdf_signature",
                    "not_pdf",
                    "Downloaded body does not start with a PDF signature.",
                )
            hexdigest = digest.hexdigest()
            duplicate = next(self.root.rglob(f"{hexdigest}_*.pdf"), None)
            if duplicate is not None:
                return StoredPdf(
                    path=duplicate,
                    sha256=hexdigest,
                    size_bytes=size,
                    content_type=media_type,
                    retrieval_status="duplicate",
                )

            filename = f"{hexdigest}_{_slug(title)}.pdf"
            destination = destination_dir / filename
            try:
                os.link(temporary, destination)
            except FileExistsError:
                return StoredPdf(
                    path=destination,
                    sha256=hexdigest,
                    size_bytes=size,
                    content_type=media_type,
                    retrieval_status="duplicate",
                )
            return StoredPdf(
                path=destination,
                sha256=hexdigest,
                size_bytes=size,
                content_type=media_type,
                retrieval_status="downloaded",
            )
        finally:
            temporary.unlink(missing_ok=True)


def _validate_component(value: str, label: str) -> None:
    if not _SAFE_COMPONENT.fullmatch(value):
        raise ValueError(f"{label} must be a lowercase safe path component.")


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return (slug[:80].rstrip("-") or "document")
