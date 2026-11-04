"""Gets what runs Windows programs, for a computer that doesn't have it: Proton and Microsoft's DirectX
libraries. Both go in the launcher's own folder, where it looks for them with the others."""

import hashlib
import json
import re
import shutil
import tarfile
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from . import VERSION, mods

USER_AGENT = f"ZoneLauncher/{VERSION} (+https://github.com/mktanner/zone-launcher)"
PROTON_RELEASE = "https://api.github.com/repos/CachyOS/proton-cachyos/releases/latest"
PROTON_ARCHIVE = re.compile(r"(proton-cachyos-.+-x86_64)\.tar\.xz")
# Unpacked, it takes this many times the download.
PROTON_UNPACK_FACTOR = 6
PROTON_FOLDER = ".local/share/zone-launcher/proton"
# The file Winetricks also gets, with the checksum it checks.
DIRECTX_URL = ("https://download.microsoft.com/download/8/4/A/84A35BF1-DAFE-4AE8-82AF-AD2AE20B6B14/"
               "directx_Jun2010_redist.exe")
DIRECTX_SHA256 = "053f76dcbb28802e23341b6a787e3b0791c0fa5c8d4d011b1044172dbf89c73b"
DIRECTX_SIZE = 100275120
DIRECTX_FOLDER = ".local/share/zone-launcher/directx"

Progress = Callable[[str], None]


class GetError(OSError):
    pass


@dataclass(frozen=True)
class ProtonRelease:
    name: str
    url: str
    checksums: str
    size: int


def _open(url: str, timeout: int = 30):
    return urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": USER_AGENT}), timeout=timeout)


def parse_proton_release(release: dict) -> ProtonRelease:
    assets = {asset.get("name"): asset for asset in release.get("assets", [])}
    for name, asset in assets.items():
        if (match := PROTON_ARCHIVE.fullmatch(name or "")) and f"{match[1]}.sha512sum" in assets:
            return ProtonRelease(match[1], asset["browser_download_url"],
                                 assets[f"{match[1]}.sha512sum"]["browser_download_url"], asset["size"])
    raise GetError("The latest CachyOS Proton release has no download for this computer.")


def latest_proton() -> ProtonRelease:
    try:
        with _open(PROTON_RELEASE) as response:
            return parse_proton_release(json.load(response))
    except (OSError, ValueError, KeyError) as e:
        raise GetError(f"Couldn't look up the latest Proton: {getattr(e, 'reason', None) or e}") from e


def _human(size: float) -> str:
    return f"{size / 1024 / 1024:.0f} MB"


def _download(url: str, path: Path, digest, expected: str, progress: Progress) -> None:
    """Saves a file, which has to match its checksum."""
    try:
        with _open(url) as response, open(path, "wb") as out:
            total, done, shown = int(response.headers.get("Content-Length") or 0), 0, -1
            while chunk := response.read(1024 * 1024):
                out.write(chunk)
                digest.update(chunk)
                done += len(chunk)
                percent = done * 100 // total if total else 0
                if percent != shown:
                    progress(f"Downloading: {percent}% of {_human(total)}")
                    shown = percent
    except OSError as e:
        raise GetError(f"Couldn't download it: {getattr(e, 'reason', None) or e}") from e
    if digest.hexdigest().lower() != expected.lower():
        raise GetError("The download doesn't match its checksum, so it's damaged. Try again.")


def _check_space(folder: Path, needed: int) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(folder).free
    if free < needed:
        raise GetError(f"That needs {_human(needed)} free in {folder}, and it has {_human(free)}.")


def install_proton(release: ProtonRelease, progress: Progress) -> Path:
    folder = Path.home() / PROTON_FOLDER
    target = folder / release.name
    if target.is_dir():
        return target
    _check_space(folder, release.size * PROTON_UNPACK_FACTOR)
    archive, staging = folder / f".{release.name}.tar.xz", folder / f".unpacking-{release.name}"
    try:
        try:
            with _open(release.checksums) as response:
                expected = response.read().decode("ascii", "replace").split()[0]
        except (OSError, IndexError) as e:
            raise GetError(f"Couldn't read the download's checksum: {getattr(e, 'reason', None) or e}") from e
        _download(release.url, archive, hashlib.sha512(), expected, progress)
        progress("Unpacking…")
        shutil.rmtree(staging, ignore_errors=True)
        try:
            with tarfile.open(archive) as tar:
                tar.extractall(staging, filter="data")
        except (tarfile.TarError, ValueError) as e:
            raise GetError(f"Couldn't unpack it: {e}") from e
        unpacked = staging / release.name
        if not (unpacked / "proton").is_file():
            raise GetError("The download doesn't have Proton in it.")
        unpacked.rename(target)
        return target
    finally:
        archive.unlink(missing_ok=True)
        shutil.rmtree(staging, ignore_errors=True)


def embedded_cabinet(data: bytes) -> bytes:
    """The cabinet inside a self-extracting installer: not every unpacker looks far enough into one to find it."""
    start = data.find(b"MSCF\0\0\0\0")
    while start != -1:
        size = int.from_bytes(data[start + 8:start + 12], "little")
        if data[start + 24:start + 26] == b"\x03\x01" and 0 < size <= len(data) - start:
            return data[start:start + size]
        start = data.find(b"MSCF\0\0\0\0", start + 1)
    raise GetError("Microsoft's download has no libraries in it.")


def install_directx(progress: Progress) -> Path:
    """Keeps the .cab files of Microsoft's DirectX installer, which the launcher then uses as it does Steam's."""
    target = Path.home() / DIRECTX_FOLDER
    parent = target.parent
    _check_space(parent, DIRECTX_SIZE * 3)
    archive, staging = parent / ".directx_Jun2010_redist.exe", parent / ".unpacking-directx"
    try:
        _download(DIRECTX_URL, archive, hashlib.sha256(), DIRECTX_SHA256, progress)
        progress("Unpacking…")
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir()
        outer = staging / "directx.cab"
        outer.write_bytes(embedded_cabinet(archive.read_bytes()))
        try:
            mods.extract(outer, staging / "inside")
        except mods.ModError as e:
            raise GetError(str(e)) from e
        cabs = list((staging / "inside").rglob("*.cab"))
        if not cabs:
            raise GetError("Microsoft's download has no libraries in it.")
        target.mkdir(exist_ok=True)
        for cab in cabs:
            cab.replace(target / cab.name)
        return target
    finally:
        archive.unlink(missing_ok=True)
        shutil.rmtree(staging, ignore_errors=True)
