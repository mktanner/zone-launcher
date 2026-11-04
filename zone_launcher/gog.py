"""GOG's setup step for a game: a small program that puts the language pack in place and sets the
game up for Windows. Heroic downloads it with the game but doesn't run it with the download."""

import json
import subprocess
from pathlib import Path

from . import standalone
from .detect import Install

SETUP_TIMEOUT = 300


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


def setup_done(install: Install) -> bool:
    """Whether a game has been through the step: it leaves its log in the game's folder."""
    return (install.path / "goglog.ini").is_file()


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
        raise OSError("GOG's setup program wasn't found")
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
