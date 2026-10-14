"""Requests to the service: the bearer, TLS, and what each failure
means for the operator."""

from __future__ import annotations

import http.client
import json
import os
import socket
import ssl
import urllib.error
import urllib.request

from .errors import Fault
from .settings import CERTIFICATE_VARIABLE, HEADER_SAFE, TOKEN_VARIABLE, URL_VARIABLE

# The service lets one operation run for up to 600 s and answers when it ends
# or pauses, so a shorter timeout abandons a publish that is still landing.
TIMEOUT_SECONDS = 620
# status and capabilities never reach a provider, so a slower answer is a hung
# connection rather than work in progress.
CHECK_TIMEOUT_SECONDS = 30
PUBLISH_PATH = "/v1/publish"
PUBLISH_ADVICE = "; the post may already be published, check the platform before publishing again"
# Refusals the service gives before a publication starts, and the one it
# gives after the platforms already ran.
NOT_STARTED = {
    "RUN_STORE_UNAVAILABLE",
    "RUN_CAPACITY_EXCEEDED",
    "RUN_LIMIT_EXCEEDED",
    "SERVER_BUSY",
    "REQUEST_RECEIVE_TIMEOUT",
}
STARTED = {"RESULT_TOO_LARGE"}
CERTIFICATE_BUNDLES = (
    "/etc/ssl/cert.pem",
    "/etc/ssl/certs/ca-certificates.crt",
    "/etc/pki/tls/certs/ca-bundle.crt",
)


class RefuseRedirect(urllib.request.HTTPRedirectHandler):
    """Turn every redirect into the HTTPError it answers with: following one
    would carry the bearer token to whatever host, over whatever scheme, the
    Location names."""

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


def service(lookup):
    url, token = lookup(URL_VARIABLE), lookup(TOKEN_VARIABLE)
    unset = [name for name, value in ((URL_VARIABLE, url), (TOKEN_VARIABLE, token)) if not value]
    if unset:
        raise Fault(
            "environment_missing",
            f"not set: {', '.join(unset)}; the amplify-setup skill walks the operator through"
            " adding them to .amplify/.config",
        )
    if not url.startswith("https://") or not HEADER_SAFE.fullmatch(url):
        raise Fault(
            "url_invalid",
            f"{URL_VARIABLE} must be an https:// URL of visible ASCII characters; the operator"
            " corrects it",
        )
    if not HEADER_SAFE.fullmatch(token):
        raise Fault(
            "token_invalid",
            f"{TOKEN_VARIABLE} holds a line break, a space or a non-ASCII character, which an"
            " Authorization header cannot carry; the operator stores the token as one line of"
            " the characters the service issued",
        )
    return url.rstrip("/"), token


def json_payload(body):
    return json.dumps(body, ensure_ascii=False).encode("utf-8"), "application/json"


def certificate_advice():
    bundle = next((path for path in CERTIFICATE_BUNDLES if os.path.isfile(path)), None)
    remedy = (
        f"add {CERTIFICATE_VARIABLE}={bundle} to .amplify/.config"
        if bundle
        else f"add {CERTIFICATE_VARIABLE}, the path of a certificate bundle, to .amplify/.config"
    )
    return (
        "the trust store of the Python running this script holds no issuer for that "
        "certificate, which is this machine's configuration and not a fault of the "
        f"service: {remedy} and run again. Where {CERTIFICATE_VARIABLE} is already "
        "set, the bundle it names is the wrong one or something is intercepting TLS. "
        "Never disable certificate verification"
    )


def refusal_advice(path, status, problem):
    code = problem.get("code") if isinstance(problem, dict) else None
    publishing = path == PUBLISH_PATH
    if publishing and code in NOT_STARTED:
        return "; nothing was published, so publishing again later posts it once"
    if publishing and (code in STARTED or status >= 500):
        return PUBLISH_ADVICE
    if code in NOT_STARTED or status >= 500:
        return "; try again later"
    return "; error.problem names the cause"


def call(url, token, method, path, payload=None, opener=OPENER, timeout=TIMEOUT_SECONDS, headers=None):
    """One request, its answer parsed as JSON. After a publish request went
    out, a lost answer, a 5xx and RESULT_TOO_LARGE leave unknown whether the
    post landed, and say so; the refusals in NOT_STARTED say it did not.
    payload is (data, content type); data is bytes or a sized iterable of
    bytes."""
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json", **(headers or {})}
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
        # The service answers a request without a valid bearer with an empty
        # 404, before it looks at the route; only an authenticated request
        # gets a problem body.
        if error.code == 404 and problem is None:
            message += f"; the service accepts no such token here: check {TOKEN_VARIABLE} and {URL_VARIABLE}"
        elif error.code == 404:
            message += f"; the token works and the route is missing: {URL_VARIABLE} must be the service root, without a path"
        elif 300 <= error.code < 400:
            message += (
                f"; this script follows no redirect with the token, so the operator sets"
                f" {URL_VARIABLE} to the service's final https URL"
            )
        else:
            message += refusal_advice(path, error.code, problem)
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


def strategy_schemas(capabilities):
    try:
        return {
            entry["id"]: entry["credentialSchema"]
            for entry in capabilities.get("result", {}).get("strategies", [])
        }
    except (AttributeError, KeyError, TypeError):
        raise unexpected_shape("GET", "/v1/capabilities")
