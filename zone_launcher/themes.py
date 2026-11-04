"""The launcher's colour themes, beyond the desktop's own light and dark."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Theme:
    label: str
    dark: bool
    background: str
    surface: str  # cards, lists and menus
    header: str
    text: str
    accent: str  # Play, switches and links
    accent_text: str  # text on the accent


THEMES = {
    "zone": Theme("The Zone", True, "#1d1f17", "#2a2d21", "#16180f", "#e6e3d0", "#c9a227", "#1a1700"),
    "clear-sky": Theme("Clear Sky", True, "#141b26", "#1f2a3a", "#0f1520", "#e1e8f2", "#5b9bd5", "#06121f"),
    "pripyat": Theme("Pripyat", True, "#202122", "#2e2f31", "#18191a", "#e7e5e2", "#d9722e", "#1c0d02"),
    "emission": Theme("Emission", True, "#1a1220", "#2a1d33", "#130c18", "#eee4f2", "#d9486e", "#1e0409"),
    "pda": Theme("PDA", True, "#0d1410", "#16211a", "#0a0f0c", "#c9e8d2", "#4fc26f", "#04140a"),
    "blackout": Theme("Blackout", True, "#000000", "#141414", "#000000", "#e8e8e8", "#d9d9d9", "#000000"),
    "cordon": Theme("Cordon", False, "#ece6d6", "#f7f3e8", "#e0d8c3", "#2b2618", "#7d5f0c", "#ffffff"),
    "frost": Theme("Frost", False, "#e8eef4", "#f6f9fc", "#dbe4ed", "#1a2430", "#2a6aa8", "#ffffff"),
}


def stylesheet(theme: Theme, variables: bool) -> str:
    """CSS giving libadwaita's colours a theme's. variables: it reads CSS variables (from libadwaita
    1.6), rather than the named colours of the version the AppImage is built with."""
    colours = {
        "window-bg-color": theme.background, "window-fg-color": theme.text,
        "view-bg-color": theme.background, "view-fg-color": theme.text,
        "headerbar-bg-color": theme.header, "headerbar-fg-color": theme.text,
        "headerbar-backdrop-color": theme.header,
        "card-bg-color": theme.surface, "card-fg-color": theme.text,
        "dialog-bg-color": theme.background, "dialog-fg-color": theme.text,
        "popover-bg-color": theme.surface, "popover-fg-color": theme.text,
        "accent-bg-color": theme.accent, "accent-fg-color": theme.accent_text, "accent-color": theme.accent,
    }
    if variables:
        return ":root {\n" + "".join(f"  --{name}: {value};\n" for name, value in colours.items()) + "}\n"
    return "".join(f"@define-color {name.replace('-', '_')} {value};\n" for name, value in colours.items())
