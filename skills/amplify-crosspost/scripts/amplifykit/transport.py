from __future__ import annotations

import base64
import http.client
import json
import os
import socket
import ssl
import urllib.error
import urllib.request

from .errors import Fault
from .settings import (
    APP_TOKEN_VARIABLE,
    CERTIFICATE_VARIABLE,
    CONFIG_PATH,
    HEADER_SAFE,
    SERVICE_TOKEN,
    TOKEN_VARIABLE,
    URL_VARIABLE,
)


TIMEOUT_SECONDS = 620
CHECK_TIMEOUT_SECONDS = 30
PUBLISH_PATH = "/v1/publish"
PUBLISH_ADVICE = "; the post may already be published, check the platform before publishing again"
NOT_STARTED = {
    "RUN_STORE_UNAVAILABLE",
    "RUN_CAPACITY_EXCEEDED",
    "RUN_LIMIT_EXCEEDED",
    "SERVER_BUSY",
    "SPOOL_CAPACITY_EXCEEDED",
    "REQUEST_RECEIVE_TIMEOUT",
}
STARTED = {"RESULT_TOO_LARGE"}

TOKEN_ADMISSION = {
    "TOKEN_STRATEGY_DENIED": f"the account may not use one of these platforms; whoever issued {TOKEN_VARIABLE} allows it, or the platform is left out",
    "TOKEN_PROFILE_LIMIT_EXCEEDED": f"the account allows fewer destinations of one platform; whoever issued {TOKEN_VARIABLE} raises its limit",
    "TOKEN_FEATURE_DENIED": f"the account may not use this operation; whoever issued {TOKEN_VARIABLE} allows it",
    "TOKEN_MEDIA_FILE_TOO_LARGE": f"a file is over the account's limit for one file; attach a smaller or shorter one, or whoever issued {TOKEN_VARIABLE} raises the limit",
    "TOKEN_MEDIA_REQUEST_TOO_LARGE": f"the files are over the account's limit for one request; attach fewer or smaller files, or whoever issued {TOKEN_VARIABLE} raises the limit",
    "TOKEN_ROLE_DENIED": f"the token may not call this: {TOKEN_VARIABLE} holds the master token and {APP_TOKEN_VARIABLE} the app token; the amplify-setup skill sets them",
}


TOKEN_RUNNING = {
    "TOKEN_DAILY_QUOTA_EXCEEDED": "the account's daily publication quota is spent",
    "TOKEN_MONTHLY_QUOTA_EXCEEDED": "the account's monthly publication quota is spent",
    "TOKEN_POLICY_UNAVAILABLE": "the service cannot read the account's limits right now",
    "TOKEN_POLICY_CAPACITY_EXCEEDED": "the service cannot count the account's publications right now",
}

CHANNEL_REFUSALS = {
    "CHANNEL_NOT_FOUND": "a platform's channel is gone from the service",
    "CHANNEL_SUBJECT_MISMATCH": "a platform's channel belongs to another client of the account",
    "CHANNEL_AUTHORIZATION_MISSING": "a platform's channel is not connected for this placement",
    "CHANNEL_REAUTH_REQUIRED": "the platform rejected a channel's connection",
}
CERTIFICATE_BUNDLES = (
    "/etc/ssl/cert.pem",
    "/etc/ssl/certs/ca-certificates.crt",
    "/etc/pki/tls/certs/ca-bundle.crt",
)


class RefuseRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def opener(cafile=None):
    handlers = [RefuseRedirect]
    if cafile:
        try:
            context = ssl.create_default_context(cafile=cafile)
        except (OSError, ssl.SSLError) as error:
            raise Fault(
                "certificate_file_invalid",
                f"{CERTIFICATE_VARIABLE}={cafile} cannot be loaded ({error}); the operator points"
                " it at a PEM certificate bundle",
            )
        handlers.append(urllib.request.HTTPSHandler(context=context))
    return urllib.request.build_opener(*handlers)


OPENER = opener()


def service(lookup, variable=TOKEN_VARIABLE):
    url, token = lookup(URL_VARIABLE), lookup(variable)
    unset = [name for name, value in ((URL_VARIABLE, url), (variable, token)) if not value]
    if unset:
        raise Fault(
            "environment_missing",
            f"not set: {', '.join(unset)}; the amplify-setup skill sets "
            + ("it" if len(unset) == 1 else "them")
            + f" in {CONFIG_PATH.as_posix()}",
            missing=unset,
        )
    if not url.startswith("https://") or not HEADER_SAFE.fullmatch(url):
        raise Fault(
            "url_invalid",
            f"{URL_VARIABLE} must be an https:// URL of visible ASCII characters; the operator"
            " corrects it",
        )
    if not SERVICE_TOKEN.fullmatch(token):
        raise Fault(
            "token_invalid",
            f"{variable} is not a token of the form name:secret, the name and the 32-character"
            " secret the service issued joined by a colon; "
            + (
                "the operator asks whoever runs the service for the master token"
                if variable == TOKEN_VARIABLE
                else "the amplify-setup skill issues a new one"
            ),
        )
    return url.rstrip("/"), token


def json_payload(body):
    return json.dumps(body, ensure_ascii=False).encode("utf-8"), "application/json"


