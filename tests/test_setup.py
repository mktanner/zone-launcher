import os
import tempfile
import unittest
from pathlib import Path

from zone_launcher import detect, fixes, setup
from zone_launcher.detect import Install
from zone_launcher.engine import Engine
from tests.test_core import touch

SOC_GOG = "1207660573"


class LinkedSetupTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.home = self.tmp / "home"
        share = self.tmp / "engine" / "share" / "openxray"
        touch(share / "fsgame.ltx", b"$app_data_root$ = true| false| $fs_root$| _appdata_\\\n")
        (share / "gamedata" / "shaders" / "gl").mkdir(parents=True)
        self.engine = Engine(self.tmp / "engine" / "bin" / "xr_3da", share)

        # A GOG Shadow of Chernobyl as Heroic installs it: no fsgame.ltx, no settings.
        self.source = self.tmp / "ssd" / "Shadow of Chernobyl"
        touch(self.source / f"goggame-{SOC_GOG}.info")
        touch(self.source / "gamedata.db0", b"archive")
        touch(self.source / "bin" / "XR_3DA.exe")
        touch(self.source / "userdocs" / "user.ltx")
        self.support = self.tmp / "support"
        touch(self.support / "userdocs" / "user.ltx", b"_preset Extreme\nbind left kLEFT\n")
        self.install = Install("soc", self.source, ["Heroic"], self.support)
        self.target = setup.target_dir("soc", self.home)

    def tearDown(self):
        self._tmp.cleanup()

    def snapshot(self, root: Path):
        return sorted((str(p.relative_to(root)), p.stat().st_mtime_ns) for p in root.rglob("*"))

    def test_setup_then_fixes_leave_the_source_untouched(self):
        before = self.snapshot(self.source)
        run, issue = setup.plan(self.install, self.home)
        self.assertEqual(issue.id, "setup")
        self.assertEqual(run.path, self.target)
        issue.fix()

        for i in fixes.check(run, self.engine):
            i.fix()
        self.assertEqual(fixes.check(run, self.engine), [])
        self.assertEqual(self.snapshot(self.source), before)

        self.assertTrue((self.target / "gamedata.db0").is_symlink())
        self.assertEqual((self.target / "gamedata.db0").read_bytes(), b"archive")
        self.assertFalse((self.target / "userdocs").exists())
        fsgame = (self.target / "fsgame.ltx").read_text().splitlines()
        config_line = next(line for line in fsgame if line.startswith("$game_config$"))
        self.assertTrue(config_line.endswith("config\\"), config_line)
        self.assertIn("bind left kLEFT", (self.target / "_appdata_" / "user.ltx").read_text())
        self.assertEqual(setup.plan(self.install, self.home), (run, None))

    def test_linked_folder_is_not_listed_as_another_install(self):
        setup.link(self.source, self.target)
        found = detect.find_installs(self.home, extra=[str(self.source)])
        self.assertEqual([i.path for i in found], [self.source])

    def test_relinking_picks_up_new_files_and_drops_removed_ones(self):
        setup.link(self.source, self.target)
        touch(self.source / "gamedata.db1")
        (self.source / "bin" / "XR_3DA.exe").unlink()
        (self.source / "bin").rmdir()
        setup.link(self.source, self.target)
        self.assertTrue((self.target / "gamedata.db1").is_symlink())
        self.assertFalse(os.path.lexists(self.target / "bin"))

    def test_switching_source_replaces_links_but_keeps_saves(self):
        setup.link(self.source, self.target)
        touch(self.target / "_appdata_" / "savedgames" / "quick.sav")
        other = self.tmp / "steam" / "Shadow of Chernobyl"
        touch(other / "gamedata.db0", b"steam archive")
        setup.link(other, self.target)
        self.assertEqual((self.target / "gamedata.db0").read_bytes(), b"steam archive")
        self.assertFalse(os.path.lexists(self.target / "bin"))
        self.assertTrue((self.target / "_appdata_" / "savedgames" / "quick.sav").exists())

    def test_hand_made_copy_is_left_alone(self):
        touch(self.target / f"goggame-{SOC_GOG}.info")
        touch(self.target / "gamedata.db0")
        self.assertTrue(setup.is_own_copy(self.target))
        own = Install("soc", self.target.resolve(), ["OpenXRay folder"], None)
        self.assertEqual(setup.plan(own, self.home), (own, None))

    def test_real_dirs_keep_launcher_files_next_to_links(self):
        touch(self.source / "localization" / "xefis_movies.db")
        touch(self.source / "gamedata" / "scripts" / "mod.script")
        setup.link(self.source, self.target)
        touch(self.target / "localization" / "xenglish.db")  # what the language fix adds
        setup.link(self.source, self.target)
        self.assertFalse((self.target / "localization").is_symlink())
        self.assertTrue((self.target / "localization" / "xefis_movies.db").is_symlink())
        self.assertTrue((self.target / "localization" / "xenglish.db").exists())
        self.assertTrue((self.target / "gamedata" / "scripts").is_symlink())


if __name__ == "__main__":
    unittest.main()
