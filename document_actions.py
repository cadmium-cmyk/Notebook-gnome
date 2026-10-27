import os
import gi

gi.require_version('Gtk', '4.0')
from gi.repository import Gtk, Gio, GLib, Gdk

from xnote_io import XNotePackageParser

try:
    gi.require_version('Adw', '1')
    from gi.repository import Adw
    HAS_ADW = True
except (ValueError, ImportError):
    Adw = None
    HAS_ADW = False


class DocumentActionsMixin:
    """Mixin class for XNotesViewerWindow handling PDF import/export, default launcher, app chooser, and unsaved changes confirmation."""

    def on_import_pdf_into_current_clicked(self, *_):
        if not self.canvas.document:
            return
        if hasattr(Gtk, "FileDialog"):
            dialog = Gtk.FileDialog(title="Import PDF Pages into Notebook")
            filters = Gio.ListStore.new(Gtk.FileFilter)
            pdf_filter = Gtk.FileFilter()
            pdf_filter.set_name("PDF Documents (*.pdf)")
            pdf_filter.add_pattern("*.pdf")
            filters.append(pdf_filter)
            dialog.set_filters(filters)
            dialog.open(self, None, self._on_import_pdf_into_current_selected)
        else:
            self.show_error("Gtk.FileDialog is required to import PDF files.")

    def _on_import_pdf_into_current_selected(self, dialog, result):
        try:
            gfile = dialog.open_finish(result)
            if gfile and gfile.get_path():
                pdf_path = gfile.get_path()
                prev_count = len(self.canvas.document.get("pages", []))
                added = XNotePackageParser.import_pdf_into_document(
                    pdf_path, self.canvas.document, self.canvas.assets
                )
                pages = self.canvas.document.get("pages", [])
                self.canvas.page_sizes = [self.canvas.compute_page_size(p) for p in pages]
                self.canvas.current_page = prev_count
                self.canvas.invalidate_page(None)
                self._on_canvas_modified(True)
                self.update_page_View()
                self._rebuild_page_sidebar()
                if self.canvas.view_mode == "scrolled":
                    GLib.idle_add(self.scroll_to_page, self.canvas.current_page)
                self.show_toast(f"Imported {added} PDF page(s) into notebook")
        except GLib.Error:
            pass
        except Exception as e:
            self.show_error(f"Failed to import PDF: {e}")

    def on_import_pdf_as_new_notebook_clicked(self, *_):
        if hasattr(Gtk, "FileDialog"):
            dialog = Gtk.FileDialog(title="Select PDF to Convert into .xnote Notebook")
            filters = Gio.ListStore.new(Gtk.FileFilter)
            pdf_filter = Gtk.FileFilter()
            pdf_filter.set_name("PDF Documents (*.pdf)")
            pdf_filter.add_pattern("*.pdf")
            filters.append(pdf_filter)
            dialog.set_filters(filters)
            dialog.open(self, None, self._on_pdf_chosen_for_new_notebook)
        else:
            self.show_error("Gtk.FileDialog is required to import PDF files.")

    def _on_pdf_chosen_for_new_notebook(self, dialog, result):
        try:
            gfile = dialog.open_finish(result)
            if gfile and gfile.get_path():
                pdf_path = gfile.get_path()
                base_name = os.path.splitext(os.path.basename(pdf_path))[0]
                save_dlg = Gtk.FileDialog(title="Save Imported PDF as .xnote Notebook")
                save_dlg.set_initial_name(f"{base_name}.xnote")
                target_folder = self._get_active_category_folder()
                if target_folder and os.path.isdir(target_folder):
                    save_dlg.set_initial_folder(Gio.File.new_for_path(target_folder))
                save_dlg.save(self, None, self._on_new_pdf_notebook_dest_chosen, pdf_path)
        except GLib.Error:
            pass

    def _on_new_pdf_notebook_dest_chosen(self, dialog, result, pdf_path):
        try:
            gfile = dialog.save_finish(result)
            if gfile and gfile.get_path():
                dest_path = gfile.get_path()
                if not dest_path.lower().endswith(".xnote"):
                    dest_path += ".xnote"
                XNotePackageParser.create_notebook_from_pdf(pdf_path, dest_path)
                if self.current_root_folder:
                    self.load_folder(self.current_root_folder)
                self.load_document(dest_path)
                self.show_toast(f"Created {os.path.basename(dest_path)} from PDF")
        except GLib.Error:
            pass
        except Exception as e:
            self.show_error(f"Failed to create notebook from PDF: {e}")

    def on_export_pdf_clicked(self, *_):
        if not self.canvas.document or self.current_file_type not in ("xnote", "xcanvas", "xtemplate"):
            self.show_error("Open an .xnote notebook to export as PDF.")
            return
        if hasattr(Gtk, "FileDialog"):
            base_name = (
                os.path.splitext(os.path.basename(self.current_file_path))[0]
                if self.current_file_path else "Notebook"
            )
            dialog = Gtk.FileDialog(title="Export Notebook to PDF")
            dialog.set_initial_name(f"{base_name}.pdf")
            if self.current_file_path:
                dialog.set_initial_folder(Gio.File.new_for_path(os.path.dirname(self.current_file_path)))
            dialog.save(self, None, self._on_export_pdf_dest_chosen)
        else:
            self.show_error("Gtk.FileDialog is required to export PDF files.")

    def _on_export_pdf_dest_chosen(self, dialog, result):
        try:
            gfile = dialog.save_finish(result)
            if gfile and gfile.get_path():
                pdf_path = gfile.get_path()
                if not pdf_path.lower().endswith(".pdf"):
                    pdf_path += ".pdf"
                XNotePackageParser.export_to_pdf(pdf_path, self.canvas.document, self.canvas)
                self.show_toast(f"Exported PDF to {os.path.basename(pdf_path)}")
        except GLib.Error:
            pass
        except Exception as e:
            self.show_error(f"Failed to export PDF: {e}")

    def _confirm_unsaved_changes(self, on_proceed_callback):
        if not self.is_modified or self.root_stack.get_visible_child_name() != "viewer":
            on_proceed_callback()
            return

        doc_name = os.path.basename(self.current_file_path) if self.current_file_path else "this note"
        heading = "Save Changes?"
        body = f"“{doc_name}” has unsaved changes. Any unsaved changes will be lost if you discard them."

        if HAS_ADW and hasattr(Adw, "AlertDialog"):
            dialog = Adw.AlertDialog.new(heading, body)
            dialog.add_response("cancel", "Cancel")
            dialog.add_response("discard", "Discard")
            dialog.add_response("save", "Save")
            dialog.set_response_appearance("discard", Adw.ResponseAppearance.DESTRUCTIVE)
            dialog.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
            dialog.set_default_response("save")
            dialog.set_close_response("cancel")

            def on_response(dlg, res):
                choice = dlg.choose_finish(res)
                if choice == "save":
                    if self.on_save_clicked():
                        on_proceed_callback()
                elif choice == "discard":
                    self.is_modified = False
                    on_proceed_callback()

            dialog.choose(self, None, on_response)
        elif HAS_ADW and hasattr(Adw, "MessageDialog"):
            dialog = Adw.MessageDialog.new(self, heading, body)
            dialog.add_response("cancel", "Cancel")
            dialog.add_response("discard", "Discard")
            dialog.add_response("save", "Save")
            dialog.set_response_appearance("discard", Adw.ResponseAppearance.DESTRUCTIVE)
            dialog.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
            dialog.set_default_response("save")
            dialog.set_close_response("cancel")

            def on_legacy_response(dlg, response_id):
                dlg.destroy()
                if response_id == "save":
                    if self.on_save_clicked():
                        on_proceed_callback()
                elif response_id == "discard":
                    self.is_modified = False
                    on_proceed_callback()

            dialog.connect("response", on_legacy_response)
            dialog.present()

    def open_with_default_app(self, path):
        if not path or not os.path.exists(path):
            self.show_error("File path does not exist.")
            return

        gfile = Gio.File.new_for_path(path)
        uri = gfile.get_uri()

        if hasattr(Gtk, "FileLauncher"):
            launcher = Gtk.FileLauncher.new(gfile)
            launcher.launch(self, None, self._on_file_launcher_done, path)
        else:
            try:
                Gio.AppInfo.launch_default_for_uri(uri, None)
            except Exception as e:
                self.show_error(f"Could not open default app: {e}")

    def _on_file_launcher_done(self, launcher, result, path):
        try:
            launcher.launch_finish(result)
        except GLib.Error as e:
            try:
                uri = Gio.File.new_for_path(path).get_uri()
                Gio.AppInfo.launch_default_for_uri(uri, None)
            except Exception as inner_e:
                if "Dismissed" not in e.message and "cancelled" not in e.message.lower():
                    self.show_error(f"Unable to open '{os.path.basename(path)}': {inner_e}")

    def choose_app_for_file(self, path):
        if not path or not os.path.exists(path):
            self.show_error("File path does not exist.")
            return

        gfile = Gio.File.new_for_path(path)
        dialog = Gtk.AppChooserDialog.new(
            self,
            Gtk.DialogFlags.MODAL | Gtk.DialogFlags.DESTROY_WITH_PARENT,
            gfile
        )
        dialog.set_heading(f"Select an application to open “{os.path.basename(path)}”")
        dialog.connect("response", self._on_app_chooser_response, gfile)
        dialog.present()

    def _on_app_chooser_response(self, dialog, response_id, gfile):
        if response_id == Gtk.ResponseType.OK:
            app_info = dialog.get_app_info()
            if app_info:
                try:
                    display = Gdk.Display.get_default()
                    launch_ctx = display.get_app_launch_context() if display else None
                    app_info.launch([gfile], launch_ctx)
                except Exception as e:
                    self.show_error(f"Failed to launch {app_info.get_display_name()}: {e}")
        dialog.destroy()

    def open_containing_folder(self, path):
        if not path:
            return
        folder = path if os.path.isdir(path) else os.path.dirname(path)
        if os.path.isdir(folder):
            self.open_with_default_app(folder)
