---
name: amplify-crosspost
description: Публикует готовый пост, рилс или сторис в соцсети. Срабатывает на кросспостинг, загрузку видео, публикацию в телеграм, vk, x, bluesky, mastodon, linkedin, instagram, youtube.
allowed-tools: Read, Write, Bash(python3 ${CLAUDE_SKILL_DIR}/scripts/amplify.py --help), Bash(python3 ${CLAUDE_SKILL_DIR}/scripts/amplify.py preview *), Bash(python3 ${CLAUDE_SKILL_DIR}/scripts/amplify.py publish *)
---

# Crosspost

Take one Markdown post from the operator's draft to their channels: prepare it, show how each platform renders it, and publish once they approve.

## The script

`amplify.py` is in the `scripts/` directory beside this `SKILL.md`. Call it by its absolute path, spelled out in full in every command, without `..` or a shell variable, from the project's root directory:

```
python3 <skill directory>/scripts/amplify.py
```

Read its `--help`, `preview --help` and `publish --help` before the first call. Run `publish`, and a `preview` that sends a video, in the background where a foreground command cannot wait as long as `publish --help` says it may run.

## Procedure

1. **The post.** A post is `content/<YYYY-MM-DD>-<slug>/index.md` at the project root. Use the folder they name; text given in prose goes into a new folder, never over an existing `index.md`. Every image and video in the folder goes with the post, in file-name order, unless they say otherwise. When they want a file at a place in the text, or several as a collage or slideshow, add a `::media` line there. Rename a file that cannot be placed only with their consent.
2. **Where and how.** One `--to` per channel (X is `twitter`). Use `--telegram-format rich` when they ask for rich formatting or an article in Telegram, when headings or tables must survive there, or when the post places files.
3. **Preview.** Show each platform's `projection` in a fenced code block under its name; formatting differences are expected. Tell them in plain words every item in `changes`, every file in `media` that does not go out and why, what each `pending_checks` item leaves unknown, and why a blocked platform is blocked. Leave out a platform the service does not offer, and say so.
4. **A text that does not fit.** On Telegram without rich, offer the rich format first and preview again with it if they agree. Otherwise give the length against the maximum and offer to shorten the text; with `thread_available`, also offer a thread, in their words a chain of posts, each answering the one before. For a thread, preview with `--thread`, dropping rich if Telegram is the platform still cut, and show each post of `parts` in its own numbered block. One text serves every platform: edit `index.md` only after they agree to the new text, then preview again.
5. **Ask.** Show the final text, the files, the placement, the channels and the number of posts in any thread, leaving out what is blocked or not connected, and wait for their yes.
6. **Publish.** Run `publish` once, with the arguments of the last preview minus every `--to` left out at the previous step. Give the link to each post and say plainly when one went out without a link or a file. For `pending` or `indeterminate`, say it is not confirmed and may be out, and ask them to check the platform. For a thread short of `published`, say which posts are out, with their links, which may be out and should be checked, and which were not sent. Never run `publish` again on your own, whatever the outcome: a second run can post twice.

## When something fails

- **`trouble: service`**: change nothing and diagnose nothing. Repeat a failed `preview` once after a minute. If it fails again, or `publish` failed, tell them there is a temporary technical problem on the service's side and their settings are not the cause. After `publish`, also pass on what the message says about the post; if it may be out, ask them to check the channel before anything is sent again. Leave out codes, addresses and timestamps.
- **`trouble: network`**: ask them to check their internet. Under Codex or Cowork, see [configuration.md](../setup/references/configuration.md#per-host).
- **An error message naming `amplify-setup`, a platform in `missing`, or files left out with `gateway_not_configured` that they want sent**: tell them in one line that publishing needs setting up, invoke `amplify-setup`, and return to the post once it is done.
- **A refusal over the account's limits or quota**: pass it on; to change them, they write to mail@smmtp.ru.

Never read or write `.smmtp/*`: it holds secrets.
