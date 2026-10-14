#!/usr/bin/env python3
"""Preview and publish a Markdown post to social platforms.

Run with --help for commands, environment, output and exit status.
Requires Python 3.9 or later and nothing outside the standard library.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import argparse
import json
import re
from pathlib import Path

from amplifykit.configuration import write_config
from amplifykit.doctor import doctor, problems
from amplifykit.errors import Fault
from amplifykit.platforms import PLACEMENTS
from amplifykit.publication import TARGET_STATES, run

HELP = """\
post:
  A folder holding index.md. The Markdown in index.md is the post. Images and
  videos are attached with --media, in the order given: JPEG, PNG, WebP, GIF,
  MP4, M4V, MOV or WebM, at most 512 MiB together. An image written into the
  Markdown is published as its literal text. --placement makes it a post (the
  default), a reel, which takes the first video, or a story, which takes the
  first image or video. A platform without native reels posts the video as an
  ordinary post and reports it in changes; one without stories blocks.

variables:
  Each variable below is read from the environment first, then from
  .amplify/.config in the nearest directory at or above the working one that
  has it, then through AMPLIFY_REST_SECRET_COMMAND. The file holds NAME=value
  lines; blank lines and lines starting with # are skipped, a # after a value
  is part of the value, one pair of quotes around a value is dropped, and an
  empty value counts as unset. "amplify.py config" writes it. preview and
  publish in a directory with no such file above it and no AMPLIFY_REST_URL
  stop before any request with error code setup_required.

  AMPLIFY_REST_URL     service URL, https
  AMPLIFY_REST_TOKEN   service token
  <STRATEGY>_<FIELD>   credentials of each strategy, for example
                       TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID;
                       preview names the ones that are missing. Where a
                       strategy takes another credential form for stories,
                       such as TELEGRAM_BUSINESS_CONNECTION_ID instead of
                       TELEGRAM_CHAT_ID, both may be set and --placement
                       picks the form
  AMPLIFY_MEDIA_GATEWAY_URL
  AMPLIFY_MEDIA_GATEWAY_TOKEN
                       media gateway origin and token; sent with every post
                       that has media when both are set. Without them the
                       service leaves the media out and says so
  AMPLIFY_REST_SECRET_COMMAND
                       command that prints the value of one variable above;
                       {name} is replaced with the variable's name. It runs
                       without a shell for every variable neither the
                       environment nor the file sets, so a secret kept in a
                       store reaches this script and nothing else. A non-zero
                       exit means the variable is not set. Example:
                       security find-generic-password -s {name} -w
  SSL_CERT_FILE        certificate bundle to trust, from the environment or
                       the file; see troubleshooting

output:
  One JSON object on stdout. "ok" is true when every destination is ready with
  its credentials present (preview) or published (publish); doctor --help
  describes its own report. "destinations" holds one entry per strategy. A
  failure that stops the command, including a refusal by the service, is
  {"ok": false, "error": {"code": ..., "message": ...}}. Whenever "ok" is
  false, stderr carries one line saying why.

exit status:
  0  "ok" is true
  1  "ok" is false: a command failed, or preview or publish left a
     destination short of its target state
  2  invalid command line
  3  doctor ran and found the setup incomplete; its report says where

examples:
  amplify.py config --root . --set AMPLIFY_REST_URL=https://service.example
  amplify.py doctor --to telegram
  amplify.py preview --post content/2026-09-24-release --to telegram --to bluesky
  amplify.py publish --post content/2026-09-24-release --to telegram \\
      --media content/2026-09-24-release/cover.png
  amplify.py preview --post content/2026-09-24-release --to instagram \\
      --placement reel --media content/2026-09-24-release/clip.mp4

troubleshooting:
  CERTIFICATE_VERIFY_FAILED on the first call is this machine's trust store,
  not a broken certificate on the service: a relocatable Python looks for
  certificates inside its own directory and ships none. Add SSL_CERT_FILE with
  a bundle the machine already has, /etc/ssl/cert.pem on macOS, to
  .amplify/.config and run again. Certificate verification is never disabled,
  here or anywhere else.

Run "amplify.py <command> --help" for the fields of a command.
"""

PREVIEW_HELP = """\
Show how the post lands on each platform without publishing anything.

