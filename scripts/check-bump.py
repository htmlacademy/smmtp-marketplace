#!/usr/bin/env python3
"""Fail when a change edits a plugin or the catalog without bumping its version.

  changed plugin files      -> new `version` in plugins/<name>/plugin.json
  changed catalog entries   -> new top-level catalog `version` (the release tag)

Compares the working tree against the merge base with `--base`.

    python3 scripts/check-bump.py                 # against origin/main
    python3 scripts/check-bump.py --base HEAD~1
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PLUGINS_DIR = REPO_ROOT / "plugins"
CATALOG_PATH = ".claude-plugin/marketplace.json"


class GitUnavailable(RuntimeError):
    """The base revision cannot be read."""


def git(*args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(REPO_ROOT), *args],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise GitUnavailable(f"git {' '.join(args)}: {result.stderr.strip()}")
    return result.stdout


def file_at(revision: str, path: str) -> dict | None:
    """JSON at `path` in `revision`; None when absent there."""
    try:
        return json.loads(git("show", f"{revision}:{path}"))
    except GitUnavailable:
        return None
    except json.JSONDecodeError as exc:
        raise GitUnavailable(f"{path} at {revision} is not valid JSON ({exc})") from exc


def changed_paths(base: str) -> set[str]:
    return {line for line in git("diff", "--name-only", base).splitlines() if line}


def previous_version(base: str, prefix: str) -> str | None:
    """Plugin version at `base`, from the portable or the Claude manifest."""
    for path in (f"{prefix}plugin.json", f"{prefix}.claude-plugin/plugin.json"):
        if (manifest := file_at(base, path)) is not None:
            return manifest.get("version")
    return None


def plugin_findings(base: str, changed: set[str]) -> list[str]:
    findings = []
    for plugin in sorted(path for path in PLUGINS_DIR.iterdir() if path.is_dir()):
        prefix = f"plugins/{plugin.name}/"
        if not any(path.startswith(prefix) for path in changed):
            continue
        previous = previous_version(base, prefix)
        if previous is None:
            continue
        current = json.loads((plugin / "plugin.json").read_text(encoding="utf-8"))
        if previous == current.get("version"):
            findings.append(
                f"{plugin.name}: files changed but version is still "
                f"{current.get('version')!r} — bump it in {prefix}plugin.json"
            )
    return findings


def catalog_findings(base: str, changed: set[str]) -> list[str]:
    if CATALOG_PATH not in changed:
        return []
    previous = file_at(base, CATALOG_PATH)
    if previous is None:
        return []
    current = json.loads((REPO_ROOT / CATALOG_PATH).read_text(encoding="utf-8"))
    if previous.get("plugins") == current.get("plugins"):
        return []
    if previous.get("version") == current.get("version"):
        return [
            f"marketplace: entries changed but version is still "
            f"{current.get('version')!r} — run scripts/sync-manifests.py"
        ]
    return []


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base", default="origin/main", help="revision to compare against")
    args = parser.parse_args()

    try:
        base = git("merge-base", args.base, "HEAD").strip()
        changed = changed_paths(base)
    except GitUnavailable as exc:
        print(f"check-bump: {exc}", file=sys.stderr)
        print("check-bump: fetch enough history for the base revision", file=sys.stderr)
        return 2

    findings = plugin_findings(base, changed) + catalog_findings(base, changed)
    for finding in findings:
        print(f"ERROR: {finding}")
    if findings:
        return 1
    print(f"check-bump: every change since {base[:12]} carries a version bump")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
