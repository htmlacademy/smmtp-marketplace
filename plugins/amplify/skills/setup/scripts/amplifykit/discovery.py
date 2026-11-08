from __future__ import annotations

from . import settings, transport
from .errors import Fault
from .platforms import CONTRACT_VERSION, choose_form, environment_name

PATH = "/v1/discover"
TIMEOUT_SECONDS = 70


def secret_schemas(capabilities):
    try:
        return {
            entry["id"]: entry["operationSchemas"]["discover"]["credential"]
            for entry in capabilities.get("result", {}).get("strategies", [])
        }
    except (AttributeError, KeyError, TypeError):
        raise transport.unexpected_shape("GET", "/v1/capabilities")


def build_body(strategy, values, address=None, cursor=None):
    destination = {"id": strategy, "strategy": strategy, "credentialRef": strategy}
    if address is not None:
        destination["address"] = address
    if cursor is not None:
        destination["cursor"] = cursor
    return {
        "input": {"contractVersion": CONTRACT_VERSION, "destinations": [destination]},
        "credentials": [{"ref": strategy, "strategy": strategy, "values": values}],
    }


def next_step(outcome):
    if outcome.get("mode") == "address_required":
        return "the platform cannot list its destinations: run again with --address naming one"
    if outcome.get("nextCursor"):
        return "more destinations follow: run again with --cursor set to next_cursor"
    return None


def summarise(strategy, outcome):
    report = {
        "ok": outcome["state"] == "read",
        "strategy": strategy,
        "state": outcome["state"],
        "mode": outcome.get("mode"),
        "candidates": [
            {
                "variables": {
                    environment_name(strategy, field): value
                    for field, value in candidate["destinationValues"].items()
                },
                "label": candidate.get("label"),
                "handle": candidate.get("handle"),
                "kind": candidate.get("kind"),
                "publish": candidate.get("publish"),
            }
            for candidate in outcome.get("candidates", [])
        ],
        "next_cursor": outcome.get("nextCursor"),
        "next": next_step(outcome),
    }
    errors = [
        {"code": problem.get("code"), "retry": problem.get("retry"), "details": problem.get("details", {})}
        for problem in outcome.get("errors", [])
    ]
    if errors:
        report["errors"] = errors
    if outcome.get("warnings"):
        report["warnings"] = [
            {"code": warning.get("code"), "details": warning.get("details", {})}
            for warning in outcome["warnings"]
        ]
    return report


def discover(strategy, address, cursor, environ, cwd):
    _, _, lookup, cafile = settings.load(environ, cwd)
    connection = transport.opener(cafile)
    url, token = transport.service(lookup)
    schemas = secret_schemas(
        transport.call(
            url, token, "GET", "/v1/capabilities", opener=connection,
            timeout=transport.CHECK_TIMEOUT_SECONDS,
        )
    )
    supported = sorted(name for name, schema in schemas.items() if schema)
    if strategy not in schemas:
        raise Fault(
            "strategy_unknown",
            f"not a platform ID the service offers: {strategy}; use one of error.available,"
            " nothing was sent",
            available=sorted(schemas),
        )
    if not schemas[strategy]:
        raise Fault(
            "discover_unsupported",
            f"{strategy} cannot look up its destinations through the service; the operator"
            " finds the value as that platform's setup describes, nothing was sent",
            supported=supported,
        )
    try:
        _, values, absent = choose_form(strategy, schemas[strategy], "post", lookup)
    except (AttributeError, KeyError, TypeError):
        raise transport.unexpected_shape("GET", "/v1/capabilities")
    if absent:
        raise Fault(
            "credentials_missing",
            "the platform's secret is not set, nothing was sent; the operator sets the"
            " variables in error.missing first",
            missing=absent,
        )
    answer = transport.call(
        url, token, "POST", PATH,
        transport.json_payload(build_body(strategy, values, address, cursor)),
        opener=connection, timeout=TIMEOUT_SECONDS,
    )
    try:
        return summarise(strategy, answer["result"]["outcomes"][0])
    except (AttributeError, IndexError, KeyError, TypeError):
        raise transport.unexpected_shape("POST", PATH)
