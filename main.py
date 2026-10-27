#!/usr/bin/env python3
import sys
import os
import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gio

from window import XNotesViewerWindow


class XNotesViewerApp(Adw.Application):
    def __init__(self):
        super().__init__(
            application_id='com.xnotes.viewer',
            flags=Gio.ApplicationFlags.HANDLES_OPEN
        )

    def do_startup(self):
        Adw.Application.do_startup(self)

        quit_action = Gio.SimpleAction.new("quit", None)
        quit_action.connect("activate", lambda *_: self.quit())
        self.add_action(quit_action)

        self.set_accels_for_action("app.quit", ["<Control>q", "<Control>w"])
        self.set_accels_for_action("win.open", ["<Control>o"])
        self.set_accels_for_action("win.new-notebook", ["<Control>n"])
        self.set_accels_for_action("win.save", ["<Control>s"])
        self.set_accels_for_action("win.undo", ["<Control>z"])
        self.set_accels_for_action("win.redo", ["<Control>y", "<Control><Shift>z"])
        self.set_accels_for_action("win.find", ["<Control>f"])
        self.set_accels_for_action("win.back", ["Escape"])
        self.set_accels_for_action("win.zoom-in", ["<Control>plus", "<Control>equal"])
        self.set_accels_for_action("win.zoom-out", ["<Control>minus"])
        self.set_accels_for_action("win.zoom-reset", ["<Control>0"])
        self.set_accels_for_action("win.next-page", ["Page_Down"])
        self.set_accels_for_action("win.prev-page", ["Page_Up"])
        self.set_accels_for_action("win.toggle-view-mode", ["<Control>m"])

    def do_activate(self):
        win = self.props.active_window
        if not win:
            win = XNotesViewerWindow(self)
            win.load_last_folder()
        win.present()

    def do_open(self, files, n_files, hint):
        win = self.props.active_window
        if not win:
            win = XNotesViewerWindow(self)
        win.present()

        if files and n_files > 0:
            path = files[0].get_path()
            if path:
                if os.path.isdir(path):
                    win.load_folder(path)
                else:
                    win.load_folder(os.path.dirname(path))
                    win.load_document(path)
            else:
                win.show_error("Cannot open non-local file URI directly.")


if __name__ == '__main__':
    app = XNotesViewerApp()
    sys.exit(app.run(sys.argv))
