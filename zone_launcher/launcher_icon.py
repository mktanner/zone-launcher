"""Showing another launcher icon everywhere the desktop shows the launcher (app menu, taskbar, dock,
pinned or not), and putting the original back.

The launcher's desktop entry names its icon (Icon=io.github.mktanner.ZoneLauncher), and desktops look
icons up in the user's icon folder before the installed ones. So an icon of that name in
~/.local/share/icons replaces it, however the entry is referred to: a pinned panel launcher can name
the Flatpak's desktop file directly, bypassing any entry in ~/.local/share/applications.
"""

import base64
import os
import re
import shlex
from pathlib import Path

from . import APP_ID

# Marks the files the launcher wrote or changed, so it only ever undoes its own.
MARKER = "X-ZoneLauncher-Icon"
ORIGINAL = "X-ZoneLauncher-Original-Icon"
SIZE = 256


def _brightness(r: int, g: int, b: int) -> int:
    # Perceived brightness (Rec. 601), 0 to 255.
    return (r * 299 + g * 587 + b * 114) // 1000


def greyscale(pixels: bytes, width: int, height: int, stride: int) -> bytes:
    """RGBA pixels in grey."""
    out = bytearray(pixels)
    for y in range(height):
        row = y * stride
        for i in range(row, row + width * 4, 4):
            out[i] = out[i + 1] = out[i + 2] = _brightness(*out[i:i + 3])
    return bytes(out)


def _coverage(brightness: int) -> float:
    """How bright a pixel counts as, 0 to 1: a soft step from dark to bright keeps the edges smooth."""
    t = min(1.0, max(0.0, (brightness / 255 - 0.3) / 0.3))
    return t * t * (3 - 2 * t)


def one_colour(pixels: bytes, width: int, height: int, stride: int, white: bool) -> bytes:
    """RGBA pixels in pure white or black, on nothing. A silhouette would be a blank disc, so the
    picture is kept: white keeps the bright parts of it, black the dark parts."""
    out = bytearray(pixels)
    value = 255 if white else 0
    for y in range(height):
        row = y * stride
        for i in range(row, row + width * 4, 4):
            kept = _coverage(_brightness(*out[i:i + 3]))
            out[i + 3] = int(out[i + 3] * (kept if white else 1 - kept))
            out[i] = out[i + 1] = out[i + 2] = value
    return bytes(out)


def share_dir() -> Path:
    # The host's folder, also inside the Flatpak (whose XDG_DATA_HOME is its own).
    return Path.home() / ".local" / "share"


def override_path() -> Path:
    # Scalable, so it matches every size the desktop asks for before the installed SVG does.
    return share_dir() / "icons" / "hicolor" / "scalable" / "apps" / f"{APP_ID}.svg"


def png_path(name: str) -> Path:
    return share_dir() / "zone-launcher" / "icons" / f"{name}.png"


def has_override() -> bool:
    try:
        return MARKER in override_path().read_text()
    except (OSError, UnicodeDecodeError):
        return False


def set_icon(png: bytes, name: str) -> None:
    """Show a PNG (SIZE pixels square) as the launcher's icon. name: what it is, for its file name."""
    restore_legacy_entry()
    path = override_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = base64.b64encode(png).decode()
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
           f'width="{SIZE}" height="{SIZE}" viewBox="0 0 {SIZE} {SIZE}">\n<!-- {MARKER} -->\n'
           f'<image width="{SIZE}" height="{SIZE}" xlink:href="data:image/png;base64,{data}"/>\n</svg>\n')
    write(path, svg.encode())
    touch_theme()
    # A tool like Gear Lever gives an AppImage its own entry, with an icon file of its own.
    appimage = os.environ.get("APPIMAGE")
    if appimage:
        png_file = png_path(name)
        png_file.parent.mkdir(parents=True, exist_ok=True)
        write(png_file, png)
        for entry in appimage_entries(appimage):
            entry.write_text(with_icon(entry.read_text(), str(png_file)))


def restore() -> None:
    """Back to the launcher's own icon."""
    if has_override():
        override_path().unlink()
        touch_theme()
    restore_legacy_entry()
    folder = share_dir() / "applications"
    for entry in folder.glob("*.desktop") if folder.is_dir() else ():
        try:
            text = entry.read_text()
        except (OSError, UnicodeDecodeError):
            continue
        original = re.search(rf"^{ORIGINAL}=(.*)$", text, re.M)
        if original and re.search(rf"^{MARKER}=", text, re.M):
            text = with_key(text, "Icon", original.group(1))
            entry.write_text(re.sub(rf"^({MARKER}|{ORIGINAL})=.*\n?", "", text, flags=re.M))


def restore_legacy_entry() -> None:
    """Remove the desktop entry 0.2.3's development builds wrote to change the icon."""
    entry = share_dir() / "applications" / f"{APP_ID}.desktop"
    try:
        if re.search(rf"^{MARKER}=", entry.read_text(), re.M):
            entry.unlink()
    except (OSError, UnicodeDecodeError):
        pass


def write(path: Path, data: bytes) -> None:
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


def touch_theme() -> None:
    """Desktops and GTK keep icon caches, checked against the icon folders' times."""
    for folder in (share_dir() / "icons" / "hicolor", share_dir() / "icons"):
        try:
            os.utime(folder)
        except OSError:
            pass


def with_icon(text: str, icon: str) -> str:
    """An entry made by another tool with our icon, keeping its own so it can be restored."""
    if not re.search(rf"^{ORIGINAL}=", text, re.M):
        current = re.search(r"^Icon=(.*)$", text, re.M)
        text = with_key(text, ORIGINAL, current.group(1) if current else "")
    text = with_key(text, "Icon", icon)
    return with_key(text, MARKER, "true")


def with_key(text: str, key: str, value: str) -> str:
    """Set a key in the [Desktop Entry] group (the first group), adding it at the group's end."""
    lines = text.splitlines()
    end = next((i for i, line in enumerate(lines) if i > 0 and line.startswith("[")), len(lines))
    for i in range(end):
        if lines[i].startswith(f"{key}="):
            lines[i] = f"{key}={value}"
            break
    else:
        while end > 0 and not lines[end - 1].strip():
            end -= 1
        lines.insert(end, f"{key}={value}")
    return "\n".join(lines) + "\n"


def appimage_entries(appimage: str) -> list[Path]:
    """Desktop entries other tools made that start this AppImage."""
    folder = share_dir() / "applications"
    if not folder.is_dir():
        return []
    found = []
    for path in folder.glob("*.desktop"):
        try:
            match = re.search(r"^Exec=(.*)$", path.read_text(), re.M)
            words = shlex.split(match.group(1)) if match else []
        except (OSError, UnicodeDecodeError, ValueError):
            continue
        if appimage in words:
            found.append(path)
    return found
