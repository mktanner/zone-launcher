"""Checks that a game folder is ready for OpenXRay, and the fixes for each problem."""

import os
import re
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from . import display
from .detect import MARKER, Install
from .engine import Engine
from .games import GAMES

# localization/ also holds non-language archives such as xefis_movies.db and base_sounds.db.
LANGUAGE_PACK = re.compile(
    r"^x(english|russian|german|french|italian|spanish|polish|ukrainian)(_\w+)?\.db$", re.IGNORECASE
)
SOC_FSGAME = Path(__file__).with_name("fsgame_soc.ltx")
# Shadow of Chernobyl has no language packs: its archives hold the text of these languages (English
# voices for all), chosen with the g_language console variable.
SOC_TEXT = {"english": "eng", "czech": "cze", "spanish": "esp", "french": "fra", "german": "ger",
            "italian": "ita", "polish": "pol"}
# Packs a switched-away-from language is moved to; the engine doesn't look in subfolders.
INACTIVE = ".inactive"


@dataclass
class Issue:
    id: str
    title: str
    detail: str
    # Applies the fix and returns a short message saying what changed; None if the user must act.
    fix: Callable[[], str] | None = None
    # Non-blocking issues are worth showing but don't stop the game from starting.
    blocking: bool = True


def _language_of(path: Path) -> str | None:
    m = LANGUAGE_PACK.match(path.name)
    return m.group(1).lower() if m else None


def installed_language_packs(install: Install) -> list[Path]:
    loc = install.path / "localization"
    return sorted(p for p in loc.iterdir() if _language_of(p)) if loc.is_dir() else []


def _linked_source(install: Install) -> Path | None:
    try:
        return Path((install.path / MARKER).read_text().strip())
    except OSError:
        return None


def available_language_packs(install: Install) -> dict[str, list[Path]]:
    """Every language pack this game could use, keyed by language ("english", "polish", ...)."""
    files: list[Path] = []
    if install.gog_support:
        files += (install.gog_support / "add").glob("*.db")
    roots = [install.path]
    if source := _linked_source(install):
        roots.append(source)  # its localization/ isn't linked as a whole; the packs are ours to manage
    for root in roots:
        files += (root / "languages").glob("*/*.db")
        files += (root / "localization").glob("*.db")
    files += (install.path / "localization" / INACTIVE).glob("*.db")
    packs: dict[str, dict[str, Path]] = {}
    for path in sorted(files):
        if language := _language_of(path):
            # One file per name; the first found (gog-support, then the game's own) wins.
            packs.setdefault(language, {}).setdefault(path.name, path)
    return {language: list(byname.values()) for language, byname in packs.items()}


def available_languages(install: Install) -> list[str]:
    if not GAMES[install.game].uses_localization:
        return list(SOC_TEXT)
    return sorted(set(available_language_packs(install)) | {_language_of(p) for p in installed_language_packs(install)})


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


def _check_soc_language(install: Install, preferred: str) -> list[Issue]:
    settings = display.user_ltx(install)
    if preferred not in SOC_TEXT:
        return [Issue("language-unavailable", f"{preferred.title()} isn't available",
                      f"{GAMES[install.game].title} has text in {', '.join(l.title() for l in SOC_TEXT)}; "
                      "it keeps its current language.", blocking=False)]
    try:
        current = re.search(r"^g_language (\S+)", settings.read_text(errors="replace"), re.MULTILINE)
    except FileNotFoundError:
        current = None
    code = SOC_TEXT[preferred]
    if (current.group(1) if current else "eng") == code:
        return []

    def fix() -> str:
        # g_language_ltx must name the language in the game's localization.ltx, or the engine ignores
        # g_language and uses that default instead.
        display.set_vars(settings, {"g_language": code, "g_language_ltx": "eng"})
        return f"Switched the game's text to {preferred.title()}"

    return [Issue("language", f"Switch text to {preferred.title()}",
                  "Shadow of Chernobyl's text changes; voices stay English.", fix)]


def _check_language(install: Install, preferred: str) -> list[Issue]:
    if not GAMES[install.game].uses_localization:
        return _check_soc_language(install, preferred)
    loc = install.path / "localization"
    installed = installed_language_packs(install)
    active = {_language_of(p) for p in installed}
    packs = available_language_packs(install)
    if not packs and not installed:
        return [Issue("language-missing", "No language pack found",
                      "localization/ has no x<language>.db file, so voices and text are missing and "
                      "a new game will crash. Reinstall the game, or copy the pack into localization/.")]
    if preferred in packs or preferred in active:
        want = preferred
    else:
        want = next(iter(active)) if len(active) == 1 else "english" if "english" in packs else next(iter(packs))
    notes = []
    if want != preferred:
        notes.append(Issue("language-unavailable", f"{preferred.title()} isn't available",
                           f"{GAMES[install.game].title} has {', '.join(l.title() for l in available_languages(install))}; "
                           f"it stays in {want.title()}.", blocking=False))
    if active == {want}:
        return notes
    files = packs.get(want, [p for p in installed if _language_of(p) == want])
    size_mb = sum(p.stat().st_size for p in files if p.is_file()) // 2**20
    game_root = (_linked_source(install) or install.path).resolve()

    def fix() -> str:
        for pack in installed_language_packs(install):
            if _language_of(pack) == want:
                continue
            if pack.is_symlink():
                pack.unlink()
            else:  # a real file the launcher copied or the player put there: keep it
                (loc / INACTIVE).mkdir(exist_ok=True)
                pack.replace(loc / INACTIVE / pack.name)
        for src in files:
            dst = loc / src.name
            if os.path.lexists(dst):
                continue
            if src.parent.name == INACTIVE:
                src.replace(dst)
            elif str(src.resolve()).startswith(str(game_root) + os.sep):
                dst.symlink_to(src)  # part of the game install: link it
            else:
                _copy_into(src, loc)  # GOG's support files can be cleaned up by Heroic: copy them
        # Let the active pack's localization.ltx decide, not the language saved from last time.
        display.set_vars(display.user_ltx(install), {"g_language": None, "g_language_ltx": None})
        return f"Switched to the {want.title()} language pack"

    if not active:
        title = f"{want.title()} language pack not installed"
        detail = (f"Voices and text live in {', '.join(f.name for f in files)} ({size_mb} MB), which "
                  "isn't in localization/. Without it, starting a new game crashes.")
    else:
        title = f"Switch language to {want.title()}"
        detail = f"Activates {', '.join(f.name for f in files)}; the current pack is set aside, not deleted."
    return [*notes, Issue("language", title, detail, fix)]


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
