from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

from . import links, settings, transport
from .errors import Fault
from .platforms import connection_fields, platform_problem
from .settings import (
    ACCOUNT_NAME,
    ACCOUNT_SECRET,
    ACCOUNT_VARIABLE,
    APP_TOKEN_VARIABLE,
    CERTIFICATE_VARIABLE,
    CHANNEL_ID,
    CHANNEL_PREFIX,
    CONFIG_PATH,
    GATEWAY_TOKEN_VARIABLE,
    GATEWAY_URL_VARIABLE,
    HEADER_SAFE,
    SECRET_COMMAND_VARIABLE,
    SECRET_PLACEHOLDER,
    SECRET_VARIABLE,
    SERVICE_TOKEN,
    URL_VARIABLE,
    VARIABLE_NAME,
    https_origin_problem,
)

EXAMPLE_PATH = Path(".smmtp", ".env.example")
TEMPLATE_SOURCE = Path(__file__).resolve().parent.parent / "config.template"
COMMENTED_SETTING = re.compile(r"#\s*([A-Z][A-Z0-9_]*)=")
SECRET_NAME = re.compile(r"TOKEN|SECRET|PASSWORD|KEY|WEBHOOK")


def parse_pairs(pairs):
    values = {}
    for number, pair in enumerate(pairs, 1):
        name, separator, value = pair.partition("=")
        if not separator or not VARIABLE_NAME.fullmatch(name) or re.search(r"[\r\n]", value):
            raise Fault(
                "config_value_invalid",
                f"--set number {number} is not NAME=value with an upper-case NAME and a one-line"
                " value; nothing was written",
            )
        values[name] = value.strip()
    if SERVICE_TOKEN.fullmatch(values.get(SECRET_VARIABLE, "")):
        values[ACCOUNT_VARIABLE], values[SECRET_VARIABLE] = values[SECRET_VARIABLE].split(":", 1)
    return values


def is_secret(name):
    return name != SECRET_COMMAND_VARIABLE and bool(SECRET_NAME.search(name))


def in_example(name):
    return name in (URL_VARIABLE, GATEWAY_URL_VARIABLE, SECRET_COMMAND_VARIABLE)


def line_name(line):
    name, separator, _ = line.partition("=")
    return name.strip() if separator and not line.lstrip().startswith("#") else None


def merge(text, values, overwrite):
    pending = dict(values)
    lines = text.splitlines()
    for index, line in enumerate(lines):
        name = line_name(line)
        if name in pending:
            value = pending.pop(name)
            if overwrite or not line.partition("=")[2].strip():
                lines[index] = f"{name}={value}"
    for index, line in enumerate(lines):
        example = COMMENTED_SETTING.match(line)
        if example and example.group(1) in pending:
            name = example.group(1)
            lines[index] = f"{name}={pending.pop(name)}"
    lines += [f"{name}={value}" for name, value in pending.items()]
    return "".join(f"{line}\n" for line in lines)


def planned_changes(text, values):
    lines = text.splitlines()
    current = {line_name(line): line.partition("=")[2].strip() for line in lines}
    examples = {match.group(1) for match in map(COMMENTED_SETTING.match, lines) if match}
    changes = []
    for name, value in values.items():
        if name not in current:
            action = "fill" if name in examples else "add"
        elif current[name]:
            action = "replace"
        else:
            action = "fill"
        secret = is_secret(name)
        changes.append(
            {"name": name, "action": action, "secret": secret, "value": None if secret else value}
        )
    return changes


def header_problem(value):
    if HEADER_SAFE.fullmatch(value):
        return None
    return "holds a space, a line break or a non-ASCII character, which a request header cannot carry"


def service_token_problem(value):
    if SERVICE_TOKEN.fullmatch(value):
        return None
    return "is not an app token the service issued"


def account_problem(value):
    if ACCOUNT_NAME.fullmatch(value):
        return None
    return "must be the account name as issued: lower-case Latin letters, digits and . _ -"


def account_secret_problem(value):
    if ACCOUNT_SECRET.fullmatch(value):
        return None
    return "must be the secret as issued: 32 characters, lower-case Latin letters and digits 2 to 7"


