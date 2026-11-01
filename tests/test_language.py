import os
import tempfile
import unittest
from pathlib import Path

from zone_launcher import display, fixes, setup
from zone_launcher.detect import Install
from zone_launcher.engine import Engine
from tests.test_core import touch

CS_GOG = "1207660603"


class LanguageTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        share = self.tmp / "engine" / "share" / "openxray"
        touch(share / "fsgame.ltx", b"$app_data_root$ = true| false| $fs_root$| _appdata_\\\n")
        (share / "gamedata" / "shaders" / "gl").mkdir(parents=True)
        self.engine = Engine(self.tmp / "engine" / "bin" / "xr_3da", share)

        # Clear Sky as GOG/Heroic installs it: other languages in languages/, English in gog-support.
        self.source = self.tmp / "ssd" / "Clear Sky"
        touch(self.source / f"goggame-{CS_GOG}.info")
        touch(self.source / "resources" / "resources.db0")
        touch(self.source / "localization" / "xefis_movies.db")
        touch(self.source / "languages" / "Fr" / "xfrench.db", b"french")
        touch(self.source / "languages" / "Ru" / "xrussian.db", b"russian")
        self.support = self.tmp / "support"
        touch(self.support / "add" / "xenglish.db", b"english")
        self.target = self.tmp / "openxray" / "Clear Sky"
        setup.link(self.source, self.target)
        self.install = Install("cs", self.target, ["Heroic"], self.support)
        self.settings = display.user_ltx(self.install)
        touch(self.settings, b"g_language eng\ng_language_ltx eng\nvid_mode 1920x1080\n")

    def tearDown(self):
        self._tmp.cleanup()

    def apply(self, language: str) -> list[fixes.Issue]:
        issues = [i for i in fixes._check_language(self.install, language)]
        for issue in issues:
            if issue.fix:
                issue.fix()
        return issues

    def active(self) -> dict[str, bool]:
        """Active pack name -> whether it's a link."""
        loc = self.target / "localization"
        return {p.name: p.is_symlink() for p in loc.iterdir() if fixes.LANGUAGE_PACK.match(p.name)}

    def test_switching_packs_back_and_forth(self):
        self.apply("english")
        self.assertEqual(self.active(), {"xenglish.db": False})  # copied from gog-support

        self.apply("french")
        self.assertEqual(self.active(), {"xfrench.db": True})  # linked from the game install
        self.assertTrue((self.target / "localization" / fixes.INACTIVE / "xenglish.db").exists())
        self.assertNotIn("g_language", self.settings.read_text())
        self.assertIn("vid_mode 1920x1080", self.settings.read_text())

        self.apply("english")
        self.assertEqual(self.active(), {"xenglish.db": False})  # back from .inactive, not re-copied
        self.assertEqual((self.target / "localization" / "xenglish.db").read_bytes(), b"english")
        self.assertEqual(fixes._check_language(self.install, "english"), [])

    def test_unavailable_language_keeps_the_current_one(self):
        self.apply("english")
        issues = self.apply("czech")
        self.assertEqual([i.id for i in issues], ["language-unavailable"])
        self.assertFalse(issues[0].blocking)
        self.assertEqual(self.active(), {"xenglish.db": False})

    def test_setup_does_not_bring_a_switched_away_pack_back(self):
        # Call of Pripyat ships its English pack inside localization/.
        touch(self.source / "localization" / "xenglish.db", b"cop english")
        setup.link(self.source, self.target)
        self.assertEqual(self.active(), {})  # language packs are left to the language check
        self.apply("russian")
        setup.link(self.source, self.target)
        self.assertEqual(self.active(), {"xrussian.db": True})

    def test_available_languages(self):
        self.assertEqual(fixes.available_languages(self.install), ["english", "french", "russian"])


class ShadowOfChernobylLanguageTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.install = Install("soc", Path(self._tmp.name), ["test"], None)
        self.settings = display.user_ltx(self.install)
        touch(self.settings, b"bind left kLEFT\n")

    def tearDown(self):
        self._tmp.cleanup()

    def test_text_language_is_set_in_user_ltx(self):
        (issue,) = fixes._check_language(self.install, "french")
        issue.fix()
        text = self.settings.read_text()
        self.assertIn("g_language fra\n", text)
        self.assertIn("g_language_ltx eng\n", text)
        self.assertEqual(fixes._check_language(self.install, "french"), [])
        self.assertEqual(fixes._check_language(self.install, "english")[0].id, "language")

    def test_english_needs_nothing_and_russian_is_unavailable(self):
        self.assertEqual(fixes._check_language(self.install, "english"), [])
        (note,) = fixes._check_language(self.install, "russian")
        self.assertEqual(note.id, "language-unavailable")
        self.assertFalse(note.blocking)


if __name__ == "__main__":
    unittest.main()
