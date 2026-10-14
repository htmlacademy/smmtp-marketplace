# smmtp marketplace

Public plugin marketplace `smmtp` for Codex, Claude Code and Claude Cowork; every skill also installs standalone via `npx skills`. Nothing may depend on a single harness.

## Layout

```
plugins/<p>/plugin.json                       source of truth (Agent Plugins 1.0.0), hand-edited
plugins/<p>/scripts/                          plugin scripts, hand-edited
plugins/<p>/skills/<s>/SKILL.md               skill
plugins/<p>/skills/<s>/agents/openai.yaml     Codex skill card, required
plugins/<p>/skills/<s>/references/            on-demand material
plugins/<p>/skills/<s>/scripts/               generated copy of plugins/<p>/scripts/
plugins/<p>/.claude-plugin/plugin.json        generated
skills/<p>-<s>/                               generated standalone skill for npx skills
.claude-plugin/marketplace.json               catalog; only `source` per entry is hand-edited
.agents/plugins/marketplace.json              generated Codex catalog
```

Never edit generated files; CI fails on drift.

## Invariants

- Codex metadata lives in `plugin.json` under `extensions.com.openai.interface`; `displayName` and `category` propagate from there. Never create `.codex-plugin/plugin.json`: Codex ignores it once the root manifest has `extensions.com.openai`.
- A skill calls its scripts from its own directory: `${CLAUDE_SKILL_DIR}/scripts/...` in `allowed-tools`, `<skill directory>/scripts/...` in the body (Codex does not substitute variables).
- Plugin skills carry `metadata.internal: true` so `npx skills` lists only the standalone `<p>-<s>` copies; `sync-skills.py` rewrites `<p>:<s>` references in them.
- Removing or renaming a plugin requires a `renames` entry in the same change.
- `SKILL.md` frontmatter: `name` matches the directory; `description` is an unquoted single-line plain scalar, at most 200 characters, with no `: ` and no em or en dash; only documented keys. Names never contain `claude` or `anthropic`.
- Language: human-facing text (READMEs, skill descriptions, `openai.yaml` cards, manifest descriptions) is Russian; `SKILL.md` bodies, references, scripts and this file are English.

## Change procedure

1. Edit the plugin and bump `version` in `plugins/<p>/plugin.json`.
2. Regenerate:

```bash
python3 scripts/sync-skills.py
python3 scripts/sync-manifests.py   # also bumps the catalog version: minor by default, --major/--patch
```

The catalog `version` is the release number: CI on `main` tags `v<version>` and publishes a release.

## Gates

```bash
python3 scripts/validate-plugins.py
python3 scripts/sync-manifests.py --check
python3 scripts/sync-skills.py --check
python3 scripts/check-bump.py
claude plugin validate . --strict
```

CI runs these, plus `claude plugin validate --strict` on each plugin. Details: [scripts/README.md](scripts/README.md).
