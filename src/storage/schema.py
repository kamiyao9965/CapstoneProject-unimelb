"""Canonical relational schema shared by storage-enabled verticals."""

from __future__ import annotations

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Engine


storage_metadata = MetaData()
flexible_json = JSON().with_variant(JSONB(), "postgresql")


verticals = Table(
    "verticals",
    storage_metadata,
    Column("code", String(64), primary_key=True),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

insurers = Table(
    "insurers",
    storage_metadata,
    Column("code", String(64), primary_key=True),
    Column("display_name", Text),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

documents = Table(
    "documents",
    storage_metadata,
    Column("document_id", String(71), primary_key=True),
    Column("insurer_code", String(64), ForeignKey("insurers.code"), nullable=False),
    Column("sha256", String(64), nullable=False, unique=True),
    Column("document_type", String(32)),
    Column("title", Text),
    Column("source_path", Text, nullable=False),
    Column("source_url", Text),
    Column("effective_from", Date),
    Column("metadata_json", flexible_json, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

schema_versions = Table(
    "schema_versions",
    storage_metadata,
    Column("schema_version_id", String(71), primary_key=True),
    Column("vertical_code", String(64), ForeignKey("verticals.code"), nullable=False),
    Column("version", String(128), nullable=False),
    Column("schema_payload", flexible_json, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    UniqueConstraint("vertical_code", "version", name="uq_schema_vertical_version"),
)

extraction_runs = Table(
    "extraction_runs",
    storage_metadata,
    Column("run_id", String(128), primary_key=True),
    Column("document_id", String(71), ForeignKey("documents.document_id"), nullable=False),
    Column(
        "schema_version_id",
        String(71),
        ForeignKey("schema_versions.schema_version_id"),
        nullable=False,
    ),
    Column("provider", String(64), nullable=False),
    Column("model", String(128), nullable=False),
    Column("status", String(32), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

raw_extractions = Table(
    "raw_extractions",
    storage_metadata,
    Column(
        "run_id",
        String(128),
        ForeignKey("extraction_runs.run_id"),
        primary_key=True,
    ),
    Column("artifact", flexible_json, nullable=False),
    Column("payload_sha256", String(64), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
)

products = Table(
    "products",
    storage_metadata,
    Column("product_id", String(71), primary_key=True),
    Column("vertical_code", String(64), ForeignKey("verticals.code"), nullable=False),
    Column("insurer_code", String(64), ForeignKey("insurers.code"), nullable=False),
    Column("canonical_name", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    UniqueConstraint(
        "vertical_code",
        "insurer_code",
        "canonical_name",
        name="uq_product_vertical_insurer_name",
    ),
)

product_releases = Table(
    "product_releases",
    storage_metadata,
    Column("release_id", String(71), primary_key=True),
    Column("product_id", String(71), ForeignKey("products.product_id"), nullable=False),
    Column("document_id", String(71), ForeignKey("documents.document_id"), nullable=False),
    Column(
        "schema_version_id",
        String(71),
        ForeignKey("schema_versions.schema_version_id"),
        nullable=False,
    ),
    Column("source_product_type", String(64), nullable=False),
    Column("effective_from", Date),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    UniqueConstraint("product_id", "document_id", name="uq_release_product_document"),
)

product_release_documents = Table(
    "product_release_documents",
    storage_metadata,
    Column("release_id", String(71), primary_key=True),
    Column("document_id", String(71), primary_key=True),
    Column("document_role", String(32), nullable=False),
    ForeignKeyConstraint(["release_id"], ["product_releases.release_id"]),
    ForeignKeyConstraint(["document_id"], ["documents.document_id"]),
)

travel_product_details = Table(
    "travel_product_details",
    storage_metadata,
    Column(
        "release_id",
        String(71),
        ForeignKey("product_releases.release_id"),
        primary_key=True,
    ),
    Column("geographic_scope", String(64)),
    Column("trip_frequency", String(64)),
    Column("plan_tier", String(64)),
    Column("customer_segment", String(64)),
    Column("trip_style", String(64)),
    Column("cruise_cover_available", Boolean),
    Column("attributes", flexible_json, nullable=False),
)


def create_storage_schema(engine: Engine) -> None:
    """Create missing storage tables without modifying or deleting existing data."""
    storage_metadata.create_all(engine, checkfirst=True)
