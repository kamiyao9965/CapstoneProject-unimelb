from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

import yaml

from src.models import SchemaField, SchemaSection, VerticalSchema
from src.pipeline.ingestor import PDFIngestor


class SchemaDiscovery:
    """
    Offline-friendly schema draft generation.

    When no LLM is configured, discovery falls back to frequency-based inference
    over title-case phrases and insurance-specific field cues.
    """

    def __init__(self, ingestor: PDFIngestor | None = None) -> None:
        self.ingestor = ingestor or PDFIngestor()

    def discover(self, sample_pdfs: list[str], vertical: str) -> str:
        docs = [self.ingestor.ingest(path) for path in sample_pdfs]
        tokens = Counter()
        for doc in docs:
            for match in re.findall(r"[A-Z][A-Za-z&/\-]{2,}(?:\s+[A-Z][A-Za-z&/\-]{2,}){0,3}", doc.full_text):
                cleaned = " ".join(match.split())
                if len(cleaned) > 2:
                    tokens[cleaned] += 1

        likely_categories = [token for token, count in tokens.most_common(20) if count >= 2]
        coverage_fields = [
            SchemaField(name="product_name", type="string", description="Marketing product name"),
            SchemaField(name="product_tier", type="string", description="Primary plan tier or variant"),
        ]
        section = SchemaSection(
            fields=coverage_fields,
            canonical_services=[self._canonicalize(token) for token in likely_categories[:10]],
            aliases={self._canonicalize(token): [token] for token in likely_categories[:10]},
        )
        schema = VerticalSchema(vertical=vertical, version="0.1-draft", coverage=section)
        return yaml.safe_dump(self._schema_to_dict(schema), sort_keys=False, allow_unicode=False)

    def _canonicalize(self, value: str) -> str:
        parts = re.findall(r"[A-Za-z0-9]+", value)
        return "".join(word.capitalize() for word in parts)

    def _schema_to_dict(self, schema: VerticalSchema) -> dict:
        if hasattr(schema, "model_dump"):
            return schema.model_dump(mode="python")
        return schema.dict()
