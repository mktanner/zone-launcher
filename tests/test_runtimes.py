import hashlib
import io
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from zone_launcher import mods, runtimes, standalone


class Response(io.BytesIO):
    def __init__(self, body: bytes):
        super().__init__(body)
        self.headers = {"Content-Length": str(len(body))}


def proton_archive(name: str) -> bytes:
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode="w:xz") as tar:
        for rel, body in ((f"{name}/proton", b"#!/usr/bin/env python3\n"), (f"{name}/version", b"1\n")):
            info = tarfile.TarInfo(rel)
            info.size, info.mode = len(body), 0o755
            tar.addfile(info, io.BytesIO(body))
    return out.getvalue()


class RuntimesTests(unittest.TestCase):
    NAME = "proton-cachyos-11.0-1-slr-x86_64"

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        patcher = mock.patch.object(Path, "home", return_value=self.home)
        patcher.start()
        self.addCleanup(patcher.stop)

    def release(self) -> runtimes.ProtonRelease:
        return runtimes.ProtonRelease(self.NAME, "https://x/proton", "https://x/sum", 1)

    def serve(self, files: dict[str, bytes]):
        patcher = mock.patch.object(runtimes, "_open", lambda url, timeout=30: Response(files[url]))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_the_plain_x86_64_build_is_chosen(self):
        def asset(name, size=5):
            return {"name": name, "browser_download_url": f"https://x/{name}", "size": size}

        name = "proton-cachyos-11.0-1-slr"
        release = runtimes.parse_proton_release({"assets": [
            asset(f"{name}-arm64.tar.xz"), asset(f"{name}-arm64.sha512sum"),
            asset(f"{name}-x86_64_v3.tar.xz"), asset(f"{name}-x86_64_v3.sha512sum"),
            asset(f"{name}-x86_64.sha512sum"), asset(f"{name}-x86_64.tar.xz", 347)]})
        self.assertEqual(release, runtimes.ProtonRelease(f"{name}-x86_64", f"https://x/{name}-x86_64.tar.xz",
                                                         f"https://x/{name}-x86_64.sha512sum", 347))

    def test_a_release_without_a_build_is_refused(self):
        with self.assertRaises(runtimes.GetError):
            runtimes.parse_proton_release({"assets": []})

    def test_proton_is_installed_and_then_used(self):
        body = proton_archive(self.NAME)
        self.serve({"https://x/proton": body,
                    "https://x/sum": f"{hashlib.sha512(body).hexdigest()}  {self.NAME}.tar.xz\n".encode()})
        said = []
        target = runtimes.install_proton(self.release(), said.append)
        self.assertEqual(target, self.home / runtimes.PROTON_FOLDER / self.NAME)
        self.assertEqual([p.name for p in target.parent.iterdir()], [self.NAME])
        self.assertTrue((target / "proton").stat().st_mode & 0o100)
        self.assertIn("Downloading: 100%", said[-2])
        self.assertEqual(standalone.best_runner().name, self.NAME)

    def test_a_damaged_download_leaves_nothing(self):
        self.serve({"https://x/proton": proton_archive(self.NAME), "https://x/sum": b"00  x\n"})
        with self.assertRaises(runtimes.GetError):
            runtimes.install_proton(self.release(), lambda _text: None)
        self.assertEqual(list((self.home / runtimes.PROTON_FOLDER).iterdir()), [])

    def test_an_archive_without_proton_is_refused(self):
        body = proton_archive("something-else")
        self.serve({"https://x/proton": body, "https://x/sum": hashlib.sha512(body).hexdigest().encode()})
        with self.assertRaises(runtimes.GetError):
            runtimes.install_proton(self.release(), lambda _text: None)
        self.assertEqual(list((self.home / runtimes.PROTON_FOLDER).iterdir()), [])

    def cabinet(self, body: bytes = b"data") -> bytes:
        total = 36 + len(body)
        return (b"MSCF\0\0\0\0" + total.to_bytes(4, "little") + bytes(12) + b"\x03\x01" + bytes(10) + body)

    def test_the_cabinet_is_found_inside_the_installer(self):
        cabinet = self.cabinet()
        # A stray signature in the program part isn't one.
        program = b"MZ" + b"MSCF\0\0\0\0" + bytes(300000)
        self.assertEqual(runtimes.embedded_cabinet(program + cabinet + b"overlay"), cabinet)
        with self.assertRaises(runtimes.GetError):
            runtimes.embedded_cabinet(program)

    def test_directx_is_installed_and_then_found(self):
        installer = b"MZ" + bytes(1000) + self.cabinet()
        self.serve({runtimes.DIRECTX_URL: installer})

        def extract(_archive, dest):
            (dest / "sub").mkdir(parents=True)
            (dest / "sub" / "Mar2008_d3dx9_37_x86.cab").write_bytes(b"cab")
            (dest / "DXSETUP.exe").write_bytes(b"not kept")

        with mock.patch.object(runtimes, "DIRECTX_SHA256", hashlib.sha256(installer).hexdigest()), \
                mock.patch.object(mods, "extract", extract):
            target = runtimes.install_directx(lambda _text: None)
        self.assertEqual([p.name for p in target.iterdir()], ["Mar2008_d3dx9_37_x86.cab"])
        self.assertIn(target, standalone.redist_folders())
        self.assertEqual([c.name for c in standalone.redist_cabs({"d3dx9_37.dll"})["d3dx9_37.dll"]],
                         ["Mar2008_d3dx9_37_x86.cab"])
        self.assertEqual([p.name for p in target.parent.iterdir()], ["directx"])

    def test_a_damaged_directx_download_leaves_nothing(self):
        self.serve({runtimes.DIRECTX_URL: b"something else"})
        with self.assertRaises(runtimes.GetError):
            runtimes.install_directx(lambda _text: None)
        self.assertEqual(list((self.home / runtimes.DIRECTX_FOLDER).parent.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
