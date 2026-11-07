"""Standalone mods: complete games in one folder, with their own data and modified Windows engine.

OpenXRay can't run them, since their changes are compiled into that engine, so they run with the
system's Wine, a Wine build or a version of Proton, all on the host: from inside the Flatpak, through
flatpak-spawn. The folder is used where it is and saves stay in it (its fsgame.ltx says so); only the
Wine prefix, Wine's log and the libraries installed for the mod live elsewhere. With addons switched
on, the game starts from a folder of links instead: the mod's files with the addons' on top.
"""

import hashlib
import os
import re
import shlex
import shutil
import subprocess
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

from . import detect, mods, pe, runtimes
from .engine import engine_environment
from .games import GAMES

ICONS = ("Linux/icon.png", "icon.png")
# Run the DXVK and runtime DLLs a mod bundles, not Wine's own. BugTrap needs Wine's dbghelp.
DLL_OVERRIDES = "dbghelp=b;d3dcompiler_43=n,b;msvcr71=n,b;msvcr80=n,b;msvcp71=n,b;msvcp80=n,b;d3d9=n,b"
# Without Wine Mono and Gecko, which a new prefix may otherwise offer to download. Only .NET programs need them.
NO_DOTNET = "mscoree=;mshtml="
# Where to look for programs, and what's never the one that starts the game.
SEARCH_DEPTH = 2
SKIP_FOLDERS = {"dedicated", "tools", "setup", "redist", "_commonredist", "directx", "support", "appdata", "_appdata_",
                "gamedata", "db", "resources", "levels", "patches", "mods", "localization", "linux"}
NOT_LAUNCHERS = re.compile(r"(unins|setup|install|vc_?redist|dxsetup|dxwebsetup|crash|bugreport|notification_helper)",
                           re.I)
# Wine's own copies of Windows' DLLs, wherever distributions and Wine's own packages put them.
WINE_LIBRARIES = "ls /usr/lib*/wine/*-windows /usr/lib/*/wine/*-windows /opt/wine*/lib*/wine/*-windows"
# The same, under a Wine build's folder ($0) or a version of Proton's.
RUNNER_LIBRARIES = 'ls "$0"/lib*/wine/*-windows "$0"/files/lib*/wine/*-windows "$0"/dist/lib*/wine/*-windows'
# Not files: names Windows (and Wine, in its apisetschema.dll) maps to the DLL that implements them.
API_SETS = ("api-ms-", "ext-ms-")
# What wine_libraries found for each runner, once it has found something.
_wine_libraries: dict[str, set[str]] = {}
# Microsoft's libraries that compile a game's shaders. Wine and Proton have their own, but those
# can't compile everything: Anomaly's shaders stop them with "E5017: not yet implemented feature".
DIRECTX_LIBRARY = re.compile(r"(d3dcompiler_\d+|d3dx\d*_\d+)\.dll", re.I)
# The first d3dx with a separate compiler.
COMPILER_SINCE = 33
# Newer d3dx9s hand it the oldest shaders (ps_1_x), and Wine's copy can't compile them. Call of
# Chernobyl then says the video card doesn't meet its requirements.
LEGACY_COMPILER = "d3dx9_31.dll"
# Where they're found, as the installers Steam downloads for its games.
DIRECTX_REDIST = "_CommonRedist/DirectX"
# What's the launcher's, the store's or the player's in a game's folder, rather than the game's.
NEVER_COPIED = {"fsgame.ltx", "fsgame.ltx.original", "_appdata_", "userdocs", detect.MARKER}
# Stands for standalone mods among the games whose downloads are waited for.
GAME = "standalone"
# How much room to expect a mod to need, for the size of its archive: Anomaly unpacks to about twice its.
UNPACK_FACTOR = 2.5
# How long a new prefix can take to set up, in seconds.
PREFIX_TIMEOUT = 300
# Wine builds of other launchers, under the home folder. Proton is found in Steam's folders too.
RUNNER_FOLDERS = (runtimes.PROTON_FOLDER, ".local/share/lutris/runners/wine", ".var/app/net.lutris.Lutris/data/lutris/runners/wine",
                  ".config/heroic/tools/proton", ".config/heroic/tools/wine",
                  ".var/app/com.heroicgameslauncher.hgl/config/heroic/tools/proton",
                  ".var/app/com.heroicgameslauncher.hgl/config/heroic/tools/wine",
                  ".local/share/bottles/runners", ".var/app/com.usebottles.bottles/data/bottles/runners")
# The folders an addon adds files to or replaces them in, with the one fsgame.ltx loads archives from
# (Anomaly's db/). Archives loose in an addon go where it loads extra ones from.
ADDON_FOLDERS = ("gamedata", "bin")
# Those places, in the order they're tried.
ARCHIVE_ALIASES = ("$arch_dir_addons$", "$arch_dir_patches$")
# The versions of Proton that run these games best, in order; newest first within each. Any Proton
# beats any Wine: it brings DXVK and the fixes games need, and only needs Steam to have downloaded it.
BEST_PROTON = (r"cachyos", r"experimental", r"^ge-proton", r"^proton \d")
SYSTEM_WINE = "System Wine"
# The setting for it. Empty means the best runner found.
SYSTEM_RUNNER = "system"
# The setting for going without Microsoft's DirectX libraries.
LIBRARIES_OFF = "off"
# The version of the system's Wine, once it has been asked.
_system_wine_version: list[str] = []


