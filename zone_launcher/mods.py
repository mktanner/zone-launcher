"""Mod library: install mod archives, choose which parts are on and in what order, and assemble the
enabled ones into a launcher-managed game folder with links.

A mod is unpacked once into <data>/zone-launcher/mods/<game>/<mod id>/. Its parts ("components") are
the folders inside it that hold gamedata/, patches/ or .db archives: the top-most one is the main
part, deeper ones (like an "!addons/<option>/" folder) are optional. On Play, compose() links every
enabled part into the game folder: loose files into gamedata/, archives into patches/.
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import zipfile
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path

from .detect import MARKER, Install

ARCHIVE_SUFFIXES = (".zip", ".7z", ".rar")
# Files of the X-Ray engine itself. A mod that ships them was made for its own modified engine.
ENGINE_FILES = {"xrgame.dll", "xrengine.exe", "xr_3da.exe", "xrcore.dll"}
ENGINE_WARNING = ("Ships its own engine ({files}). Mods like this are made for their modified Windows "
                  "engine, which OpenXRay replaces, so it will most likely crash the game or not work. "
                  "If the mod's instructions say to copy a bin folder, it won't run here.")
# Archives in patches/ load in name order, after the game's own xpatch_*.db.
PATCH_PREFIX = "zz_mod_"


class ModError(Exception):
    pass


@dataclass
class Component:
    path: str  # relative to the mod folder; "" for the mod folder itself
    name: str
    gamedata: bool
    archives: list[str]  # .db/.xdb files, relative to the component folder


@dataclass
class Mod:
    id: str
    name: str
    archive: str
    installed: str
    components: list[Component]
    warnings: list[str] = field(default_factory=list)
    # Where it was downloaded from, for mods installed from ModDB: {"site", "mod", "page", "file"}.
    source: dict[str, str] = field(default_factory=dict)
    # Bytes the unpacked mod takes in the library (0 for mods installed before this was recorded).
    size: int = 0
    # The archive it was installed from, so removing the mod can offer to delete that too.
    archive_path: str = ""
    # Engine files it ships (see ENGINE_FILES); None for mods installed before this was checked.
    engine: list[str] | None = None
    # Filled from the game's state file, not stored in mod.json.
    enabled: list[str] = field(default_factory=list)

    @property
    def is_enabled(self) -> bool:
        return bool(self.enabled)


def data_dir() -> Path:
    # Inside the Flatpak, XDG_DATA_HOME is ~/.var/app/<app id>/data.
    base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / "zone-launcher" / "mods"


def library_dir(game: str) -> Path:
    return data_dir() / game


# ----- reading and writing the library ----------------------------------------------------------

def _state_path(game: str) -> Path:
    return library_dir(game) / "state.json"


def _load_state(game: str) -> dict:
    try:
        state = json.loads(_state_path(game).read_text())
    except (OSError, ValueError):
        state = {}
    state.setdefault("order", [])  # lowest priority first; later mods win conflicts
    state.setdefault("enabled", {})  # mod id -> enabled component paths
    state.setdefault("engine_ok", [])  # mods with their own engine the user plays with anyway
    return state


def _save_state(game: str, state: dict) -> None:
    path = _state_path(game)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.replace(path)


def _load_mod(folder: Path) -> Mod | None:
    try:
        data = json.loads((folder / "mod.json").read_text())
    except (OSError, ValueError):
        return None
    data["components"] = [Component(**c) for c in data["components"]]
    return Mod(**data)


def _save_mod(folder: Path, mod: Mod) -> None:
    (folder / "mod.json").write_text(json.dumps({k: v for k, v in asdict(mod).items() if k != "enabled"}, indent=2))


def list_mods(game: str) -> list[Mod]:
    """Installed mods in load order (lowest priority first)."""
    state = _load_state(game)
    lib = library_dir(game)
    mods = []
    for mod_id in state["order"]:
        mod = _load_mod(lib / mod_id)
        if mod:
            if mod.engine is None:
                _check_engine(lib / mod_id, mod)
            mod.enabled = list(state["enabled"].get(mod_id, []))
            mods.append(mod)
    return mods


def _check_engine(folder: Path, mod: Mod) -> None:
    """Look for engine files in a mod installed before they were looked for, once."""
    mod.engine = find_engine_files(folder)
    if mod.engine:
        # Its old warning about skipped bin/ files undersold it.
        mod.warnings = [w for w in mod.warnings if not w.startswith("Includes Windows engine files")]
        mod.warnings.insert(0, ENGINE_WARNING.format(files=", ".join(mod.engine)))
    try:
        _save_mod(folder, mod)
    except OSError:
        pass


def engine_mods(game: str, unconfirmed: bool = False) -> list[Mod]:
    """Switched-on mods that ship their own engine; with unconfirmed, only those the user hasn't
    chosen to play with anyway."""
    confirmed = set(_load_state(game)["engine_ok"]) if unconfirmed else set()
    return [m for m in list_mods(game) if m.is_enabled and m.engine and m.id not in confirmed]


def confirm_engine_mods(game: str, mod_ids: list[str]) -> None:
    """The user plays with these anyway; don't ask about them again."""
    state = _load_state(game)
    state["engine_ok"] = sorted(set(state["engine_ok"]) | set(mod_ids))
    _save_state(game, state)


