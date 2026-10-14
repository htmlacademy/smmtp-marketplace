"""Requests to the news service: the bearer key, how long one run waits on a
rate limit, and what each refusal means for the caller."""

from __future__ import annotations

import http.client
import json
import os
import re
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request

from .errors import Fault
from .settings import CERTIFICATE_VARIABLE, KEY_FILE, KEY_VARIABLE, URL_VARIABLE

# The service answers within five seconds; the rest is for a slow connection.
TIMEOUT_SECONDS = 30
# A harness usually gives one command two minutes, so the waits of a whole
# run stay well under that.
WAIT_BUDGET_SECONDS = 60
# A limit that has not cleared after three waits is not a burst; say so.
MAX_WAITS = 3
# A service at capacity answers like a refused key and clears within seconds,
# so one empty answer is asked again before it is reported.
CAPACITY_WAIT_SECONDS = 5
# Twice the 1 MiB the service caps a response at: more is not its answer.
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_PROBLEM_BYTES = 64 * 1024
RATE_CODES = ("rate", "heavy_rate")
# Refusals the service gives before it admits, and so charges, a request.
UNADMITTED_CODES = (*RATE_CODES, "access_denied", "service_rejected")
# What an answer in a form the service never uses most likely means.
NOT_THE_SERVICE = (
    f"{URL_VARIABLE} in {KEY_FILE} may point at something that is not the service, which the"
    " operator checks; otherwise try again later"
)
SERVICE_CODE = re.compile(r"[a-z_]{1,64}")
SECONDS = re.compile(r"[0-9]{1,5}")
CERTIFICATE_BUNDLES = (
    "/etc/ssl/cert.pem",
    "/etc/ssl/certs/ca-certificates.crt",
    "/etc/pki/tls/certs/ca-bundle.crt",
)
ADVICE = {
    "invalid_request": (
        "the service rejected the request without saying which part; with --cursor, the cursor"
        " is older than 15 minutes or another argument changed, so run the command again"
        " without it; otherwise check each value against the command's --help"
    ),
    "finding_not_found": "no item has this ID; take the ID or the URL from a search result",
    "representation_too_large": "one item does not fit a response; pass fewer --fields",
    "quota": "the key's daily quota is spent; 'news.py doctor' shows when it resets",
    "rate": "the request rate limit is exceeded; run the command again later",
    "heavy_rate": (
        "the rate limit of searches with --count or a *. host is exceeded; run the command"
        " again later"
    ),
    "unavailable": "the service is unavailable; try again later",
    "archive_error": "the service could not answer; try again later",
    "timeout": "the service ran out of time; shorten the period or add a filter",
    "internal_error": "the service failed; request_id identifies the request",
}


