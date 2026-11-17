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
  the working directory (~/.smmtp/.env.local serves every project under ~
  without its own), then AMPLIFY_REST_SECRET_COMMAND when set, then a default.
  An empty value is unset.

  AMPLIFY_REST_URL     service URL, https
  AMPLIFY_REST_TOKEN   master token, name:secret: connects platforms, cannot
                       publish
  AMPLIFY_REST_APP_TOKEN
                       app token, name:secret: publishes; app-token writes it
  AMPLIFY_CHANNEL_<PLATFORM>
                       a platform's channel, ch_...; connect writes it
  <PLATFORM>_<FIELD>   a value a platform's connection asks for, such as
                       TELEGRAM_BOT_TOKEN, or DISCORD_WEBHOOK_URL for a whole
                       webhook link; read only by connect, which empties it in
                       the file once the channel exists
  AMPLIFY_MEDIA_GATEWAY_URL, AMPLIFY_MEDIA_GATEWAY_TOKEN
                       media gateway origin and token, sent with posts that
                       carry media; without both the service leaves media out
  AMPLIFY_REST_SECRET_COMMAND
                       command printing one variable's value, {name} replaced
                       by its name, run without a shell for variables neither
                       the environment nor the file sets; non-zero exit means
                       unset. No store is opened without it. macOS keychain:
                       security find-generic-password -s {name} -w
  SSL_CERT_FILE        certificate bundle to trust after
                       CERTIFICATE_VERIFY_FAILED

output:
  One JSON object on stdout; each command's --help lists its fields. A
  failure is {"ok": false, "error": {"code", "message", ...}}, the message
  naming what to do. When "ok" is false, stderr has one line naming what
  failed.

exit status:
  0  "ok" is true
  1  a command failed, or preview or publish left a platform short of ready
     or published
  2  invalid command line
  3  doctor found the setup incomplete, or connect stopped for the operator

examples:
  amplify.py plan --link https://t.me/channelname --link https://www.linkedin.com/company/acme
  amplify.py config --root . --set AMPLIFY_REST_URL=https://service.example
  amplify.py app-token
  amplify.py connect --to telegram --link https://t.me/channelname
  amplify.py doctor --to telegram
  amplify.py preview --post content/2026-09-24-release --to telegram --to bluesky
  amplify.py publish --post content/2026-09-24-release --to telegram \\
      --media content/2026-09-24-release/cover.png

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
Show how the post lands on each platform, publishing nothing. Without the
settings file or AMPLIFY_REST_URL it stops with setup_required.

post:
  A folder holding index.md, whose Markdown is the post. --media attaches
  images and videos in the order given: JPEG, PNG, WebP, GIF, MP4, M4V, MOV
  or WebM, at most 512 MiB together. A line of its own, with blank lines
  around it, places attached files in the text: ::media{id="cover.png"}
  places one, ::media{ids="a.png,b.png" layout="collage"} or
  layout="slideshow" places several as one group. A file is placed by its file
  name, which must be up to 128 Latin letters, digits and . _ : -, start with a
  letter or digit, and be unique among the attached files. Telegram with
  --telegram-format rich and Discord keep the places and put unplaced files
  first; elsewhere files go out as attachments and changes says so. Markdown
  image syntax stays literal text. --placement post (default), reel (takes the
  first video) or story (takes the first image or video); a platform without
  reels posts the video as a post and says so in changes, one without stories
  blocks.

  --telegram-format rich sends a Telegram Rich Message: tables, nested and
  task lists, quotes, code, highlight, spoilers and formulas ($...$, fenced
  math) survive, headings become bold paragraphs, the text may run to 32768
  characters instead of 4096 (1024 with media), and up to 50 images, videos
  and GIFs go in the same message at their ::media places. A collage takes
  images and videos, a slideshow images only; any other group is split.
  Posts only. Other platforms ignore it.

output:
  destinations, one per --to:
    platform
    state           ready or blocked
    projection      the post as the platform receives it, in the platform's
                    own format, such as Telegram HTML; none when blocked
    measurements    field, unit, value and maximum after fitting (maximum
                    null: no cap); block or item locate a block or media item
    changes         what the platform changes beyond formatting: subject,
                    class, details. A text cut to fit: class approximated,
                    details.reason text_truncated, details.truncated with
                    unit, value (original length) and maximum. subject.range
                    is [start, end) in UTF-16 units of the Markdown. A placed
                    file moved to attachments: media_attached; a group split:
                    media_group_split
    media           per attached file left out or changed: file, class,
                    details, such as details.reason gateway_not_configured
    pending_checks  what the service cannot check before publishing: code,
                    file, details
    missing         [channel variable] of a platform not connected yet
    errors          code, retry, file, details; a blocked platform also lists
                    the change that blocks it, with retry null
    warnings        code, details
"""

PUBLISH_HELP = """\
Publish the post to each platform's channel; nothing is sent while a platform
has no channel. The post, --media, --placement and --telegram-format are as in
preview --help. The service may take up to 10 minutes and waits up to 300
seconds for a platform still processing the post. Never retries: after a lost
answer the post may already be out, so check the platform first.

output:
  destinations, one per --to:
    platform
    state           published, pending (still processing when the wait ended;
                    not confirmed), blocked, failed, cancelled or
                    indeterminate
    url             published only: link to the post, or null when the
                    platform gave none, the post being out all the same
    changes, media, errors, warnings
                    as in preview
"""

DOCTOR_HELP = """\
Check the setup and name the next stage, publishing nothing. With --to, also
check those platforms' channels with the platform for --placement.

Of the variables, only URLs and channel IDs are printed. A variable's source is
environment, config, store, default or null.

