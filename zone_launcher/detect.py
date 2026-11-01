"""Find STALKER installs from OpenXRay's default folder, Heroic, Steam, Lutris, a search of the usual
game folders and drives, and user-added paths."""

import getpass
from contextlib import closing
import json
import os
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from .games import GAMES, GOG_IDS, STEAM_APPIDS, identify

# Written by setup.link() into an OpenXRay folder that only links to another install.
MARKER = ".zone-launcher-source"


@dataclass
class Install:
    game: str
    path: Path
    sources: list[str] = field(default_factory=list)
    # GOG ships extra files (like the English language pack) outside the game folder;
    # Heroic keeps them here: .../gog-support/<id>/<id>/
    gog_support: Path | None = None

    @property
    def label(self) -> str:
        return f"{self.path}  ({', '.join(self.sources)})"


def heroic_config_dirs(home: Path) -> list[Path]:
    return [
        home / ".config" / "heroic",
        home / ".var" / "app" / "com.heroicgameslauncher.hgl" / "config" / "heroic",
    ]


def steam_roots(home: Path) -> list[Path]:
    return [
        home / ".local" / "share" / "Steam",
        home / ".steam" / "steam",
        home / ".var" / "app" / "com.valvesoftware.Steam" / ".local" / "share" / "Steam",
    ]


def find_gog_support(home: Path, gog_id: str) -> Path | None:
    for cfg in heroic_config_dirs(home):
        support = cfg / "gogdlConfig" / "heroic_gogdl" / "gog-support" / gog_id / gog_id
        if support.is_dir():
            return support
    return None


def _heroic_installs(home: Path):
    for cfg in heroic_config_dirs(home):
        try:
            data = json.loads((cfg / "gog_store" / "installed.json").read_text())
        except (OSError, ValueError):
            continue
        entries = data.get("installed", []) if isinstance(data, dict) else data
        for entry in entries:
            key = GOG_IDS.get(str(entry.get("appName")))
            if key and entry.get("install_path"):
                yield key, Path(entry["install_path"]), "Heroic"


def _steam_libraries(root: Path) -> list[Path]:
    libs = [root]
    try:
        vdf = (root / "steamapps" / "libraryfolders.vdf").read_text(errors="replace")
    except OSError:
        return libs
    libs += [Path(p.replace("\\\\", "\\")) for p in re.findall(r'"path"\s+"([^"]+)"', vdf)]
    return libs


def _steam_installs(home: Path):
    for root in steam_roots(home):
        for lib in _steam_libraries(root):
            for appid, key in STEAM_APPIDS.items():
                try:
                    acf = (lib / "steamapps" / f"appmanifest_{appid}.acf").read_text(errors="replace")
                except OSError:
                    continue
                m = re.search(r'"installdir"\s+"([^"]+)"', acf)
                if m:
                    yield key, lib / "steamapps" / "common" / m.group(1), "Steam"


def _heroic_sideloaded(home: Path):
    # Games added to Heroic by hand ("Add game") point at their .exe.
    for cfg in heroic_config_dirs(home):
        try:
            data = json.loads((cfg / "sideload_apps" / "library.json").read_text())
        except (OSError, ValueError):
            continue
        for entry in data.get("games", []) if isinstance(data, dict) else []:
            exe = (entry.get("install") or {}).get("executable") or ""
            if exe:
                for folder in list(Path(exe).parents)[:2]:  # <game>/Stalker-COP.exe, <game>/bin/XR_3DA.exe
                    yield None, folder, "Heroic"


def lutris_dirs(home: Path) -> list[Path]:
    """Folders Lutris installed games into (a game folder, or a Wine prefix holding one)."""
    dirs = []
    for db in (home / ".local/share/lutris/pga.db",
               home / ".var/app/net.lutris.Lutris/data/lutris/pga.db"):
        if not db.is_file():
            continue
        try:
            with closing(sqlite3.connect(f"file:{db}?mode=ro", uri=True)) as conn:
                dirs += [Path(d) for (d,) in conn.execute("SELECT directory FROM games WHERE directory != ''")]
        except sqlite3.Error:
            continue
    return dirs


def _openxray_installs(home: Path):
    base = home / ".local" / "share" / "GSC Game World"
    for game in GAMES.values():
        yield game.key, base / game.pref_dir, "OpenXRay folder"
    # Other copies kept next to them, such as a hand-made copy the launcher renamed "... (own copy)".
    names = {game.pref_dir for game in GAMES.values()}
    try:
        others = sorted(p for p in base.iterdir() if p.name not in names)
    except OSError:
        return
    for folder in others:
        yield None, folder, "OpenXRay folder"


# ----- searching folders --------------------------------------------------------------------------

# Never worth looking inside: Windows itself in a Wine prefix, caches, source trees, and the game
# folders' own subfolders (a game is never inside another one).
SKIP_DIRS = {"windows", "programdata", "users", "dosdevices", "node_modules", "__pycache__", "proc",
             "sys", "dev", "lost+found", "$recycle.bin", "system volume information", "shadercache",
             "compatdata", "gamedata", "levels", "resources", "localization"}
# Subfolders of a game a user might pick instead of the game folder itself.
GAME_SUBDIRS = {"bin", "resources", "patches", "localization", "levels", "gamedata", "languages", "mp"}


