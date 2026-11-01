#!/bin/bash
# Runs inside ubuntu:24.04 (see build.sh). /src is the project, /work a build cache, /dist the output.
set -euo pipefail
trap 'chown -R "$HOST_UID:$HOST_GID" /work /dist' EXIT
export DEBIAN_FRONTEND=noninteractive APPIMAGE_EXTRACT_AND_RUN=1

apt-get update -qq
apt-get install -y -qq --no-install-recommends \
    build-essential gcc-13 g++-13 cmake ninja-build git ca-certificates curl file patchelf 7zip \
    liblzma-dev zlib1g-dev libbz2-dev libzstd-dev liblz4-dev \
    libsdl2-dev libopenal-dev libogg-dev libvorbis-dev libtheora-dev liblzo2-dev libjpeg-dev \
    libglew-dev libegl-dev libgl-dev \
    python3 python3-gi python3-gi-cairo gir1.2-gtk-4.0 gir1.2-adw-1 libadwaita-1-0 libgtk-4-dev pkg-config \
    librsvg2-common librsvg2-bin shared-mime-info adwaita-icon-theme > /dev/null

# --- OpenXRay: the same pinned commit and patches as the Flatpak --------------------------------
commit=$(sed -n 's/^ *commit: *\([0-9a-f]\{40\}\).*/\1/p' /src/flatpak/*.yml | head -1)
src=/work/xray-16
[ -d "$src/.git" ] || git clone -q https://github.com/OpenXRay/xray-16.git "$src"
git config --global --add safe.directory "*"  # /work is owned by the host user
# Reset and patch the source only when the commit or patches change: re-patching touches headers that
# nearly every file includes, which would recompile almost the whole engine on every run.
stamp="$commit $(cat /src/patches/openxray/*.patch | sha256sum | cut -c1-16)"
if [ "$(cat "$src/.zone-launcher-stamp" 2>/dev/null)" != "$stamp" ]; then
    git -C "$src" cat-file -e "$commit^{commit}" 2>/dev/null || git -C "$src" fetch -q origin
    git -C "$src" checkout -q -f "$commit"
    git -C "$src" clean -fdq
    git -C "$src" submodule update -q --init --recursive --force
    for patch in /src/patches/openxray/*.patch; do git -C "$src" apply "$patch"; done
    # The engine is built unoptimized (see README), so link-time optimization gains nothing and only
    # doubles the compiler's work and memory use.
    sed -i 's/set(CMAKE_INTERPROCEDURAL_OPTIMIZATION_RELEASEMASTERGOLD ON)/set(CMAKE_INTERPROCEDURAL_OPTIMIZATION_RELEASEMASTERGOLD OFF)/' \
        "$src/cmake/XRay.Build.cmake"
    echo "$stamp" > "$src/.zone-launcher-stamp"
fi

# Same build as the Flatpak: no unity build, no extra flags (see README). GCC 13, not 14: Ubuntu's
# GCC 14.2 leaves some luabind template constructors out, which links as a call to address 0 (Call
# of Pripyat crashed creating hanging lamps), and it also crashes now and then on this codebase.
cmake -S "$src" -B /work/build -G Ninja -DCMAKE_BUILD_TYPE=ReleaseMasterGold \
    -DCMAKE_INSTALL_PREFIX=/usr -DCMAKE_C_COMPILER=gcc-13 -DCMAKE_CXX_COMPILER=g++-13 > /dev/null
# If the compiler still crashes on a single file now and then, ninja drops the failed output and
# resumes, so retrying is safe.
for attempt in 1 2 3; do
    cmake --build /work/build ${JOBS:+-j "$JOBS"} && break
    [ "$attempt" = 3 ] && exit 1
    echo "Compiler crashed; retrying ($attempt/3)"
done

# A function the compiler forgot to emit links as a weak symbol at address 0 and crashes when called.
missing=$(nm -C "$src/bin/x86_64/ReleaseMasterGold/xr_3da" | awk '$1 == "w"' | grep -v -E ' (__gmon_start__|_ITM_|__cxa_|_Jv_|__pthread|__libc_)' || true)
if [ -n "$missing" ]; then
    echo "The engine has unresolved functions; it would crash calling them:"; echo "$missing"; exit 1
fi

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

# --- bsdtar and 7-Zip, to unpack mods ------------------------------------------------------------
# A current libarchive: Ubuntu 24.04's 3.7 (and every 7-Zip up to 26.00) fails on some 7z archives,
# like ABR's BCJ2 streams, that libarchive 3.8 unpacks correctly. It also reads RAR, which Debian's
# 7-Zip has removed. Same version as the Flatpak runtime.
libarchive=libarchive-3.8.9
[ -s "/work/$libarchive.tar.xz" ] || curl -fsSL -o "/work/$libarchive.tar.xz" \
    "https://github.com/libarchive/libarchive/releases/download/v3.8.9/$libarchive.tar.xz"
echo "888c934f9d95648ecb9163dc8e23ab80a476ecb81a8f1154704a227b5b676dde  /work/$libarchive.tar.xz" | sha256sum -c -
rm -rf "/work/$libarchive" && tar -xf "/work/$libarchive.tar.xz" -C /work
cmake -S "/work/$libarchive" -B /work/libarchive-build -G Ninja -DCMAKE_BUILD_TYPE=Release \
    -DBUILD_SHARED_LIBS=OFF -DENABLE_TEST=OFF -DENABLE_CPIO=OFF -DENABLE_CAT=OFF -DENABLE_UNZIP=OFF \
    -DENABLE_OPENSSL=OFF -DENABLE_LIBXML2=OFF -DENABLE_EXPAT=OFF -DENABLE_ACL=OFF -DENABLE_XATTR=OFF > /dev/null
cmake --build /work/libarchive-build --target bsdtar > /dev/null
install -Dm755 /work/libarchive-build/bin/bsdtar "$app/usr/bin/bsdtar"

# Ubuntu's /usr/bin/7z is a wrapper; the real binary loads its format plugin (7z.so) from its own folder.
mkdir -p "$app/usr/lib/7zip"
cp /usr/lib/7zip/7z /usr/lib/7zip/7z.so "$app/usr/lib/7zip/"
printf '#!/bin/sh\nexec "$(dirname "$(readlink -f "$0")")/../lib/7zip/7z" "$@"\n' > "$app/usr/bin/7z"
chmod +x "$app/usr/bin/7z"

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
    --executable "$app/usr/bin/python3" --executable "$app/usr/bin/xr_3da" --executable "$app/usr/bin/bsdtar" \
    --deploy-deps-only "$app/usr/lib/7zip/7z" --deploy-deps-only "$app/usr/lib/7zip/7z.so" \
    "${libs[@]}" \
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
