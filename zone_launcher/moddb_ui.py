"""Browsing ModDB inside the mods window, and installing what the user downloads in their browser."""

import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gsk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Adw, Gdk, GLib, GObject, Graphene, Gsk, Gtk  # noqa: E402

from . import downloads, files, moddb, mods  # noqa: E402

# Thumbnails load a few at a time, so a list's 30 don't open 30 connections at once; download sizes
# (one file page each) even fewer.
_images = ThreadPoolExecutor(max_workers=4)
_details = ThreadPoolExecutor(max_workers=2)
# File page -> its size in bytes (None: unknown), so refilling a list doesn't look them up again.
_sizes: dict[str, int | None] = {}

COMPAT_NOTICE = ("Check a mod's page before downloading it. Mods for the original game usually work. "
                 "Standalone games such as Anomaly, and mods that need their own engine (OGSR, OGSE, "
                 "X-Ray Monolith, or ones that replace the game's bin/ files) won't run on OpenXRay. "
                 "Entries marked with a warning mention one of these.")


def open_uri(widget: Gtk.Widget, uri: str):
    Gtk.UriLauncher(uri=uri).launch(widget.get_root(), None, None)


def in_background(work, done):
    """Run work() on a thread, then done(result, error) on the main loop."""
    def run():
        try:
            result, error = work(), None
        except (moddb.ModDBError, OSError) as e:
            result, error = None, str(e)
        GLib.idle_add(done, result, error)
    threading.Thread(target=run, daemon=True).start()


class Thumbnail(Gtk.Widget):
    """A fixed-size image, scaled to fill it and cut to fit, so every row's picture is the same size."""
    __gtype_name__ = "ZoneLauncherThumbnail"

    def __init__(self, url: str, width: int = 96, height: int = 72):
        super().__init__(valign=Gtk.Align.CENTER, css_classes=["card"])
        self.width, self.height = width, height
        self.texture: Gdk.Texture | None = None
        if url:
            _images.submit(self.load, url)

    def load(self, url: str):
        try:
            data = moddb.image(url)
        except (moddb.ModDBError, OSError):
            return
        GLib.idle_add(self.show, data)

    def show(self, data: bytes):
        try:
            self.texture = Gdk.Texture.new_from_bytes(GLib.Bytes.new(data))
        except GLib.Error:
            return  # a format GTK can't read; leave the frame empty
        self.queue_draw()

    def do_measure(self, orientation, _for_size):
        size = self.width if orientation == Gtk.Orientation.HORIZONTAL else self.height
        return size, size, -1, -1

    def do_snapshot(self, snapshot):
        if not self.texture:
            return
        w, h = self.get_width(), self.get_height()
        tw, th = self.texture.get_width(), self.texture.get_height()
        scale = max(w / tw, h / th)
        clip = Gsk.RoundedRect()
        clip.init_from_rect(Graphene.Rect().init(0, 0, w, h), 6)
        snapshot.push_rounded_clip(clip)
        snapshot.append_scaled_texture(self.texture, Gsk.ScalingFilter.TRILINEAR, Graphene.Rect().init(
            (w - tw * scale) / 2, (h - th * scale) / 2, tw * scale, th * scale))
        snapshot.pop()


def notice(text: str) -> Gtk.Widget:
    box = Gtk.Box(spacing=12, css_classes=["card"])
    box.append(Gtk.Image(icon_name="dialog-warning-symbolic", valign=Gtk.Align.START, margin_start=12, margin_top=12))
    box.append(Gtk.Label(label=text, wrap=True, xalign=0, hexpand=True, margin_top=10, margin_bottom=10,
                         margin_end=12, css_classes=["caption"]))
    return box


def size_label(url: str) -> Gtk.Label:
    """The download's size, looked up on its ModDB page in the background."""
    label = Gtk.Label(css_classes=["dim-label", "caption", "numeric"], valign=Gtk.Align.CENTER,
                      tooltip_text="Download size")

    def show(size):
        label.set_label(files.human_size(size) if size is not None else "")
        return False

    if url in _sizes:
        show(_sizes[url])
        return label
    label.set_label("…")

    def look_up():
        try:
            size = moddb.details(url).size
        except (moddb.ModDBError, OSError):
            size = None
        _sizes[url] = size
        GLib.idle_add(show, size)

    _details.submit(look_up)
    return label