@dataclass(frozen=True)
class Settings:
    # The folder of the Wine build or version of Proton that runs the mod, or SYSTEM_RUNNER.
    # Empty: the best one found, which stays the best as new versions are installed.
    runner: str = ""
    # Added to the DLL overrides the launcher sets, and win over them: d3d11=n,b;dxgi=n,b
    overrides: str = ""
    # Environment variables for the game: DXVK_HUD=fps MANGOHUD=1
    environment: str = ""
    # Passed to the program that starts the game.
    arguments: str = ""
    # LIBRARIES_OFF, or empty to install Microsoft's DirectX libraries when the mod would need them.
    libraries: str = ""


@dataclass(frozen=True)
class Runner:
    """What runs a mod's Windows program."""
    name: str
    # Its folder; None for the system's Wine.
    path: Path | None = None

    @property
    def is_proton(self) -> bool:
        return self.path is not None and (self.path / "proton").is_file()

    @property
    def family(self) -> str:
        """The name without its version, so that a new version of Proton carries on from the last one's prefix."""
        return re.sub(r"[^a-z]+", "-", re.match(r"\D*", self.name.lower())[0]).strip("-") or "proton"

    @property
    def wine_folder(self) -> Path | None:
        """The folder with bin/wine: inside a version of Proton, either name depending on its age."""
        if not self.is_proton:
            return self.path
        return self.path / "files" if (self.path / "files").is_dir() else self.path / "dist"

    def command(self, *args: str) -> list[str]:
        """A command that runs a Windows program with this runner."""
        if self.is_proton:
            return ["python3", str(self.path / "proton"), "waitforexitandrun", *args]
        return [str(self.path / "bin" / "wine") if self.path else "wine", *args]


@dataclass(frozen=True)
class Standalone:
    path: Path
    name: str
    # The program that starts the game, relative to path: the one chosen, or the best one found.
    exe: str | None
    settings: Settings = Settings()

    @property
    def key(self) -> str:
        """Identifies it among running games; game keys never contain a colon."""
        return f"standalone:{self.path}"

    @property
    def data_dir(self) -> Path:
        slug = re.sub(r"[^a-z0-9]+", "-", self.name.lower()).strip("-")
        digest = hashlib.sha256(str(self.path).encode()).hexdigest()[:8]
        # The host's folder, also inside the Flatpak (whose XDG_DATA_HOME is its own): Wine runs on the host.
        return Path.home() / ".local" / "share" / "zone-launcher" / "standalone" / f"{slug}-{digest}"

    @cached_property
    def runner(self) -> Runner:
        return runner_at(self.settings.runner)

    @property
    def compat_dir(self) -> Path:
        """Proton's own folder for the mod, with its prefix inside. Proton calls a prefix from
        another family of Proton invalid, and Wine's differ too, so each has its own."""
        return self.data_dir / self.runner.family

    @property
    def prefix(self) -> Path:
        return self.compat_dir / "pfx" if self.runner.is_proton else self.data_dir / "wineprefix"

    @property
    def libraries_dir(self) -> Path:
        """Windows libraries installed for the mod, in folders 32 and 64: they go into whichever prefix it uses."""
        return self.data_dir / "libraries"

    @property
    def play_dir(self) -> Path:
        """Where the game starts from with addons on: links to the mod's files and the addons'."""
        return self.data_dir / "play"

    @property
    def wine_log(self) -> Path:
        return self.data_dir / "wine.log"

    @property
    def icon(self) -> Path | None:
        """The icon the mod has, or else the one of its programs, kept so they aren't read every time."""
        if found := next((self.path / name for name in ICONS if (self.path / name).is_file()), None):
            return found
        kept = self.data_dir / "icon.png"
        try:
            if not kept.exists():
                kept.parent.mkdir(parents=True, exist_ok=True)
                kept.write_bytes(program_icon(self) or b"")
            return kept if kept.stat().st_size else None
        except OSError:
            return None

    @property
    def app_data(self) -> Path:
        """Saves, settings and logs: wherever the mod's fsgame.ltx puts them."""
        return self.path / (app_data_folder(self.path) or "_appdata_")

    def latest_log(self) -> Path | None:
        logs = sorted((self.app_data / "logs").glob("*.log"), key=lambda p: p.stat().st_mtime)
        return logs[-1] if logs else None


def program_icon(mod: Standalone) -> bytes | None:
    """The icon of a mod's own launcher, in its top folder, or else of the program that starts it."""
    programs = sorted(find_launchers(mod.path), key=lambda rel: "/" in rel)
    for rel in dict.fromkeys(programs[:1] + [mod.exe] * bool(mod.exe) + programs):
        if image := pe.icon(mod.path / rel):
            return image
    return None


@dataclass(frozen=True)
class Check:
    title: str
    ok: bool
    detail: str


def app_data_folder(folder: Path) -> str | None:
    """The folder fsgame.ltx names for $app_data_root$, if it's inside the game's folder."""
    try:
        text = (folder / "fsgame.ltx").read_text(errors="replace")
    except OSError:
        return None
    match = re.search(r"^\s*\$app_data_root\$\s*=(.*)$", text, re.M)
    fields = [f.strip() for f in match.group(1).split("|")] if match else []
    # Either "true| false| $fs_root$| appdata\\" or, relative to the game's folder, "true| false| appdata\\".
    if len(fields) == 3 and not fields[2].startswith("$"):
        fields.insert(2, "$fs_root$")
    if len(fields) < 4 or fields[2] != "$fs_root$":
        return None
    name = fields[3].rstrip("\\/")
    return name if name and ".." not in name else None


