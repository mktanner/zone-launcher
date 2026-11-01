# Zone Launcher

A Linux launcher for the S.T.A.L.K.E.R. trilogy (Shadow of Chernobyl, Clear Sky, Call of Pripyat)
running natively on the [OpenXRay](https://github.com/OpenXRay/xray-16) engine, packaged as a Flatpak
or an AppImage with the engine built in.

You need your own copies of the games. No game files are included.

## Download

Get `Zone_Launcher-x86_64.AppImage` from the [latest release](https://github.com/mktanner/zone-launcher/releases/latest),
then make it executable and run it:

```bash
chmod +x Zone_Launcher-x86_64.AppImage
./Zone_Launcher-x86_64.AppImage
```

It needs glibc 2.39 or newer (most distros from 2024 on). To build the Flatpak or the AppImage
yourself, see [Building](#building).

## What it does

- **Finds your games** in OpenXRay's folder (`~/.local/share/GSC Game World/`), Heroic's GOG
  library and games added to Heroic by hand (native or Flatpak), Steam and all its libraries (native
  or Flatpak), and Lutris. In the background it also searches `~/Games`, Wine and Bottles prefixes
  (GOG Galaxy or a retail installer run in Wine) and every drive under `/run/media`, `/media` and
  `/mnt`, for copies no launcher knows about.
- **Lets you pick the game files by hand**: each game's Details lists every copy found (from GOG,
  Steam, a disc or anywhere else) to choose from. **Choose folder…** accepts the game folder, a
  folder inside it (such as `bin/`), or a folder holding several games; **Search…** looks through
  any folder or whole drive. A folder you pick with a different game in it is added to that game.
  Copies you added or a search found can be removed from the list again (no files are deleted).
- **Shows what's in the game files**: whether every archive of the game is there (an incomplete
  download or copy is flagged), which official patch is installed, and a file browser for the game
  folder, the original install (read-only), saved games, and settings and logs. It tells you where
  each file comes from (the install, a mod, the launcher, the engine, or you), finds broken links,
  and can open, show, copy the path of, or trash files you or a tool added.
- **Sets each game up in OpenXRay's standard folder** (`~/.local/share/GSC Game World/<game>/`)
  by linking to the files of your Heroic/Steam install: nothing is copied and the original folder
  is never written to. Saves, settings and logs live in that folder's `_appdata_/`. A game you
  already copied there by hand is used as it is; if you pick another copy instead, Play renames
  yours to `<game> (own copy)` (still listed, with its saves) and copies its saves and settings over.
  Links are refreshed on every launch, so game updates are picked up.
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
- **Language**: one setting for all games, applied on the next Play to every game that has it.
  Clear Sky switches between its full language packs (voices and text), Shadow of Chernobyl between
  the text languages in its archives; Call of Pripyat only ships English from GOG. A pack that's
  switched away from is unlinked, or set aside in `localization/.inactive/` if it's a real file.
  Details lists each game's languages.
- **Resolution and display mode**: fullscreen at your monitor's native resolution by default,
  or borderless/windowed at any common size. Written into the game's `_appdata_/user.ltx` before
  each launch; pick "Use in-game setting" to manage it from the game's own options instead.
- **One window, no scrolling**: the three games side by side, each with its status, Mods,
  Details (install, checks and fixes, log) and Play; settings in a bar along the bottom.
- **Shows why a game crashed**: after a crash it pulls the error out of the OpenXRay log.
- **Mods**: browse ModDB for each game's mods and addons, download in your browser and have the
  launcher install the file when it lands; or install zip/7z/rar archives and unpacked folders
  yourself (button or drag and drop). Switch mods and their optional parts on and off, and set the
  load order. See below.

## Mods

Each game's card has a **Mods** button that opens its mod list.

- **Browse ModDB…** lists 30 of the game's mods or addons on ModDB, recently updated, popular all
  time, popular today or newly added, with pictures and a filter. Opening a mod shows its newest files (up to 30) with their type (full
  version, patch...) and download size. **Download** looks up the file's
  name, size and MD5 on its ModDB page, then opens ModDB's download page in your browser (ModDB
  doesn't allow automated downloads, so the launcher never downloads files itself). The launcher
  watches your Downloads folder (as set in `~/.config/user-dirs.dirs`) until the file is complete,
  checks its MD5, and installs it under the file's ModDB name, remembering where it came from.
  The wait carries on if you close the mods window; it's forgotten when the launcher quits. If
  your browser saves the file somewhere else, choose it from the waiting row.
- **Not every ModDB mod works here.** Mods for the original game usually do; standalone games such
  as Anomaly (and GAMMA on top of it), and mods that need their own engine (OGSR, OGSE, X-Ray
  Monolith, or anything that replaces the game's `bin/` files) don't run on OpenXRay. The ModDB
  pages say so, and entries whose name or summary mentions one of these get a warning.
- **Mods that ship their own engine** (`xrGame.dll`, `xrEngine.exe`, `xrCore.dll` or `xr_3da.exe`,
  usually in a `bin/` folder) are made for that modified Windows engine, which OpenXRay replaces; their
  files usually depend on it (ABR CS MOD Final, for one, crashes loading objects). The launcher marks
  them in the mod list, installs them switched off, asks before playing with one switched on (switch it
  off, or play anyway and don't be asked again), and names it if the game then crashes.
- For anything not in the list, paste a link to a ModDB mod (to see its files) or to a file or
  addon (to download it) into the filter, or use **Search on ModDB** and **All files on ModDB**,
  which open ModDB in your browser.
- The launcher reads only the ModDB pages you ask for, one at a time, as a browser would: a list's
  first page, a mod's downloads page, a file's page. The popular and newly added orders are `?sort=`
  links, which ModDB's `robots.txt` asks crawlers not to fetch; the launcher fetches one only when you
  pick that order, and never pages through lists. It identifies itself in its User-Agent, and caches
  lists for an hour, file pages for a day and pictures for a week (`~/.cache/zone-launcher/moddb/`). Download
  sizes come from each file's page, looked up two at a time.
- **Deleting** a mod (the trash button on its row) removes it from the library and shows how much
  space that frees. If the archive it was installed from is still there, you can move that to the
  trash too.
- **Installing** unpacks a mod once into the launcher's library (`~/.var/app/<app id>/data/zone-launcher/mods/`
  for the Flatpak, `~/.local/share/zone-launcher/mods/` otherwise). The launcher looks for the folders
  that hold `gamedata/`, `patches/` or `.db` archives: the top-most one is the mod itself (on by
  default), deeper ones such as an `!addons/<option>/` folder become optional parts with their own
  switch. A Windows `bin/` folder is skipped (see the note on mods with their own engine above).
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
- `0006-swing-arms-when-moving-unarmed.patch`: with empty hands the body always used the standing
  arm pose, so the player's shadow walked and ran with frozen arms. Uses the models' empty-handed
  walk/run/sprint arm animations.
- `0007-fix-light-plane-shader-alpha-on-opengl.patch`: the OpenGL light-plane shader left its alpha
  undefined (vertex shader wrote a 3-component color, pixel shader faded by the 4th), so Clear Sky's
  searchlight beams and spider webs strobed on Mesa. Writes the fade into all four, like the DirectX
  shader. Found by scanning every OpenGL shader pair for such mismatches; this was the only one.

Note that upstream's `ReleaseMasterGold` build type passes no optimisation flags, so the engine
is built unoptimised (as a normal upstream build is), which also enables libstdc++ assertions
with GCC 15 and later.

## Developing

The core logic (`detect.py`, `fixes.py`, `engine.py`, `mods.py`, `moddb.py`, `downloads.py`) doesn't import GTK and runs on any Python 3.10+:

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
| `zone_launcher/detect.py` | Finds installs from OpenXRay, Heroic, Steam, Lutris, folder searches and user folders |
| `zone_launcher/fixes.py` | The pre-launch checks and their fixes |
| `zone_launcher/engine.py` | Finds and launches `xr_3da`, reads crash logs |
| `zone_launcher/files.py` | Lists a game folder and where each file comes from |
| `zone_launcher/mods.py` | The mod library: installing, optional parts, load order, linking into the game |
| `zone_launcher/moddb.py` | Reads ModDB's lists, files feeds and file pages |
| `zone_launcher/downloads.py` | Notices finished browser downloads and checks them |
| `zone_launcher/window.py` | The GTK4/libadwaita window |
| `zone_launcher/files_ui.py` | The file browser |
| `zone_launcher/mods_ui.py`, `moddb_ui.py` | The mods window and its ModDB pages |
| `flatpak/` | Flatpak manifest (builds OpenXRay + the launcher) |