def installed_message(mod: mods.Mod, site: str = "") -> str:
    where = f" from {site}" if site else ""
    if mod.engine:
        return f"Installed {mod.name}{where}, switched off: it needs its own engine"
    return f"Installed {mod.name}{where}" + (" (with warnings; expand it to see them)" if mod.warnings else "")


def installed_urls(game: str) -> set[str]:
    urls = set()
    for mod in mods.list_mods(game):
        urls.update(v for k, v in mod.source.items() if k in ("page", "file"))
    return urls


# ----- downloads ---------------------------------------------------------------------------------

class Downloads:
    """The ModDB downloads the user started in their browser: waits for each to land in Downloads,
    checks it, and installs it. Lives as long as the window, so closing the mods window doesn't stop
    a long download from being picked up."""

    def __init__(self):
        self.watcher = downloads.Watcher()
        self.installing: list[downloads.Pending] = []
        self.listeners: list = []
        self.timer: int | None = None

    def listen(self, callback):
        """callback(message or None) runs after every change. Returns a function that stops it."""
        self.listeners.append(callback)
        return lambda *_: self.listeners.remove(callback) if callback in self.listeners else None

    def notify(self, message: str | None = None):
        for callback in list(self.listeners):
            callback(message)

    def waiting(self, game: str) -> list[downloads.Pending]:
        return self.watcher.for_game(game)

    def busy(self, game: str) -> list[downloads.Pending]:
        return [p for p in self.installing if p.game == game]

    def start(self, pending: downloads.Pending, uri: str, widget: Gtk.Widget):
        self.watcher.add(pending)
        open_uri(widget, uri)
        if self.timer is None:
            self.timer = GLib.timeout_add_seconds(2, self.tick)
        self.notify()

    def cancel(self, pending: downloads.Pending):
        self.watcher.cancel(pending)
        self.notify()

    def pick(self, pending: downloads.Pending, path: Path):
        """The user points at the file themselves (their browser saved it somewhere else)."""
        self.watcher.cancel(pending)
        self.install(pending, path)

    def tick(self) -> bool:
        for pending, path in self.watcher.poll():
            self.install(pending, path)
        if self.watcher.pending:
            return True
        self.timer = None
        return False

    def install(self, pending: downloads.Pending, path: Path):
        self.installing.append(pending)
        self.notify()

        def work():
            if not pending.verify(path):
                raise mods.ModError(f"{path.name} doesn't match the file on ModDB (its checksum differs), so "
                                    "it's damaged or a different file. Download it again.")
            return mods.install(pending.game, path, name=pending.title, origin=pending.origin)

        def done(mod, error):
            self.installing.remove(pending)
            if error:
                self.notify(f"Couldn't install {pending.title}: {error}")
            else:
                self.notify(installed_message(mod, "ModDB"))

        def run():
            try:
                result, error = work(), None
            except (mods.ModError, OSError) as e:
                result, error = None, str(e)
            GLib.idle_add(done, result, error)

        threading.Thread(target=run, daemon=True).start()


def start_download(dialog: Adw.PreferencesDialog, game: str, downloads_: Downloads, url: str,
                   title: str | None, mod_name: str = "", mod_page: str = "", button: Gtk.Widget | None = None):
    """Look the file up on ModDB, then open its download in the browser and wait for it."""
    if button:
        button.set_sensitive(False)

    def work():
        details = moddb.details(url)
        # Created here, before the browser starts, so the file isn't counted as already there.
        return details, downloads.Pending(
            game=game, title=title or details.title or "ModDB download",
            origin={"site": "ModDB", "mod": mod_name, "page": mod_page or url, "file": url},
            filename=details.filename, size=details.size, md5=details.md5)

    def done(result, error):
        if button:
            button.set_sensitive(True)
        if error:
            dialog.add_toast(Adw.Toast(title=GLib.markup_escape_text(error), timeout=5))
            return
        details, pending = result
        downloads_.start(pending, details.start or url, dialog)
        dialog.add_toast(Adw.Toast(title="Download it in your browser; it's installed when it's done", timeout=5))

    in_background(work, done)


# ----- pages ---------------------------------------------------------------------------------------