def fsgame_folder(folder: Path, alias: str) -> str | None:
    """The folder fsgame.ltx gives an alias, relative to the game's folder, if it's inside it."""
    try:
        text = (folder / "fsgame.ltx").read_text(errors="replace")
    except OSError:
        return None
    entries = {}
    for match in re.finditer(r"^\s*(\$[^$\s]+\$)\s*=(.*)$", text, re.M):
        entries[match.group(1)] = [f.strip() for f in match.group(2).split("|")]

    def resolve(name: str, depth: int) -> str | None:
        if name == "$fs_root$":
            return ""
        fields = entries.get(name)
        if not fields or len(fields) < 3 or depth > 10:
            return None
        root, add = fields[2], fields[3] if len(fields) > 3 else ""
        if not root.startswith("$"):
            if len(fields) > 3 or ":" in root:
                return None  # a folder somewhere else
            root, add = "$fs_root$", root  # "true| false| appdata\\": relative to the game's folder
        base = resolve(root, depth + 1)
        if base is None:
            return None
        return "/".join(part for part in (*base.split("/"), *re.split(r"[\\/]+", add)) if part)

    found = resolve(alias, 0)
    return found if found and ".." not in found.split("/") else None


def addons_key(mod: Standalone) -> str:
    """Its addons' place among the libraries of mods, which are kept by game."""
    return f"{GAME}-{mod.data_dir.name}"


def _clear_links(folder: Path) -> None:
    """Removes the links and empty folders in a folder, keeping files the game wrote there."""
    if folder.is_symlink():
        folder.unlink()
    folder.mkdir(parents=True, exist_ok=True)
    for dirpath, dirnames, filenames in os.walk(folder, topdown=False):
        for name in filenames + dirnames:
            path = Path(dirpath) / name
            if path.is_symlink():
                path.unlink()
            elif path.is_dir() and not any(path.iterdir()):
                path.rmdir()


def _as_spelled(root: Path, rel: Path) -> Path:
    """rel, spelled like what's already there: Windows doesn't tell gamedata/Configs from gamedata/configs."""
    current, spelled = root, []
    for part in rel.parts:
        if not (current / part).exists() and current.is_dir():
            part = next((e.name for e in current.iterdir() if e.name.lower() == part.lower()), part)
        spelled.append(part)
        current = current / part
    return Path(*spelled)


def compose_addons(mod: Standalone) -> tuple[Path, list[str]]:
    """The folder to start the mod from, and warnings to show. With addons on, it's rebuilt each
    time from links to the mod's files with the switched-on addons' on top, in load order, so the
    mod's own folder is never changed. With none on, it's the mod's folder."""
    parts = mods.enabled_parts(addons_key(mod))
    if not parts:
        return mod.path, []
    play = mod.play_dir
    archives = next((found for alias in ARCHIVE_ALIASES if (found := fsgame_folder(mod.path, alias))), None)
    merged = {*ADDON_FOLDERS, *([archives.split("/")[0]] if archives else [])}
    # Saves go where fsgame.ltx says: that has to be the mod's own folder, linked from here.
    if saves := app_data_folder(mod.path):
        (mod.path / saves).mkdir(parents=True, exist_ok=True)
    _link_files({entry.name: entry for entry in mod.path.iterdir() if entry.name not in merged}, play)
    for name in merged:
        _clear_links(play / name)
        if (mod.path / name).is_dir():
            for entry in (mod.path / name).iterdir():
                if not (play / name / entry.name).exists():
                    (play / name / entry.name).symlink_to(entry)

    warnings = []

    def link(rel: Path, source: Path, addon: mods.Mod):
        rel = _as_spelled(play, rel)
        mods.real_dir(play, rel.parent)
        target = play / rel
        if target.is_symlink():
            target.unlink()  # the mod's own file, or a lower-priority addon's
        elif target.exists():
            warnings.append(f"{addon.name}: kept {rel.as_posix()}, which the game wrote")
            return
        target.symlink_to(source)

    for index, (addon, comp, folder) in enumerate(parts):
        for content in sorted(merged):
            for rel in mods.content_files(folder, content):
                link(Path(content, rel), folder / content / rel, addon)
        if comp.archives and not archives:
            warnings.append(f"{addon.name}: its archives were skipped, since the game's fsgame.ltx doesn't "
                            "say where extra archives go")
        for rel in comp.archives if archives else ():
            link(Path(archives, f"{mods.PATCH_PREFIX}{index:03d}_{addon.id}_{Path(rel).name}"), folder / rel, addon)
    return play, warnings


def has_avx() -> bool:
    try:
        return " avx " in Path("/proc/cpuinfo").read_text()
    except OSError:
        return True


def _rank(rel: str, avx: bool) -> tuple:
    """Popular engines first, then anything in bin, then launcher programs, then the rest."""
    path = Path(rel)
    stem = path.stem.lower()
    in_bin = path.parts[0].lower().startswith("bin")
    if match := re.fullmatch(r"anomalydx(\d+)(avx)?", stem):
        # The official launcher's default: the newest renderer, with AVX if the processor has it.
        return (0, bool(match[2]) != avx, -int(match[1]), rel)
    if stem in ("xr_3da", "xrengine"):
        return (0, not in_bin, 0, rel)
    if "launcher" in stem:
        return (2, False, 0, rel)
    return (1 if in_bin else 3, False, 0, rel)


