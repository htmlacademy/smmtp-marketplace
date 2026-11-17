from __future__ import annotations

import time
import urllib.parse

from . import account, links, settings, telegram, transport
from .configuration import update_file
from .errors import Fault
from .platforms import connectable, field_variables, publishing_forms
from .settings import APP_TOKEN_VARIABLE, SERVICE_TOKEN, SUBJECT, channel_variable

REDIRECT_WAIT_SECONDS = 120
POLL_SECONDS = 3
WEBHOOK_VARIABLE = "DISCORD_WEBHOOK_URL"
TELEGRAM_TOKEN_VARIABLE = "TELEGRAM_BOT_TOKEN"
BUSINESS_VARIABLE = "TELEGRAM_BUSINESS_CONNECTION_ID"
STEP_REFUSALS = {
    "CREDENTIAL_REJECTED": "the platform refused the values sent: the operator checks them and corrects them, then run again with --flow",
    "FLOW_STEP_INVALID": "a value does not have the shape the platform takes: the operator corrects it, then run again with --flow",
    "TARGET_UNAVAILABLE": "the account cannot publish there: the operator gives it access, then run again with --flow",
}
TARGET_REASONS = {
    "chat_not_found": "the platform finds no channel at that address: check the link with the operator, then run again with --flow and --link",
    "chat_inaccessible": "the platform finds no channel at that address: check the link with the operator, then run again with --flow and --link",
    "bot_not_member": "{who} is not in the channel: the operator adds it as an administrator, then run again with --flow",
    "channel_private": "{who} is not in the channel: the operator adds it as an administrator, then run again with --flow",
    "bot_kicked": "{who} was removed from the channel: the operator adds it back as an administrator, then run again with --flow",
    "bot_not_admin": "{who} is in the channel but not an administrator: the operator makes it one, then run again with --flow",
    "can_post_messages_missing": "{who} is an administrator without the right to post messages: the operator grants it, then run again with --flow",
    "write_permission_missing": "{who} is an administrator without the right to post messages: the operator grants it, then run again with --flow",
}
OTHER_REASON = "the platform refused the channel ({reason}): tell the operator what to fix for {who} there, then run again with --flow"
CONNECT_REFUSALS = {
    "AUTHORIZATION_ACCOUNT_MISMATCH": "the key or sign-in belongs to another account than the channel's: use that account's, or to connect a different channel empty AMPLIFY_CHANNEL_<PLATFORM> with config first",
    "AUTHORIZATION_CONFLICT": "the channel is already connected for these placements: run doctor",
    "FLOW_LIMIT_EXCEEDED": "too many flows are open: wait until they expire, 15 minutes after they start, then connect again without --flow",
    "FLOW_FORM_UNAVAILABLE": "the service cannot start that flow: whoever runs the service sets it up",
    "FLOW_NOT_FOUND": "the flow expired: connect again without --flow",
}


def telegram_unreachable():
    return Fault(
        "telegram_unreachable",
        "Telegram did not answer the bot's update request; check the network, or that no webhook"
        " is set on the bot, nothing was started",
    )


def open_master(environ, cwd):
    path, _, lookup, cafile = settings.load(environ, cwd)
    connection = transport.opener(cafile)
    url, master = transport.service(lookup)
    account.require_config(path)
    return path, lookup, connection, url, master


def app_token(environ, cwd):
    path, lookup, connection, url, master = open_master(environ, cwd)
    if lookup.source(APP_TOKEN_VARIABLE) == "environment":
        raise Fault(
            "app_token_in_environment",
            f"{APP_TOKEN_VARIABLE} is set in the environment, which wins over the file; remove it"
            " there first, nothing was issued",
        )
    previous = lookup(APP_TOKEN_VARIABLE)
    name, token = account.issue_app_token(url, master, connection)
    update_file(path, {APP_TOKEN_VARIABLE: token})
    revoked = None
    if previous and SERVICE_TOKEN.fullmatch(previous):
        old = previous.split(":", 1)[0]
        if old != name:
            revoked = account.revoke_app_token(url, master, connection, old)
    return {"ok": True, "revoked": revoked}


