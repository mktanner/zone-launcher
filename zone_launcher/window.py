"""The launcher window: the three games side by side, with settings along the bottom."""

import shlex
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Pango", "1.0")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

from . import detect, display, engine, fixes, mods, setup  # noqa: E402
from .files_ui import FilesDialog  # noqa: E402
from .mods_ui import ModsDialog  # noqa: E402
from .config import Config  # noqa: E402
from .games import GAMES  # noqa: E402

# Every language at least one game has (Clear Sky packs, Shadow of Chernobyl text); each game switches
# to it if it can and says so in Details if not.
LANGUAGES = ["english", "russian", "german", "french", "italian", "spanish", "polish", "czech"]
# What to look for in a game's folder, for when it has to be picked by hand.
FOLDER_HINT = {"soc": "gamedata.db0 and a bin folder",
               "cs": "a resources folder with resources.db0 (and patches/xpatch_10.db)",
               "cop": "Stalker-COP.exe, or a resources folder with resources.db3"}
WHERE_WE_LOOK = ("OpenXRay's folder, Heroic, Steam, Lutris, ~/Games, Wine and Bottles prefixes, and "
                 "other drives")


@dataclass
class GameState:
    """Everything the card and the details dialog show for one game."""
    install: detect.Install | None
    options: list[detect.Install]
    run: detect.Install | None = None
    issues: list[fixes.Issue] | None = None
    status: str = ""
    icon: str = "emblem-ok-symbolic"
    can_play: bool = False


