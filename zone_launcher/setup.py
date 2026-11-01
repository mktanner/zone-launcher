"""Set a game up in OpenXRay's standard folder by linking to an install elsewhere (Heroic, Steam...).

~/.local/share/GSC Game World/<game>/ gets a symlink to every file of the source install, so
nothing is copied and the source folder is never written to. The few things that must differ
from the Windows install are real files here: fsgame.ltx, _appdata_ (saves, settings, logs), the
OpenGL shader link and the language pack.
"""

import os
from pathlib import Path

from .detect import MARKER, Install
from .fixes import LANGUAGE_PACK, Issue
from .games import GAMES, identify

# Never linked: the launcher or the engine owns these here.
SKIP = {"fsgame.ltx", "fsgame.ltx.original", "_appdata_", "userdocs", MARKER, "gamedata/shaders/gl"}
# Real folders here, filled with per-file links, so the launcher can add its own files inside.
REAL_DIRS = {"gamedata", "gamedata/shaders", "localization", "patches"}


def target_dir(game: str, home: Path | None = None) -> Path:
    return (home or Path.home()) / ".local" / "share" / "GSC Game World" / GAMES[game].pref_dir


def linked_source(target: Path) -> Path | None:
    try:
        return Path((target / MARKER).read_text().strip())
    except OSError:
        return None


def is_own_copy(target: Path) -> bool:
    """A game copied here by hand (the usual OpenXRay setup), which the launcher must leave alone."""
    return target.is_dir() and linked_source(target) is None and identify(target) is not None


def _language_pack(rel: str, name: str) -> bool:
    # Which language is active is up to fixes._check_language, so leave localization/x<lang>.db alone.
    return rel == "localization" and bool(LANGUAGE_PACK.match(name))


def _is_under(path: str, root: Path) -> bool:
    return os.path.commonpath([os.path.abspath(path), str(root)]) == str(root)


def link(source: Path, target: Path) -> None:
    """Create or refresh the links in target so it mirrors source. Safe to run on every launch."""
    source = source.resolve()
    target.mkdir(parents=True, exist_ok=True)

    def mirror(rel: str) -> None:
        src_dir, dst_dir = source / rel, target / rel
        dst_dir.mkdir(parents=True, exist_ok=True)
        wanted = set()
        for entry in sorted(src_dir.iterdir()) if src_dir.is_dir() else []:
            sub = f"{rel}/{entry.name}" if rel else entry.name
            if sub in SKIP or _language_pack(rel, entry.name):
                continue
            wanted.add(entry.name)
            dst = dst_dir / entry.name
            if sub in REAL_DIRS and entry.is_dir():
                if dst.is_symlink():
                    dst.unlink()
                mirror(sub)
            elif dst.is_symlink():
                if os.readlink(dst) != str(entry):
                    dst.unlink()
                    dst.symlink_to(entry)
            elif not dst.exists():
                dst.symlink_to(entry)
            # A real file already here is the launcher's or the player's; leave it.
        # Drop links into a previous source, or to files the source no longer has.
        for dst in dst_dir.iterdir():
            sub = f"{rel}/{dst.name}" if rel else dst.name
            if dst.is_symlink() and sub not in SKIP and dst.name not in wanted and not _language_pack(rel, dst.name):
                target_path = os.readlink(dst)
                if not _is_under(target_path, target):
                    dst.unlink()

    mirror("")
    for rel in REAL_DIRS:
        if (source / rel).is_dir():
            mirror(rel)
    (target / MARKER).write_text(f"{source}\n")


def plan(source: Install, home: Path | None = None) -> tuple[Install, Issue | None]:
    """Return the install to actually run, and the setup step still needed (None if ready)."""
    target = target_dir(source.game, home)
    if target.exists() and source.path == target.resolve():
        return source, None  # the hand-made copy in the OpenXRay folder itself
    run = Install(source.game, target, source.sources, source.gog_support)
    if linked_source(target) == source.path:
        return run, None
    if is_own_copy(target):
        # Shouldn't be offered: the window only lists the hand-made copy when one exists.
        return source, None

    def fix() -> str:
        link(source.path, target)
        return f"Set up in {target} (linked, nothing copied)"

    return run, Issue(
        "setup", "Not set up for OpenXRay yet",
        f"Play will create {target} with links to the game files in {source.path}. "
        "Nothing is copied, and the original folder isn't changed.", fix,
    )
