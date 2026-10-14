#!/usr/bin/env python3
"""Lint the plugin tree: manifests, component frontmatter, Codex cards, tracked junk.

Covers what `claude plugin validate --strict` passes: a `description` containing
`: `, unknown frontmatter keys, a `name` that differs from its directory, an
overlong description. Catalog parity belongs to sync-manifests.py, skill copies
to sync-skills.py.

Exit: 0 clean, 1 errors, 2 usage.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PLUGINS_DIR = REPO_ROOT / "plugins"

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")
# Reserved by the Agent Skills spec.
RESERVED = ("claude", "anthropic")
JUNK_RE = re.compile(r"(^|/)(__pycache__/|\.DS_Store$|\._[^/]*$)|\.py[co]$")

PORTABLE_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
CODEX_NAMESPACE = "com.openai"

CORE_FIELDS = ("name", "version", "description")
ACTIVE_FIELDS = ("author.name", "author.email", "repository", "license")
# The catalog entry derives from these.
INTERFACE_FIELDS = ("displayName", "category")

# Documented Claude Code frontmatter keys; anything else is a typo.
SKILL_KEYS = frozenset({
    "name", "description", "when_to_use", "argument-hint", "arguments",
    "disable-model-invocation", "user-invocable", "allowed-tools",
    "disallowed-tools", "model", "effort", "context", "agent", "background",
    "hooks", "paths", "shell", "metadata", "license", "compatibility",
})
AGENT_KEYS = frozenset({
    "name", "description", "tools", "disallowedTools", "model", "effort",
    "maxTurns", "skills", "memory", "background", "isolation", "color",
    "permissionMode", "mcpServers", "hooks", "initialPrompt",
})

# Agent `tools:` takes tool names; the commas inside Bash(...) split the list.
AGENT_TOOL_PATTERN_RE = re.compile(r"\bBash\s*\(")

# Stricter than the platform's 1024: every installed skill shares the listing budget.
DESCRIPTION_MAX = 200
BANNED_IN_DESCRIPTION = ("—", "–")

FM_KEY_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*):(?:[ \t]+(.*))?$")

# Without agents/openai.yaml a skill is invisible in Codex. Keys per codex-cli 0.154.
CODEX_AGENT_KEYS = frozenset({"interface", "dependencies", "policy"})
CODEX_INTERFACE_KEYS = frozenset({
    "display_name", "short_description", "long_description", "default_prompt",
    "brand_color", "composer_icon", "screenshots", "website_url",
})
CODEX_INTERFACE_REQUIRED = ("display_name", "short_description")


def field(data: dict, path: str) -> str:
    """Dotted lookup; '' when absent or not a string."""
    value: object = data
    for key in path.split("."):
        value = value.get(key) if isinstance(value, dict) else None
    return value.strip() if isinstance(value, str) else ""


def check_manifest(manifest: Path) -> list[str]:
    label = manifest.relative_to(REPO_ROOT)
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"{label}: unreadable or invalid JSON ({exc})"]

    errors = [f"{label}: {required} is missing or empty"
              for required in CORE_FIELDS if not field(data, required)]
    errors += [f"{label}: {required} is missing or empty"
               for required in ACTIVE_FIELDS if not field(data, required)]

    if data.get("$schema") != PORTABLE_SCHEMA:
        errors.append(f"{label}: $schema must be {PORTABLE_SCHEMA}")
    if name := field(data, "name"):
        if not NAME_RE.match(name):
            errors.append(f"{label}: name {name!r} must match {NAME_RE.pattern}")
        elif reserved := next((word for word in RESERVED if word in name.lower()), None):
            errors.append(f"{label}: name {name!r} contains reserved word {reserved!r}")
    if (version := field(data, "version")) and not SEMVER_RE.match(version):
        errors.append(f"{label}: version {version!r} is not semver X.Y.Z")
    # The namespace is one key containing a dot, so not via field().
    interface = data.get("extensions", {}).get(CODEX_NAMESPACE, {}).get("interface", {})
    errors += [f"{label}: extensions.{CODEX_NAMESPACE}.interface.{required} is missing"
               for required in INTERFACE_FIELDS if not field(interface, required)]
    return errors


def split_frontmatter(text: str) -> list[str] | None:
    """Frontmatter lines, or None without a block."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    for index in range(1, len(lines)):
        if lines[index].strip() in ("---", "..."):
            return lines[1:index]
    return None


def parse_frontmatter(body: list[str]) -> tuple[dict[str, dict], list[str]]:
    """Strictly parse top-level keys, recording each value's form.

    Forms: block, quoted, plain, nested, empty. Any other shape is an error.
    """
    entries: dict[str, dict] = {}
    errors: list[str] = []
    index = 0
    while index < len(body):
        raw = body[index]
        line_no = index + 1
        if not raw.strip() or raw.lstrip().startswith("#"):
            index += 1
            continue
        if raw[:1] in (" ", "\t"):
            errors.append(f"line {line_no}: indented line belongs to no key")
            index += 1
            continue
        match = FM_KEY_RE.match(raw)
        if not match:
            errors.append(f"line {line_no}: not a `key:` or `key: value` line ({raw.strip()!r})")
            index += 1
            continue

        key, value = match.group(1), (match.group(2) or "").strip()
        if key in entries:
            errors.append(f"line {line_no}: duplicate key {key!r}")

        nested: list[str] = []
        cursor = index + 1
        while cursor < len(body) and (not body[cursor].strip() or body[cursor][:1] in (" ", "\t")):
            if body[cursor].strip():
                if body[cursor].startswith("\t"):
                    errors.append(f"line {cursor + 1}: YAML forbids a tab indent")
                nested.append(body[cursor])
            cursor += 1

        if value.startswith((">", "|")):
            form = "block"
        elif value.startswith(("'", '"')):
            form = "quoted"
        elif value:
            form = "plain"
        elif nested:
            form = "nested"
        else:
            form = "empty"

        entries[key] = {
            "form": form,
            "value": value,
            "line": line_no,
            "nested": nested,
            "items": [line.strip()[2:].strip() for line in nested if line.strip().startswith("- ")],
        }
        index = cursor
    return entries, errors


