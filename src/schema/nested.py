"""Small closed, inline JSON Schema dialect for nested extraction objects.

No remote refs or arbitrary schema keywords; legacy descriptions remain valid.
"""
from jsonschema import Draft202012Validator
from copy import deepcopy


def resolve_local_refs(schema, definitions):
    """Resolve only named local definitions; never fetch URLs or allow cycles."""
    if not isinstance(definitions, dict):
        raise ValueError('Local schema definitions must be an object')
    budget = [20000]

    def expand(node, stack=(), depth=0):
        budget[0] -= 1
        if budget[0] < 0 or depth > 32:
            raise ValueError('Nested schema reference expansion is too large/deep')
        if isinstance(node, list):
            return [expand(item, stack, depth + 1) for item in node]
        if not isinstance(node, dict):
            return node
        if '$ref' in node:
            ref = node['$ref']
            if set(node) != {'$ref'} or not isinstance(ref, str) or not ref.startswith('#/$defs/'):
                raise ValueError('Only standalone local #/$defs/name references are allowed')
            name = ref.removeprefix('#/$defs/')
            if '/' in name or '~' in name or name not in definitions or name in stack:
                raise ValueError('Missing, invalid or cyclic local schema reference')
            return expand(definitions[name], (*stack, name), depth + 1)
        return {key: expand(value, stack, depth + 1) for key, value in node.items()}

    return expand(deepcopy(schema))


def validate_item_schema(schema, definitions=None):
    schema = resolve_local_refs(schema, definitions or {})
    allowed={'type','properties','required','additionalProperties','items','enum','minimum','maximum','minLength','description'}
    def walk(node,depth=0):
        if depth>16 or not isinstance(node,dict) or set(node)-allowed:
            raise ValueError('Unsupported nested schema shape/keyword')
        types=node.get('type')
        types=types if isinstance(types,list) else [types]
        if not types or any(t not in {'object','array','string','number','integer','boolean','null'} for t in types):
            raise ValueError('Nested schemas require explicit JSON types')
        if 'object' in types:
            props=node.get('properties')
            if not isinstance(props,dict) or node.get('additionalProperties') is not False or set(node.get('required',[]))!=set(props):
                raise ValueError('Nested objects must declare and require every key; use nullable values for unknowns')
            for value in props.values(): walk(value,depth+1)
        if 'array' in types:
            walk(node.get('items'),depth+1)
    Draft202012Validator.check_schema(schema)
    if schema.get('type')!='object':
        raise ValueError('item_schema must describe an object')
    walk(schema)
