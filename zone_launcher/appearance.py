"""The launcher's look: light or dark style, and which icon it shows in the app menu, taskbar and dock."""

import os
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gsk", "4.0")
gi.require_version("Graphene", "1.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Graphene, Gsk, Gtk  # noqa: E402

from . import APP_ID, launcher_icon  # noqa: E402
from .games import GAMES  # noqa: E402
from .themes import THEMES, stylesheet  # noqa: E402

# Config value -> (label, libadwaita color scheme); the first is the default.
STYLES = {
    "system": ("Follow system", Adw.ColorScheme.DEFAULT),
    "light": ("Light", Adw.ColorScheme.FORCE_LIGHT),
    "dark": ("Dark", Adw.ColorScheme.FORCE_DARK),
    **{key: (theme.label, Adw.ColorScheme.FORCE_DARK if theme.dark else Adw.ColorScheme.FORCE_LIGHT)
       for key, theme in THEMES.items()},
}
DEFAULT_ICON = "default"
# Config value -> label; the first is the default.
ICON_COLOURS = {"colour": "Full colour", "monochrome": "Monochrome", "black": "Black", "white": "White"}
DEFAULT_COLOUR = "colour"


_theme_css: Gtk.CssProvider | None = None


def apply_style(style: str) -> None:
    global _theme_css
    Adw.StyleManager.get_default().set_color_scheme(STYLES.get(style, STYLES["system"])[1])
    if _theme_css is None:
        _theme_css = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), _theme_css,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    theme = THEMES.get(style)
    _theme_css.load_from_string(stylesheet(theme, Adw.get_minor_version() >= 6) if theme else "")


def icon_choices() -> list[tuple[str, str, str]]:
    """(config value, label, icon name) for every icon the launcher can show."""
    return [(DEFAULT_ICON, "Zone Launcher", APP_ID),
            *((key, game.title, game.icon) for key, game in GAMES.items())]


def icon_name(choice: str) -> str:
    return GAMES[choice].icon if choice in GAMES else APP_ID


def apply_icon(choice: str, colour: str) -> str | None:
    """Show the chosen icon wherever the desktop shows the launcher. Returns what went wrong, or None."""
    Gtk.Window.set_default_icon_name(icon_name(choice))
    if choice not in GAMES and colour == DEFAULT_COLOUR:
        launcher_icon.restore()
        return None
    png = render_icon(icon_name(choice), colour)
    if png is None:
        return "That icon wasn't found."
    launcher_icon.set_icon(png, choice if colour == DEFAULT_COLOUR else f"{choice}-{colour}")
    return None


def ensure_icon(choice: str, colour: str) -> None:
    """At startup: put the chosen icon back if it went missing (or was chosen by an older version)."""
    Gtk.Window.set_default_icon_name(icon_name(choice))
    if (choice in GAMES or colour != DEFAULT_COLOUR) and not launcher_icon.has_override():
        try:
            apply_icon(choice, colour)
        except OSError:
            pass


def icon_source(name: str) -> Path | None:
    """The installed file for an icon: the launcher's own from where it's installed (an AppImage's
    GTK also sees the user's icon folder, where the replacement is), a game's from the icon theme."""
    if name == APP_ID:
        bases = (os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share").split(":")
        if os.environ.get("APPDIR"):
            bases.insert(0, f"{os.environ['APPDIR']}/usr/share")
        for base in bases:
            path = Path(base) / "icons" / "hicolor" / "scalable" / "apps" / f"{APP_ID}.svg"
            if path.is_file():
                return path
    theme = Gtk.IconTheme.get_for_display(Gdk.Display.get_default())
    gfile = theme.lookup_icon(name, None, launcher_icon.SIZE, 1, Gtk.TextDirection.NONE, 0).get_file()
    path = gfile.get_path() if gfile else None
    return Path(path) if path and Path(path).is_file() and name in Path(path).name else None


def render_icon(name: str, colour: str = DEFAULT_COLOUR) -> bytes | None:
    """An icon as a PNG of launcher_icon.SIZE pixels (the games' icons go up to 64), in a colour of ICON_COLOURS."""
    source = icon_source(name)
    if source is None:
        return None
    size = launcher_icon.SIZE
    paintable = Gtk.IconPaintable.new_for_file(Gio.File.new_for_path(str(source)), size, 1)
    snapshot = Gtk.Snapshot()
    paintable.snapshot(snapshot, size, size)
    node = snapshot.to_node()
    if node is None:
        return None
    renderer = Gsk.CairoRenderer()
    renderer.realize_for_display(Gdk.Display.get_default())
    try:
        texture = renderer.render_texture(node, Graphene.Rect().init(0, 0, size, size))
    finally:
        renderer.unrealize()
    if colour != DEFAULT_COLOUR:
        texture = recolour(texture, colour)
    return texture.save_to_png_bytes().get_data()


def recolour(texture: Gdk.Texture, colour: str) -> Gdk.Texture:
    downloader = Gdk.TextureDownloader.new(texture)
    downloader.set_format(Gdk.MemoryFormat.R8G8B8A8)
    data, stride = downloader.download_bytes()
    width, height = texture.get_width(), texture.get_height()
    if colour == "monochrome":
        pixels = launcher_icon.greyscale(data.get_data(), width, height, stride)
    else:
        pixels = launcher_icon.one_colour(data.get_data(), width, height, stride, colour == "white")
    return Gdk.MemoryTexture.new(width, height, Gdk.MemoryFormat.R8G8B8A8, GLib.Bytes.new(pixels), stride)
