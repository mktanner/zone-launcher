import tempfile
import unittest
from pathlib import Path

from zone_launcher import display

SAMPLE = (
    "rs_fullscreen on\r\nrs_refresh_60hz off\r\nsnd_volume_eff 1.\r\n"
    "vid_mode 2560x1440 (165Hz)\r\nvid_monitor 0. AOC Q32G2WG3\r\nvid_window_mode st_opt_fullscreen\r\n"
)


class DisplayTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "_appdata_" / "user.ltx"
        self.path.parent.mkdir()
        self.path.write_bytes(SAMPLE.encode())

    def tearDown(self):
        self._tmp.cleanup()

    def lines(self):
        return self.path.read_bytes().decode().split("\r\n")

    def test_same_resolution_keeps_refresh_rate_and_line_endings(self):
        display.apply(self.path, (2560, 1440), "fullscreen")
        self.assertEqual(self.path.read_bytes().decode(), SAMPLE)

    def test_new_resolution_drops_refresh_rate(self):
        display.apply(self.path, (1920, 1080), display.KEEP)
        self.assertIn("vid_mode 1920x1080", self.lines())
        self.assertIn("vid_window_mode st_opt_fullscreen", self.lines())

    def test_windowed_turns_off_legacy_fullscreen_flag(self):
        display.apply(self.path, None, "windowed")
        self.assertIn("vid_window_mode st_opt_windowed", self.lines())
        self.assertIn("rs_fullscreen off", self.lines())
        self.assertIn("vid_mode 2560x1440 (165Hz)", self.lines())

    def test_keep_everything_leaves_file_untouched(self):
        display.apply(self.path, None, display.KEEP)
        self.assertEqual(self.path.read_bytes().decode(), SAMPLE)

    def test_duplicates_are_collapsed(self):
        self.path.write_text("vid_mode 800x600\nfoo 1\nvid_mode 1024x768\n")
        display.apply(self.path, (1280, 720), display.KEEP)
        self.assertEqual(self.path.read_text(), "vid_mode 1280x720\nfoo 1\n")

    def test_fresh_install_without_user_ltx(self):
        self.path.unlink()
        display.apply(self.path, (2560, 1440), "fullscreen")
        self.assertEqual(
            self.path.read_text(),
            "vid_mode 2560x1440\nvid_window_mode st_opt_fullscreen\nrs_fullscreen on\n",
        )

    def test_resolution_choices_fit_the_monitor(self):
        choices = display.resolution_choices((2560, 1440))
        self.assertNotIn((2560, 1440), choices)
        self.assertNotIn((3840, 2160), choices)
        self.assertIn((1920, 1080), choices)


if __name__ == "__main__":
    unittest.main()