def channel_problem(value):
    if CHANNEL_ID.fullmatch(value):
        return None
    return "must be a channel ID, ch_ and 26 characters"


def webhook_problem(value):
    if links.webhook_parts(value):
        return None
    return "must be the whole Discord webhook URL, https://discord.com/api/webhooks/<id>/<token>"


def secret_command_problem(value):
    try:
        settings.parse_secret_command(value)
    except Fault:
        return f"must parse as a command and contain {SECRET_PLACEHOLDER} where the variable's name goes"
    return None


def certificate_problem(value):
    try:
        transport.opener(value)
    except Fault:
        return "names no certificate bundle that loads"
    return None


LOCAL_CHECKS = {
    URL_VARIABLE: https_origin_problem,
    GATEWAY_URL_VARIABLE: https_origin_problem,
    ACCOUNT_VARIABLE: account_problem,
    SECRET_VARIABLE: account_secret_problem,
    APP_TOKEN_VARIABLE: service_token_problem,
    GATEWAY_TOKEN_VARIABLE: header_problem,
    SECRET_COMMAND_VARIABLE: secret_command_problem,
    CERTIFICATE_VARIABLE: certificate_problem,
    "DISCORD_WEBHOOK_URL": webhook_problem,
}


def local_check(name):
    if name.startswith(CHANNEL_PREFIX):
        return channel_problem
    return LOCAL_CHECKS.get(name)


def validate(values, config, environ):
    problems, invalid, checked, unchecked = [], [], [], []
    platform = {}
    for name, value in values.items():
        if not value:
            continue
        check = local_check(name)
        if check:
            problem = check(value)
            if problem:
                problems.append(f"{name} {problem}")
                invalid.append(name)
        else:
            platform[name] = value
    if platform:
        try:
            lookup = settings.secret_lookup(environ, config)
            url, token = transport.service(lookup)
            cafile = environ.get(CERTIFICATE_VARIABLE) or config.get(CERTIFICATE_VARIABLE)
            fields = connection_fields(transport.capabilities(url, token, transport.opener(cafile)))
        except Fault:
            unchecked = list(platform)
        else:
            for name, value in platform.items():
                problem = platform_problem(name, value, fields)
                if problem:
                    problems.append(problem)
                    invalid.append(name)
                else:
                    checked.append(name)
    if problems:
        raise Fault(
            "config_value_invalid",
            f"{'; '.join(problems)}; nothing was written",
            invalid=invalid,
        )
    return checked, unchecked


def write_atomically(path, text, mode):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=f"{path.name}.")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(text)
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def update_file(path, values):
    current = settings.read_text(path)
    updated = merge(current, values, overwrite=True)
    settings.parse_config(updated, path)
    validate({name: value for name, value in values.items() if local_check(name)}, {}, {})
    try:
        write_atomically(path, updated, 0o600)
    except OSError as error:
        raise Fault(
            "config_unwritable",
            f"cannot write {path}: {error.strerror}; check that it is writable",
        )


def write_config(root, pairs, dry_run, environ):
    values = parse_pairs(pairs)
    root = Path(root).expanduser()
    if not root.is_dir():
        raise Fault(
            "config_unwritable",
            f"--root {root} is not a directory; pass the project root, or ~ for every project under it",
        )
    config, example = root / CONFIG_PATH, root / EXAMPLE_PATH
    current = settings.read_text(config if config.is_file() else TEMPLATE_SOURCE)
    settings.parse_config(current, config)
    updated = merge(current, values, overwrite=True)
    checked, unchecked = validate(values, settings.parse_config(updated, config), environ)
    report = {
        "ok": True,
        "dry_run": dry_run,
        "config": str(config),
        "changes": planned_changes(current, values),
        "checked": checked,
        "unchecked": unchecked,
    }
    if dry_run:
        return report
    shown = {name: value if in_example(name) else "" for name, value in values.items()}
    try:
        write_atomically(config, updated, 0o600)
        base = settings.read_text(example if example.is_file() else TEMPLATE_SOURCE)
        write_atomically(example, merge(base, shown, overwrite=False), 0o644)
    except OSError as error:
        raise Fault(
            "config_unwritable",
            f"cannot write under {root}: {error.strerror}; check that --root is the intended"
            " directory and writable",
        )
    return report
