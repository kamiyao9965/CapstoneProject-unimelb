from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from src.schema.discovery import SchemaDiscovery


class FailingFileClient:
    def __init__(self) -> None:
        self.create_calls = 0
        self.deleted: list[str] = []
        self.files = self

    def create(self, *, file, purpose: str):
        self.create_calls += 1
        if self.create_calls == 2:
            raise RuntimeError("upload failed")
        return SimpleNamespace(id="file_001")

    def delete(self, file_id: str) -> None:
        self.deleted.append(file_id)


class SchemaDiscoveryUploadCleanupTest(unittest.TestCase):
    def test_partial_upload_failure_deletes_already_uploaded_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            first_pdf = Path(tmp) / "first.pdf"
            second_pdf = Path(tmp) / "second.pdf"
            first_pdf.touch()
            second_pdf.touch()
            client = FailingFileClient()
            discovery = SchemaDiscovery(
                client=client,
                usage_log_path=None,
                log=None,
            )

            with mock.patch.dict(
                os.environ,
                {"MY_OPENAI_API_KEY": "test-only"},
                clear=True,
            ):
                with self.assertRaisesRegex(RuntimeError, "upload failed"):
                    discovery.discover([str(first_pdf), str(second_pdf)])

            self.assertEqual(client.deleted, ["file_001"])


if __name__ == "__main__":
    unittest.main()