def read_link(strategy, link):
    if link is None:
        return None
    info = links.parse(link, strategy)
    if info["platform"] != strategy:
        raise Fault(
            "link_mismatch",
            f"{link!r} is not a {strategy} address; pass the operator's {strategy} link, nothing"
            " was started",
        )
    return info


def choose_form(strategy, entry, placement, form_id, info=None):
    forms = publishing_forms(entry, placement)
    form_id = form_id or (info or {}).get("form")
    if form_id is not None:
        forms = [form for form in forms if form.get("id") == form_id]
        if not forms:
            raise Fault(
                "form_unknown",
                f"{strategy} has no form {form_id} for a {placement}; use one of error.forms,"
                " nothing was started",
                forms=[form.get("id") for form in publishing_forms(entry, placement)],
            )
    usable = [form for form in forms if connectable(form)]
    if not usable:
        raise Fault(
            "form_unavailable",
            f"the service cannot connect {strategy} for a {placement}; whoever runs the service"
            " sets it up, nothing was started",
        )
    if len(usable) > 1:
        raise Fault(
            "form_ambiguous",
            f"{strategy} connects in more than one way for a {placement}; ask the question plan"
            " gives for it and pass the answer as --form, nothing was started",
            forms=[form.get("id") for form in usable],
        )
    return usable[0]


def derived_fields(strategy, form, lookup, info):
    derived = {}
    if strategy == "discord":
        parts = links.webhook_parts(lookup(WEBHOOK_VARIABLE))
        if parts:
            derived = {"webhookId": parts[0], "webhookToken": parts[1]}
    if strategy == "mastodon" and info and info.get("host"):
        derived = {"host": info["host"]}
    if strategy == "bluesky" and info and info.get("address"):
        derived = {"identifier": info["address"].removeprefix("@")}
    fields = field_variables(strategy, form)
    return {field: value for field, value in derived.items() if field in fields}


def operator_asks(strategy, form, info):
    asks = [
        name for field, name in field_variables(strategy, form).items()
        if field not in derived_fields(strategy, form, lambda _: None, info)
        and name not in settings.DEFAULTS
        and name != BUSINESS_VARIABLE
    ]
    if strategy == "discord" and asks:
        return [WEBHOOK_VARIABLE]
    return asks


def field_values(strategy, form, lookup, info):
    derived = derived_fields(strategy, form, lookup, info)
    values, missing = {}, []
    for field, name in field_variables(strategy, form).items():
        value = lookup(name) or derived.get(field)
        if value:
            values[field] = value
        elif strategy == "discord":
            missing = [WEBHOOK_VARIABLE]
        elif name != BUSINESS_VARIABLE:
            missing.append(name)
    return values, missing


def flow_path(flow_id):
    return f"/v1/flows/{urllib.parse.quote(flow_id, safe='')}"


def flow_call(url, master, connection, method, path, body=None):
    try:
        state = account.master_call(url, master, connection, method, path, body)
    except Fault as fault:
        code = (fault.details.get("problem") or {}).get("code")
        if code in STEP_REFUSALS:
            raise Fault("step_refused", f"{code}: {STEP_REFUSALS[code]}", problem=fault.details["problem"])
        if code in CONNECT_REFUSALS:
            raise Fault("connect_refused", f"{code}: {CONNECT_REFUSALS[code]}", problem=fault.details["problem"])
        raise
    if not isinstance(state, dict):
        raise transport.unexpected_shape(method, path)
    return state


def start(url, master, connection, strategy, form, channel_id):
    body = {"strategy": strategy, "form": form["id"], "subject": SUBJECT}
    existing = None
    if channel_id:
        try:
            existing = account.channel(url, master, connection, channel_id)
        except Fault as fault:
            if (fault.details.get("problem") or {}).get("code") != "CHANNEL_NOT_FOUND":
                raise
    if existing is not None:
        present = next(
            (item for item in existing["authorizations"] if item.get("form") == form["id"]), None
        )
        if present is not None:
            path = f"/v1/authorizations/{urllib.parse.quote(present['id'], safe='')}/reauthorize"
            return flow_call(url, master, connection, "POST", path, {})
        body["channelId"] = channel_id
    return flow_call(url, master, connection, "POST", "/v1/flows", body)


