from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

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


def recent_channels(token, opener):
    answer = call(token, "getUpdates", {}, opener)
    if not answer or not answer.get("ok") or not isinstance(answer.get("result"), list):
        return None
    latest = {}
    for update in answer["result"]:
        member = (update or {}).get("my_chat_member") or {}
        chat = member.get("chat") or {}
        status = (member.get("new_chat_member") or {}).get("status")
        if chat.get("type") in ("channel", "supergroup", "group") and isinstance(chat.get("id"), int):
            latest[chat["id"]] = {"id": str(chat["id"]), "title": chat.get("title"), "admin": status in ADMINISTRATOR}
    return [{"id": item["id"], "title": item["title"]} for item in latest.values() if item["admin"]]


def business_connection(token, opener):
    answer = call(token, "getUpdates", {}, opener)
    if not answer or not answer.get("ok") or not isinstance(answer.get("result"), list):
        return False
    found = None
    for update in answer["result"]:
        connection = (update or {}).get("business_connection") or {}
        rights = connection.get("rights") or {}
        if connection.get("id") and connection.get("is_enabled") and rights.get("can_manage_stories"):
            found = str(connection["id"])
    return found
