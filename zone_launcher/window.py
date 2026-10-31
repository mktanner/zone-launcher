"""The launcher window: one card per game, with its checks, fixes and a Play button."""

import shlex
import subprocess
import threading
import time
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from . import detect, display, engine, fixes, setup  # noqa: E402
from .config import Config  # noqa: E402
from .games import GAMES, identify  # noqa: E402

LANGUAGES = ["english", "russian", "ukrainian", "german", "french", "italian", "spanish", "polish"]


class LauncherWindow(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application):
        super().__init__(application=app, title="Zone Launcher", default_width=720, default_height=820)
        self.config = Config.load()
        self.engine = engine.find_engine(self.config.engine_override or None)
        self.installs: list[detect.Install] = []
        # Game key -> running engine process, so a game can't be started twice.
        self.running: dict[str, subprocess.Popen] = {}
        # Games the user stopped; their forced exit isn't reported as a crash.
        self.stopping: set[str] = set()
        # Game key -> message while fixes are being applied before launch.
        self.busy: dict[str, str] = {}

        self.toasts = Adw.ToastOverlay()
        view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        add_btn = Gtk.Button(icon_name="folder-new-symbolic", tooltip_text="Add a game folder")
        add_btn.connect("clicked", self.on_add_folder)
        refresh_btn = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="Look for games again")
        refresh_btn.connect("clicked", lambda *_: self.refresh())
        header.pack_start(add_btn)
        header.pack_end(refresh_btn)
        view.add_top_bar(header)

        self.banner = Adw.Banner(title="OpenXRay engine not found. Install it, or set its path below.")
        view.add_top_bar(self.banner)

        self.page = Adw.PreferencesPage()
        view.set_content(self.page)
        self.toasts.set_child(view)
        self.set_content(self.toasts)
        self.groups: list[Adw.PreferencesGroup] = []
        self.refresh()

    # ----- building the page -------------------------------------------------------------

    def refresh(self):
        self.engine = engine.find_engine(self.config.engine_override or None)
        self.banner.set_revealed(self.engine is None)
        self.installs = detect.find_installs(extra=self.config.extra_folders)
        for group in self.groups:
            self.page.remove(group)
        self.groups = [self.build_game_group(key) for key in GAMES]
        self.groups.append(self.build_settings_group())
        for group in self.groups:
            self.page.add(group)

    def game_options(self, key: str) -> list[detect.Install]:
        options = [i for i in self.installs if i.game == key]
        target = setup.target_dir(key)
        if setup.is_own_copy(target):
            # A copy made by hand in the OpenXRay folder is the one to play; leave other installs be.
            return [i for i in options if i.path == target.resolve()]
        return options

    def selected_install(self, key: str) -> detect.Install | None:
        options = self.game_options(key)
        chosen = self.config.selected.get(key)
        return next((i for i in options if str(i.path) == chosen), options[0] if options else None)

    def build_game_group(self, key: str) -> Adw.PreferencesGroup:
        game = GAMES[key]
        group = Adw.PreferencesGroup(title=game.title)
        options = self.game_options(key)
        install = self.selected_install(key)

        play = Gtk.Button(label="Play", valign=Gtk.Align.CENTER, css_classes=["suggested-action", "pill"])
        group.set_header_suffix(play)

        if install is None:
            group.add(Adw.ActionRow(
                title="Not found",
                subtitle="Install it with Heroic, Steam or GOG, or add its folder with the + folder button.",
            ))
            play.set_sensitive(False)
            return group

        if len(options) > 1:
            row = Adw.ComboRow(title="Install", model=Gtk.StringList.new([i.label for i in options]))
            row.set_selected(options.index(install))
            row.connect("notify::selected", self.on_install_chosen, key, options)
            group.add(row)
        else:
            group.add(Adw.ActionRow(title="Install", subtitle=install.label, subtitle_selectable=True, use_markup=False))

        run, setup_issue = setup.plan(install)
        if run.path != install.path and not setup_issue:
            group.add(Adw.ActionRow(title="Plays from", subtitle=f"{run.path}  (linked to the install above)",
                                    subtitle_selectable=True, use_markup=False))
        if setup_issue:
            issues = [setup_issue]
        else:
            issues = fixes.check(run, self.engine, self.config.language) if self.engine else []
        for issue in issues:
            group.add(self.build_issue_row(install, issue))
        stuck = [i for i in issues if i.blocking and not i.fix]

        if key in self.running:
            status, sensitive = "Running…", True
        elif key in self.busy:
            status, sensitive = self.busy[key], False
        elif stuck:
            status, sensitive = "Needs your attention before it can start", False
        elif any(i.blocking for i in issues):
            status, sensitive = "Play will fix these first", True
        else:
            status, sensitive = "Ready to play", True
        status_row = Adw.ActionRow(title=status)
        status_row.add_prefix(Gtk.Image(icon_name="emblem-ok-symbolic" if not issues else "dialog-information-symbolic"))
        log = engine.latest_log(run)
        if log:
            log_btn = Gtk.Button(label="Open log", valign=Gtk.Align.CENTER, css_classes=["flat"])
            log_btn.connect("clicked", lambda *_: self.open_file(log))
            status_row.add_suffix(log_btn)
        group.add(status_row)

        if key in self.running:
            # Also the way out if the engine hangs after its window closes.
            play.set_label("Stop")
            play.set_css_classes(["destructive-action", "pill"])
            play.connect("clicked", self.on_stop, key)
        else:
            play.connect("clicked", self.on_play, key)
        play.set_sensitive(sensitive and self.engine is not None)
        return group

    def build_issue_row(self, install: detect.Install, issue: fixes.Issue) -> Adw.ActionRow:
        row = Adw.ActionRow(title=issue.title, subtitle=issue.detail, use_markup=False)
        row.add_prefix(Gtk.Image(icon_name="dialog-warning-symbolic" if issue.blocking else "dialog-information-symbolic"))
        if issue.fix and install.game not in self.busy:
            btn = Gtk.Button(label="Fix", valign=Gtk.Align.CENTER)
            btn.connect("clicked", lambda *_: self.apply_fixes(install, [issue]))
            row.add_suffix(btn)
        return row

    def build_settings_group(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title="Settings")

        lang = Adw.ComboRow(title="Language to install", subtitle="Used when a game has no language pack yet",
                            model=Gtk.StringList.new([l.title() for l in LANGUAGES]))
        lang.set_selected(LANGUAGES.index(self.config.language) if self.config.language in LANGUAGES else 0)
        lang.connect("notify::selected", self.on_language_chosen)
        group.add(lang)

        args = Adw.EntryRow(title="Extra engine arguments", text=self.config.extra_args, show_apply_button=True)
        args.connect("apply", self.on_args_applied)
        group.add(args)

        # (label, config value) pairs; the first entry of each is the default.
        self.mode_options = [(label, key) for key, (label, _) in display.WINDOW_MODES.items()]
        self.mode_options.append(("Use in-game setting", display.KEEP))
        group.add(self.build_choice_row("Display mode", self.mode_options, "window_mode"))

        native = self.native_resolution()
        self.resolution_options = [
            (f"Native ({native[0]} × {native[1]})" if native else "Native", display.NATIVE),
            *((f"{w} × {h}", f"{w}x{h}") for w, h in display.resolution_choices(native)),
            ("Use in-game setting", display.KEEP),
        ]
        group.add(self.build_choice_row("Resolution", self.resolution_options, "resolution"))

        group.add(Adw.ActionRow(
            title="Engine", subtitle=str(self.engine.binary) if self.engine else "Not found",
            subtitle_selectable=True, use_markup=False,
        ))
        return group

    # ----- actions -----------------------------------------------------------------------

    def open_file(self, path: Path):
        Gtk.FileLauncher(file=Gio.File.new_for_path(str(path))).launch(self, None, None)

    def toast(self, text: str):
        self.toasts.add_toast(Adw.Toast(title=GLib.markup_escape_text(text), timeout=4))

    def on_install_chosen(self, row, _pspec, key, options):
        self.config.selected[key] = str(options[row.get_selected()].path)
        self.config.save()
        GLib.idle_add(self.refresh)

    def on_language_chosen(self, row, _pspec):
        self.config.language = LANGUAGES[row.get_selected()]
        self.config.save()
        GLib.idle_add(self.refresh)

    def build_choice_row(self, title: str, options: list[tuple[str, str]], setting: str) -> Adw.ComboRow:
        row = Adw.ComboRow(title=title, model=Gtk.StringList.new([label for label, _ in options]))
        values = [value for _, value in options]
        current = getattr(self.config, setting)
        row.set_selected(values.index(current) if current in values else 0)

        def chosen(row, _pspec):
            setattr(self.config, setting, values[row.get_selected()])
            self.config.save()

        row.connect("notify::selected", chosen)
        return row

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

    def on_args_applied(self, row):
        try:
            shlex.split(row.get_text())
        except ValueError as e:
            self.toast(f"Couldn't read those arguments: {e}")
            return
        self.config.extra_args = row.get_text()
        self.config.save()
        self.toast("Saved")

    def on_add_folder(self, _btn):
        Gtk.FileDialog(title="Choose a STALKER game folder").select_folder(self, None, self.on_folder_chosen)

    def on_folder_chosen(self, dialog, result):
        try:
            folder = dialog.select_folder_finish(result)
        except GLib.Error:
            return  # cancelled
        path = folder.get_path()
        if path is None or identify(Path(path)) is None:
            self.toast("That folder doesn't look like a STALKER game")
            return
        if path not in self.config.extra_folders:
            self.config.extra_folders.append(path)
            self.config.save()
        self.refresh()

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