def wait_past_redirect(url, master, connection, flow_id, state):
    deadline = time.monotonic() + REDIRECT_WAIT_SECONDS
    while (state.get("step") or {}).get("kind") == "redirect" and time.monotonic() < deadline:
        time.sleep(POLL_SECONDS)
        state = flow_call(url, master, connection, "GET", flow_path(flow_id))
    return state


def pick_target(targets, wanted, info):
    if wanted is not None:
        for candidate in targets:
            if wanted in (candidate.get("target"), candidate.get("title")):
                return candidate
        raise Fault(
            "target_unknown",
            f"{wanted!r} is none of the targets the platform listed; pass the title of one"
            " of error.targets as --target",
            targets=[item.get("title") for item in targets],
        )
    address = (info or {}).get("address")
    if address:
        matches = [
            item for item in targets
            if address == item.get("target") or links.same_name(address, item.get("title"))
        ]
        if len(matches) == 1:
            return matches[0]
    return targets[0] if len(targets) == 1 else None


def target_input(candidate):
    selection = {"target": candidate["target"]}
    joinable = candidate.get("channels") or []
    if len(joinable) == 1:
        selection["channelId"] = joinable[0]["id"]
    return {"targets": [selection]}


def telegram_private_target(lookup, connection, wanted):
    channels = telegram.recent_channels(lookup(TELEGRAM_TOKEN_VARIABLE), connection)
    if channels is None:
        raise telegram_unreachable()
    if wanted is not None:
        chosen = [item for item in channels if wanted in (item["id"], item["title"])]
        return (chosen[0]["id"], None) if chosen else (None, channels)
    if len(channels) == 1:
        return channels[0]["id"], None
    return None, channels


def stop(report, step, next_step, **fields):
    report.update(step=step, next=next_step, **fields)
    return report


