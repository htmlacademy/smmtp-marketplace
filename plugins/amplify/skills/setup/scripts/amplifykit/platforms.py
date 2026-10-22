"""Platform credentials: the variable each field lives in, the credential
form a placement takes, whether a value fits the field's schema, and the
request that carries them."""

from __future__ import annotations

import re

from . import transport

CONTRACT_VERSION = "amplify-semantic/1"
CONTENT_FORMAT = "amplify-markdown/1"
PLACEMENTS = ("post", "reel", "story")
TELEGRAM_FORMATS = ("regular", "rich")
# The service accepts a credential form holding one of these fields only for
# that placement, and blocks it for any other.
PLACEMENT_FIELDS = {"story": "businessConnectionId"}
# A credential form holding this field names a connection kept on the
# service's host, which no variable of this script supplies.
HOST_FORM_FIELD = "authMode"


def environment_name(strategy, field):
    return f"{strategy.upper()}_{re.sub(r'(?<!^)(?=[A-Z])', '_', field).upper()}"


def credential_forms(schema):
    """The credential forms of a strategy that variables can fill, in schema
    order."""
    forms = schema.get("anyOf") or [schema]
    return [form for form in forms if HOST_FORM_FIELD not in form.get("properties", {})]


def field_schema(schema, field):
    """The schema a field's value must meet: forms that share a field may
    constrain it differently, and a value any of them accepts is valid."""
    definitions = [
        definition if isinstance(definition, dict) else {}
        for form in credential_forms(schema)
        for name, definition in form.get("properties", {}).items()
        if name == field
    ]
    return definitions[0] if len(definitions) == 1 else {"anyOf": definitions}


def serves(form, placement):
    fields = form.get("properties", {})
    marker = PLACEMENT_FIELDS.get(placement)
    if marker:
        return marker in fields
    return not any(field in fields for field in PLACEMENT_FIELDS.values())


def placement_forms(schema, placement):
    """The credential forms a strategy accepts for placement, in schema order;
    every form when none is marked for it."""
    forms = credential_forms(schema)
    return [form for form in forms if serves(form, placement)] or forms


def choose_form(strategy, schema, placement, lookup):
    """The first form for placement whose required variables are all set,
    else the first form for placement: (form, values, unset required names)."""
    chosen = None
    for form in placement_forms(schema, placement):
        values, absent = {}, []
        for field in form.get("properties", {}):
            variable = environment_name(strategy, field)
            value = lookup(variable)
            if value:
                values[field] = value
            elif field in form.get("required", []):
                absent.append(variable)
        if not absent:
            return form, values, []
        chosen = chosen or (form, values, absent)
    return chosen


def complete_form(strategy, schema, lookup):
    """The first credential form, for any placement, whose required variables
    are all set; None when there is none."""
    for form in credential_forms(schema):
        required = form.get("required", [])
        if all(lookup(environment_name(strategy, field)) for field in required):
            return form
    return None


def resolve_credentials(targets, schemas, lookup, placement="post"):
    credentials = []
    missing = {}
    for strategy in targets:
        _, values, absent = choose_form(strategy, schemas[strategy], placement, lookup)
        if absent:
            missing[strategy] = absent
        else:
            credentials.append({"ref": strategy, "strategy": strategy, "values": values})
    return credentials, missing


def schema_problem(value, schema):
    """What makes value fail a credential field's JSON Schema, for the
    keywords a credential schema uses; None when it passes or the schema
    says nothing this can check."""
    if isinstance(schema.get("anyOf"), list):
        found = [schema_problem(value, option) for option in schema["anyOf"] if isinstance(option, dict)]
        return None if not found or None in found else " and ".join(dict.fromkeys(found))
    if "const" in schema and value != str(schema["const"]):
        return f"must be {schema['const']}"
    kinds = schema.get("type", [])
    kinds = [kinds] if isinstance(kinds, str) else kinds
    if "integer" in kinds and re.fullmatch(r"-?[0-9]+", value):
        return None
    if kinds and "string" not in kinds:
        return f"must be {' or '.join(kinds)}"
    if len(value) > schema.get("maxLength", len(value)):
        return f"is longer than {schema['maxLength']} characters"
    if len(value) < schema.get("minLength", 0):
        return f"is shorter than {schema['minLength']} characters"
    if "enum" in schema and value not in schema["enum"]:
        return f"must be one of {', '.join(map(str, schema['enum']))}"
    pattern = schema.get("pattern")
    try:
        if pattern and not re.search(pattern, value):
            return f"does not match the pattern {pattern}"
    except re.error:
        return None
    return None


def credential_fields(schemas):
    """Every platform variable the service knows: name -> (strategy, field schema)."""
    try:
        return {
            environment_name(strategy, field): (strategy, field_schema(schema, field))
            for strategy, schema in schemas.items()
            for form in credential_forms(schema)
            for field in form.get("properties", {})
        }
    except (AttributeError, TypeError):
        raise transport.unexpected_shape("GET", "/v1/capabilities")


def platform_problem(name, value, fields):
    if name not in fields:
        prefix = name.split("_", 1)[0]
        known = sorted(known for known in fields if known.split("_", 1)[0] == prefix)
        hint = f"; that platform's variables are {', '.join(known)}" if known else ""
        return f"{name} is not a variable of any platform the service offers{hint}"
    problem = schema_problem(value, fields[name][1])
    return f"{name} {problem} for {fields[name][0]}" if problem else None


def build_body(operation, markdown, targets, credentials, media, placement="post", telegram_format="regular"):
    destinations = [
        {
            "id": strategy,
            "strategy": strategy,
            "options": (
                {"messageFormat": telegram_format}
                if strategy == "telegram" and telegram_format != "regular"
                else {}
            ),
        }
        for strategy in targets
    ]
    body = {
        "input": {
            "contractVersion": CONTRACT_VERSION,
            "placement": placement,
            "destinations": destinations,
        }
    }
    # The service rejects fields it does not expect: verify takes no content,
    # and analyze takes no credentials.
    if operation != "verify":
        body["input"]["content"] = {
            "format": CONTENT_FORMAT,
            "markdown": markdown,
            "media": [{"id": item["id"], "kind": item["kind"]} for item in media],
        }
    if operation in ("publish", "verify"):
        for destination in destinations:
            destination["credentialRef"] = destination["id"]
        body["credentials"] = credentials
    return body
