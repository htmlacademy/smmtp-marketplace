# Secret stores and hosts

## A secret in the keychain

The operator stores each secret in their own terminal, one item per variable, named after it:

```sh
security add-generic-password -a "$USER" -s MASTODON_ACCESS_TOKEN -w
```

With `-w` last, `security` asks for the value, which keeps it out of the shell history. `.smmtp/.env.local` then carries the command once, and the secret's own line stays empty, because a value in the file wins over the store:

```
AMPLIFY_REST_SECRET_COMMAND=security find-generic-password -s {name} -w
```

macOS asks the operator to allow access the first time the script reads the keychain, and for its password when it is locked. For an item in another keychain, append its name: `security find-generic-password -s {name} -w stash.keychain`. Any other store works whose command behaves as `--help` describes for `AMPLIFY_REST_SECRET_COMMAND`.

## Per host

- **Codex** runs commands in the `workspace-write` sandbox with the network off. Set `network_access = true` under `[sandbox_workspace_write]` in `~/.codex/config.toml`, or approve the command when Codex asks to run it outside the sandbox.
- **Claude Cowork** reads the file from the folder the session works in. Its network goes through an allowlist, so the host of `AMPLIFY_REST_URL` has to be allowed in Cowork's network settings. There is no keychain inside Cowork, so a secret there sits in the file.
