"""Compile approved Canonical Schemas into storage metadata and load plans."""

from __future__ import annotations

import copy
import hashlib
from collections.abc import Mapping
from dataclasses import dataclass

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    ForeignKey,
    MetaData,
    Numeric,
    String,
    Table,
    Text,
)
from sqlalchemy.engine import Engine

from src.common.json_contracts import validate_inline_contract
from src.schema.canonical import (
    compile_canonical_extraction_contract,
    require_approved_canonical_schema,
)
from src.storage.schema import flexible_json


@dataclass(frozen=True)
class CompiledVerticalStorage:
    """One generated extension table plus a safe idempotent create operation."""

    table: Table

    def create(self, engine: Engine) -> None:
        self.table.create(engine, checkfirst=True)


@dataclass(frozen=True)
class CanonicalProductLoad:
    """Business values ready for repository-owned IDs and foreign keys."""

    product_name: str
    product_type: str
    core_values: dict[str, object]
    extension_values: dict[str, object]
    attributes: dict[str, object]


@dataclass(frozen=True)
class CanonicalLoadPlan:
    """Deterministic, side-effect-free output of Canonical Schema mapping."""

    vertical: str
    schema_version: str
    extension_table: str
    products: tuple[CanonicalProductLoad, ...]


def compile_vertical_storage_metadata(
    payload: object,
) -> CompiledVerticalStorage:
    """Compile an approved schema into one product-release extension table."""
    schema = require_approved_canonical_schema(payload)
    extension = _mapping(schema["extension"], "extension")
    table_name = str(extension["table"])
    attributes_column = str(extension["attributes_column"])

    metadata = MetaData()
    Table(
        "product_releases",
        metadata,
        Column("release_id", String(71), primary_key=True),
        info={"canonical_reference_stub": True},
    )
    columns = [
        Column(
            "release_id",
            String(71),
            ForeignKey("product_releases.release_id"),
            primary_key=True,
        )
    ]
    enum_constraints: list[tuple[str, list[str]]] = []
    fields = schema["fields"]
    if not isinstance(fields, list):
        raise ValueError("Canonical Schema fields must be a list.")
    for field_value in fields:
        field = _mapping(field_value, "field")
        storage = _mapping(field["storage"], f"field {field['name']} storage")
        if storage["strategy"] != "extension_column":
            continue
        column_name = str(storage["column"])
        columns.append(
            Column(
                column_name,
                _sql_type(str(field["type"])),
                nullable=field["nullable"] is True,
            )
        )
        if field["type"] == "enum":
            enum_constraints.append((column_name, list(field["values"])))

    columns.append(Column(attributes_column, flexible_json, nullable=False))
    table = Table(table_name, metadata, *columns)
    for column_name, values in enum_constraints:
        table.append_constraint(
            CheckConstraint(
                table.c[column_name].in_(values),
                name=_enum_constraint_name(table_name, column_name),
            )
        )
    return CompiledVerticalStorage(table=table)


def compile_canonical_load_plan(
    schema_payload: object,
    extraction_payload: object,
) -> CanonicalLoadPlan:
    """Validate extraction JSON and mechanically split it into storage values."""
    schema = require_approved_canonical_schema(schema_payload)
    extraction_contract = compile_canonical_extraction_contract(schema)
    validate_inline_contract(
        extraction_payload,
        extraction_contract,
        name="canonical_extraction",
    )
    if not isinstance(extraction_payload, Mapping):
        raise ValueError("Canonical extraction payload must be an object.")

    output = _mapping(schema["output"], "output")
    extracted_products = extraction_payload[str(output["collection"])]
    if output["cardinality"] == "multiple":
        if not isinstance(extracted_products, list):
            raise ValueError("Canonical multiple output must contain a product list.")
        product_payloads = extracted_products
    else:
        product_payloads = [extracted_products]

    identity = _mapping(schema["identity"], "identity")
    product_name_field = str(identity["product_name_field"])
    product_type_field = str(identity["product_type_field"])
    fields = schema["fields"]
    if not isinstance(fields, list):
        raise ValueError("Canonical Schema fields must be a list.")

    records: list[CanonicalProductLoad] = []
    seen_identities: set[str] = set()
    for product_payload in product_payloads:
        product = _mapping(product_payload, "extracted product")
        product_name = product[product_name_field]
        product_type = product[product_type_field]
        if not isinstance(product_name, str) or not product_name.strip():
            raise ValueError("Canonical product-name identity must be non-empty.")
        if not isinstance(product_type, str) or not product_type.strip():
            raise ValueError("Canonical product-type identity must be non-empty.")
        identity_key = product_name.strip().casefold()
        if identity_key in seen_identities:
            raise ValueError(
                f"Canonical extraction contains duplicate product identity: {product_name!r}."
            )
        seen_identities.add(identity_key)

        core_values: dict[str, object] = {}
        extension_values: dict[str, object] = {}
        attributes: dict[str, object] = {}
        for field_value in fields:
            field = _mapping(field_value, "field")
            name = str(field["name"])
            value = copy.deepcopy(product.get(name))
            storage = _mapping(field["storage"], f"field {name} storage")
            strategy = storage["strategy"]
            if strategy == "core_column":
                core_values[str(storage["target"])] = value
            elif strategy == "extension_column":
                extension_values[str(storage["column"])] = value
            else:
                attributes[name] = value

        records.append(
            CanonicalProductLoad(
                product_name=product_name,
                product_type=product_type,
                core_values=core_values,
                extension_values=extension_values,
                attributes=attributes,
            )
        )

    extension = _mapping(schema["extension"], "extension")
    return CanonicalLoadPlan(
        vertical=str(schema["vertical"]),
        schema_version=str(schema["version"]),
        extension_table=str(extension["table"]),
        products=tuple(records),
    )


def _sql_type(field_type: str) -> object:
    if field_type in {"string", "enum"}:
        return Text()
    if field_type == "number":
        return Numeric()
    if field_type == "boolean":
        return Boolean()
    raise ValueError(
        f"Canonical field type {field_type!r} cannot be an extension column."
    )


def _enum_constraint_name(table_name: str, column_name: str) -> str:
    digest = hashlib.sha256(f"{table_name}.{column_name}".encode("utf-8")).hexdigest()[:10]
    return f"ck_{table_name[:40]}_{digest}"


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"Canonical {label} must be an object.")
    return value
