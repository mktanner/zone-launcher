import json
import os
import tempfile
import unittest
from pathlib import Path

from zone_launcher import detect, engine, fixes
from zone_launcher.detect import Install
from zone_launcher.engine import Engine

FIXTURES = Path(__file__).parent / "fixtures"
CS_GOG = "1207660603"


def touch(path: Path, data: bytes = b"x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def make_cs(root: Path) -> Path:
    touch(root / f"goggame-{CS_GOG}.info")
    touch(root / "resources" / "resources.db0")
    touch(root / "patches" / "xpatch_10.db")
    (root / "localization").mkdir(parents=True)
    touch(root / "languages" / "Fr" / "xfrench.db")
    return root


class TempCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        share = self.tmp / "engine" / "share" / "openxray"
        touch(share / "fsgame.ltx", b"$app_data_root$ = true| false| $fs_root$| _appdata_\\\n")
        (share / "gamedata" / "shaders" / "gl").mkdir(parents=True)
        self.engine = Engine(self.tmp / "engine" / "bin" / "xr_3da", share)

    def tearDown(self):
        self._tmp.cleanup()

    def ids(self, install):
        return [i.id for i in fixes.check(install, self.engine)]


class IdentifyTests(TempCase):
    def test_each_game(self):
        soc = self.tmp / "soc"
        touch(soc / "gamedata.db0")
        cop = self.tmp / "cop"
        touch(cop / "resources" / "resources.db0")
        touch(cop / "resources" / "resources.db3")
        cs = self.tmp / "cs"
        touch(cs / "resources" / "resources.db0")
        touch(cs / "patches" / "xpatch_10.db")
        self.assertEqual([detect.identify(p) for p in (soc, cs, cop)], ["soc", "cs", "cop"])
        self.assertIsNone(detect.identify(self.tmp))


class FindInstallsTests(TempCase):
    def test_heroic_flatpak_install_with_support_files_and_dedupe(self):
        home = self.tmp / "home"
        game = make_cs(self.tmp / "ssd" / "Clear Sky")
        heroic = home / ".var/app/com.heroicgameslauncher.hgl/config/heroic"
        touch(heroic / "gog_store/installed.json",
              json.dumps({"installed": [{"appName": CS_GOG, "install_path": str(game)}]}).encode())
        support = heroic / f"gogdlConfig/heroic_gogdl/gog-support/{CS_GOG}/{CS_GOG}"
        touch(support / "add" / "xenglish.db")
        # Empty engine leftovers in the OpenXRay folder must not show up as an install.
        (home / ".local/share/GSC Game World/S.T.A.L.K.E.R. - Shadow of Chernobyl").mkdir(parents=True)

        found = detect.find_installs(home, extra=[str(game)])
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].game, "cs")
        self.assertEqual(found[0].sources, ["Heroic", "Added by you"])
        self.assertEqual(found[0].gog_support, support)


class FixTests(TempCase):
    def install(self, gog_support=None):
        return Install("cs", make_cs(self.tmp / "game"), ["test"], gog_support)

    def apply_all(self, install):
        for issue in fixes.check(install, self.engine):
            if issue.fix:
                issue.fix()

    def test_clear_sky_bug_is_found_and_fixed_from_gog_support(self):
        support = self.tmp / "support"
        touch(support / "add" / "xenglish.db", b"english")
        install = self.install(support)
        touch(install.path / "localization" / "xefis_movies.db")  # not a language pack

        self.assertEqual(self.ids(install), ["fsgame", "shaders", "language"])
        self.apply_all(install)
        self.assertEqual(self.ids(install), [])
        self.assertEqual((install.path / "localization" / "xenglish.db").read_bytes(), b"english")
        self.assertFalse(list((install.path / "localization").glob(".*partial")))

    def test_falls_back_to_languages_folder(self):
        install = self.install()
        issue = next(i for i in fixes.check(install, self.engine) if i.id == "language")
        self.assertIn("French", issue.title)
        issue.fix()
        self.assertTrue((install.path / "localization" / "xfrench.db").exists())

    def test_no_language_pack_anywhere_needs_the_user(self):
        install = self.install()
        (install.path / "languages" / "Fr" / "xfrench.db").unlink()
        issue = next(i for i in fixes.check(install, self.engine) if i.id == "language-missing")
        self.assertIsNone(issue.fix)

    def test_original_fsgame_is_backed_up(self):
        install = self.install()
        touch(install.path / "fsgame.ltx", b"$game_data$ = false| true| $fs_root$| gamedata\\\n")
        self.apply_all(install)
        self.assertEqual((install.path / "fsgame.ltx.original").read_bytes()[:11], b"$game_data$")
        self.assertIn(b"$app_data_root$", (install.path / "fsgame.ltx").read_bytes())
        self.assertFalse((install.path / "fsgame.ltx").is_symlink())

    def test_links_into_another_engine_are_repointed(self):
        # The state a system-installed engine leaves behind, seen from inside the Flatpak.
        install = self.install()
        os.symlink("/nonexistent/openxray/fsgame.ltx", install.path / "fsgame.ltx")
        gl = install.path / "gamedata" / "shaders" / "gl"
        gl.parent.mkdir(parents=True)
        os.symlink("/nonexistent/openxray/gamedata/shaders/gl", gl)
        self.assertIn("fsgame", self.ids(install))
        self.assertIn("shaders", self.ids(install))
        self.apply_all(install)
        self.assertEqual(Path(os.readlink(gl)), self.engine.gl_shaders)

    def test_custom_shader_folder_is_left_alone(self):
        install = self.install()
        (install.path / "gamedata" / "shaders" / "gl").mkdir(parents=True)
        issue = next(i for i in fixes.check(install, self.engine) if i.id == "shaders-custom")
        self.assertFalse(issue.blocking)
        self.assertIsNone(issue.fix)

    def test_read_only_folder_stops_other_checks(self):
        install = self.install()
        install.path.chmod(0o555)
        try:
            if os.access(install.path, os.W_OK):
                self.skipTest("running as a user that ignores permissions")
            self.assertEqual(self.ids(install), ["not-writable"])
        finally:
            install.path.chmod(0o755)


class CrashSummaryTests(unittest.TestCase):
    def test_real_clear_sky_crash(self):
        summary = engine.crash_summary((FIXTURES / "cs_missing_language_crash.log").read_text())
        self.assertIn("There is no sound collection", summary)
        self.assertIn("FATAL ERROR", summary)
        self.assertNotIn("stack trace", summary)
        self.assertNotIn("phase time", summary)

    def test_clean_log(self):
        self.assertIsNone(engine.crash_summary("* phase time: 1 ms\n* End of synchronization\n"))


if __name__ == "__main__":
    unittest.main()
