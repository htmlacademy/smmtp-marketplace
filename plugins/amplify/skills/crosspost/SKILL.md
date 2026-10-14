---
name: crosspost
description: Публикует готовый пост, рилс или сторис в соцсети. Срабатывает на кросспостинг, загрузку видео, публикацию в телеграм, vk, x, bluesky, mastodon, slack, discord, youtube, tiktok.
allowed-tools: Read, Write, Bash(python3 ${CLAUDE_SKILL_DIR}/scripts/amplify.py --help), Bash(python3 ${CLAUDE_SKILL_DIR}/scripts/amplify.py preview *), Bash(python3 ${CLAUDE_SKILL_DIR}/scripts/amplify.py publish *)
metadata:
  internal: true
---

# Crosspost

Publish one Markdown post to several platforms through the service, after showing the operator a preview.

## The script

`amplify.py` lives in the `scripts/` directory beside this `SKILL.md`. Call it by its absolute path, spelled out without `..`, from the project's root directory:

```
python3 <skill directory>/scripts/amplify.py
```

Read `amplify.py --help` and `amplify.py <command> --help` before the first call: the help describes the commands, arguments, variables, output and exit status. Give `publish`, and a `preview` that sends a video, fifteen minutes, in the background where a command in the foreground cannot run that long.

## Procedure

1. **The post.** A post is `content/<date>-<slug>/index.md` at the project root, `<date>` as `YYYY-MM-DD`, Markdown only. Use the folder the operator names. If they give the text in prose, write it into a new folder, never over an existing `index.md`. Every image and video in the post folder goes with the post unless the operator says otherwise. It is a post unless the operator asks for a reel or a story: then pass `--placement reel` or `--placement story` to both commands.
2. **Preview.** Run `preview` with a `--to` per platform the operator named, by its ID in the service (X is `twitter`), and a `--media` per image or video of the post, in file-name order unless the operator gave another. When a platform is not in `error.available`, tell the operator the service does not offer it and preview without it. Show them, per platform, its `projection` in a fenced code block under the platform's name, so the post's start and end are plain and the chat renders nothing inside it. Where it differs from the source Markdown, that is the platform's formatting, not an error. Tell the operator every item in `changes` in plain words, such as a reel that a platform posts as an ordinary video post. For `text_truncated`, say how long the text is against how long it may be, and offer to shorten it; the text is one for every platform, so edit `index.md` only after they agree to the new text. Say which files do not go out and why, what each `pending_checks` item leaves unknown, and why a blocked platform is blocked. Change the text and preview again until it is right.
3. **Ask.** Show the final text, the files, the placement and the platforms to publish to, leaving out any that preview still blocks, and wait for the operator's yes.
4. **Publish.** Run `publish` once, with the same `--media` and `--placement`. Give the operator the link to each published post, say plainly when a post was published without one or without a file, and report every error in plain words. Report a `pending` post as not confirmed yet, for the operator to check on the platform later. Do not run `publish` again on your own, whether after a failure, a pending post or to get a missing link: a second run can post twice.

## Setup

The script reads its values from `.amplify/.config`, which holds secrets: never read it or write it from here. When `preview` stops with `setup_required`, tell the operator in one line that crossposting has to be set up first, and invoke the `amplify:setup` skill. Do the same for any other setup problem: a variable, a token, the service's certificate, a platform's `missing` credentials, `gateway_url_invalid`, or files left out with `gateway_not_configured` that the operator wants to go out. After setup is done, come back to the post; `doctor` and `config` belong to that skill, not to this one.