def connect(strategy, placement, form_id, flow_id, link, target, environ, cwd):
    path, lookup, connection, url, master = open_master(environ, cwd)
    info = read_link(strategy, link)
    strategies = transport.capabilities(url, master, connection)
    if strategy not in strategies:
        raise Fault(
            "platform_unknown",
            f"not a platform the service offers: {strategy}; use one of error.available,"
            " nothing was started",
            available=sorted(strategies),
        )
    allowed = account.policy(url, master, connection)
    if allowed is not None and strategy not in allowed:
        raise Fault(
            "platform_not_allowed",
            f"the account may not use {strategy}; whoever issued {settings.TOKEN_VARIABLE} allows"
            " it, nothing was started",
            allowed=sorted(allowed),
        )
    form = choose_form(strategy, strategies[strategy], placement, form_id, info)
    values, missing = field_values(strategy, form, lookup, info)
    report = {"ok": False, "platform": strategy, "flow": flow_id}
    business = next(
        (field for field, name in field_variables(strategy, form).items() if name == BUSINESS_VARIABLE), None
    )
    if business and business not in values and not missing:
        found = telegram.business_connection(lookup(TELEGRAM_TOKEN_VARIABLE), connection)
        if found is False:
            raise telegram_unreachable()
        if found is None:
            return stop(
                report, "fields",
                "Telegram shows no Telegram Business connection within the last day that lets the"
                " bot manage stories: the operator connects it, or disconnects and connects it"
                " again if it is there already, then run again",
            )
        values[business] = found
    typed = (info or {}).get("address")
    if strategy == "telegram" and info and info["private"] and "target" in form["steps"] and not missing:
        typed, choices = telegram_private_target(lookup, connection, target)
        if typed is None and choices:
            return stop(
                report, "target",
                "ask the operator which of targets is their channel, then run again with --target"
                " set to its title",
                targets=[item["title"] for item in choices],
            )
        if typed is None:
            return stop(
                report, "target",
                "Telegram shows no channel that made the bot an administrator within the last day:"
                " the operator adds it to their channel as an administrator allowed to post, or"
                " removes it and adds it again if it is there already, then run again",
            )
        target = None
    if flow_id is None:
        if missing:
            raise Fault(
                "credentials_missing",
                f"{strategy} needs the values in error.missing from the operator first; nothing"
                " was started",
                missing=missing,
            )
        state = start(url, master, connection, strategy, form, lookup(channel_variable(strategy)))
    else:
        state = flow_call(url, master, connection, "GET", flow_path(flow_id))
        state = wait_past_redirect(url, master, connection, flow_id, state)
    report["flow"] = state.get("flowId", flow_id)

    def send(step_input):
        try:
            return flow_call(url, master, connection, "POST", flow_path(report["flow"]), {"input": step_input})
        except Fault as fault:
            if fault.code != "step_refused":
                raise
            fault.details["flow"] = report["flow"]
            problem = fault.details["problem"]
            reason = (problem.get("details") or {}).get("reason")
            if problem.get("code") == "TARGET_UNAVAILABLE" and isinstance(reason, str):
                who = (report.get("account") or {}).get("name") or "the account"
                fault.details["reason"] = reason
                fault.message = "TARGET_UNAVAILABLE: " + TARGET_REASONS.get(reason, OTHER_REASON).format(
                    who=who, reason=reason
                )
            raise

    sent_fields = False
    while True:
        if isinstance(state.get("account"), dict):
            report["account"] = state["account"]
        if "error" in state:
            report["error"] = {
                "code": "flow_ended",
                "message": f"the flow ended with {state['error'].get('code')}; connect again"
                " without --flow once the operator has dealt with it",
                "problem": state["error"],
            }
            return report
        if "authorization" in state:
            return complete(report, state, path, lookup, strategy, form)
        step = state.get("step") or {}
        kind = step.get("kind")
        if kind == "fields" and not sent_fields and not missing:
            state = send({"values": values})
            sent_fields = True
        elif kind == "fields":
            return stop(
                report, kind, "the operator supplies the values in missing, then run again with --flow",
                missing=missing,
            )
        elif kind == "redirect":
            return stop(
                report, kind,
                "give the operator url to open, sign in with the account that publishes and allow"
                " access; once they have, run again with --flow",
                url=step.get("url"),
            )
        elif kind == "target" and step.get("targets") is not None:
            chosen = pick_target(step["targets"], target, info)
            if chosen is None:
                return stop(
                    report, kind,
                    "tell the operator which account they signed in with and ask which of targets"
                    " to publish to, then run again with --flow and --target set to its title",
                    targets=[item.get("title") for item in step["targets"]],
                )
            state = send(target_input(chosen))
            target = None
        elif kind == "target" and typed is None:
            return stop(
                report, kind,
                "ask the operator for the link to their channel, then run again with --flow and"
                " --link",
            )
        elif kind == "target":
            state = send({"targets": [{"target": typed}]})
        else:
            raise Fault(
                "step_unsupported",
                f"the flow asks for a {kind} step, which this script does not run; tell"
                " whoever runs the service",
            )


def complete(report, state, path, lookup, strategy, form):
    try:
        channel_id = state["channels"][0]["id"]
        title = state["channels"][0].get("title")
        identity = state["authorization"].get("identity")
    except (AttributeError, IndexError, KeyError, TypeError):
        raise transport.unexpected_shape("GET", "/v1/flows/{flowId}")
    variables = list(field_variables(strategy, form).values())
    if strategy == "discord":
        variables.append(WEBHOOK_VARIABLE)
    cleared = [name for name in variables if lookup.source(name) == "config"]
    update_file(path, {channel_variable(strategy): channel_id, **{name: "" for name in cleared}})
    report.update(
        ok=True,
        channel=channel_id,
        title=title,
        account=identity,
        cleared=cleared,
        stored=[name for name in variables if lookup.source(name) == "store"],
    )
    return report
