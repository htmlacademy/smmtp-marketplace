"""preview and publish: one Markdown post, its images and videos, and what
each platform does with them."""

from __future__ import annotations

import json
import secrets
from pathlib import Path

from . import settings, transport
from .errors import Fault
from .platforms import build_body, resolve_credentials
from .settings import (
    CONFIG_PATH,
    GATEWAY_TOKEN_VARIABLE,
    GATEWAY_URL_VARIABLE,
    URL_VARIABLE,
    https_origin_problem,
)

MEDIA_KINDS = {
    ".jpg": "image",
    ".jpeg": "image",
    ".png": "image",
    ".webp": "image",
    ".gif": "gif",
    ".mp4": "video",
    ".m4v": "video",
    ".mov": "video",
    ".webm": "video",
}
# The service's ceilings: bytes for one binary part and for all of them
# together, and media items in one post.
MEDIA_MAXIMUM_BYTES = 512 * 1024 * 1024
MEDIA_MAXIMUM_COUNT = 64
CHUNK_BYTES = 1024 * 1024
# blocked goes to errors; the two formatting classes are what the projection
# already shows.
UNREPORTED_CHANGES = {"blocked", "textual_fallback", "unwrapped"}
OPERATIONS = {"preview": "analyze", "publish": "publish"}
TARGET_STATES = {"preview": "ready", "publish": "published"}
# The longest the service waits for a platform that pauses while it processes
# the post; past it that post comes back pending.
PUBLISH_WAIT_SECONDS = 300


def read_post(folder):
    path = Path(folder) / "index.md"
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise Fault(
            "post_unreadable",
            f"cannot read {path}: {error}; --post names a folder holding a UTF-8 index.md",
        )


def read_media(paths):
    if len(paths) > MEDIA_MAXIMUM_COUNT:
        raise Fault(
            "media_too_many",
            f"{len(paths)} files, over the service's {MEDIA_MAXIMUM_COUNT} per post; attach fewer,"
            " nothing was sent",
        )
    media = []
    total = 0
    for index, path in enumerate(paths, 1):
        kind = MEDIA_KINDS.get(Path(path).suffix.lower())
        if kind is None:
            raise Fault(
                "media_unsupported",
                f"neither an image nor a video: {path}; attach only the types in error.supported",
                supported=sorted(MEDIA_KINDS),
            )
        try:
            if not Path(path).is_file():
                raise FileNotFoundError("not a file")
            size = Path(path).stat().st_size
        except OSError as error:
            raise Fault("media_unreadable", f"cannot read {path}: {error}; check the path")
        if not size:
            raise Fault("media_unreadable", f"{path} is empty; check the path")
        total += size
        if size > MEDIA_MAXIMUM_BYTES or total > MEDIA_MAXIMUM_BYTES:
            raise Fault(
                "media_too_large",
                f"{path} brings the media to {total} bytes, over the service's"
                f" {MEDIA_MAXIMUM_BYTES}; attach a smaller or shorter file, nothing was sent",
            )
        media.append({"id": f"media-{index}", "kind": kind, "path": path, "size": size})
    return media


def media_gateway(lookup):
    """The gateway the manifest carries, or None. The service treats an origin
    outside its pattern as no gateway and drops the media without saying why,
    so such an origin stops the command here."""
    origin, bearer = lookup(GATEWAY_URL_VARIABLE), lookup(GATEWAY_TOKEN_VARIABLE)
    if not (origin and bearer):
        return None
    problem = https_origin_problem(origin)
    if problem:
        raise Fault(
            "gateway_url_invalid",
            f"{GATEWAY_URL_VARIABLE} {problem}; the operator corrects it, nothing was sent",
        )
    return {"origin": origin, "bearer": bearer}


class Stream:
    """A request body of byte strings and files, read in chunks as it is sent
    so a video never sits in memory whole; len() is its Content-Length."""

    def __init__(self, pieces):
        self.pieces = pieces

    def __len__(self):
        return sum(piece["size"] if isinstance(piece, dict) else len(piece) for piece in self.pieces)

    def __iter__(self):
        for piece in self.pieces:
            if isinstance(piece, bytes):
                yield piece
                continue
            remaining = piece["size"]
            try:
                with open(piece["path"], "rb") as stream:
                    while remaining:
                        chunk = stream.read(min(CHUNK_BYTES, remaining))
                        if not chunk:
                            break
                        remaining -= len(chunk)
                        yield chunk
                    if remaining or stream.read(1):
                        raise OSError("the file changed size while it was sent")
            except OSError as error:
                raise Fault(
                    "media_unreadable",
                    f"cannot send {piece['path']}: {error}; the upload stopped unfinished, so"
                    " nothing was published, and sending it again once the file stops changing"
                    " posts it once",
                )


def encode(body, media, gateway):
    """Return the request body and its content type. The service takes media
    bytes only as multipart: a manifest part, then one part per file. It has
    no media gateway of its own, so the manifest carries the caller's."""
    if not media:
        return transport.json_payload(body)
    manifest = {
        **body,
        "sources": [{"mediaId": item["id"], "partId": item["id"]} for item in media],
    }
    if gateway:
        manifest["mediaIngress"] = gateway
    parts = [
        (
            {"Content-Disposition": 'form-data; name="manifest"', "Content-Type": "application/json"},
            json.dumps(manifest, ensure_ascii=False).encode("utf-8"),
        )
    ]
    parts += [
        (
            {
                "Content-Disposition": 'form-data; name="media"',
                "Content-Type": "application/octet-stream",
                "Content-ID": f"<{item['id']}>",
            },
            item,
        )
        for item in media
    ]
    boundary = f"amplify-{secrets.token_hex(16)}"
    pieces = []
    for headers, data in parts:
        lines = "".join(f"{name}: {value}\r\n" for name, value in headers.items())
        pieces += [f"--{boundary}\r\n{lines}\r\n".encode("ascii"), data, b"\r\n"]
    pieces.append(f"--{boundary}--\r\n".encode("ascii"))
    return Stream(pieces), f"multipart/form-data; boundary={boundary}"


