"""Checks that a game folder is ready for OpenXRay, and the fixes for each problem."""

import os
import re
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from .detect import Install
from .engine import Engine
from .games import GAMES

# localization/ also holds non-language archives such as xefis_movies.db and base_sounds.db.
LANGUAGE_PACK = re.compile(
    r"^x(english|russian|german|french|italian|spanish|polish|ukrainian)(_\w+)?\.db$", re.IGNORECASE
)
SOC_FSGAME = Path(__file__).with_name("fsgame_soc.ltx")


@dataclass
class Issue:
    id: str
    title: str
    detail: str
    # Applies the fix and returns a short message saying what changed; None if the user must act.
    fix: Callable[[], str] | None = None
    # Non-blocking issues are worth showing but don't stop the game from starting.
    blocking: bool = True


def installed_language_packs(install: Install) -> list[Path]:
    loc = install.path / "localization"
    return sorted(p for p in loc.glob("*.db") if LANGUAGE_PACK.match(p.name)) if loc.is_dir() else []


def available_language_packs(install: Install) -> dict[str, list[Path]]:
    """Language packs that could be installed, keyed by language name ("english", "polish", ...)."""
    files: list[Path] = []
    if install.gog_support:
        files += (install.gog_support / "add").glob("*.db")
    files += (install.path / "languages").glob("*/*.db")
    packs: dict[str, list[Path]] = {}
    for f in sorted(files):
        m = LANGUAGE_PACK.match(f.name)
        if m:
            packs.setdefault(m.group(1).lower(), []).append(f)
    return packs


def _copy_into(src: Path, dest_dir: Path) -> None:
    dest_dir.mkdir(parents=True, exist_ok=True)
    tmp = dest_dir / f".{src.name}.partial"
    shutil.copyfile(src, tmp)
    # Rename last so a cancelled or failed copy never leaves a truncated archive the engine would load.
    tmp.replace(dest_dir / src.name)


def _check_writable(install: Install) -> list[Issue]:
    if os.access(install.path, os.W_OK):
        return []
    return [Issue("not-writable", "Game folder is read-only",
                  f"OpenXRay saves games and settings inside {install.path}, so it needs write access.")]


def _normalized(text: str) -> list[str]:
    return [line.rstrip() for line in text.splitlines() if line.strip() and not line.startswith(";")]


def _check_fsgame(install: Install, engine: Engine) -> list[Issue]:
    target = install.path / "fsgame.ltx"
    # OpenXRay ships one fsgame.ltx, laid out for Clear Sky and Call of Pripyat.
    expected = SOC_FSGAME if install.game == "soc" else engine.fsgame
    try:
        if _normalized(target.read_text(errors="replace")) == _normalized(expected.read_text()):
            return []
        problem = "doesn't match the file layout OpenXRay needs for this game"
    except FileNotFoundError:
        problem = "is missing or points to an engine that isn't here"
    except OSError as e:
        problem = f"can't be read ({e.strerror})"

    def fix() -> str:
        msg = "Installed the fsgame.ltx this game needs"
        if target.is_symlink():
            target.unlink()
        elif target.exists():
            backup = target.with_name("fsgame.ltx.original")
            if not backup.exists():
                target.rename(backup)
                msg += f" (kept the old one as {backup.name})"
            else:
                target.unlink()
        # A copy rather than a symlink, so it keeps working for both the Flatpak and a system engine.
        shutil.copyfile(expected, target)
        return msg

    return [Issue("fsgame", "File layout config needs updating", f"fsgame.ltx {problem}.", fix)]


def _check_shaders(install: Install, engine: Engine) -> list[Issue]:
    link = install.path / "gamedata" / "shaders" / "gl"
    if link.is_symlink():
        if Path(os.path.realpath(link)) == engine.gl_shaders.resolve():
            return []
        detail = f"The OpenGL shader link points to {os.readlink(link)}, not this engine's shaders."
    elif link.exists():
        return [Issue("shaders-custom", "Custom OpenGL shaders in use",
                      f"{link} is a real folder, probably from a mod. If graphics break, it may not "
                      "match this engine version.", blocking=False)]
    else:
        detail = "The game folder has no link to the engine's OpenGL shaders, so it can't render."

    def fix() -> str:
        if link.is_symlink():
            link.unlink()
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(engine.gl_shaders)
        return "Linked the engine's OpenGL shaders"

    return [Issue("shaders", "OpenGL shaders not linked", detail, fix)]


def _check_language(install: Install, preferred: str) -> list[Issue]:
    if not GAMES[install.game].uses_localization or installed_language_packs(install):
        return []
    packs = available_language_packs(install)
    if not packs:
        return [Issue("language-missing", "No language pack found",
                      "localization/ has no x<language>.db file, so voices and text are missing and "
                      "a new game will crash. Reinstall the game, or copy the pack into localization/.")]
    language = preferred if preferred in packs else "english" if "english" in packs else next(iter(packs))
    files = packs[language]
    size_mb = sum(f.stat().st_size for f in files) // 2**20

    def fix() -> str:
        for f in files:
            _copy_into(f, install.path / "localization")
        return f"Installed the {language.title()} language pack"

    return [Issue("language", f"{language.title()} language pack not installed",
                  f"Voices and text live in {', '.join(f.name for f in files)} ({size_mb} MB), which "
                  "isn't in localization/. Without it, starting a new game crashes.", fix)]


def _check_default_settings(install: Install) -> list[Issue]:
    defaults = install.gog_support / "userdocs" / "user.ltx" if install.gog_support else None
    if not defaults or not defaults.is_file():
        return []
    settings = install.path / "_appdata_" / "user.ltx"
    try:
        # Without key bindings the game can't be played; a fresh install has no settings at all.
        if "\nbind " in settings.read_text(errors="replace"):
            return []
    except FileNotFoundError:
        pass

    def fix() -> str:
        settings.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(defaults, settings)
        return "Installed GOG's default settings and key bindings"

    return [Issue("settings", "Default settings not installed",
                  "There are no key bindings or graphics settings yet. GOG's installer normally sets "
                  "these up; the launcher will copy GOG's defaults.", fix)]


def check(install: Install, engine: Engine, preferred_language: str = "english") -> list[Issue]:
    issues = _check_writable(install)
    if issues:
        return issues
    return [
        *_check_fsgame(install, engine),
        *_check_shaders(install, engine),
        *_check_language(install, preferred_language),
        *_check_default_settings(install),
    ]
