from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

from .errors import Fault

API = "https://api.telegram.org"
TIMEOUT_SECONDS = 20
MAXIMUM_BYTES = 4 * 1024 * 1024
ADMINISTRATOR = {"administrator", "creator"}


def call(token, method, params, opener):
    url = f"{API}/bot{token}/{method}?{urllib.parse.urlencode(params)}"
    try:
        with opener.open(url, timeout=TIMEOUT_SECONDS) as response:
            raw = response.read(MAXIMUM_BYTES + 1)
    except urllib.error.HTTPError as error:
        raw = error.read(MAXIMUM_BYTES + 1)
    except (urllib.error.URLError, OSError):
        return None
    if len(raw) > MAXIMUM_BYTES:
        return None
    try:
        answer = json.loads(raw)
    except ValueError:
        return None
    return answer if isinstance(answer, dict) else None


def updates(token, opener):
    answer = call(token, "getUpdates", {}, opener)
    if answer and answer.get("error_code") == 401:
        raise Fault(
            "bot_token_rejected",
            "Telegram refused the bot's key: the operator copies it from @BotFather again and it"
            " is saved with config, nothing was started",
        )
    if not answer or not answer.get("ok") or not isinstance(answer.get("result"), list):
        return None
    return answer["result"]


def recent_channels(token, opener):
    found = updates(token, opener)
    if found is None:
        return None
    latest = {}
    for update in found:
        member = (update or {}).get("my_chat_member") or {}
        chat = member.get("chat") or {}
        status = (member.get("new_chat_member") or {}).get("status")
        if chat.get("type") in ("channel", "supergroup", "group") and isinstance(chat.get("id"), int):
            latest[chat["id"]] = {"id": str(chat["id"]), "title": chat.get("title"), "admin": status in ADMINISTRATOR}
    return [{"id": item["id"], "title": item["title"]} for item in latest.values() if item["admin"]]


def business_connection(token, opener):
    updates_found = updates(token, opener)
    if updates_found is None:
        return False
    found = None
    for update in updates_found:
        connection = (update or {}).get("business_connection") or {}
        rights = connection.get("rights") or {}
        if connection.get("id") and connection.get("is_enabled") and rights.get("can_manage_stories"):
            found = str(connection["id"])
    return found
