from __future__ import annotations

import secrets
import urllib.parse

from . import transport
from .errors import Fault
from .settings import APP_TOKEN_VARIABLE, SUBJECT

APP_TOKEN_ROLE = "publish"
APP_TOKEN_PREFIX = "crosspost-"


def account_call(url, credential, connection, method, path, body=None):
    payload = transport.json_payload(body) if body is not None else None
    return transport.call(
        url, credential, method, path, payload, opener=connection, timeout=transport.CHECK_TIMEOUT_SECONDS
    )


def allowed_strategies(token_answer):
    try:
        allowed = token_answer["token"]["policy"]["allowed_strategies"]
    except (KeyError, TypeError):
        raise transport.unexpected_shape("GET", "/v1/token")
    if allowed is not None and not isinstance(allowed, list):
        raise transport.unexpected_shape("GET", "/v1/token")
    return allowed


def policy(url, credential, connection):
    return allowed_strategies(account_call(url, credential, connection, "GET", "/v1/token"))


def check_account(url, credential, connection):
    query = urllib.parse.urlencode({"subject": SUBJECT})
    account_call(url, credential, connection, "GET", f"/v1/channels?{query}")


def channel(url, credential, connection, channel_id):
    path = f"/v1/channels/{urllib.parse.quote(channel_id, safe='')}"
    answer = account_call(url, credential, connection, "GET", path)
    if not isinstance(answer, dict) or not isinstance(answer.get("authorizations"), list):
        raise transport.unexpected_shape("GET", "/v1/channels/{channelId}")
    return answer


def app_tokens(url, credential, connection):
    answer = account_call(url, credential, connection, "GET", "/v1/app-tokens")
    try:
        return list(answer["appTokens"])
    except (KeyError, TypeError):
        raise transport.unexpected_shape("GET", "/v1/app-tokens")


def app_token_problem(entry):
    if entry.get("status") != "active":
        return "revoked"
    if entry.get("role") != APP_TOKEN_ROLE:
        return f"has the role {entry.get('role')}, not {APP_TOKEN_ROLE}"
    subjects = entry.get("subjects")
    if subjects != "*" and SUBJECT not in (subjects or []):
        return f"is not bound to the client {SUBJECT}"
    return None


def issue_app_token(url, credential, connection):
    body = {"role": APP_TOKEN_ROLE, "subjects": [SUBJECT], "name": APP_TOKEN_PREFIX + secrets.token_hex(4)}
    answer = account_call(url, credential, connection, "POST", "/v1/app-tokens", body)
    try:
        return answer["name"], f"{answer['name']}:{answer['secret']}"
    except (KeyError, TypeError):
        raise transport.unexpected_shape("POST", "/v1/app-tokens")


def revoke_app_token(url, credential, connection, name):
    for entry in app_tokens(url, credential, connection):
        if entry.get("name") == name and entry.get("status") == "active":
            path = f"/v1/app-tokens/{urllib.parse.quote(str(entry.get('id')), safe='')}"
            account_call(url, credential, connection, "DELETE", path)
            return name
    return None


def require_config(path):
    if path is None:
        raise Fault(
            "config_missing",
            f"no settings file to write {APP_TOKEN_VARIABLE} or a channel into; run"
            " 'amplify.py config --root DIR' first, nothing was changed",
        )
