"""Static facts about the three X-Ray games OpenXRay can run."""

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Game:
    key: str
    title: str
    # Command-line switch that tells xr_3da which game it is running.
    engine_flag: str | None
    # Folder name OpenXRay uses under ~/.local/share/GSC Game World/.
    pref_dir: str
    gog_id: str
    steam_appid: str
    # Clear Sky and Call of Pripyat keep voice/text in localization/x<language>.db.
    uses_localization: bool
    icon: str


GAMES: dict[str, Game] = {
    g.key: g
    for g in (
        Game("soc", "Shadow of Chernobyl", "-soc", "S.T.A.L.K.E.R. - Shadow of Chernobyl",
             "1207660573", "4500", False, "openxray_soc"),
        Game("cs", "Clear Sky", "-cs", "S.T.A.L.K.E.R. - Clear Sky",
             "1207660603", "20510", True, "openxray_cs"),
        Game("cop", "Call of Pripyat", None, "S.T.A.L.K.E.R. - Call of Pripyat",
             "1207660583", "41700", True, "openxray_cop"),
    )
}

GOG_IDS = {g.gog_id: g.key for g in GAMES.values()}
STEAM_APPIDS = {g.steam_appid: g.key for g in GAMES.values()}


def identify(path: Path) -> str | None:
    """Guess which game a folder holds from the files GSC shipped with it."""
    for gog_id, key in GOG_IDS.items():
        if (path / f"goggame-{gog_id}.info").is_file():
            return key
    if (path / "gamedata.db0").is_file() or (path / "bin" / "XR_3DA.exe").is_file():
        return "soc"
    resources = path / "resources"
    if (resources / "resources.db3").is_file() or (path / "Stalker-COP.exe").is_file():
        return "cop"
    if (resources / "resources.db0").is_file() and (path / "patches" / "xpatch_10.db").is_file():
        return "cs"
    if (resources / "resources.db0").is_file():
        # Clear Sky with a different patch level; Call of Pripyat was caught above.
        return "cs"
    return None