output:
  ok              nothing below is broken and every --to platform is verified;
                  next may still name a stage not yet done
  next            first stage not done: service, app_token, media_gateway
                  (partial or invalid only), platforms (also while no channel
                  exists), verify (platforms.unverified not empty); null when
                  done
  config          the settings file read, or null
  secret_command  where AMPLIFY_REST_SECRET_COMMAND is set: environment,
                  config or null
  service         url, variables with sources, readiness, or error (also when
                  AMPLIFY_REST_TOKEN is not a master token)
  app_token       state: none, working, or unusable with reason or error
  media_gateway   url, variables, state: complete, partial, invalid or none
  platforms:
    placement     the placement --to was checked for
    channels      per platform with a channel, or named with --to:
      channel
      state       none, connected, reauth_required (the platform rejected its
                  key or sign-in), gone (no longer on the service), or error
                  (could not be read, with error)
      title       its name on the platform, or null
      placements
      verify      with --to: state, identity, url, errors with code, retry
                  and details
    unverified    connected platforms not checked in this run
    unknown       --to values that are not platforms
    error         a verify call that failed as a whole
"""

PLAN_HELP = """\
Say what connecting each of the operator's channels takes, from the links or
addresses they give, before anything is connected. Without a working service
it still reads the links; available is then null.

output:
  service         ready, or the error code that kept the service from answering
  placement
  channels        per --link, then per --to:
    link
    platform      null: ask which platform the channel is on
    address       what the link names, as connect --link takes it
    connected     this platform's channel publishes --placement; before the
                  service answers, its channel variable is set
    title         when the channel exists: its name on the platform, or null
    available     true; false with reason; null before the service answers
    form          the way to connect, when the link settles it
    sign_in       the operator signs in on the platform
    asks          values the operator supplies before connect
    question, forms
                  what to ask the operator when the link does not settle the
                  form; their answer goes to connect as --form
"""

CONNECT_HELP = """\
Connect a platform's channel with the master token, so preview and publish
reach it. Nothing is posted. Runs the platform's steps until done or until the
operator has to act, then stops with next and the flow to continue with
--flow; give the same --to, --placement, --form and --link again. A flow
expires 15 minutes after it starts.

--link takes the operator's link or address of the channel as they gave it
and derives what the platform needs: a Telegram channel's name, a Mastodon
server, a Bluesky handle, the page to pick from a list. For a Telegram
channel without a public name, its invite link makes connect ask Telegram,
with the bot token, which channel made the bot an administrator within the
last day.

  fields    sends the values plan lists as asks; all must be set before the
            first run
  redirect  stops with url for the operator to sign in; the run with --flow
            waits up to 120 s for the sign-in
  target    takes the channel --link names; otherwise stops with targets to
            choose from (pass the title as --target) or for the link

On a platform that already has a channel, a form for another placement, such
as stories, joins that channel, and connecting it again for a placement it
has renews its key or sign-in in place. When done, AMPLIFY_CHANNEL_<PLATFORM>
is written to the settings file in effect.

output:
  ok        true once connected
  platform, flow
  step      where it stopped: fields, redirect or target
  next      what to do, then how to run again
  url, targets, missing
            what that step needs
  account   id and name of the account that will publish, such as the bot to
            make an administrator, once the platform confirmed it
  channel, title
            when connected: the channel and its name on the platform, null
            when the platform gives none
  cleared   field variables emptied in the file
  stored    field variables read from the secret store, which the operator
            may now delete there
  error     its message says what to do; on step_refused the flow stays
            open, so continue it with error.flow; a channel the platform
            refused carries reason, such as Telegram chat_not_found,
            bot_not_member, bot_not_admin or can_post_messages_missing
"""

APP_TOKEN_HELP = """\
Issue an app token with the master token and write it to the settings
file in effect as AMPLIFY_REST_APP_TOKEN, never printing it. The token it
replaces is revoked.

output:
  revoked   the replaced token's name, or null
"""

CONFIG_HELP = """\
Write NAME=value lines into DIR/.smmtp/.env.local, owner-readable only. A new
file starts from the annotated template. A value replaces the line that sets
its name, else its commented-out example, else goes on a new line; other lines
stay. An empty value leaves NAME= for the operator to fill. Secret values are
never printed.

Every value is checked first; one failure writes nothing:
  AMPLIFY_REST_URL, AMPLIFY_MEDIA_GATEWAY_URL
                                       an https root, no path or user
  AMPLIFY_REST_TOKEN, AMPLIFY_REST_APP_TOKEN
                                       name:secret
  AMPLIFY_MEDIA_GATEWAY_TOKEN          one line of visible ASCII
  AMPLIFY_CHANNEL_<PLATFORM>           ch_...
  AMPLIFY_REST_SECRET_COMMAND          parses and contains {name}
  SSL_CERT_FILE                        a bundle that loads
  DISCORD_WEBHOOK_URL                  a whole Discord webhook URL
  other names                          a value a connection asks for, checked
                                       against the service's schema when it
                                       answers, else written unchecked

DIR/.smmtp/.env.example, the shareable copy without secrets, gets the URL
values and AMPLIFY_REST_SECRET_COMMAND, and NAME= for every other name, on
empty lines only.

output:
  dry_run     true when nothing was written
  config, example, created
  changes     per name: action (fill, replace, add), secret, value (null when
              secret)
  checked, unchecked
              connection values whose format was or was not checked against
              the service; only connect proves a key works
  error       config_value_invalid with invalid listing the names refused
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


def main(argv=None, environ=None, cwd=None):
    arguments = build_parser().parse_args(argv)
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
                arguments.placement, arguments.telegram_format,
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
    return 3 if arguments.command in ("doctor", "connect") and "error" not in envelope else 1


if __name__ == "__main__":
    sys.exit(main())
