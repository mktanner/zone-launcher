"""The file browser for one game: its folder, the original install, saves, and where each file comes from."""

from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Pango", "1.0")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

from . import files  # noqa: E402
from .detect import Install  # noqa: E402
from .games import GAMES  # noqa: E402

ICONS = {"missing": "dialog-warning-symbolic", "mod": "application-x-addon-symbolic",
         "saves": "document-save-symbolic", "launcher": "applications-system-symbolic"}


class FilesDialog(Adw.Dialog):
    def __init__(self, install: Install, run: Install, engine_share: Path | None = None):
        super().__init__(title=f"{GAMES[install.game].title} files", content_width=760, content_height=620)
        self.classifier = files.Classifier(install.game, install.path, run.path, engine_share)
        # (label, folder) for each place the dropdown offers.
        self.places = [("Game folder", run.path)]
        if run.path != install.path:
            self.places.append(("Original install (read-only)", install.path))
        self.places += [("Saved games", run.path / "_appdata_" / "savedgames"),
                        ("Settings and logs", run.path / "_appdata_")]
        self.root = run.path
        self.folder = run.path

        view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        self.up = Gtk.Button(icon_name="go-up-symbolic", tooltip_text="Up one folder")
        self.up.connect("clicked", lambda *_: self.show(self.folder.parent))
        header.pack_start(self.up)
        place = Gtk.DropDown.new_from_strings([label for label, _ in self.places])
        place.set_tooltip_text("Which folder to browse")
        place.connect("notify::selected", self.on_place)
        header.set_title_widget(place)
        manager = Gtk.Button(icon_name="folder-open-symbolic", tooltip_text="Open this folder in the file manager")
        manager.connect("clicked", lambda *_: self.launch(self.folder))
        header.pack_end(manager)
        view.add_top_bar(header)

        self.where = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.START, selectable=True,
                               css_classes=["dim-label", "caption"], margin_start=12, margin_end=12,
                               margin_top=6, margin_bottom=6)
        view.add_top_bar(self.where)

        self.list = Gtk.ListBox(css_classes=["boxed-list"], selection_mode=Gtk.SelectionMode.NONE,
                                margin_top=6, margin_bottom=12, margin_start=12, margin_end=12,
                                valign=Gtk.Align.START)
        self.list.connect("row-activated", self.on_row)
        view.set_content(Gtk.ScrolledWindow(child=self.list, vexpand=True,
                                            hscrollbar_policy=Gtk.PolicyType.NEVER))

        self.status = Gtk.Label(xalign=0, wrap=True, css_classes=["dim-label", "caption"],
                                margin_start=12, margin_end=12, margin_top=6, margin_bottom=8)
        view.add_bottom_bar(self.status)

        self.toasts = Adw.ToastOverlay(child=view)
        self.set_child(self.toasts)
        self.show(self.folder)

    # ----- showing a folder ------------------------------------------------------------------------

    def show(self, folder: Path):
        self.folder = folder
        self.up.set_sensitive(folder != self.root)
        read_only = self.classifier.read_only(folder)
        self.where.set_label(str(folder) + ("  (read-only: the launcher never changes the original install)"
                                            if read_only else ""))
        self.list.remove_all()
        if not folder.is_dir():
            empty = Adw.ActionRow(title="Nothing here yet", subtitle="The game creates this folder when it needs it.")
            self.list.append(empty)
            self.status.set_label("")
            return
        entries = self.classifier.list(folder)
        for entry in entries:
            self.list.append(self.build_row(entry))
        if not entries:
            self.list.append(Adw.ActionRow(title="Empty folder"))
        self.status.set_label(files.summary(entries))

    def build_row(self, entry: files.Entry) -> Adw.ActionRow:
        subtitle = entry.detail
        if entry.size is not None:
            subtitle = " · ".join(filter(None, [files.human_size(entry.size), subtitle]))
        row = Adw.ActionRow(title=entry.name, subtitle=subtitle, use_markup=False, activatable=True,
                            title_lines=1, subtitle_lines=2)
        row.entry = entry
        if entry.is_dir:
            icon = "folder-symbolic"
        else:
            icon = ICONS.get(entry.origin, "package-x-generic-symbolic" if entry.name.lower().endswith(
                (".db", ".xdb") + tuple(f".db{c}" for c in "0123456789abcdef")) else "text-x-generic-symbolic")
        row.add_prefix(Gtk.Image(icon_name=icon))
        if not entry.is_dir:
            row.add_suffix(self.button("folder-open-symbolic", "Show in the file manager",
                                       lambda: self.launch(entry.path, containing=True)))
        row.add_suffix(self.button("edit-copy-symbolic", "Copy the full path", lambda: self.copy(entry.path)))
        if entry.removable:
            tip = "Remove the broken link" if entry.origin == "missing" else "Move to the trash"
            row.add_suffix(self.button("user-trash-symbolic", tip, lambda: self.remove(entry)))
        if entry.is_dir:
            row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
        return row

    def button(self, icon: str, tooltip: str, action) -> Gtk.Button:
        btn = Gtk.Button(icon_name=icon, tooltip_text=tooltip, valign=Gtk.Align.CENTER, css_classes=["flat"])
        btn.connect("clicked", lambda *_: action())
        return btn

    # ----- actions ---------------------------------------------------------------------------------

    def on_place(self, dropdown, _pspec):
        self.root = self.places[dropdown.get_selected()][1]
        self.show(self.root)

    def on_row(self, _list, row):
        entry = getattr(row, "entry", None)
        if entry is None:
            return
        if entry.is_dir:
            self.show(entry.path)
        elif entry.origin != "missing":
            self.launch(entry.path)

    def launch(self, path: Path, containing: bool = False):
        launcher = Gtk.FileLauncher(file=Gio.File.new_for_path(str(path)))
        root = self.get_root()
        if containing:
            launcher.open_containing_folder(root, None, None)
        else:
            launcher.launch(root, None, None)

    def copy(self, path: Path):
        Gdk.Display.get_default().get_clipboard().set(str(path))
        self.toast("Path copied")

    def remove(self, entry: files.Entry):
        try:
            if entry.origin == "missing":
                entry.path.unlink()
                self.toast(f"Removed the broken link {entry.name}")
            else:
                Gio.File.new_for_path(str(entry.path)).trash(None)
                self.toast(f"Moved {entry.name} to the trash")
        except (OSError, GLib.Error) as e:
            self.toast(f"Couldn't remove {entry.name}: {getattr(e, 'strerror', None) or getattr(e, 'message', e)}")
        self.show(self.folder)

    def toast(self, text: str):
        self.toasts.add_toast(Adw.Toast(title=GLib.markup_escape_text(text), timeout=3))
