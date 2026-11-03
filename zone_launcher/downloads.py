"""Notice mod archives the user downloads in their browser, so they can be installed automatically.

When the user starts a ModDB download, the launcher remembers what it expects: the file's name,
size and MD5 from its ModDB page. Polling the Downloads folder then finds the file once it's
complete (its full size, with no browser .part file next to it), checks its MD5, and hands it over.
"""

import hashlib
import re
import shlex
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .mods import ARCHIVE_SUFFIXES

# Browsers' files for a download still in progress, next to (or instead of) the final name.
PARTIAL_SUFFIXES = (".part", ".crdownload", ".download", ".partial", ".wkdownload")


def downloads_dir(home: Path | None = None) -> Path:
    """The user's Downloads folder, as their desktop names it (~/.config/user-dirs.dirs)."""
    home = home or Path.home()
    try:
        for line in (home / ".config" / "user-dirs.dirs").read_text().splitlines():
            if line.startswith("XDG_DOWNLOAD_DIR="):
                value = shlex.split(line.split("=", 1)[1])[0]
                return Path(value.replace("$HOME", str(home)))
    except (OSError, ValueError, IndexError):
        pass
    return home / "Downloads"


def _snapshot(folder: Path) -> dict[str, tuple[int, float]]:
    found = {}
    try:
        for path in folder.iterdir():
            try:
                st = path.stat()
                found[path.name] = (st.st_size, st.st_mtime)
            except OSError:
                pass
    except OSError:
        pass
    return found


@dataclass
class Pending:
    game: str
    # The mod's name once installed (the file's title on ModDB).
    title: str
    # Saved with the mod (Mod.source): where it came from.
    origin: dict[str, str]
    filename: str | None = None
    size: int | None = None
    md5: str | None = None
    folder: Path = field(default_factory=downloads_dir)
    started: float = field(default_factory=time.time)
    # Files already in the folder when the download started, so they aren't mistaken for it.
    before: dict[str, tuple[int, float]] = field(default_factory=dict)
    # Sizes seen on the last poll, to tell when a file of unknown size has stopped growing.
    sizes: dict[str, int] = field(default_factory=dict)
    # A standalone mod: unpacks its archive and returns its folder, rather than installing it as a game's mod.
    unpack: Callable[[Path], Path] | None = None

    def __post_init__(self):
        if not self.before:
            self.before = _snapshot(self.folder)

    def name_matches(self, name: str) -> bool:
        """The expected file, or the browser's copy of it ("x(1).zip", "x (1).zip", "x-1.zip")."""
        if not self.filename:
            return False
        stem, dot, suffix = self.filename.rpartition(".")
        if not dot:
            return name.lower() == self.filename.lower()
        return re.fullmatch(rf"{re.escape(stem)}(?: ?\(\d+\)|[-_]\d+)?\.{re.escape(suffix)}", name, re.I) is not None

    def check(self) -> Path | None:
        """The downloaded archive, once it has finished; None while it's missing or in progress."""
        new = {name: info for name, info in _snapshot(self.folder).items()
               if self.before.get(name) != info}
        candidates = []
        for name, (size, _mtime) in new.items():
            if not name.lower().endswith(ARCHIVE_SUFFIXES) or name.startswith("."):
                continue
            if any(f"{name}{suffix}" in new or (self.folder / f"{name}{suffix}").exists()
                   for suffix in PARTIAL_SUFFIXES):
                continue  # Firefox creates the final name empty and writes to name.part
            if self.size is not None:
                # The exact size is the surest sign the download is complete. A new archive of that
                # size counts even under another name (the browser may have renamed it).
                if size == self.size:
                    candidates.append((not self.name_matches(name), name))
                continue
            # Unknown size: wait until it stops growing between two polls.
            settled = size > 0 and self.sizes.get(name) == size
            self.sizes[name] = size
            if settled and (self.name_matches(name) or not self.filename):
                candidates.append((not self.name_matches(name), name))
        if not candidates:
            return None
        return self.folder / sorted(candidates)[0][1]

    def verify(self, path: Path) -> bool:
        """The file is what ModDB serves (when its page gave an MD5)."""
        if not self.md5:
            return True
        digest = hashlib.md5()
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                digest.update(chunk)
        return digest.hexdigest() == self.md5


def already_downloaded(filename: str | None, size: int | None, folder: Path | None = None) -> Path | None:
    """The file in the Downloads folder, if it's all there, so it doesn't have to be downloaded again."""
    if not filename or size is None:
        return None
    path = (folder or downloads_dir()) / filename
    try:
        return path if path.stat().st_size == size else None
    except OSError:
        return None


class Watcher:
    """The downloads the user started and hasn't received yet."""

    def __init__(self):
        self.pending: list[Pending] = []

    def add(self, pending: Pending) -> None:
        # Starting the same download again replaces the earlier wait for it.
        self.pending = [p for p in self.pending if p.origin.get("file") != pending.origin.get("file")
                        or p.game != pending.game]
        self.pending.append(pending)

    def cancel(self, pending: Pending) -> None:
        self.pending = [p for p in self.pending if p is not pending]

    def for_game(self, game: str) -> list[Pending]:
        return [p for p in self.pending if p.game == game]

    def poll(self) -> list[tuple[Pending, Path]]:
        """Downloads that have finished since the last poll; they stop being pending. One file
        goes to one download only."""
        done, taken = [], set()
        for pending in list(self.pending):
            path = pending.check()
            if path and path not in taken:
                taken.add(path)
                done.append((pending, path))
                self.pending.remove(pending)
        return done