output, per destination:
  state           ready or blocked
  measurement     the post's length in the platform's unit against its
                  maximum, after any cut
  projection      the post as the platform receives it: the Markdown
                  converted to the richest formatting the platform supports,
                  in its own request format, such as Telegram's HTML text; a
                  blocked post has none
  changes         what the platform changes beyond formatting, as the service
                  reports it: subject, class and details. A text over the
                  limit is cut to fit and ends with an ellipsis: class
                  approximated, details.reason text_truncated, details.value
                  the original length against details.maximum
  media           what happens to each attached file: file, class and
                  details, for example class omitted with details.reason
                  gateway_not_configured
  pending_checks  requirements the service cannot check before publishing:
                  code, file when it is about an attached file, and details
  missing         credential variables that are not set
  errors          service codes with retry, file as in pending_checks, and
                  details; retry is null for a blocking change, which comes
                  beside the error naming it
  warnings        service codes with details
"""

PUBLISH_HELP = """\
Publish the post to each platform. Sends nothing if a credential variable is
not set. The service may take up to ten minutes to answer, its limit for one
operation, and waits up to 300 seconds of it for a platform still processing
the post; sending the media comes on top. Never retries: after a lost answer,
or a run cut off before it prints, it is unknown whether the post was
published, so check the platform before publishing again.

output, per destination:
  state           published, pending, blocked, failed, cancelled or
                  indeterminate. pending: the platform was still processing
                  when the wait ran out; the post is not confirmed, and this
                  script does not continue it
  url             link to the published post; null when the service could not
                  get one, and the post is published all the same
  next_check_at   when a pending post was due its next check
  changes, media  as in preview
  errors, warnings
                  as in preview
"""

DOCTOR_HELP = """\
Check the setup without publishing: the config file, where each variable comes
from, the service and its token, the media gateway, and every platform with a
complete credential form. With --to the service also checks those
platforms' credentials with the provider, and a named platform short of a
value is reported with what it lacks; nothing is posted. A platform named
with --to is checked in the credential form --placement needs, so one that
takes another form for stories is checked for stories with --placement story.

A variable's source is environment, config or store, or null when it is unset.
Only the two URLs are printed; no other value is.

output:
  ok              the service answers with the token, and every --to platform
                  has its variables and is verified
  next            the first setup stage not done: service, media_gateway
                  (only when partial or invalid) or platforms, the last also
                  while no platform is complete; null when all are done
  config          path of the file read, or null; git_ignored and git_tracked,
                  each true, false, or null outside a git repository
  secret_command  where AMPLIFY_REST_SECRET_COMMAND is set: environment,
                  config, or null
  service         url, variables with their sources, readiness (ready or
                  busy), or error when it could not be reached
  media_gateway   url, variables, state: complete, partial, invalid (a URL
                  that is not an https root, which the service would ignore)
                  or none; images and videos reach the platforms only when
                  complete
  strategies      null when the service did not answer; otherwise available
                  (every platform ID) and configured: each platform with a
                  complete credential form for any placement, and each named
                  with --to, in the form --placement needs, whether or not it
                  is complete; each with variables, the
                  required ones missing, the ones whose value the platform's
                  schema rejects (invalid) and, with --to, verify: state
                  (verified, blocked, failed or cancelled), identity of the
                  account, and errors with code and retry. unknown lists --to
                  values that are not platform IDs; error is a verify call
                  that failed as a whole
"""

CONFIG_HELP = """\
Write NAME=value lines into DIR/.amplify/.config, readable by its owner only.
A new file starts as the annotated template that ships with this script: every
name present, empty but for public defaults such as BLUESKY_HOST. A value
goes on the line that sets its name, else on its commented-out example, else
on a new line; every other line stays as it is. An empty value leaves NAME=
for the operator to fill in, and is also how a value moves out of the file
into the store. Surrounding spaces are dropped. A secret value is never
printed.

Every value is checked before anything is written, and one that fails stops
the whole command:
  AMPLIFY_REST_URL, AMPLIFY_MEDIA_GATEWAY_URL   an https root, without a path
                                              or a user
  AMPLIFY_REST_TOKEN, AMPLIFY_MEDIA_GATEWAY_TOKEN
                                              one line of visible ASCII
  AMPLIFY_REST_SECRET_COMMAND                 parses, and contains {name}
  SSL_CERT_FILE                               a certificate bundle that loads
  any other name                              a platform variable the service
                                              names in /v1/capabilities, and a
                                              value its schema accepts; when
                                              the service does not answer with
                                              the settings as written, the
                                              value is written unchecked

DIR/.amplify/.config.local is the example that may be committed. It starts as
the same template and gains the value of each *_URL name and of
AMPLIFY_REST_SECRET_COMMAND on an empty line; a line holding a value is never
changed. In a git repository that does not ignore .amplify/.config, the line
.amplify/.config is added to DIR/.gitignore.

