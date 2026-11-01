# Zone Launcher

A Linux launcher for the S.T.A.L.K.E.R. trilogy (Shadow of Chernobyl, Clear Sky, Call of Pripyat)
running natively on the [OpenXRay](https://github.com/OpenXRay/xray-16) engine, packaged as a Flatpak
with the engine built in.

You need your own copies of the games. No game files are included.

## What it does

- **Finds your games** in OpenXRay's folder (`~/.local/share/GSC Game World/`), Heroic's GOG
  library (native or Flatpak), Steam (native or Flatpak), and any folder you add.
- **Sets each game up in OpenXRay's standard folder** (`~/.local/share/GSC Game World/<game>/`)
  by linking to the files of your Heroic/Steam install: nothing is copied and the original folder
  is never written to. Saves, settings and logs live in that folder's `_appdata_/`. A game you
  already copied there by hand is used as it is. Links are refreshed on every launch, so game
  updates are picked up.
- **Checks each game folder before launch** and fixes what it can:
  - **Missing language pack** (Clear Sky, Call of Pripyat). GOG ships Clear Sky's English voices
    and text as a separate `xenglish.db` that its Windows installer copies into `localization/`.
    Heroic downloads it but never copies it, so starting a new game crashes with
    `There is no sound collection with path 'characters_voice\scenario\...'`. The launcher copies
    the pack in from Heroic's GOG support folder, or from the game's own `languages/` folder.
  - **`fsgame.ltx`** missing or wrong for the game: installs OpenXRay's version, or for Shadow of
    Chernobyl one with its `config\` layout (OpenXRay only ships the Clear Sky/Call of Pripyat
    layout; GOG's installer normally writes Shadow of Chernobyl's). Keeps any old one as
    `fsgame.ltx.original`.
  - **Default settings and key bindings** missing: copies GOG's default `user.ltx`, which its
    Windows installer would have installed.
  - **OpenGL shaders** not linked, or linked to a different engine install (such as a system
    `/usr/share/openxray` seen from inside the Flatpak): re-links `gamedata/shaders/gl`.
- **Resolution and display mode**: fullscreen at your monitor's native resolution by default,
  or borderless/windowed at any common size. Written into the game's `_appdata_/user.ltx` before
  each launch; pick "Use in-game setting" to manage it from the game's own options instead.
- **One window, no scrolling**: the three games side by side, each with its status, Mods,
  Details (install, checks and fixes, log) and Play; settings in a bar along the bottom.
- **Shows why a game crashed**: after a crash it pulls the error out of the OpenXRay log.
- **Mods**: install mods from zip/7z/rar archives or unpacked folders (button or drag and drop),
  switch them and their optional parts on and off, and set the load order. See below.

## Mods

Each game's card has a **Mods** button that opens its mod list.

- **Installing** unpacks a mod once into the launcher's library (`~/.var/app/<app id>/data/zone-launcher/mods/`
  for the Flatpak, `~/.local/share/zone-launcher/mods/` otherwise). The launcher looks for the folders
  that hold `gamedata/`, `patches/` or `.db` archives: the top-most one is the mod itself (on by
  default), deeper ones such as an `!addons/<option>/` folder become optional parts with their own
  switch. A Windows `bin/` folder (a modified original engine) is skipped with a warning.
- **On Play**, the game folder's `gamedata/` and `patches/` are rebuilt from the original install,
  then every enabled part is linked in, in load order: loose files into `gamedata/` (higher in the
  list wins), archives into `patches/` (named so they load after the game's own patches). Nothing is
  copied, and switching a mod off restores the original files on the next Play.
- Mods need a game the launcher set up (linked); a copy made by hand is left alone.
- Unpacking uses libarchive's `bsdtar` (in the Flatpak runtime, bundled in the AppImage), with 7-Zip as
  a fallback: some 7z archives (ABR's BCJ2 streams, for example) only unpack correctly with libarchive 3.8.

## Building

Needs `flatpak` with Flathub set up.

```bash
flatpak install --user flathub org.flatpak.Builder org.gnome.Sdk//51
flatpak run org.flatpak.Builder --user --force-clean --install-deps-from=flathub \
    --repo=repo --install build-dir flatpak/io.github.mktanner.ZoneLauncher.yml
flatpak run io.github.mktanner.ZoneLauncher
```

To make a single-file bundle to share:

```bash
flatpak build-bundle repo zone-launcher.flatpak io.github.mktanner.ZoneLauncher
```

The engine is pinned to an OpenXRay `dev` commit in the manifest. To update it, change `commit:`
under the `openxray` module.

Don't combine `CMAKE_UNITY_BUILD` with Flatpak's default C/C++ flags for the engine (upstream's
own Flatpak manifest does). Built that way, Clear Sky's opening cutscene leaves the camera and
the player under the terrain at the start of a new game. Unity alone and `-O2` alone were both
tested and work.

## AppImage

The same launcher and engine as a single file, for systems without Flatpak:

```bash
./appimage/build.sh
```

This builds everything inside an Ubuntu 24.04 Docker container (the engine from the same pinned
commit and patches as the Flatpak), and writes `dist/Zone_Launcher-x86_64.AppImage`. It runs on
distros with glibc 2.39 or newer (roughly anything from 2024 on). It bundles Python, GTK 4,
libadwaita and the engine's libraries, and uses the system's graphics driver, C++ runtime and the
usual desktop base libraries (X11/Wayland, ALSA, fontconfig, FreeType, HarfBuzz, FriBidi), which
AppImages leave out on purpose. Settings live in `~/.config/zone-launcher/`, separate from the
Flatpak's.

The engine is built with Ubuntu's GCC 13: its GCC 14 crashes now and then on this codebase and once
left a luabind function out entirely (the build now fails if any function is left unresolved). Set
`JOBS=<n>` to limit parallel compile jobs.

The AppImage and the Flatpak can both be used on one machine: each re-links the game folders'
OpenGL shaders to its own engine when you press Play.

## Engine patches

Fixes not yet in upstream OpenXRay live in `patches/openxray/` and are applied by the manifest:

- `0001-unload-level-when-window-closed.patch`: closing the game window (X button, Alt+F4)
  while in a level left `xr_3da` hung forever on exit. SDL requests quit on the same frame the
  engine queues its level unload, so the main loop exits first; the leftover render lights then
  lock the already-freed spatial database during static destruction.
- `0002-keep-missing-error-dialogs-in-place.patch`: Shadow of Chernobyl crashed on startup. The
  main menu looks its error dialogs up by index but dropped any whose layout the game lacks, so
  later lookups read past the end of the list.
- `0003-skip-secondary-task-panel-when-missing.patch`: pressing the objectives key (Tab) in
  Shadow of Chernobyl crashed with "XML node not found: secondary_task"; that game's UI has no
  secondary task panel.
- `0004-detect-debugger-without-ptrace-traceme.patch` (OpenXRay #2143): the engine checked for a
  debugger with `ptrace(PTRACE_TRACEME)`, which made its parent (terminal, desktop session) its
  tracer for good; the next signal froze the game in state `t (tracing stop)`. Inside a Flatpak the
  probe failed instead and the engine believed a debugger was attached. Now reads `TracerPid`.
- `0005-fix-uninitialized-memory-and-enum-ub.patch`: found by running all three games under
  AddressSanitizer/UndefinedBehaviorSanitizer. Physics objects serialized uninitialized
  positions/velocities, the animation blend pool and inventory grid used uninitialized values,
  and the camera/post-process effector enums couldn't legally hold the ids scripts use.

Note that upstream's `ReleaseMasterGold` build type passes no optimisation flags, so the engine
is built unoptimised (as a normal upstream build is), which also enables libstdc++ assertions
with GCC 15 and later.

## Developing

The core logic (`detect.py`, `fixes.py`, `engine.py`) doesn't import GTK and runs on any Python 3.10+:

```bash
python3 -m unittest discover -s tests -t .
```

To run the UI from a checkout against a system OpenXRay install, use a Python with GTK 4 and
libadwaita, or the GNOME SDK:

```bash
flatpak run --filesystem=home --command=python3 org.gnome.Sdk//51 bin/zone-launcher
```

| File | Purpose |
| --- | --- |
| `zone_launcher/games.py` | Facts about each game: engine flag, GOG/Steam IDs, how to recognise its folder |
| `zone_launcher/detect.py` | Finds installs from OpenXRay, Heroic, Steam and user folders |
| `zone_launcher/fixes.py` | The pre-launch checks and their fixes |
| `zone_launcher/engine.py` | Finds and launches `xr_3da`, reads crash logs |
| `zone_launcher/window.py` | The GTK4/libadwaita window |
| `flatpak/` | Flatpak manifest (builds OpenXRay + the launcher) |