def find_launchers(folder: Path) -> list[str]:
    """The programs that could start the game, relative to the folder, best first."""
    found = []
    # A game's Windows version has links to its folders. Only SEARCH_DEPTH down, so they can't loop.
    for root, folders, names in os.walk(folder, followlinks=True):
        here = Path(root).relative_to(folder)
        if len(here.parts) >= SEARCH_DEPTH:
            folders.clear()
        folders[:] = [f for f in folders if f.lower() not in SKIP_FOLDERS]
        found += [(here / name).as_posix() for name in names
                  if name.lower().endswith(".exe") and not NOT_LAUNCHERS.search(name)]
    avx = has_avx()
    return sorted(found, key=lambda rel: _rank(rel, avx))


def locate(folder: Path) -> Path:
    """The mod's own folder: the one picked, or the only folder inside it if it's just a wrapper."""
    if (folder / "fsgame.ltx").is_file():
        return folder
    try:
        children = [c for c in folder.iterdir() if not c.name.startswith(".")]
    except OSError:
        return folder
    inner = [c for c in children if (c / "fsgame.ltx").is_file()]
    return inner[0] if len(inner) == 1 else folder


def load(path: str | Path, launcher: str | None = None, settings: dict[str, str] | None = None) -> Standalone:
    path = Path(path)
    if not launcher and path.is_dir():
        launcher = next(iter(find_launchers(path)), None)
    known = {k: v for k, v in (settings or {}).items() if k in Settings.__dataclass_fields__}
    return Standalone(path, " ".join(path.name.replace("_", " ").split()), launcher, Settings(**known))


def windows_folder(game: str) -> Path:
    """Where a game's Windows version runs from: links to its files, so its own folder isn't written to."""
    return Path.home() / ".local" / "share" / "zone-launcher" / "windows" / f"{GAMES[game].title} (Windows)"


def windows_game(mod: Standalone) -> str | None:
    """The game a mod is the Windows version of, if it is one."""
    return next((key for key in GAMES if mod.path == windows_folder(key)), None)


