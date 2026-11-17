from __future__ import annotations

import re

from . import transport
from .settings import SUBJECT

CONTRACT_VERSION = "amplify-semantic/1"
CONTENT_FORMAT = "gsx/1"
PLACEMENTS = ("post", "reel", "story")
TELEGRAM_FORMATS = ("regular", "rich")
FLOW_STEPS = {"fields", "target", "redirect"}


def environment_name(strategy, field):
    return f"{strategy.upper().replace('-', '_')}_{re.sub(r'(?<!^)(?=[A-Z])', '_', field).upper()}"


def connections(entry):
    try:
        return entry["capabilities"]["connections"]
    except (KeyError, TypeError):
        raise transport.unexpected_shape("GET", "/v1/capabilities")


def publishing_forms(entry, placement):
    return [
        form for form in connections(entry)
        if placement in ((form.get("publish") or {}).get("placements") or [])
    ]


def connectable(form):
    steps = form.get("steps") or []
    return bool(steps) and set(steps) <= FLOW_STEPS and form.get("available") is True


def field_variables(strategy, form):
    if (form.get("steps") or [None])[0] != "fields":
        return {}
    return {field: environment_name(strategy, field) for field in form.get("secret", [])}


def field_schema(credential_schema, field):
    forms = credential_schema.get("anyOf") or [credential_schema]
    definitions = [
        definition if isinstance(definition, dict) else {}
        for form in forms
        for name, definition in form.get("properties", {}).items()
        if name == field
    ]
    return definitions[0] if len(definitions) == 1 else {"anyOf": definitions}


def schema_problem(value, schema):
    if isinstance(schema.get("anyOf"), list):
        found = [schema_problem(value, option) for option in schema["anyOf"] if isinstance(option, dict)]
        return None if not found or None in found else " or ".join(dict.fromkeys(found))
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


def connection_fields(strategies):
    try:
        return {
            name: (strategy, field_schema(entry["credentialSchema"], field))
            for strategy, entry in strategies.items()
            for form in connections(entry)
            for field, name in field_variables(strategy, form).items()
        }
    except (AttributeError, KeyError, TypeError):
        raise transport.unexpected_shape("GET", "/v1/capabilities")


def platform_problem(name, value, fields):
    if name not in fields:
        prefix = name.split("_", 1)[0]
        known = sorted(known for known in fields if known.split("_", 1)[0] == prefix)
        hint = f"; that platform's variables are {', '.join(known)}" if known else ""
        return f"{name} is not a variable of any platform the service connects{hint}"
    problem = schema_problem(value, fields[name][1])
    return f"{name} {problem} for {fields[name][0]}" if problem else None


def build_body(operation, markdown, targets, channels, media, placement="post", telegram_format="regular"):
    destinations = []
    for strategy in targets:
        destination = {"id": strategy}
        if operation == "analyze":
            destination["strategy"] = strategy
        else:
            destination["channelId"] = channels[strategy]
        destination["options"] = (
            {"messageFormat": telegram_format}
            if strategy == "telegram" and telegram_format != "regular"
            else {}
        )
        destinations.append(destination)
    body = {
        "input": {
            "contractVersion": CONTRACT_VERSION,
            "placement": placement,
            "destinations": destinations,
        }
    }

    if operation != "verify":
        body["input"]["content"] = {
            "format": CONTENT_FORMAT,
            "markdown": markdown,
            "media": [{"id": item["id"], "kind": item["kind"]} for item in media],
        }
    if operation != "analyze":
        body = {"subject": SUBJECT, **body}
    return body
