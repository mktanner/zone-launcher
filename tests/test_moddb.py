import hashlib
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from zone_launcher import downloads, moddb, mods
from tests.test_core import touch

# Trimmed copies of the markup ModDB uses, with made-up content.
LIST_PAGE = """
<div class="table">
<div class="row rowcontent rowreleased1 rowgenre3 clear">
  <a href="/mods/zone-overhaul" title="Zone Overhaul" class="image"><img src="https://media.moddb.com/cache/images/mods/1/2/3/crop_120x90/zo.jpg" alt="Zone Overhaul" /></a>
  <div class="content">
    <h4><a href="/mods/zone-overhaul">Zone Overhaul &amp; Friends</a></h4>
    <span class="date">
      <time datetime="2026-10-29T12:13:07+00:00">Oct 29 2026</time>  </span>
    <span class="subheading"><time datetime="2026-02-17">Released Feb 17, 2026</time> First Person Shooter</span>
    <p>Bigger, <b>better</b>
       zone.</p>  </div>
</div>
<div class="row rowcontent rowcategory117 clear">
  <a href="/games/stalker-clear-sky/addons/new-menu-music" title="New menu music" class="image"><img src="https://media.moddb.com/x.png" alt="" /></a>
  <div class="content">
    <h4><a href="/games/stalker-clear-sky/addons/new-menu-music">New menu music</a></h4>
    <span class="date"><time datetime="2026-09-15T04:09:23+00:00">Sep 14 2026</time></span>
  </div>
</div>
</div>
"""

DOWNLOADS_PAGE = """
<title>Downloads - Zone Overhaul &#45; ENGLISH mod for S.T.A.L.K.E.R.: Clear Sky - ModDB</title>
<div class="row rowcontent rowcategory4 clear">
  <a href="/mods/zone-overhaul/downloads/fix-pack-11" title="Fix Pack 1.1" class="image"><img src="https://media.moddb.com/t.png" alt="" /></a>
  <div class="content">
    <h4><a href="/mods/zone-overhaul/downloads/fix-pack-11">Zone Overhaul Fix Pack 1.1</a></h4>
    <span class="date"><time datetime="2026-10-29T12:11:06+00:00">Oct 29 2026</time></span>
    <span class="subheading">
      Patch <a href="/mods/zone-overhaul/downloads/fix-pack-11" class="commenticon">11 comments</a>  </span>
    <p>Two crash fixes &#40;save compatible&#41;.</p>
  </div>
</div>
"""

FILE_PAGE = """
<meta property="og:title" content="Zone Overhaul Fix Pack 1.1 file" />
<a href="/downloads/start/320228" class="buttondownload">Download now</a>
<div class="row clear"><h5>Filename</h5>
  <span class="summary">
    Zone_Overhaul_Fix_Pack_1.1.zip  </span></div>
<div class="row clear"><h5>Size</h5>
  <span class="summary">
    4.72kb (4,837 bytes)  </span></div>
<div class="row clear"><h5>MD5 Hash</h5>
  <span class="summary">
    255C4FF7972F74262B2F25BF88076062  </span></div>
"""


