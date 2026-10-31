#!/bin/bash
# Build Zone Launcher as an AppImage inside an Ubuntu 24.04 container (glibc 2.39), so it runs on
# distros from 2024 on. Needs Docker. Output: dist/Zone_Launcher-x86_64.AppImage
set -euo pipefail
root=$(cd "$(dirname "$0")/.." && pwd)
mkdir -p "$root/.appimage-build" "$root/dist"
docker run --rm \
    -v "$root":/src:ro -v "$root/.appimage-build":/work -v "$root/dist":/dist \
    -e HOST_UID="$(id -u)" -e HOST_GID="$(id -g)" \
    ubuntu:24.04 bash /src/appimage/build-in-container.sh