class ListPage(Adw.NavigationPage):
    """A page with a header, a list that loads in the background, and a line along the bottom."""

    def __init__(self, title: str, footer: str, footer_button: str, footer_action):
        super().__init__(title=title)
        self.view = Adw.ToolbarView()
        self.header = Adw.HeaderBar()
        self.view.add_top_bar(self.header)

        self.stack = Gtk.Stack()
        spinner = Gtk.Spinner(spinning=True, width_request=32, height_request=32,
                              halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        self.stack.add_named(spinner, "loading")
        self.error = Adw.StatusPage(icon_name="network-error-symbolic", title="Couldn't load ModDB")
        retry = Gtk.Button(label="Try again", halign=Gtk.Align.CENTER, css_classes=["pill"])
        retry.connect("clicked", lambda *_: self.load())
        self.error.set_child(retry)
        self.stack.add_named(self.error, "error")
        self.list = Gtk.ListBox(css_classes=["boxed-list"], selection_mode=Gtk.SelectionMode.NONE,
                                valign=Gtk.Align.START)
        self.list.connect("row-activated", lambda _l, row: getattr(row, "on_activate", lambda: None)())
        self.content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_top=6,
                               margin_bottom=12, margin_start=12, margin_end=12)
        self.content.append(self.list)
        self.stack.add_named(Gtk.ScrolledWindow(child=self.content, vexpand=True,
                                                hscrollbar_policy=Gtk.PolicyType.NEVER), "list")
        self.view.set_content(self.stack)

        bar = Gtk.Box(spacing=12, margin_start=12, margin_end=12, margin_top=6, margin_bottom=6)
        self.footer = Gtk.Label(label=footer, xalign=0, wrap=True, hexpand=True, css_classes=["dim-label", "caption"])
        bar.append(self.footer)
        more = Gtk.Button(label=footer_button, css_classes=["flat"], valign=Gtk.Align.CENTER)
        more.connect("clicked", lambda *_: footer_action())
        bar.append(more)
        self.view.add_bottom_bar(bar)
        self.set_child(self.view)

    def load(self):
        self.stack.set_visible_child_name("loading")
        # Only the latest load counts (switching lists quickly starts several).
        self.loading = generation = getattr(self, "loading", 0) + 1
        in_background(self.fetch, lambda result, error: generation == self.loading and self.loaded(result, error))

    def loaded(self, result, error):
        if error:
            self.error.set_description(error)
            self.stack.set_visible_child_name("error")
            return
        self.items = result
        self.fill()
        self.stack.set_visible_child_name("list")

    def fetch(self):
        raise NotImplementedError

    def fill(self):
        raise NotImplementedError

    def item_row(self, item: moddb.Item, installed: set[str], mod_name: str = "", mod_page: str = "") -> Adw.ActionRow:
        """A mod (opens its files) or a download (with its size and a Download button)."""
        hint = item.compat_hint()
        subtitle = " · ".join(filter(None, [
            f"Check first: it {hint}." if hint else "",
            item.category,
            item.stat,
            f"Updated {item.updated}" if item.updated and item.kind != "file" else item.updated,
            item.summary]))
        row = Adw.ActionRow(title=item.title, subtitle=subtitle, use_markup=False, title_lines=2, subtitle_lines=3)
        row.item = item
        row.add_prefix(Thumbnail(item.image))
        if hint:
            row.add_suffix(Gtk.Image(icon_name="dialog-warning-symbolic", valign=Gtk.Align.CENTER,
                                     tooltip_text=f"May not work on OpenXRay: it {hint}"))
        if item.url in installed:
            row.add_suffix(Gtk.Label(label="Installed", css_classes=["dim-label", "caption"], valign=Gtk.Align.CENTER))
        if item.kind == "mod":
            row.set_activatable(True)
            row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
            return row
        row.add_suffix(size_label(item.url))
        row.add_suffix(self.button("adw-external-link-symbolic", "Open its page on ModDB",
                                   lambda _b: open_uri(self, item.url)))
        row.add_suffix(self.button("folder-download-symbolic", "Download and install", label="Download",
                                   action=lambda b: start_download(self.dialog, self.game, self.downloads, item.url,
                                                                   item.title, mod_name, mod_page, button=b)))
        return row

    def button(self, icon: str, tooltip: str, action, label: str | None = None) -> Gtk.Button:
        btn = Gtk.Button(icon_name=icon, tooltip_text=tooltip, valign=Gtk.Align.CENTER,
                         css_classes=["flat"] if not label else ["suggested-action"])
        if label:
            btn.set_label(label)
        btn.connect("clicked", lambda b: action(b))
        return btn


