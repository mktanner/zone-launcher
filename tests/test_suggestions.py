import tempfile
import unittest
from pathlib import Path
from unittest import mock

from zone_launcher import downloads, mods, moddb, standalone, suggestions
from tests.test_core import touch


def item(title: str, updated: str, category: str = "Full Version") -> moddb.Item:
    return moddb.Item(title, f"https://www.moddb.com/x/{title}", "", "", updated, "file", category)


def suggestion(name: str) -> suggestions.Suggestion:
    return next(s for s in suggestions.SUGGESTIONS if s.name == name)


class SuggestionTests(unittest.TestCase):
    def test_newest_full_version_is_chosen(self):
        files = [item("STALKER Complete 2009 1.4.4 [archieved]", "2009-09-24"),
                 item("Zone Reclamation Project ZRP 1.09 XR3a", "2026-06-22"),
                 item("STALKER Complete 1.5.4.5", "2026-10-09"),
                 item("STALKER Complete 1.5.4.4", "2026-01-01"),
                 item("STALKER Complete 1.6", "2026-11-01", "Patch")]
        self.assertEqual(suggestions.latest_full_version(suggestion("STALKER Complete"), files).title,
                         "STALKER Complete 1.5.4.5")

    def test_other_mods_full_versions(self):
        for name, title in (("Anomaly", "STALKER Anomaly 1.5.3"), ("Radiophobia 3", "Radiophobia 3 ver. 1.20"),
                            ("Call of Chernobyl", "[Release] Call of Chernobyl 1.4.12")):
            self.assertEqual(suggestions.latest_full_version(suggestion(name), [item(title, "2026-01-01")]).title, title)

    def test_nothing_to_download(self):
        self.assertIsNone(suggestions.latest_full_version(suggestion("Anomaly"), [item("Translation tool", "2022-12-07")]))
        self.assertIsNone(suggestions.latest_full_version(suggestion("G.A.M.M.A."), [item("G.A.M.M.A.", "2022-12-07")]))

    def test_every_suggestion_can_be_acted_on(self):
        for s in suggestions.SUGGESTIONS:
            self.assertTrue(s.full_version or s.guide, s.name)

    def test_added_goes_by_the_folders_name(self):
        anomaly = suggestion("Anomaly")
        self.assertTrue(suggestions.added(anomaly, ["/x/STALKER_Complete_1.5.4.5", "/home/me/Anomaly-1.5.3-Full.2"]))
        # Not by the folders it's in.
        self.assertFalse(suggestions.added(anomaly, ["/mnt/anomaly/STALKER_Complete_1.5.4.5"]))
        self.assertFalse(suggestions.added(anomaly, []))


class UnpackTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.games = self.tmp / "Games"

    def unpack(self, *files: str, name: str = "Mod-1.0.7z") -> Path:
        def extract(_archive, dest):
            dest.mkdir(parents=True, exist_ok=True)
            for file in files:
                touch(dest / file)

        with mock.patch.object(mods, "extract", extract):
            return standalone.unpack(self.tmp / name, self.games)

    def leftovers(self) -> list[str]:
        return [p.name for p in self.games.iterdir()]

    def test_archive_with_its_own_folder(self):
        self.assertEqual(self.unpack("Mod 1.0/fsgame.ltx", "Mod 1.0/bin/game.exe"), self.games / "Mod 1.0")
        self.assertTrue((self.games / "Mod 1.0" / "bin" / "game.exe").is_file())
        self.assertEqual(self.leftovers(), ["Mod 1.0"])

    def test_archive_without_a_folder_is_named_after_itself(self):
        self.assertEqual(self.unpack("fsgame.ltx", "bin/game.exe"), self.games / "Mod-1.0")

    def test_archive_with_folders_around_it(self):
        target = self.unpack("wrap/Mod/fsgame.ltx", "wrap/Mod/bin/game.exe", name="x.7z")
        self.assertEqual(target, self.games / "Mod")

    def test_readme_beside_the_mod(self):
        self.assertEqual(self.unpack("readme.txt", "Mod/fsgame.ltx", "Mod/bin/game.exe"), self.games / "Mod")

    def test_not_a_game_leaves_nothing(self):
        with self.assertRaises(mods.ModError):
            self.unpack("readme.txt", "textures/a.dds")
        self.assertEqual(self.leftovers(), [])

    def test_an_existing_folder_is_left_alone(self):
        touch(self.games / "Mod" / "my save")
        with self.assertRaises(mods.ModError):
            self.unpack("Mod/fsgame.ltx", "Mod/bin/game.exe")
        self.assertEqual(self.leftovers(), ["Mod"])
        self.assertTrue((self.games / "Mod" / "my save").is_file())

    def test_free_space_of_a_folder_not_made_yet(self):
        self.assertGreater(standalone.free_space(self.tmp / "not" / "yet"), 0)


class AlreadyDownloadedTests(unittest.TestCase):
    def test_only_the_whole_file_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "mod.7z").write_bytes(b"12345")
            self.assertEqual(downloads.already_downloaded("mod.7z", 5, folder), folder / "mod.7z")
            self.assertIsNone(downloads.already_downloaded("mod.7z", 6, folder))
            self.assertIsNone(downloads.already_downloaded("other.7z", 5, folder))
            self.assertIsNone(downloads.already_downloaded(None, 5, folder))
            self.assertIsNone(downloads.already_downloaded("mod.7z", None, folder))


if __name__ == "__main__":
    unittest.main()
