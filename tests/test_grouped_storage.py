from __future__ import annotations

import copy
import json
import unittest
from contextlib import contextmanager
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from unittest import mock

from sqlalchemy import UniqueConstraint
from sqlalchemy.dialects import postgresql

from src.common.json_artifacts import build_success_artifact
from src.schema.canonical import compile_canonical_extraction_contract
from src.storage import service
from src.storage.canonical import compile_vertical_storage_metadata
from src.storage.repository import deterministic_product_id, deterministic_release_id, write_prepared_load
from src.verticals.documents import document_group_id, resolve_document_groups
from tests import test_delivery_gate as delivery_fixture


class MappingResult:
    def __init__(self, row=None) -> None:
        self.row = row

    def mappings(self):
        return self

    def first(self):
        return self.row


class GroupRecordingConnection:
    """Record real PostgreSQL statements and enforce their declared unique keys."""

    def __init__(self, *, fail_second_document_link: bool = False) -> None:
        self.rows: dict[str, dict[tuple, dict]] = {}
        self.operations: list[tuple[str, str, dict]] = []
        self.fail_second_document_link = fail_second_document_link
        self.document_links = 0

    def execute(self, statement):
        if statement.is_insert:
            table = statement.table
            compiled = statement.compile(dialect=postgresql.dialect())
            values = {name: compiled.params[name] for name in table.c.keys() if name in compiled.params}
            self.operations.append(("insert", table.name, copy.deepcopy(values)))
            if table.name == "product_release_documents":
                self.document_links += 1
                if self.fail_second_document_link and self.document_links == 2:
                    raise RuntimeError("synthetic second source link failure")
            key = tuple(values[column.name] for column in table.primary_key.columns)
            rows = self.rows.setdefault(table.name, {})
            if key in rows:
                if "DO UPDATE" in str(compiled):
                    rows[key].update(copy.deepcopy(values))
                return MappingResult()
            for constraint in table.constraints:
                if not isinstance(constraint, UniqueConstraint):
                    continue
                columns = tuple(column.name for column in constraint.columns)
                if any(values.get(name) is None for name in columns):
                    continue
                for existing in rows.values():
                    if all(existing.get(name) == values.get(name) for name in columns):
                        if "DO NOTHING" in str(compiled):
                            return MappingResult()
                        raise RuntimeError(f"Unique constraint rejected new {table.name} identity: {columns}")
            rows[key] = copy.deepcopy(values)
            return MappingResult()
        if statement.is_select:
            table = statement.get_final_froms()[0]
            conditions = {clause.left.name: clause.right.value for clause in statement._where_criteria}
            self.operations.append(("select", table.name, dict(conditions)))
            row = next((row for row in self.rows.get(table.name, {}).values()
                        if all(row.get(name) == value for name, value in conditions.items())), None)
            return MappingResult(copy.deepcopy(row))
        raise AssertionError("Unexpected non-insert/select statement in the offline storage fixture.")


class GroupRecordingEngine:
    def __init__(self, connection: GroupRecordingConnection) -> None:
        self.connection = connection
        self.begin_calls = 0
        self.commits = 0
        self.rollbacks = 0
        self.disposals = 0

    @contextmanager
    def begin(self):
        self.begin_calls += 1
        before = copy.deepcopy(self.connection.rows)
        try:
            yield self.connection
        except Exception:
            self.connection.rows = before
            self.rollbacks += 1
            raise
        else:
            self.commits += 1

    def dispose(self) -> None:
        self.disposals += 1


