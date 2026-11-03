"""What's in a game folder and where each file comes from, for the file browser."""

import os
from dataclasses import dataclass
from pathlib import Path

from . import mods
from .detect import MARKER
from .fixes import INACTIVE, LANGUAGE_PACK

LAUNCHER_FILES = {"fsgame.ltx", "fsgame.ltx.original", MARKER}
REAL_TOP = {("gamedata",), ("localization",), ("patches",)}


@dataclass
class Entry:
    path: Path
    is_dir: bool
    # Bytes for a file (what a link points to); None for a folder or a broken link.
    size: int | None
    # "install", "mod", "modfile", "engine", "link", "launcher", "saves", "yours", "folder" or "missing".
    origin: str
    detail: str
    # Removing it can't break the game: a broken link, or a file the player (or a tool) put there.
    removable: bool = False

    @property
    def name(self) -> str:
        return self.path.name


def _under(path: str | Path, root: Path) -> bool:
    try:
        return os.path.commonpath([os.path.abspath(path), str(root)]) == str(root)
    except ValueError:
        return False


class StandaloneClassifier:
    """Says what the files of a standalone mod's folder are. It's all the mod's own, with no links or installs."""

    def __init__(self, folder: Path, app_data: Path, protected: set[Path]):
        self.folder = folder
        self.app_data = app_data
        # What the mod can't start without.
        self.protected = protected

    def read_only(self, _folder: Path) -> bool:
        return False

    def entry(self, path: Path) -> Entry:
        is_dir = path.is_dir()
        saves = _under(path, self.app_data)
        origin = "folder" if is_dir else "saves" if saves else "modfile"
        detail = "Saved game" if _under(path, self.app_data / "savedgames") else "Settings and logs" if saves else ""
        if is_dir:
            try:
                count = len(os.listdir(path))
                detail = f"{count} item{'s' if count != 1 else ''}"
            except OSError:
                detail = ""
        size = None
        if not is_dir:
            try:
                size = path.stat().st_size
            except OSError:
                pass
        return Entry(path, is_dir, size, origin, detail, not is_dir and path not in self.protected)

    def list(self, folder: Path) -> list[Entry]:
        try:
            paths = list(folder.iterdir())
        except OSError:
            return []
        return sorted((self.entry(p) for p in paths), key=lambda e: (not e.is_dir, e.name.lower()))


class Classifier:
    """Says where the files of one game's folders come from."""

    def __init__(self, game: str, install: Path, run: Path, engine_share: Path | None = None):
        self.game = game
        self.install = install
        self.run = run
        self.engine_share = engine_share
        self.mod_names = {m.id: m.name for m in mods.list_mods(game)}
        self.library = mods.library_dir(game)

    def read_only(self, folder: Path) -> bool:
        """The original install (when the game plays from a linked folder) is never written to."""
        return self.run != self.install and _under(folder, self.install) and not _under(folder, self.run)

    def _link(self, path: Path) -> tuple[str, str, bool]:
        target = os.readlink(path)
        if not os.path.isabs(target):
            target = os.path.join(path.parent, target)
        if not path.exists():
            return "missing", f"Missing: links to {target}", True
        if _under(target, self.install):
            return "install", "Game install", False
        if _under(target, self.library):
            mod_id = Path(target).relative_to(self.library).parts[0]
            return "mod", f"Mod: {self.mod_names.get(mod_id, mod_id)}", False
        if self.engine_share and _under(target, self.engine_share) or "/share/openxray/" in target:
            return "engine", "Engine (OpenXRay)", False
        return "link", f"Link to {target}", False

    def _real(self, path: Path, is_dir: bool) -> tuple[str, str, bool]:
        if self.read_only(path) or self.run == self.install and not _under(path, self.run / "_appdata_"):
            if is_dir:
                return "folder", "", False
            # Playing from the install itself: only the launcher's own files can be told apart.
            if path.parent == self.run and path.name in LAUNCHER_FILES:
                return "launcher", "Written by the launcher", False
            return "install", "Game install", False
        rel = path.relative_to(self.run).parts if _under(path, self.run) else ()
        if rel[:1] == ("_appdata_",):
            if is_dir:
                return "folder", "Saves, settings and logs" if len(rel) == 1 else "", False
            if rel[1:2] == ("savedgames",):
                return "saves", "Saved game", True
            return "saves", "Settings, logs and caches", True
        if is_dir:
            # One of the folders setup makes real, so mods and the launcher can add files next to links.
            if tuple(rel) in REAL_TOP:
                return "folder", "Game install with mod and launcher files", False
            return "folder", "Language packs not in use" if path.name == INACTIVE else "", False
        if len(rel) == 1 and path.name in LAUNCHER_FILES:
            return "launcher", "Written by the launcher", False
        if rel[:1] == ("localization",) and (LANGUAGE_PACK.match(path.name) or INACTIVE in rel):
            return "launcher", "Language pack (copied by the launcher)", False
        return "yours", "Added by you or another tool", True

    def entry(self, path: Path) -> Entry:
        is_dir = path.is_dir()
        if path.is_symlink():
            origin, detail, removable = self._link(path)
        else:
            origin, detail, removable = self._real(path, is_dir)
        if is_dir:
            try:
                count = len(os.listdir(path))
                detail = ", ".join(filter(None, [detail, f"{count} item{'s' if count != 1 else ''}"]))
            except OSError:
                pass
        if self.read_only(path):
            removable = False
        size = None
        if not is_dir and origin != "missing":
            try:
                size = path.stat().st_size
            except OSError:
                pass
        return Entry(path, is_dir, size, origin, detail, removable)

    def list(self, folder: Path) -> list[Entry]:
        try:
            paths = list(folder.iterdir())
        except OSError:
            return []
        entries = [self.entry(p) for p in paths]
        return sorted(entries, key=lambda e: (not e.is_dir, e.name.lower()))


def human_size(size: int) -> str:
    for unit in ("bytes", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size} {unit}" if unit == "bytes" else f"{size:.1f} {unit}"
        size /= 1024
    return ""


def summary(entries: list[Entry]) -> str:
    """One line about a folder's contents: how many items, how big, and where they come from."""
    if not entries:
        return "Empty folder"
    total = sum(e.size or 0 for e in entries)
    counts: dict[str, int] = {}
    for e in entries:
        if not e.is_dir:
            label = {"install": "from the install", "mod": "from mods", "launcher": "from the launcher",
                     "yours": "added by you", "saves": "saves and settings", "missing": "missing",
                     "engine": "from the engine", "link": "other links", "modfile": "mod files"}[e.origin]
            counts[label] = counts.get(label, 0) + 1
    folders = sum(e.is_dir for e in entries)
    parts = [f"{len(entries)} item{'s' if len(entries) != 1 else ''}"]
    if total:
        parts.append(human_size(total))
    parts += [f"{n} {label}" for label, n in counts.items()]
    if folders:
        parts.append(f"{folders} folder{'s' if folders != 1 else ''}")
    return " · ".join(parts)
