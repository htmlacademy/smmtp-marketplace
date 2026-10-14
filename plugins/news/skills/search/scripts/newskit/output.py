"""What a result looks like in the caller's context: tab-separated rows, and a
file in the run directory for anything too long to print."""

from __future__ import annotations

import hashlib
import os
import unicodedata
from datetime import date
from pathlib import Path

from .errors import Fault

RUNS = "docs/.run"
RUN_ROOT = f"{RUNS}/news"
# Past this many characters a result goes to a file in the run directory:
# Bash shows about 30,000 inline and only a short preview beyond that.
INLINE_CHARS = 20000


def encodable(text: str) -> str:
    """JSON admits a lone surrogate that UTF-8 does not; replace it rather
    than fail to print a result that is already paid for."""
    return text.encode("utf-8", "replace").decode("utf-8")


def cell(value: object) -> str:
    """One value as one table cell: null is empty, a list is comma-joined, and
    a control character, which would break the row, becomes a space."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        value = ",".join(str(item) for item in value)
    text = "".join(" " if unicodedata.category(char) == "Cc" else char for char in str(value))
    return " ".join(text.split())


def table(fields: list, items: list) -> str:
    rows = ["\t".join(fields)]
    rows.extend("\t".join(cell(item.get(name)) for name in fields) for item in items)
    return "\n".join(rows)


def plain(text: str) -> str:
    """A text without the control characters a terminal would act on."""
    return "".join(
        char for char in text if char in "\n\t" or unicodedata.category(char) != "Cc"
    )


def digest(*parts: str) -> str:
    return hashlib.sha256("\0".join(parts).encode("utf-8", "replace")).hexdigest()[:12]


def project_root(cwd: Path) -> Path:
    """The nearest directory at or above cwd that holds .git, else the nearest
    one that already holds RUN_ROOT's docs/.run, else cwd."""
    chain = (cwd, *cwd.parents)
    for marker in (".git", RUNS):
        for directory in chain:
            if (directory / marker).exists():
                return directory
    return cwd


def run_file(cwd: Path, day: date, kind: str, name: str, text: str) -> str:
    """Write text under RUN_ROOT of the project, replacing the file whole, and
    return its absolute path."""
    target = project_root(cwd) / RUN_ROOT / f"{kind}-{day.isoformat()}" / name
    draft = target.with_name(name + ".part")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        draft.write_text(encodable(text), encoding="utf-8")
        os.replace(draft, target)
    except OSError as error:
        raise Fault(
            "output",
            f"cannot write {target}: {error.strerror or error}; the result is printed instead,"
            f" and the operator checks that {RUN_ROOT} in the project is writable",
        )
    return str(target.resolve())