def search_roots(home: Path) -> list[Path]:
    """Where games installed without a store launcher usually are: game folders, Wine prefixes
    (GOG Galaxy or a Windows installer run in Wine, Bottles), Lutris folders and other drives."""
    roots = [home / "Games", home / "games", home / "GOG Games", home / ".wine",
             home / ".local/share/bottles/bottles", home / ".var/app/com.usebottles.bottles/data/bottles/bottles",
             *lutris_dirs(home)]
    user = getpass.getuser()
    for mounts in (Path("/run/media") / user, Path("/media") / user, Path("/mnt")):
        try:
            roots += sorted(p for p in mounts.iterdir() if p.is_dir())
        except OSError:
            continue
    return roots


def search(roots: list[Path], depth: int = 5, budget: int = 20000) -> list[tuple[str, Path]]:
    """Find game folders under roots, looking at most depth folders down and at budget folders per root.

    A Wine prefix's drive_c restarts the depth count, since games sit deep inside it
    (drive_c/Program Files (x86)/GOG Galaxy/Games/<game>). Symlinks aren't followed, so a prefix's
    z: drive (/) is never walked.
    """
    found: dict[Path, str] = {}
    seen: set[Path] = set()
    for root in roots:
        stack, left_over = [(root, depth)], budget
        while stack and left_over > 0:
            folder, left = stack.pop()
            try:
                real = folder.resolve()
            except OSError:
                continue
            if real in seen:
                continue
            seen.add(real)
            left_over -= 1
            if (folder / MARKER).exists():
                continue  # a folder this launcher set up: it links to an install found elsewhere
            if key := identify(folder):
                found.setdefault(real, key)
                continue
            if left == 0:
                continue
            try:
                entries = sorted(os.scandir(folder), key=lambda e: e.name, reverse=True)
            except OSError:
                continue
            for entry in entries:
                name = entry.name.lower()
                if name.startswith(".") and name != ".wine" or name in SKIP_DIRS:
                    continue
                try:
                    if not entry.is_dir(follow_symlinks=False):
                        continue
                except OSError:
                    continue
                stack.append((Path(entry.path), 4 if name == "drive_c" else left - 1))
    return [(key, path) for path, key in found.items()]


def locate(folder: Path) -> list[tuple[str, Path]]:
    """The games in a folder the user picked: the folder itself, the game it's part of (bin/,
    resources/...), the install a launcher-made folder links to, or the games somewhere inside it."""
    folder = folder.resolve()
    try:
        source = Path((folder / MARKER).read_text().strip())
        if key := identify(source):
            return [(key, source)]
    except OSError:
        pass
    if key := identify(folder):
        return [(key, folder)]
    if folder.name.lower() in GAME_SUBDIRS and (key := identify(folder.parent)):
        return [(key, folder.parent)]
    return search([folder], depth=7, budget=100000)


def missing_files(install: "Install") -> list[str]:
    """Archives every release of the game has, that this install lacks (an incomplete download or copy)."""
    if install.game == "soc":
        needed = ["gamedata.db0"]
    else:
        needed = ["resources/configs.db", "resources/resources.db0", "resources/resources.db1",
                  "resources/resources.db2"] + (["resources/resources.db3", "resources/resources.db4"]
                                                if install.game == "cop" else [])
        needed.append("levels")
    return [rel for rel in needed if not (install.path / rel).exists()]


def patch_level(install: "Install") -> str | None:
    """The official patch installed, from the newest patches/xpatch_NN.db."""
    numbers = sorted(int(m.group(1)) for p in (install.path / "patches").glob("xpatch_*.db")
                     if (m := re.fullmatch(r"xpatch_(\d+)\.db", p.name, re.IGNORECASE)))
    if not numbers:
        return None
    return {"cs": f"1.5.{numbers[-1]:02d}", "cop": f"1.6.{numbers[-1]:02d}"}.get(install.game)


# ----- putting it together ------------------------------------------------------------------------

USER_ADDED = "Added by you"
SEARCHED = "Found by search"


def find_installs(home: Path | None = None, extra: list[str] | None = None,
                  searched: list[str] | None = None) -> list[Install]:
    """Return every install found, one entry per real folder, in a stable order.

    searched holds folders an earlier search() found; searching is too slow to repeat on every refresh.
    """
    home = home or Path.home()
    candidates = [
        *_openxray_installs(home),
        *_heroic_installs(home),
        *_heroic_sideloaded(home),
        *_steam_installs(home),
        *((None, Path(p), USER_ADDED) for p in (extra or [])),
        *((None, Path(p), SEARCHED) for p in (searched or [])),
    ]

    found: dict[Path, Install] = {}
    for key, path, source in candidates:
        try:
            real = path.resolve(strict=True)
        except OSError:
            continue
        if not real.is_dir() or (real / MARKER).exists():
            continue  # a linked OpenXRay folder is where a game runs, not another copy of it
        if real in found:
            if source not in found[real].sources and not (source == SEARCHED and found[real].sources):
                found[real].sources.append(source)
            continue
        detected = identify(real)
        # Trust the folder's own files over where we found it, but fall back to the hint.
        key = detected or key
        if key is None or (detected is None and source == "OpenXRay folder"):
            # An empty ~/.local/share/GSC Game World/<game> is just engine leftovers.
            continue
        found[real] = Install(key, real, [source], find_gog_support(home, GAMES[key].gog_id))

    return sorted(found.values(), key=lambda i: (list(GAMES).index(i.game), str(i.path)))
