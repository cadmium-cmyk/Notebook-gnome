import sys
import os
import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, Gio, GLib

from window import XNotesViewerWindow


class XNotesApp(Adw.Application):
    def __init__(self):
        super().__init__(
            application_id="io.github.xnotes.Notebook",
            flags=Gio.ApplicationFlags.HANDLES_OPEN
        )

    def do_startup(self):
        Adw.Application.do_startup(self)

        quit_action = Gio.SimpleAction.new("quit", None)
        quit_action.connect("activate", lambda *_: self.quit())
        self.add_action(quit_action)

        self.set_accels_for_action("app.quit", ["<Control>q", "<Control>w"])
        self.set_accels_for_action("win.preferences", ["<Control>comma"])
        self.set_accels_for_action("win.shortcuts", ["<Control>question", "<Control><Shift>question"])
        self.set_accels_for_action("win.about", ["F1"])
        self.set_accels_for_action("win.open", ["<Control>o"])
        self.set_accels_for_action("win.new-notebook", ["<Control>n"])
        self.set_accels_for_action("win.save", ["<Control>s"])
        self.set_accels_for_action("win.save-as", ["<Control><Shift>s"])
        self.set_accels_for_action("win.export-pdf", ["<Control>e"])
        self.set_accels_for_action("win.undo", ["<Control>z"])
        self.set_accels_for_action("win.redo", ["<Control>y", "<Control><Shift>z"])
        self.set_accels_for_action("win.copy", ["<Control>c"])
        self.set_accels_for_action("win.cut", ["<Control>x"])
        self.set_accels_for_action("win.paste", ["<Control>v"])
        self.set_accels_for_action("win.find", ["<Control>f"])
        self.set_accels_for_action("win.back", ["Escape"])
        self.set_accels_for_action("win.zoom-in", ["<Control>plus", "<Control>equal"])
        self.set_accels_for_action("win.zoom-out", ["<Control>minus"])
        self.set_accels_for_action("win.zoom-reset", ["<Control>0"])
        self.set_accels_for_action("win.fit-width", ["<Control><Alt>w"])
        self.set_accels_for_action("win.fit-height", ["<Control><Alt>h"])
        self.set_accels_for_action("win.next-page", ["Page_Down"])
        self.set_accels_for_action("win.prev-page", ["Page_Up"])
        self.set_accels_for_action("win.toggle-view-mode", ["<Control>m"])
        self.set_accels_for_action("win.toggle-zoom-lock", ["<Control>l"])
        self.set_accels_for_action("win.toggle-double-page", ["<Control>d"])

    def do_activate(self):
        win = self.props.active_window
        if not win:
            win = XNotesViewerWindow(self)
            win.load_last_folder()
        win.present()

    def do_open(self, files, n_files, hint):
        self.do_activate()
        win = self.props.active_window
        if win and n_files > 0:
            target = files[0].get_path()
            if target and os.path.exists(target):
                if os.path.isdir(target):
                    win.open_library_folder(target)
                elif os.path.isfile(target):
                    win.open_document_file(target)


def main():
    app = XNotesApp()
    return app.run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
