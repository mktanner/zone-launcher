import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from zone_launcher import detect, files, mods, setup
from zone_launcher.detect import Install
from tests.test_core import touch

SOC_GOG = "1207660573"
COP_GOG = "1207660583"


def make_soc(root: Path) -> Path:
    touch(root / f"goggame-{SOC_GOG}.info")
    touch(root / "gamedata.db0", b"archive")
    touch(root / "bin" / "XR_3DA.exe")
    return root


def make_cop(root: Path) -> Path:
    touch(root / "Stalker-COP.exe")
    for name in ("configs.db", "resources.db0", "resources.db1", "resources.db2", "resources.db3",
                 "resources.db4"):
        touch(root / "resources" / name)
    (root / "levels").mkdir()
    touch(root / "patches" / "xpatch_02.db")
    return root


class TempCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.home = self.tmp / "home"

    def tearDown(self):
        self._tmp.cleanup()


class SearchTests(TempCase):
    def test_finds_games_in_wine_prefixes_and_drives(self):
        prefix = self.home / ".wine" / "drive_c"
        galaxy = make_soc(prefix / "Program Files (x86)" / "GOG Galaxy" / "Games" / "STALKER Shadow of Chernobyl")
        bottle = make_cop(self.home / ".local/share/bottles/bottles/Games/drive_c/Program Files/GSC/COP")
        drive = make_soc(self.tmp / "drive" / "Games" / "Retail" / "SoC")
        # A prefix's z: drive links to /, which must never be walked.
        (prefix.parent / "dosdevices").mkdir()
        os.symlink("/", prefix.parent / "dosdevices" / "z:")
        found = detect.search([self.home / ".wine", self.home / ".local/share/bottles/bottles", self.tmp / "drive"])
        self.assertEqual(sorted(found), sorted([("soc", galaxy), ("cop", bottle), ("soc", drive)]))

    def test_depth_and_launcher_folders(self):
        make_soc(self.tmp / "a" / "b" / "c" / "d" / "e" / "f" / "soc")
        linked = self.tmp / "linked"
        make_soc(linked)
        touch(linked / detect.MARKER, b"/elsewhere\n")
        self.assertEqual(detect.search([self.tmp], depth=3), [])
        self.assertEqual([k for k, _ in detect.search([self.tmp], depth=8)], ["soc"])

    def test_locate_accepts_parent_subfolder_and_linked_folder(self):
        games = self.tmp / "Games"
        soc = make_soc(games / "Heroic" / "SoC")
        cop = make_cop(games / "Heroic" / "CoP")
        self.assertEqual(sorted(detect.locate(games)), sorted([("soc", soc), ("cop", cop)]))
        self.assertEqual(detect.locate(cop / "resources"), [("cop", cop)])
        self.assertEqual(detect.locate(soc / "bin"), [("soc", soc)])
        setup.link(soc, self.tmp / "openxray")
        self.assertEqual(detect.locate(self.tmp / "openxray"), [("soc", soc)])
        self.assertEqual(detect.locate(self.tmp / "openxray" / "bin" / ".."), [("soc", soc)])
        (self.tmp / "empty").mkdir()
        self.assertEqual(detect.locate(self.tmp / "empty"), [])


class FindInstallsTests(TempCase):
    def test_lutris_heroic_sideload_and_searched_folders(self):
        lutris_game = make_cop(self.tmp / "lutris" / "cop")
        db = self.home / ".local/share/lutris/pga.db"
        db.parent.mkdir(parents=True)
        with sqlite3.connect(db) as conn:
            conn.execute("CREATE TABLE games (directory TEXT)")
            conn.execute("INSERT INTO games VALUES (?)", (str(self.tmp / "lutris"),))
        self.assertIn(self.tmp / "lutris", detect.lutris_dirs(self.home))

        sideloaded = make_soc(self.tmp / "sideload" / "SoC")
        touch(self.home / ".config/heroic/sideload_apps/library.json", json.dumps(
            {"games": [{"install": {"executable": str(sideloaded / "bin" / "XR_3DA.exe")}}]}).encode())

        found = detect.find_installs(self.home, searched=[str(lutris_game), str(sideloaded)])
        self.assertEqual([(i.game, i.path, i.sources) for i in found], [
            ("soc", sideloaded, ["Heroic"]),  # not also "Found by search"
            ("cop", lutris_game, [detect.SEARCHED]),
        ])

    def test_contents(self):
        cop = Install("cop", make_cop(self.tmp / "cop"))
        self.assertEqual(detect.missing_files(cop), [])
        self.assertEqual(detect.patch_level(cop), "1.6.02")
        (cop.path / "resources" / "resources.db4").unlink()
        self.assertEqual(detect.missing_files(cop), ["resources/resources.db4"])
        self.assertIsNone(detect.patch_level(Install("soc", make_soc(self.tmp / "soc"))))