def _link_files(wanted: dict[str, Path], target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    for entry in target.iterdir():
        if entry.is_symlink() and wanted.get(entry.name) != Path(os.readlink(entry)):
            entry.unlink()
    for name, entry in wanted.items():
        if not (target / name).is_symlink() and not (target / name).exists():
            (target / name).symlink_to(entry)


def link_windows_version(game: str, source: Path, fsgame: Path, language_packs: Iterable[Path] = ()) -> Path:
    """Sets up or refreshes a game's Windows version, with the file list and saves of its own in the folder.
    Language packs a store keeps elsewhere are linked in with the game's own localization folder."""
    target = windows_folder(game)
    _link_files({entry.name: entry for entry in source.iterdir()
                 if entry.name not in NEVER_COPIED and entry.name != "localization"}, target)
    localization = source / "localization"
    _link_files({entry.name: entry for entry in (*(localization.iterdir() if localization.is_dir() else ()), *language_packs)},
                target / "localization")
    if not (target / "fsgame.ltx").exists():
        shutil.copyfile(fsgame, target / "fsgame.ltx")
    (target / detect.MARKER).write_text(f"{source}\n")
    return target


def _missing_paths(source: Path, target: Path, language_packs: Iterable[Path]) -> dict[str, Path]:
    missing = {}
    for root, folders, names in os.walk(source):
        here = Path(root).relative_to(source)
        if not here.parts:
            folders[:] = [f for f in folders if f not in NEVER_COPIED]
            names = [n for n in names if n not in NEVER_COPIED and not n.startswith("goggame-")]
        missing |= {(here / name).as_posix(): Path(root) / name for name in names if not (target / here / name).exists()}
    missing |= {f"localization/{pack.name}": pack for pack in language_packs
                if not (target / "localization" / pack.name).exists()}
    return missing


def missing_files(source: Path, target: Path, language_packs: Iterable[Path] = ()) -> list[tuple[str, int]]:
    """The files of a game a folder doesn't have: (their paths relative to it, their sizes). A store
    may keep the game's language packs elsewhere, so they're counted separately."""
    return [(rel, path.stat().st_size) for rel, path in _missing_paths(source, target, language_packs).items()]


def copy_missing(source: Path, target: Path, language_packs: Iterable[Path] = ()) -> int:
    """Copies the files of a game a folder doesn't have, and how many. Nothing there is overwritten."""
    missing = _missing_paths(source, target, language_packs)
    for rel, path in missing.items():
        (target / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target / rel)
    return len(missing)


def default_folder() -> Path:
    """Where downloaded mods go unless the user says otherwise."""
    return Path.home() / "Games"


def free_space(folder: Path) -> int:
    """The room on the drive of a folder, which may not exist yet."""
    while not folder.exists() and folder != folder.parent:
        folder = folder.parent
    try:
        return shutil.disk_usage(folder).free
    except OSError:
        return 0


def _mod_root(unpacked: Path) -> Path | None:
    """The folder of a mod that goes on top of a game: the one with its gamedata folder."""
    found = sorted((p for p in unpacked.rglob("gamedata") if p.is_dir()), key=lambda p: len(p.parts))
    return found[0].parent if found else None


def install_over_game(archive: Path, folder: Path, name: str, game: Path, fsgame: Path | None,
                      language_packs: Iterable[Path] = ()) -> Path:
    """Gives a mod that goes on top of a game its own copy of that game, which stays as it is. Nothing
    is left behind if it isn't that kind of mod."""
    target = folder / name
    if target.exists():
        raise mods.ModError(f"{target} already exists. Add that folder with +, or move it out of the way.")
    staging = folder / f".unpacking-{archive.stem}"
    shutil.rmtree(staging, ignore_errors=True)
    folder.mkdir(parents=True, exist_ok=True)
    try:
        mods.extract(archive, staging)
        root = _mod_root(staging)
        if not root:
            raise mods.ModError(f"{archive.name} has no gamedata folder, so it doesn't look like a mod for this game.")
        copy_missing(game, root, language_packs)
        if fsgame:
            shutil.copyfile(fsgame, root / "fsgame.ltx")
        root.rename(target)
        return target
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def unpack(archive: Path, folder: Path) -> Path:
    """Unpacks a mod's archive to its own folder inside folder, and returns that. Nothing is left
    behind if it isn't a mod that can be played."""
    staging = folder / f".unpacking-{archive.stem}"
    shutil.rmtree(staging, ignore_errors=True)
    folder.mkdir(parents=True, exist_ok=True)
    try:
        mods.extract(archive, staging)
        root = staging
        # Archives often wrap everything in one folder named after the mod.
        while not (root / "fsgame.ltx").is_file() and len(entries := list(root.iterdir())) == 1 and entries[0].is_dir():
            root = entries[0]
        root = locate(root)
        if not find_launchers(root):
            raise mods.ModError(f"{archive.name} has no Windows program that starts a game.")
        target = folder / (archive.stem if root == staging else root.name)
        if target.exists():
            raise mods.ModError(f"{target} already exists. Add that folder with +, or move it out of the way.")
        root.rename(target)
        return target
    finally:
        shutil.rmtree(staging, ignore_errors=True)


# ----- what runs it ------------------------------------------------------------------------------


def runner_at(setting: str) -> Runner:
    if not setting:
        return best_runner()
    return Runner(SYSTEM_WINE) if setting == SYSTEM_RUNNER else Runner(Path(setting).name, Path(setting))


def is_runner(folder: Path) -> bool:
    return (folder / "proton").is_file() or (folder / "bin" / "wine").is_file()


def _version_order(runner: Runner) -> list:
    """Sorts names by their numbers, so 10-34 comes after 10-4."""
    return [(0, int(part)) if part.isdigit() else (1, part) for part in re.split(r"(\d+)", runner.name.lower()) if part]


def _preference(runner: Runner) -> int:
    if not runner.is_proton:
        return len(BEST_PROTON) + 1
    name = runner.name.lower()
    return next((i for i, pattern in enumerate(BEST_PROTON) if re.search(pattern, name)), len(BEST_PROTON))


def find_runners() -> list[Runner]:
    """Every version of Proton and Wine build found, best first."""
    home = Path.home()
    places = [root / "compatibilitytools.d" for root in detect.steam_roots(home)]
    places += [home / place for place in RUNNER_FOLDERS]
    folders = [folder for place in places if place.is_dir() for folder in place.iterdir()]
    for root in detect.steam_roots(home):
        for library in detect._steam_libraries(root):
            folders += (library / "steamapps" / "common").glob("Proton*")
    # The same folder is reached more than once (~/.steam/steam is a link to Steam's folder), and other
    # launchers have copies of the same version. Only the name shows, so the first one found stays: Steam's.
    named: dict[str, Path] = {}
    for folder in folders:
        if is_runner(folder):
            named.setdefault(folder.name, folder)
    newest = sorted((Runner(name, folder) for name, folder in named.items()), key=_version_order, reverse=True)
    return sorted(newest, key=_preference)


def best_runner() -> Runner:
    """What a mod runs with unless the user says otherwise. An old Wine build is a last resort."""
    runners = find_runners()
    if runners and runners[0].is_proton:
        return runners[0]
    return Runner(SYSTEM_WINE) if not runners or wine_version() else runners[0]


def wine_version() -> str | None:
    """The version of the system's Wine, or None if it isn't installed."""
    if not _system_wine_version:
        try:
            result = subprocess.run(host_command(["wine", "--version"]), stdin=subprocess.DEVNULL, capture_output=True,
                                    text=True, timeout=10, env=engine_environment())
        except (OSError, subprocess.TimeoutExpired):
            return None
        if result.returncode != 0:
            return None
        _system_wine_version.append(result.stdout.strip())
    return _system_wine_version[0]


def runner_problem(runner: Runner) -> str | None:
    """Why a runner can't start anything, in words."""
    if runner.path is None:
        return None if wine_version() else ("Neither Proton nor the system's Wine was found. Get Proton in Options, "
                                            "or install Wine with 32-bit support.")
    if not is_runner(runner.path):
        return f"{runner.name} isn't there any more. Choose another."
    if runner.is_proton and not host_has("python3"):
        return "Proton needs Python 3 on this computer."
    return None


def host_has(program: str) -> bool:
    if not in_flatpak():
        return shutil.which(program, path=engine_environment().get("PATH")) is not None
    try:
        return subprocess.run(host_command(["sh", "-c", f"command -v {program}"]), stdin=subprocess.DEVNULL,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def wine_libraries(runner: Runner) -> set[str]:
    """The DLLs (in lower case) a runner has its own copies of, so a mod doesn't have to bundle them."""
    key = str(runner.path or "")
    if key not in _wine_libraries:
        command = ["sh", "-c", RUNNER_LIBRARIES, str(runner.path)] if runner.path else ["sh", "-c", WINE_LIBRARIES]
        try:
            output = subprocess.run(host_command(command), stdin=subprocess.DEVNULL, capture_output=True, text=True,
                                    timeout=10, env=engine_environment()).stdout
        except (OSError, subprocess.TimeoutExpired):
            return set()
        found = {line.strip().lower() for line in output.splitlines() if "." in line}
        if not found:
            return set()
        _wine_libraries[key] = found
    return _wine_libraries[key]


# ----- what it loads -----------------------------------------------------------------------------


def _files_found(folder: Path, exe: str) -> dict[str, Path]:
    """The files by lower-case name where Windows looks for a program's DLLs: beside it and in the working folder."""
    found: dict[str, Path] = {}
    for place in (folder, (folder / exe).parent):
        try:
            found |= {p.name.lower(): p for p in place.iterdir() if p.is_file()}
        except OSError:
            pass
    return found


def _loaded_libraries(folder: Path, exe: str) -> tuple[dict[str, str], set[str]]:
    """Follows the DLLs a program loads at startup, and those they load: (their names by lower-case
    name, those the mod bundles)."""
    start = folder / exe
    bundled = _files_found(folder, exe)
    seen: dict[str, str] = {}
    # The program loads its renderers and game DLLs while running, so they aren't among its imports.
    queue = [start, *(p for p in bundled.values() if p.parent == start.parent and p.suffix.lower() == ".dll")]
    while queue:
        program = pe.read(queue.pop())
        for name in program.imports if program else ():
            key = name.lower()
            if key not in seen:
                seen[key] = name
                if key in bundled:
                    queue.append(bundled[key])
    return seen, set(seen) & set(bundled)


def missing_libraries(folder: Path, exe: str, provided: set[str]) -> tuple[int, list[str]]:
    """(How many of the DLLs a program needs the mod bundles, those neither it nor Wine (provided) has)."""
    seen, bundled = _loaded_libraries(folder, exe)
    missing = [name for key, name in seen.items()
               if key not in bundled and key not in provided and not key.startswith(API_SETS)]
    return len(bundled), sorted(missing, key=str.lower)


def installed_libraries(mod: Standalone) -> set[str]:
    """The Windows libraries (in lower case) installed for the mod."""
    return {p.name for p in mod.libraries_dir.glob("*/*.dll")}


def wanted_libraries(mod: Standalone, exe: str | None = None) -> set[str]:
    """Microsoft's DirectX libraries (in lower case) a program of the mod would use Wine's copies of."""
    if not (exe := exe or mod.exe):
        return set()
    seen, _bundled = _loaded_libraries(mod.path, exe)
    found = set(_files_found(mod.path, exe))
    wanted = {key for key in set(seen) - found if DIRECTX_LIBRARY.fullmatch(key)}
    # Microsoft's d3dx compiles shaders with the compiler of its own version, which isn't among its imports.
    for key in list(wanted):
        if (version := re.fullmatch(r"d3dx\d*_(\d+)\.dll", key)) and int(version[1]) >= COMPILER_SINCE:
            wanted.add(f"d3dcompiler_{version[1]}.dll")
        if key.startswith("d3dx9_"):
            wanted.add(LEGACY_COMPILER)
    # What it's given for this may be there without anything importing it.
    return wanted - found


def libraries_to_install(mod: Standalone) -> set[str]:
    """What any of the mod's programs wants, so changing its launcher doesn't need another install."""
    programs = {*find_launchers(mod.path), *([mod.exe] if mod.exe else [])}
    return {name for exe in programs for name in wanted_libraries(mod, exe)} - installed_libraries(mod)


def redist_folders() -> list[Path]:
    """The folders of Microsoft's DirectX installers in the user's Steam libraries."""
    folders = []
    home = Path.home()
    for root in detect.steam_roots(home):
        for library in detect._steam_libraries(root):
            common = library / "steamapps" / "common"
            folders += common.glob(f"Steamworks Shared/{DIRECTX_REDIST}/*")
            folders += common.glob(f"*/{DIRECTX_REDIST}/*")
    folders.append(home / runtimes.DIRECTX_FOLDER)
    return list({folder.resolve(): folder for folder in folders if folder.is_dir()}.values())


def _keep_library(mod: Standalone, dll: Path) -> str | None:
    """Keeps a DLL for the mod, by its bitness and in lower case: Wine's own names are."""
    program = pe.read(dll)
    if not program:
        return None
    folder = mod.libraries_dir / str(program.bits)
    folder.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(dll, folder / dll.name.lower())
    return dll.name.lower()


def add_library_file(mod: Standalone, file: Path) -> list[str]:
    """Keeps a DLL, or every DLL in an installer's .cab file: the names of those kept."""
    if file.suffix.lower() != ".cab":
        return [name for name in [_keep_library(mod, file)] if name]
    mod.data_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=mod.data_dir) as unpacked:
        mods.extract(file, Path(unpacked))
        kept = [_keep_library(mod, dll) for dll in Path(unpacked).rglob("*") if dll.suffix.lower() == ".dll"]
    return [name for name in kept if name]


def redist_cabs(names: set[str]) -> dict[str, list[Path]]:
    """The installers' .cab files of those libraries the user's Steam folders have."""
    cabs = [cab for folder in redist_folders() for cab in folder.glob("*.cab")]

    def named(stem: str) -> list[Path]:
        return [cab for cab in cabs if re.search(rf"_{re.escape(stem)}_x(64|86)\.cab$", cab.name, re.I)]

    # Before August 2009 a compiler came in the installer of the d3dx10 of its version.
    found = {name: named(Path(name).stem) or named(Path(name).stem.replace("d3dcompiler_", "d3dx10_")) for name in names}
    return {name: files for name, files in found.items() if files}


def install_libraries(mod: Standalone) -> tuple[list[str], list[str]]:
    """Installs what the mod wants from the installers in Steam's folders: (those installed, those not found)."""
    wanted = libraries_to_install(mod)
    installed = []
    for name, cabs in sorted(redist_cabs(wanted).items()):
        kept = [kept for cab in cabs for kept in add_library_file(mod, cab)]
        if name in kept:
            installed.append(name)
    return installed, sorted(wanted - set(installed))


def remove_libraries(mod: Standalone) -> None:
    """Goes back to Wine's and Proton's own copies, in every prefix the mod has."""
    prefixes = [mod.data_dir / "wineprefix", *mod.data_dir.glob("*/pfx")]
    for dll in mod.libraries_dir.glob("*/*.dll"):
        for prefix in prefixes:
            for place in ("system32", "syswow64"):
                (prefix / "drive_c" / "windows" / place / dll.name).unlink(missing_ok=True)
        dll.unlink()


def place_libraries(mod: Standalone) -> None:
    """Copies the mod's libraries into its prefix. Proton's prefixes link to Proton's own files, so
    the link is replaced rather than written through."""
    windows = mod.prefix / "drive_c" / "windows"
    system = windows / "system32"
    # In a 64-bit prefix, 32-bit programs use syswow64; a 32-bit prefix has only system32, for them.
    places = {"64": system, "32": windows / "syswow64"} if (windows / "syswow64").is_dir() else {"32": system}
    for bits, place in places.items():
        for dll in (mod.libraries_dir / bits).glob("*.dll"):
            target = place / dll.name
            target.unlink(missing_ok=True)
            shutil.copyfile(dll, target)


def _game_data(folder: Path) -> str | None:
    """Where the game's data is, in words, or None if there's none in any usual place."""
    try:
        archives = [p for p in folder.iterdir() if p.is_file() and re.search(r"\.x?db[\w]*$", p.name, re.I)]
    except OSError:
        return None
    if archives:
        return f"{len(archives)} archive{'s' if len(archives) != 1 else ''} in the mod's folder"
    for name in ("db", "database", "resources", "gamedata"):
        try:
            if any((folder / name).iterdir()):
                return f"The {name} folder"
        except OSError:
            continue
    return None


def check(mod: Standalone) -> list[Check]:
    """Whether a mod has what it needs, from the folder and the program itself rather than a list per mod."""
    checks = []
    exe = mod.path / mod.exe if mod.exe else None
    program = pe.read(exe) if exe and exe.is_file() else None
    if not mod.exe:
        checks.append(Check("Launcher", False, "No Windows program found in the folder."))
    elif not exe.is_file():
        checks.append(Check("Launcher", False, f"{mod.exe} isn't in the folder any more. Choose another."))
    elif not program:
        checks.append(Check("Launcher", False, f"{mod.exe} isn't a Windows program. Choose another."))
    else:
        kind = f"{program.bits}-bit" + (", .NET" if program.dotnet else "")
        checks.append(Check("Launcher", True, f"{mod.exe} ({kind})"))
    fsgame = (mod.path / "fsgame.ltx").is_file()
    checks.append(Check("Game file list (fsgame.ltx)", fsgame, "In the mod's folder" if fsgame
                        else "Not in this folder. X-Ray games need it: is this the folder the game is in?"))
    data = _game_data(mod.path)
    checks.append(Check("Game data", bool(data), data or "No archives or data folders found."))
    runner = mod.runner
    if problem := runner_problem(runner):
        checks.append(Check(runner.name, False, problem))
    elif program and (provided := wine_libraries(runner)):
        count, missing = missing_libraries(mod.path, mod.exe, provided)
        source = "Proton" if runner.is_proton else "Wine"
        checks.append(Check("Libraries", not missing, f"Missing: {', '.join(missing)}" if missing
                            else f"All there: {count} from the mod, the rest from {source}" if count
                            else f"All there: {source} provides them"))
    absent = wanted_libraries(mod) - installed_libraries(mod) if program else set()
    if absent and mod.settings.libraries != LIBRARIES_OFF:
        unavailable = absent - set(redist_cabs(absent))
        checks.append(Check("DirectX libraries", False,
                            f"{', '.join(sorted(unavailable))} from Microsoft aren't installed for this mod, and "
                            "Steam's DirectX installer wasn't found. Wine's and Proton's own can't compile every "
                            "game's shaders. Manage can download them.") if unavailable else
                      Check("DirectX libraries", True, f"Microsoft's {', '.join(sorted(absent))} will be installed "
                            "from Steam's DirectX installer when you press Play."))
    return checks


def problems(mod: Standalone) -> list[Check]:
    return [c for c in check(mod) if not c.ok]


def in_flatpak() -> bool:
    return Path("/.flatpak-info").exists()


def host_command(argv: list[str], directory: Path | None = None, env: dict[str, str] | None = None) -> list[str]:
    """A command for the host. Outside the Flatpak the caller passes directory and env to Popen too."""
    if not in_flatpak():
        return argv
    options = [f"--directory={directory}"] if directory else []
    options += [f"--env={name}={value}" for name, value in (env or {}).items()]
    return ["flatpak-spawn", "--host", *options, *argv]


def user_environment(text: str) -> dict[str, str]:
    """Environment variables as written in the settings. Raises ValueError for anything else."""
    variables = {}
    for word in shlex.split(text):
        name, equals, value = word.partition("=")
        if not equals or not re.fullmatch(r"[A-Za-z_]\w*", name):
            raise ValueError(f"{word} isn't NAME=value")
        variables[name] = value
    return variables


def wine_environment(mod: Standalone) -> dict[str, str]:
    program = pe.read(mod.path / mod.exe) if mod.exe else None
    overrides = [DLL_OVERRIDES] if program and program.dotnet else [DLL_OVERRIDES, NO_DOTNET]
    overrides += [f"{Path(name).stem}=n,b" for name in sorted(installed_libraries(mod))]
    # The last one for a DLL is the one Wine uses.
    if mod.settings.overrides.strip():
        overrides.append(mod.settings.overrides.strip().strip(";"))
    env = {"WINEPREFIX": str(mod.prefix), "WINEDLLOVERRIDES": ";".join(overrides)}
    if mod.runner.is_proton:
        roots = [root for root in detect.steam_roots(Path.home()) if root.is_dir()]
        env |= {"STEAM_COMPAT_DATA_PATH": str(mod.compat_dir),
                "STEAM_COMPAT_CLIENT_INSTALL_PATH": str(roots[0] if roots else mod.compat_dir)}
    try:
        return env | user_environment(mod.settings.environment)
    except ValueError:
        return env


def _spawn(mod: Standalone, argv: list[str], output=subprocess.DEVNULL, cwd: Path | None = None) -> subprocess.Popen:
    # The engine finds fsgame.ltx in the working directory.
    env, cwd = wine_environment(mod), cwd or mod.path
    return subprocess.Popen(host_command(argv, cwd, env), cwd=cwd, stdin=subprocess.DEVNULL, stdout=output,
                            stderr=subprocess.STDOUT, env={**engine_environment(), **env})


def _make_prefix_folder(mod: Standalone) -> None:
    (mod.compat_dir if mod.runner.is_proton else mod.prefix).mkdir(parents=True, exist_ok=True)


def prefix_ready(mod: Standalone) -> bool:
    return (mod.prefix / "system.reg").is_file() and (mod.prefix / "drive_c" / "windows" / "system32").is_dir()


def prepare(mod: Standalone) -> None:
    """Installs the libraries the mod needs and puts them in its prefix, first making the prefix if
    it's new: the game can't wait for that, since it starts as soon as the prefix exists. Takes a
    while, so not for the main thread."""
    if mod.settings.libraries != LIBRARIES_OFF and libraries_to_install(mod):
        try:
            install_libraries(mod)
        except (OSError, mods.ModError):
            pass  # The game may run without them, and if it doesn't its log says why.
    if not installed_libraries(mod):
        return
    if not prefix_ready(mod):
        _make_prefix_folder(mod)
        process = _spawn(mod, mod.runner.command("wineboot", "-i"))
        try:
            process.wait(timeout=PREFIX_TIMEOUT)
        except subprocess.TimeoutExpired:
            process.kill()
    place_libraries(mod)


def launch(mod: Standalone, folder: Path | None = None) -> subprocess.Popen:
    """Starts the game from its folder, or another one with its files (see compose_addons)."""
    folder = folder or mod.path
    _make_prefix_folder(mod)
    # Proton doesn't find a program by a relative path, and exits without saying so.
    program = str(folder / mod.exe) if mod.runner.is_proton else mod.exe
    try:
        arguments = shlex.split(mod.settings.arguments)
    except ValueError:
        arguments = []
    with open(mod.wine_log, "wb") as log:
        return _spawn(mod, mod.runner.command(program, *arguments), log, cwd=folder)


def run_program(mod: Standalone, program: Path, arguments: list[str]) -> subprocess.Popen:
    """Runs another program in the mod's prefix, from its own folder."""
    _make_prefix_folder(mod)
    return _spawn(mod, mod.runner.command(str(program), *arguments), cwd=program.parent)


def configure(mod: Standalone) -> subprocess.Popen:
    """Opens Wine's own settings for the mod's prefix."""
    _make_prefix_folder(mod)
    return _spawn(mod, mod.runner.command("winecfg"))


def stop(mod: Standalone) -> None:
    """End everything running in the mod's prefix: stopping Wine's starter process doesn't end the game."""
    runner = mod.runner
    _spawn(mod, [str(runner.wine_folder / "bin" / "wineserver"), "-k"] if runner.is_proton
           else runner.command("wineboot", "-k"))


def shader_failure(log_text: str) -> str | None:
    """The error of Wine's shader compiler that stopped a game, if there's one in its log."""
    match = re.search(r"^.*:\d+:\d+: (E5\d{3}: .*)$", log_text, re.M)
    return match[1] if match else None