class LauncherWindow(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application):
        super().__init__(application=app, title="Zone Launcher", default_width=1120, default_height=600)
        self.config = Config.load()
        self.engine = engine.find_engine(self.config.engine_override or None)
        self.installs: list[detect.Install] = []
        # Game key -> running engine process, so a game can't be started twice.
        self.running: dict[str, subprocess.Popen] = {}
        # Games the user stopped; their forced exit isn't reported as a crash.
        self.stopping: set[str] = set()
        # Game key -> message while fixes are being applied before launch.
        self.busy: dict[str, str] = {}
        # The open details dialog and its game, so it can follow refreshes.
        self.details: tuple[Adw.PreferencesDialog, str] | None = None
        # A folder search is running (the one at startup, or one the user started).
        self.searching = False

        self.toasts = Adw.ToastOverlay()
        view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        add_btn = Gtk.Button(icon_name="folder-new-symbolic", tooltip_text="Add a game folder")
        add_btn.connect("clicked", self.on_add_folder)
        self.refresh_btn = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="Look for games again")
        self.refresh_btn.connect("clicked", lambda *_: (self.refresh(), self.search_in_background()))
        header.pack_start(add_btn)
        header.pack_end(self.refresh_btn)
        view.add_top_bar(header)

        self.banner = Adw.Banner(title="OpenXRay engine not found. Install it, or set its path below.")
        view.add_top_bar(self.banner)

        # vexpand set explicitly, so the cards' inner spacer doesn't stretch the whole row.
        self.cards = Gtk.Box(spacing=18, homogeneous=True, vexpand=False)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18, valign=Gtk.Align.CENTER,
                       margin_top=18, margin_bottom=18, margin_start=18, margin_end=18)
        body.append(self.cards)
        body.append(self.build_settings_bar())
        # Keep a maximized window tidy: centered, no wider than this. Scrolls only if made very small.
        clamp = Adw.Clamp(child=body, maximum_size=1280, tightening_threshold=1100)
        view.set_content(Gtk.ScrolledWindow(child=clamp, hscrollbar_policy=Gtk.PolicyType.NEVER))
        self.toasts.set_child(view)
        self.set_content(self.toasts)
        self.refresh()
        self.search_in_background()

    # ----- building the page -------------------------------------------------------------

    def refresh(self):
        self.engine = engine.find_engine(self.config.engine_override or None)
        self.banner.set_revealed(self.engine is None)
        self.engine_label.set_label(str(self.engine.binary) if self.engine else "Not found")
        self.installs = detect.find_installs(extra=self.config.extra_folders, searched=self.config.found_folders)
        while child := self.cards.get_first_child():
            self.cards.remove(child)
        for key in GAMES:
            self.cards.append(self.build_game_card(key))
        if self.details:
            self.fill_details(*self.details)

    def game_options(self, key: str) -> list[detect.Install]:
        return [i for i in self.installs if i.game == key]

    def selected_install(self, key: str) -> detect.Install | None:
        options = self.game_options(key)
        chosen = self.config.selected.get(key)
        if match := next((i for i in options if str(i.path) == chosen), None):
            return match
        # Unless the player picked one: a copy made by hand in the OpenXRay folder, then one a store
        # or the player added, then whatever a search found.
        target = setup.target_dir(key)
        if setup.is_own_copy(target):
            if own := next((i for i in options if i.path == target.resolve()), None):
                return own
        return next((i for i in options if i.sources != [detect.SEARCHED]), options[0] if options else None)

    def game_state(self, key: str) -> GameState:
        state = GameState(self.selected_install(key), self.game_options(key))
        if state.install is None:
            state.status, state.icon = "Not found", "dialog-question-symbolic"
            return state
        state.run, setup_issue = setup.plan(state.install)
        if setup_issue:
            state.issues = [setup_issue]
        else:
            state.issues = fixes.check(state.run, self.engine, self.config.language) if self.engine else []
        pending = [i for i in state.issues if i.blocking]
        stuck = [i for i in pending if not i.fix]
        if key in self.running:
            state.status, state.icon, state.can_play = "Running", "media-playback-start-symbolic", True
        elif key in self.busy:
            state.status, state.icon = self.busy[key], "content-loading-symbolic"
        elif stuck:
            state.status, state.icon = stuck[0].title, "dialog-error-symbolic"
        elif pending:
            n = len(pending)
            state.status = f"{n} fix{'es' if n != 1 else ''} pending"
            state.icon, state.can_play = "dialog-warning-symbolic", True
        else:
            state.status, state.can_play = "Ready to play", True
        state.can_play = state.can_play and self.engine is not None
        return state

    def build_game_card(self, key: str) -> Gtk.Widget:
        game = GAMES[key]
        state = self.game_state(key)
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, css_classes=["card"])
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10,
                        margin_top=18, margin_bottom=18, margin_start=18, margin_end=18)
        card.append(inner)

        title_row = Gtk.Box(spacing=12)
        title_row.append(Gtk.Image(icon_name=game.icon, pixel_size=48))
        title_row.append(Gtk.Label(label=game.title, css_classes=["title-3"], wrap=True, xalign=0,
                                   hexpand=True))
        inner.append(title_row)

        status = Gtk.Box(spacing=8)
        if key in self.busy:
            status.append(Gtk.Spinner(spinning=True))
        else:
            status.append(Gtk.Image(icon_name=state.icon))
        status.append(Gtk.Label(label=state.status, wrap=True, xalign=0, hexpand=True))
        if state.issues:
            status.set_tooltip_text("\n".join(f"• {i.title}" for i in state.issues) +
                                    ("\nPlay applies these first." if state.can_play else ""))
        inner.append(status)

        if state.install:
            run = state.run or state.install
            where = f"{', '.join(state.install.sources)} · linked" if run.path != state.install.path \
                else ", ".join(state.install.sources)
            inner.append(Gtk.Label(label=where, tooltip_text=str(run.path), xalign=0,
                                   ellipsize=Pango.EllipsizeMode.MIDDLE, css_classes=["dim-label"]))
            installed = mods.list_mods(key)
            enabled = sum(m.is_enabled for m in installed)
            mods_text = f"Mods: {len(installed)} installed, {enabled} on" if installed else "Mods: none"
            inner.append(Gtk.Label(label=mods_text, xalign=0, css_classes=["dim-label"]))
        else:
            inner.append(Gtk.Label(label="Install it with Heroic, Steam or GOG, or choose or search for its "
                                         "folder in Details.",
                                   wrap=True, xalign=0, css_classes=["dim-label"]))

        inner.append(Gtk.Box(vexpand=True, height_request=6))  # keep buttons level across cards
        buttons = Gtk.Box(spacing=6, homogeneous=True)
        mods_btn = Gtk.Button(label="Mods", sensitive=state.install is not None and key not in self.running)
        mods_btn.connect("clicked", lambda *_: ModsDialog(key, on_change=self.refresh).present(self))
        details_btn = Gtk.Button(label="Details")
        details_btn.connect("clicked", lambda *_: self.show_details(key))
        buttons.append(mods_btn)
        buttons.append(details_btn)
        inner.append(buttons)

        play = Gtk.Button(label="Play", css_classes=["suggested-action", "pill"], height_request=48)
        if key in self.running:
            # Also the way out if the engine hangs after its window closes.
            play.set_label("Stop")
            play.set_css_classes(["destructive-action", "pill"])
            play.connect("clicked", self.on_stop, key)
        else:
            play.connect("clicked", self.on_play, key)
        play.set_sensitive(state.can_play)
        inner.append(play)
        return card

    def show_details(self, key: str):
        dialog = Adw.PreferencesDialog(title=f"{GAMES[key].title} details", search_enabled=False)
        self.details = (dialog, key)
        dialog.connect("closed", lambda *_: setattr(self, "details", None))
        self.fill_details(dialog, key)
        dialog.present(self)

    def fill_details(self, dialog: Adw.PreferencesDialog, key: str):
        state = self.game_state(key)
        page = Adw.PreferencesPage()
        page.add(self.build_files_group(key, state))
        if state.install is not None:
            checks = Adw.PreferencesGroup(title="Checks")
            page.add(checks)
            for issue in state.issues or []:
                checks.add(self.build_issue_row(state.install, issue))
            if not state.issues:
                ok = Adw.ActionRow(title="Everything is in order")
                ok.add_prefix(Gtk.Image(icon_name="emblem-ok-symbolic"))
                checks.add(ok)
            log = engine.latest_log(state.run) if state.run else None
            if log:
                log_row = Adw.ActionRow(title="Last log", subtitle=log.name, use_markup=False)
                log_btn = Gtk.Button(label="Open", valign=Gtk.Align.CENTER)
                log_btn.connect("clicked", lambda *_: self.open_file(log))
                log_row.add_suffix(log_btn)
                checks.add(log_row)
        if old := dialog.get_visible_page():
            dialog.remove(old)
        dialog.add(page)

    def build_files_group(self, key: str, state: GameState) -> Adw.PreferencesGroup:
        game = GAMES[key]
        group = Adw.PreferencesGroup(title="Game files")
        actions = Gtk.Box(spacing=6)
        choose = Gtk.Button(label="Choose folder…", css_classes=["flat"],
                            tooltip_text=f"Pick the folder {game.title} is installed in")
        choose.connect("clicked", lambda *_: self.choose_folder(key))
        search = Gtk.Button(label="Searching…" if self.searching else "Search…", css_classes=["flat"],
                            sensitive=not self.searching,
                            tooltip_text="Look for the game anywhere inside a folder or drive you pick")
        search.connect("clicked", lambda *_: self.choose_folder(key, search=True))
        actions.append(choose)
        actions.append(search)
        group.set_header_suffix(actions)

        install = state.install
        if install is None:
            row = Adw.ActionRow(title="Not found", use_markup=False,
                                subtitle=f"Looked in {WHERE_WE_LOOK}. Choose the folder that has "
                                         f"{FOLDER_HINT[key]}, or search a drive for it.")
            row.add_prefix(Gtk.Image(icon_name="dialog-question-symbolic"))
            group.add(row)
            return group

        if len(state.options) > 1:
            row = Adw.ComboRow(title="Install", model=Gtk.StringList.new([i.label for i in state.options]),
                               tooltip_text="Every copy of the game found; pick the one to play")
            row.set_selected(state.options.index(install))
            row.connect("notify::selected", self.on_install_chosen, key, state.options)
        else:
            row = Adw.ActionRow(title="Install", subtitle=install.label, subtitle_selectable=True,
                                use_markup=False)
        if set(install.sources) <= {detect.USER_ADDED, detect.SEARCHED}:
            forget = Gtk.Button(icon_name="list-remove-symbolic", valign=Gtk.Align.CENTER, css_classes=["flat"],
                                tooltip_text="Remove from the list (no files are deleted)")
            forget.connect("clicked", lambda *_: self.forget_folder(install))
            row.add_suffix(forget)
        group.add(row)

        missing = detect.missing_files(install)
        level = detect.patch_level(install)
        if missing:
            contents = Adw.ActionRow(title="Files missing", use_markup=False,
                                     subtitle=f"{', '.join(missing)} not found, so the game may not start. "
                                              f"Verify or repair it in {', '.join(install.sources)}, or "
                                              "choose a complete copy.")
            contents.add_prefix(Gtk.Image(icon_name="dialog-warning-symbolic"))
        else:
            contents = Adw.ActionRow(title="Contents", use_markup=False,
                                     subtitle="All game archives present" + (f" · patch {level}" if level else ""))
        group.add(contents)

        run = state.run if state.run and state.run.path.is_dir() else install
        if state.run and state.run.path != install.path:
            plays = "linked to the install above" if state.run.path.is_dir() else "set up on the first Play"
            group.add(Adw.ActionRow(title="Plays from", subtitle=f"{state.run.path}  ({plays})",
                                    subtitle_selectable=True, use_markup=False))
        browse = Adw.ActionRow(title="Browse files", activatable=True, use_markup=False,
                               subtitle="Every file and where it comes from (install, mods, launcher, you), "
                                        "saved games and logs")
        browse.add_prefix(Gtk.Image(icon_name="folder-symbolic"))
        manager = Gtk.Button(icon_name="folder-open-symbolic", valign=Gtk.Align.CENTER, css_classes=["flat"],
                             tooltip_text="Open the game folder in the file manager")
        manager.connect("clicked", lambda *_: self.open_file(run.path))
        browse.add_suffix(manager)
        browse.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
        browse.connect("activated", lambda *_: FilesDialog(
            install, run, self.engine.share if self.engine else None).present(self))
        group.add(browse)

        if state.run:
            available = fixes.available_languages(state.run)
            group.add(Adw.ActionRow(title="Languages", use_markup=False,
                                    subtitle=", ".join(l.title() for l in available) or "None found"))
        return group

    def build_issue_row(self, install: detect.Install, issue: fixes.Issue) -> Adw.ActionRow:
        row = Adw.ActionRow(title=issue.title, subtitle=issue.detail, use_markup=False)
        row.add_prefix(Gtk.Image(icon_name="dialog-warning-symbolic" if issue.blocking else "dialog-information-symbolic"))
        if issue.fix and install.game not in self.busy:
            btn = Gtk.Button(label="Fix", valign=Gtk.Align.CENTER)
            btn.connect("clicked", lambda *_: self.apply_fixes(install, [issue]))
            row.add_suffix(btn)
        return row

    def build_settings_bar(self) -> Gtk.Widget:
        bar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, css_classes=["card"])
        grid = Gtk.Grid(column_spacing=12, row_spacing=10, margin_top=14, margin_bottom=14,
                        margin_start=18, margin_end=18)
        bar.append(grid)

        def field(label: str, widget: Gtk.Widget, column: int, row: int, width: int = 1):
            grid.attach(Gtk.Label(label=label, xalign=1, css_classes=["dim-label"]), column, row, 1, 1)
            grid.attach(widget, column + 1, row, width, 1)

        # (label, config value) pairs; the first entry of each is the default.
        modes = [(label, key) for key, (label, _) in display.WINDOW_MODES.items()]
        modes.append(("Use in-game setting", display.KEEP))
        native = self.native_resolution()
        resolutions = [
            (f"Native ({native[0]} × {native[1]})" if native else "Native", display.NATIVE),
            *((f"{w} × {h}", f"{w}x{h}") for w, h in display.resolution_choices(native)),
            ("Use in-game setting", display.KEEP),
        ]
        languages = [(lang.title(), lang) for lang in LANGUAGES]
        field("Display", self.build_choice(modes, "window_mode"), 0, 0)
        field("Resolution", self.build_choice(resolutions, "resolution"), 2, 0)
        language = self.build_choice(languages, "language", refresh=True)
        language.set_tooltip_text("Applied on the next Play to every game that has it; "
                                  "Details shows each game's languages")
        field("Language", language, 4, 0)

        args = Gtk.Entry(text=self.config.extra_args, placeholder_text="none", hexpand=True)
        args.connect("activate", self.on_args_applied)
        focus = Gtk.EventControllerFocus()
        focus.connect("leave", lambda *_: self.on_args_applied(args))
        args.add_controller(focus)
        field("Engine arguments", args, 0, 1, 3)
        self.engine_label = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.MIDDLE, selectable=True, hexpand=True)
        field("Engine", self.engine_label, 4, 1)
        return bar

    def build_choice(self, options: list[tuple[str, str]], setting: str, refresh: bool = False) -> Gtk.DropDown:
        values = [value for _, value in options]
        choice = Gtk.DropDown.new_from_strings([label for label, _ in options])
        current = getattr(self.config, setting)
        choice.set_selected(values.index(current) if current in values else 0)
        choice.set_hexpand(True)

        def chosen(choice, _pspec):
            setattr(self.config, setting, values[choice.get_selected()])
            self.config.save()
            if refresh:
                GLib.idle_add(self.refresh)

        choice.connect("notify::selected", chosen)
        return choice

    # ----- actions -----------------------------------------------------------------------

    def open_file(self, path: Path):
        Gtk.FileLauncher(file=Gio.File.new_for_path(str(path))).launch(self, None, None)

    def toast(self, text: str):
        self.toasts.add_toast(Adw.Toast(title=GLib.markup_escape_text(text), timeout=4))

    def on_install_chosen(self, row, _pspec, key, options):
        self.config.selected[key] = str(options[row.get_selected()].path)
        self.config.save()
        GLib.idle_add(self.refresh)

    def native_resolution(self) -> tuple[int, int] | None:
        # The engine opens on monitor 0 by default; GTK reports logical size, so undo the scaling.
        monitors = Gdk.Display.get_default().get_monitors()
        monitor = monitors.get_item(0) if monitors.get_n_items() else None
        if monitor is None:
            return None
        geometry, scale = monitor.get_geometry(), monitor.get_scale()
        return round(geometry.width * scale), round(geometry.height * scale)

    def display_resolution(self) -> tuple[int, int] | None:
        value = self.config.resolution
        if value == display.NATIVE:
            return self.native_resolution()
        if value == display.KEEP:
            return None
        w, _, h = value.partition("x")
        return (int(w), int(h)) if w.isdigit() and h.isdigit() else None

    def on_args_applied(self, entry):
        text = entry.get_text().strip()
        if text == self.config.extra_args:
            return
        try:
            shlex.split(text)
        except ValueError as e:
            self.toast(f"Couldn't read those arguments: {e}")
            return
        self.config.extra_args = text
        self.config.save()
        self.toast("Engine arguments saved")

    def on_add_folder(self, _btn):
        self.choose_folder(None)

    def choose_folder(self, key: str | None, search: bool = False):
        if search:
            title = "Choose a folder or drive to search"
        else:
            title = f"Choose the {GAMES[key].title} folder" if key else "Choose a STALKER game folder"
        Gtk.FileDialog(title=title).select_folder(self, None, self.on_folder_chosen, key, search)

    def on_folder_chosen(self, dialog, result, key: str | None, search: bool):
        try:
            folder = dialog.select_folder_finish(result)
        except GLib.Error:
            return  # cancelled
        path = folder.get_path()
        if path is None or path.startswith("/run/user/") and "/doc/" in path:
            # The Flatpak's file chooser hands out folders it can't otherwise reach through a
            # proxy path, which breaks once the engine follows links into it.
            self.alert("The launcher can't use that folder",
                       "It's outside the folders the launcher may read (your home folder and drives "
                       "under /run/media, /media and /mnt). Move the game there, or give the Flatpak "
                       "access with:\n\nflatpak override --user --filesystem=<folder> "
                       "io.github.mktanner.ZoneLauncher")
            return
        if search:
            self.set_searching(True)

        def work():
            found = detect.search([Path(path)], depth=8, budget=200000) if search else detect.locate(Path(path))
            GLib.idle_add(self.add_found, found, key, Path(path), search)

        threading.Thread(target=work, daemon=True).start()

    def add_found(self, found: list[tuple[str, Path]], key: str | None, picked: Path, search: bool):
        if search:
            self.set_searching(False)
        if not found:
            if key:
                body = f"Nothing in {picked} looks like {GAMES[key].title}. Choose the folder that has {FOLDER_HINT[key]}."
            else:
                body = (f"Nothing in {picked} looks like a STALKER game. Choose the folder that has the game's "
                        "archives (gamedata.db0, or a resources folder), or search a drive.")
            self.alert(f"No {GAMES[key].title if key else 'game'} files found", body)
            return
        for _game, path in found:
            if str(path) not in self.config.extra_folders:
                self.config.extra_folders.append(str(path))
            if str(path) in self.config.hidden_folders:
                self.config.hidden_folders.remove(str(path))
        if key and (match := next((p for g, p in found if g == key), None)):
            self.config.selected[key] = str(match)
        self.config.save()
        titles = sorted({GAMES[g].title for g, _ in found}, key=lambda title: [GAMES[k].title for k in GAMES].index(title))
        if key and key not in {g for g, _ in found}:
            self.toast(f"No {GAMES[key].title} there; added {', '.join(titles)} to {'its' if len(titles) == 1 else 'their'} game")
        else:
            n = len(found)
            self.toast(f"Added {n} folder{'s' if n != 1 else ''}: {', '.join(titles)}")
        self.refresh()

    def forget_folder(self, install: detect.Install):
        path = str(install.path)
        self.config.extra_folders = [p for p in self.config.extra_folders if p != path]
        if path in self.config.found_folders and path not in self.config.hidden_folders:
            self.config.hidden_folders.append(path)  # so the next search doesn't bring it back
        self.config.found_folders = [p for p in self.config.found_folders if p != path]
        if self.config.selected.get(install.game) == path:
            del self.config.selected[install.game]
        self.config.save()
        self.toast("Removed from the list; choose the folder again to bring it back")
        self.refresh()

    def set_searching(self, searching: bool):
        self.searching = searching
        self.refresh_btn.set_sensitive(not searching)
        self.refresh_btn.set_tooltip_text("Searching for games…" if searching else "Look for games again")
        if self.details:
            self.fill_details(*self.details)

    def search_in_background(self):
        """Look through the usual game folders and drives (a few seconds), and remember what's there."""
        if self.searching:
            return
        self.set_searching(True)

        def work():
            found = detect.search(detect.search_roots(Path.home()))
            GLib.idle_add(done, found)

        def done(found):
            self.set_searching(False)
            hidden = set(self.config.hidden_folders)
            paths = sorted(str(p) for _, p in found if str(p) not in hidden)
            known = {str(i.path) for i in self.installs}
            new = [p for p in paths if p not in known]
            if paths != self.config.found_folders:
                self.config.found_folders = paths
                self.config.save()
                self.refresh()
            if new:
                self.toast(f"Found {len(new)} more game folder{'s' if len(new) != 1 else ''}")

        threading.Thread(target=work, daemon=True).start()

    def alert(self, heading: str, body: str):
        dialog = Adw.AlertDialog(heading=heading, body=body)
        dialog.add_response("close", "Close")
        dialog.present(self)

    def apply_fixes(self, install: detect.Install, issues: list[fixes.Issue], then=None):
        """Run fixes off the main thread (language packs are hundreds of MB), then refresh."""
        key = install.game
        self.busy[key] = "Fixing: " + ", ".join(i.title for i in issues)
        self.refresh()

        def work():
            messages, error = [], None
            for issue in issues:
                try:
                    messages.append(issue.fix())
                except OSError as e:
                    error = f"{issue.title}: {e.strerror or e}"
                    break
            GLib.idle_add(done, messages, error)

        def done(messages, error):
            self.busy.pop(key, None)
            self.refresh()
            self.toast(error or "; ".join(messages))
            if then and not error:
                then()

        threading.Thread(target=work, daemon=True).start()

    def on_play(self, _btn, key: str):
        install = self.selected_install(key)
        if not install or not self.engine:
            return
        run, setup_issue = setup.plan(install)
        if setup_issue:
            self.apply_fixes(install, [setup_issue], then=lambda: self.on_play(None, key))
            return
        if run.path != install.path:
            try:
                setup.link(install.path, run.path)  # pick up files added by a game update
            except OSError as e:
                self.toast(f"Couldn't update the game links: {e.strerror or e}")
                return
        pending = [i for i in fixes.check(run, self.engine, self.config.language) if i.blocking and i.fix]
        if pending:
            self.apply_fixes(install, pending, then=lambda: self.start_game(run))
        else:
            self.start_game(run)

    def start_game(self, install: detect.Install):
        started = time.time()
        try:
            for warning in mods.compose(install):
                self.toast(warning)
        except (mods.ModError, OSError) as e:
            dialog = Adw.AlertDialog(heading="Couldn't apply mods", body=str(e))
            dialog.add_response("close", "Close")
            dialog.present(self)
            return
        try:
            display.apply(display.user_ltx(install), self.display_resolution(), self.config.window_mode)
        except OSError as e:
            self.toast(f"Couldn't set the resolution: {e.strerror or e}")
        try:
            proc = engine.launch(self.engine, install, shlex.split(self.config.extra_args))
        except OSError as e:
            self.toast(f"Couldn't start the engine: {e.strerror or e}")
            return
        self.running[install.game] = proc
        self.refresh()

        def exited():
            self.running.pop(install.game, None)
            stopped = install.game in self.stopping
            self.stopping.discard(install.game)
            self.refresh()
            if not stopped:
                self.report_crash(install, started)

        threading.Thread(target=lambda: (proc.wait(), GLib.idle_add(exited)), daemon=True).start()

    def on_stop(self, _btn, key: str):
        proc = self.running.get(key)
        if not proc:
            return
        self.stopping.add(key)
        proc.terminate()

        def force():
            if proc.poll() is None:
                proc.kill()
            return GLib.SOURCE_REMOVE

        GLib.timeout_add_seconds(5, force)

    def report_crash(self, install: detect.Install, started: float):
        log = engine.latest_log(install)
        if not log or log.stat().st_mtime < started:
            return
        summary = engine.crash_summary(log.read_text(errors="replace"))
        if not summary:
            return
        dialog = Adw.AlertDialog(heading=f"{GAMES[install.game].title} crashed", body="The log says:")
        text = Gtk.TextView(editable=False, monospace=True, wrap_mode=Gtk.WrapMode.WORD_CHAR,
                            top_margin=8, bottom_margin=8, left_margin=8, right_margin=8)
        text.get_buffer().set_text(summary)
        scroller = Gtk.ScrolledWindow(child=text, min_content_height=240, min_content_width=520)
        dialog.set_extra_child(scroller)
        dialog.add_response("log", "Open full log")
        dialog.add_response("close", "Close")
        dialog.set_default_response("close")
        dialog.connect("response", lambda _d, r: r == "log" and self.open_file(log))
        dialog.present(self)
