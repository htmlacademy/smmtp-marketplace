# Where the values live

`amplify.py --help` gives the file's format and the order in which the script looks for a value; `config --help` gives what a write does. This page holds what neither says.

## The files

| File | Holds | In git |
|---|---|---|
| `.amplify/.config` in the project | this project's values | never |
| `~/.amplify/.config` | the values of every project under the home directory without its own | not in a repository |
| `.amplify/.config.local` | the example: the same names, without secrets | may be committed |

The nearest `.amplify/.config` is the whole answer: a project file does not inherit names from the one in the home directory. A teammate copies `.config.local` to `.config` and fills in their own secrets.

## A secret in the keychain

The operator stores each secret in their own terminal, one item per variable, named after it:

```sh
security add-generic-password -a "$USER" -s MASTODON_ACCESS_TOKEN -w
```

With `-w` last, `security` asks for the value, which keeps it out of the shell history. `.amplify/.config` then carries the command once, and the secret's own line stays empty, because a value in the file wins over the store:

```
AMPLIFY_REST_SECRET_COMMAND=security find-generic-password -s {name} -w
```

For an item in another keychain, append its name: `security find-generic-password -s {name} -w stash.keychain`. Any other store works whose command behaves as `--help` describes for `AMPLIFY_REST_SECRET_COMMAND`. A locked keychain asks for its password in a dialog on the first read.

## Per host

- **Claude Code** needs nothing beyond the file.
- **Codex** runs commands in the `workspace-write` sandbox with the network off. Set `network_access = true` under `[sandbox_workspace_write]` in `~/.codex/config.toml`, or approve the command when Codex asks to run it outside the sandbox.
- **Claude Cowork** reads the file from the folder the session works in. Its network goes through an allowlist, so the host of `AMPLIFY_REST_URL` has to be allowed in Cowork's network settings. There is no keychain inside Cowork, so a secret there sits in the file.
