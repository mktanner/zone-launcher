"""Locate the OpenXRay engine, launch it, and read back its logs."""

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .detect import Install
from .games import GAMES


@dataclass(frozen=True)
class Engine:
    binary: Path
    # <prefix>/share/openxray: the fsgame.ltx and OpenGL shaders that match this binary.
    share: Path

    @property
    def fsgame(self) -> Path:
        return self.share / "fsgame.ltx"

    @property
    def gl_shaders(self) -> Path:
        return self.share / "gamedata" / "shaders" / "gl"


def find_engine(override: str | None = None) -> Engine | None:
    # ZONE_LAUNCHER_ENGINE is set by the AppImage to its bundled engine.
    candidates = [override, os.environ.get("ZONE_LAUNCHER_ENGINE"), "/app/bin/xr_3da",
                  shutil.which("xr_3da"), "/usr/games/xr_3da"]
    for candidate in filter(None, candidates):
        binary = Path(candidate)
        if not (binary.is_file() and os.access(binary, os.X_OK)):
            continue
        # /app/bin -> /app/share, /usr/games -> /usr/share, /usr/local/bin -> /usr/local/share
        share = binary.resolve().parent.parent / "share" / "openxray"
        if (share / "fsgame.ltx").is_file():
            return Engine(binary, share)
    return None


def engine_environment(env: dict[str, str] | None = None) -> dict[str, str]:
    """The environment for the engine: without the AppImage's Python/GTK settings.

    The AppImage's AppRun lists the variables it set in ZONE_LAUNCHER_SET_VARS and saves any value
    they had before as ZONE_LAUNCHER_ORIG_<name>. The engine finds its own bundled libraries
    through its RUNPATH; inheriting LD_LIBRARY_PATH could make the host's GL driver load the
    launcher's older copies of libraries it shares with them.
    """
    env = dict(os.environ if env is None else env)
    for name in env.pop("ZONE_LAUNCHER_SET_VARS", "").split():
        original = env.pop(f"ZONE_LAUNCHER_ORIG_{name}", None)
        if original is None:
            env.pop(name, None)
        else:
            env[name] = original
    return env


def launch(engine: Engine, install: Install, extra_args: list[str] | None = None) -> subprocess.Popen:
    flag = GAMES[install.game].engine_flag
    args = [str(engine.binary), *([flag] if flag else []), *(extra_args or [])]
    # With fsgame.ltx in the working directory, OpenXRay treats that folder as the game root.
    return subprocess.Popen(args, cwd=install.path, stdin=subprocess.DEVNULL, env=engine_environment())


def latest_log(install: Install) -> Path | None:
    logs = sorted((install.path / "_appdata_" / "logs").glob("*.log"), key=lambda p: p.stat().st_mtime)
    return logs[-1] if logs else None


def crash_summary(log_text: str) -> str | None:
    """Pull the part of an OpenXRay log that explains a crash, or None if it didn't crash."""
    lines = log_text.splitlines()
    try:
        fatal = next(i for i, line in enumerate(lines) if line.strip() == "FATAL ERROR")
    except StopIteration:
        return None
    # Lua errors are logged just before FATAL ERROR and usually name the real cause.
    start = fatal
    while start > 0 and (lines[start - 1].startswith("! ") or not lines[start - 1].strip()):
        start -= 1
    end = next((i for i in range(fatal, len(lines)) if lines[i].startswith("stack trace")), len(lines))
    return "\n".join(line for line in lines[start:end] if line.strip())