output:
  dry_run       true when nothing was written
  config        path of the file
  created       true when the file did not exist
  template      path of the example
  changes       per name: action (fill an empty line, replace a value, add a
                line), secret, and value, null for a secret
  checked       platform names checked against the service's schema
  unchecked     platform names written without that check
  gitignore     path of the .gitignore and whether the line is added; null
                outside a repository
  git_ignored   whether git ignores the file, after the write
  git_tracked   whether git already tracks the file: its secrets are then in
                the history, and ignoring it does not remove them
  error         with code config_value_invalid, invalid lists the names
                refused
"""


class Parser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2, f"amplify.py: error: {message}; see '{self.prog} --help'\n")


def build_parser():
    formatter = argparse.RawDescriptionHelpFormatter
    parser = Parser(
        prog="amplify.py",
        description=__doc__.splitlines()[0],
        epilog=HELP,
        formatter_class=formatter,
    )
    commands = parser.add_subparsers(dest="command", required=True, metavar="command")
    check = commands.add_parser(
        "doctor", help="check the setup without publishing", description=DOCTOR_HELP,
        formatter_class=formatter,
    )
    check.add_argument(
        "--to",
        action="append",
        default=[],
        metavar="STRATEGY",
        help="platform whose credentials the service checks with the provider; repeat for each;"
        " none by default",
    )
    add_placement(check, "placement whose credential form is checked")
    write = commands.add_parser(
        "config", help="write values into .amplify/.config", description=CONFIG_HELP,
        formatter_class=formatter,
    )
    write.add_argument(
        "--root", required=True, metavar="DIR",
        help="directory to hold .amplify/: the project root, or ~ for every project",
    )
    write.add_argument(
        "--set", action="append", default=[], metavar="NAME=VALUE",
        help="value to write; repeat for each; without one, only the missing file, example and"
        " .gitignore line are written",
    )
    write.add_argument(
        "--dry-run", action="store_true",
        help="check the values and report the plan without writing anything",
    )
    for name, summary, text in (
        ("preview", "show how the post lands on each platform", PREVIEW_HELP),
        ("publish", "publish the post to each platform", PUBLISH_HELP),
    ):
        command = commands.add_parser(
            name, help=summary, description=text, formatter_class=formatter
        )
        command.add_argument(
            "--post", required=True, metavar="FOLDER", help="post folder holding index.md"
        )
        command.add_argument(
            "--to",
            action="append",
            required=True,
            metavar="STRATEGY",
            help="service strategy ID, such as telegram; repeat for each platform",
        )
        command.add_argument(
            "--media",
            action="append",
            default=[],
            metavar="FILE",
            help="image or video to attach; repeat for each file, in order; none by default",
        )
        add_placement(command, "publication form")
    return parser


def add_placement(command, summary):
    command.add_argument(
        "--placement",
        choices=PLACEMENTS,
        default="post",
        help=f"{summary}; post by default",
    )


def failure_line(command, envelope):
    """The one stderr line a failed run adds; service-supplied text stays in the JSON."""
    destinations = envelope.get("destinations", [])
    if "error" in envelope:
        error = envelope["error"]
        text = f"{command} failed ({error['code']}): {error['message']}"
    elif command == "doctor":
        text = f"doctor: {'; '.join(problems(envelope))}; read the report on stdout"
    elif destinations:
        target = TARGET_STATES[command]
        short = sum(entry["state"] != target or "missing" in entry for entry in destinations)
        text = (
            f"{command}: {short} of {len(destinations)} destinations not {target};"
            ' read "destinations" in the output'
        )
    else:
        text = f"{command}: the service reported no destination; check the --to values"
    return "amplify.py: error: " + re.sub(r"[\r\n]+", " ", text)


def main(argv=None, environ=None, cwd=None):
    arguments = build_parser().parse_args(argv)
    environ = os.environ if environ is None else environ
    cwd = Path.cwd() if cwd is None else Path(cwd)
    try:
        if arguments.command == "doctor":
            envelope = doctor(arguments.to, environ, cwd, arguments.placement)
        elif arguments.command == "config":
            envelope = write_config(arguments.root, arguments.set, arguments.dry_run, environ)
        else:
            envelope = run(
                arguments.command, arguments.post, arguments.to, arguments.media, environ, cwd,
                arguments.placement,
            )
    except Fault as fault:
        envelope = {"ok": False, "error": fault.as_object()}
    if not envelope["ok"]:
        print(failure_line(arguments.command, envelope), file=sys.stderr)
    try:
        json.dump(envelope, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        sys.stdout.flush()
    except BrokenPipeError:
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
    if envelope["ok"]:
        return 0
    return 3 if arguments.command == "doctor" and "error" not in envelope else 1


if __name__ == "__main__":
    sys.exit(main())
