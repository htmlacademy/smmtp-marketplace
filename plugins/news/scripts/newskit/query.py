"""Turn command-line filters into a request the service accepts. The service
rejects a bad value or combination without saying which part, so its
documented rules are checked here, where the refusal can name the argument."""

from __future__ import annotations

import re
from datetime import date, timedelta

from .errors import UsageFault

FINDING_FIELDS = (
    "id", "url", "host", "title", "date", "digest_date", "language", "categories", "author",
    "site_name", "text_preview",
)
# Fields the service knows under another name.
SERVICE_NAMES = {"digest_date": "first_import_day"}
DEFAULT_FINDING_FIELDS = ("date", "title", "url")
# The service's filter for an item whose page the archive fetched and
# processed; the rest are links it only recorded.
PROCESSED = "enriched"
SORTS = ("date_desc", "date_asc")
PAGE_MAX = 100
PREVIEW_MAX = 65536
# The longest period of digest days the service takes in one search.
MAX_DAYS = 31
# (flag, attribute holding its values, the most values the service takes)
CEILINGS = (
    ("--id", "ids", 100), ("--url", "urls", 100), ("--host", "hosts", 100),
    ("--author", "authors", 100), ("--language", "languages", 100),
    ("--category", "category", 20), ("--category-all", "category_all", 20),
    ("--exclude-category", "exclude_category", 20),
)
# A --host value with this prefix is the service's domain filter: the site
# itself and every subdomain. The service takes fewer of those than hosts.
WITH_SUBDOMAINS = "*."
DOMAINS_MAX = 20
HEX_ID = re.compile(r"[0-9a-f]{24}")
NOT_A_HOST = re.compile(r"[\s/:@*]")
HTTP_URL = re.compile(r"https?://\S+")


def parse_fields(text: str | None, allowed: tuple, default: tuple) -> list:
    if text is None:
        return list(default)
    if text == "all":
        return list(allowed)
    names = text.split(",")
    unknown = [name for name in names if name not in allowed]
    if unknown or len(set(names)) != len(names):
        raise UsageFault(
            f"--fields takes distinct names from {', '.join(allowed)}, separated by commas, or"
            f" 'all'; got {text!r}"
        )
    return names


def period(start: date | None, end: date | None, days: int | None, today: date):
    """The inclusive (first day, last day) the three arguments name, or None
    when none of them is given."""
    if days is not None:
        if start or end:
            raise UsageFault("--days cannot be combined with --from or --to")
        return today - timedelta(days=days - 1), today
    if start is None:
        if end is not None:
            raise UsageFault("--to needs --from")
        return None
    if end is None:
        if start > today:
            raise UsageFault(f"--from {start} is after today, {today}")
        return start, today
    if end < start:
        raise UsageFault(f"--to {end} is before --from {start}")
    return start, end


def bounds(dates) -> dict:
    """The service's half-open form of an inclusive period."""
    return {"gte": dates[0].isoformat(), "lt": (dates[1] + timedelta(days=1)).isoformat()}


def sites(hosts: list, wide: bool) -> list:
    """The names given with the *. prefix, without it, or the plain ones."""
    if wide:
        return [host[len(WITH_SUBDOMAINS):] for host in hosts if host.startswith(WITH_SUBDOMAINS)]
    return [host for host in hosts if not host.startswith(WITH_SUBDOMAINS)]


def check_values(arguments) -> None:
    for flag, name, ceiling in CEILINGS:
        values = getattr(arguments, name)
        if len(values) > ceiling:
            raise UsageFault(f"{flag} takes at most {ceiling} values")
        if "" in values:
            raise UsageFault(f"{flag} takes a value that is not empty")
    for host in arguments.hosts:
        name = host[len(WITH_SUBDOMAINS):] if host.startswith(WITH_SUBDOMAINS) else host
        if NOT_A_HOST.search(name) or (name != host and "." not in name.strip(".")):
            raise UsageFault(
                "--host takes a bare host name such as example.org, or *.example.org for the"
                f" site with its subdomains, without scheme, path or port; got {host!r}"
            )
    wide = sites(arguments.hosts, wide=True)
    if wide and len(wide) != len(arguments.hosts):
        raise UsageFault(
            "--host takes either plain names or *. names in one search, not both; run two"
            " searches"
        )
    if len(wide) > DOMAINS_MAX:
        raise UsageFault(f"--host takes at most {DOMAINS_MAX} *. names")
    for url in arguments.urls:
        if not HTTP_URL.fullmatch(url):
            raise UsageFault(f"--url takes a full http or https URL; got {url!r}")
    if arguments.ids and arguments.urls:
        raise UsageFault("--id and --url exclude each other; search by one of them")
    shared = sorted(set(arguments.category_all) & set(arguments.exclude_category))
    if shared:
        raise UsageFault(f"{', '.join(shared)} is in both --category-all and --exclude-category")


def search_request(arguments, fields: list, today: date):
    """(body, period) for POST /api/findings/search. The period is digest
    days, today when none is given; an item named by ID or URL is looked up
    whatever its day and state."""
    check_values(arguments)
    named = bool(arguments.ids or arguments.urls)
    dates = period(arguments.date_from, arguments.date_to, arguments.date_days, today)
    if dates is None and not named:
        dates = (today, today)
    if dates and (dates[1] - dates[0]).days >= MAX_DAYS:
        raise UsageFault(
            f"a search takes a period of at most {MAX_DAYS} days, and this one spans"
            f" {(dates[1] - dates[0]).days + 1}; split it into several commands"
        )
    if arguments.preview is not None and "text_preview" not in fields:
        raise UsageFault("--preview sets the length of text_preview; add it to --fields")

    categories = {
        key: values
        for key, values in (
            ("any", arguments.category),
            ("all", arguments.category_all),
            ("none", arguments.exclude_category),
        )
        if values
    }
    filters = {
        key: value
        for key, value in (
            ("ids", arguments.ids),
            ("urls", arguments.urls),
            ("hosts", sites(arguments.hosts, wide=False)),
            ("domains", sites(arguments.hosts, wide=True)),
            ("authors", arguments.authors),
            ("languages", arguments.languages),
            (SERVICE_NAMES["digest_date"], bounds(dates) if dates else None),
            ("categories", categories),
        )
        if value
    }
    if not (arguments.all or named):
        filters[PROCESSED] = True
    body = {
        "fields": [SERVICE_NAMES.get(name, name) for name in fields],
        "sort": arguments.sort,
        "limit": arguments.limit,
    }
    if arguments.preview is not None:
        body["preview"] = arguments.preview
    if filters:
        body["filter"] = filters
    return body, dates
