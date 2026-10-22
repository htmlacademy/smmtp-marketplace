---
name: amplify-crosspost
description: Публикует готовый пост, рилс или сторис в соцсети. Срабатывает на кросспостинг, загрузку видео, публикацию в телеграм, vk, x, bluesky, mastodon, slack, discord, youtube, tiktok.
allowed-tools: Read, Write, Bash(python3 ${CLAUDE_SKILL_DIR}/scripts/amplify.py --help), Bash(python3 ${CLAUDE_SKILL_DIR}/scripts/amplify.py preview *), Bash(python3 ${CLAUDE_SKILL_DIR}/scripts/amplify.py publish *)
---

# Crosspost

Publish one Markdown post to several platforms through the service, once the operator approves its preview.

## The script

`amplify.py` lives in the `scripts/` directory beside this `SKILL.md`. Call it by its absolute path, spelled out in full in every command, without `..` or a shell variable, from the project's root directory:

```
python3 <skill directory>/scripts/amplify.py
```

Read `amplify.py --help`, `preview --help` and `publish --help` before the first call. Give `publish`, and a `preview` that sends a video, fifteen minutes, in the background where a foreground command cannot run that long.

## Procedure

1. **The post.** A post is `content/<date>-<slug>/index.md` at the project root, `<date>` as `YYYY-MM-DD`. Use the folder the operator names. Text they give in prose goes into a new folder, never over an existing `index.md`. Every image and video in the folder goes with the post, in file-name order, unless the operator says otherwise.
2. **Arguments.** A `--to` per platform the operator named, by its service ID (X is `twitter`), and a `--media` per file. `--placement reel` or `--placement story` when the operator asks for one. `--telegram-format rich` when they ask for rich, extended or full formatting in Telegram, for an article or a longread there, or for its headings and tables to survive.
3. **Preview.** Run `preview`. A platform the service does not offer stops it with `strategy_unknown`: tell the operator and preview without it. Per platform, show its `projection` in a fenced code block under the platform's name; where it differs from the Markdown, that is the platform's formatting, not an error. Tell the operator in plain words every item in `changes`, every file in `media` that does not go out and why, what each `pending_checks` item leaves unknown, and why a blocked platform is blocked. For `text_truncated`, give the length against the maximum and offer to shorten the text; for Telegram, offer the rich format first. One text serves every platform: edit `index.md` only after the operator agrees to the new text, then preview again.
4. **Ask.** Show the final text, the files, the placement and the platforms, leaving out any still blocked, and wait for the operator's yes.
5. **Publish.** Run `publish` once, with the arguments of the last preview. Give the link to each published post, say plainly when one went out without a link or without a file, report every error in plain words, and report a `pending` post as not confirmed yet, for the operator to check on the platform later. Never run `publish` again on your own, after a failure, a pending post or a missing link alike: a second run can post twice.

## Setup

The script reads its values from `.smmtp/.env.local`, which holds secrets: never read or write it, or any other settings file. On `setup_required` or any other setup problem (a variable, a token, the service's certificate, a platform's `missing` credentials, `gateway_url_invalid`, or files left out with `gateway_not_configured` that the operator wants sent), tell the operator in one line that crossposting needs setting up, invoke the `amplify-setup` skill, and come back to the post once it is done. `doctor` and `config` belong to that skill.
