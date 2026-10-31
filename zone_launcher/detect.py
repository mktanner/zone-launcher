"""Find STALKER installs from OpenXRay's default folder, Heroic, Steam and user-added paths."""

import json
import re
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


def _openxray_installs(home: Path):
    base = home / ".local" / "share" / "GSC Game World"
    for game in GAMES.values():
        yield game.key, base / game.pref_dir, "OpenXRay folder"


def find_installs(home: Path | None = None, extra: list[str] | None = None) -> list[Install]:
    """Return every install found, one entry per real folder, in a stable order."""
    home = home or Path.home()
    candidates = [
        *_openxray_installs(home),
        *_heroic_installs(home),
        *_steam_installs(home),
        *((None, Path(p), "Added by you") for p in (extra or [])),
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
            if source not in found[real].sources:
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
