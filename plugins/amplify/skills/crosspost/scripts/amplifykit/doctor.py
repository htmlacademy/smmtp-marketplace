"""doctor: what is set up, where each value comes from, and what to do next,
without publishing anything."""

from __future__ import annotations

from . import settings, transport
from .configuration import git_state
from .errors import Fault
from .platforms import (
    build_body,
    choose_form,
    complete_form,
    environment_name,
    resolve_credentials,
    schema_problem,
)
from .settings import (
    GATEWAY_TOKEN_VARIABLE,
    GATEWAY_URL_VARIABLE,
    SECRET_COMMAND_VARIABLE,
    TOKEN_VARIABLE,
    URL_VARIABLE,
    https_origin_problem,
)


def sources(lookup, names):
    return {name: lookup.source(name) for name in names}


def media_gateway_report(lookup):
    variables = sources(lookup, (GATEWAY_URL_VARIABLE, GATEWAY_TOKEN_VARIABLE))
    found = sum(source is not None for source in variables.values())
    url = lookup(GATEWAY_URL_VARIABLE)
    state = ("none", "partial", "complete")[found]
    if state == "complete" and https_origin_problem(url):
        state = "invalid"
    return {"url": url, "state": state, "variables": variables}


def verify_entry(outcome):
    entry = {"state": outcome["state"]}
    if "identity" in outcome:
        entry["identity"] = outcome["identity"]
    errors = [
        {"code": problem.get("code"), "retry": problem.get("retry"), "details": problem.get("details", {})}
        for problem in outcome.get("errors", [])
    ]
    if errors:
        entry["errors"] = errors
    return entry


def check_strategies(targets, schemas, lookup, verify, placement="post"):
    """Every platform named in targets, with its credential form for
    placement, and every other platform with a complete credential form for
    any placement: the form's variables, the required ones missing, and the
    ones its schema rejects. verify(ready targets, credentials) returns the
    service's outcomes for the named platforms whose values are all present
    and valid."""
    try:
        configured = {}
        for strategy, schema in schemas.items():
            if strategy in targets:
                form, _, missing = choose_form(strategy, schema, placement, lookup)
            else:
                form, missing = complete_form(strategy, schema, lookup), []
            if form is not None:
                fields = form.get("properties", {})
                names = {field: environment_name(strategy, field) for field in fields}
                variables = sources(lookup, names.values())
                configured[strategy] = {
                    "variables": variables,
                    "missing": missing,
                    "invalid": [
                        name for field, name in names.items()
                        if variables[name] is not None
                        and isinstance(fields[field], dict)
                        and schema_problem(lookup(name), fields[field])
                    ],
                }
    except (AttributeError, KeyError, TypeError):
        raise transport.unexpected_shape("GET", "/v1/capabilities")

    section = {"available": sorted(schemas), "configured": configured}
    unknown = [strategy for strategy in targets if strategy not in schemas]
    if unknown:
        section["unknown"] = unknown
    ready = []
    for strategy in targets:
        if strategy in configured:
            configured[strategy]["verify"] = None
            if not configured[strategy]["missing"] and not configured[strategy]["invalid"]:
                ready.append(strategy)
    if ready:
        credentials, _ = resolve_credentials(ready, schemas, lookup, placement)
        try:
            for outcome in verify(ready, credentials):
                configured[outcome["strategy"]]["verify"] = verify_entry(outcome)
        except Fault as fault:
            section["error"] = fault.as_object()
        except (AttributeError, KeyError, TypeError):
            section["error"] = transport.unexpected_shape("POST", "/v1/verify").as_object()
    return section


def doctor(targets, environ, cwd, placement="post"):
    targets = list(dict.fromkeys(targets))
    path, config, lookup, cafile = settings.load(environ, cwd)
    connection = transport.opener(cafile)
    service_section = {
        "url": lookup(URL_VARIABLE),
        "variables": sources(lookup, (URL_VARIABLE, TOKEN_VARIABLE)),
    }
    ignored, tracked = git_state(path.parent.parent) if path else (None, None)
    report = {
        "ok": False,
        "next": "service",
        "config": {"path": str(path) if path else None, "git_ignored": ignored, "git_tracked": tracked},
        "secret_command": (
            "environment" if environ.get(SECRET_COMMAND_VARIABLE)
            else "config" if config.get(SECRET_COMMAND_VARIABLE)
            else None
        ),
        "service": service_section,
        "media_gateway": media_gateway_report(lookup),
        "strategies": None,
    }
    try:
        url, token = transport.service(lookup)
        status = transport.call(
            url, token, "GET", "/v1/status", opener=connection,
            timeout=transport.CHECK_TIMEOUT_SECONDS,
        )
        try:
            service_section["readiness"] = status["result"]["readiness"]
        except (KeyError, TypeError):
            raise transport.unexpected_shape("GET", "/v1/status")
        capabilities = transport.call(
            url, token, "GET", "/v1/capabilities", opener=connection,
            timeout=transport.CHECK_TIMEOUT_SECONDS,
        )
        schemas = transport.strategy_schemas(capabilities)
    except Fault as fault:
        service_section["error"] = fault.as_object()
        return report

    def verify(ready, credentials):
        body = build_body("verify", None, ready, credentials, [], placement)
        answer = transport.call(
            url, token, "POST", "/v1/verify", transport.json_payload(body), opener=connection
        )
        return answer["result"]["outcomes"]

    report["strategies"] = check_strategies(targets, schemas, lookup, verify, placement)
    report["ok"] = not problems(report)
    report["next"] = next_stage(report)
    return report


def settled(entry):
    if entry["missing"] or entry["invalid"]:
        return False
    return "verify" not in entry or (entry["verify"] or {}).get("state") == "verified"


def next_stage(report):
    """The first setup stage that is not done: service, media_gateway (partial
    or invalid) or platforms; None when every stage the report can judge is
    done."""
    if "error" in report["service"]:
        return "service"
    if report["media_gateway"]["state"] in ("partial", "invalid"):
        return "media_gateway"
    section = report["strategies"]
    configured = section["configured"].values()
    if section.get("unknown") or "error" in section or not configured:
        return "platforms"
    return None if all(settled(entry) for entry in configured) else "platforms"


def problems(report):
    """What keeps "ok" false, one short phrase each."""
    service_section = report["service"]
    if "error" in service_section:
        return [f"service {service_section['error']['code']}"]
    section = report["strategies"]
    found = [f"unknown platform {strategy}" for strategy in section.get("unknown", [])]
    if "error" in section:
        found.append(f"verify {section['error']['code']}")
    for strategy, entry in section["configured"].items():
        if "verify" not in entry:
            continue
        if entry["missing"]:
            found.append(f"{strategy} missing {', '.join(entry['missing'])}")
        elif entry["invalid"]:
            found.append(f"{strategy} invalid {', '.join(entry['invalid'])}")
        elif entry["verify"] is None:
            if "error" not in section:
                found.append(f"{strategy} not verified")
        elif entry["verify"]["state"] != "verified":
            found.append(f"{strategy} {entry['verify']['state']}")
    return found
