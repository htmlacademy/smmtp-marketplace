from __future__ import annotations

import re
import urllib.parse

TELEGRAM_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]{3,31}")
HANDLE = re.compile(r"@?([A-Za-z0-9_.-]{1,64})")
WEBHOOK = re.compile(r"https://(?:ptb\.|canary\.)?discord(?:app)?\.com/api/webhooks/(\d+)/([A-Za-z0-9_-]+)/?")
HOSTS = {
    "t.me": "telegram",
    "telegram.me": "telegram",
    "telegram.dog": "telegram",
    "vk.com": "vk",
    "vk.ru": "vk",
    "m.vk.com": "vk",
    "x.com": "twitter",
    "twitter.com": "twitter",
    "mobile.twitter.com": "twitter",
    "bsky.app": "bluesky",
    "linkedin.com": "linkedin",
    "youtube.com": "youtube",
    "m.youtube.com": "youtube",
    "facebook.com": "facebook",
    "fb.com": "facebook",
    "m.facebook.com": "facebook",
    "instagram.com": "instagram",
    "threads.net": "threads",
    "threads.com": "threads",
    "discord.com": "discord",
    "discordapp.com": "discord",
    "tiktok.com": "tiktok",
    "max.ru": "max",
    "web.max.ru": "max",
}
QUESTIONS = {
    "instagram": "Is the Instagram account linked to a Facebook page? Linked: --form facebook-login-token; otherwise --form instagram-login-token",
    "linkedin": "Do they publish as themselves (--form member) or as a company page (--form organization)?",
}


def found(platform, address=None, form=None, private=False):
    return {
        "platform": platform,
        "address": address,
        "form": form,
        "private": private,
        "question": QUESTIONS.get(platform) if form is None else None,
    }


def parse(text, platform=None):
    text = text.strip()
    if platform == "telegram" or (platform is None and text.startswith("tg://")):
        bare = telegram_address(text)
        if bare:
            return bare
    if re.fullmatch(r"@[^@\s]+@[^@\s]+\.[^@\s]+", text):
        user, host = text[1:].split("@", 1)
        return {**found("mastodon", f"@{user}@{host.lower()}"), "host": host.lower()}
    if WEBHOOK.fullmatch(text):
        return found("discord")
    parts = urllib.parse.urlsplit(text if "://" in text else f"https://{text}")
    host = (parts.hostname or "").lower().removeprefix("www.")
    path = [segment for segment in parts.path.split("/") if segment]
    if host in HOSTS:
        return from_url(HOSTS[host], path, parts.query)
    if host.endswith(".slack.com"):
        channel = path[1] if len(path) > 1 and path[0] == "archives" else None
        return found("slack", channel)
    if host and path and path[0].startswith("@") and len(path) == 1:
        return {**found("mastodon", f"{path[0]}@{host}"), "host": host}
    if platform and re.fullmatch(r"-?\d+", text):
        return found(platform, text)
    if platform and HANDLE.fullmatch(text):
        return found(platform, text if text.startswith("@") else f"@{text}")
    return found(platform)


def telegram_address(text):
    if re.fullmatch(r"-?\d{5,20}", text):
        return found("telegram", text)
    if text.startswith("@") and TELEGRAM_NAME.fullmatch(text[1:]):
        return found("telegram", text)
    if text.startswith("tg://"):
        domain = urllib.parse.parse_qs(urllib.parse.urlsplit(text).query).get("domain", [""])[0]
        return found("telegram", f"@{domain}") if TELEGRAM_NAME.fullmatch(domain) else None
    if TELEGRAM_NAME.fullmatch(text):
        return found("telegram", f"@{text}")
    return None


def from_url(platform, path, query):
    first = path[0] if path else ""
    if platform == "telegram":
        if first in ("joinchat",) or first.startswith("+"):
            return found("telegram", private=True)
        name = path[1] if first == "s" and len(path) > 1 else first
        return found("telegram", f"@{name}" if TELEGRAM_NAME.fullmatch(name) else None)
    if platform == "vk":
        community = re.fullmatch(r"(?:club|public|event)(\d+)", first)
        user = re.fullmatch(r"id(\d+)", first)
        if community:
            return found("vk", f"-{community.group(1)}")
        return found("vk", user.group(1) if user else (first or None))
    if platform == "linkedin":
        if first in ("company", "showcase", "school") and len(path) > 1:
            return found("linkedin", path[1], "organization")
        if first == "in" and len(path) > 1:
            return found("linkedin", path[1], "member")
        return found("linkedin")
    if platform == "youtube":
        if first.startswith("@") or (first in ("channel", "c", "user") and len(path) > 1):
            return found("youtube", first if first.startswith("@") else path[1])
        return found("youtube")
    if platform == "facebook":
        page = urllib.parse.parse_qs(query).get("id", [None])[0] if first == "profile.php" else first
        return found("facebook", page or None)
    if platform == "bluesky":
        return found("bluesky", path[1] if first == "profile" and len(path) > 1 else None)
    if platform == "discord":
        return found("discord", path[2] if first == "channels" and len(path) > 2 else None)
    if platform in ("twitter", "threads", "tiktok", "instagram"):
        name = first.removeprefix("@")
        return found(platform, f"@{name}" if name else None)
    return found(platform, first or None)


def webhook_parts(url):
    match = WEBHOOK.fullmatch((url or "").strip())
    return match.groups() if match else None


def same_name(a, b):
    def fold(value):
        return re.sub(r"[^0-9a-z]", "", (value or "").casefold())

    return bool(fold(a)) and fold(a) == fold(b)
