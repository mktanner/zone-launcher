"""Reads what a Windows program says about itself, from its file header. Nothing here runs it."""

import struct
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Program:
    bits: int
    # A .NET program: it needs Wine Mono.
    dotnet: bool
    # The DLLs it loads at startup, as written in the file.
    imports: tuple[str, ...]


def read(path: Path) -> Program | None:
    """The header of a .exe or .dll, or None if it isn't a Windows program."""
    try:
        data = path.read_bytes()
        pe = struct.unpack_from("<I", data, 0x3C)[0]
        if data[:2] != b"MZ" or data[pe:pe + 4] != b"PE\0\0":
            return None
        sections, optional_size = struct.unpack_from("<H", data, pe + 6)[0], struct.unpack_from("<H", data, pe + 20)[0]
        optional = pe + 24
        magic = struct.unpack_from("<H", data, optional)[0]
        if magic not in (0x10B, 0x20B):
            return None
        directories = optional + (96 if magic == 0x10B else 112)
        imports_rva = struct.unpack_from("<I", data, directories + 1 * 8)[0]
        dotnet_rva = struct.unpack_from("<I", data, directories + 14 * 8)[0]
        table = [struct.unpack_from("<IIII", data, optional + optional_size + i * 40 + 8) for i in range(sections)]

        def offset(rva: int) -> int | None:
            for virtual_size, address, raw_size, raw in table:
                if address <= rva < address + max(virtual_size, raw_size):
                    return rva - address + raw
            return None

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
