#!/usr/bin/env python3


from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import argparse
import json
import re
from pathlib import Path

from amplifykit.configuration import write_config
from amplifykit.connection import app_token, connect
from amplifykit.doctor import doctor, problems
from amplifykit.errors import Fault
from amplifykit.planning import plan
from amplifykit.platforms import PLACEMENTS, TELEGRAM_FORMATS
from amplifykit.publication import TARGET_STATES, run

HELP = """\
variables:
  Read from the environment, then the nearest .smmtp/.env.local at or above
  the working directory, then AMPLIFY_REST_SECRET_COMMAND. Empty means unset.

  AMPLIFY_REST_URL             service root, https
  AMPLIFY_REST_ACCOUNT         account name
  AMPLIFY_REST_SECRET          account secret; with the name, connects channels
  AMPLIFY_REST_APP_TOKEN       app token, publishes; app-token writes it
  AMPLIFY_CHANNEL_<PLATFORM>   channel ID; connect writes it
  <PLATFORM>_<FIELD>           value a connection asks for, such as
                               TELEGRAM_BOT_TOKEN or DISCORD_WEBHOOK_URL;
                               connect empties it once connected
  AMPLIFY_MEDIA_GATEWAY_URL, AMPLIFY_MEDIA_GATEWAY_TOKEN
                               media gateway; without both, media is left out
  AMPLIFY_REST_SECRET_COMMAND  prints one variable's value, {name} replaced by
                               its name; no shell; non-zero exit means unset.
                               macOS: security find-generic-password -s {name} -w
  SSL_CERT_FILE                certificate bundle, for a network that replaces
                               certificates

output:
  One JSON object on stdout; when ok is false, stderr carries one line. A
  failed command is {"ok": false, "error": {"code", "message", ...}}, message
  saying what to do; doctor, a connect stop and destinations report in their
  own fields. error.trouble: service (the service or a platform is failing,
  the setup is fine) or network (this computer cannot reach the service).

exit status:
  0  ok is true
  1  a command failed, doctor hit error.trouble, or a platform did not reach
     ready or published
  2  invalid command line
  3  doctor found the setup incomplete, or connect stopped for the operator

examples:
  amplify.py connect --to telegram --link https://t.me/channelname
  amplify.py preview --post content/2026-09-24-release --to telegram --to bluesky

troubleshooting:
  CERTIFICATE_VERIFY_FAILED on the first call is this machine's trust store,
  not a broken certificate on the service: a relocatable Python looks for
  certificates inside its own directory and ships none. Add SSL_CERT_FILE with
  a bundle the machine already has, /etc/ssl/cert.pem on macOS, to
  .smmtp/.env.local and run again. Certificate verification is never disabled,
  here or anywhere else.

Run "amplify.py <command> --help" for the fields of a command.
"""

PREVIEW_HELP = """\
Show how the post lands on each platform, publishing nothing. Stops with an
error naming amplify-setup when publishing is not set up.

post:
  --post is a folder holding index.md. --media attaches JPEG, PNG, WebP, GIF,
  MP4, M4V, MOV or WebM files in the order given, at most 64 files and 512 MiB
  together.

placing files:
  A ::media line between blank lines places attached files by file name:
    ::media{id="cover.png"}
    ::media{ids="a.png,b.png" layout="collage"}
    ::media{ids="a.png,b.png" layout="slideshow"}
  A placeable name is unique among the files, up to 128 Latin letters, digits
  and . _ : -, starting with a letter or digit. A platform that cannot keep a
  place attaches the file and reports media_attached. Markdown image syntax
  stays literal text.

--placement:
  reel takes the first video, story the first image or video. A platform
  without reels posts the video as a post; one without stories blocks.

--telegram-format rich:
  A Telegram Rich Message, posts only: tables, nested and task lists, quotes,
  code, highlight, spoilers and formulas survive, headings become bold. Holds
  32768 characters (regular: 4096, or 1024 with media) and 50 files at their
  ::media places. A collage takes images and videos, a slideshow images only;
  any other group is split.

--thread:
  Posts only. A text too long for one post becomes a thread of up to 25 posts,
  each replying to the one before, on platforms with threads; others cut it.
  A Telegram rich message stays one message.

output:
  destinations, one per --to:
    platform
    state             ready or blocked
    projection        the post as the platform receives it; absent when
                      blocked or when parts is present
    parts             the thread, per post: projection, files
    thread_available  the text is cut here and --thread can keep it whole, on
                      Telegram without rich
    changes           what the platform changes beyond formatting: subject,
                      class, details. details.reason text_truncated carries
                      details.truncated: unit, value (original), maximum
    media             attached files left out or changed: file, class, details
    pending_checks    what only publishing can prove: code, file, details
    missing           channel variable of a platform not connected
    measurements      when blocked: field, unit, value, maximum
    errors            code, retry, file, details
    warnings          code, details
"""

