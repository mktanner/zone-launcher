"""Popular standalone mods to suggest. What to download is looked up on ModDB when it's asked for,
so a suggestion stays right as its mod releases new versions."""

import re
from dataclasses import dataclass

from . import moddb


@dataclass(frozen=True)
class Suggestion:
    name: str
    summary: str
    # Where to read about it, and on ModDB, where its files are.
    page: str
    # Finds its folder among the mods already added.
    folder: str
    # The title of its full version among its files on ModDB. None: it isn't one download.
    full_version: str | None = None
    # The button for a mod that isn't one download.
    guide: str | None = None


SUGGESTIONS = (
    Suggestion("STALKER Complete", "Shadow of Chernobyl restored and updated for today's screens, with Linux support.",
               "https://www.moddb.com/mods/stalker-complete-2009", r"stalker.?complete",
               full_version=r"STALKER Complete 1\.\d[\d.]*"),
    Suggestion("Anomaly", "A sandbox set across the Zone, and the base for most of today's addons and modpacks.",
               "https://www.moddb.com/mods/stalker-anomaly", r"anomaly",
               full_version=r"STALKER Anomaly \d[\d.]*"),
    Suggestion("G.A.M.M.A.", "The most popular Anomaly modpack. It needs Anomaly, then installs with its own tool "
                             "rather than as one download: follow the guide, then add its folder here with +.",
               "https://github.com/Mord3rca/gamma-launcher", r"gamma", guide="Install Guide"),
    Suggestion("Radiophobia 3", "A story-driven campaign, and one of the most polished standalone mods.",
               "https://www.moddb.com/mods/radiophobia", r"radiophobia",
               full_version=r"Radiophobia 3 ver\. [\d.]+"),
    Suggestion("Call of Chernobyl", "The free-play sandbox Anomaly grew out of, with 32 maps. Its page asks for an installed copy of "
                                    "Call of Pripyat.",
               "https://www.moddb.com/mods/call-of-chernobyl", r"call.?of.?chernobyl",
               full_version=r"\[Release\] Call of Chernobyl [\d.]+"),
    Suggestion("Lost Alpha", "The cut content of the original game made real. It comes as an installer in six parts: "
                             "get it from its page, then add its folder here with +.",
               "https://www.moddb.com/mods/lost-alpha", r"lost.?alpha", guide="Open Page"),
    Suggestion("Dead Air", "A hardcore survival sandbox on Call of Chernobyl. Its page links to the download: get it "
                           "there, then add its folder here with +.",
               "https://www.moddb.com/mods/dead-air", r"dead.?air", guide="Open Page"),
    Suggestion("True Stalker", "A long story campaign. ModDB only has its patches: get the game "
                               "from its page, then add its folder here with +.",
               "https://www.moddb.com/mods/true-stalker", r"true.?stalker", guide="Open Page"),
)


def added(suggestion: Suggestion, folders: list[str]) -> bool:
    """Whether one of the folders added looks like this mod, by its name."""
    return any(re.search(suggestion.folder, folder.rstrip("/").rpartition("/")[2], re.I) for folder in folders)


def latest_full_version(suggestion: Suggestion, items: list[moddb.Item]) -> moddb.Item | None:
    full = [item for item in items if item.category == "Full Version"
            and re.fullmatch(suggestion.full_version or "", item.title)]
    return max(full, key=lambda item: item.updated, default=None)


def find_release(suggestion: Suggestion) -> tuple[moddb.Item, moddb.Details] | None:
    """The newest full version on ModDB and what its page says about the download. Slow: it fetches pages."""
    item = latest_full_version(suggestion, moddb.files(suggestion.page)[1])
    return (item, moddb.details(item.url)) if item else None