class GroupedStorageTests(unittest.TestCase):
    def setUp(self) -> None:
        # Reuse only its synthetic file setup; no confirmation method is called.
        self.fixture = delivery_fixture.DeliveryGateTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def prepare(self):
        return service.prepare_storage_load(
            manifest=self.fixture.manifest, schema_path=self.fixture.schema_path,
            artifact_path=self.fixture.artifact_path, insurer_code="cover_more",
        )

    def refresh_receipt(self) -> None:
        group = resolve_document_groups(
            self.fixture.manifest, [self.fixture.primary], input_root=self.fixture.input_root,
        )[0]
        self.fixture.group = group
        self.fixture.artifact.update(source_path=str(group.primary_path), **group.provenance())
        self.fixture.write_artifact()

    def move_secondary(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(self.fixture.amendment.read_bytes())
        self.fixture.artifact["source_documents"][1] = str(path)
        self.fixture.artifact["source_document_refs"][1]["source_path"] = str(path)
        self.fixture.write_artifact()
        inventory = copy.deepcopy(list(self.fixture.manifest.documents.inventory))
        inventory[1]["source_path"] = str(path)
        self.fixture.manifest = replace(
            self.fixture.manifest,
            documents=replace(self.fixture.manifest.documents, inventory=tuple(inventory)),
        )

    def write(self, connection, prepared=None):
        prepared = prepared or self.prepare()
        extension = compile_vertical_storage_metadata(prepared.schema_payload)
        return write_prepared_load(connection, prepared, extension.table)

    def load(self):
        return service.load_extraction_artifact(
            database_url="postgresql+psycopg:///offline_fixture", manifest=self.fixture.manifest,
            schema_path=self.fixture.schema_path, artifact_path=self.fixture.artifact_path,
            insurer_code="cover_more",
        )

    def test_prepared_source_document_has_content_identity_and_metadata(self) -> None:
        digest = sha256(self.fixture.amendment.read_bytes()).hexdigest()
        document = service.PreparedSourceDocument(
            document_id=f"sha256:{digest}", pdf_sha256=digest, document_type="spds",
            document_title="Amendment", source_path=str(self.fixture.amendment),
            metadata={"document_alias": "B"},
        )
        self.assertEqual(document.document_id, f"sha256:{digest}")
        self.assertEqual(document.metadata, {"document_alias": "B"})

    def test_prepare_preserves_all_sources_receipts_and_primary_document_identity(self) -> None:
        prepared = self.prepare()
        expected_ids = tuple(f"sha256:{reference['sha256']}" for reference in self.fixture.artifact["source_document_refs"])
        self.assertEqual(tuple(source.document_id for source in prepared.source_documents), expected_ids)
        self.assertEqual(tuple(source.source_path for source in prepared.source_documents),
                         (str(self.fixture.primary), str(self.fixture.amendment)))
        self.assertEqual(tuple(source.document_type for source in prepared.source_documents), ("pds", "spds"))
        self.assertEqual(prepared.document_id, expected_ids[0])
        self.assertEqual(prepared.source_document_refs, tuple(self.fixture.artifact["source_document_refs"]))
        self.assertEqual(prepared.document_group_id, self.fixture.group.group_id)
        self.assertIsInstance(prepared.release_source_id, str)
        self.assertNotEqual(prepared.release_source_id, prepared.document_id)

    def test_single_pdf_keeps_its_existing_release_source_identity(self) -> None:
        self.fixture.manifest = replace(
            self.fixture.manifest,
            documents=replace(self.fixture.manifest.documents, inventory=()),
        )
        for name in ("source_documents", "source_document_refs", "document_group_id"):
            self.fixture.artifact.pop(name)
        self.fixture.write_artifact()
        prepared = self.prepare()
        self.assertEqual(prepared.release_source_id, prepared.document_id)
        product_id = deterministic_product_id(prepared.vertical, prepared.insurer_code, prepared.plan.products[0].product_name)
        summary = self.write(GroupRecordingConnection(), prepared)
        self.assertEqual(summary.release_ids, (deterministic_release_id(product_id, prepared.document_id),))

    def test_repeated_group_load_is_idempotent(self) -> None:
        first = self.prepare()
        second = self.prepare()
        self.assertEqual(first, second)
        connection = GroupRecordingConnection()
        original_summary = self.write(connection, first)
        rows = copy.deepcopy(connection.rows)
        repeated_summary = self.write(connection, second)
        self.assertEqual(original_summary.release_ids, repeated_summary.release_ids)
        self.assertEqual(connection.rows, rows)
        self.assertEqual(len(connection.rows["documents"]), 2)
        self.assertEqual(len(connection.rows["product_release_documents"]), 2)

    def test_changing_only_spds_creates_a_distinct_release_with_the_same_primary_pdf(self) -> None:
        first = self.prepare()
        connection = GroupRecordingConnection()
        original = self.write(connection, first)
        self.fixture.amendment.write_bytes(b"%PDF-1.4\nRevised synthetic SPDS\n%%EOF\n")
        self.refresh_receipt()
        second = self.prepare()
        updated = self.write(connection, second)
        self.assertEqual(first.document_id, second.document_id)
        self.assertNotEqual(original.release_ids, updated.release_ids)
        self.assertNotEqual(first.release_source_id, second.release_source_id)
        self.assertEqual(len(connection.rows["product_releases"]), 2)

    def test_sql_persists_every_document_and_links_each_product_release_to_every_source(self) -> None:
        product = copy.deepcopy(self.fixture.artifact["data"]["products"][0])
        product["product_name"] = "Separate fixture plan"
        self.fixture.artifact["data"]["products"].append(product)
        self.fixture.write_artifact()
        prepared = self.prepare()
        connection = GroupRecordingConnection()
        summary = self.write(connection, prepared)
        expected_docs = {f"sha256:{reference['sha256']}" for reference in self.fixture.artifact["source_document_refs"]}
        documents = list(connection.rows["documents"].values())
        self.assertEqual({row["document_id"] for row in documents}, expected_docs)
        self.assertEqual({row["source_path"] for row in documents}, {str(self.fixture.primary), str(self.fixture.amendment)})
        links = list(connection.rows["product_release_documents"].values())
        self.assertEqual(len(links), 4)
        self.assertEqual({(row["release_id"], row["document_id"]) for row in links},
                         {(release_id, document_id) for release_id in summary.release_ids for document_id in expected_docs})
        for document in prepared.source_documents:
            row = next(row for row in documents if row["document_id"] == document.document_id)
            self.assertEqual(row["metadata_json"], dict(document.metadata))
        table_order = [name for kind, name, _ in connection.operations if kind == "insert"]
        self.assertLess(table_order.index("raw_extractions"), table_order.index("products"))

    def test_missing_secondary_pdf_is_rejected(self) -> None:
        self.fixture.amendment.unlink()
        with self.assertRaises((ValueError, FileNotFoundError)):
            self.prepare()

    def test_secondary_pdf_changes_are_rejected_until_extraction_receipt_is_regenerated(self) -> None:
        self.fixture.amendment.write_bytes(b"%PDF-1.4\nUnrecorded SPDS change\n%%EOF\n")
        with self.assertRaises(ValueError):
            self.prepare()

    def test_incorrect_secondary_hash_is_rejected_even_with_self_consistent_group_id(self) -> None:
        self.fixture.artifact["source_document_refs"][1]["sha256"] = "0" * 64
        self.fixture.artifact["document_group_id"] = document_group_id(self.fixture.artifact["source_document_refs"])
        self.fixture.write_artifact()
        with self.assertRaises(ValueError):
            self.prepare()

    def test_secondary_pdf_cannot_cross_the_input_root(self) -> None:
        self.move_secondary(self.fixture.root / "outside.pdf")
        with self.assertRaises(ValueError):
            self.prepare()

    def test_secondary_pdf_cannot_cross_insurers(self) -> None:
        self.move_secondary(self.fixture.input_root / "tick/spds/other_insurer.pdf")
        with self.assertRaises(ValueError):
            self.prepare()

    def test_secondary_source_must_have_a_pdf_signature(self) -> None:
        self.fixture.amendment.write_bytes(b"not a PDF")
        self.refresh_receipt()
        with self.assertRaises(ValueError):
            self.prepare()

    def test_manifest_amendments_cannot_be_silently_omitted_from_storage_receipt(self) -> None:
        self.fixture.artifact["source_documents"] = self.fixture.artifact["source_documents"][:1]
        self.fixture.artifact["source_document_refs"] = self.fixture.artifact["source_document_refs"][:1]
        self.fixture.artifact["document_group_id"] = document_group_id(self.fixture.artifact["source_document_refs"])
        self.fixture.write_artifact()
        with self.assertRaises(ValueError):
            self.prepare()

    def test_enveloped_and_native_group_sources_prepare_equivalent_loads(self) -> None:
        native = self.prepare()
        envelope = build_success_artifact(
            artifact_type="extraction_result", contract_version="1.0.0", data=self.fixture.artifact["data"],
            data_contract_schema=compile_canonical_extraction_contract(self.fixture.schema),
            provenance={
                "vertical": self.fixture.manifest.vertical, "schema_version": self.fixture.schema["version"],
                "run_id": "synthetic-group-run", "provider": "openai", "model": "gpt-5",
                "document_input": "markdown", "document_parser": "pdfingestor",
                **self.fixture.group.provenance(), "source_artifacts": [],
            },
        )
        self.fixture.artifact_path.write_text(json.dumps(envelope), encoding="utf-8")
        prepared = self.prepare()
        self.assertEqual(native.source_documents, prepared.source_documents)
        self.assertEqual(native.source_document_refs, prepared.source_document_refs)
        self.assertEqual(native.release_source_id, prepared.release_source_id)

    def test_group_load_uses_one_transaction_for_all_document_links(self) -> None:
        connection = GroupRecordingConnection()
        engine = GroupRecordingEngine(connection)
        with mock.patch("src.storage.service.create_engine", return_value=engine):
            summary = self.load()
        self.assertEqual(engine.begin_calls, 1)
        self.assertEqual(engine.commits, 1)
        self.assertEqual(engine.rollbacks, 0)
        self.assertEqual(engine.disposals, 1)
        self.assertEqual(summary.products_loaded, 1)
        self.assertEqual(len(connection.rows["documents"]), 2)
        self.assertEqual(len(connection.rows["product_release_documents"]), 2)

    def test_second_source_link_failure_rolls_back_all_group_rows(self) -> None:
        connection = GroupRecordingConnection(fail_second_document_link=True)
        engine = GroupRecordingEngine(connection)
        with mock.patch("src.storage.service.create_engine", return_value=engine):
            with self.assertRaisesRegex(RuntimeError, "synthetic second source link failure"):
                self.load()
        self.assertEqual(engine.begin_calls, 1)
        self.assertEqual(engine.commits, 0)
        self.assertEqual(engine.rollbacks, 1)
        self.assertEqual(engine.disposals, 1)
        self.assertEqual(connection.rows, {})

    def test_secondary_validation_stops_before_an_engine_is_created(self) -> None:
        self.fixture.amendment.write_bytes(b"%PDF-1.4\nUnrecorded SPDS change\n%%EOF\n")
        engine = GroupRecordingEngine(GroupRecordingConnection())
        with mock.patch("src.storage.service.create_engine", return_value=engine) as builder:
            with self.assertRaises(ValueError):
                self.load()
        builder.assert_not_called()


if __name__ == "__main__":
    unittest.main()
