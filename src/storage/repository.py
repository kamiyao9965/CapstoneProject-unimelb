"""Transactional PostgreSQL persistence for prepared canonical loads."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from sqlalchemy import Table, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import Connection

from src.storage.schema import (
    documents,
    extraction_runs,
    insurers,
    product_release_documents,
    product_releases,
    products,
    raw_extractions,
    schema_versions,
    verticals,
)

if TYPE_CHECKING:
    from src.storage.service import PreparedStorageLoad


@dataclass(frozen=True)
class StorageLoadSummary:
    """Stable CLI-facing result that does not expose database internals."""

    run_id: str
    document_id: str
    schema_version_id: str
    products_loaded: int
    release_ids: tuple[str, ...]


def deterministic_product_id(vertical: str, insurer: str, product_name: str) -> str:
    """Identify a product independent of display-name casing and whitespace."""
    identity = "\x1f".join(
        (vertical.strip(), insurer.strip(), product_name.strip().casefold())
    )
    return _content_id(identity)


def deterministic_release_id(product_id: str, document_id: str) -> str:
    """Identify the release represented by one product/document pairing."""
    return _content_id("\x1f".join((product_id, document_id)))


def write_prepared_load(
    connection: Connection,
    prepared: PreparedStorageLoad,
    extension_table: Table,
) -> StorageLoadSummary:
    """Write one load on a caller-owned PostgreSQL transaction."""
    _insert_ignore(connection, verticals, {"code": prepared.vertical})
    _insert_ignore(
        connection,
        insurers,
        {"code": prepared.insurer_code, "display_name": None},
    )
    _insert_and_verify(
        connection,
        documents,
        {
            "document_id": prepared.document_id,
            "insurer_code": prepared.insurer_code,
            "sha256": prepared.pdf_sha256,
            "document_type": prepared.document_type,
            "title": prepared.document_title,
            "source_path": prepared.source_path,
            "source_url": None,
            "effective_from": None,
            "metadata_json": {},
        },
        lookup={"document_id": prepared.document_id},
        immutable=("insurer_code", "sha256"),
        collision_label="document identity collision",
    )
    _insert_and_verify(
        connection,
        schema_versions,
        {
            "schema_version_id": prepared.schema_version_id,
            "vertical_code": prepared.vertical,
            "version": prepared.plan.schema_version,
            "schema_payload": prepared.schema_payload,
        },
        lookup={
            "vertical_code": prepared.vertical,
            "version": prepared.plan.schema_version,
        },
        immutable=("schema_version_id", "schema_payload"),
        collision_label="schema version collision",
    )
    _insert_and_verify(
        connection,
        extraction_runs,
        {
            "run_id": prepared.run_id,
            "document_id": prepared.document_id,
            "schema_version_id": prepared.schema_version_id,
            "provider": prepared.provider,
            "model": prepared.model,
            "status": "success",
        },
        lookup={"run_id": prepared.run_id},
        immutable=(
            "document_id",
            "schema_version_id",
            "provider",
            "model",
            "status",
        ),
        collision_label="run ID collision",
    )
    _insert_and_verify(
        connection,
        raw_extractions,
        {
            "run_id": prepared.run_id,
            "artifact": prepared.raw_artifact,
            "payload_sha256": prepared.payload_sha256,
        },
        lookup={"run_id": prepared.run_id},
        immutable=("artifact", "payload_sha256"),
        collision_label="run ID collision",
    )

    release_ids: list[str] = []
    for product in prepared.plan.products:
        canonical_name = product.product_name.strip()
        product_id = deterministic_product_id(
            prepared.vertical,
            prepared.insurer_code,
            canonical_name,
        )
        release_id = deterministic_release_id(product_id, prepared.document_id)
        _insert_and_verify(
            connection,
            products,
            {
                "product_id": product_id,
                "vertical_code": prepared.vertical,
                "insurer_code": prepared.insurer_code,
                "canonical_name": canonical_name,
            },
            lookup={"product_id": product_id},
            immutable=("vertical_code", "insurer_code"),
            collision_label="product identity collision",
        )
        release_values = {
            "release_id": release_id,
            "product_id": product_id,
            "document_id": prepared.document_id,
            "schema_version_id": prepared.schema_version_id,
            "source_product_type": product.product_type,
            "effective_from": None,
        }
        _upsert_and_verify(
            connection,
            product_releases,
            release_values,
            lookup={"release_id": release_id},
            update_columns=(
                "schema_version_id",
                "source_product_type",
                "effective_from",
            ),
            expected=(
                "product_id",
                "document_id",
                "schema_version_id",
                "source_product_type",
                "effective_from",
            ),
            collision_label="product release identity collision",
        )
        _insert_ignore(
            connection,
            product_release_documents,
            {
                "release_id": release_id,
                "document_id": prepared.document_id,
                "document_role": prepared.document_type,
            },
        )
        extension_config = prepared.schema_payload["extension"]
        assert isinstance(extension_config, dict)
        attributes_column = str(extension_config["attributes_column"])
        extension_values: dict[str, Any] = {
            "release_id": release_id,
            **product.extension_values,
            attributes_column: product.attributes,
        }
        update_values = {
            key: value for key, value in extension_values.items() if key != "release_id"
        }
        statement = insert(extension_table).values(**extension_values)
        connection.execute(
            statement.on_conflict_do_update(
                index_elements=[extension_table.c.release_id],
                set_=update_values,
            )
        )
        release_ids.append(release_id)

    return StorageLoadSummary(
        run_id=prepared.run_id,
        document_id=prepared.document_id,
        schema_version_id=prepared.schema_version_id,
        products_loaded=len(prepared.plan.products),
        release_ids=tuple(release_ids),
    )


def _insert_ignore(
    connection: Connection,
    table: Table,
    values: dict[str, Any],
) -> None:
    connection.execute(insert(table).values(**values).on_conflict_do_nothing())


def _insert_and_verify(
    connection: Connection,
    table: Table,
    values: dict[str, Any],
    *,
    lookup: dict[str, Any],
    immutable: tuple[str, ...],
    collision_label: str,
) -> None:
    _insert_ignore(connection, table, values)
    _verify_row(
        connection,
        table,
        values,
        lookup=lookup,
        expected=immutable,
        collision_label=collision_label,
    )


def _upsert_and_verify(
    connection: Connection,
    table: Table,
    values: dict[str, Any],
    *,
    lookup: dict[str, Any],
    update_columns: tuple[str, ...],
    expected: tuple[str, ...],
    collision_label: str,
) -> None:
    statement = insert(table).values(**values)
    connection.execute(
        statement.on_conflict_do_update(
            index_elements=[table.c[name] for name in lookup],
            set_={name: values[name] for name in update_columns},
        )
    )
    _verify_row(
        connection,
        table,
        values,
        lookup=lookup,
        expected=expected,
        collision_label=collision_label,
    )


def _verify_row(
    connection: Connection,
    table: Table,
    values: dict[str, Any],
    *,
    lookup: dict[str, Any],
    expected: tuple[str, ...],
    collision_label: str,
) -> None:
    predicate = [table.c[name] == value for name, value in lookup.items()]
    row = connection.execute(select(table).where(*predicate)).mappings().first()
    if row is None:
        raise RuntimeError(f"Could not verify {table.name} after insert.")
    for name in expected:
        if row[name] != values[name]:
            raise ValueError(f"{collision_label}: existing {table.name} row differs.")


def _content_id(value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"
