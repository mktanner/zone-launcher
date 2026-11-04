"""Rows that get Proton and Microsoft's DirectX libraries, for the dialogs that want them."""

import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

from . import runtimes, standalone  # noqa: E402

PROTON, DIRECTX = "proton", "directx"


class Getter:
    """Runs one download of each thing, which carries on if the dialog that started it closes."""

    def __init__(self, window):
        self.window = window
        self.status: dict[str, str] = {}
        self.outcome: dict[str, str] = {}
        self.listeners: list = []

    def notify(self):
        for listener in list(self.listeners):
            listener()

    def set_status(self, kind: str, text: str):
        def show():
            self.status[kind] = text
            self.notify()

        GLib.idle_add(show)

    def start(self, kind: str):
        if kind in self.status:
            return
        self.status[kind] = "Starting…"
        self.notify()

        def run():
            try:
                message, error = (self.get_proton if kind == PROTON else self.get_directx)(), None
            except OSError as e:
                message, error = None, str(e)
            GLib.idle_add(self.finished, kind, message, error)

        threading.Thread(target=run, daemon=True).start()

    def get_proton(self) -> str:
        self.set_status(PROTON, "Looking up the latest version…")
        release = runtimes.latest_proton()
        if any(runner.name == release.name for runner in standalone.find_runners()):
            return f"You already have the latest: {release.name}"
        runtimes.install_proton(release, lambda text: self.set_status(PROTON, text))
        return f"Installed {release.name}. Automatic uses it now."

    def get_directx(self) -> str:
        runtimes.install_directx(lambda text: self.set_status(DIRECTX, text))
        return "Installed Microsoft's DirectX libraries. Mods get them when you press Play."

    def finished(self, kind: str, message: str | None, error: str | None):
        self.status.pop(kind, None)
        self.outcome[kind] = message or f"Couldn't get it: {error}"
        self.window.toast(self.outcome[kind])
        self.window.refresh()
        self.notify()


class GetRow(Adw.ActionRow):
    """The state of Proton or the DirectX libraries, with a button to get them."""

    def __init__(self, window, kind: str):
        super().__init__(title="Proton" if kind == PROTON else "Microsoft's DirectX libraries", use_markup=False)
        self.getter, self.kind = window.getter, kind
        self.spinner = Gtk.Spinner(visible=False, valign=Gtk.Align.CENTER)
        self.button = Gtk.Button(label="Download", valign=Gtk.Align.CENTER)
        self.button.connect("clicked", lambda *_: self.getter.start(kind))
        self.add_suffix(self.spinner)
        self.add_suffix(self.button)
        self.connect("map", lambda *_: (self.getter.listeners.append(self.fill), self.fill()))
        self.connect("unmap", lambda *_: self.getter.listeners.remove(self.fill))
        self.fill()

    def fill(self):
        busy = self.getter.status.get(self.kind)
        self.spinner.set_visible(bool(busy))
        self.spinner.set_spinning(bool(busy))
        self.button.set_sensitive(not busy)
        if self.kind == PROTON:
            best = standalone.best_runner()
            state = (f"Automatic uses {best.name}. Download gets the latest CachyOS Proton from GitHub, about 350 MB, "
                     "which has run these games best." if best.is_proton else
                     "None found. Download gets the latest CachyOS Proton from GitHub, about 350 MB, which has run "
                     "these games best. Mods then use it automatically.")
        elif standalone.redist_folders():
            state = "You have them, in Steam's DirectX installer or from an earlier download. Mods get them when you press Play."
            self.button.set_visible(False)
        else:
            state = ("Not found. Wine's and Proton's own can't compile every game's shaders. Download gets them from "
                     "Microsoft, about 100 MB.")
            self.button.set_visible(True)
        self.set_subtitle(busy or self.getter.outcome.get(self.kind) or state)
