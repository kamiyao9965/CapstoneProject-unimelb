from __future__ import annotations

from src.models import ExtractionInput, ParsedDocument


class FormatRouter:
    """
    Choose a prompt / extraction mode based on how tabular the source is.
    """

    def route(self, doc: ParsedDocument) -> ExtractionInput:
        mode = "table_assisted" if doc.table_coverage > 0.3 else "text_only"
        return ExtractionInput(
            mode=mode,
            text=doc.full_text,
            tables=doc.tables,
            source_document=doc,
        )