class BrowsePage(ListPage):
    """A game's mods or addons on ModDB, in one of moddb.SORTS' orders."""

    # The order last picked, kept while the launcher runs.
    sort = "updated"

    def __init__(self, dialog, game: str, downloads_: Downloads):
        self.dialog, self.game, self.downloads = dialog, game, downloads_
        self.kind = "mods"
        self.items: list[moddb.Item] = []
        super().__init__("ModDB", "", "Search on ModDB", self.search_on_site)

        kinds = Gtk.DropDown.new_from_strings(["Mods", "Addons"])
        kinds.set_tooltip_text("Whole mods, or small addons and fixes")
        kinds.connect("notify::selected", self.on_kind)
        self.header.set_title_widget(kinds)
        self.header.pack_end(self.button("adw-external-link-symbolic", "Open this list on ModDB",
                                         lambda _b: open_uri(self, moddb.list_url(game, self.kind, self.sort))))

        self.search = Gtk.SearchEntry(placeholder_text="Filter, or paste a link to a ModDB mod or file",
                                      hexpand=True)
        self.search.connect("search-changed", self.on_search)
        self.search.connect("activate", lambda *_: self.open_link())
        entry_box = Gtk.Box(spacing=6, margin_start=12, margin_end=12, margin_top=6, margin_bottom=6)
        entry_box.append(self.search)
        sorts = Gtk.DropDown.new_from_strings([label for _key, label, *_values in moddb.SORTS])
        sorts.set_tooltip_text("Order of the list")
        sorts.set_selected([key for key, *_rest in moddb.SORTS].index(self.sort))
        sorts.connect("notify::selected", self.on_sort)
        entry_box.append(sorts)
        self.view.add_top_bar(entry_box)

        self.link_row = Adw.ActionRow(title="Open this ModDB link", activatable=True, visible=False)
        self.link_row.add_prefix(Gtk.Image(icon_name="adw-external-link-symbolic"))
        self.link_row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
        self.link_row.connect("activated", lambda *_: self.open_link())
        links = Gtk.ListBox(css_classes=["boxed-list"], selection_mode=Gtk.SelectionMode.NONE)
        links.append(self.link_row)
        self.link_row.bind_property("visible", links, "visible", GObject.BindingFlags.SYNC_CREATE)
        self.content.prepend(links)
        self.content.prepend(notice(COMPAT_NOTICE))
        self.list.set_filter_func(self.matches)
        # Mark what gets installed while the page is open.
        dialog.connect("closed", downloads_.listen(lambda _m: self.items and self.fill()))
        self.load()

    def fetch(self):
        return moddb.browse(self.game, self.kind, self.sort)

    def fill(self):
        self.list.remove_all()
        installed = installed_urls(self.game)
        label = next(label for key, label, *_values in moddb.SORTS if key == self.sort).lower()
        self.footer.set_label(f"{len(self.items)} {self.kind} from ModDB, {label}. Looking for something else?")
        for item in self.items:
            row = self.item_row(item, installed)
            if item.kind == "mod":
                row.on_activate = lambda item=item: self.open_mod(item.title, item.url)
            self.list.append(row)
        if not self.items:
            self.list.append(Adw.ActionRow(title="Nothing listed on ModDB yet"))

    def matches(self, row) -> bool:
        words = self.search.get_text().strip().lower()
        item = getattr(row, "item", None)
        if not words or item is None or moddb.parse_link(words):
            return True
        return all(w in f"{item.title} {item.summary}".lower() for w in words.split())

    def on_kind(self, dropdown, _pspec):
        self.kind = moddb.KINDS[dropdown.get_selected()]
        self.load()

    def on_sort(self, dropdown, _pspec):
        BrowsePage.sort = moddb.SORTS[dropdown.get_selected()][0]
        self.load()

    def on_search(self, _entry):
        link = moddb.parse_link(self.search.get_text())
        self.link_row.set_visible(link is not None)
        if link:
            self.link_row.set_subtitle("A mod: see its files" if link[0] == "mod" else "A file: download and install it")
        self.list.invalidate_filter()

    def open_link(self):
        link = moddb.parse_link(self.search.get_text())
        if not link:
            return
        kind, url = link
        if kind == "mod":
            self.open_mod(None, url)
        else:
            start_download(self.dialog, self.game, self.downloads, url, None, button=self.link_row)

    def open_mod(self, title: str | None, url: str):
        self.dialog.push_subpage(FilesPage(self.dialog, self.game, self.downloads, title, url))

    def search_on_site(self):
        words = self.search.get_text().strip()
        open_uri(self, moddb.search_url(self.game, self.kind, "" if moddb.parse_link(words) else words))


