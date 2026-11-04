import struct
import tempfile
import unittest
import zlib
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


def write_icon_program(path: Path, images: list[tuple[int, int, bytes]]) -> None:
    """A program with one icon: (width, bits, image) for each of its sizes."""
    rva, raw = 0x1000, 0x400
    def directory(entries):  # [(id, offset of what it points to, is it a directory)]
        return struct.pack("<IIHHHH", 0, 0, 0, 0, 0, len(entries)) + b"".join(
            struct.pack("<II", number, offset | (0x80000000 if inner else 0)) for number, offset, inner in entries)
    group = struct.pack("<HHH", 0, 1, len(images)) + b"".join(
        struct.pack("<BBBBHHIH", width % 256, width % 256, 0, 0, 1, bits, len(image), i + 1)
        for i, (width, bits, image) in enumerate(images))
    blobs = [group, *[image for _w, _b, image in images]]
    # root, then for each kind: names, languages, data entries.
    names = [(14, [1], [0]), (3, [i + 1 for i in range(len(images))], [i + 1 for i in range(len(images))])]
    tree_size = 16 + 8 * 2
    place = tree_size
    kinds, languages, entries = [], [], []
    for kind, numbers, blob_indexes in names:
        kinds.append((kind, place))
        place += 16 + 8 * len(numbers)
    for kind, numbers, blob_indexes in names:
        for _ in numbers:
            languages.append(place)
            place += 16 + 8
    for _ in blobs:
        entries.append(place)
        place += 16
    tree = directory([(kind, offset, True) for kind, offset in kinds])
    i = 0
    for kind, numbers, blob_indexes in names:
        tree += directory([(number, languages[i + j], True) for j, number in enumerate(numbers)])
        i += len(numbers)
    i = 0
    for kind, numbers, blob_indexes in names:
        for blob_index in blob_indexes:
            tree += directory([(1033, entries[blob_index], False)])
    offset = place
    table = b""
    body = b""
    for blob in blobs:
        table += struct.pack("<IIII", rva + offset + len(body), len(blob), 0, 0)
        body += blob
    section = tree + table + body
    pe = 0x80
    optional_size = 224
    data = bytearray(raw + len(section))
    data[:2] = b"MZ"
    struct.pack_into("<I", data, 0x3C, pe)
    data[pe:pe + 4] = b"PE\0\0"
    struct.pack_into("<H", data, pe + 6, 1)
    struct.pack_into("<H", data, pe + 20, optional_size)
    struct.pack_into("<H", data, pe + 24, 0x10B)
    struct.pack_into("<I", data, pe + 24 + 96 + 2 * 8, rva)
    struct.pack_into("<IIII", data, pe + 24 + optional_size + 8, len(section), rva, len(section), raw)
    data[raw:] = section
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(data))


def bitmap(width: int, bits: int, pixels: bytes, palette: bytes = b"", mask: bytes | None = None) -> bytes:
    colors = len(palette) // 4
    header = struct.pack("<IiiHHIIiiII", 40, width, width * 2, 1, bits, 0, 0, 0, 0, colors, 0)
    return header + palette + pixels + (mask if mask is not None else bytes(4 * width))


def decode_png(data: bytes) -> tuple[int, int, bytes]:
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    width, height = struct.unpack(">II", data[16:24])
    start = data.index(b"IDAT") + 4
    length = struct.unpack(">I", data[start - 8:start - 4])[0]
    raw = zlib.decompress(data[start:start + length])
    return width, height, b"".join(raw[y * (width * 4 + 1) + 1:(y + 1) * (width * 4 + 1)] for y in range(height))


class IconTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "game.exe"

    def test_a_program_without_one_has_none(self):
        self.path.write_bytes(b"not a program")
        self.assertIsNone(pe.icon(self.path))
        self.assertIsNone(pe.icon(self.path.parent / "missing.exe"))

    def test_the_largest_image_with_the_most_colors_is_used(self):
        small = bitmap(1, 32, bytes((1, 2, 3, 255)))
        fewer = bitmap(2, 8, bytes(4 * 2), palette=bytes((9, 9, 9, 0)) * 2)
        best = bitmap(2, 32, bytes((10, 20, 30, 255)) * 4)
        write_icon_program(self.path, [(1, 32, small), (2, 8, fewer), (2, 32, best)])
        width, height, rgba = decode_png(pe.icon(self.path))
        self.assertEqual((width, height), (2, 2))
        self.assertEqual(rgba[:4], bytes((30, 20, 10, 255)))

    def test_a_png_image_is_kept_as_it_is(self):
        png = b"\x89PNG\r\n\x1a\n" + b"made up"
        write_icon_program(self.path, [(0, 32, png)])
        self.assertEqual(pe.icon(self.path), png)

    def test_rows_are_turned_right_way_up(self):
        # Bitmaps start with the bottom row.
        image = bitmap(2, 32, bytes((0, 0, 1, 255)) * 2 + bytes((0, 0, 2, 255)) * 2)
        write_icon_program(self.path, [(2, 32, image)])
        self.assertEqual(decode_png(pe.icon(self.path))[2], bytes((2, 0, 0, 255)) * 2 + bytes((1, 0, 0, 255)) * 2)

    def test_old_icons_use_their_palette_and_mask(self):
        # 2 x 2, 4 bits: bottom row first. The mask makes the bottom left pixel see-through.
        palette = bytes((0, 0, 200, 0)) + bytes((0, 200, 0, 0))
        pixels = bytes((0x01, 0, 0, 0)) + bytes((0x10, 0, 0, 0))
        mask = bytes((0x80, 0, 0, 0)) + bytes((0, 0, 0, 0))
        write_icon_program(self.path, [(2, 4, bitmap(2, 4, pixels, palette, mask))])
        _w, _h, rgba = decode_png(pe.icon(self.path))
        self.assertEqual(rgba[0:4], bytes((0, 200, 0, 255)))  # top left: color 1
        self.assertEqual(rgba[4:8], bytes((200, 0, 0, 255)))  # top right: color 0
        self.assertEqual(rgba[8:12], bytes((200, 0, 0, 0)))  # bottom left: masked
        self.assertEqual(rgba[12:16], bytes((0, 200, 0, 255)))  # bottom right: color 1


if __name__ == "__main__":
    unittest.main()
