import base64
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from zone_launcher import APP_ID, launcher_icon

PNG = b"\x89PNG\r\n\x1a\nfake"

GEAR_LEVER = """[Desktop Entry]
Name=Zone Launcher
Exec="/home/u/AppImages/Zone Launcher.AppImage" %U
Icon=/home/u/.local/share/icons/zone_launcher.png
Type=Application
"""

LEGACY = f"""[Desktop Entry]
Name=Zone Launcher
Exec=flatpak run {APP_ID}
Icon=/home/u/.local/share/zone-launcher/icons/openxray_cs.png
{launcher_icon.MARKER}=true
"""


class LauncherIconTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.share = Path(self._tmp.name) / ".local" / "share"
        patch = mock.patch.object(launcher_icon, "share_dir", lambda: self.share)
        patch.start()
        self.addCleanup(patch.stop)
        env = mock.patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("APPIMAGE", None)
        self.apps = self.share / "applications"
        self.svg = self.share / "icons" / "hicolor" / "scalable" / "apps" / f"{APP_ID}.svg"

    def test_icon_replaced_and_restored(self):
        launcher_icon.set_icon(PNG, "cs")
        text = self.svg.read_text()
        self.assertIn(base64.b64encode(PNG).decode(), text)
        self.assertTrue(launcher_icon.has_override())
        self.assertFalse(self.apps.exists())

        launcher_icon.restore()
        self.assertFalse(self.svg.exists())
        self.assertFalse(launcher_icon.has_override())

    def test_restore_leaves_an_icon_it_did_not_write(self):
        self.svg.parent.mkdir(parents=True)
        self.svg.write_text("<svg/>")
        self.assertFalse(launcher_icon.has_override())
        launcher_icon.restore()
        self.assertTrue(self.svg.exists())

    def test_legacy_entry_removed(self):
        self.apps.mkdir(parents=True)
        legacy = self.apps / f"{APP_ID}.desktop"
        legacy.write_text(LEGACY)
        launcher_icon.set_icon(PNG, "cs")
        self.assertFalse(legacy.exists())

        # One the user made themselves stays.
        legacy.write_text(LEGACY.replace(f"{launcher_icon.MARKER}=true\n", ""))
        launcher_icon.restore()
        self.assertTrue(legacy.exists())

    def test_appimage_tool_entry_changed_and_restored(self):
        self.apps.mkdir(parents=True)
        other = self.apps / "gearlever_zone_launcher.desktop"
        other.write_text(GEAR_LEVER)
        unrelated = self.apps / "other.desktop"
        unrelated.write_text("[Desktop Entry]\nExec=other\nIcon=other\n")
        with mock.patch.dict(os.environ, {"APPIMAGE": "/home/u/AppImages/Zone Launcher.AppImage"}):
            launcher_icon.set_icon(PNG, "cop")
            # Choosing again keeps the tool's first icon to restore.
            launcher_icon.set_icon(PNG, "cs-mono")
        png = self.share / "zone-launcher" / "icons" / "cs-mono.png"
        self.assertEqual(png.read_bytes(), PNG)
        text = other.read_text()
        self.assertIn(f"Icon={png}\n", text)
        self.assertIn(f"{launcher_icon.ORIGINAL}=/home/u/.local/share/icons/zone_launcher.png\n", text)

        launcher_icon.restore()
        self.assertEqual(other.read_text(), GEAR_LEVER)
        self.assertEqual(unrelated.read_text(), "[Desktop Entry]\nExec=other\nIcon=other\n")

    def test_with_key_stays_in_desktop_entry_group(self):
        text = "[Desktop Entry]\nName=x\n\n[Desktop Action Play]\nExec=play\n"
        out = launcher_icon.with_key(launcher_icon.with_key(text, "Exec", "run"), "Name", "y")
        entry, action = out.split("[Desktop Action Play]")
        self.assertIn("Exec=run", entry)
        self.assertIn("Name=y", entry)
        self.assertIn("Exec=play", action)


if __name__ == "__main__":
    unittest.main()
