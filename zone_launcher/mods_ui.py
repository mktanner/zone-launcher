"""The mods window for one game: browse ModDB, install, switch on and off, order and remove mods."""

import threading
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from . import downloads, files, mods  # noqa: E402
from .games import GAMES  # noqa: E402
from .moddb_ui import BrowsePage, Downloads, installed_message, open_uri, pending_row  # noqa: E402


class ModsDialog(Adw.PreferencesDialog):
    def __init__(self, game: str, downloads: Downloads, on_change=None):
        # Tall enough for ModDB's lists; smaller windows shrink it.
        super().__init__(title=f"{GAMES[game].title} mods", search_enabled=False, content_width=720,
                         content_height=680)
        self.game = game
        self.downloads = downloads
        self.on_change = on_change
        self.installing: str | None = None
        self.page = Adw.PreferencesPage()
        self.add(self.page)
        self.groups: list[Adw.PreferencesGroup] = []

        drop = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        drop.connect("drop", self.on_drop)
        self.add_controller(drop)
        # Downloads finish (and get installed) while this window is open.
        self.connect("closed", downloads.listen(self.on_download))
        self.refresh()

    # ----- building ----------------------------------------------------------------------------

    def refresh(self):
        for group in self.groups:
            self.page.remove(group)
        self.groups = [self.build_install_group(), self.build_downloads_group(), self.build_list_group()]
        self.groups = [g for g in self.groups if g]
        for group in self.groups:
            self.page.add(group)

    def build_install_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(
            description="Mods are applied when you press Play. Changing mods in the middle of a "
                        "playthrough can break your saves.")
        buttons = Gtk.Box(spacing=6, halign=Gtk.Align.START)
        browse = Gtk.Button(label="Browse ModDB…", css_classes=["suggested-action"],
                            tooltip_text="Find mods on ModDB, download them in your browser, and they're installed "
                                         "when the download finishes")
        browse.connect("clicked", lambda *_: self.push_subpage(BrowsePage(self, self.game, self.downloads)))
        buttons.append(browse)
        for label, handler in (("Install from file…", self.on_install_file),
                               ("Install from folder…", self.on_install_folder)):
            btn = Gtk.Button(label=label)
            btn.connect("clicked", handler)
            btn.set_sensitive(self.installing is None)
            buttons.append(btn)
        group.add(buttons)
        if self.installing:
            row = Adw.ActionRow(title=f"Installing {self.installing}…",
                                subtitle="Unpacking can take a minute for big mods.", use_markup=False)
            spinner = Gtk.Spinner(spinning=True)
            row.add_prefix(spinner)
            group.add(row)
        return group

    def build_downloads_group(self) -> Adw.PreferencesGroup | None:
        busy, waiting = self.downloads.busy(self.game), self.downloads.waiting(self.game)
        if not busy and not waiting:
            return None
        group = Adw.PreferencesGroup(title="Downloads")
        for pending in busy:
            group.add(pending_row(self, self.downloads, pending, installing=True))
        for pending in waiting:
            group.add(pending_row(self, self.downloads, pending, installing=False))
        return group

    def build_list_group(self) -> Adw.PreferencesGroup:
        installed = list(reversed(mods.list_mods(self.game)))  # highest priority first
        group = Adw.PreferencesGroup(
            title="Installed",
            description="Higher in the list wins when two mods change the same file." if len(installed) > 1 else "")
        if not installed:
            group.add(Adw.ActionRow(title="No mods yet",
                                    subtitle="Browse ModDB, install a zip, 7z or rar mod archive, or drop one here."))
            return group
        conflicts = mods.conflicts(self.game)
        names = {m.id: m.name for m in installed}
        for position, mod in enumerate(installed):
            group.add(self.build_mod_row(mod, position, len(installed), conflicts.get(mod.id, {}), names))
        return group

    def build_mod_row(self, mod: mods.Mod, position: int, count: int, beats: dict[str, int],
                      names: dict[str, str]) -> Adw.ExpanderRow:
        main = mod.components[0].path
        options = mod.components[1:]
        source = mod.source.get("site")
        if source:
            subtitle = f"From {source}{': ' + mod.source['mod'] if mod.source.get('mod') else ''} · installed {mod.installed}"
        else:
            subtitle = f"Installed {mod.installed} from {mod.archive}"
        subtitle += f" · {files.human_size(mods.disk_size(self.game, mod))}"
        if mod.engine:
            subtitle = f"Needs its own engine: probably won't work · {subtitle}"
        row = Adw.ExpanderRow(title=mod.name, use_markup=False, subtitle=subtitle)
        if mod.engine:
            row.add_suffix(Gtk.Image(icon_name="dialog-warning-symbolic", valign=Gtk.Align.CENTER,
                                     tooltip_text="Made for its own modified engine, which OpenXRay replaces. "
                                                  "Expand for details."))
        delete = Gtk.Button(icon_name="user-trash-symbolic", tooltip_text="Delete this mod", valign=Gtk.Align.CENTER,
                            css_classes=["flat"])
        delete.connect("clicked", lambda *_: self.on_remove(mod))
        row.add_suffix(delete)
        switch = Gtk.Switch(active=main in mod.enabled, valign=Gtk.Align.CENTER)
        switch.connect("notify::active", self.on_mod_switched, mod)
        row.add_suffix(switch)

        for warning in mod.warnings:
            warn = Adw.ActionRow(subtitle=warning, use_markup=False)
            warn.add_prefix(Gtk.Image(icon_name="dialog-warning-symbolic"))
            row.add_row(warn)
        for other, replaced in beats.items():
            info = Adw.ActionRow(subtitle=f"Replaces {replaced} file{'s' if replaced != 1 else ''} from "
                                          f"{names.get(other, other)}", use_markup=False)
            info.add_prefix(Gtk.Image(icon_name="dialog-information-symbolic"))
            row.add_row(info)
        for comp in options:
            option = Adw.SwitchRow(title=comp.name, use_markup=False, subtitle="Optional part",
                                   active=comp.path in mod.enabled, sensitive=main in mod.enabled)
            option.connect("notify::active", self.on_option_switched, mod, comp.path)
            row.add_row(option)

        actions = Gtk.Box(spacing=6, margin_top=6, margin_bottom=6, margin_start=12, margin_end=12)
        for icon, tip, handler, sensitive in (
            ("go-up-symbolic", "Higher priority", lambda *_: self.on_move(mod, +1), position > 0),
            ("go-down-symbolic", "Lower priority", lambda *_: self.on_move(mod, -1), position < count - 1),
            ("folder-open-symbolic", "Open the mod's folder", lambda *_: self.on_open(mod), True),
        ) + ((("adw-external-link-symbolic", f"Open its page on {source}",
               lambda *_: open_uri(self, mod.source.get("page") or mod.source["file"]), True),) if source else ()):
            btn = Gtk.Button(icon_name=icon, tooltip_text=tip, sensitive=sensitive)
            btn.connect("clicked", handler)
            actions.append(btn)
        remove = Gtk.Button(label="Delete", hexpand=True, halign=Gtk.Align.END,
                            css_classes=["destructive-action"])
        remove.connect("clicked", lambda *_: self.on_remove(mod))
        actions.append(remove)
        row.add_row(actions)
        return row

    # ----- actions -----------------------------------------------------------------------------

    def changed(self, message: str | None = None):
        self.refresh()
        if message:
            self.add_toast(Adw.Toast(title=GLib.markup_escape_text(message), timeout=4))
        if self.on_change:
            self.on_change()

    def on_download(self, message: str | None):
        self.refresh()
        if message:
            self.add_toast(Adw.Toast(title=GLib.markup_escape_text(message), timeout=5))

    def on_mod_switched(self, switch, _pspec, mod: mods.Mod):
        main = mod.components[0].path
        if switch.get_active():
            mods.set_enabled(self.game, mod.id, [main])
        else:
            mods.set_enabled(self.game, mod.id, [])
        GLib.idle_add(self.changed, f"{mod.name} turned {'on' if switch.get_active() else 'off'}")

    def on_option_switched(self, row, _pspec, mod: mods.Mod, path: str):
        enabled = [p for p in mod.enabled if p != path]
        if row.get_active():
            enabled.append(path)
        mods.set_enabled(self.game, mod.id, enabled)
        GLib.idle_add(self.changed)

    def on_move(self, mod: mods.Mod, delta: int):
        mods.move(self.game, mod.id, delta)
        self.changed()

    def on_open(self, mod: mods.Mod):
        folder = Gio.File.new_for_path(str(mods.library_dir(self.game) / mod.id))
        Gtk.FileLauncher(file=folder).launch(self.get_root(), None, None)

    def on_remove(self, mod: mods.Mod):
        dialog = Adw.AlertDialog(heading=f"Delete {mod.name}?",
                                 body=f"Its files ({files.human_size(mods.disk_size(self.game, mod))}) are deleted "
                                      "from the mod library. The game itself isn't changed until you next press Play.")
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("remove", "Delete")
        dialog.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)
        archive = mods.downloaded_archive(mod, downloads.downloads_dir())
        also = None
        if archive:
            size = files.human_size(archive.stat().st_size)
            also = Gtk.CheckButton(label=f"Also move the downloaded {archive.name} ({size}) to the trash",
                                   tooltip_text=str(archive))
            dialog.set_extra_child(also)

        def response(_d, answer):
            if answer != "remove":
                return
            mods.remove(self.game, mod.id)
            message = f"Deleted {mod.name}"
            if also is not None and also.get_active():
                try:
                    Gio.File.new_for_path(str(archive)).trash(None)
                    message += f" and moved {archive.name} to the trash"
                except GLib.Error as e:
                    message += f", but couldn't move {archive.name} to the trash: {e.message}"
            self.changed(message)

        dialog.connect("response", response)
        dialog.present(self)

    def on_install_file(self, _btn):
        filters = Gio.ListStore.new(Gtk.FileFilter)
        archives = Gtk.FileFilter(name="Mod archives (zip, 7z, rar)")
        for suffix in mods.ARCHIVE_SUFFIXES:
            archives.add_suffix(suffix.lstrip("."))
        filters.append(archives)
        dialog = Gtk.FileDialog(title="Choose a mod archive", filters=filters)
        dialog.open(self.get_root(), None, self.on_chosen, "open_finish")

    def on_install_folder(self, _btn):
        Gtk.FileDialog(title="Choose an unpacked mod folder").select_folder(
            self.get_root(), None, self.on_chosen, "select_folder_finish")

    def on_chosen(self, dialog, result, finish):
        try:
            chosen = getattr(dialog, finish)(result)
        except GLib.Error:
            return  # cancelled
        if chosen and chosen.get_path():
            self.install(Path(chosen.get_path()))

    def on_drop(self, _target, value, _x, _y):
        for file in value.get_files():
            if file.get_path():
                self.install(Path(file.get_path()))
                return True
        return False

    def install(self, source: Path):
        if self.installing:
            return
        self.installing = source.name
        self.refresh()

        def work():
            try:
                mod = mods.install(self.game, source)
                message = installed_message(mod)
            except (mods.ModError, OSError) as e:
                message = f"Couldn't install {source.name}: {e}"
            GLib.idle_add(done, message)

        def done(message):
            self.installing = None
            self.changed(message)

        threading.Thread(target=work, daemon=True).start()