PUBLISH_HELP = """\
Publish the post to each platform's channel; nothing is sent while any lacks
one. Arguments as in preview --help. May run for 10 minutes. Never retries:
after a lost answer the post may already be out.

output:
  destinations, one per --to:
    platform
    state   published, partial (a thread with only its first posts out),
            pending (not confirmed yet), indeterminate (may be out), blocked,
            failed or cancelled
    url     the post or a thread's first post; on a published post, null
            means the platform gave none
    parts   the thread, per post: index, url, state confirmed (out), possible
            (may be out) or absent (not sent)
    changes, media, errors, warnings
            as in preview
"""

DOCTOR_HELP = """\
Check the setup and say what to do next, publishing nothing. With --to, also
verify those platforms' channels for --placement.

output:
  ok              nothing is broken and every --to platform is verified
  stage           first stage not done: service, app_token, media_gateway,
                  platforms or verify; null when done
  next            what to do for that stage; null when done
  config          the settings file read, or null
  secret_command  where AMPLIFY_REST_SECRET_COMMAND is set, or null
  service         url, unset, readiness, or error, also for error.trouble in
                  any later check
  app_token       state: none, working, or unusable with reason or error
  media_gateway   url, unset, state: complete, partial, invalid or none
  platforms:
    placement
    channels      per platform with a channel or named with --to: channel,
                  title, placements, state: none, connected, reauth_required
                  (the platform rejected its key or sign-in), gone or error;
                  with --to, verify: state, identity, url, errors
    unverified    connected platforms not verified in this run
    unknown       --to values that are not platforms
    error         a verify call that failed as a whole
"""

PLAN_HELP = """\
Say what connecting each of the operator's channels takes, from the links or
addresses they give. Before the service answers it only reads the links:
available is null, and sign_in and asks are absent.

output:
  service         ready, or the code of the error that kept it from answering
  trouble         as error.trouble, when that error has one
  placement
  channels        per --link, then per --to:
    link
    platform      null: ask which platform the channel is on
    address       what the link names
    connected     the platform's one channel publishes --placement, which may
                  not be this link (compare title); null when unknown
    title         the channel's name on the platform, or null
    available     true; false with reason; null before the service answers
    form          the way to connect, when the link settles it
    sign_in       the operator signs in on the platform
    asks          values the operator supplies before connect
    question, forms
                  what to ask when the link does not settle the form; the
                  answer goes to connect as --form
"""

CONNECT_HELP = """\
Connect a platform's channel so preview and publish reach it; nothing is
posted. Runs the platform's steps until done or until the operator has to act,
then stops with next and flow. Continue with --flow and the same --to,
--placement, --form and --link. A flow expires after 15 minutes.

--link takes the operator's link or address as given. A private Telegram
channel's invite link finds the channel that made the bot an administrator
within the last day.

steps:
  fields    sends the values plan lists as asks
  redirect  stops with url for the operator to sign in; a run with --flow
            waits up to 90 s for it
  target    takes the channel --link names or the only one listed, else stops
            with targets; pass the chosen title as --target

A platform keeps one channel: another form joins it, the same form renews its
key or sign-in. Writes AMPLIFY_CHANNEL_<PLATFORM> when done.

output:
  ok        true once connected
  platform, flow
  step      where it stopped: fields, redirect or target
  next      what to do
  url, targets, missing
            what that step needs
  identity  id and name of what publishes, such as the bot
  channel, title
            when connected
  cleared   values emptied in the settings file
  stored    values read from the secret store, now safe to delete there
  error     message says what to do; flow to continue after a refusal or
            error.trouble; reason names a channel the platform refused
"""

