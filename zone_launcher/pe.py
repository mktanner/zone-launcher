"""Reads what a Windows program says about itself, from its file header. Nothing here runs it."""

import struct
import zlib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Program:
    bits: int
    # A .NET program: it needs Wine Mono.
    dotnet: bool
    # The DLLs it loads at startup, as written in the file.
    imports: tuple[str, ...]


def _layout(data: bytes):
    """Where a program's table of directories is, and how to turn addresses into places in the file."""
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    if data[:2] != b"MZ" or data[pe:pe + 4] != b"PE\0\0":
        return None, None
    sections, optional_size = struct.unpack_from("<H", data, pe + 6)[0], struct.unpack_from("<H", data, pe + 20)[0]
    optional = pe + 24
    magic = struct.unpack_from("<H", data, optional)[0]
    if magic not in (0x10B, 0x20B):
        return None, None
    directories = optional + (96 if magic == 0x10B else 112)
    table = [struct.unpack_from("<IIII", data, optional + optional_size + i * 40 + 8) for i in range(sections)]

    def offset(rva: int) -> int | None:
        for virtual_size, address, raw_size, raw in table:
            if address <= rva < address + max(virtual_size, raw_size):
                return rva - address + raw
        return None

    return directories, offset


def _png(width: int, height: int, rgba: bytes) -> bytes:
    def chunk(name: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + name + body + struct.pack(">I", zlib.crc32(name + body))

    rows = b"".join(b"\0" + rgba[y * width * 4:(y + 1) * width * 4] for y in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


def _image_as_png(image: bytes) -> bytes | None:
    """One image of an icon, which is a PNG already or a Windows bitmap."""
    if image[:8] == b"\x89PNG\r\n\x1a\n":
        return image
    header, width, doubled, _planes, bits = struct.unpack_from("<IiiHH", image, 0)
    height = doubled // 2
    if width <= 0 or height <= 0 or bits not in (1, 4, 8, 24, 32):
        return None
    colors = struct.unpack_from("<I", image, 32)[0] or (1 << bits if bits <= 8 else 0)
    palette = header
    pixels = palette + colors * 4
    stride = (width * bits + 31) // 32 * 4
    mask = pixels + stride * height
    mask_stride = (width + 31) // 32 * 4
    has_alpha = bits == 32 and any(image[pixels + 3 + i * 4] for i in range(width * height))
    rgba = bytearray()
    for y in range(height - 1, -1, -1):  # bottom row first
        row = pixels + y * stride
        for x in range(width):
            if bits >= 24:
                b, g, r = image[row + x * bits // 8:row + x * bits // 8 + 3]
            else:
                byte = image[row + x * bits // 8]
                index = (byte >> (8 - bits - (x * bits) % 8)) & ((1 << bits) - 1)
                b, g, r = image[palette + index * 4:palette + index * 4 + 3]
            if has_alpha:
                alpha = image[row + x * 4 + 3]
            else:
                alpha = 0 if image[mask + y * mask_stride + x // 8] & (0x80 >> (x % 8)) else 255
            rgba += bytes((r, g, b, alpha))
    return _png(width, height, bytes(rgba))


def icon(path: Path) -> bytes | None:
    """A program's icon as a PNG, its largest and most colorful image, or None if it has none."""
    try:
        data = path.read_bytes()
        directories, offset = _layout(data)
        if directories is None:
            return None
        rva = struct.unpack_from("<I", data, directories + 2 * 8)[0]
        base = offset(rva) if rva else None
        if base is None:
            return None

        def resources(kind: int) -> dict[int, bytes]:
            """The resources of a kind by their number, in the first language of each."""
            def entries(table: int):
                named, numbered = struct.unpack_from("<HH", data, base + table + 12)
                return [struct.unpack_from("<II", data, base + table + 16 + i * 8) for i in range(named + numbered)]

            found = {}
            for name, target in entries(0):
                if name != kind or not target & 0x80000000:
                    continue
                for number, inner in entries(target & 0x7FFFFFFF):
                    if inner & 0x80000000:
                        languages = entries(inner & 0x7FFFFFFF)
                        if languages:
                            entry = languages[0][1]
                            data_rva, size = struct.unpack_from("<II", data, base + entry)
                            if (start := offset(data_rva)) is not None:
                                found[number] = data[start:start + size]
            return found

        groups = resources(14)
        if not groups:
            return None
        images = resources(3)
        # The first group is the one Windows shows for the program.
        group = groups[next(iter(groups))]
        count = struct.unpack_from("<H", group, 4)[0]
        best = None
        for i in range(count):
            width, _height, _colors, _reserved, _planes, bits, _size, number = struct.unpack_from("<BBBBHHIH", group, 6 + i * 14)
            score = (width or 256, bits)
            if number in images and (best is None or score > best[0]):
                best = (score, images[number])
        return _image_as_png(best[1]) if best else None
    except (OSError, struct.error, ValueError, IndexError, TypeError):
        return None


def read(path: Path) -> Program | None:
    """The header of a .exe or .dll, or None if it isn't a Windows program."""
    try:
        data = path.read_bytes()
        directories, offset = _layout(data)
        if directories is None:
            return None
        pe = struct.unpack_from("<I", data, 0x3C)[0]
        magic = struct.unpack_from("<H", data, pe + 24)[0]
        imports_rva = struct.unpack_from("<I", data, directories + 1 * 8)[0]
        dotnet_rva = struct.unpack_from("<I", data, directories + 14 * 8)[0]
        names = []
        entry = offset(imports_rva) if imports_rva else None
        while entry is not None:
            name_rva = struct.unpack_from("<I", data, entry + 12)[0]
            if not name_rva or (start := offset(name_rva)) is None:
                break
            names.append(data[start:data.index(b"\0", start)].decode("latin-1"))
            entry += 20
        return Program(32 if magic == 0x10B else 64, bool(dotnet_rva), tuple(names))
    except (OSError, struct.error, ValueError):
        return None
