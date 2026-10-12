"""Encode explicit synthetic schemas at the fake provider wire boundary."""

from copy import deepcopy


def descriptor(schema):
    from src.schema.validation import normalize_schema
    schema = normalize_schema(schema)
    nodes = []

    def node(spec):
        from src.schema.nested import resolve_local_refs
        spec = resolve_local_refs(spec, schema.get("$defs"))
        node_id = f"node_{len(nodes)}"
        kind = spec["type"]
        nullable = isinstance(kind, list) and "null" in kind
        if isinstance(kind, list):
            kind = next(value for value in kind if value != "null")
        entry = dict(node_id=node_id, type=kind, nullable=nullable, description=spec.get("description", ""),
                     properties=[], items_node_id="", enum_values=[value for value in spec.get("enum", []) if value is not None],
                     minimum=spec.get("minimum"), maximum=spec.get("maximum"),
                     min_length=spec.get("minLength"), max_length=spec.get("maxLength"))
        nodes.append(entry)
        if kind == "object":
            entry["properties"] = [{"name": name, "node_id": node(value)} for name, value in spec["properties"].items()]
        if kind == "array":
            entry["items_node_id"] = node(spec["items"])
        return node_id

    wire = {key: deepcopy(schema[key]) for key in ("vertical", "version", "description", "product_types", "taxonomies", "notes")}
    wire["fields"] = [{**{key: deepcopy(field[key]) for key in ("name", "type", "description", "applies_to", "required", "values")},
                       "item_node_id": node(field["item_schema"]) if field.get("item_schema") else ""} for field in schema["fields"]]
    wire["validation_profile"] = schema.get("validation_profile", "")
    wire["document_fields"] = [{"name": name, "node_id": node(value)} for name, value in schema.get("document_fields", {}).items()]
    wire["schema_nodes"] = nodes
    return wire


def patch_descriptor(payload):
    return {"patches": [{**deepcopy(patch), "item_node_id": ""} for patch in payload["patches"]], "schema_nodes": []}