APP_TOKEN_HELP = """\
Issue the app token and write it to the settings file as
AMPLIFY_REST_APP_TOKEN, never printing it. The one it replaces is revoked.

output:
  revoked      the replaced token's name, or null
  not_revoked  a replaced token the service kept active; the new one works
"""

CONFIG_HELP = """\
Write NAME=value lines to DIR/.smmtp/.env.local, owner-readable, and NAME= to
the shareable DIR/.smmtp/.env.example, which keeps only URLs and
AMPLIFY_REST_SECRET_COMMAND. Other lines stay. An empty value leaves NAME= for
the operator to fill. Secrets are never printed. One invalid value writes
nothing.

checks:
  AMPLIFY_REST_URL, AMPLIFY_MEDIA_GATEWAY_URL   https root, no path or user
  AMPLIFY_REST_ACCOUNT                          account name as issued
  AMPLIFY_REST_SECRET                           32 characters as issued; an
                                                account:secret line fills both
  AMPLIFY_REST_APP_TOKEN                        an issued app token
  AMPLIFY_MEDIA_GATEWAY_TOKEN                   visible ASCII
  AMPLIFY_CHANNEL_<PLATFORM>                    ch_...
  AMPLIFY_REST_SECRET_COMMAND                   parses and contains {name}
  SSL_CERT_FILE                                 a bundle that loads
  DISCORD_WEBHOOK_URL                           a whole webhook URL
  other names                                   the service's schema, when it
                                                answers

output:
  dry_run   true when nothing was written
  config    the settings file
  changes   per name: action (fill, replace, add), secret, value (null when
            secret)
  checked, unchecked
            connection values checked or not against the service; only
            connect proves a key works
  error     config_value_invalid, with invalid naming the refused values
"""


class Parser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2, f"amplify.py: error: {message}; see '{self.prog} --help'\n")


def build_parser():
    formatter = argparse.RawDescriptionHelpFormatter
    parser = Parser(
        prog="amplify.py",
        description="Connect social channels and publish a Markdown post to them.",
        epilog=HELP,
        formatter_class=formatter,
    )
    commands = parser.add_subparsers(dest="command", required=True, metavar="command")
    check = commands.add_parser(
        "doctor", help="check the setup", description=DOCTOR_HELP, formatter_class=formatter,
    )
    check.add_argument(
        "--to", action="append", default=[], metavar="PLATFORM",
        help="platform whose channel to verify; repeat for each; none by default",
    )
    add_placement(check, "placement to verify --to for")
    survey = commands.add_parser(
        "plan", help="say what connecting each channel takes", description=PLAN_HELP,
        formatter_class=formatter,
    )
    survey.add_argument(
        "--link", action="append", default=[], metavar="TEXT",
        help="the operator's link or address of a channel; repeat for each; none by default",
    )
    survey.add_argument(
        "--to", action="append", default=[], metavar="PLATFORM",
        help="platform named without a link; repeat for each; none by default",
    )
    add_placement(survey, "placement to plan for")
    join = commands.add_parser(
        "connect", help="connect a platform's channel", description=CONNECT_HELP,
        formatter_class=formatter,
    )
    join.add_argument("--to", required=True, metavar="PLATFORM", help="platform, such as telegram")
    add_placement(join, "placement to connect")
    join.add_argument(
        "--form", metavar="FORM",
        help="form, when the platform connects in more than one way; none by default",
    )
    join.add_argument("--flow", metavar="ID", help="flow to continue; none by default")
    join.add_argument(
        "--link", metavar="TEXT", help="the operator's link or address of the channel; none by default"
    )
    join.add_argument(
        "--target", metavar="TITLE", help="title chosen from targets; none by default"
    )
    commands.add_parser(
        "app-token", help="issue the app token", description=APP_TOKEN_HELP,
        formatter_class=formatter,
    )
    write = commands.add_parser(
        "config", help="write .smmtp/.env.local", description=CONFIG_HELP, formatter_class=formatter,
    )
    write.add_argument(
        "--root", required=True, metavar="DIR",
        help="directory to hold .smmtp/: the project root, or ~ for every project under it",
    )
    write.add_argument(
        "--set", action="append", default=[], metavar="NAME=VALUE",
        help="value to write; repeat for each; none by default",
    )
    write.add_argument("--dry-run", action="store_true", help="check and plan, writing nothing")
    for name, summary, text in (
        ("preview", "show how the post lands on each platform", PREVIEW_HELP),
        ("publish", "publish the post", PUBLISH_HELP),
    ):
        command = commands.add_parser(name, help=summary, description=text, formatter_class=formatter)
        command.add_argument("--post", required=True, metavar="FOLDER", help="folder holding index.md")
        command.add_argument(
            "--to", action="append", required=True, metavar="PLATFORM",
            help="platform, such as telegram; repeat for each",
        )
        command.add_argument(
            "--media", action="append", default=[], metavar="FILE",
            help="image or video to attach; repeat for each, in order; none by default",
        )
        add_placement(command, "post, reel or story")
        command.add_argument(
            "--telegram-format", choices=TELEGRAM_FORMATS, default="regular",
            help="rich sends a Telegram Rich Message; regular by default",
        )
        command.add_argument(
            "--thread", action="store_true",
            help="publish a text too long for one post as a thread; off by default",
        )
    return parser