def certificate_advice():
    bundle = next((path for path in CERTIFICATE_BUNDLES if os.path.isfile(path)), None)
    value = bundle or "<path of a certificate bundle>"
    return (
        "this Python's trust store has no issuer for the service's certificate: run"
        f" 'amplify.py config --root <dir> --set {CERTIFICATE_VARIABLE}={value}' and try again."
        f" Where {CERTIFICATE_VARIABLE} is already set, it names the wrong bundle or something"
        " intercepts TLS. Never disable certificate verification"
    )


def token_advice(code, publishing, retry_after):
    wait = f", in {retry_after} s" if retry_after and retry_after.isdigit() else ""
    if code in TOKEN_ADMISSION:
        return f"; {TOKEN_ADMISSION[code]}" + ("; nothing was published" if publishing else "")
    if publishing:
        return (
            f"; {TOKEN_RUNNING[code]}: platforms the service reached before that may already show"
            f" the post, so check each platform, and publish again later{wait} only to those"
            " without it"
        )
    return f"; {TOKEN_RUNNING[code]}; try again later{wait}"


def refusal_advice(path, status, problem, retry_after=None):
    code = problem.get("code") if isinstance(problem, dict) else None
    publishing = path == PUBLISH_PATH
    if code in TOKEN_ADMISSION or code in TOKEN_RUNNING:
        return token_advice(code, publishing, retry_after)
    if code in CHANNEL_REFUSALS:
        return (
            f"; {CHANNEL_REFUSALS[code]}: the amplify-setup skill connects it again"
            + ("; nothing was published" if publishing else "")
        )
    if publishing and code in NOT_STARTED:
        return "; nothing was published, so publishing again later posts it once"
    if publishing and (code in STARTED or status >= 500):
        return PUBLISH_ADVICE
    if code in NOT_STARTED or status >= 500:
        return "; try again later"
    return "; error.problem names the cause"


def call(
    url, token, method, path, payload=None, opener=OPENER, timeout=TIMEOUT_SECONDS, headers=None,
    token_variable=TOKEN_VARIABLE,
):
    basic = base64.b64encode(token.encode("ascii")).decode("ascii")
    headers = {"Authorization": f"Basic {basic}", "Accept": "application/json", **(headers or {})}
    data = None
    if payload is not None:
        data, headers["Content-Type"] = payload
        if not isinstance(data, bytes):
            headers["Content-Length"] = str(len(data))
    request = urllib.request.Request(url + path, data=data, headers=headers, method=method)
    advice = PUBLISH_ADVICE if path == PUBLISH_PATH else ""
    try:
        with opener.open(request, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as error:
        try:
            problem = json.load(error)
        except (ValueError, OSError, http.client.HTTPException):
            problem = None
        finally:
            error.close()
        message = f"{method} {path} answered {error.code}"
        code = problem.get("code") if isinstance(problem, dict) else None

        if error.code == 404 and problem is None:
            message += f"; the service accepts no such token here: {token_variable} or {URL_VARIABLE} is wrong" + (
                "; the amplify-setup skill issues a new token"
                if token_variable == APP_TOKEN_VARIABLE
                else "; the operator checks both with whoever gave them access"
            )
        elif error.code == 404 and code in (None, "ROUTE_NOT_FOUND"):
            message += f"; the token works and the route is missing: {URL_VARIABLE} must be the service root, without a path"
        elif 300 <= error.code < 400:
            message += (
                f"; this script follows no redirect with the token, so the operator sets"
                f" {URL_VARIABLE} to the service's final https URL"
            )
        else:
            message += refusal_advice(path, error.code, problem, (error.headers or {}).get("Retry-After"))
        raise Fault("service_rejected", message, problem=problem)
    except urllib.error.URLError as error:
        reason = error.reason
        message = f"{method} {path} failed: {reason}"
        if isinstance(reason, ssl.SSLCertVerificationError):
            message += f"; {certificate_advice()}"
        elif isinstance(reason, (socket.timeout, TimeoutError)):
            message += advice or f"; check the network and {URL_VARIABLE}"
        else:
            message += f"; check the network and {URL_VARIABLE}"
        raise Fault("service_unreachable", message)
    except (OSError, http.client.HTTPException) as error:
        raise Fault(
            "service_unreachable",
            f"{method} {path} lost the answer: {type(error).__name__}"
            + (advice or f"; check the network and {URL_VARIABLE}"),
        )
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError as error:
        raise Fault(
            "response_invalid",
            f"{method} {path} did not answer JSON ({error})"
            + (advice or f"; check that {URL_VARIABLE} is the service's address"),
        )


def unexpected_shape(method, path):
    return Fault(
        "response_invalid",
        f"{method} {path} answered JSON of an unexpected shape"
        + (PUBLISH_ADVICE if path == PUBLISH_PATH else "")
        + f"; check that {URL_VARIABLE} is the service's address",
    )


def strategies(capabilities):
    try:
        return {entry["id"]: entry for entry in capabilities["result"]["strategies"]}
    except (AttributeError, KeyError, TypeError):
        raise unexpected_shape("GET", "/v1/capabilities")


def capabilities(url, token, connection, token_variable=TOKEN_VARIABLE):
    return strategies(
        call(
            url, token, "GET", "/v1/capabilities", opener=connection,
            timeout=CHECK_TIMEOUT_SECONDS, token_variable=token_variable,
        )
    )