class FilesPage(ListPage):
    """One mod's files on ModDB, newest first."""

    def __init__(self, dialog, game: str, downloads_: Downloads, title: str | None, url: str):
        self.dialog, self.game, self.downloads = dialog, game, downloads_
        self.mod_name, self.url = title or url.rstrip("/").rsplit("/", 1)[-1], url
        self.items: list[moddb.Item] = []
        super().__init__(self.mod_name, "The 30 newest files of the mod.", "All files on ModDB",
                         lambda: open_uri(self, moddb.all_files_url(url)))
        self.header.pack_end(self.button("adw-external-link-symbolic", "Open the mod's page on ModDB",
                                         lambda _b: open_uri(self, url)))
        hint = Gtk.Label(label="Most mods have one full version to download; patches and fixes go on top "
                               "of it, higher in the load order.",
                         wrap=True, xalign=0, css_classes=["dim-label"])
        self.content.prepend(hint)
        self.content.prepend(notice(COMPAT_NOTICE))
        # Mark what gets installed while the page is open.
        dialog.connect("closed", downloads_.listen(lambda _m: self.items and self.fill()))
        self.load()

    def fetch(self):
        return moddb.files(self.url)

    def loaded(self, result, error):
        if result:
            name, result = result
            if name:  # a pasted link only gave the address
                self.mod_name = name
                self.set_title(name)
        super().loaded(result, error)

    def fill(self):
        self.list.remove_all()
        installed = installed_urls(self.game)
        for item in self.items:
            self.list.append(self.item_row(item, installed, self.mod_name, self.url))
        if not self.items:
            self.list.append(Adw.ActionRow(title="No files on ModDB yet"))


def pending_row(dialog: Adw.PreferencesDialog, downloads_: Downloads, pending: downloads.Pending,
                installing: bool) -> Adw.ActionRow:
    """A download in the mods window: waiting for the browser, or being installed."""
    if installing:
        row = Adw.ActionRow(title=f"Installing {pending.title}…", use_markup=False,
                            subtitle="Unpacking can take a minute for big mods.")
        row.add_prefix(Gtk.Spinner(spinning=True))
        return row
    name = pending.filename or "the file"
    folder = str(pending.folder)
    if folder.startswith(str(Path.home()) + "/"):
        folder = "~" + folder[len(str(Path.home())):]
    if pending.size is not None:
        name += f" ({files.human_size(pending.size)})"
    row = Adw.ActionRow(title=f"Waiting for {pending.title}", use_markup=False, subtitle_lines=2,
                        subtitle=f"Download {name} in your browser. It's installed once it's finished in {folder}.")
    row.add_prefix(Gtk.Spinner(spinning=True))

    def choose(_b):
        def chosen(file_dialog, result):
            try:
                file = file_dialog.open_finish(result)
            except GLib.Error:
                return
            if file and file.get_path():
                downloads_.pick(pending, Path(file.get_path()))
        Gtk.FileDialog(title=f"Choose {name}").open(dialog.get_root(), None, chosen)

    for icon, tip, action in (
        ("document-open-symbolic", "Saved it somewhere else? Choose the file", choose),
        ("adw-external-link-symbolic", "Open the download page again", lambda _b: open_uri(dialog, pending.origin["file"])),
        ("process-stop-symbolic", "Stop waiting for it", lambda _b: downloads_.cancel(pending)),
    ):
        btn = Gtk.Button(icon_name=icon, tooltip_text=tip, valign=Gtk.Align.CENTER, css_classes=["flat"])
        btn.connect("clicked", action)
        row.add_suffix(btn)
    return row

