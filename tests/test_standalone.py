import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from zone_launcher import pe, standalone
from tests.test_core import touch
from tests.test_pe import write_program


class StandaloneTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.folder = self.tmp / "STALKER_Complete_1.5.4.5"
        touch(self.folder / "fsgame.ltx")
        touch(self.folder / "bin" / "XR_3DA.exe")
        home = mock.patch.object(Path, "home", lambda: self.tmp / "home")
        home.start()
        self.addCleanup(home.stop)

    def in_flatpak(self, value: bool):
        patch = mock.patch.object(standalone, "in_flatpak", lambda: value)
        patch.start()
        self.addCleanup(patch.stop)

    def test_load(self):
        mod = standalone.load(self.folder)
        self.assertEqual(mod.name, "STALKER Complete 1.5.4.5")
        self.assertEqual(mod.exe, "bin/XR_3DA.exe")
        self.assertEqual(mod.key, f"standalone:{self.folder}")
        self.assertEqual(standalone.load(self.folder, "bin/Other.exe").exe, "bin/Other.exe")
        self.assertIsNone(standalone.load(self.tmp / "missing").exe)

    def test_popular_engines_are_recognised(self):
        folder = self.tmp / "anomaly"
        for name in ("AnomalyLauncher.exe", "bin/AnomalyDX9.exe", "bin/AnomalyDX9AVX.exe", "bin/AnomalyDX11.exe",
                     "bin/AnomalyDX11AVX.exe", "bin/VerifiedDX11.exe", "tools/converter.exe", "unins000.exe"):
            touch(folder / name)
        with mock.patch.object(standalone, "has_avx", lambda: True):
            self.assertEqual(standalone.find_launchers(folder),
                             ["bin/AnomalyDX11AVX.exe", "bin/AnomalyDX9AVX.exe", "bin/AnomalyDX11.exe",
                              "bin/AnomalyDX9.exe", "bin/VerifiedDX11.exe", "AnomalyLauncher.exe"])
        with mock.patch.object(standalone, "has_avx", lambda: False):
            self.assertEqual(standalone.find_launchers(folder)[0], "bin/AnomalyDX11.exe")

    def test_any_mod_finds_a_launcher_by_itself(self):
        folder = self.tmp / "unknown"
        for name in ("readme.txt", "Launcher.exe", "game.exe", "bin/mygame.exe", "setup/setup.exe", "Setup.exe"):
            touch(folder / name)
        self.assertEqual(standalone.find_launchers(folder), ["bin/mygame.exe", "Launcher.exe", "game.exe"])
        self.assertEqual(standalone.load(folder).exe, "bin/mygame.exe")

    def test_engine_name_ignores_case(self):
        folder = self.tmp / "other"
        touch(folder / "bin" / "xrengine.EXE")
        touch(folder / "bin" / "aaa.exe")
        self.assertEqual(standalone.find_launchers(folder)[0], "bin/xrengine.EXE")

    def test_locate_inside_a_wrapper_folder(self):
        wrapper = self.tmp / "wrapper"
        touch(wrapper / "readme.txt")
        touch(wrapper / "Mod 1.0" / "fsgame.ltx")
        self.assertEqual(standalone.locate(wrapper), wrapper / "Mod 1.0")
        self.assertEqual(standalone.locate(self.folder), self.folder)
        touch(wrapper / "Mod 2.0" / "fsgame.ltx")
        self.assertEqual(standalone.locate(wrapper), wrapper)

    def test_app_data_comes_from_fsgame(self):
        mod = standalone.load(self.folder)
        self.assertEqual(mod.app_data, self.folder / "_appdata_")  # the default: this fsgame.ltx is empty
        (self.folder / "fsgame.ltx").write_text(
            ";abbreviation = recurs| notif| root| add\n$app_data_root$  = true \t| false\t| $fs_root$\t | appdata\\\n")
        self.assertEqual(mod.app_data, self.folder / "appdata")
        touch(self.folder / "appdata" / "logs" / "xray_me.log")
        self.assertEqual(mod.latest_log(), self.folder / "appdata" / "logs" / "xray_me.log")
        (self.folder / "fsgame.ltx").write_text("$app_data_root$ = true| false| $fs_root$| ..\\elsewhere\\\n")
        self.assertEqual(mod.app_data, self.folder / "_appdata_")

    def test_check_finds_what_is_missing(self):
        folder = self.tmp / "broken"
        write_program(folder / "bin" / "game.exe", imports=["xrCore.dll", "KERNEL32.dll", "xrSound.dll",
                                                            "api-ms-win-crt-runtime-l1-1-0.dll"])
        write_program(folder / "bin" / "xrCore.dll", imports=["stlport.dll", "user32.dll"])
        mod = standalone.load(folder)
        with mock.patch.object(standalone, "runner_problem", lambda r: None), \
                mock.patch.object(standalone, "wine_libraries", lambda r: {"kernel32.dll", "user32.dll"}):
            checks = {c.title: c for c in standalone.check(mod)}
            self.assertTrue(checks["Launcher"].ok)
            self.assertFalse(checks["Game file list (fsgame.ltx)"].ok)
            self.assertFalse(checks["Game data"].ok)
            self.assertFalse(checks["Libraries"].ok)
            self.assertEqual(checks["Libraries"].detail, "Missing: stlport.dll, xrSound.dll")

            touch(folder / "fsgame.ltx")
            touch(folder / "gamedata.db0")
            write_program(folder / "bin" / "stlport.dll")
            write_program(folder / "bin" / "xrsound.DLL")
            self.assertEqual(standalone.problems(mod), [])

    def test_check_bad_launcher(self):
        with mock.patch.object(standalone, "runner_problem", lambda r: None), \
                mock.patch.object(standalone, "wine_libraries", lambda r: set()):
            # Not a real program: the test's empty file.
            checks = standalone.check(standalone.load(self.folder))
            self.assertFalse(checks[0].ok)
            self.assertIn("isn't a Windows program", checks[0].detail)
            gone = standalone.check(standalone.load(self.folder, "bin/gone.exe"))
            self.assertIn("isn't in the folder", gone[0].detail)
            nothing = self.tmp / "nothing"
            nothing.mkdir()
            self.assertIn("No Windows program", standalone.check(standalone.load(nothing))[0].detail)

    def test_check_wine_missing(self):
        with mock.patch.object(standalone, "wine_version", lambda: None):
            titles = {c.title: c.ok for c in standalone.check(standalone.load(self.folder))}
        self.assertFalse(titles[standalone.SYSTEM_WINE])

    def test_wine_libraries_are_asked_once(self):
        self.in_flatpak(False)
        standalone._wine_libraries.clear()
        self.addCleanup(standalone._wine_libraries.clear)
        result = mock.Mock(stdout="/usr/lib/wine/x86_64-windows:\nKtmW32.dll\nd3d9.dll\n\n")
        with mock.patch("subprocess.run", return_value=result) as run:
            system = standalone.runner_at(standalone.SYSTEM_RUNNER)
            self.assertEqual(standalone.wine_libraries(system), {"ktmw32.dll", "d3d9.dll"})
            standalone.wine_libraries(system)
            self.assertEqual(run.call_count, 1)
            standalone.wine_libraries(standalone.runner_at("/some/proton"))
        self.assertEqual(run.call_count, 2)
        self.assertEqual(run.call_args.args[0][-2:], [standalone.RUNNER_LIBRARIES, "/some/proton"])

    def test_dotnet_launcher_keeps_wine_mono(self):
        write_program(self.folder / "Launcher.exe", dotnet=True)
        write_program(self.folder / "bin" / "XR_3DA.exe")
        self.assertIn(standalone.NO_DOTNET, standalone.wine_environment(standalone.load(self.folder))["WINEDLLOVERRIDES"])
        chosen = standalone.load(self.folder, "Launcher.exe")
        self.assertNotIn("mscoree", standalone.wine_environment(chosen)["WINEDLLOVERRIDES"])

    def test_each_mod_has_its_own_prefix(self):
        other = self.tmp / "elsewhere" / self.folder.name
        touch(other / "fsgame.ltx")
        first, second = standalone.load(self.folder), standalone.load(other)
        self.assertNotEqual(first.prefix, second.prefix)
        self.assertTrue(first.prefix.is_relative_to(self.tmp / "home" / ".local" / "share" / "zone-launcher"))
        self.assertTrue(first.data_dir.name.startswith("stalker-complete-1-5-4-5-"))

    def test_icon(self):
        mod = standalone.load(self.folder)
        self.assertIsNone(mod.icon)
        touch(self.folder / "Linux" / "icon.png")
        self.assertEqual(mod.icon, self.folder / "Linux" / "icon.png")

    def test_host_command_outside_flatpak(self):
        self.in_flatpak(False)
        self.assertEqual(standalone.host_command(["wine", "x.exe"], self.folder, {"A": "1"}), ["wine", "x.exe"])

    def test_host_command_in_flatpak(self):
        self.in_flatpak(True)
        self.assertEqual(standalone.host_command(["wine", "x.exe"], self.folder, {"A": "1"}),
                         ["flatpak-spawn", "--host", f"--directory={self.folder}", "--env=A=1", "wine", "x.exe"])

    def test_launch(self):
        self.in_flatpak(False)
        mod = standalone.load(self.folder)
        with mock.patch("subprocess.Popen") as popen:
            standalone.launch(mod)
        args, kwargs = popen.call_args
        self.assertEqual(args[0], ["wine", "bin/XR_3DA.exe"])
        self.assertEqual(kwargs["cwd"], self.folder)
        self.assertEqual(kwargs["env"]["WINEPREFIX"], str(mod.prefix))
        # The bundled DXVK, and the runtime DLLs play.sh also asks for.
        for override in ("d3d9=n,b", "dbghelp=b", "msvcr71=n,b", "d3dcompiler_43=n,b"):
            self.assertIn(override, kwargs["env"]["WINEDLLOVERRIDES"].split(";"))
        self.assertTrue(mod.prefix.is_dir())
        self.assertTrue(mod.wine_log.is_file())

    def test_launch_undoes_appimage_environment(self):
        self.in_flatpak(False)
        env = {"ZONE_LAUNCHER_SET_VARS": " LD_LIBRARY_PATH", "LD_LIBRARY_PATH": "/tmp/.mount/usr/lib"}
        with mock.patch.dict(os.environ, env), mock.patch("subprocess.Popen") as popen:
            standalone.launch(standalone.load(self.folder))
        self.assertNotIn("LD_LIBRARY_PATH", popen.call_args.kwargs["env"])

    def test_stop_ends_the_prefix(self):
        self.in_flatpak(True)
        mod = standalone.load(self.folder)
        with mock.patch("subprocess.Popen") as popen:
            standalone.stop(mod)
        argv = popen.call_args.args[0]
        self.assertEqual(argv[-3:], ["wine", "wineboot", "-k"])
        self.assertIn(f"--env=WINEPREFIX={mod.prefix}", argv)

    def proton(self, name="GE-Proton11-1") -> Path:
        folder = self.tmp / "home" / ".local" / "share" / "Steam" / "compatibilitytools.d" / name
        touch(folder / "proton")
        (folder / "files" / "bin").mkdir(parents=True, exist_ok=True)
        return folder

    def test_runners_are_found(self):
        self.proton("GE-Proton10-4")
        self.proton("GE-Proton10-34")
        touch(self.tmp / "home" / ".steam" / "steam" / "steamapps" / "common" / "Proton 10.0" / "proton")
        touch(self.tmp / "home" / ".steam" / "steam" / "steamapps" / "common" / "Proton EasyAntiCheat Runtime" / "x")
        touch(self.tmp / "home" / ".local" / "share" / "lutris" / "runners" / "wine" / "lutris-9" / "bin" / "wine")
        touch(self.tmp / "home" / ".local" / "share" / "Steam" / "compatibilitytools.d" / "not-a-runner" / "readme")
        touch(self.tmp / "home" / ".steam" / "steam" / "steamapps" / "common" / "Proton - Experimental" / "proton")
        touch(self.tmp / "home" / ".steam" / "steam" / "steamapps" / "common" / "Proton Hotfix" / "proton")
        self.proton("proton-cachyos-11.0-20261005-slr-x86_64")
        self.proton("proton-cachyos-11.0-20261015-slr-x86_64")
        self.assertEqual([r.name for r in standalone.find_runners()],
                         ["proton-cachyos-11.0-20261015-slr-x86_64", "proton-cachyos-11.0-20261005-slr-x86_64",
                          "Proton - Experimental", "GE-Proton10-34", "GE-Proton10-4", "Proton 10.0", "Proton Hotfix",
                          "lutris-9"])

    def test_a_new_mod_uses_the_best_runner(self):
        self.assertEqual(standalone.load(self.folder).runner.name, standalone.SYSTEM_WINE)
        build = self.tmp / "home" / ".local" / "share" / "lutris" / "runners" / "wine" / "lutris-7"
        touch(build / "bin" / "wine")
        # A Wine build only if there's nothing newer to be sure of.
        with mock.patch.object(standalone, "wine_version", lambda: "wine-10.0"):
            self.assertEqual(standalone.load(self.folder).runner.name, standalone.SYSTEM_WINE)
        with mock.patch.object(standalone, "wine_version", lambda: None):
            self.assertEqual(standalone.load(self.folder).runner.name, "lutris-7")
        self.proton("GE-Proton10-34")
        self.assertEqual(standalone.load(self.folder).runner.name, "GE-Proton10-34")
        self.proton("proton-cachyos-11.0-20261015-slr-x86_64")
        self.assertEqual(standalone.load(self.folder).runner.name, "proton-cachyos-11.0-20261015-slr-x86_64")

    def test_a_chosen_runner_stays_chosen(self):
        proton = self.proton("GE-Proton10-34")
        self.proton("proton-cachyos-11.0-20261015-slr-x86_64")
        self.assertEqual(standalone.load(self.folder, settings={"runner": str(proton)}).runner.path, proton)
        system = standalone.load(self.folder, settings={"runner": standalone.SYSTEM_RUNNER}).runner
        self.assertEqual(system.name, standalone.SYSTEM_WINE)
        self.assertFalse(system.is_proton)

    def test_each_family_of_proton_has_its_own_prefix(self):
        def prefix(name):
            return standalone.load(self.folder, settings={"runner": str(self.proton(name))}).prefix
        self.assertEqual(prefix("GE-Proton10-34"), prefix("GE-Proton11-1"))
        self.assertEqual(prefix("proton-cachyos-11.0-20261005-slr-x86_64"),
                         prefix("proton-cachyos-11.0-20261015-slr-x86_64"))
        self.assertNotEqual(prefix("GE-Proton11-1"), prefix("proton-cachyos-11.0-20261015-slr-x86_64"))
        self.assertNotEqual(prefix("Proton - Experimental"), prefix("proton-cachyos-11.0-20261015-slr-x86_64"))
        self.assertEqual(prefix("Proton 10.0").parent.name, "proton")

    def test_steam_link_finds_each_runner_once(self):
        self.proton()
        (self.tmp / "home" / ".steam").mkdir(parents=True)
        (self.tmp / "home" / ".steam" / "steam").symlink_to(self.tmp / "home" / ".local" / "share" / "Steam")
        self.assertEqual([r.name for r in standalone.find_runners()], ["GE-Proton11-1"])

    def test_a_copy_of_a_version_shows_once_and_steams_is_used(self):
        proton = self.proton()
        touch(self.tmp / "home" / ".local" / "share" / "lutris" / "runners" / "wine" / proton.name / "bin" / "wine")
        self.assertEqual(standalone.find_runners(), [standalone.Runner(proton.name, proton)])

    def test_runner_problems(self):
        self.in_flatpak(False)
        with mock.patch.object(standalone, "wine_version", lambda: "wine-10.0"):
            self.assertIsNone(standalone.runner_problem(standalone.runner_at("")))
        self.assertIn("isn't there", standalone.runner_problem(standalone.runner_at(str(self.tmp / "gone"))))
        with mock.patch.object(standalone, "host_has", lambda program: False):
            self.assertIn("Python", standalone.runner_problem(standalone.runner_at(str(self.proton()))))

    def test_launch_with_proton(self):
        self.in_flatpak(False)
        proton = self.proton()
        touch(self.tmp / "home" / ".local" / "share" / "Steam" / "steam.sh")
        mod = standalone.load(self.folder, settings={"runner": str(proton)})
        with mock.patch("subprocess.Popen") as popen:
            standalone.launch(mod)
        # Proton only finds a program by its whole path.
        self.assertEqual(popen.call_args.args[0],
                         ["python3", str(proton / "proton"), "waitforexitandrun", str(self.folder / "bin/XR_3DA.exe")])
        env = popen.call_args.kwargs["env"]
        self.assertEqual(env["STEAM_COMPAT_DATA_PATH"], str(mod.compat_dir))
        self.assertEqual(env["STEAM_COMPAT_CLIENT_INSTALL_PATH"], str(self.tmp / "home" / ".local" / "share" / "Steam"))
        self.assertEqual(mod.prefix, mod.compat_dir / "pfx")
        # Proton makes its prefix itself, and wouldn't fill one that's already there.
        self.assertTrue(mod.compat_dir.is_dir())
        self.assertFalse(mod.prefix.exists())

    def test_wine_and_proton_have_separate_prefixes(self):
        self.assertNotEqual(standalone.load(self.folder).prefix,
                            standalone.load(self.folder, settings={"runner": str(self.proton())}).prefix)

    def test_launch_with_a_wine_build(self):
        self.in_flatpak(False)
        build = self.tmp / "lutris-9"
        touch(build / "bin" / "wine")
        with mock.patch("subprocess.Popen") as popen:
            standalone.launch(standalone.load(self.folder, settings={"runner": str(build)}))
        self.assertEqual(popen.call_args.args[0], [str(build / "bin" / "wine"), "bin/XR_3DA.exe"])

    def test_stop_with_proton_ends_its_wineserver(self):
        self.in_flatpak(False)
        proton = self.proton()
        mod = standalone.load(self.folder, settings={"runner": str(proton)})
        with mock.patch("subprocess.Popen") as popen:
            standalone.stop(mod)
        self.assertEqual(popen.call_args.args[0], [str(proton / "files" / "bin" / "wineserver"), "-k"])
        self.assertEqual(popen.call_args.kwargs["env"]["WINEPREFIX"], str(mod.prefix))

    def test_user_settings_reach_the_game(self):
        self.in_flatpak(False)
        mod = standalone.load(self.folder, settings={"arguments": "-nointro -skin 'a b'", "overrides": "d3d9=b;",
                                                     "environment": "DXVK_HUD=fps NAME='a b'", "unknown": "x"})
        with mock.patch("subprocess.Popen") as popen:
            standalone.launch(mod)
        self.assertEqual(popen.call_args.args[0], ["wine", "bin/XR_3DA.exe", "-nointro", "-skin", "a b"])
        env = popen.call_args.kwargs["env"]
        self.assertEqual((env["DXVK_HUD"], env["NAME"]), ("fps", "a b"))
        # After the launcher's own d3d9=n,b: Wine goes by the last one.
        self.assertEqual(env["WINEDLLOVERRIDES"].split(";")[-1], "d3d9=b")

    def test_user_environment_must_be_variables(self):
        self.assertEqual(standalone.user_environment(""), {})
        for wrong in ("fps", "1A=b", "A='b"):
            with self.assertRaises(ValueError):
                standalone.user_environment(wrong)

    def directx_mod(self) -> standalone.Standalone:
        folder = self.tmp / "anomaly"
        write_program(folder / "bin" / "AnomalyDX11.exe", imports=["D3DCOMPILER_43.dll", "kernel32.dll"], bits=64)
        write_program(folder / "bin" / "AnomalyDX9.exe", imports=["d3dx9_43.dll"], bits=64)
        return standalone.load(folder, "bin/AnomalyDX11.exe")

    def redist(self, *names: str) -> Path:
        folder = (self.tmp / "home" / ".local" / "share" / "Steam" / "steamapps" / "common" / "Steamworks Shared"
                  / "_CommonRedist" / "DirectX" / "Jun2010")
        folder.mkdir(parents=True)
        for name in names:
            (folder / name).touch()
        return folder

    @staticmethod
    def fake_extract(archive: Path, dest: Path) -> None:
        """A cab with the one DLL its name says, of the bitness it says."""
        dll = re.search(r"_(\w+?)_x(64|86)", archive.name)
        write_program(dest / f"{dll[1]}.dll", bits=64 if dll[2] == "64" else 32)

    def test_libraries_a_mod_brings_itself_are_not_wanted(self):
        write_program(self.folder / "bin" / "XR_3DA.exe", imports=["d3dx9_34.dll", "D3DCompiler_43.dll", "d3d9.dll"])
        write_program(self.folder / "bin" / "d3dx9_34.dll")
        mod = standalone.load(self.folder)
        self.assertEqual(standalone.wanted_libraries(mod), {"d3dcompiler_43.dll"})
        write_program(self.folder / "bin" / "D3DCompiler_43.dll")
        self.assertEqual(standalone.wanted_libraries(mod), set())

    def test_a_programs_renderers_count_though_it_loads_them_later(self):
        folder = self.tmp / "coc"
        write_program(folder / "bin" / "xrEngine.exe", imports=["kernel32.dll"])
        write_program(folder / "bin" / "xrRender_R4.dll", imports=["d3dx11_43.dll"])
        self.assertEqual(standalone.wanted_libraries(standalone.load(folder, "bin/xrEngine.exe")),
                         {"d3dx11_43.dll", "d3dcompiler_43.dll"})

    def test_d3dx9_wants_the_old_compiler_it_hands_old_shaders_to(self):
        folder = self.tmp / "coc"
        write_program(folder / "bin" / "xrEngine.exe", imports=["d3dx9_43.dll"])
        mod = standalone.load(folder, "bin/xrEngine.exe")
        self.assertEqual(standalone.wanted_libraries(mod), {"d3dx9_43.dll", "d3dcompiler_43.dll", "d3dx9_31.dll"})
        # A mod that brings it needs nothing from Steam for it.
        write_program(folder / "bin" / "d3dx9_31.dll")
        self.assertNotIn("d3dx9_31.dll", standalone.wanted_libraries(mod))

    def test_app_data_relative_to_the_game_folder(self):
        (self.folder / "fsgame.ltx").write_text("$app_data_root$ \t\t= true| false| appdata\\\n")
        self.assertEqual(standalone.load(self.folder).app_data, self.folder / "appdata")

    def test_database_folder_is_game_data(self):
        folder = self.tmp / "coc"
        touch(folder / "database" / "config" / "ai.db")
        self.assertEqual(standalone._game_data(folder), "The database folder")

    def windows_setup(self) -> tuple[Path, Path]:
        game = self.tmp / "SSD" / "Clear Sky"
        write_program(game / "bin" / "xrEngine.exe")
        touch(game / "resources" / "resources.db0")
        touch(game / "goggame-1.info")
        touch(game / "fsgame.ltx")
        fsgame = self.tmp / "fsgame.ltx"
        fsgame.write_text("$app_data_root$ = true| false| $fs_root$| _appdata_\\\n")
        return game, fsgame

    def test_windows_version_links_the_game_and_leaves_it_alone(self):
        game, fsgame = self.windows_setup()
        before = sorted(p.name for p in game.iterdir())
        folder = standalone.link_windows_version("cs", game, fsgame)
        self.assertTrue((folder / "resources").is_symlink())
        self.assertFalse((folder / "fsgame.ltx").is_symlink())
        self.assertEqual((folder / "fsgame.ltx").read_text(), fsgame.read_text())
        self.assertEqual(sorted(p.name for p in game.iterdir()), before)
        mod = standalone.load(folder)
        self.assertEqual(mod.exe, "bin/xrEngine.exe")
        self.assertEqual(mod.name, "Clear Sky (Windows)")
        self.assertEqual(standalone.windows_game(mod), "cs")
        self.assertEqual(mod.app_data, folder / "_appdata_")
        self.assertIsNone(standalone.windows_game(standalone.load(self.folder)))

    def test_a_language_pack_kept_elsewhere_is_linked_in(self):
        game, fsgame = self.windows_setup()
        touch(game / "localization" / "xefis_movies.db")
        pack = self.tmp / "gog-support" / "add" / "xenglish.db"
        touch(pack)
        folder = standalone.link_windows_version("cs", game, fsgame, [pack])
        self.assertFalse((folder / "localization").is_symlink())
        self.assertEqual(sorted(p.name for p in (folder / "localization").iterdir()), ["xefis_movies.db", "xenglish.db"])
        self.assertEqual(sorted(p.name for p in (game / "localization").iterdir()), ["xefis_movies.db"])
        # Changing language swaps the pack.
        other = self.tmp / "gog-support" / "add" / "xpolish.db"
        touch(other)
        folder = standalone.link_windows_version("cs", game, fsgame, [other])
        self.assertEqual(sorted(p.name for p in (folder / "localization").iterdir()), ["xefis_movies.db", "xpolish.db"])

    def test_a_language_pack_kept_elsewhere_is_copied(self):
        game, _fsgame = self.windows_setup()
        pack = self.tmp / "gog-support" / "add" / "xenglish.db"
        touch(pack)
        target = self.tmp / "mod"
        self.assertIn("localization/xenglish.db", [rel for rel, _ in standalone.missing_files(game, target, [pack])])
        standalone.copy_missing(game, target, [pack])
        self.assertTrue((target / "localization" / "xenglish.db").is_file())
        self.assertEqual(standalone.missing_files(game, target, [pack]), [])

    def test_a_mods_icon_comes_from_its_launcher_and_is_kept(self):
        write_program(self.folder / "bin" / "xrEngine.exe")
        write_program(self.folder / "Stalker-CoC.exe")
        mod = standalone.load(self.folder)
        with mock.patch.object(standalone.pe, "icon", side_effect=lambda p: b"png" if p.name == "Stalker-CoC.exe" else None) as icon:
            self.assertEqual(mod.icon.read_bytes(), b"png")
            self.assertEqual(mod.icon, mod.data_dir / "icon.png")
            self.assertEqual(icon.call_count, 1)

    def test_a_mods_own_picture_comes_first(self):
        touch(self.folder / "Linux" / "icon.png")
        self.assertEqual(standalone.load(self.folder).icon, self.folder / "Linux" / "icon.png")

    def test_a_mod_with_no_icon_is_only_looked_at_once(self):
        write_program(self.folder / "bin" / "xrEngine.exe")
        mod = standalone.load(self.folder)
        with mock.patch.object(standalone.pe, "icon", return_value=None) as icon:
            self.assertIsNone(mod.icon)
            calls = icon.call_count
            self.assertIsNone(mod.icon)
            self.assertEqual(icon.call_count, calls)

    def test_a_games_dedicated_server_is_not_its_launcher(self):
        game, fsgame = self.windows_setup()
        write_program(game / "bin" / "dedicated" / "xrEngine.exe")
        folder = standalone.link_windows_version("cs", game, fsgame)
        self.assertEqual(standalone.find_launchers(folder), ["bin/xrEngine.exe"])

    def test_windows_version_follows_the_game_to_another_folder(self):
        game, fsgame = self.windows_setup()
        folder = standalone.link_windows_version("cs", game, fsgame)
        touch(folder / "_appdata_" / "savedgames" / "a.scop")
        other = self.tmp / "Other" / "Clear Sky"
        other.parent.mkdir()
        game.rename(other)
        folder = standalone.link_windows_version("cs", other, fsgame)
        self.assertEqual(Path(os.readlink(folder / "bin")), other / "bin")
        self.assertTrue((folder / "_appdata_" / "savedgames" / "a.scop").is_file())

    def test_missing_files_are_copied_without_overwriting(self):
        game, _fsgame = self.windows_setup()
        (game / "readme.txt").write_text("the game's")
        target = self.tmp / "mod"
        touch(target / "gamedata" / "a.ltx")
        (target / "readme.txt").parent.mkdir(exist_ok=True)
        (target / "readme.txt").write_text("the mod's")
        missing = [rel for rel, _size in standalone.missing_files(game, target)]
        self.assertEqual(sorted(missing), ["bin/xrEngine.exe", "resources/resources.db0"])
        self.assertEqual(standalone.copy_missing(game, target), 2)
        self.assertEqual((target / "readme.txt").read_text(), "the mod's")
        self.assertTrue((target / "resources" / "resources.db0").is_file())
        # The store's files and the game's file list aren't the mod's business.
        self.assertFalse((target / "goggame-1.info").exists())
        self.assertFalse((target / "fsgame.ltx").exists())
        self.assertEqual(standalone.missing_files(game, target), [])

    def test_missing_directx_libraries_are_reported(self):
        mod = self.directx_mod()
        with mock.patch.object(standalone, "runner_problem", lambda r: None), \
                mock.patch.object(standalone, "wine_libraries", lambda r: set()):
            self.assertIn("d3dcompiler_43.dll", {c.title: c for c in standalone.check(mod)}["DirectX libraries"].detail)
            write_program(mod.libraries_dir / "64" / "d3dcompiler_43.dll", bits=64)
            self.assertNotIn("DirectX libraries", {c.title for c in standalone.check(mod)})

    def test_libraries_steam_has_are_no_problem(self):
        mod = self.directx_mod()
        self.redist("Jun2010_D3DCompiler_43_x64.cab")
        with mock.patch.object(standalone, "runner_problem", lambda r: None), \
                mock.patch.object(standalone, "wine_libraries", lambda r: set()):
            check = {c.title: c for c in standalone.check(mod)}["DirectX libraries"]
            self.assertTrue(check.ok)
            self.assertIn("when you press Play", check.detail)
            turned_off = standalone.load(mod.path, mod.exe, {"libraries": standalone.LIBRARIES_OFF})
            self.assertNotIn("DirectX libraries", {c.title for c in standalone.check(turned_off)})

    def test_play_installs_the_libraries_a_mod_needs(self):
        self.in_flatpak(False)
        mod = self.directx_mod()
        self.redist("Jun2010_D3DCompiler_43_x64.cab", "Jun2010_d3dx9_43_x64.cab")
        system = mod.prefix / "drive_c" / "windows" / "system32"
        system.mkdir(parents=True)
        (mod.prefix / "drive_c" / "windows" / "syswow64").mkdir()
        touch(mod.prefix / "system.reg")
        with mock.patch.object(standalone.mods, "extract", self.fake_extract):
            standalone.prepare(standalone.load(mod.path, mod.exe, {"libraries": standalone.LIBRARIES_OFF}))
            self.assertEqual(standalone.installed_libraries(mod), set())
            standalone.prepare(mod)
        self.assertTrue((system / "d3dcompiler_43.dll").is_file())
        self.assertTrue((system / "d3dx9_43.dll").is_file())

    def test_a_failed_install_does_not_stop_the_game(self):
        self.in_flatpak(False)
        mod = self.directx_mod()
        self.redist("Jun2010_D3DCompiler_43_x64.cab")
        with mock.patch.object(standalone.mods, "extract", side_effect=standalone.mods.ModError("damaged")), \
                mock.patch("subprocess.Popen") as popen:
            standalone.prepare(mod)
        popen.assert_not_called()

    def test_older_compilers_come_with_d3dx10(self):
        self.redist("Mar2008_d3dx9_37_x86.cab", "Mar2008_d3dx10_37_x86.cab", "Jun2010_D3DCompiler_43_x86.cab",
                    "Jun2010_d3dx10_43_x86.cab")
        found = standalone.redist_cabs({"d3dcompiler_37.dll", "d3dcompiler_43.dll", "d3dcompiler_36.dll"})
        self.assertEqual([cab.name for cab in found["d3dcompiler_37.dll"]], ["Mar2008_d3dx10_37_x86.cab"])
        # One with its own installer doesn't bring in d3dx10's as well.
        self.assertEqual([cab.name for cab in found["d3dcompiler_43.dll"]], ["Jun2010_D3DCompiler_43_x86.cab"])
        self.assertNotIn("d3dcompiler_36.dll", found)

    def test_install_libraries_from_steam(self):
        mod = self.directx_mod()
        self.assertEqual(standalone.libraries_to_install(mod), {"d3dcompiler_43.dll", "d3dx9_43.dll", "d3dx9_31.dll"})
        self.redist("Jun2010_D3DCompiler_43_x64.cab", "Jun2010_D3DCompiler_43_x86.cab", "Jun2010_d3dx10_43_x64.cab")
        with mock.patch.object(standalone.mods, "extract", self.fake_extract):
            installed, absent = standalone.install_libraries(mod)
        self.assertEqual((installed, absent), (["d3dcompiler_43.dll"], ["d3dx9_31.dll", "d3dx9_43.dll"]))
        self.assertTrue((mod.libraries_dir / "64" / "d3dcompiler_43.dll").is_file())
        self.assertTrue((mod.libraries_dir / "32" / "d3dcompiler_43.dll").is_file())
        self.assertFalse((mod.libraries_dir / "64" / "d3dx10_43.dll").exists())
        self.assertIn("d3dcompiler_43=n,b", standalone.wine_environment(mod)["WINEDLLOVERRIDES"].split(";"))
        self.assertEqual(standalone.libraries_to_install(mod), {"d3dx9_43.dll", "d3dx9_31.dll"})

    def test_add_a_library_by_hand(self):
        mod = self.directx_mod()
        write_program(self.tmp / "D3DX9_43.DLL", bits=64)
        self.assertEqual(standalone.add_library_file(mod, self.tmp / "D3DX9_43.DLL"), ["d3dx9_43.dll"])
        self.assertTrue((mod.libraries_dir / "64" / "d3dx9_43.dll").is_file())
        touch(self.tmp / "text.dll")
        self.assertEqual(standalone.add_library_file(mod, self.tmp / "text.dll"), [])

    def test_place_libraries_replaces_links_to_protons_files(self):
        mod = standalone.load(self.folder, settings={"runner": str(self.proton())})
        write_program(mod.libraries_dir / "64" / "d3dcompiler_43.dll", bits=64)
        write_program(mod.libraries_dir / "32" / "d3dcompiler_43.dll", bits=32)
        original = self.tmp / "proton's own.dll"
        original.write_text("Proton's")
        windows = mod.prefix / "drive_c" / "windows"
        (windows / "system32").mkdir(parents=True)
        (windows / "syswow64").mkdir()
        (windows / "system32" / "d3dcompiler_43.dll").symlink_to(original)
        standalone.place_libraries(mod)
        self.assertEqual(original.read_text(), "Proton's")
        self.assertFalse((windows / "system32" / "d3dcompiler_43.dll").is_symlink())
        self.assertEqual(pe.read(windows / "system32" / "d3dcompiler_43.dll").bits, 64)
        self.assertEqual(pe.read(windows / "syswow64" / "d3dcompiler_43.dll").bits, 32)

    def test_place_libraries_in_a_32_bit_prefix(self):
        mod = standalone.load(self.folder)
        write_program(mod.libraries_dir / "64" / "d3dcompiler_43.dll", bits=64)
        write_program(mod.libraries_dir / "32" / "d3dcompiler_43.dll", bits=32)
        system = mod.prefix / "drive_c" / "windows" / "system32"
        system.mkdir(parents=True)
        standalone.place_libraries(mod)
        self.assertEqual(pe.read(system / "d3dcompiler_43.dll").bits, 32)

    def test_remove_libraries(self):
        mod = standalone.load(self.folder)
        write_program(mod.libraries_dir / "64" / "d3dcompiler_43.dll", bits=64)
        touch(mod.prefix / "drive_c" / "windows" / "system32" / "d3dcompiler_43.dll")
        standalone.remove_libraries(mod)
        self.assertEqual(standalone.installed_libraries(mod), set())
        self.assertFalse((mod.prefix / "drive_c" / "windows" / "system32" / "d3dcompiler_43.dll").exists())

    def test_prepare_makes_a_new_prefix_before_placing_libraries(self):
        self.in_flatpak(False)
        mod = standalone.load(self.folder)
        with mock.patch("subprocess.Popen") as popen:
            standalone.prepare(mod)
            popen.assert_not_called()  # nothing to place: Wine makes the prefix when the game starts
            write_program(mod.libraries_dir / "32" / "d3dcompiler_43.dll", bits=32)

            def made(*_a, **_k):
                touch(mod.prefix / "system.reg")
                (mod.prefix / "drive_c" / "windows" / "system32").mkdir(parents=True)
                return mock.Mock()

            popen.side_effect = made
            standalone.prepare(mod)
            self.assertEqual(popen.call_args.args[0], ["wine", "wineboot", "-i"])
            self.assertTrue((mod.prefix / "drive_c" / "windows" / "system32" / "d3dcompiler_43.dll").is_file())
            popen.reset_mock()
            standalone.prepare(mod)
            popen.assert_not_called()

    def test_shader_failure_in_a_log(self):
        self.assertIsNone(standalone.shader_failure("! error:  :759:21: W5300: Implicit truncation of vector type.\n"))
        self.assertEqual(standalone.shader_failure(":1168:38: E5017: Aborting due to not yet implemented feature.\n"),
                         "E5017: Aborting due to not yet implemented feature.")


if __name__ == "__main__":
    unittest.main()