def add_placement(command, summary):
    command.add_argument(
        "--placement", choices=PLACEMENTS, default="post", help=f"{summary}; post by default",
    )


def failure_line(command, envelope):
    destinations = envelope.get("destinations", [])
    if "error" in envelope:
        text = f"{command} failed ({envelope['error']['code']}); read error.message on stdout"
    elif command == "doctor":
        text = f"doctor: {'; '.join(problems(envelope))}; read the report on stdout"
    elif command == "connect":
        text = f"connect: {envelope['platform']} stopped at {envelope['step']}; read \"next\" on stdout"
    elif destinations:
        target = TARGET_STATES[command]
        short = sum(entry["state"] != target or "missing" in entry for entry in destinations)
        text = (
            f"{command}: {short} of {len(destinations)} destinations not {target};"
            ' read "destinations" on stdout'
        )
    else:
        text = f"{command}: the service reported no destination; check the --to values"
    return "amplify.py: error: " + re.sub(r"[\r\n]+", " ", text)


def exit_status(command, envelope):
    if envelope["ok"]:
        return 0
    if command not in ("doctor", "connect") or "error" in envelope:
        return 1
    return 1 if ((envelope.get("service") or {}).get("error") or {}).get("trouble") else 3


def main(argv=None, environ=None, cwd=None):
    parser = build_parser()
    arguments = parser.parse_args(argv)
    if getattr(arguments, "thread", False) and arguments.placement != "post":
        parser.error("--thread applies only to --placement post")
    environ = os.environ if environ is None else environ
    cwd = Path.cwd() if cwd is None else Path(cwd)
    try:
        if arguments.command == "doctor":
            envelope = doctor(arguments.to, environ, cwd, arguments.placement)
        elif arguments.command == "plan":
            envelope = plan(arguments.link, arguments.to, arguments.placement, environ, cwd)
        elif arguments.command == "connect":
            envelope = connect(
                arguments.to, arguments.placement, arguments.form, arguments.flow, arguments.link,
                arguments.target, environ, cwd,
            )
        elif arguments.command == "app-token":
            envelope = app_token(environ, cwd)
        elif arguments.command == "config":
            envelope = write_config(arguments.root, arguments.set, arguments.dry_run, environ)
        else:
            envelope = run(
                arguments.command, arguments.post, arguments.to, arguments.media, environ, cwd,
                arguments.placement, arguments.telegram_format, arguments.thread,
            )
    except Fault as fault:
        envelope = {"ok": False, "error": fault.as_object()}
    if not envelope["ok"]:
        print(failure_line(arguments.command, envelope), file=sys.stderr)
    try:
        json.dump(envelope, sys.stdout, ensure_ascii=False, separators=(",", ":"))
        sys.stdout.write("\n")
        sys.stdout.flush()
    except BrokenPipeError:
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
    return exit_status(arguments.command, envelope)


if __name__ == "__main__":
    sys.exit(main())