def coded(items, files):
    """code and details of each item, and the file of one about a media item."""
    found = []
    for item in items:
        entry = {"code": item.get("code")}
        subject = item.get("subject") or {}
        if subject.get("kind") == "media":
            entry["file"] = files.get(subject.get("id"))
        entry["details"] = item.get("details", {})
        found.append(entry)
    return found


def summarise(command, result, media):
    files = {item["id"]: item["path"] for item in media}
    destinations = []
    for outcome in result.get("outcomes", []):
        entry = {"strategy": outcome.get("strategy"), "state": outcome.get("state")}
        changes = outcome.get("changes", [])
        kept = [change for change in changes if change.get("class") not in UNREPORTED_CHANGES]
        media_changes = [change for change in kept if change.get("subject", {}).get("kind") == "media"]
        other_changes = [change for change in kept if change.get("subject", {}).get("kind") != "media"]

        problems = [
            *outcome.get("errors", []),
            *(change for change in changes if change.get("class") == "blocked"),
        ]
        errors = [
            {**entry, "retry": problem.get("retry")}
            for problem, entry in zip(problems, coded(problems, files))
        ]
        if errors:
            entry["errors"] = errors

        if command == "preview":
            # A ready outcome carries the measurement under `projection`, a
            # blocked one at the top level.
            projection = outcome.get("projection", {})
            measurement = outcome.get("measurement") or projection.get("measurement")
            if measurement:
                entry["measurement"] = measurement
            if "body" in projection:
                entry["projection"] = projection["body"]
            if outcome.get("pendingChecks"):
                entry["pending_checks"] = coded(outcome["pendingChecks"], files)
        else:
            if "url" in outcome:
                entry["url"] = outcome["url"]
            if "progress" in outcome:
                entry["next_check_at"] = outcome["progress"].get("nextCheckAt")

        if other_changes:
            entry["changes"] = [
                {"subject": change.get("subject"), "class": change.get("class"), "details": change.get("details", {})}
                for change in other_changes
            ]
        if media_changes:
            entry["media"] = [
                {
                    "file": files.get(change["subject"].get("id")),
                    "class": change.get("class"),
                    "details": change.get("details", {}),
                }
                for change in media_changes
            ]
        if outcome.get("warnings"):
            entry["warnings"] = coded(outcome["warnings"], files)
        destinations.append(entry)
    return destinations


def outcome_ok(command, destinations, missing):
    target = TARGET_STATES[command]
    return (
        bool(destinations)
        and not missing
        and all(entry["state"] == target for entry in destinations)
    )


def run(command, folder, targets, media_paths, environ, cwd, placement="post"):
    targets = list(dict.fromkeys(targets))
    markdown = read_post(folder)
    media = read_media(media_paths)
    path, _, lookup, cafile = settings.load(environ, cwd)
    if path is None and lookup(URL_VARIABLE) is None:
        raise Fault(
            "setup_required",
            f"no {CONFIG_PATH.as_posix()} at or above {cwd} and no {URL_VARIABLE} set: crossposting is"
            " not set up for this project; the amplify:setup skill sets it up, nothing was sent",
        )
    connection = transport.opener(cafile)
    url, token = transport.service(lookup)

    schemas = transport.strategy_schemas(
        transport.call(
            url, token, "GET", "/v1/capabilities", opener=connection,
            timeout=transport.CHECK_TIMEOUT_SECONDS,
        )
    )
    unknown = [strategy for strategy in targets if strategy not in schemas]
    if unknown:
        raise Fault(
            "strategy_unknown",
            f"not a platform ID the service offers: {', '.join(unknown)}; use one of"
            " error.available, nothing was sent",
            available=sorted(schemas),
        )
    try:
        credentials, missing = resolve_credentials(targets, schemas, lookup, placement)
    except (AttributeError, KeyError, TypeError):
        raise transport.unexpected_shape("GET", "/v1/capabilities")
    if command == "publish" and missing:
        raise Fault(
            "credentials_missing",
            "credential variables are not set, nothing was sent; the operator sets the"
            " variables in error.missing",
            missing=missing,
        )

    operation = OPERATIONS[command]
    path = f"/v1/{operation}"
    body = build_body(operation, markdown, targets, credentials, media, placement)
    gateway = media_gateway(lookup) if media else None
    # A fresh key per call: this script never replays or resumes a run.
    headers = (
        {"Idempotency-Key": secrets.token_hex(16), "Prefer": f"wait={PUBLISH_WAIT_SECONDS}"}
        if command == "publish"
        else None
    )
    answer = transport.call(
        url, token, "POST", path, encode(body, media, gateway), opener=connection, headers=headers
    )
    if command == "publish" and isinstance(answer, dict) and answer.get("result", {}) is None:
        raise Fault(
            "result_missing",
            "POST /v1/publish answered before the service saved a result"
            + transport.PUBLISH_ADVICE,
        )
    try:
        destinations = summarise(command, answer.get("result", {}), media)
        for entry in destinations:
            if entry["strategy"] in missing:
                entry["missing"] = missing[entry["strategy"]]
        ok = outcome_ok(command, destinations, missing)
    except (AttributeError, KeyError, TypeError):
        raise transport.unexpected_shape("POST", path)
    return {"ok": ok, "destinations": destinations}