class RefuseRedirect(urllib.request.HTTPRedirectHandler):
    """Turn every redirect into the HTTPError it answers with: following one
    would carry the key to whatever host the Location names."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def opener(cafile: str | None = None):
    handlers: list = [RefuseRedirect]
    if cafile:
        try:
            context = ssl.create_default_context(cafile=cafile)
        except (OSError, ssl.SSLError) as error:
            raise Fault(
                "certificate",
                f"{CERTIFICATE_VARIABLE}={cafile} in {KEY_FILE} cannot be loaded ({error}); the"
                " operator points it at a PEM certificate bundle",
            )
        handlers.append(urllib.request.HTTPSHandler(context=context))
    return urllib.request.build_opener(*handlers)


def certificate_advice() -> str:
    bundle = next((path for path in CERTIFICATE_BUNDLES if os.path.isfile(path)), None)
    line = f"{CERTIFICATE_VARIABLE}={bundle or '<certificate bundle>'}"
    return (
        "the service's certificate was not verified: this Python has no certificate for its"
        " issuer, which is this machine's trust store and not a fault of the service; the"
        f" operator adds the line {line} to {KEY_FILE}. Where {CERTIFICATE_VARIABLE} is already"
        " set, the bundle is the wrong one or something intercepts TLS. Never disable"
        " certificate verification"
    )


def refusal(error: urllib.error.HTTPError) -> Fault:
    try:
        raw = error.read(MAX_PROBLEM_BYTES)
    except (OSError, http.client.HTTPException):
        raw = b""
    finally:
        error.close()
    try:
        problem = json.loads(raw)["error"]
        code, request_id = problem["code"], problem.get("request_id")
    except (ValueError, KeyError, TypeError):
        code, request_id = None, None
    if isinstance(code, str) and SERVICE_CODE.fullmatch(code):
        details: dict = {"status": error.code}
        if isinstance(request_id, str):
            details["request_id"] = request_id[:64]
        retry_after = error.headers.get("Retry-After", "")
        if SECONDS.fullmatch(retry_after):
            details["retry_after"] = int(retry_after)
        advice = "the service refused the request; request_id identifies it"
        return Fault(code, ADVICE.get(code, advice), **details)
    if error.code == 404 and not raw.strip():
        # The service answers an empty 404 to every request it will not confirm.
        return Fault(
            "access_denied",
            f"the service gave an empty answer: {KEY_VARIABLE} in {KEY_FILE} is wrong,"
            " expired or revoked, or the service is at capacity; the access_denied entry under troubleshooting in 'news.py --help' tells"
            " them apart",
            status=404,
        )
    if 300 <= error.code < 400:
        return Fault(
            "service_rejected",
            "the address answered with a redirect, which this script never follows with the key;"
            f" {URL_VARIABLE} in {KEY_FILE} is not the service's own address, which the operator"
            " checks",
            status=error.code,
        )
    return Fault(
        "service_rejected",
        f"the address answered {error.code} in a form the service does not use;"
        f" {NOT_THE_SERVICE}",
        status=error.code,
    )


class Client:
    """One run's requests. A rate refusal is waited out a few times while the
    waits fit the run's budget; nothing else is retried, because the service
    charges every request it admits."""

    def __init__(self, key: str, url: str, opener, sleep=time.sleep) -> None:
        self.key = key
        self.url = url
        self.opener = opener
        self.sleep = sleep
        self.waited = 0
        self.requests = 0

    def get(self, path: str, query: dict | None = None) -> dict:
        return self.call("GET", path, query, None)

    def post(self, path: str, body: dict) -> dict:
        return self.call("POST", path, None, body)

    def call(self, method: str, path: str, query: dict | None, body: dict | None) -> dict:
        waits, denied = 0, False
        while True:
            try:
                return self.send(method, path, query, body)
            except Fault as fault:
                limited = fault.details.get("retry_after")
                if fault.code == "access_denied" and not denied:
                    denied, wait = True, CAPACITY_WAIT_SECONDS
                elif fault.code in RATE_CODES and limited is not None and waits < MAX_WAITS:
                    waits, wait = waits + 1, max(limited, 1)
                else:
                    raise
                if self.waited + wait > WAIT_BUDGET_SECONDS:
                    raise
                self.waited += wait
                self.sleep(wait)

    def send(self, method: str, path: str, query: dict | None, body: dict | None) -> dict:
        url = self.url + path
        if query:
            url += "?" + urllib.parse.urlencode(query, doseq=True)
        headers = {"Authorization": f"Bearer {self.key}", "Accept": "application/json"}
        data = None
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with self.opener.open(request, timeout=TIMEOUT_SECONDS) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as error:
            fault = refusal(error)
            if fault.code not in UNADMITTED_CODES:
                self.requests += 1
            raise fault
        except urllib.error.URLError as error:
            if isinstance(error.reason, ssl.SSLCertVerificationError):
                raise Fault("certificate", certificate_advice())
            host = urllib.parse.urlsplit(self.url).hostname
            if isinstance(error.reason, socket.gaierror):
                cause = "its host name does not resolve"
            elif isinstance(error.reason, TimeoutError):
                cause = "it did not answer in time"
            else:
                cause = "the connection failed"
            raise Fault(
                "unreachable",
                f"no connection to the service, {cause}: no network, a sandbox that blocks it, or"
                f" a wrong {URL_VARIABLE} in {KEY_FILE}; the operator allows the host {host},"
                " approves the command to run outside the sandbox, or corrects the address",
            )
        except (OSError, http.client.HTTPException) as error:
            raise Fault(
                "unreachable",
                f"the answer was lost ({type(error).__name__}); the request may have been"
                " charged all the same, so check the network before running it again",
            )
        self.requests += 1
        if len(raw) > MAX_RESPONSE_BYTES:
            raise Fault(
                "response_invalid", f"the answer is larger than the service sends; {NOT_THE_SERVICE}"
            )
        try:
            answer = json.loads(raw)
        except ValueError:
            answer = None
        if not isinstance(answer, dict) or "data" not in answer:
            raise Fault(
                "response_invalid", f"the answer is not the service's JSON; {NOT_THE_SERVICE}"
            )
        return answer
