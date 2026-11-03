"""Launcher settings, stored as JSON in the user's config folder."""

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path


def config_path() -> Path:
    # Inside the Flatpak, XDG_CONFIG_HOME is ~/.var/app/<app id>/config.
    base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "zone-launcher" / "config.json"


@dataclass
class Config:
    # Game key -> path of the install the user picked to play.
    selected: dict[str, str] = field(default_factory=dict)
    # Folders the user added (or found with a search they started).
    extra_folders: list[str] = field(default_factory=list)
    # Folders the launcher's own search of the usual places found last time.
    found_folders: list[str] = field(default_factory=list)
    # Found folders the user removed from the list, so searching doesn't bring them back.
    hidden_folders: list[str] = field(default_factory=list)
    # Folders of standalone mods the user added.
    standalone_mods: list[str] = field(default_factory=list)
    # A standalone mod's folder -> the program the user chose to start it, relative to the folder.
    standalone_launchers: dict[str, str] = field(default_factory=dict)
    # A standalone mod's folder -> the fields of standalone.Settings the user changed.
    standalone_settings: dict[str, dict[str, str]] = field(default_factory=dict)
    extra_args: str = ""
    language: str = "english"
    # "native", "keep" (use the in-game setting) or "WIDTHxHEIGHT".
    resolution: str = "native"
    # A key of display.WINDOW_MODES, or "keep".
    window_mode: str = "fullscreen"
    engine_override: str = ""
    # A key of appearance.STYLES.
    style: str = "system"
    # "default" (the launcher's own) or a game key: the icon in the app menu, taskbar and dock.
    icon: str = "default"
    # Show that icon in grey.
    monochrome_icon: bool = False

    @classmethod
    def load(cls) -> "Config":
        try:
            data = json.loads(config_path().read_text())
        except (OSError, ValueError):
            return cls()
        known = cls.__dataclass_fields__
        return cls(**{k: v for k, v in data.items() if k in known})

    def save(self) -> None:
        path = config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self), indent=2))
        tmp.replace(path)
