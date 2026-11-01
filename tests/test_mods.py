import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from zone_launcher import mods, setup
from zone_launcher.detect import Install
from tests.test_core import touch

CS_GOG = "1207660603"


def target_of(link: Path) -> str:
    return os.readlink(link)


class ModTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        env = mock.patch.dict(os.environ, {"XDG_DATA_HOME": str(self.tmp / "data")})
        env.start()
        self.addCleanup(env.stop)

        # The original install, as Heroic leaves it.
        self.source = self.tmp / "ssd" / "Clear Sky"
        touch(self.source / f"goggame-{CS_GOG}.info")
        touch(self.source / "resources" / "resources.db0")
        touch(self.source / "patches" / "xpatch_10.db")
        touch(self.source / "gamedata" / "configs" / "system.ltx", b"original")
        touch(self.source / "gamedata" / "configs" / "game.ltx", b"original game")
        touch(self.source / "gamedata" / "shaders" / "r2" / "a.s")
        self.target = self.tmp / "openxray" / "Clear Sky"
        setup.link(self.source, self.target)
        (self.target / "gamedata" / "shaders" / "gl").symlink_to("/engine/shaders/gl")
        self.install = Install("cs", self.target, ["test"], None)

    def tearDown(self):
        self._tmp.cleanup()

    def make_mod(self, name: str, files: dict[str, bytes]) -> Path:
        """A zip with everything inside a wrapper folder, like most mod downloads."""
        archive = self.tmp / "downloads" / f"{name}.zip"
        archive.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive, "w") as z:
            for rel, data in files.items():
                z.writestr(f"{name}/{rel}", data)
        return archive

    def abr_like(self) -> mods.Mod:
        return mods.install("cs", self.make_mod("ABR", {
            "gamedata/configs/system.ltx": b"modded",
            "gamedata/scripts/new.script": b"new",
            "patches/xpatch_11.db": b"db11",
            "!addons/bigger cells/patches/xpatch_12.db": b"db12",
            "bin/xrGame.dll": b"windows",
            "read me/FAQ.txt": b"faq",
        }))

    def test_install_finds_main_part_options_and_warns_about_bin(self):
        mod = self.abr_like()
        self.assertEqual([c.path for c in mod.components], ["", "!addons/bigger cells"])
        self.assertEqual(mod.enabled, [""])
        self.assertTrue(mod.components[0].gamedata)
        self.assertEqual(mod.components[0].archives, ["patches/xpatch_11.db"])
        self.assertTrue(any("bin/" in w for w in mod.warnings))
        self.assertEqual([m.id for m in mods.list_mods("cs")], [mod.id])

    def test_compose_overlays_mod_and_disabling_restores_the_game(self):
        mod = self.abr_like()
        mods.compose(self.install)
        gd = self.target / "gamedata"
        self.assertEqual((gd / "configs" / "system.ltx").read_bytes(), b"modded")
        self.assertEqual((gd / "configs" / "game.ltx").read_bytes(), b"original game")
        self.assertEqual((gd / "scripts" / "new.script").read_bytes(), b"new")
        self.assertTrue((gd / "shaders" / "gl").is_symlink())
        self.assertEqual((gd / "shaders" / "r2" / "a.s").exists(), True)
        patches = sorted(p.name for p in (self.target / "patches").iterdir())
        self.assertEqual(patches, ["xpatch_10.db", f"zz_mod_000_{mod.id}_xpatch_11.db"])

        mods.set_enabled("cs", mod.id, ["", "!addons/bigger cells"])
        mods.compose(self.install)
        self.assertEqual(len(list((self.target / "patches").iterdir())), 3)

        mods.set_enabled("cs", mod.id, [])
        mods.compose(self.install)
        self.assertEqual((gd / "configs" / "system.ltx").read_bytes(), b"original")
        self.assertFalse(os.path.lexists(gd / "scripts"))
        self.assertEqual([p.name for p in (self.target / "patches").iterdir()], ["xpatch_10.db"])
        self.assertTrue((gd / "shaders" / "gl").is_symlink())
        # Nothing in the original install was touched.
        self.assertEqual((self.source / "gamedata" / "configs" / "system.ltx").read_bytes(), b"original")

    def test_load_order_decides_conflicts(self):
        a = mods.install("cs", self.make_mod("A", {"gamedata/configs/system.ltx": b"A"}))
        b = mods.install("cs", self.make_mod("B", {"gamedata/configs/system.ltx": b"B"}))
        mods.compose(self.install)
        system = self.target / "gamedata" / "configs" / "system.ltx"
        self.assertEqual(system.read_bytes(), b"B")  # installed later = higher priority
        self.assertEqual(mods.conflicts("cs"), {b.id: {a.id: 1}})

        mods.move("cs", a.id, +1)
        mods.compose(self.install)
        self.assertEqual(system.read_bytes(), b"A")
        self.assertEqual([m.id for m in mods.list_mods("cs")], [b.id, a.id])

    def test_mods_need_a_launcher_managed_folder(self):
        self.abr_like()
        (self.target / setup.MARKER).unlink()
        with self.assertRaises(mods.ModError):
            mods.compose(self.install)

    def test_real_files_in_gamedata_stop_compose(self):
        self.abr_like()
        # gamedata/ itself is a real folder; its subfolders are links into the original install.
        touch(self.target / "gamedata" / "mine.ltx")
        with self.assertRaises(mods.ModError):
            mods.compose(self.install)

    def test_archive_without_game_content_is_rejected(self):
        with self.assertRaises(mods.ModError):
            mods.install("cs", self.make_mod("Wallpapers", {"pictures/a.jpg": b"x"}))
        self.assertEqual(mods.list_mods("cs"), [])

    def test_bsdtar_is_tried_before_7zip(self):
        tools = {"7z": "/bin/7z", "bsdtar": "/bin/bsdtar"}
        with mock.patch("shutil.which", side_effect=tools.get):
            commands = mods._unpack_commands(Path("a.7z"), Path("out"))
        self.assertEqual([c[0] for c in commands], ["/bin/bsdtar", "/bin/7z"])
        with mock.patch("shutil.which", side_effect={"7z": "/bin/7z"}.get):
            self.assertEqual([c[0] for c in mods._unpack_commands(Path("a.rar"), Path("out"))], ["/bin/7z"])

    def test_remove_deletes_the_mod_and_compose_cleans_up(self):
        mod = self.abr_like()
        mods.compose(self.install)
        mods.remove("cs", mod.id)
        mods.compose(self.install)
        self.assertFalse((mods.library_dir("cs") / mod.id).exists())
        self.assertEqual((self.target / "gamedata" / "configs" / "system.ltx").read_bytes(), b"original")


if __name__ == "__main__":
    unittest.main()
