---
name: crosspost
description: Публикует готовый пост, рилс или сторис в соцсети. Срабатывает на кросспостинг, загрузку видео, публикацию в телеграм, vk, x, bluesky, mastodon, linkedin, instagram, youtube.
allowed-tools: Read, Write, Bash(python3 ${CLAUDE_SKILL_DIR}/scripts/amplify.py --help), Bash(python3 ${CLAUDE_SKILL_DIR}/scripts/amplify.py preview *), Bash(python3 ${CLAUDE_SKILL_DIR}/scripts/amplify.py publish *)
metadata:
  internal: true
---

# Crosspost

Take one Markdown post from the operator's draft to its channels: prepare it, show how each platform renders it, and publish once the operator approves.

## The script

`amplify.py` lives in the `scripts/` directory beside this `SKILL.md`. Call it by its absolute path, spelled out in full in every command, without `..` or a shell variable, from the project's root directory:

```
python3 <skill directory>/scripts/amplify.py
```

Read `amplify.py --help`, `preview --help` and `publish --help` before the first call. Run `publish`, and a `preview` that sends a video, in the background where a foreground command cannot wait as long as `publish --help` says the service may take.

## Procedure

1. **The post.** A post is `content/<date>-<slug>/index.md` at the project root, `<date>` as `YYYY-MM-DD`. Use the folder the operator names; text they give in prose goes into a new folder, never over an existing `index.md`. Every image and video in the folder goes with the post, in file-name order, unless the operator says otherwise. When they want a file at a place in the text, or several as a collage or slideshow, write the `::media` line there; a file whose name cannot be placed is renamed only with their consent.
2. **Where and how.** One `--to` per channel they name, by its platform ID (X is `twitter`). `--telegram-format rich` when they ask for rich or full formatting in Telegram, an article or longread there, headings and tables that survive, or when the post places files with `::media`.
3. **Preview.** Run `preview`. Per platform, show its `projection` in a fenced code block under the platform's name; where it differs from the Markdown, that is the platform's formatting, not an error. Tell the operator in plain words every item in `changes`, every file in `media` that does not go out and why, what each `pending_checks` item leaves unknown, and why a blocked platform is blocked. For `text_truncated`, give the length against the maximum and offer to shorten the text; for Telegram, offer the rich format first. One text serves every platform: edit `index.md` only after the operator agrees to the new text, then preview again. A platform the service does not offer (`platform_unknown`) is left out after telling them.
4. **Ask.** Show the final text, the files, the placement and the channels, leaving out any still blocked or not connected, and wait for the operator's yes.
5. **Publish.** Run `publish` once, with the arguments of the last preview. Give the link to each published post, say plainly when one went out without a link or without a file, report every error in plain words, and report a `pending` post as not confirmed yet, for the operator to check on the platform later. Never run `publish` again on your own, after a failure, a pending post or a missing link alike: a second run can post twice.

## Setup

Never read or write `.smmtp/.env.local` or any other settings file: it holds secrets. When the script reports a setup problem (`setup_required`, `environment_missing`, `url_invalid`, `token_invalid`, `config_invalid`, `config_unreadable`, `secret_command_invalid`, `secret_command_failed`, `certificate_file_invalid`, a certificate failure, `channel_missing`, a platform in `missing`, a message that names `amplify:setup`, `gateway_url_invalid`, or files left out with `gateway_not_configured` that the operator wants sent), tell the operator in one line that publishing needs setting up, invoke `amplify:setup`, and come back to the post once it is done. A refusal over the account's limits or quota is not a setup problem: pass the message on, since only whoever issued the master token changes them.
