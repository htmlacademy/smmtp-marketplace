from __future__ import annotations

from . import account, links, settings, transport
from .connection import choose_form, operator_asks
from .errors import Fault
from .settings import channel_variable


def entry_for(item, placement, strategies, allowed, lookup):
    platform = item["platform"]
    entry = {
        "link": item.get("link"),
        "platform": platform,
        "address": item["address"],
        "connected": bool(platform and lookup(channel_variable(platform))),
    }
    if platform is None:
        entry["question"] = "Which platform is this channel on?"
        return entry
    if strategies is None:
        entry["available"] = None
        if item["form"]:
            entry["form"] = item["form"]
        if item["question"]:
            entry["question"] = item["question"]
        return entry
    if platform not in strategies:
        return {**entry, "available": False, "reason": "the service does not offer this platform"}
    if allowed is not None and platform not in allowed:
        return {**entry, "available": False, "reason": "the account may not use this platform"}
    try:
        form = choose_form(platform, strategies[platform], placement, None, item)
    except Fault as fault:
        if fault.code != "form_ambiguous":
            return {**entry, "available": False, "reason": "the service cannot connect it"}
        return {
            **entry,
            "available": True,
            "question": item["question"] or f"Which way: {', '.join(fault.details['forms'])}?",
            "forms": fault.details["forms"],
        }
    return {
        **entry,
        "available": True,
        "form": form["id"],
        "sign_in": "redirect" in form["steps"],
        "asks": operator_asks(platform, form, item),
    }


def plan(link_texts, platforms, placement, environ, cwd):
    _, _, lookup, cafile = settings.load(environ, cwd)
    items = [{**links.parse(text), "link": text} for text in link_texts]
    items += [{**links.found(platform), "link": None} for platform in platforms]
    report = {"ok": True, "placement": placement, "service": None}
    strategies = allowed = None
    try:
        url, master = transport.service(lookup)
        connection = transport.opener(cafile)
        offered = transport.capabilities(url, master, connection)
        allowed = account.policy(url, master, connection)
        strategies = offered
        report["service"] = "ready"
    except Fault as fault:
        report["service"] = fault.code
    report["channels"] = [entry_for(item, placement, strategies, allowed, lookup) for item in items]
    if strategies is not None:
        for entry in report["channels"]:
            if entry["connected"]:
                channel_id = lookup(channel_variable(entry["platform"]))
                entry.update(connected_channel(url, master, connection, channel_id, placement))
    return report


def connected_channel(url, master, connection, channel_id, placement):
    try:
        found = account.channel(url, master, connection, channel_id)
    except Fault as fault:
        gone = (fault.details.get("problem") or {}).get("code") == "CHANNEL_NOT_FOUND"
        return {"connected": not gone, "title": None}
    placements = (found.get("available") or {}).get("placements") or []
    return {"connected": placement in placements, "title": found.get("title")}
