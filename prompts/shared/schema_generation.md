Generate the fixed schema descriptor defined by the response contract. Insurance
field names are dynamic STRING VALUES in name, not new keys in the response.
The application mechanically compiles this descriptor to the native schema.

For discovery include vertical, version, description, product_types, taxonomies,
notes, fields, validation_profile, document_fields and schema_nodes. For patches
include patches and schema_nodes. Do not return native item_schema, item_fields,
$defs, JSON-schema properties maps or arbitrary extra keys.

Each field/patch uses item_node_id: a declared object node for list[object], or
the empty string for other field types. Describe nested values in schema_nodes.
Every node has node_id, type, nullable, description, properties, items_node_id,
enum_values, minimum, maximum, min_length and max_length. Node IDs must be unique
identifiers beginning with a letter. No missing, circular or unused nodes.

An object node declares properties as an array of {name, node_id} entries. All
its keys are required; use a nullable child node for unknown values. An array
node declares items_node_id. Other nodes have properties=[] and items_node_id="".
Use type string/number/integer/boolean/null for scalar nodes. enum_values contains
only allowed values of that scalar type; nullability is specified by nullable.
Numerical bounds apply only to number/integer. String lengths apply only to
string. Unused bounds are null; unused enums are []. Never invent a business
constraint merely to fill a descriptor slot.

document_fields is an array of {name, node_id} entries for explicitly defined
document-level output, or [] when there is none. validation_profile is the
configured business profile, or "" when none is configured. Do not invent a
profile ID. Nested reuse uses node IDs instead of recursive transport schemas.
Names and meanings must be supported by the supplied documents. Preserving an
unknown value is preferable to guessing it. Successful structural validation is
not human approval of a schema.
