"""config: write .amplify/.config and its example, checking every value
first, and keep the file out of git."""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
from pathlib import Path

from . import settings, transport
from .errors import Fault
from .platforms import credential_fields, platform_problem
from .settings import (
    CERTIFICATE_VARIABLE,
    CONFIG_PATH,
    GATEWAY_TOKEN_VARIABLE,
    GATEWAY_URL_VARIABLE,
    HEADER_SAFE,
    SECRET_COMMAND_VARIABLE,
    SECRET_PLACEHOLDER,
    TOKEN_VARIABLE,
    URL_VARIABLE,
    VARIABLE_NAME,
    https_origin_problem,
)

TEMPLATE_PATH = Path(".amplify", ".config.local")
TEMPLATE_SOURCE = Path(__file__).resolve().parent.parent / "config.template"
COMMENTED_SETTING = re.compile(r"#\s*([A-Z][A-Z0-9_]*)=")
SECRET_NAME = re.compile(r"TOKEN|SECRET|PASSWORD|KEY|WEBHOOK")
GIT_TIMEOUT_SECONDS = 10


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
    return values


def is_secret(name):
    return name != SECRET_COMMAND_VARIABLE and bool(SECRET_NAME.search(name))


def in_example(name):
    return name.endswith("_URL") or name == SECRET_COMMAND_VARIABLE


def line_name(line):
    name, separator, _ = line.partition("=")
    return name.strip() if separator and not line.lstrip().startswith("#") else None


def merge(text, values, overwrite):
    """Put each value on the line that sets its name, or on the first
    commented-out example of it, or on a new line at the end. Without
    overwrite, a line that already holds a value keeps it."""
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
    """The action merge() takes for each name: fill an empty or commented-out
    line, replace a value, or add a line."""
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
    TOKEN_VARIABLE: header_problem,
    GATEWAY_TOKEN_VARIABLE: header_problem,
    SECRET_COMMAND_VARIABLE: secret_command_problem,
    CERTIFICATE_VARIABLE: certificate_problem,
}


def validate(values, config, environ):
    """Refuse every value the script could not use, before anything is
    written. Platform values are checked against the live schema when the
    service answers with the settings as they will be; otherwise they are
    returned as unchecked."""
    problems, invalid, checked, unchecked = [], [], [], []
    platform = {}
    for name, value in values.items():
        if not value:
            continue
        if name in LOCAL_CHECKS:
            problem = LOCAL_CHECKS[name](value)
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
            capabilities = transport.call(
                url, token, "GET", "/v1/capabilities", opener=transport.opener(cafile),
                timeout=transport.CHECK_TIMEOUT_SECONDS,
            )
            fields = credential_fields(transport.strategy_schemas(capabilities))
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


def git_answer(root, *arguments):
    """True or False as a git query exits 0 or 1, None outside a repository or
    without git."""
    try:
        completed = subprocess.run(
            ["git", *arguments],
            cwd=root,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return {0: True, 1: False}.get(completed.returncode)


def git_state(root):
    """Whether git ignores root/.amplify/.config, and whether it already
    tracks it."""
    relative = CONFIG_PATH.as_posix()
    return (
        git_answer(root, "check-ignore", "-q", relative),
        git_answer(root, "ls-files", "--error-unmatch", relative),
    )


def append_line(path, line):
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    if text and not text.endswith("\n"):
        text += "\n"
    write_atomically(path, f"{text}{line}\n", 0o644)


def write_config(root, pairs, dry_run, environ):
    values = parse_pairs(pairs)
    root = Path(root).expanduser()
    if not root.is_dir():
        raise Fault(
            "config_unwritable",
            f"--root {root} is not a directory; pass the project root, or ~ for every project",
        )
    config, example = root / CONFIG_PATH, root / TEMPLATE_PATH
    created = not config.is_file()
    current = settings.read_text(TEMPLATE_SOURCE if created else config)
    settings.parse_config(current, config)
    updated = merge(current, values, overwrite=True)
    checked, unchecked = validate(values, settings.parse_config(updated, config), environ)
    ignored, tracked = git_state(root)
    gitignore = None if ignored is None else {"path": str(root / ".gitignore"), "add": not ignored}
    report = {
        "ok": True,
        "dry_run": dry_run,
        "config": str(config),
        "created": created,
        "template": str(example),
        "changes": planned_changes(current, values),
        "checked": checked,
        "unchecked": unchecked,
        "gitignore": gitignore,
        "git_ignored": ignored,
        "git_tracked": tracked,
    }
    if dry_run:
        return report
    shown = {name: value if in_example(name) else "" for name, value in values.items()}
    try:
        write_atomically(config, updated, 0o600)
        base = settings.read_text(example if example.is_file() else TEMPLATE_SOURCE)
        write_atomically(example, merge(base, shown, overwrite=False), 0o644)
        if gitignore and gitignore["add"]:
            append_line(root / ".gitignore", CONFIG_PATH.as_posix())
    except OSError as error:
        raise Fault(
            "config_unwritable",
            f"cannot write under {root}: {error.strerror}; check that --root is the intended"
            " directory and writable",
        )
    report["git_ignored"], report["git_tracked"] = git_state(root)
    return report
