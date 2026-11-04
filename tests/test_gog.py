import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from zone_launcher import gog, standalone
from zone_launcher.detect import Install


class GogTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.game, self.support = self.tmp / "Game", self.tmp / "support"
        self.game.mkdir()
        self.support.mkdir()
        (self.game / "goggame-1.info").write_text(json.dumps({"language": "Polish"}))
        self.install = Install("cs", self.game, ["Heroic"], self.support)

    def script(self, actions: list):
        (self.game / "goggame-1.script").write_text(json.dumps({"actions": actions, "productId": "1"}))

    def with_program(self) -> Path:
        self.script([{"install": {"action": "Execute", "arguments": {"executable": "galaxy.exe"}}}])
        (self.support / "galaxy.exe").write_bytes(b"MZ")
        return self.support / "galaxy.exe"

    def test_the_program_comes_from_gogs_script(self):
        program = self.with_program()
        self.assertEqual(gog.setup_program(self.install), program)

    def test_a_script_without_a_program_has_no_setup(self):
        self.script([{"install": {"action": "saveToFile", "arguments": {"filename": "{app}/fsgame.ltx", "text": ""}}}])
        self.assertIsNone(gog.setup_program(self.install))

    def test_a_program_not_downloaded_is_no_setup(self):
        self.script([{"install": {"action": "Execute", "arguments": {"executable": "galaxy.exe"}}}])
        self.assertIsNone(gog.setup_program(self.install))

    def test_other_stores_have_no_setup(self):
        self.with_program()
        self.assertIsNone(gog.setup_program(Install("cs", self.game, ["Steam"])))

    def scripted(self):
        self.script([
            {"install": {"action": "saveToFile", "arguments": {
                "filename": "{app}/fsgame.ltx",
                "text": "$app_data_root$ = true | false| {userdocs}\\Stalker-SHOC\n$logs$ = true | false| $app_data_root$| logs\\\n"}}},
            {"install": {"action": "execute", "arguments": {"argument": "+r fsgame.ltx", "executable": "{sys}/attrib.exe"}}},
            {"install": {"action": "supportData", "arguments": {
                "source": "{supportDir}/userdocs/user.ltx", "target": "{userdocs}/STALKER-SHOC/user.ltx", "type": "File"}}},
            {"install": {"action": "savePath", "arguments": {"savePath": "{userdocs}/STALKER-SHOC", "type": "Folder"}}},
            {"install": {"action": "setRegistry", "arguments": {"root": "HKEY_LOCAL_MACHINE", "valueData": "{app}"}}},
        ])
        (self.support / "userdocs").mkdir()
        (self.support / "userdocs" / "user.ltx").write_text("vid_mode 1024x768\n")

    def test_a_script_of_steps_is_a_setup_too(self):
        self.scripted()
        self.assertIsNone(gog.setup_program(self.install))
        self.assertTrue(gog.has_setup(self.install))
        self.assertFalse(gog.setup_done(self.install))
        self.assertFalse(gog.can_run_again(self.install))

    def test_a_folder_without_a_script_has_no_setup(self):
        self.assertFalse(gog.has_setup(self.install))
        self.assertFalse(gog.setup_done(self.install))

    def test_the_script_writes_its_files_in_the_game_folder(self):
        self.scripted()
        with mock.patch.object(standalone, "run_program") as run_program:
            gog.run_setup(self.install)
        run_program.assert_not_called()
        self.assertEqual((self.game / "fsgame.ltx").read_text().splitlines()[0],
                         "$app_data_root$ = true | false| $fs_root$| userdocs\\Stalker-SHOC\\")
        self.assertEqual((self.game / "userdocs" / "STALKER-SHOC" / "user.ltx").read_text(), "vid_mode 1024x768\n")
        self.assertTrue(gog.setup_done(self.install))

    def test_the_script_leaves_what_is_already_there(self):
        self.scripted()
        (self.game / "fsgame.ltx").write_text("mine")
        settings = self.game / "userdocs" / "STALKER-SHOC" / "user.ltx"
        settings.parent.mkdir(parents=True)
        settings.write_text("my settings")
        gog.run_setup(self.install)
        self.assertEqual((self.game / "fsgame.ltx").read_text(), "mine")
        self.assertEqual(settings.read_text(), "my settings")

    def test_the_script_cant_write_outside_the_game(self):
        self.script([
            {"install": {"action": "saveToFile", "arguments": {"filename": "{app}/../outside.txt", "text": "x"}}},
            {"install": {"action": "saveToFile", "arguments": {"filename": "/tmp/zl-outside.txt", "text": "x"}}},
            {"install": {"action": "saveToFile", "arguments": {"filename": "{sys}/evil.dll", "text": "x"}}},
            {"install": {"action": "supportData", "arguments": {
                "source": "{supportDir}/../secret", "target": "{app}/secret", "type": "File"}}},
        ])
        (self.tmp / "secret").write_text("x")
        self.assertFalse(gog.has_setup(self.install))
        with self.assertRaises(OSError):
            gog.run_setup(self.install)
        self.assertFalse((self.tmp / "outside.txt").exists())
        self.assertFalse((self.game / "secret").exists())

    def test_settings_are_skipped_without_the_stores_support_files(self):
        self.scripted()
        install = Install("soc", self.game, ["Heroic"])
        gog.run_setup(install)
        self.assertTrue((self.game / "fsgame.ltx").is_file())
        self.assertFalse((self.game / "userdocs").exists())

    def test_arguments_say_where_and_in_which_language(self):
        arguments = gog.setup_arguments(self.install)
        self.assertIn("/DIR=Z:" + str(self.game).replace("/", "\\"), arguments)
        self.assertIn("/Language=Polish", arguments)
        self.assertIn("/LANG=polish", arguments)
        self.assertIn("/VERYSILENT", arguments)

    def run_setup(self, code: int = 0, writes: str = "changed"):
        self.with_program()
        process = mock.Mock()

        def wait(timeout):
            (self.game / "fsgame.ltx").write_text(writes)
            return code

        process.wait = wait
        with mock.patch.object(standalone, "run_program", return_value=process) as run, \
                mock.patch.object(standalone, "runner_problem", return_value=None), \
                mock.patch.object(standalone, "best_runner", return_value=standalone.Runner("Wine")):
            gog.run_setup(self.install)
        return run

    def test_the_file_list_stays_as_it_was(self):
        (self.game / "fsgame.ltx").write_text("the game's")
        run = self.run_setup()
        self.assertEqual((self.game / "fsgame.ltx").read_text(), "the game's")
        self.assertEqual(run.call_args.args[1], self.support / "galaxy.exe")

    def test_failing_is_reported_and_keeps_the_file_list(self):
        (self.game / "fsgame.ltx").write_text("the game's")
        with self.assertRaises(OSError):
            self.run_setup(code=3)
        self.assertEqual((self.game / "fsgame.ltx").read_text(), "the game's")

    def test_done_is_seen_from_its_log(self):
        self.assertFalse(gog.setup_done(self.install))
        (self.game / "goglog.ini").write_text("")
        self.assertTrue(gog.setup_done(self.install))


if __name__ == "__main__":
    unittest.main()
