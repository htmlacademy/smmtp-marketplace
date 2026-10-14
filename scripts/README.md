# Scripts

Repository tooling, stdlib Python only. Plugin scripts live in `plugins/<name>/scripts/`. Each derived file has exactly one owner.

| Script | Owns |
|---|---|
| `validate-plugins.py` | `plugins/` structure: manifests, frontmatter, Codex cards, tracked junk |
| `sync-manifests.py` | Claude manifests, catalog entries, Codex catalog |
| `sync-skills.py` | script copies inside skills, standalone skills in `skills/` |
| `check-bump.py` | version bumps for changed plugins and catalog entries |

## sync-manifests.py

```
plugins/<name>/.claude-plugin/plugin.json   from plugins/<name>/plugin.json
.claude-plugin/marketplace.json             version, description, displayName, category
.agents/plugins/marketplace.json            whole file
```

```bash
python3 scripts/sync-manifests.py           # write; minor catalog bump if an entry changed
python3 scripts/sync-manifests.py --patch   # patch bump instead (--major also exists)
python3 scripts/sync-manifests.py --check   # verify only
```

Idempotent: a second run changes nothing. The Codex shelf name is the catalog `name`. A `.codex-plugin/plugin.json` fails both modes.

## sync-skills.py

```
plugins/<name>/skills/<skill>/scripts/   exact mirror of plugins/<name>/scripts/
skills/<name>-<skill>/                   standalone copy for npx skills
```

```bash
python3 scripts/sync-skills.py            # write
python3 scripts/sync-skills.py --check    # verify only
```

A standalone copy differs from the plugin skill in three ways: `name` becomes `<name>-<skill>`; `<name>:<skill>` in text files and `$<skill>` in `.md`/`.yaml` become `<name>-<skill>`; `internal: true` is dropped from `metadata`. Stale files and orphaned `skills/` directories are removed. A plugin skill without `metadata.internal: true` is an error.

## check-bump.py

```bash
python3 scripts/check-bump.py                 # against origin/main
python3 scripts/check-bump.py --base HEAD~1
```

Compares the working tree with the merge base. A plugin absent at the base is skipped.

## validate-plugins.py

```bash
python3 scripts/validate-plugins.py
```

Errors: unreadable manifest, non-portable `$schema`, invalid or reserved name, non-semver version, missing required field or `interface.displayName`/`category`; frontmatter that is malformed, has an unknown key, a `name` not matching its directory, or a `description` that is not a single-line unquoted scalar of at most 200 characters without `: ` and em or en dashes; missing or malformed `agents/openai.yaml`; tracked `__pycache__`, `.pyc`, `.DS_Store`.

## CI

`.github/workflows/ci.yml`, on pull requests and `main`:

| Job | Runs |
|---|---|
| Lint | `validate-plugins.py`, both generators with `--check`, `check-bump.py` on pull requests |
| Verify | `claude plugin validate --strict` on the catalog and each plugin |
| Publish | `main` only: release `v<version>` from the catalog version unless it exists |
