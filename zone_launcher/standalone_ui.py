"""The Manage dialog of a standalone mod: which program starts it, what runs it, what it needs, and its files."""

import shlex
import threading
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from . import files, mods, standalone  # noqa: E402
from .files_ui import FilesDialog  # noqa: E402


class ManageDialog(Adw.PreferencesDialog):
    def __init__(self, window, mod: standalone.Standalone):
        super().__init__(title=mod.name, search_enabled=False)
        self.window = window
        self.mod = mod
        self.chosen = window.config.standalone_launchers.get(str(mod.path))
        page = Adw.PreferencesPage()
        self.add(page)

        launcher_group = Adw.PreferencesGroup(title="Launcher",
                                              description="The program Play starts. Popular engines are picked "
                                                          "automatically.")
        pick = Gtk.Button(label="Choose File…", css_classes=["flat"], valign=Gtk.Align.CENTER,
                          tooltip_text="Pick any Windows program in the mod's folder")
        pick.connect("clicked", self.on_pick)
        launcher_group.set_header_suffix(pick)
        self.launcher_row = Adw.ComboRow(title="Starts with", use_subtitle=True)
        launcher_group.add(self.launcher_row)
        page.add(launcher_group)
        self.fill_launchers()

        self.checks_group = Adw.PreferencesGroup(title="Required files")
        self.check_rows: list[Adw.ActionRow] = []
        page.add(self.checks_group)
        self.fill_checks()

        page.add(self.build_wine_group())

        files_group = Adw.PreferencesGroup(title="Files")
        browse = Adw.ActionRow(title="Browse Files", subtitle="Saves, settings, logs and the mod's own files",
                               activatable=True)
        browse.add_prefix(Gtk.Image(icon_name="folder-symbolic"))
        browse.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
        browse.connect("activated", self.on_browse)
        files_group.add(browse)
        files_group.add(self.open_row("Open Mod Folder", "folder-open-symbolic", mod.path))
        if mod.wine_log.is_file():
            files_group.add(self.open_row("Wine's Log", "text-x-generic-symbolic", mod.wine_log))
        page.add(files_group)

        remove_group = Adw.PreferencesGroup()
        forget = Adw.ActionRow(title="Remove From List", subtitle="Keeps the mod's files", activatable=True)
        forget.add_prefix(Gtk.Image(icon_name="list-remove-symbolic"))
        forget.connect("activated", self.on_remove)
        remove_group.add(forget)
        trash = Adw.ActionRow(title="Move Mod Folder to Trash", subtitle="Includes its saves", activatable=True,
                              css_classes=["error"])
        trash.add_prefix(Gtk.Image(icon_name="user-trash-symbolic"))
        trash.connect("activated", self.on_trash)
        remove_group.add(trash)
        page.add(remove_group)

    # ----- the launcher ----------------------------------------------------------------------------

    def fill_launchers(self):
        found = standalone.find_launchers(self.mod.path)
        best = found[0] if found else None
        # The automatic choice first, then everything found, and a file picked by hand.
        self.options: list[str | None] = [None, *found]
        if self.chosen and self.chosen not in found:
            self.options.append(self.chosen)
        labels = [f"Automatic ({best})" if best else "Automatic (none found)", *self.options[1:]]
        # The handler would fire for what's already chosen, and while it's being filled.
        if handler := getattr(self, "launcher_handler", None):
            self.launcher_row.disconnect(handler)
        self.launcher_row.set_model(Gtk.StringList.new(labels))
        self.launcher_row.set_selected(self.options.index(self.chosen) if self.chosen in self.options else 0)
        self.launcher_handler = self.launcher_row.connect("notify::selected", self.on_launcher)

    def on_launcher(self, row, _pspec):
        self.set_launcher(self.options[row.get_selected()])

    def set_launcher(self, launcher: str | None):
        self.chosen = launcher
        self.window.choose_standalone_launcher(self.mod, launcher)
        self.mod = self.window.load_standalone(str(self.mod.path))
        self.fill_checks()

    def on_pick(self, _btn):
        windows = Gtk.FileFilter(name="Windows programs")
        windows.add_suffix("exe")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(windows)
        Gtk.FileDialog(title="Choose the program that starts the mod", filters=filters, default_filter=windows,
                       initial_folder=Gio.File.new_for_path(str(self.mod.path))).open(
            self.window, None, self.on_picked)

    def on_picked(self, dialog, result):
        try:
            picked = dialog.open_finish(result).get_path()
        except GLib.Error:
            return  # cancelled
        try:
            rel = Path(picked).relative_to(self.mod.path).as_posix()
        except (TypeError, ValueError):
            self.add_toast(Adw.Toast(title="Choose a program inside the mod's folder"))
            return
        self.chosen = rel
        self.fill_launchers()
        self.set_launcher(rel)

    # ----- what runs it -----------------------------------------------------------------------------

    def build_wine_group(self) -> Adw.PreferencesGroup:
        recommendation = ("Automatic uses the best you have: CachyOS Proton, then Proton Experimental, GE-Proton and "
                          "Steam's other versions." if standalone.best_runner().is_proton else
                          "For the smoothest games, install Proton Experimental with Steam, or proton-cachyos. "
                          "Automatic will use it.")
        group = Adw.PreferencesGroup(title="Wine and Proton", description=recommendation)
        pick = Gtk.Button(label="Choose Folder…", css_classes=["flat"], valign=Gtk.Align.CENTER,
                          tooltip_text="Pick the folder of a Wine build or a version of Proton")
        pick.connect("clicked", self.on_pick_runner)
        group.set_header_suffix(pick)
        self.runner_row = Adw.ComboRow(title="Runs with", use_subtitle=True)
        group.add(self.runner_row)
        self.fill_runners()

        self.libraries_row = Adw.ActionRow(title="Microsoft's DirectX libraries", use_markup=False)
        self.libraries_spinner = Gtk.Spinner(visible=False, valign=Gtk.Align.CENTER)
        self.install_libraries = Gtk.Button(label="Install Now", valign=Gtk.Align.CENTER)
        self.install_libraries.connect("clicked", self.on_install_libraries)
        self.remove_libraries = Gtk.Button(label="Remove", valign=Gtk.Align.CENTER)
        self.remove_libraries.connect("clicked", self.on_remove_libraries)
        self.choose_libraries = Gtk.Button(label="Choose Files…", valign=Gtk.Align.CENTER,
                                           tooltip_text="Pick DLLs, or the .cab files of Microsoft's DirectX installer")
        self.choose_libraries.connect("clicked", self.on_choose_libraries)
        for widget in (self.libraries_spinner, self.install_libraries, self.remove_libraries, self.choose_libraries):
            self.libraries_row.add_suffix(widget)
        group.add(self.libraries_row)
        self.fill_libraries()

        group.add(self.setting_row("Launch arguments", "arguments", "Passed to the program, such as -nointro",
                                   shlex.split))
        group.add(self.setting_row("DLL overrides", "overrides", "WINEDLLOVERRIDES entries, such as d3d11=n,b;dxgi=n,b"))
        group.add(self.setting_row("Environment variables", "environment", "Such as DXVK_HUD=fps MANGOHUD=1",
                                   standalone.user_environment))

        configure = Adw.ActionRow(title="Wine Configuration", activatable=True)
        configure.add_prefix(Gtk.Image(icon_name="emblem-system-symbolic"))
        configure.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
        configure.connect("activated", self.on_configure)
        group.add(configure)
        self.prefix_folder_row = Adw.ActionRow(title="Open Prefix Folder", activatable=True)
        self.prefix_folder_row.add_prefix(Gtk.Image(icon_name="folder-open-symbolic"))
        self.prefix_folder_row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
        self.prefix_folder_row.connect("activated", lambda *_: self.window.open_file(self.mod.prefix))
        group.add(self.prefix_folder_row)
        self.reset_row = Adw.ActionRow(title="Reset Prefix", subtitle="Start again with a new one. Saves stay in the "
                                                                      "mod's folder", activatable=True)
        self.reset_row.add_prefix(Gtk.Image(icon_name="edit-clear-all-symbolic"))
        self.reset_row.connect("activated", self.on_reset_prefix)
        group.add(self.reset_row)
        self.show_prefix_rows()
        return group

    def fill_runners(self):
        found = standalone.find_runners()
        # The automatic choice first, then every version of Proton, Wine, and a folder picked by hand.
        self.runner_options = [("", f"Automatic: {standalone.best_runner().name} (recommended)")]
        self.runner_options += [(str(r.path), r.name) for r in found if r.is_proton]
        version = standalone.wine_version()
        self.runner_options.append((standalone.SYSTEM_RUNNER,
                                    f"{standalone.SYSTEM_WINE} ({version or 'not installed'})"))
        self.runner_options += [(str(r.path), r.name) for r in found if not r.is_proton]
        current = self.mod.settings.runner
        settings = [setting for setting, _label in self.runner_options]
        if current not in settings:
            self.runner_options.append((current, Path(current).name))
            settings.append(current)
        if handler := getattr(self, "runner_handler", None):
            self.runner_row.disconnect(handler)
        self.runner_row.set_model(Gtk.StringList.new([label for _setting, label in self.runner_options]))
        self.runner_row.set_selected(settings.index(current))
        self.runner_handler = self.runner_row.connect("notify::selected", self.on_runner)

    def on_runner(self, row, _pspec):
        self.set_setting("runner", self.runner_options[row.get_selected()][0])

    def on_pick_runner(self, _btn):
        Gtk.FileDialog(title="Choose the folder of a Wine build or a version of Proton").select_folder(
            self.window, None, self.on_runner_picked)

    def on_runner_picked(self, dialog, result):
        try:
            picked = Path(dialog.select_folder_finish(result).get_path())
        except (GLib.Error, TypeError):
            return  # cancelled, or a place with no path
        if not standalone.is_runner(picked):
            self.add_toast(Adw.Toast(title="That folder has no proton file or bin/wine"))
            return
        self.set_setting("runner", str(picked))
        self.fill_runners()

    def setting_row(self, title: str, name: str, hint: str, validate=None) -> Adw.EntryRow:
        row = Adw.EntryRow(title=title, tooltip_text=hint, show_apply_button=True, use_markup=False,
                           text=getattr(self.mod.settings, name))

        def apply(_row):
            text = row.get_text().strip()
            try:
                if validate:
                    validate(text)
            except ValueError as e:
                row.add_css_class("error")
                self.add_toast(Adw.Toast(title=f"{title}: {e}"))
                return
            self.set_setting(name, text)

        row.connect("apply", apply)
        row.connect("changed", lambda *_: row.remove_css_class("error"))
        return row

    def set_setting(self, name: str, value: str):
        self.window.change_standalone_setting(self.mod, name, value)
        self.mod = self.window.load_standalone(str(self.mod.path))
        self.fill_checks()
        self.show_prefix_rows()

    def fill_libraries(self):
        installed = sorted(standalone.installed_libraries(self.mod))
        wanted = sorted(standalone.libraries_to_install(self.mod))
        found = standalone.redist_cabs(set(wanted))
        off = self.mod.settings.libraries == standalone.LIBRARIES_OFF
        if wanted and off:
            subtitle = f"Turned off, so {', '.join(wanted)} come from Wine or Proton. They may not compile every shader."
        elif wanted and len(found) < len(wanted):
            subtitle = f"This mod uses {', '.join(wanted)}. Steam's DirectX installer wasn't found: choose them yourself."
        elif wanted:
            subtitle = f"Installed from your Steam folder when you press Play: {', '.join(wanted)}"
        elif installed:
            subtitle = f"Installed for this mod: {', '.join(installed)}"
        else:
            subtitle = "This mod doesn't need any, or brings its own."
        self.libraries_row.set_subtitle(subtitle)
        self.install_libraries.set_visible(bool(found))
        self.remove_libraries.set_label("Remove" if installed else "Turn Off")
        self.remove_libraries.set_visible(bool(installed or (wanted and not off)))

    def set_libraries_busy(self, busy: bool):
        self.libraries_spinner.set_visible(busy)
        self.libraries_spinner.set_spinning(busy)
        for button in (self.install_libraries, self.remove_libraries, self.choose_libraries):
            button.set_sensitive(not busy)

    def in_background(self, work, done):
        """Runs work away from the window, then done(its result, or the error it stopped with)."""
        self.set_libraries_busy(True)

        def run():
            try:
                outcome = work(), None
            except (mods.ModError, OSError) as e:
                outcome = None, str(e)
            GLib.idle_add(finish, *outcome)

        def finish(result, error):
            self.set_libraries_busy(False)
            self.fill_libraries()
            self.fill_checks()
            if error:
                self.add_toast(Adw.Toast(title=f"Couldn't install them: {error}"))
            else:
                done(result)

        threading.Thread(target=run, daemon=True).start()

    def on_install_libraries(self, _btn):
        def done(result):
            installed, absent = result
            self.add_toast(Adw.Toast(title=f"Not found in Steam's folders: {', '.join(absent)}" if absent
                                     else f"Installed {', '.join(installed)}"))

        self.set_setting("libraries", "")
        self.in_background(lambda: standalone.install_libraries(self.mod), done)

    def on_choose_libraries(self, _btn):
        libraries = Gtk.FileFilter(name="DLLs and .cab files")
        libraries.add_suffix("dll")
        libraries.add_suffix("cab")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(libraries)
        Gtk.FileDialog(title="Choose Windows libraries for this mod", filters=filters,
                       default_filter=libraries).open_multiple(self.window, None, self.on_libraries_chosen)

    def on_libraries_chosen(self, dialog, result):
        try:
            chosen = dialog.open_multiple_finish(result)
        except GLib.Error:
            return  # cancelled
        paths = [Path(chosen.get_item(i).get_path()) for i in range(chosen.get_n_items())]
        self.set_setting("libraries", "")

        def done(kept):
            self.add_toast(Adw.Toast(title=f"Installed {', '.join(kept)}" if kept
                                     else "Those aren't Windows libraries"))

        self.in_background(lambda: sorted({n for p in paths for n in standalone.add_library_file(self.mod, p)}), done)

    def on_remove_libraries(self, _btn):
        try:
            standalone.remove_libraries(self.mod)
        except OSError as e:
            self.add_toast(Adw.Toast(title=f"Couldn't remove them: {e.strerror or e}"))
        self.set_setting("libraries", standalone.LIBRARIES_OFF)
        self.fill_libraries()

    def show_prefix_rows(self):
        made = self.mod.prefix.exists()
        self.prefix_folder_row.set_visible(made)
        self.reset_row.set_visible(made)

    def on_configure(self, *_):
        new = not standalone.prefix_ready(self.mod)
        try:
            standalone.configure(self.mod)
        except OSError as e:
            self.add_toast(Adw.Toast(title=f"Couldn't start {self.mod.runner.name}: {e.strerror or e}"))
            return
        self.show_prefix_rows()
        if new:
            self.add_toast(Adw.Toast(title="Setting up Wine for this mod. It can take a minute to open."))

    def on_reset_prefix(self, *_):
        mod = self.mod
        dialog = Adw.AlertDialog(heading="Reset the prefix?",
                                 body=f"This moves {mod.name}'s Wine prefix to the trash, with anything installed in "
                                      "it by hand. The next start makes a new one. Saves stay in the mod's folder, and "
                                      "the DirectX libraries installed here go into the new prefix.")
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("reset", "Reset")
        dialog.set_response_appearance("reset", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def response(_d, answer):
            if answer != "reset":
                return
            try:
                Gio.File.new_for_path(str(mod.compat_dir if mod.runner.is_proton else mod.prefix)).trash(None)
            except GLib.Error as e:
                self.window.alert("Couldn't move it to the trash", e.message)
                return
            self.show_prefix_rows()
            self.add_toast(Adw.Toast(title="The next start makes a new prefix"))

        dialog.connect("response", response)
        dialog.present(self.window)

    # ----- what it needs ---------------------------------------------------------------------------

    def fill_checks(self):
        for row in self.check_rows:
            self.checks_group.remove(row)
        self.check_rows = []
        for check in standalone.check(self.mod):
            row = Adw.ActionRow(title=check.title, subtitle=check.detail, use_markup=False)
            row.add_prefix(Gtk.Image(icon_name="object-select-symbolic" if check.ok else "dialog-warning-symbolic",
                                     css_classes=["success" if check.ok else "warning"]))
            self.checks_group.add(row)
            self.check_rows.append(row)

    # ----- its files -------------------------------------------------------------------------------

    def open_row(self, title: str, icon: str, path: Path) -> Adw.ActionRow:
        row = Adw.ActionRow(title=title, activatable=True)
        row.add_prefix(Gtk.Image(icon_name=icon))
        row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
        row.connect("activated", lambda *_: self.window.open_file(path))
        return row

    def on_browse(self, *_):
        mod = self.mod
        protected = {mod.path / "fsgame.ltx"} | ({mod.path / mod.exe} if mod.exe else set())
        places = [("Mod folder", mod.path), ("Saved games", mod.app_data / "savedgames"),
                  ("Settings and logs", mod.app_data)]
        FilesDialog(f"{mod.name} files", places,
                    files.StandaloneClassifier(mod.path, mod.app_data, protected)).present(self.window)

    def on_remove(self, *_):
        self.window.remove_standalone(self.mod)
        self.window.toast(f"Removed {self.mod.name} from the list. Its files are still in its folder.")
        self.close()

    def on_trash(self, *_):
        dialog = Adw.AlertDialog(heading=f"Move {self.mod.name} to the trash?",
                                 body=f"This moves the whole folder, with its saves, to the trash:\n\n{self.mod.path}")
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("trash", "Move to Trash")
        dialog.set_response_appearance("trash", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def response(_d, answer):
            if answer != "trash":
                return
            try:
                Gio.File.new_for_path(str(self.mod.path)).trash(None)
            except GLib.Error as e:
                self.window.alert("Couldn't move it to the trash",
                                  f"{e.message}\n\nThe mod's files are unchanged. A drive without a trash folder "
                                  "can cause this: delete the folder with your file manager instead.")
                return
            self.window.remove_standalone(self.mod)
            self.window.toast(f"Moved {self.mod.name} to the trash")
            self.close()

        dialog.connect("response", response)
        dialog.present(self.window)
