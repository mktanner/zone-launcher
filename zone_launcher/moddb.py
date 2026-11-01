"""Browse ModDB: each game's mod and addon lists, a mod's files, and the details of one file.

Only pages a visitor's browser would load are fetched, one at a time as the user asks for them: the
first page of a list (in one of SORTS' orders), a mod's downloads, and a file's own page. Sorted lists
use a ?sort= link, which robots.txt asks crawlers not to fetch; the launcher fetches one when the user
picks that order, like a browser would, never page after page. Downloading is left to the user's
browser, since ModDB doesn't allow automated downloads. Everything is cached, so browsing back and
forth doesn't fetch pages again.
"""

import hashlib
import html
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote_plus, urlsplit

from . import VERSION
from .games import GAMES

SITE = "https://www.moddb.com"
HOSTS = {"www.moddb.com", "moddb.com", "media.moddb.com"}
USER_AGENT = f"ZoneLauncher/{VERSION} (+https://github.com/mktanner/zone-launcher)"
# Lists change a few times a day; a file's page (its size and checksum) hardly ever.
PAGE_TTL = 60 * 60
DETAILS_TTL = 24 * 60 * 60
IMAGE_TTL = 7 * 24 * 60 * 60
KINDS = ("mods", "addons")
# (key, label, ?sort= value for mods, for addons). "" is ModDB's own order for that list.
SORTS = [
    ("updated", "Recently updated", "", ""),
    ("popular", "Popular all time", "visitstotal-desc", "visitstotal-desc"),
    ("today", "Popular today", "ranktoday-asc", "ranktoday-asc"),
    ("added", "Newly added", "id-desc", "date-desc"),
]
SORT_VALUES = {value for _key, _label, *values in SORTS for value in values if value}

# Words in a mod's name or summary that suggest it won't work on OpenXRay with the original game:
# (pattern, what to tell the user). Only a hint; the mod's page has the facts.
COMPAT_HINTS = [
    # "Anomaly" alone is also the in-game hazard ("anomaly detector"), so only the game's own names.
    (r"\b(s\.?t\.?a\.?l\.?k\.?e\.?r\.?:? anomaly|anomaly 1\.\d|(for|on|requires) anomaly|anomaly[- ]based"
     r"|anomaly (mod|modpack|addon))\b", "mentions Anomaly, a separate game on its own engine"),
    (r"(?-i:\bGAMMA\b|G\.A\.M\.M\.A)", "mentions GAMMA, a modpack for Anomaly (a separate game)"),
    (r"\bstand-?alone\b", "says it's standalone, so it may be a separate game rather than a mod"),
    (r"\bogsr\b", "mentions OGSR, an engine of its own"),
    (r"\bogse\b", "mentions OGSE, which ships its own engine"),
    (r"x-?ray monolith|monolith\)? engine", "mentions the X-Ray Monolith engine"),
    (r"\b(co-?op|multiplayer)\b", "co-op and multiplayer mods need their own engine"),
    (r"\b(own|new|modified|custom|modded) (engine|exes?)\b|\bengine exes?\b", "mentions its own engine"),
]


class ModDBError(Exception):
    pass


@dataclass
class Item:
    """A mod or an addon in one of a game's lists."""
    title: str
    url: str
    summary: str
    image: str
    updated: str  # ISO date of the last update
    # "mod" (has files of its own), or "addon" or "file" (is a download).
    kind: str
    # ModDB's category: "Full Version", "Patch", "Music"... (files and addons only).
    category: str = ""
    # In a list sorted by popularity, ModDB shows the rank and today's visits instead of a date.
    stat: str = ""

    def compat_hint(self) -> str | None:
        return compat_hint(f"{self.title} {self.summary}")


@dataclass
class Details:
    """What a file's own page says about the download."""
    url: str
    title: str
    filename: str | None
    size: int | None
    md5: str | None
    # The page that starts the download in a browser.
    start: str | None


# ----- fetching ------------------------------------------------------------------------------------

def cache_dir() -> Path:
    base = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    return base / "zone-launcher" / "moddb"


def allowed(url: str) -> bool:
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.hostname not in HOSTS:
        return False
    # No query strings, except a list's sort order.
    return not parts.query or (parts.query.startswith("sort=") and parts.query[5:] in SORT_VALUES)


def fetch(url: str, ttl: int = PAGE_TTL) -> bytes:
    """The body of a ModDB page, from the cache while it's fresh. Falls back to a stale copy when
    the site can't be reached."""
    if not allowed(url):
        raise ModDBError(f"Not a ModDB page the launcher reads: {url}")
    cached = cache_dir() / hashlib.sha1(url.encode()).hexdigest()
    try:
        if time.time() - cached.stat().st_mtime < ttl:
            return cached.read_bytes()
    except OSError:
        pass
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            body = response.read()
    except (urllib.error.URLError, OSError) as e:
        if cached.is_file():
            return cached.read_bytes()
        if isinstance(e, urllib.error.HTTPError) and e.code == 404:
            raise ModDBError("ModDB has no such page (any more).") from e
        raise ModDBError(f"Couldn't reach ModDB: {getattr(e, 'reason', None) or e}") from e
    cached.parent.mkdir(parents=True, exist_ok=True)
    tmp = cached.with_suffix(".tmp")
    tmp.write_bytes(body)
    tmp.replace(cached)
    return body


# ----- addresses -----------------------------------------------------------------------------------