# ----- installing ---------------------------------------------------------------------------------

def find_7zip() -> str | None:
    for name in ("7zz", "7z", "7za"):
        if path := shutil.which(name):
            return path
    return None


def _unpack_commands(archive: Path, dest: Path) -> list[list[str]]:
    seven, bsdtar = find_7zip(), shutil.which("bsdtar")
    # libarchive's bsdtar first: it's in the Flatpak runtime (and bundled in the AppImage), reads RAR
    # (Debian's 7-Zip has RAR removed), and unpacks some 7z archives that 7-Zip reports as damaged
    # (ABR's BCJ2 streams). 7-Zip is the fallback.
    commands = [
        [bsdtar, "-xf", str(archive), "-C", str(dest)] if bsdtar else None,
        [seven, "x", "-y", f"-o{dest}", str(archive)] if seven else None,
    ]
    return [c for c in commands if c]


def extract(archive: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    errors = []
    for command in _unpack_commands(archive, dest):
        result = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        if result.returncode == 0:
            return
        errors.append(result.stderr.strip()[-300:])
        shutil.rmtree(dest, ignore_errors=True)  # don't mix a partial unpack with the next attempt
        dest.mkdir(parents=True)
    if archive.suffix.lower() == ".zip":
        with zipfile.ZipFile(archive) as z:
            z.extractall(dest)
        return
    if errors:
        raise ModError(f"Couldn't unpack {archive.name}: {errors[-1]}")
    raise ModError("7-Zip or bsdtar is needed to unpack .7z and .rar archives, and neither was found.")


def _is_component(folder: Path) -> bool:
    if (folder / "gamedata").is_dir() or (folder / "patches").is_dir():
        return True
    return any(p.suffix.lower() in (".db", ".xdb") for p in folder.iterdir() if p.is_file())


def _archives_in(folder: Path) -> list[str]:
    found = []
    for base in (folder / "patches", folder):
        if base.is_dir():
            found += [str(p.relative_to(folder)) for p in sorted(base.iterdir())
                      if p.is_file() and p.suffix.lower() in (".db", ".xdb")]
    return found


def find_components(root: Path) -> list[Component]:
    """Every folder in the mod that holds game content, shallowest first."""
    found = []
    for folder in sorted([root, *(p for p in root.rglob("*") if p.is_dir())],
                         key=lambda p: (len(p.relative_to(root).parts), str(p))):
        rel = folder.relative_to(root)
        # Content folders themselves aren't components.
        if any(part.lower() in ("gamedata", "patches", "bin") for part in rel.parts):
            continue
        if _is_component(folder):
            found.append(Component(
                path="" if rel == Path(".") else str(rel),
                name=" / ".join(rel.parts) if rel.parts else "Main files",
                gamedata=(folder / "gamedata").is_dir(),
                archives=_archives_in(folder),
            ))
    return found


def find_engine_files(root: Path) -> list[str]:
    """The engine files a mod ships (relative paths), if any."""
    found = sorted(str(p.relative_to(root)) for p in root.rglob("*")
                   if p.name.lower() in ENGINE_FILES and p.is_file())
    return found[:4]


def _warnings(root: Path, engine: list[str]) -> list[str]:
    warnings = []
    has_fsgame = any(p.name.lower() == "fsgame.ltx" for p in root.rglob("*"))
    if engine:
        warnings.append(ENGINE_WARNING.format(files=", ".join(engine)))
        if has_fsgame:
            warnings.append("It also has its own fsgame.ltx, so it may be a standalone game rather than a "
                            "mod for the original one.")
    elif any(p.is_dir() and p.name.lower() == "bin" for p in root.rglob("*")):
        warnings.append("Includes Windows engine files (bin/), which were skipped: OpenXRay replaces "
                        "the game's engine. Features that depend on a modified engine won't work.")
    if any(p.name.lower() == "fsgame.ltx" for p in root.rglob("*")):
        warnings.append("Includes its own fsgame.ltx, which was ignored; the launcher manages that file.")
    return warnings


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "mod"


def install(game: str, source: Path, name: str | None = None, origin: dict[str, str] | None = None) -> Mod:
    """Install a mod from an archive or an already unpacked folder. origin says where it was
    downloaded from (see Mod.source)."""
    lib = library_dir(game)
    lib.mkdir(parents=True, exist_ok=True)
    name = name or re.sub(r"\.(zip|7z|rar)$", "", source.name, flags=re.IGNORECASE)
    mod_id = f"{_slug(name)}-{hashlib.sha1(str(source.resolve()).encode()).hexdigest()[:6]}"
    staging = lib / f".installing-{mod_id}"
    shutil.rmtree(staging, ignore_errors=True)
    try:
        if source.is_dir():
            shutil.copytree(source, staging, symlinks=True)
        else:
            extract(source, staging)
        # Archives often wrap everything in one folder named after the mod.
        root = staging
        entries = list(root.iterdir())
        while len(entries) == 1 and entries[0].is_dir() and not _is_component(root):
            root = entries[0]
            entries = list(root.iterdir())
        components = find_components(root)
        if not components:
            raise ModError(f"{source.name} has no gamedata/ or patches/ folder, so it doesn't look "
                           "like a mod for this game.")
        size = sum(p.stat().st_size for p in root.rglob("*") if p.is_file() and not p.is_symlink())
        engine = find_engine_files(root)
        mod = Mod(mod_id, name, source.name, date.today().isoformat(), components, _warnings(root, engine),
                  dict(origin or {}), size, "" if source.is_dir() else str(source.resolve()), engine)
        _save_mod(root, mod)
        final = lib / mod_id
        shutil.rmtree(final, ignore_errors=True)
        root.rename(final)
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    state = _load_state(game)
    if mod_id not in state["order"]:
        state["order"].append(mod_id)
    # The shallowest part is the mod itself; deeper ones are options the user turns on. A mod made for
    # its own engine starts switched off, since it would most likely crash the game.
    state["enabled"][mod_id] = [] if engine else [components[0].path]
    _save_state(game, state)
    mod.enabled = state["enabled"][mod_id]
    return mod


def remove(game: str, mod_id: str) -> None:
    state = _load_state(game)
    state["order"] = [m for m in state["order"] if m != mod_id]
    state["enabled"].pop(mod_id, None)
    _save_state(game, state)
    shutil.rmtree(library_dir(game) / mod_id, ignore_errors=True)


def disk_size(game: str, mod: Mod) -> int:
    """Bytes the mod takes in the library (worked out for mods installed before sizes were recorded)."""
    if mod.size:
        return mod.size
    folder = library_dir(game) / mod.id
    return sum(p.stat().st_size for p in folder.rglob("*") if p.is_file() and not p.is_symlink())


def downloaded_archive(mod: Mod, downloads: Path) -> Path | None:
    """The archive the mod was installed from, if it's still there: where it was installed from, or
    (for mods installed before that was recorded) a file of that name in the Downloads folder."""
    for candidate in (mod.archive_path, downloads / mod.archive if mod.archive else ""):
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    return None


def set_enabled(game: str, mod_id: str, component_paths: list[str]) -> None:
    state = _load_state(game)
    state["enabled"][mod_id] = list(component_paths)
    _save_state(game, state)


def move(game: str, mod_id: str, delta: int) -> None:
    """Move a mod up (+1, higher priority) or down (-1) the load order."""
    state = _load_state(game)
    order = state["order"]
    i = order.index(mod_id)
    j = max(0, min(len(order) - 1, i + delta))
    order.insert(j, order.pop(i))
    _save_state(game, state)


# ----- assembling into a game folder --------------------------------------------------------------

def _enabled_parts(game: str) -> list[tuple[Mod, Component, Path]]:
    lib = library_dir(game)
    parts = []
    for mod in list_mods(game):
        for comp in mod.components:
            if comp.path in mod.enabled:
                parts.append((mod, comp, lib / mod.id / comp.path))
    return parts


def gamedata_files(folder: Path) -> list[str]:
    gamedata = folder / "gamedata"
    return sorted(str(p.relative_to(gamedata)) for p in gamedata.rglob("*") if p.is_file()) if gamedata.is_dir() else []


def conflicts(game: str) -> dict[str, dict[str, int]]:
    """For each enabled mod, how many loose files it takes over from each lower-priority mod."""
    owner: dict[str, str] = {}
    result: dict[str, dict[str, int]] = {}
    for mod, _comp, folder in _enabled_parts(game):
        for rel in gamedata_files(folder):
            previous = owner.get(rel)
            if previous and previous != mod.id:
                result.setdefault(mod.id, {}).setdefault(previous, 0)
                result[mod.id][previous] += 1
            owner[rel] = mod.id
    return result


def _real_dir(root: Path, rel: Path) -> Path:
    """Make root/rel a real folder, splitting any linked folder on the way into per-entry links."""
    current = root
    for part in rel.parts:
        current = current / part
        if current.is_symlink():
            target = Path(os.readlink(current))
            current.unlink()
            current.mkdir()
            if target.is_dir():
                for child in target.iterdir():
                    (current / child.name).symlink_to(child)
        elif not current.exists():
            current.mkdir()
    return current


def _reset(folder: Path, keep: set[str]) -> None:
    """Remove every link and leftover empty folder under folder, except the paths in keep."""
    if folder.is_symlink():
        folder.unlink()
    folder.mkdir(exist_ok=True)
    for dirpath, dirnames, filenames in os.walk(folder, topdown=False):
        for name in filenames + dirnames:
            path = Path(dirpath) / name
            if str(path.relative_to(folder)) in keep:
                continue
            if path.is_symlink():
                path.unlink()
            elif path.is_dir():
                if not any(path.iterdir()):
                    path.rmdir()
            else:
                raise ModError(f"{path} is a real file, not one the launcher linked. Move it out of the "
                               "game folder (or install it as a mod) so mods can be managed safely.")


def compose(install: Install) -> list[str]:
    """Rebuild the game folder's gamedata/ and patches/ from the original install plus the enabled
    mods, in load order. Returns warnings to show. Safe to run on every launch."""
    from .setup import link, linked_source

    target = install.path
    parts = _enabled_parts(install.game)
    source = linked_source(target)
    if source is None:
        if parts:
            raise ModError("Mods only work with a game the launcher set up itself; this copy was made "
                           "by hand, and its files would be overwritten.")
        return []

    gamedata, patches = target / "gamedata", target / "patches"
    # Start from the original game every time, so turning a mod off restores the files it replaced.
    _reset(gamedata, keep={"shaders", "shaders/gl"})
    _reset(patches, keep=set())
    link(source, target)

    warnings = []
    for index, (mod, comp, folder) in enumerate(parts):
        for rel in gamedata_files(folder):
            dst = gamedata / rel
            _real_dir(gamedata, Path(rel).parent)
            if dst.is_symlink():
                dst.unlink()  # the original game's file or a lower-priority mod's
            elif dst.exists():
                warnings.append(f"{mod.name}: kept the existing file gamedata/{rel}")
                continue
            dst.symlink_to(folder / "gamedata" / rel)
        for rel in comp.archives:
            (patches / f"{PATCH_PREFIX}{index:03d}_{mod.id}_{Path(rel).name}").symlink_to(folder / rel)
    return warnings