def check_description(entries: dict, label: Path) -> list[str]:
    description = entries.get("description")
    if description is None or description["form"] == "empty":
        return [f"{label}: description is missing or empty"]

    errors: list[str] = []
    value = description["value"]
    if description["form"] == "block":
        errors.append(f"{label}: description must be an inline scalar, not a block scalar")
    elif description["form"] == "quoted":
        errors.append(f"{label}: description must be an unquoted inline scalar")
    elif description["nested"]:
        errors.append(f"{label}: description must fit on one line")
    # `: ` in a plain scalar truncates silently; quoting would only hide it.
    if ": " in value:
        errors.append(f"{label}: description must not contain ': '")
    if len(value) > DESCRIPTION_MAX:
        errors.append(f"{label}: description is {len(value)} characters, limit is {DESCRIPTION_MAX}")
    if any(dash in value for dash in BANNED_IN_DESCRIPTION):
        errors.append(f"{label}: description must not contain an em or en dash")
    return errors


def check_component(path: Path, kind: str) -> list[str]:
    label = path.relative_to(REPO_ROOT)
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return [f"{label}: unreadable ({exc})"]

    body = split_frontmatter(text)
    if body is None:
        return [f"{label}: no YAML frontmatter block"]

    entries, parse_errors = parse_frontmatter(body)
    errors = [f"{label}: {message}" for message in parse_errors]

    permitted = SKILL_KEYS if kind == "skill" else AGENT_KEYS
    errors += [f"{label}: unknown frontmatter key {key!r}"
               for key in entries if key not in permitted]

    expected = path.parent.name if kind == "skill" else path.stem
    name = entries.get("name")
    if name and name["form"] in ("plain", "quoted"):
        declared = name["value"].strip("'\"")
        if declared != expected:
            errors.append(f"{label}: name {declared!r} must match {expected!r}")

    errors += check_description(entries, label)

    tools_key = "allowed-tools" if kind == "skill" else "tools"
    if (tools := entries.get(tools_key)) is not None:
        if tools["form"] == "empty":
            errors.append(f"{label}: {tools_key} is present but empty")
        elif tools["form"] == "nested" and not tools["items"]:
            errors.append(f"{label}: {tools_key} block is not a list of strings")
        if kind == "agent" and AGENT_TOOL_PATTERN_RE.search(tools["value"]):
            errors.append(f"{label}: tools takes plain tool names; write 'Bash', not a Bash(...) pattern")

    return errors


def check_codex_agent(skill: Path) -> list[str]:
    """Check agents/openai.yaml by line shape, keeping the tooling dependency-free."""
    path = skill / "agents" / "openai.yaml"
    label = path.relative_to(REPO_ROOT)
    if not path.is_file():
        return [
            f"{skill.relative_to(REPO_ROOT)}: no agents/openai.yaml, so Codex has no "
            f"display name, summary or starter prompt for this skill"
        ]

    lines = path.read_text(encoding="utf-8").splitlines()
    top = [line.split(":", 1)[0] for line in lines if re.match(r"^[a-z_]+:", line)]
    interface = [
        line.strip().split(":", 1)[0]
        for index, line in enumerate(lines)
        if re.match(r"^  [a-z_]+:", line) and last_top_key(lines, index) == "interface"
    ]

    errors = [f"{label}: unknown top-level key {key!r}" for key in top if key not in CODEX_AGENT_KEYS]
    if "interface" not in top:
        return errors + [f"{label}: no interface block"]
    errors += [f"{label}: unknown interface key {key!r}"
               for key in interface if key not in CODEX_INTERFACE_KEYS]
    errors += [f"{label}: interface.{required} is missing"
               for required in CODEX_INTERFACE_REQUIRED if required not in interface]
    return errors


def last_top_key(lines: list[str], index: int) -> str:
    for line in reversed(lines[:index]):
        if re.match(r"^[a-z_]+:", line):
            return line.split(":", 1)[0]
    return ""


def tracked_junk() -> list[str]:
    """Junk tracked by git; ignored local files are not reported."""
    try:
        listing = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "ls-files"],
            capture_output=True, text=True, check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        print("note: git unavailable, skipping tracked-junk check", file=sys.stderr)
        return []
    return [path for path in listing.splitlines() if JUNK_RE.search(path)]


def main() -> int:
    if not PLUGINS_DIR.is_dir():
        print(f"error: {PLUGINS_DIR} not found", file=sys.stderr)
        return 2

    errors: list[str] = []
    skills: list[Path] = []
    plugins = sorted(path for path in PLUGINS_DIR.iterdir() if path.is_dir())

    for plugin in plugins:
        manifest = plugin / "plugin.json"
        if not manifest.is_file():
            errors.append(f"{plugin.relative_to(REPO_ROOT)}: missing plugin.json")
            continue
        errors += check_manifest(manifest)

        for skill_file in sorted(plugin.glob("skills/*/SKILL.md")):
            skills.append(skill_file.parent)
            errors += check_component(skill_file, "skill")
            errors += check_codex_agent(skill_file.parent)
        for agent in sorted(plugin.glob("agents/*.md")):
            errors += check_component(agent, "agent")

    errors += [f"{path}: tracked junk file" for path in tracked_junk()]

    for message in errors:
        print(f"ERROR: {message}")
    print()
    print(f"checked {len(plugins)} plugins and {len(skills)} skills: {len(errors)} error(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