def list_url(game: str, kind: str, sort: str = "updated") -> str:
    """A game's mods or addons, in the order of one of SORTS."""
    value = next((mods if kind == "mods" else addons for key, _l, mods, addons in SORTS if key == sort), "")
    return f"{SITE}/games/{GAMES[game].moddb}/{kind}" + (f"?sort={value}" if value else "")


def search_url(game: str, kind: str, words: str) -> str:
    """ModDB's own search of a game's list, for the user's browser."""
    base = f"{SITE}/games/{GAMES[game].moddb}/{kind}"
    return f"{base}?filter=t&kw={quote_plus(words)}" if words else base


def all_files_url(mod_url: str) -> str:
    return f"{mod_url.rstrip('/')}/downloads"


def parse_link(text: str) -> tuple[str, str] | None:
    """A ModDB link someone pasted -> ("mod", mod page) or ("file", file page); None if it's
    neither. Accepts links to anything under a mod (its images, a file) and to addons."""
    text = text.strip()
    if not re.match(r"(https?://)?(www\.)?moddb\.com/", text):
        return None
    path = urlsplit(text if "://" in text else f"https://{text}").path.rstrip("/")
    if m := re.fullmatch(r"/mods/([\w-]+)/downloads/([\w-]+)", path):
        return "file", f"{SITE}/mods/{m[1]}/downloads/{m[2]}"
    if m := re.fullmatch(r"/(games/[\w-]+/)?(addons|downloads)/([\w-]+)", path):
        return "file", f"{SITE}{path}"
    if m := re.match(r"/mods/([\w-]+)", path):
        return "mod", f"{SITE}/mods/{m[1]}"
    return None


# ----- parsing -------------------------------------------------------------------------------------

def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


def compat_hint(text: str) -> str | None:
    for pattern, hint in COMPAT_HINTS:
        if re.search(pattern, text, re.I):
            return hint
    return None


def parse_list(page: str, kind: str) -> list[Item]:
    """The entries of a list page: a game's "mods" or "addons", or a mod's "files"."""
    items = []
    for block in page.split('<div class="row rowcontent')[1:]:
        link = re.search(r'<h4><a href="([^"]+)">(.*?)</a>', block, re.S)
        if not link:
            continue
        image = re.search(r'<img src="([^"]+)"', block)
        updated = re.search(r'<span class="date">\s*<time datetime="([^"T]+)', block)
        summary = re.search(r"<p>(.*?)</p>", block, re.S)
        category = re.search(r'<span class="subheading">(.*?)</span>', block, re.S)
        stat = re.search(r'<span class="date">\s*<span title="([^"]*)"[^>]*>\s*([\d,]+)', block)
        items.append(Item(
            title=_text(link[2]),
            url=SITE + link[1] if link[1].startswith("/") else link[1],
            summary=_text(summary[1]) if summary else "",
            image=image[1] if image else "",
            updated=updated[1] if updated else "",
            kind={"addons": "addon", "files": "file"}.get(kind, "mod"),
            # A mod's subheading is its release date and genre, not a category.
            category=re.sub(r"\d+ comments?", "", _text(category[1])).strip() if category and kind != "mods" else "",
            stat=f"Rank {stat[2]} on ModDB, {html.unescape(stat[1])}" if stat else "",
        ))
    return items


def parse_mod_name(page: str) -> str:
    """The mod's name from the title of one of its pages ("Downloads - <name> mod for <game> - ModDB")."""
    m = re.search(r"<title>(?:[^<]*? - )?([^<]*) mod for [^<]* - ModDB</title>", page)
    return html.unescape(m[1]).strip() if m else ""


def _field(page: str, name: str) -> str | None:
    m = re.search(rf"<h5>{name}</h5>\s*<span class=\"summary\">\s*(.*?)\s*</span>", page, re.S)
    return _text(m[1]) if m else None


def parse_details(page: str, url: str) -> Details:
    title = re.search(r'<meta property="og:title" content="([^"]*)"', page)
    size = _field(page, "Size")
    size_bytes = re.search(r"\(([\d,]+) bytes\)", size or "")
    md5 = _field(page, "MD5 Hash")
    start = re.search(r'href="(/(?:downloads|addons)/start/\d+)"', page)
    return Details(
        url=url,
        title=re.sub(r" (file|addon)( - [^-]*)?$", "", html.unescape(title[1])) if title else "",
        filename=_field(page, "Filename"),
        size=int(size_bytes[1].replace(",", "")) if size_bytes else None,
        md5=md5.lower() if md5 and re.fullmatch(r"[0-9a-fA-F]{32}", md5) else None,
        start=SITE + start[1] if start else None,
    )


# ----- what the UI asks for ------------------------------------------------------------------------

def browse(game: str, kind: str, sort: str = "updated") -> list[Item]:
    """The first page (30) of a game's mods or addons, in the order of one of SORTS."""
    return parse_list(fetch(list_url(game, kind, sort)).decode("utf-8", "replace"), kind)


def files(mod_url: str) -> tuple[str, list[Item]]:
    """A mod's name and its files (the first page of its downloads: up to 30, newest first)."""
    page = fetch(all_files_url(mod_url)).decode("utf-8", "replace")
    return parse_mod_name(page), parse_list(page, "files")


def details(file_url: str) -> Details:
    return parse_details(fetch(file_url, ttl=DETAILS_TTL).decode("utf-8", "replace"), file_url)


def image(url: str) -> bytes:
    return fetch(url, ttl=IMAGE_TTL)