class ParseTests(unittest.TestCase):
    def test_list_page(self):
        mod, addon = moddb.parse_list(LIST_PAGE, "mods")
        self.assertEqual(mod.title, "Zone Overhaul & Friends")
        self.assertEqual(mod.url, "https://www.moddb.com/mods/zone-overhaul")
        self.assertEqual(mod.summary, "Bigger, better zone.")
        self.assertEqual(mod.updated, "2026-10-29")
        self.assertTrue(mod.image.endswith("zo.jpg"))
        self.assertEqual(addon.summary, "")
        self.assertEqual(moddb.parse_list(LIST_PAGE, "addons")[1].kind, "addon")

    def test_downloads_page(self):
        self.assertEqual(moddb.parse_mod_name(DOWNLOADS_PAGE), "Zone Overhaul - ENGLISH")
        [fix] = moddb.parse_list(DOWNLOADS_PAGE, "files")
        self.assertEqual((fix.kind, fix.category, fix.updated), ("file", "Patch", "2026-10-29"))
        self.assertEqual(fix.summary, "Two crash fixes (save compatible).")
        self.assertEqual(moddb.parse_list(LIST_PAGE, "mods")[0].category, "")  # a release date, not a category

    def test_compatibility_hints(self):
        self.assertIn("standalone", moddb.compat_hint("Anomaly-free STANDALONE overhaul"))
        self.assertIn("OGSR", moddb.compat_hint("Weapon pack for OGSR 1.0.11"))
        self.assertIn("Anomaly", moddb.compat_hint("S.T.A.L.K.E.R. Anomaly 1.5.3 weapon pack"))
        self.assertIn("GAMMA", moddb.compat_hint("Fix for STALKER GAMMA"))
        self.assertIsNone(moddb.compat_hint("Removes the anomaly detector's beeping"))
        self.assertIsNone(moddb.compat_hint("Better gamma and brightness"))
        self.assertIsNone(moddb.compat_hint("Join the Monolith faction and fight in the Zone"))
        self.assertIsNone(moddb.compat_hint("Engine sounds for vehicles"))
        self.assertIsNotNone(moddb.compat_hint("Requires the modified exes from the archive"))
        self.assertIn("Monolith", moddb.compat_hint("Modified Anomaly (monolith) engine exe"))

    def test_file_page(self):
        d = moddb.parse_details(FILE_PAGE, "https://www.moddb.com/mods/zone-overhaul/downloads/fix-pack-11")
        self.assertEqual(d.title, "Zone Overhaul Fix Pack 1.1")
        self.assertEqual(d.filename, "Zone_Overhaul_Fix_Pack_1.1.zip")
        self.assertEqual(d.size, 4837)
        self.assertEqual(d.md5, "255c4ff7972f74262b2f25bf88076062")
        self.assertEqual(d.start, "https://www.moddb.com/downloads/start/320228")
        bare = moddb.parse_details("<html></html>", "u")
        self.assertEqual((bare.filename, bare.size, bare.md5, bare.start), (None, None, None, None))

    def test_links(self):
        self.assertEqual(moddb.parse_link("https://www.moddb.com/mods/zone-overhaul/images/12"),
                         ("mod", "https://www.moddb.com/mods/zone-overhaul"))
        self.assertEqual(moddb.parse_link("moddb.com/mods/zone-overhaul/downloads/fix-pack-11/"),
                         ("file", "https://www.moddb.com/mods/zone-overhaul/downloads/fix-pack-11"))
        self.assertEqual(moddb.parse_link("https://www.moddb.com/games/stalker-clear-sky/addons/new-menu-music"),
                         ("file", "https://www.moddb.com/games/stalker-clear-sky/addons/new-menu-music"))
        self.assertIsNone(moddb.parse_link("https://example.com/mods/x"))
        self.assertIsNone(moddb.parse_link("zone overhaul"))
        self.assertEqual(moddb.all_files_url("https://www.moddb.com/mods/zone-overhaul"),
                         "https://www.moddb.com/mods/zone-overhaul/downloads")
        self.assertEqual(moddb.list_url("cs", "addons"), "https://www.moddb.com/games/stalker-clear-sky/addons")

    def test_only_allowed_pages_are_fetched(self):
        self.assertTrue(moddb.allowed("https://www.moddb.com/mods/x/downloads"))
        self.assertTrue(moddb.allowed("https://media.moddb.com/images/x.png"))
        self.assertFalse(moddb.allowed("https://www.moddb.com/games/stalker/mods?sort=rating"))
        self.assertFalse(moddb.allowed("http://www.moddb.com/mods/x"))
        self.assertFalse(moddb.allowed("https://evil.example/mods/x"))
        with self.assertRaises(moddb.ModDBError):
            moddb.fetch("https://www.moddb.com/search?q=x")

    def test_cache_and_offline_fallback(self):
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp}):
            url = "https://www.moddb.com/mods/zone-overhaul"
            response = mock.MagicMock()
            response.__enter__.return_value.read.return_value = b"page"
            with mock.patch("urllib.request.urlopen", return_value=response) as urlopen:
                self.assertEqual(moddb.fetch(url), b"page")
                self.assertEqual(moddb.fetch(url), b"page")
                self.assertEqual(urlopen.call_count, 1)
            import urllib.error
            with mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError("offline")):
                self.assertEqual(moddb.fetch(url, ttl=0), b"page")  # stale beats nothing
                with self.assertRaises(moddb.ModDBError):
                    moddb.fetch("https://www.moddb.com/mods/other")


class DownloadTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.folder = self.tmp / "Downloads"
        self.folder.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def pending(self, data: bytes | None = b"archive", **kw) -> downloads.Pending:
        args = dict(game="cs", title="Fix Pack", origin={"file": "https://www.moddb.com/f"},
                    filename="Fix_Pack.zip", folder=self.folder)
        if data is not None:
            args.update(size=len(data), md5=hashlib.md5(data).hexdigest())
        args.update(kw)
        return downloads.Pending(**args)

    def test_downloads_dir_from_user_dirs(self):
        home = self.tmp / "home"
        touch(home / ".config" / "user-dirs.dirs", b'XDG_DESKTOP_DIR="$HOME/Desktop"\n'
                                                     b'XDG_DOWNLOAD_DIR="$HOME/Telechargements"\n')
        self.assertEqual(downloads.downloads_dir(home), home / "Telechargements")
        self.assertEqual(downloads.downloads_dir(self.tmp / "other"), self.tmp / "other" / "Downloads")

    def test_waits_for_firefox_to_finish(self):
        touch(self.folder / "Fix_Pack.zip", b"old download")  # already there: not this one
        p = self.pending()
        self.assertIsNone(p.check())
        # Firefox: an empty file under the final name, the data in .part.
        touch(self.folder / "Fix_Pack(1).zip", b"")
        touch(self.folder / "Fix_Pack(1).zip.part", b"arch")
        self.assertIsNone(p.check())
        (self.folder / "Fix_Pack(1).zip.part").unlink()
        touch(self.folder / "Fix_Pack(1).zip", b"archive")
        found = p.check()
        self.assertEqual(found, self.folder / "Fix_Pack(1).zip")
        self.assertTrue(p.verify(found))
        touch(found, b"damaged")
        self.assertFalse(p.verify(found))

    def test_size_finds_a_renamed_file_and_unknown_size_waits_to_settle(self):
        p = self.pending()
        touch(self.folder / "something-else.7z", b"archive")
        self.assertEqual(p.check(), self.folder / "something-else.7z")

        q = self.pending(data=None, filename=None)
        touch(self.folder / "big.rar", b"grow")
        self.assertIsNone(q.check())  # first sight
        touch(self.folder / "big.rar", b"growing")
        self.assertIsNone(q.check())  # still growing
        self.assertEqual(q.check(), self.folder / "big.rar")

    def test_watcher(self):
        w = downloads.Watcher()
        a = self.pending()
        w.add(a)
        w.add(self.pending())  # the same download again replaces the first wait
        self.assertEqual(len(w.pending), 1)
        b = self.pending(data=b"other", filename="Other.zip", origin={"file": "g"})
        w.add(b)
        self.assertEqual(w.poll(), [])
        touch(self.folder / "Other.zip", b"other")
        self.assertEqual(w.poll(), [(b, self.folder / "Other.zip")])
        self.assertEqual(len(w.for_game("cs")), 1)
        w.cancel(w.pending[0])
        self.assertEqual(w.pending, [])

    def test_installed_mod_remembers_where_it_came_from(self):
        archive = self.folder / "Fix_Pack.zip"
        with zipfile.ZipFile(archive, "w") as z:
            z.writestr("Fix Pack/gamedata/scripts/fix.script", b"fix")
        with mock.patch.dict(os.environ, {"XDG_DATA_HOME": str(self.tmp / "data")}):
            origin = {"site": "ModDB", "mod": "Zone Overhaul", "page": "https://www.moddb.com/mods/zone-overhaul",
                      "file": "https://www.moddb.com/mods/zone-overhaul/downloads/fix-pack-11"}
            mods.install("cs", archive, name="Fix Pack 1.1", origin=origin)
            [mod] = mods.list_mods("cs")
            self.assertEqual((mod.size, mods.disk_size("cs", mod)), (3, 3))  # the script
            self.assertEqual(mods.downloaded_archive(mod, self.tmp / "elsewhere"), archive)
            archive.rename(self.folder / "moved.zip")
            self.assertIsNone(mods.downloaded_archive(mod, self.folder))
            # Mods installed before the archive's path was recorded: look in Downloads by name.
            mod.archive_path, mod.archive = "", "moved.zip"
            self.assertEqual(mods.downloaded_archive(mod, self.folder), self.folder / "moved.zip")
        self.assertEqual(mod.name, "Fix Pack 1.1")
        self.assertEqual(mod.source, origin)


if __name__ == "__main__":
    unittest.main()
