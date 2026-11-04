"""GOG's setup step for a game, which Heroic doesn't run with the download. For some games it's a
small program that puts the language pack in place and sets the game up for Windows. For others
it's a list of steps in GOG's install script, and the ones that write files are done here: the
registry values and save folder they also name only matter to GOG Galaxy and a Windows install."""

import json
import re
import shutil
import subprocess
from pathlib import Path

from . import standalone
from .detect import Install

SETUP_TIMEOUT = 300
# GOG's script puts these in the Windows user's Documents, which doesn't exist here.
USERDOCS = "userdocs"


def _read(install: Install, pattern: str) -> dict:
    for path in sorted(install.path.glob(pattern)):
        try:
            data = json.loads(path.read_text(errors="replace"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict):
            return data
    return {}


def setup_program(install: Install) -> Path | None:
    """The program GOG's install script names, if the store has downloaded it."""
    if not install.gog_support:
        return None
    for action in _read(install, "goggame-*.script").get("actions", []):
        name = action.get("install", {}).get("arguments", {}).get("executable")
        if name and (install.gog_support / name).is_file():
            return install.gog_support / name
    return None


def _inside_game(install: Install, template: str) -> Path | None:
    """Where a script's path goes. Only ever in the game's folder, whatever the script says."""
    path = template.replace("{app}", str(install.path)).replace("{userdocs}", str(install.path / USERDOCS))
    if "{" in path:
        return None
    path = Path(path).resolve()
    return path if path.is_relative_to(install.path.resolve()) and path != install.path.resolve() else None


def _file_with_text(install: Install, arguments: dict) -> tuple[Path, str] | None:
    target, text = _inside_game(install, arguments.get("filename", "")), arguments.get("text")
    if target is None or not isinstance(text, str):
        return None
    # In the file list: $app_data_root$ = true | false| {userdocs}\Stalker-SHOC
    return target, re.sub(r"\{userdocs\}\\?([^\s|]*)", lambda m: f"$fs_root$| {USERDOCS}\\{m[1]}\\", text)


def _copied_file(install: Install, arguments: dict) -> tuple[Path, Path] | None:
    if not install.gog_support or arguments.get("type") != "File":
        return None
    name = arguments.get("source", "")
    target = _inside_game(install, arguments.get("target", ""))
    if target is None or not name.startswith("{supportDir}/"):
        return None
    source = (install.gog_support / name.removeprefix("{supportDir}/")).resolve()
    return (source, target) if source.is_relative_to(install.gog_support.resolve()) and source.is_file() else None


def _script_steps(install: Install) -> tuple[list[tuple[Path, str]], list[tuple[Path, Path]]]:
    """The files GOG's install script writes: (where, text), and (from, where) to copy."""
    written, copied = [], []
    for action in _read(install, "goggame-*.script").get("actions", []):
        step = action.get("install", {})
        arguments = step.get("arguments", {})
        if step.get("action") == "saveToFile" and (file := _file_with_text(install, arguments)):
            written.append(file)
        elif step.get("action") == "supportData" and (file := _copied_file(install, arguments)):
            copied.append(file)
    return written, copied


def has_setup(install: Install) -> bool:
    written, copied = _script_steps(install)
    return bool(setup_program(install) or written or copied)


def setup_done(install: Install) -> bool:
    """Whether a game has been through the step: a program leaves its log in the game's folder, and
    a script's files are there."""
    if (install.path / "goglog.ini").is_file():
        return True
    if setup_program(install):
        return False
    written, copied = _script_steps(install)
    targets = [path for path, _ in written] + [target for _, target in copied]
    return bool(targets) and all(path.exists() for path in targets)


def can_run_again(install: Install) -> bool:
    return setup_program(install) is not None


def _run_script_steps(install: Install) -> None:
    """Writes what's missing. Anything already there is left alone: it may be the player's."""
    written, copied = _script_steps(install)
    for path, text in written:
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
    for source, target in copied:
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)


def setup_arguments(install: Install) -> list[str]:
    language = _read(install, "goggame-*.info").get("language", "English")
    folder = "Z:" + str(install.path).replace("/", "\\")
    arguments = ["/VERYSILENT", f"/DIR={folder}", f"/Language={language}", f"/LANG={language.lower()}",
                 "/galaxyclient", "/nodesktopshortcut", "/NOICONS"]
    if product := _read(install, "goggame-*.script").get("productId"):
        arguments.append(f"/ProductId={product}")
    return arguments


def run_setup(install: Install) -> None:
    """Runs the step, which writes to the game's folder. Takes a while, so not for the main thread."""
    if not (program := setup_program(install)):
        if not has_setup(install):
            raise OSError("GOG's setup wasn't found")
        _run_script_steps(install)
        return
    mod = standalone.Standalone(install.path, f"{install.path.name} setup", None, standalone.Settings())
    if problem := standalone.runner_problem(mod.runner):
        raise OSError(problem)
    # It adds a path inside its own Wine prefix to the file list, which wouldn't work from anywhere else.
    file_list = install.path / "fsgame.ltx"
    original = file_list.read_bytes() if file_list.is_file() else None
    process = standalone.run_program(mod, program, setup_arguments(install))
    try:
        code = process.wait(timeout=SETUP_TIMEOUT)
    except subprocess.TimeoutExpired:
        process.kill()
        raise OSError("It didn't finish in 5 minutes") from None
    finally:
        if original is not None:
            file_list.write_bytes(original)
    if code != 0:
        raise OSError(f"It stopped with error {code}")