class OwnCopyTests(TempCase):
    def test_picking_another_install_moves_the_copy_aside_with_its_saves(self):
        target = setup.target_dir("soc", self.home)
        make_soc(target)
        touch(target / "_appdata_" / "savedgames" / "quick.sav", b"save")
        heroic = Install("soc", make_soc(self.tmp / "heroic" / "SoC"), ["Heroic"])

        run, issue = setup.plan(heroic, self.home)
        self.assertEqual(run.path, target)
        self.assertEqual(issue.id, "setup-own-copy")
        issue.fix()
        aside = target.with_name(f"{target.name} (own copy)")
        self.assertTrue((aside / "gamedata.db0").is_file())
        self.assertTrue((aside / "_appdata_" / "savedgames" / "quick.sav").is_file())
        self.assertEqual((target / "_appdata_" / "savedgames" / "quick.sav").read_bytes(), b"save")
        self.assertEqual(setup.linked_source(target), heroic.path)
        self.assertEqual(setup.plan(heroic, self.home), (run, None))

        # The renamed copy is still listed, so it can be picked again.
        found = detect.find_installs(self.home, searched=[str(heroic.path)])
        self.assertIn(aside, [i.path for i in found])


class ClassifierTests(TempCase):
    def test_where_files_come_from(self):
        source = make_soc(self.tmp / "heroic" / "SoC")
        touch(source / "gamedata" / "config" / "game.ltx")
        run = self.tmp / "openxray"
        setup.link(source, run)
        touch(run / "fsgame.ltx")
        touch(run / "gamedata" / "my_tweak.ltx")
        touch(run / "_appdata_" / "savedgames" / "quick.sav", b"1234")
        os.symlink(self.tmp / "gone.db", run / "patches_gone.db")
        engine_share = self.tmp / "engine" / "share" / "openxray"
        (engine_share / "gamedata" / "shaders" / "gl").mkdir(parents=True)
        (run / "gamedata" / "shaders").mkdir(exist_ok=True)
        os.symlink(engine_share / "gamedata" / "shaders" / "gl", run / "gamedata" / "shaders" / "gl")

        with mock.patch.dict(os.environ, {"XDG_DATA_HOME": str(self.tmp / "data")}):
            lib = mods.library_dir("soc") / "cool-mod"
            touch(lib / "gamedata" / "scripts" / "cool.script")
            os.symlink(lib / "gamedata" / "scripts", run / "gamedata" / "scripts")
            c = files.Classifier("soc", source, run, engine_share)
            top = {e.name: e for e in c.list(run)}
            data = {e.name: e for e in c.list(run / "gamedata")}

        self.assertEqual(top["gamedata.db0"].origin, "install")
        self.assertEqual(top["gamedata.db0"].size, 7)
        self.assertEqual(top["fsgame.ltx"].origin, "launcher")
        self.assertEqual(top["patches_gone.db"].origin, "missing")
        self.assertTrue(top["patches_gone.db"].removable)
        self.assertEqual(top["_appdata_"].origin, "folder")
        self.assertTrue(top["gamedata"].is_dir)
        self.assertEqual(data["config"].origin, "install")
        self.assertEqual(data["scripts"].detail, "Mod: cool-mod, 1 item")
        self.assertEqual(data["my_tweak.ltx"].origin, "yours")
        self.assertTrue(data["my_tweak.ltx"].removable)
        self.assertEqual(data["shaders"].origin, "folder")
        self.assertEqual(c.entry(run / "gamedata" / "shaders" / "gl").origin, "engine")
        self.assertEqual(c.entry(run / "_appdata_" / "savedgames" / "quick.sav").detail, "Saved game")

        # The original install is browsable but read-only.
        self.assertTrue(c.read_only(source / "bin"))
        self.assertFalse(any(e.removable for e in c.list(source)))
        self.assertEqual(files.summary(c.list(source)), "4 items · 8 bytes · 2 from the install · 2 folders")

    def test_playing_from_the_install_itself(self):
        game = make_soc(self.tmp / "SoC")
        touch(game / "fsgame.ltx")
        touch(game / "_appdata_" / "user.ltx")
        c = files.Classifier("soc", game, game)
        top = {e.name: e for e in c.list(game)}
        self.assertEqual(top["fsgame.ltx"].origin, "launcher")
        self.assertEqual(top["gamedata.db0"].origin, "install")
        self.assertFalse(top["gamedata.db0"].removable)
        self.assertTrue(c.entry(game / "_appdata_" / "user.ltx").removable)


if __name__ == "__main__":
    unittest.main()
