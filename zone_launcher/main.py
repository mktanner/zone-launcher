import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw  # noqa: E402

from . import APP_ID  # noqa: E402
from .window import LauncherWindow  # noqa: E402


class LauncherApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID)

    def do_activate(self):
        (self.get_active_window() or LauncherWindow(self)).present()


def main() -> int:
    return LauncherApp().run(sys.argv)
