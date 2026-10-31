#!/bin/bash
# Runs inside ubuntu:24.04 (see build.sh). /src is the project, /work a build cache, /dist the output.
set -euo pipefail
trap 'chown -R "$HOST_UID:$HOST_GID" /work /dist' EXIT
export DEBIAN_FRONTEND=noninteractive APPIMAGE_EXTRACT_AND_RUN=1

apt-get update -qq
apt-get install -y -qq --no-install-recommends \
    build-essential gcc-14 g++-14 cmake ninja-build git ca-certificates curl file patchelf \
    libsdl2-dev libopenal-dev libogg-dev libvorbis-dev libtheora-dev liblzo2-dev libjpeg-dev \
    libglew-dev libegl-dev libgl-dev \
    python3 python3-gi python3-gi-cairo gir1.2-gtk-4.0 gir1.2-adw-1 libadwaita-1-0 libgtk-4-dev pkg-config \
    librsvg2-common librsvg2-bin shared-mime-info adwaita-icon-theme > /dev/null

# --- OpenXRay: the same pinned commit and patches as the Flatpak --------------------------------
commit=$(sed -n 's/^ *commit: *\([0-9a-f]\{40\}\).*/\1/p' /src/flatpak/*.yml | head -1)
src=/work/xray-16
[ -d "$src/.git" ] || git clone -q https://github.com/OpenXRay/xray-16.git "$src"
git config --global --add safe.directory "*"  # /work is owned by the host user
git -C "$src" cat-file -e "$commit^{commit}" 2>/dev/null || git -C "$src" fetch -q origin
git -C "$src" checkout -q -f "$commit"
git -C "$src" clean -fdq
git -C "$src" submodule update -q --init --recursive --force
for patch in /src/patches/openxray/*.patch; do git -C "$src" apply "$patch"; done

# Same build as the Flatpak: no unity build, no extra flags (see README).
cmake -S "$src" -B /work/build -G Ninja -DCMAKE_BUILD_TYPE=ReleaseMasterGold \
    -DCMAKE_INSTALL_PREFIX=/usr -DCMAKE_C_COMPILER=gcc-14 -DCMAKE_CXX_COMPILER=g++-14 > /dev/null
cmake --build /work/build

app=/work/AppDir
rm -rf "$app"
DESTDIR="$app" cmake --install /work/build > /dev/null
rm -rf "$app"/usr/share/applications "$app"/usr/share/bash-completion "$app"/usr/share/pixmaps

# --- Launcher ------------------------------------------------------------------------------------
id=io.github.mktanner.ZoneLauncher
install -Dm755 /src/bin/zone-launcher "$app/usr/bin/zone-launcher"
mkdir -p "$app/usr/share/zone-launcher"
cp -r /src/zone_launcher "$app/usr/share/zone-launcher/"
find "$app/usr/share/zone-launcher" -name __pycache__ -prune -exec rm -rf {} +
install -Dm644 "/src/data/$id.desktop" "$app/usr/share/applications/$id.desktop"
install -Dm644 "/src/data/$id.metainfo.xml" "$app/usr/share/metainfo/$id.appdata.xml"
install -Dm644 "/src/data/$id.svg" "$app/usr/share/icons/hicolor/scalable/apps/$id.svg"
mkdir -p "$app/usr/share/icons/hicolor/256x256/apps"
rsvg-convert -w 256 -h 256 "/src/data/$id.svg" -o "$app/usr/share/icons/hicolor/256x256/apps/$id.png"

# --- Python + PyGObject + GTK 4 / libadwaita -----------------------------------------------------
install -Dm755 /usr/bin/python3.12 "$app/usr/bin/python3"
mkdir -p "$app/usr/lib/python3/dist-packages" "$app/usr/lib/x86_64-linux-gnu"
cp -a /usr/lib/python3.12 "$app/usr/lib/"
rm -rf "$app"/usr/lib/python3.12/{test,idlelib,tkinter,turtledemo,ensurepip,lib2to3,config-3.12-x86_64-linux-gnu}
cp -a /usr/lib/python3/dist-packages/{gi,cairo} "$app/usr/lib/python3/dist-packages/"
cp -a /usr/lib/x86_64-linux-gnu/girepository-1.0 "$app/usr/lib/x86_64-linux-gnu/"

# --- Collect shared libraries --------------------------------------------------------------------
tools=/work/tools
mkdir -p "$tools"
fetch() { [ -s "$tools/$1" ] || curl -fsSL -o "$tools/$1" "$2"; chmod +x "$tools/$1"; }
fetch linuxdeploy https://github.com/linuxdeploy/linuxdeploy/releases/download/continuous/linuxdeploy-x86_64.AppImage
fetch linuxdeploy-plugin-gtk.sh https://raw.githubusercontent.com/linuxdeploy/linuxdeploy-plugin-gtk/master/linuxdeploy-plugin-gtk.sh
fetch appimagetool https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage

libs=()
# Libraries PyGObject loads by name through the typelibs, plus the PyGObject/pycairo modules.
for name in libgtk-4.so.1 libadwaita-1.so.0 libgirepository-1.0.so.1 libgraphene-1.0.so.0 \
            libpangocairo-1.0.so.0 libcairo-gobject.so.2 libgdk_pixbuf-2.0.so.0 libharfbuzz-gobject.so.0; do
    libs+=(--library "/usr/lib/x86_64-linux-gnu/$name")
done
while IFS= read -r so; do libs+=(--deploy-deps-only "$so"); done < <(find "$app/usr/lib/python3" "$app/usr/lib/python3.12/lib-dynload" -name '*.so')

export DEPLOY_GTK_VERSION=4 PATH="$tools:$PATH"
"$tools/linuxdeploy" --appdir "$app" \
    --executable "$app/usr/bin/python3" --executable "$app/usr/bin/xr_3da" "${libs[@]}" \
    --desktop-file "$app/usr/share/applications/$id.desktop" \
    --icon-file "$app/usr/share/icons/hicolor/256x256/apps/$id.png" \
    --custom-apprun /src/appimage/AppRun --plugin gtk > /work/linuxdeploy.log

# linuxdeploy wraps a custom AppRun to source the plugin hooks first; ours sources them itself, after
# recording the environment, so the launcher can undo every change before starting the engine.
cp /src/appimage/AppRun "$app/AppRun"
rm -f "$app/AppRun.wrapped"
# appimagetool builds .DirIcon from a top-level PNG named after the desktop file's Icon=.
ln -sf "usr/share/icons/hicolor/256x256/apps/$id.png" "$app/$id.png"

# The host's newer C++ runtime must be used: its GL driver (Mesa) may need symbols ours lacks.
rm -f "$app"/usr/lib/libstdc++.so* "$app"/usr/lib/libgcc_s.so*

ARCH=x86_64 "$tools/appimagetool" --no-appstream "$app" /dist/Zone_Launcher-x86_64.AppImage > /work/appimagetool.log 2>&1
ls -la /dist/Zone_Launcher-x86_64.AppImage
