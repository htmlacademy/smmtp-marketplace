#!/usr/bin/env python3
"""Derive every installable copy of a skill from the plugin tree.

  plugins/<plugin>/skills/<skill>/scripts/   mirror of plugins/<plugin>/scripts/
  skills/<plugin>-<skill>/                   standalone copy for `npx skills`

`npx skills` copies only the skill directory, so scripts must live inside it.
A standalone copy has no plugin prefix, so it is renamed `<plugin>-<skill>`,
its `<plugin>:<skill>` references follow, and it drops `metadata.internal`,
which hides the plugin copy from `npx skills`.

    python3 scripts/sync-skills.py            # write
    python3 scripts/sync-skills.py --check    # verify only
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PLUGINS_DIR = REPO_ROOT / "plugins"
STANDALONE_DIR = REPO_ROOT / "skills"

JUNK_RE = re.compile(r"(^|/)(__pycache__(/|$)|\.DS_Store$|\._[^/]*$)|\.py[co]$")
INTERNAL_LINE = "  internal: true"
# Where a skill is named with Codex's `$skill` mention syntax.
MENTION_SUFFIXES = (".md", ".yaml")


def tree(root: Path) -> dict[str, bytes]:
    if not root.is_dir():
        return {}
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file() and not JUNK_RE.search(path.relative_to(root).as_posix())
    }


def standalone_name(plugin: str, skill: str) -> str:
    return f"{plugin}-{skill}"


def rename_references(text: str, plugin: str, skills: list[str], mentions: bool) -> str:
    for skill in skills:
        new = standalone_name(plugin, skill)
        text = re.sub(rf"(?<![\w-]){re.escape(plugin)}:{re.escape(skill)}(?![\w-])", new, text)
        if mentions:
            text = re.sub(rf"\${re.escape(skill)}(?![\w-])", f"${new}", text)
    return text


def standalone_frontmatter(text: str, plugin: str, skill: str) -> str:
    """Rename the skill and drop `internal: true`."""
    lines = text.split("\n")
    end = lines.index("---", 1)
    head = [line for line in lines[1:end] if line != INTERNAL_LINE]
    head = [
        f"name: {standalone_name(plugin, skill)}" if line == f"name: {skill}" else line
        for line in head
    ]
    if "metadata:" in head:
        index = head.index("metadata:")
        if index + 1 == len(head) or not head[index + 1].startswith(" "):
            del head[index]
    return "\n".join(["---", *head, *lines[end:]])


def standalone_file(relative: str, content: bytes, plugin: str, skill: str, skills: list[str]) -> bytes:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return content
    if relative == "SKILL.md":
        text = standalone_frontmatter(text, plugin, skill)
    text = rename_references(text, plugin, skills, relative.endswith(MENTION_SUFFIXES))
    return text.encode("utf-8")


def expected() -> tuple[dict[Path, dict[str, bytes]], list[str]]:
    """Each derived directory mapped to its expected files."""
    derived: dict[Path, dict[str, bytes]] = {}
    errors: list[str] = []
    for plugin in sorted(path for path in PLUGINS_DIR.iterdir() if path.is_dir()):
        shared = tree(plugin / "scripts")
        skill_dirs = sorted(path.parent for path in plugin.glob("skills/*/SKILL.md"))
        names = [path.name for path in skill_dirs]
        for skill_dir in skill_dirs:
            files = {
                relative: content
                for relative, content in tree(skill_dir).items()
                if not (shared and relative.startswith("scripts/"))
            }
            if shared:
                derived[skill_dir / "scripts"] = shared
                files.update({f"scripts/{relative}": content for relative, content in shared.items()})
            if INTERNAL_LINE not in files["SKILL.md"].decode("utf-8").split("---", 2)[1].split("\n"):
                errors.append(
                    f"{skill_dir.relative_to(REPO_ROOT)}/SKILL.md: needs `metadata:` with"
                    f" `internal: true`, or `npx skills` lists it beside its standalone copy"
                )
            target = STANDALONE_DIR / standalone_name(plugin.name, skill_dir.name)
            derived[target] = {
                relative: standalone_file(relative, content, plugin.name, skill_dir.name, names)
                for relative, content in files.items()
            }
    return derived, errors


def reconcile(root: Path, files: dict[str, bytes], *, check_only: bool) -> bool:
    """Make `root` hold exactly `files`. True when it differed."""
    actual = tree(root)
    label = root.relative_to(REPO_ROOT).as_posix()
    if actual == files:
        print(f"  ok       {label}")
        return False
    stale = sorted(name for name in actual.keys() | files.keys() if actual.get(name) != files.get(name))
    print(f"  {'stale' if check_only else 'written':8} {label} ({len(stale)} file(s))")
    if check_only:
        return True
    for name in stale:
        path = root / name
        if name in files:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(files[name])
        else:
            path.unlink()
    for directory in sorted((path for path in root.rglob("*") if path.is_dir()), reverse=True):
        if not any(directory.iterdir()):
            directory.rmdir()
    return True


def sync(*, check_only: bool) -> int:
    derived, errors = expected()
    for error in errors:
        print(error, file=sys.stderr)
    if errors:
        return 1

    drift = [root for root, files in derived.items() if reconcile(root, files, check_only=check_only)]

    orphans = sorted(
        path for path in STANDALONE_DIR.iterdir() if path.is_dir() and path not in derived
    ) if STANDALONE_DIR.is_dir() else []
    for orphan in orphans:
        label = orphan.relative_to(REPO_ROOT).as_posix()
        if check_only:
            print(f"  orphan   {label}")
        else:
            shutil.rmtree(orphan)
            print(f"  removed  {label}")

    if check_only and (drift or orphans):
        print(
            f"\n{len(drift) + len(orphans)} skill copies out of sync — run scripts/sync-skills.py",
            file=sys.stderr,
        )
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="verify without writing, for CI")
    return sync(check_only=parser.parse_args().check)


if __name__ == "__main__":
    raise SystemExit(main())
