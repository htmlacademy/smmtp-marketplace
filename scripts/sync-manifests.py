#!/usr/bin/env python3
"""Derive every manifest from the hand-edited `plugins/<name>/plugin.json`.

  plugins/<name>/.claude-plugin/plugin.json   Claude Code manifest
  .claude-plugin/marketplace.json             version, description, displayName, category per entry
  .agents/plugins/marketplace.json            Codex catalog

Only `source` in a catalog entry is hand-edited. The catalog `version` is the
release number and bumps once per run that changed an entry. A
`.codex-plugin/plugin.json` is an error: Codex ignores it once the root
manifest carries `extensions.com.openai`.

    python3 scripts/sync-manifests.py            # write, minor catalog bump
    python3 scripts/sync-manifests.py --patch    # write, patch catalog bump
    python3 scripts/sync-manifests.py --check    # verify only
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PLUGINS_DIR = REPO_ROOT / "plugins"
CATALOG = REPO_ROOT / ".claude-plugin" / "marketplace.json"
CODEX_CATALOG = REPO_ROOT / ".agents" / "plugins" / "marketplace.json"

CLAUDE_SCHEMA = "https://json.schemastore.org/claude-code-plugin-manifest.json"

# Agent Plugins 1.0.0 §5.2: closed schema, in spec order.
PORTABLE_FIELDS = (
    "name",
    "version",
    "description",
    "author",
    "homepage",
    "repository",
    "license",
    "keywords",
)
# §5.4.
AUTHOR_FIELDS = ("name", "email", "url")
# §5.5: 1-64 chars, [a-z0-9.-], alphanumeric ends, no `--` or `..`.
PORTABLE_NAME_RE = re.compile(r"^(?!.*(?:--|\.\.))[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?$")
SEMVER_RE = re.compile(r"\d+\.\d+\.\d+")

CODEX_NAMESPACE = "com.openai"
OBSOLETE_OVERLAY = Path(".codex-plugin") / "plugin.json"
# Codex requires a value and offers no "never"; no plugin here prompts at install.
CODEX_AUTHENTICATION = "ON_USE"


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def dump(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def interface(source: dict) -> dict:
    return source.get("extensions", {}).get(CODEX_NAMESPACE, {}).get("interface", {})


def claude_manifest(source: dict) -> dict:
    """Portable fields plus `displayName`, which the closed portable schema lacks."""
    manifest: dict = {"$schema": CLAUDE_SCHEMA, "name": source["name"]}
    if display_name := interface(source).get("displayName"):
        manifest["displayName"] = display_name
    for field in PORTABLE_FIELDS:
        if field == "name" or field not in source:
            continue
        value = source[field]
        if field == "author" and isinstance(value, dict):
            value = {key: value[key] for key in AUTHOR_FIELDS if key in value}
        manifest[field] = value
    return manifest


def catalog_entry(entry: dict, source: dict) -> dict:
    merged = dict(entry)
    merged["version"] = source["version"]
    merged["description"] = source["description"]
    if display_name := interface(source).get("displayName"):
        merged["displayName"] = display_name
    if category := interface(source).get("category"):
        merged["category"] = category.lower()
    return {key: merged[key] for key in entry_order(merged)}


def entry_order(entry: dict) -> list[str]:
    preferred = ["name", "displayName", "version", "description", "source", "category"]
    return [key for key in preferred if key in entry] + [
        key for key in entry if key not in preferred
    ]


def codex_catalog(catalog: dict) -> dict:
    """The Codex catalog; its shelf shows the marketplace name as is."""
    entries = []
    for entry in catalog["plugins"]:
        codex_entry = {
            "name": entry["name"],
            "source": {"source": "local", "path": entry["source"]},
            "policy": {
                "installation": "AVAILABLE",
                "authentication": CODEX_AUTHENTICATION,
            },
        }
        if category := entry.get("category"):
            codex_entry["category"] = category.title()
        entries.append(codex_entry)

    return {
        "name": catalog["name"],
        "interface": {"displayName": catalog["name"]},
        "plugins": entries,
    }


def bump(version: str, level: str) -> str:
    major, minor, patch = (int(part) for part in version.split("."))
    if level == "major":
        return f"{major + 1}.0.0"
    if level == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def reconcile(target: Path, payload: dict, *, check_only: bool, drift: list[str]) -> bool:
    """Write `payload` unless already present. True when it differed."""
    expected = dump(payload)
    label = target.relative_to(REPO_ROOT).as_posix()
    if target.is_file() and target.read_text(encoding="utf-8") == expected:
        print(f"  ok       {label}")
        return False

    state = "stale" if target.is_file() else "missing"
    if check_only:
        drift.append(label)
        print(f"  {state:8} {label}")
        return True
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(expected, encoding="utf-8")
    print(f"  written  {label} ({state})")
    return True


def read_sources(errors: list[str]) -> dict[str, dict]:
    sources: dict[str, dict] = {}
    for plugin in sorted(path for path in PLUGINS_DIR.iterdir() if path.is_dir()):
        manifest = plugin / "plugin.json"
        if not manifest.is_file():
            errors.append(f"plugins/{plugin.name}: no plugin.json")
            continue
        try:
            source = load(manifest)
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"plugins/{plugin.name}/plugin.json: unreadable ({exc})")
            continue
        name = source.get("name", "")
        if name != plugin.name:
            errors.append(f"plugins/{plugin.name}/plugin.json: name {name!r} must match its directory")
            continue
        if not PORTABLE_NAME_RE.fullmatch(name) or len(name) > 64:
            errors.append(f"plugins/{plugin.name}: name {name!r} violates Agent Plugins §5.5")
            continue
        if (plugin / OBSOLETE_OVERLAY).exists():
            errors.append(
                f"plugins/{plugin.name}/{OBSOLETE_OVERLAY.as_posix()}: obsolete, Codex reads"
                f" extensions.{CODEX_NAMESPACE} from plugin.json — delete it"
            )
        for required in ("version", "description"):
            if not source.get(required):
                errors.append(f"plugins/{plugin.name}/plugin.json: {required} is missing")
        sources[name] = source
    return sources


def report(errors: list[str]) -> bool:
    for error in errors:
        print(error, file=sys.stderr)
    return bool(errors)


def sync(*, check_only: bool, level: str) -> int:
    errors: list[str] = []
    drift: list[str] = []

    sources = read_sources(errors)
    if not sources:
        errors.append(f"no plugin manifests under {PLUGINS_DIR}")
    catalog = load(CATALOG)
    # Hand-edited fields: report a missing one as a typo, not a traceback.
    for required in ("name", "version", "plugins"):
        if not catalog.get(required):
            errors.append(f"{CATALOG.name}: {required} is missing")
    if (version := catalog.get("version")) and not SEMVER_RE.fullmatch(str(version)):
        errors.append(f"{CATALOG.name}: version {version!r} is not semver X.Y.Z")
    if not catalog.get("owner", {}).get("name"):
        errors.append(f"{CATALOG.name}: owner.name is missing, and Claude Code requires it")
    if report(errors):
        return 1

    catalogued = {entry["name"] for entry in catalog["plugins"]}

    for name in sorted(catalogued - sources.keys()):
        errors.append(f"{CATALOG.name}: entry {name!r} has no plugins/{name}/plugin.json")
    for name in sorted(sources.keys() - catalogued):
        errors.append(f"{CATALOG.name}: plugins/{name} is not catalogued")

    if report(errors):
        return 1

    for name, source in sorted(sources.items()):
        reconcile(
            PLUGINS_DIR / name / ".claude-plugin" / "plugin.json",
            claude_manifest(source),
            check_only=check_only,
            drift=drift,
        )

    updated = dict(catalog)
    updated["plugins"] = [catalog_entry(entry, sources[entry["name"]]) for entry in catalog["plugins"]]
    if updated["plugins"] != catalog["plugins"] and not check_only:
        updated["version"] = bump(catalog["version"], level)
        print(f"  bumped   marketplace version {catalog['version']} → {updated['version']} ({level})")
    reconcile(CATALOG, updated, check_only=check_only, drift=drift)
    reconcile(CODEX_CATALOG, codex_catalog(updated), check_only=check_only, drift=drift)

    if check_only and drift:
        print(
            f"\n{len(drift)} manifest(s) out of sync — run scripts/sync-manifests.py",
            file=sys.stderr,
        )
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="verify without writing, for CI")
    level = parser.add_mutually_exclusive_group()
    level.add_argument("--major", dest="level", action="store_const", const="major")
    level.add_argument("--minor", dest="level", action="store_const", const="minor")
    level.add_argument("--patch", dest="level", action="store_const", const="patch")
    parser.set_defaults(level="minor")
    args = parser.parse_args()
    return sync(check_only=args.check, level=args.level)


if __name__ == "__main__":
    raise SystemExit(main())
