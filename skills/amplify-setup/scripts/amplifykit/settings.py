"""Where a variable's value comes from: the environment, then the nearest
.amplify/.config, then the secret command."""

from __future__ import annotations

import re
import shlex
import subprocess
import urllib.parse
from pathlib import Path

from .errors import Fault

CONFIG_PATH = Path(".amplify", ".config")
URL_VARIABLE = "AMPLIFY_REST_URL"
TOKEN_VARIABLE = "AMPLIFY_REST_TOKEN"
GATEWAY_URL_VARIABLE = "AMPLIFY_MEDIA_GATEWAY_URL"
GATEWAY_TOKEN_VARIABLE = "AMPLIFY_MEDIA_GATEWAY_TOKEN"
SECRET_COMMAND_VARIABLE = "AMPLIFY_REST_SECRET_COMMAND"
CERTIFICATE_VARIABLE = "SSL_CERT_FILE"
SECRET_PLACEHOLDER = "{name}"
# A locked keychain asks the operator for its password in a dialog, and the
# store answers only after that.
SECRET_TIMEOUT_SECONDS = 120
VARIABLE_NAME = re.compile(r"[A-Z][A-Z0-9_]*")
# http.client refuses a header value holding a line break or a non-Latin-1
# character with an error that quotes the whole value, the token included.
HEADER_SAFE = re.compile(r"[\x21-\x7e]+")


def https_origin_problem(value):
    """Why value is not an https origin without a path or userinfo, the form
    the service takes for itself and for a media gateway; None when it is."""
    parts = urllib.parse.urlsplit(value)
    if parts.scheme != "https" or not parts.netloc or not HEADER_SAFE.fullmatch(value):
        return "must be an https:// URL of visible ASCII characters"
    if "@" in parts.netloc:
        return "must not carry a user name or password"
    if parts.path not in ("", "/") or parts.query or parts.fragment:
        return "must be the root, without a path, query or fragment"
    return None


def find_config(start):
    for directory in (start, *start.parents):
        candidate = directory / CONFIG_PATH
        if candidate.is_file():
            return candidate
    return None


def read_text(path):
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise Fault("config_unreadable", f"{path} is not UTF-8 text; the operator saves it as UTF-8")
    except OSError as error:
        raise Fault("config_unreadable", f"cannot read {path}: {error.strerror}; check its permissions")


def read_config(path):
    return parse_config(read_text(path), path)


def parse_config(text, path):
    """Parse NAME=value lines. An error names the line by number and never
    quotes it, since the line may hold a secret."""
    values = {}
    for number, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name, separator, value = line.partition("=")
        name = name.strip()
        if not separator or not VARIABLE_NAME.fullmatch(name):
            raise Fault(
                "config_invalid",
                f"{path}:{number} is not NAME=value with an upper-case NAME; the operator corrects"
                " that line",
            )
        if name in values:
            raise Fault(
                "config_invalid",
                f"{path}:{number} sets {name} a second time; the operator keeps one of the two",
            )
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[name] = value
    return values


class Lookup:
    """A variable's value and its source: the environment, then the config
    file, then the secret command. Each name is resolved once, so a store
    that prompts for its password prompts once."""

    def __init__(self, environ, config, command):
        self.environ = environ
        self.config = config
        self.command = command
        self.resolved = {}

    def resolve(self, name):
        if name not in self.resolved:
            if self.environ.get(name):
                found = self.environ[name], "environment"
            elif self.config.get(name):
                found = self.config[name], "config"
            else:
                value = read_secret(self.command, name) if self.command else None
                found = (value, "store") if value else (None, None)
            self.resolved[name] = found
        return self.resolved[name]

    def __call__(self, name):
        return self.resolve(name)[0]

    def source(self, name):
        return self.resolve(name)[1]


def secret_lookup(environ, config=None):
    config = config or {}
    command = parse_secret_command(
        environ.get(SECRET_COMMAND_VARIABLE) or config.get(SECRET_COMMAND_VARIABLE, "")
    )
    return Lookup(environ, config, command)


def parse_secret_command(text):
    try:
        command = shlex.split(text)
    except ValueError as error:
        raise Fault(
            "secret_command_invalid",
            f"{SECRET_COMMAND_VARIABLE}: {error}; the operator corrects its quoting",
        )
    if command and not any(SECRET_PLACEHOLDER in part for part in command):
        raise Fault(
            "secret_command_invalid",
            f"{SECRET_COMMAND_VARIABLE} must contain {SECRET_PLACEHOLDER}; the operator adds it"
            " where the variable's name goes",
        )
    return command


def read_secret(command, name):
    if not VARIABLE_NAME.fullmatch(name):
        raise Fault(
            "variable_name_invalid",
            f"not a variable name: {name!r}; the service's credential schema names a field"
            " this script cannot map to a variable, so report it",
        )
    try:
        completed = subprocess.run(
            [part.replace(SECRET_PLACEHOLDER, name) for part in command],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=SECRET_TIMEOUT_SECONDS,
        )
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        raise Fault(
            "secret_command_failed",
            f"{SECRET_COMMAND_VARIABLE} for {name}: {error}; the operator runs the command in a"
            " terminal and fixes it",
        )
    if completed.returncode != 0:
        return None
    return completed.stdout.removesuffix("\n") or None


def load(environ, cwd):
    """The config file in effect, its values, the variable lookup, and the
    certificate bundle the environment or the file names."""
    path = find_config(cwd)
    config = read_config(path) if path else {}
    cafile = environ.get(CERTIFICATE_VARIABLE) or config.get(CERTIFICATE_VARIABLE)
    return path, config, secret_lookup(environ, config), cafile
