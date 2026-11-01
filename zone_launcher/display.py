"""Resolution and display mode, written into the game's user.ltx before launch."""

import re
from pathlib import Path

from .detect import Install

# Launcher setting -> value of the engine's vid_window_mode console command.
WINDOW_MODES = {
    "fullscreen": ("Fullscreen", "st_opt_fullscreen"),
    "borderless_fullscreen": ("Borderless fullscreen", "st_opt_fullscreen_borderless"),
    "windowed": ("Windowed", "st_opt_windowed"),
    "borderless_window": ("Borderless window", "st_opt_windowed_borderless"),
}
KEEP = "keep"
NATIVE = "native"

COMMON_RESOLUTIONS = [
    (3840, 2160), (3440, 1440), (2560, 1600), (2560, 1440), (2560, 1080), (1920, 1200),
    (1920, 1080), (1680, 1050), (1600, 900), (1366, 768), (1280, 800), (1280, 720),
]

VID_MODE = re.compile(r"^vid_mode\s+(\d+)x(\d+)(?:\s+\((\d+)Hz\))?\s*$")


def resolution_choices(native: tuple[int, int] | None) -> list[tuple[int, int]]:
    """Common resolutions that fit on the monitor, largest first."""
    if not native:
        return COMMON_RESOLUTIONS
    return [r for r in COMMON_RESOLUTIONS if r[0] <= native[0] and r[1] <= native[1] and r != native]


def user_ltx(install: Install) -> Path:
    # OpenXRay's fsgame.ltx puts $app_data_root$ at <game>/_appdata_/.
    return install.path / "_appdata_" / "user.ltx"


def set_vars(path: Path, wanted: dict[str, str | None]) -> None:
    """Set console variables in user.ltx (None removes one), keeping everything else as it was."""
    try:
        text = path.read_bytes().decode("latin-1")
    except FileNotFoundError:
        text = ""
    newline = "\r\n" if "\r\n" in text else "\n"
    seen = set()
    out = []
    for line in text.splitlines():
        key = line.split(maxsplit=1)[0] if line.strip() else ""
        if key in wanted:
            if key not in seen and wanted[key] is not None:
                out.append(f"{key} {wanted[key]}")
            seen.add(key)
            continue  # drop duplicates so a later line can't undo ours
        out.append(line)
    out += [f"{k} {v}" for k, v in wanted.items() if k not in seen and v is not None]
    if out == text.splitlines():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes((newline.join(out) + newline).encode("latin-1"))
    tmp.replace(path)


def apply(path: Path, resolution: tuple[int, int] | None, window_mode: str) -> None:
    """Set vid_mode / vid_window_mode in user.ltx; None or KEEP leaves that setting alone."""
    wanted: dict[str, str | None] = {}
    if resolution:
        w, h = resolution
        value = f"{w}x{h}"
        try:
            lines = path.read_bytes().decode("latin-1").splitlines()
        except FileNotFoundError:
            lines = []
        old = next((m for m in map(VID_MODE.match, lines) if m), None)
        # Keep the refresh rate only while the resolution is unchanged; another size may not support it.
        if old and old.group(3) and (int(old.group(1)), int(old.group(2))) == (w, h):
            value += f" ({old.group(3)}Hz)"
        wanted["vid_mode"] = value
    if window_mode in WINDOW_MODES:
        wanted["vid_window_mode"] = WINDOW_MODES[window_mode][1]
        # Older setting the engine still reads; it must agree or it overrides the mode above.
        wanted["rs_fullscreen"] = "on" if window_mode == "fullscreen" else "off"
    if wanted:
        set_vars(path, wanted)
