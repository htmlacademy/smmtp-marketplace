from __future__ import annotations

from . import account, settings, transport
from .errors import Fault
from .platforms import build_body
from .settings import (
    APP_TOKEN_VARIABLE,
    GATEWAY_TOKEN_VARIABLE,
    GATEWAY_URL_VARIABLE,
    SECRET_COMMAND_VARIABLE,
    SERVICE_TOKEN,
    TOKEN_VARIABLE,
    URL_VARIABLE,
    channel_variable,
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


def app_token_report(lookup, url, master, connection):
    token = lookup(APP_TOKEN_VARIABLE)
    if not token:
        return {"state": "none"}
    if not SERVICE_TOKEN.fullmatch(token):
        return {"state": "unusable", "reason": "is not name:secret"}
    name = token.split(":", 1)[0]
    try:
        transport.call(
            url, token, "GET", "/v1/token", opener=connection,
            timeout=transport.CHECK_TIMEOUT_SECONDS, token_variable=APP_TOKEN_VARIABLE,
        )
        listed = next(
            (
                entry for entry in account.app_tokens(url, master, connection)
                if entry.get("name") == name and entry.get("status") == "active"
            ),
            None,
        )
    except Fault as fault:
        return {"state": "unusable", "error": fault.as_object()}
    reason = "is not an active token of this account" if listed is None else account.app_token_problem(listed)
    return {"state": "unusable", "reason": reason} if reason else {"state": "working"}


def channel_report(url, master, connection, channel_id):
    try:
        entry = account.channel(url, master, connection, channel_id)
    except Fault as fault:
        if (fault.details.get("problem") or {}).get("code") == "CHANNEL_NOT_FOUND":
            return {"state": "gone"}
        return {"state": "error", "error": fault.as_object()}
    states = {item.get("state") for item in entry["authorizations"]}
    return {
        "state": "reauth_required" if "reauth_required" in states else "connected",
        "title": entry.get("title"),
        "placements": (entry.get("available") or {}).get("placements", []),
    }


def verify_entry(outcome):
    entry = {"state": outcome["state"]}
    for key in ("identity", "url"):
        if key in outcome:
            entry[key] = outcome[key]
    errors = [
        {"code": problem.get("code"), "retry": problem.get("retry"), "details": problem.get("details", {})}
        for problem in outcome.get("errors", [])
    ]
    if errors:
        entry["errors"] = errors
    return entry


def platforms_report(targets, strategies, lookup, url, master, connection, verify, placement):
    channels = {}
    for strategy in sorted(set(strategies) | set(targets)):
        channel_id = lookup(channel_variable(strategy))
        if not channel_id and strategy not in targets:
            continue
        entry = {"channel": channel_id}
        entry.update(channel_report(url, master, connection, channel_id) if channel_id else {"state": "none"})
        if strategy in targets:
            entry["verify"] = None
        channels[strategy] = entry
    section = {
        "placement": placement,
        "channels": channels,
        "unverified": [
            strategy for strategy, entry in channels.items()
            if "verify" not in entry and entry["state"] == "connected"
        ],
    }
    unknown = [strategy for strategy in targets if strategy not in strategies]
    if unknown:
        section["unknown"] = unknown
    ready = [
        strategy for strategy, entry in channels.items()
        if "verify" in entry and entry["state"] == "connected" and placement in entry["placements"]
    ]
    if ready:
        try:
            for outcome in verify(ready, {strategy: channels[strategy]["channel"] for strategy in ready}):
                channels[outcome["strategy"]]["verify"] = verify_entry(outcome)
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
    report = {
        "ok": False,
        "next": "service",
        "config": str(path) if path else None,
        "secret_command": (
            "environment" if environ.get(SECRET_COMMAND_VARIABLE)
            else "config" if config.get(SECRET_COMMAND_VARIABLE)
            else None
        ),
        "service": service_section,
        "app_token": None,
        "media_gateway": media_gateway_report(lookup),
        "platforms": None,
    }
    try:
        url, master = transport.service(lookup)
        status = transport.call(
            url, master, "GET", "/v1/status", opener=connection,
            timeout=transport.CHECK_TIMEOUT_SECONDS,
        )
        try:
            service_section["readiness"] = status["result"]["readiness"]
        except (KeyError, TypeError):
            raise transport.unexpected_shape("GET", "/v1/status")
        account.check_master(url, master, connection)
        strategies = transport.capabilities(url, master, connection)
    except Fault as fault:
        service_section["error"] = fault.as_object()
        return report
    report["app_token"] = app_token_report(lookup, url, master, connection)

    def verify(ready, channels):
        if report["app_token"]["state"] != "working":
            raise Fault(
                "app_token_unusable",
                f"{APP_TOKEN_VARIABLE} does not work, so nothing was checked; run 'amplify.py"
                " app-token' first",
            )
        body = build_body("verify", None, ready, channels, [], placement)
        answer = transport.call(
            url, lookup(APP_TOKEN_VARIABLE), "POST", "/v1/verify", transport.json_payload(body),
            opener=connection, token_variable=APP_TOKEN_VARIABLE,
        )
        return answer["result"]["outcomes"]

    report["platforms"] = platforms_report(
        targets, strategies, lookup, url, master, connection, verify, placement
    )
    report["ok"] = not problems(report)
    report["next"] = next_stage(report)
    return report


def settled(entry, placement):
    if entry["state"] != "connected":
        return False
    if "verify" not in entry:
        return True
    return placement in entry["placements"] and (entry["verify"] or {}).get("state") == "verified"


def next_stage(report):
    if "error" in report["service"]:
        return "service"
    if report["app_token"]["state"] != "working":
        return "app_token"
    if report["media_gateway"]["state"] in ("partial", "invalid"):
        return "media_gateway"
    section = report["platforms"]
    channels = section["channels"]
    if section.get("unknown") or "error" in section or not channels:
        return "platforms"
    if not all(settled(entry, section["placement"]) for entry in channels.values()):
        return "platforms"
    return "verify" if section["unverified"] else None


def problems(report):
    if "error" in report["service"]:
        return [f"service {report['service']['error']['code']}"]
    found = []
    if report["app_token"]["state"] != "working":
        found.append(f"app token {report['app_token']['state']}")
    if report["media_gateway"]["state"] in ("partial", "invalid"):
        found.append(f"media gateway {report['media_gateway']['state']}")
    section = report["platforms"]
    found += [f"unknown platform {strategy}" for strategy in section.get("unknown", [])]
    if "error" in section:
        found.append(f"verify {section['error']['code']}")
    placement = section["placement"]
    for strategy, entry in section["channels"].items():
        if entry["state"] != "connected":
            found.append(f"{strategy} {entry['state']}")
        elif "verify" in entry:
            if placement not in entry["placements"]:
                found.append(f"{strategy} not connected for a {placement}")
            elif entry["verify"] is None:
                if "error" not in section:
                    found.append(f"{strategy} not verified")
            elif entry["verify"]["state"] != "verified":
                found.append(f"{strategy} {entry['verify']['state']}")
    return found
