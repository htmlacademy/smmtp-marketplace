"""Where the service's address and the access key come from: the nearest
.env.local at or above the working directory that sets the key."""

from __future__ import annotations

import re
import subprocess
import urllib.parse
from pathlib import Path
from typing import NamedTuple

from .errors import Fault

KEY_VARIABLE = "MELANGE_KEY"
URL_VARIABLE = "MELANGE_URL"
CERTIFICATE_VARIABLE = "SSL_CERT_FILE"
KEY_FILE = ".env.local"
GIT_TIMEOUT_SECONDS = 10
# http.client refuses a header value holding a line break or a non-Latin-1
# character with an error that quotes the whole value, the key included.
HEADER_SAFE = re.compile(r"[\x21-\x7e]+")


class Access(NamedTuple):
    key: str
    url: str
    path: Path
    cafile: str | None


def read_value(text: str, variable: str) -> str | None:
    """The value the last line assigning `variable` gives it. Every other
    line belongs to someone else and is neither validated nor reported."""
    value = None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        name, separator, raw = line.partition("=")
        if not separator or name.strip() != variable:
            continue
        raw = raw.strip()
        closing = raw.find(raw[0], 1) if raw[:1] in ("'", '"') else -1
        value = raw[1:closing] if closing > 0 else raw.split(" #", 1)[0].rstrip()
    return value or None


def find_key(cwd: Path) -> Access:
    for directory in (cwd, *cwd.parents):
        path = directory / KEY_FILE
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError:
            raise Fault(
                "key_file_unreadable", f"{path} is not UTF-8 text; the operator saves it as UTF-8"
            )
        except OSError as error:
            raise Fault(
                "key_file_unreadable",
                f"cannot read {path}: {error.strerror}; the operator checks its permissions",
            )
        key = read_value(text, KEY_VARIABLE)
        if key is None:
            continue
        if not HEADER_SAFE.fullmatch(key):
            raise Fault(
                "key_invalid",
                f"{KEY_VARIABLE} in {path} holds a space or a non-ASCII character; the operator"
                " stores the key on one line, exactly as it was issued",
            )
        url = read_value(text, URL_VARIABLE)
        if url is None:
            raise Fault(
                "setup_required",
                f"{path} sets {KEY_VARIABLE} and no {URL_VARIABLE}; the operator adds the line"
                f' {URL_VARIABLE}="https://<service address>"',
            )
        return Access(key, service_url(url, path), path, read_value(text, CERTIFICATE_VARIABLE))
    raise Fault(
        "setup_required",
        f"no {KEY_FILE} at or above {cwd} sets {KEY_VARIABLE}; the operator adds the lines"
        f' {URL_VARIABLE}="https://<service address>" and {KEY_VARIABLE}="<key>" to {KEY_FILE}'
        " in the project root and keeps that file out of git; both values come from https://smmtp.ru or mail@smmtp.ru",
    )


def service_url(text: str, path: Path) -> str:
    """The address without a trailing slash. Only https: the key travels in a
    header of every request."""
    try:
        parts = urllib.parse.urlsplit(text)
        sound = parts.scheme == "https" and parts.hostname and parts.port != 0
    except ValueError:
        sound = False
    if not sound or parts.username or parts.password or parts.query or parts.fragment:
        raise Fault(
            "url_invalid",
            f"{URL_VARIABLE} in {path} is not an https address of the form"
            " https://host[:port][/path]; the operator corrects it",
        )
    return text.rstrip("/")


def git_state(path: Path) -> str:
    """How git treats the key file: tracked, ignored, unignored, or outside
    (no repository, or no git to ask)."""

    def git(*arguments: str) -> int | None:
        try:
            return subprocess.run(
                ["git", "-C", str(path.parent), *arguments, path.name],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=GIT_TIMEOUT_SECONDS,
            ).returncode
        except (OSError, subprocess.TimeoutExpired):
            return None

    if git("ls-files", "--error-unmatch", "--") == 0:
        return "tracked"
    return {0: "ignored", 1: "unignored"}.get(git("check-ignore", "--quiet", "--"), "outside")
