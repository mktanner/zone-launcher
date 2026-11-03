import struct
import tempfile
import unittest
from pathlib import Path

from zone_launcher import pe

TEXT_RVA = 0x1000
TEXT_RAW = 0x400


def write_program(path: Path, imports: list[str] | None = None, bits: int = 32, dotnet: bool = False) -> None:
    """A minimal Windows program header: just enough for pe.read."""
    imports = imports or []
    optional_size = 224 if bits == 32 else 240
    pe_at = 0x80
    table = bytearray()
    names = bytearray()
    names_at = TEXT_RVA + 20 * (len(imports) + 1)
    for name in imports:
        table += struct.pack("<IIIII", 0, 0, 0, names_at + len(names), 0)
        names += name.encode() + b"\0"
    table += bytes(20)
    body = bytes(table + names)
    optional = bytearray(optional_size)
    struct.pack_into("<H", optional, 0, 0x10B if bits == 32 else 0x20B)
    directories = 96 if bits == 32 else 112
    if imports:
        struct.pack_into("<I", optional, directories + 8, TEXT_RVA)
    if dotnet:
        struct.pack_into("<I", optional, directories + 14 * 8, TEXT_RVA)
    section = struct.pack("<8sIIII", b".text", max(len(body), 1), TEXT_RVA, max(len(body), 1), TEXT_RAW) + bytes(16)
    header = bytearray(pe_at)
    header[:2] = b"MZ"
    struct.pack_into("<I", header, 0x3C, pe_at)
    header += b"PE\0\0" + struct.pack("<HHIIIHH", 0x14C, 1, 0, 0, 0, optional_size, 0) + optional + section
    data = bytes(header).ljust(TEXT_RAW, b"\0") + body
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


class PeTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def test_32_bit_with_imports(self):
        write_program(self.tmp / "a.exe", imports=["xrCore.dll", "KERNEL32.dll"])
        self.assertEqual(pe.read(self.tmp / "a.exe"), pe.Program(32, False, ("xrCore.dll", "KERNEL32.dll")))

    def test_64_bit_dotnet(self):
        write_program(self.tmp / "b.exe", imports=["mscoree.dll"], bits=64, dotnet=True)
        program = pe.read(self.tmp / "b.exe")
        self.assertEqual((program.bits, program.dotnet, program.imports), (64, True, ("mscoree.dll",)))

    def test_no_imports(self):
        write_program(self.tmp / "c.exe")
        self.assertEqual(pe.read(self.tmp / "c.exe").imports, ())

    def test_not_a_program(self):
        (self.tmp / "text.exe").write_text("just text")
        (self.tmp / "short.exe").write_bytes(b"MZ")
        (self.tmp / "other.exe").write_bytes(b"MZ" + bytes(0x3A) + struct.pack("<I", 0x40) + b"XX\0\0")
        for name in ("text.exe", "short.exe", "other.exe", "missing.exe"):
            self.assertIsNone(pe.read(self.tmp / name), name)


if __name__ == "__main__":
    unittest.main()
