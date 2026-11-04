import os
import datetime
import shutil
import cairo
import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Pango', '1.0')
from gi.repository import Gtk, Adw, GLib, Pango

from xnote_io import XNotePackageParser, SUPPORTED_EXTENSIONS
from markdown_renderer import MarkdownRenderer


class LibraryManagerMixin:
    """Mixin class for XNotesViewerWindow handling folder scanning, cached cover thumbnail rendering with Libadwaita spinners, flowbox/listbox card generation, and library sorting."""

    def _get_active_category_folder(self):
        sub = getattr(self, "active_subfolder_filter", None)
        if sub and sub in getattr(self, "notebooks_data", {}):
            folder = self.notebooks_data[sub].get("folder_path")
            if folder and os.path.isdir(folder):
                return folder
        if (
            self.active_notebook_filter not in ("ALL", ".", "UNCATEGORIZED", None)
            and self.active_notebook_filter in getattr(self, "notebooks_data", {})
        ):
            folder = self.notebooks_data[self.active_notebook_filter].get("folder_path")
            if folder and os.path.isdir(folder):
                return folder
        return self.current_root_folder

    def on_new_category_clicked(self, *_):
        if not self.current_root_folder or not os.path.isdir(self.current_root_folder):
            self.show_error("Open a collection folder first before creating a category.")
            return

        heading = "New Category"
        body = "Enter a name for the new category folder:"

        entry = Gtk.Entry(
            placeholder_text="Category Name…",
            hexpand=True,
            margin_top=6
        )

        def create_category_dir(raw_name):
            folder_name = raw_name.strip().strip("/\\")
            if not folder_name or folder_name in (".", ".."):
                return
            target_path = os.path.join(self.current_root_folder, folder_name)
            try:
                os.makedirs(target_path, exist_ok=True)
                self.load_folder(self.current_root_folder)
                self.show_toast(f"Created category “{folder_name}”")
            except Exception as e:
                self.show_error(f"Failed to create category folder: {e}")

        if hasattr(Adw, "AlertDialog"):
            dialog = Adw.AlertDialog.new(heading, body)
            dialog.set_extra_child(entry)
            dialog.add_response("cancel", "Cancel")
            dialog.add_response("create", "Create")
            dialog.set_response_appearance("create", Adw.ResponseAppearance.SUGGESTED)
            dialog.set_default_response("create")
            dialog.set_close_response("cancel")

            entry.connect("activate", lambda *_: dialog.response("create"))

            def on_response(dlg, res):
                choice = dlg.choose_finish(res)
                if choice == "create":
                    create_category_dir(entry.get_text())

            dialog.choose(self, None, on_response)
        elif hasattr(Adw, "MessageDialog"):
            dialog = Adw.MessageDialog.new(self, heading, body)
            dialog.set_extra_child(entry)
            dialog.add_response("cancel", "Cancel")
            dialog.add_response("create", "Create")
            dialog.set_response_appearance("create", Adw.ResponseAppearance.SUGGESTED)
            dialog.set_default_response("create")
            dialog.set_close_response("cancel")

            entry.connect("activate", lambda *_: dialog.response("create"))

            def on_legacy_response(dlg, response_id):
                txt = entry.get_text()
                dlg.destroy()
                if response_id == "create":
                    create_category_dir(txt)

            dialog.connect("response", on_legacy_response)
            dialog.present()

    def on_rename_category_clicked(self, nb_key=None, *_):
        if not self.current_root_folder or not os.path.isdir(self.current_root_folder):
            self.show_error("Open a collection folder first.")
            return

        target_key = nb_key if nb_key is not None else getattr(self, "active_notebook_filter", None)
        if not target_key or target_key in ("ALL", "CANVASES", "__NEW_CATEGORY__", ".", "UNCATEGORIZED"):
            self.show_error("Please select a category to rename.")
            return

        nb_info = self.notebooks_data.get(target_key, {})
        folder_path = nb_info.get("folder_path") or os.path.join(self.current_root_folder, target_key)
        if not os.path.isdir(folder_path):
            self.show_error(f"Category folder “{target_key}” does not exist.")
            return

        old_name = os.path.basename(os.path.normpath(folder_path))
        heading = "Rename Category"
        body = f"Enter a new name for category “{old_name}”:"

        entry = Gtk.Entry(
            text=old_name,
            placeholder_text="Category Name…",
            hexpand=True,
            margin_top=6
        )

        def do_rename(raw_name):
            new_name = raw_name.strip().strip("/\\")
            if not new_name or new_name in (".", ".."):
                return
            if new_name == old_name:
                return
            parent_dir = os.path.dirname(os.path.normpath(folder_path))
            new_path = os.path.join(parent_dir, new_name)
            if os.path.exists(new_path):
                self.show_error(f"A folder named “{new_name}” already exists.")
                return
            try:
                if getattr(self, "current_file_path", None):
                    abs_cat = os.path.abspath(folder_path)
                    abs_file = os.path.abspath(self.current_file_path)
                    try:
                        if os.path.commonpath([abs_cat, abs_file]) == abs_cat:
                            rel_inside = os.path.relpath(abs_file, abs_cat)
                            self.current_file_path = os.path.join(new_path, rel_inside)
                    except ValueError:
                        pass

                os.rename(folder_path, new_path)
                new_key = os.path.relpath(new_path, self.current_root_folder)
                if getattr(self, "active_notebook_filter", None) == target_key:
                    self.active_notebook_filter = new_key
                self.load_folder(self.current_root_folder)
                self.show_toast(f"Renamed category to “{new_name}”")
            except Exception as e:
                self.show_error(f"Failed to rename category: {e}")

        if hasattr(Adw, "AlertDialog"):
            dialog = Adw.AlertDialog.new(heading, body)
            dialog.set_extra_child(entry)
            dialog.add_response("cancel", "Cancel")
            dialog.add_response("rename", "Rename")
            dialog.set_response_appearance("rename", Adw.ResponseAppearance.SUGGESTED)
            dialog.set_default_response("rename")
            dialog.set_close_response("cancel")

            entry.connect("activate", lambda *_: dialog.response("rename"))

            def on_response(dlg, res):
                choice = dlg.choose_finish(res)
                if choice == "rename":
                    do_rename(entry.get_text())

            dialog.choose(self, None, on_response)
        elif hasattr(Adw, "MessageDialog"):
            dialog = Adw.MessageDialog.new(self, heading, body)
            dialog.set_extra_child(entry)
            dialog.add_response("cancel", "Cancel")
            dialog.add_response("rename", "Rename")
            dialog.set_response_appearance("rename", Adw.ResponseAppearance.SUGGESTED)
            dialog.set_default_response("rename")
            dialog.set_close_response("cancel")

            entry.connect("activate", lambda *_: dialog.response("rename"))

            def on_legacy_response(dlg, response_id):
                txt = entry.get_text()
                dlg.destroy()
                if response_id == "rename":
                    do_rename(txt)

            dialog.connect("response", on_legacy_response)
            dialog.present()

    def on_delete_category_clicked(self, nb_key=None, *_):
        if not self.current_root_folder or not os.path.isdir(self.current_root_folder):
            self.show_error("Open a collection folder first.")
            return

        target_key = nb_key if nb_key is not None else getattr(self, "active_notebook_filter", None)
        if not target_key or target_key in ("ALL", "CANVASES", "__NEW_CATEGORY__", ".", "UNCATEGORIZED"):
            self.show_error("Please select a category to delete.")
            return

        nb_info = self.notebooks_data.get(target_key, {})
        folder_path = nb_info.get("folder_path") or os.path.join(self.current_root_folder, target_key)
        if not os.path.isdir(folder_path):
            self.show_error(f"Category folder “{target_key}” does not exist.")
            return

        cat_name = os.path.basename(os.path.normpath(folder_path))
        heading = f"Delete Category “{cat_name}”?"
        body = f"Are you sure you want to delete this category? All contents and notes inside “{cat_name}” will be permanently deleted."

        def do_delete():
            try:
                if getattr(self, "current_file_path", None):
                    abs_cat = os.path.abspath(folder_path)
                    abs_file = os.path.abspath(self.current_file_path)
                    try:
                        if os.path.commonpath([abs_cat, abs_file]) == abs_cat:
                            self._perform_return_to_collection()
                    except ValueError:
                        pass

                shutil.rmtree(folder_path)

                if getattr(self, "active_notebook_filter", None) == target_key:
                    self.active_notebook_filter = "ALL"

                self.load_folder(self.current_root_folder)
                self.show_toast(f"Deleted category “{cat_name}”")
            except Exception as e:
                self.show_error(f"Failed to delete category: {e}")

        if hasattr(Adw, "AlertDialog"):
            dialog = Adw.AlertDialog.new(heading, body)
            dialog.add_response("cancel", "Cancel")
            dialog.add_response("delete", "Delete")
            dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
            dialog.set_default_response("cancel")
            dialog.set_close_response("cancel")

            def on_response(dlg, res):
                choice = dlg.choose_finish(res)
                if choice == "delete":
                    do_delete()

            dialog.choose(self, None, on_response)
        elif hasattr(Adw, "MessageDialog"):
            dialog = Adw.MessageDialog.new(self, heading, body)
            dialog.add_response("cancel", "Cancel")
            dialog.add_response("delete", "Delete")
            dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
            dialog.set_default_response("cancel")
            dialog.set_close_response("cancel")

            def on_legacy_response(dlg, response_id):
                dlg.destroy()
                if response_id == "delete":
                    do_delete()

            dialog.connect("response", on_legacy_response)
            dialog.present()

    def _build_category_popover(self, nb_key, title):
        popover = Gtk.Popover()
        popover.set_autohide(True)
        vbox = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=4,
            margin_top=6,
            margin_bottom=6,
            margin_start=6,
            margin_end=6
        )

        rename_btn = Gtk.Button()
        rename_btn.add_css_class("flat")
        r_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        r_box.append(Gtk.Image.new_from_icon_name("document-edit-symbolic"))
        r_box.append(Gtk.Label(label="Rename Category…", xalign=0.0, hexpand=True))
        rename_btn.set_child(r_box)
        rename_btn.connect("clicked", lambda *_: (popover.popdown(), self.on_rename_category_clicked(nb_key)))
        vbox.append(rename_btn)

        del_btn = Gtk.Button()
        del_btn.add_css_class("flat")
        del_btn.add_css_class("destructive-action")
        d_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        d_box.append(Gtk.Image.new_from_icon_name("user-trash-symbolic"))
        d_box.append(Gtk.Label(label="Delete Category…", xalign=0.0, hexpand=True))
        del_btn.set_child(d_box)
        del_btn.connect("clicked", lambda *_: (popover.popdown(), self.on_delete_category_clicked(nb_key)))
        vbox.append(del_btn)

        popover.set_child(vbox)
        return popover

    def _show_category_popover_for_row(self, row, notebook_key, title, x, y):
        popover = self._build_category_popover(notebook_key, title)
        popover.set_parent(row)
        rect = Gdk.Rectangle()
        rect.x = int(x)
        rect.y = int(y)
        rect.width = 1
        rect.height = 1
        popover.set_pointing_to(rect)
        popover.connect("closed", lambda p: p.unparent())
        popover.popup()

    def _update_category_edit_button(self):
        if not hasattr(self, "category_edit_btn"):
            return
        cat_key = getattr(self, "active_notebook_filter", "ALL")
        if cat_key in ("ALL", "CANVASES", ".", "__NEW_CATEGORY__", "UNCATEGORIZED"):
            self.category_edit_btn.set_visible(False)
        elif hasattr(self, "notebooks_data") and cat_key in self.notebooks_data:
            cat_title = self.notebooks_data[cat_key]["title"]
            self.category_edit_btn.set_popover(self._build_category_popover(cat_key, cat_title))
            self.category_edit_btn.set_tooltip_text(f"Edit Category “{cat_title}”")
            self.category_edit_btn.set_visible(True)
        else:
            self.category_edit_btn.set_visible(False)

    def _select_category(self, nb_key):
        if not hasattr(self, "notebook_listbox"):
            return
        row = self.notebook_listbox.get_first_child()
        while row:
            if getattr(row, "notebook_key", None) == nb_key:
                self.notebook_listbox.select_row(row)
                return
            row = row.get_next_sibling()
        self.active_notebook_filter = nb_key
        self.active_subfolder_filter = None
        self._update_category_buttons()
        self._apply_collection_filters()

    def _select_subfolder(self, sub_key):
        if getattr(self, "active_subfolder_filter", None) == sub_key:
            # Clicking currently active subfolder toggles back to category view
            self.active_subfolder_filter = None
        else:
            self.active_subfolder_filter = sub_key

        self._update_category_buttons()
        self._apply_collection_filters()

    def _on_category_btn_right_clicked(self, btn, sub_key, title, x, y):
        self._show_category_popover_for_row(btn, sub_key, title, x, y)

    def _update_category_buttons(self):
        grids = []
        if hasattr(self, "category_flowbox_grid"):
            grids.append(self.category_flowbox_grid)
        if hasattr(self, "category_flowbox_list"):
            grids.append(self.category_flowbox_list)

        if not grids:
            return

        for g in grids:
            child = g.get_first_child()
            while child is not None:
                nxt = child.get_next_sibling()
                g.remove(child)
                child = nxt

        if not hasattr(self, "notebooks_data") or not self.notebooks_data:
            for g in grids:
                g.set_visible(False)
            return

        cat_key = getattr(self, "active_notebook_filter", "ALL")
        if cat_key in ("ALL", "CANVASES", ".", "UNCATEGORIZED", None):
            for g in grids:
                g.set_visible(False)
            return

        # Show folders within the selected category (../root/category/folder)
        sub_prefix = cat_key + "/"
        sub_prefix_os = cat_key + os.sep
        subdirs = [
            (k, info)
            for k, info in sorted(self.notebooks_data.items(), key=lambda item: item[1].get("dir_name", item[1]["title"]).lower())
            if k != cat_key and (k.startswith(sub_prefix) or k.startswith(sub_prefix_os))
        ]

        if not subdirs:
            for g in grids:
                g.set_visible(False)
            return

        active_sub = getattr(self, "active_subfolder_filter", None)

        for g in grids:
            g.set_visible(True)
            for sub_key, sub_info in subdirs:
                # Name of directory only, not the full path
                dir_name = sub_info.get("dir_name") or os.path.basename(sub_info.get("folder_path", sub_key))

                btn = Gtk.Button()
                btn.add_css_class("pill")
                btn.add_css_class("category-folder-btn")
                is_active = (active_sub == sub_key)
                if is_active:
                    btn.add_css_class("suggested-action")

                hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
                icon = Gtk.Image.new_from_icon_name("folder-symbolic")
                lbl = Gtk.Label(label=dir_name)
                hbox.append(icon)
                hbox.append(lbl)
                btn.set_child(hbox)
                btn.set_tooltip_text(f"Folder “{dir_name}”")

                btn.connect("clicked", lambda _, k=sub_key: self._select_subfolder(k))

                right_click = Gtk.GestureClick.new()
                right_click.set_button(3)
                right_click.connect("pressed", lambda gesture, n_press, x, y, b=btn, k=sub_key, t=dir_name: (
                    self._on_category_btn_right_clicked(b, k, t, x, y)
                ))
                btn.add_controller(right_click)

                g.append(btn)

    def _on_library_view_mode_toggled(self, btn, mode):
        if not btn.get_active() or self.library_view_mode == mode:
            return
        self.library_view_mode = mode
        self.save_config()
        if self.content_stack.get_visible_child_name() in ("grid", "list"):
            self.content_stack.set_visible_child_name(self.library_view_mode)

    def _on_sort_library_action(self, action, param):
        sort_mode = param.get_string()
        if not sort_mode:
            return
        action.set_state(GLib.Variant.new_string(sort_mode))
        self.library_sort_mode = sort_mode
        self.save_config()
        self.library_flowbox.invalidate_sort()
        self.library_listbox.invalidate_sort()

    def _compare_items(self, a, b):
        mode = self.library_sort_mode
        name_a = getattr(a, "file_name", "").lower()
        name_b = getattr(b, "file_name", "").lower()

        if mode == "name_desc":
            return -1 if name_a > name_b else (1 if name_a < name_b else 0)
        elif mode == "date_desc":
            mt_a = getattr(a, "mtime", 0.0)
            mt_b = getattr(b, "mtime", 0.0)
            if mt_a != mt_b:
                return -1 if mt_a > mt_b else 1
        elif mode == "date_asc":
            mt_a = getattr(a, "mtime", 0.0)
            mt_b = getattr(b, "mtime", 0.0)
            if mt_a != mt_b:
                return -1 if mt_a < mt_b else 1
        elif mode == "notebook":
            nb_a = getattr(a, "notebook_title", "").lower()
            nb_b = getattr(b, "notebook_title", "").lower()
            if nb_a != nb_b:
                return -1 if nb_a < nb_b else 1
        elif mode == "type":
            ext_a = getattr(a, "file_ext", "").lower()
            ext_b = getattr(b, "file_ext", "").lower()
            if ext_a != ext_b:
                return -1 if ext_a < ext_b else 1

        return -1 if name_a < name_b else (1 if name_a > name_b else 0)

    def _flowbox_sort_func(self, fb_child1, fb_child2):
        c1 = fb_child1.get_child()
        c2 = fb_child2.get_child()
        if c1 is None or c2 is None:
            return 0
        return self._compare_items(c1, c2)

    def _listbox_sort_func(self, row1, row2):
        return self._compare_items(row1, row2)

    def _on_library_list_row_activated(self, listbox, row):
        path = getattr(row, "file_path", None)
        if path:
            self.load_document(path)

    def _sidebar_header_func(self, row, before):
        section = getattr(row, "section_label", None)
        if section:
            lbl = Gtk.Label(label=section, xalign=0.0)
            lbl.add_css_class("sidebar-section-header")
            row.set_header(lbl)
        else:
            row.set_header(None)

    def on_notebook_row_selected(self, listbox, row):
        if row is None:
            return

        notebook_key = getattr(row, "notebook_key", "ALL")
        notebook_title = getattr(row, "notebook_title", "Library")
        self.active_notebook_filter = notebook_key
        self.active_subfolder_filter = None
        if notebook_key == "ALL":
            self.collection_title.set_title("Library")
        elif notebook_key == "CANVASES":
            self.collection_title.set_title("Canvases")
        elif notebook_key == "UNCATEGORIZED":
            self.collection_title.set_title("Uncategorized")
        else:
            self.collection_title.set_title(notebook_title)
        self._update_category_edit_button()
        self._update_category_buttons()
        self._apply_collection_filters()

    def _apply_collection_filters(self):
        query = self.search_entry.get_text().strip().lower()
        selected_nb = self.active_notebook_filter
        selected_sub = getattr(self, "active_subfolder_filter", None)
        show_md = getattr(self, "show_markdown_files", True)

        fb_child = self.library_flowbox.get_first_child()
        visible_count = 0
        while fb_child is not None:
            card = fb_child.get_child()
            if card is not None:
                nb_key = getattr(card, "notebook_key", "")
                file_name = getattr(card, "file_name", "").lower()
                nb_title = getattr(card, "notebook_title", "").lower()
                file_ext = getattr(card, "file_ext", "").lower()
                if not file_ext:
                    file_path = getattr(card, "file_path", "")
                    file_ext = os.path.splitext(file_path)[1].lower()

                is_md = file_ext in (".md", ".markdown")
                md_allowed = show_md or (not is_md)

                if selected_nb == "ALL":
                    nb_matches = (file_ext != ".xcanvas")
                elif selected_nb == "CANVASES":
                    nb_matches = (file_ext == ".xcanvas")
                elif selected_nb == "UNCATEGORIZED":
                    nb_matches = (
                        (nb_key == "." or (getattr(self, "current_root_folder", None) and os.path.dirname(getattr(card, "file_path", "")) == self.current_root_folder))
                        and file_ext != ".xcanvas"
                    )
                elif selected_sub:
                    nb_matches = (nb_key == selected_sub)
                else:
                    nb_matches = (
                        nb_key == selected_nb
                        or nb_key.startswith(selected_nb + "/")
                        or nb_key.startswith(selected_nb + os.sep)
                    )

                query_matches = (not query) or (query in file_name) or (query in nb_title)
                is_vis = bool(nb_matches and query_matches and md_allowed)
                fb_child.set_visible(is_vis)
                if is_vis:
                    visible_count += 1
            fb_child = fb_child.get_next_sibling()

        l_row = self.library_listbox.get_first_child()
        while l_row is not None:
            nb_key = getattr(l_row, "notebook_key", "")
            file_name = getattr(l_row, "file_name", "").lower()
            nb_title = getattr(l_row, "notebook_title", "").lower()
            file_ext = getattr(l_row, "file_ext", "").lower()
            if not file_ext:
                file_path = getattr(l_row, "file_path", "")
                file_ext = os.path.splitext(file_path)[1].lower()

            is_md = file_ext in (".md", ".markdown")
            md_allowed = show_md or (not is_md)

            if selected_nb == "ALL":
                nb_matches = (file_ext != ".xcanvas")
            elif selected_nb == "CANVASES":
                nb_matches = (file_ext == ".xcanvas")
            elif selected_nb == "UNCATEGORIZED":
                nb_matches = (
                    (nb_key == "." or (getattr(self, "current_root_folder", None) and os.path.dirname(getattr(l_row, "file_path", "")) == self.current_root_folder))
                    and file_ext != ".xcanvas"
                )
            elif selected_sub:
                nb_matches = (nb_key == selected_sub)
            else:
                nb_matches = (
                    nb_key == selected_nb
                    or nb_key.startswith(selected_nb + "/")
                    or nb_key.startswith(selected_nb + os.sep)
                )

            query_matches = (not query) or (query in file_name) or (query in nb_title)
            l_row.set_visible(nb_matches and query_matches and md_allowed)
            l_row = l_row.get_next_sibling()

        if not getattr(self, "current_root_folder", None):
            self.content_stack.set_visible_child_name("empty")
            return

        if visible_count == 0:
            if query:
                if hasattr(self, "empty_search_page"):
                    self.content_stack.set_visible_child_name("empty_search")
                else:
                    self.content_stack.set_visible_child_name(self.library_view_mode)
            elif selected_nb == "CANVASES":
                if hasattr(self, "empty_canvases_page"):
                    self.content_stack.set_visible_child_name("empty_canvases")
                else:
                    self.content_stack.set_visible_child_name(self.library_view_mode)
            elif selected_nb == "ALL":
                if hasattr(self, "empty_notes_page"):
                    self.content_stack.set_visible_child_name("empty_notes")
                else:
                    self.content_stack.set_visible_child_name(self.library_view_mode)
            elif selected_nb == "UNCATEGORIZED":
                if hasattr(self, "empty_category_page"):
                    self.empty_category_page.set_title("No Notes in Uncategorized")
                    self.content_stack.set_visible_child_name("empty_category")
                else:
                    self.content_stack.set_visible_child_name(self.library_view_mode)
            else:
                if hasattr(self, "empty_category_page"):
                    if selected_sub:
                        sub_info = self.notebooks_data.get(selected_sub, {}) if hasattr(self, "notebooks_data") else {}
                        sub_title = sub_info.get("dir_name", os.path.basename(selected_sub))
                        self.empty_category_page.set_title(f"No Notes in “{sub_title}”")
                    else:
                        cat_info = self.notebooks_data.get(selected_nb, {}) if hasattr(self, "notebooks_data") else {}
                        nb_title = cat_info.get("title", selected_nb)
                        self.empty_category_page.set_title(f"No Notes in “{nb_title}”")
                    self.content_stack.set_visible_child_name("empty_category")
                else:
                    self.content_stack.set_visible_child_name(self.library_view_mode)
        else:
            self.content_stack.set_visible_child_name(self.library_view_mode)

    def load_folder(self, path):
        try:
            self._folder_load_id += 1
            current_load_id = self._folder_load_id

            self.current_root_folder = path
            self.save_config(path=path)
            self.current_folder_name = os.path.basename(os.path.normpath(path))
            self.collection_title.set_title("Library")

            if hasattr(self, "library_sidebar_btn"):
                self.library_sidebar_btn.set_visible(True)
                self.library_sidebar_btn.set_active(True)
            if hasattr(self, "split_view") and hasattr(self.split_view, "set_show_sidebar"):
                self.split_view.set_show_sidebar(True)

            if hasattr(self, "_library_card_widgets"):
                self._library_card_widgets.clear()
            else:
                self._library_card_widgets = {}

            if hasattr(self, "category_flowbox_grid"):
                child = self.category_flowbox_grid.get_first_child()
                while child is not None:
                    nxt = child.get_next_sibling()
                    self.category_flowbox_grid.remove(child)
                    child = nxt

            if hasattr(self, "category_flowbox_list"):
                child = self.category_flowbox_list.get_first_child()
                while child is not None:
                    nxt = child.get_next_sibling()
                    self.category_flowbox_list.remove(child)
                    child = nxt

            child = self.library_flowbox.get_first_child()
            while child is not None:
                nxt = child.get_next_sibling()
                self.library_flowbox.remove(child)
                child = nxt

            lchild = self.library_listbox.get_first_child()
            while lchild is not None:
                nxt = lchild.get_next_sibling()
                self.library_listbox.remove(lchild)
                lchild = nxt

            row = self.notebook_listbox.get_first_child()
            while row is not None:
                nxt = row.get_next_sibling()
                self.notebook_listbox.remove(row)
                row = nxt

            self.executor.submit(self._scan_folder_worker, path, current_load_id)
        except Exception as e:
            self.show_error(f"Failed to load folder {os.path.basename(path)}: {e}")

    def _scan_folder_worker(self, root_path, load_id):
        """Discover Categories (folders) and note files inside root_path."""
        notebooks = {}
        root_name = os.path.basename(os.path.normpath(root_path)) or "Root Category"

        for cur_root, dirs, files in os.walk(root_path):
            dirs[:] = sorted([d for d in dirs if not d.startswith('.')])
            valid_files = sorted([
                f for f in files
                if not f.startswith('.') and f.lower().endswith(SUPPORTED_EXTENSIONS)
            ])

            rel_dir = os.path.relpath(cur_root, root_path)
            if rel_dir == ".":
                nb_key = "."
                nb_title = root_name
                dir_name = root_name
            else:
                nb_key = rel_dir
                nb_title = rel_dir.replace(os.sep, " / ")
                dir_name = os.path.basename(cur_root)

            notebooks[nb_key] = {
                "key": nb_key,
                "title": nb_title,
                "dir_name": dir_name,
                "folder_path": cur_root,
                "files": [os.path.join(cur_root, f) for f in valid_files]
            }

        # Build the sidebar AND placeholder cards (with Libadwaita spinners) immediately on the main thread
        GLib.idle_add(self._build_notebooks_skeleton, notebooks, load_id)

        for nb_key, nb_info in notebooks.items():
            for full_path in nb_info["files"]:
                if self._folder_load_id != load_id:
                    return
                self.executor.submit(
                    self._render_thumbnail_worker,
                    nb_key,
                    nb_info["title"],
                    full_path,
                    load_id
                )

    def _create_sidebar_row(self, title, notebook_key, icon_name="folder-symbolic", section_label=None, is_new_category_action=False):
        row = Gtk.ListBoxRow()
        row.notebook_key = notebook_key
        row.notebook_title = title
        row.section_label = section_label
        row.is_new_category_action = is_new_category_action
        row.set_tooltip_text(title)

        hbox = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=12,
            margin_start=10,
            margin_end=10,
            margin_top=8,
            margin_bottom=8
        )
        icon = Gtk.Image.new_from_icon_name(icon_name)
        hbox.append(icon)

        lbl = Gtk.Label(
            label=title,
            xalign=0.0,
            hexpand=True,
            ellipsize=Pango.EllipsizeMode.END
        )
        hbox.append(lbl)

        if notebook_key not in ("ALL", "CANVASES", "__NEW_CATEGORY__", ".", "UNCATEGORIZED"):
            right_click = Gtk.GestureClick.new()
            right_click.set_button(3)
            right_click.connect("pressed", lambda gesture, n_press, x, y, r=row, k=notebook_key, t=title: (
                self.notebook_listbox.select_row(r),
                self._show_category_popover_for_row(r, k, t, x, y)
            ))
            row.add_controller(right_click)

        row.set_child(hbox)
        return row

    def _build_notebooks_skeleton(self, notebooks, load_id):
        if self._folder_load_id != load_id:
            return False

        self.notebooks_data = notebooks
        prev_filter = getattr(self, "active_notebook_filter", "ALL")
        if prev_filter == "CANVASES":
            target_filter = "CANVASES"
        elif prev_filter == "UNCATEGORIZED":
            target_filter = "UNCATEGORIZED"
        elif prev_filter != "ALL" and prev_filter in notebooks:
            target_filter = prev_filter
        else:
            target_filter = "ALL"
        self.active_notebook_filter = target_filter

        all_row = self._create_sidebar_row(
            "Library",
            "ALL",
            icon_name="ephy-reader-mode-symbolic",
            section_label=None
        )
        self.notebook_listbox.append(all_row)
        if target_filter == "ALL":
            self.notebook_listbox.select_row(all_row)

        canvases_row = self._create_sidebar_row(
            "Canvases",
            "CANVASES",
            icon_name="image-alt-symbolic",
            section_label=None
        )
        self.notebook_listbox.append(canvases_row)
        if target_filter == "CANVASES":
            self.notebook_listbox.select_row(canvases_row)

        # Show direct folders in the root folder on the sidebar as categories
        root_categories = [
            (nb_key, nb_info)
            for nb_key, nb_info in sorted(notebooks.items(), key=lambda item: item[1]["title"].lower())
            if nb_key != "." and (os.sep not in nb_key) and ("/" not in nb_key)
        ]

        parent_cat = target_filter.split("/")[0].split(os.sep)[0]
        for nb_key, nb_info in root_categories:
            row = self._create_sidebar_row(
                nb_info["title"],
                nb_key,
                icon_name="feather-tag-symbolic",
                section_label=None
            )
            self.notebook_listbox.append(row)
            if target_filter == nb_key or parent_cat == nb_key:
                self.notebook_listbox.select_row(row)

        uncat_row = self._create_sidebar_row(
            "Uncategorized",
            "UNCATEGORIZED",
            icon_name="folder-symbolic",
            section_label=None
        )
        self.notebook_listbox.append(uncat_row)
        if target_filter == "UNCATEGORIZED":
            self.notebook_listbox.select_row(uncat_row)

        if target_filter != parent_cat and target_filter != "UNCATEGORIZED":
            self.active_notebook_filter = parent_cat
            self.active_subfolder_filter = target_filter

        if target_filter == "ALL":
            self.collection_title.set_title("Library")
        elif target_filter == "CANVASES":
            self.collection_title.set_title("Canvases")
        elif target_filter == "UNCATEGORIZED":
            self.collection_title.set_title("Uncategorized")
        elif parent_cat in notebooks:
            self.collection_title.set_title(notebooks[parent_cat]["title"])
        self._update_category_edit_button()
        self._update_category_buttons()

        # Immediately populate card skeletons with Libadwaita spinners (or cached thumbnails if unchanged)
        thumb_cache = getattr(self, "_library_thumb_cache", {})
        for nb_key, nb_info in notebooks.items():
            for full_path in nb_info["files"]:
                try:
                    mtime = os.path.getmtime(full_path)
                except OSError:
                    mtime = 0.0
                cached_entry = thumb_cache.get((full_path, mtime))
                if cached_entry is not None:
                    surf, tw, th = cached_entry
                    self._create_or_update_library_card(nb_key, nb_info["title"], full_path, surf, tw, th, mtime)
                else:
                    self._create_or_update_library_card(nb_key, nb_info["title"], full_path, None, 148, 210, mtime)

        self._apply_collection_filters()
        self.library_flowbox.invalidate_sort()
        self.library_listbox.invalidate_sort()
        if self.root_stack.get_visible_child_name() != "viewer":
            self.root_stack.set_visible_child_name("collection")
        return False

    def _render_thumbnail_worker(self, nb_key, nb_title, path, load_id):
        if self._folder_load_id != load_id:
            return
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            mtime = 0.0

        thumb_cache = getattr(self, "_library_thumb_cache", None)
        if thumb_cache is not None and (path, mtime) in thumb_cache:
            return

        ext = os.path.splitext(path)[1].lower()
        try:
            if ext in ('.md', '.markdown'):
                thumb_w, thumb_h = 150, 210
                surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, thumb_w, thumb_h)
                cr = cairo.Context(surface)
                with open(path, 'r', encoding='utf-8', errors='replace') as f:
                    preview_text = f.read(2400)
                MarkdownRenderer.draw_markdown_thumb(cr, thumb_w, thumb_h, preview_text)
                surface.flush()
                if thumb_cache is not None:
                    thumb_cache[(path, mtime)] = (surface, thumb_w, thumb_h)
                GLib.idle_add(
                    self._append_thumbnail_widget,
                    nb_key,
                    nb_title,
                    path,
                    surface,
                    thumb_w,
                    thumb_h,
                    load_id
                )
            elif ext in ('.xnote', '.xcanvas', '.xtemplate'):
                doc, assets, templates = XNotePackageParser.load_archive(path, thumbnails_only=True)
                if doc.get("format") == "xcanvas":
                    thumb_w = 148
                    thumb_h = 210
                    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, thumb_w, thumb_h)
                    cr = cairo.Context(surface)
                    self.canvas.draw_canvas_thumb(cr, thumb_w, thumb_h, doc, assets, templates)
                    surface.flush()
                else:
                    pages = doc.get("pages", [])
                    if not pages:
                        return

                    page = pages[0]
                    pw, ph = self.canvas.compute_page_size(page)
                    thumb_height = 210.0
                    scale = thumb_height / ph if ph > 0 else 1.0
                    thumb_w = max(130, min(260, int(pw * scale)))
                    thumb_h = int(thumb_height)

                    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, thumb_w, thumb_h)
                    cr = cairo.Context(surface)
                    self.canvas.draw_page_thumb(cr, thumb_w, thumb_h, doc, page, pw, ph, assets, templates, page_idx=0)
                    surface.flush()

                if thumb_cache is not None:
                    thumb_cache[(path, mtime)] = (surface, thumb_w, thumb_h)
                GLib.idle_add(
                    self._append_thumbnail_widget,
                    nb_key,
                    nb_title,
                    path,
                    surface,
                    thumb_w,
                    thumb_h,
                    load_id
                )
        except Exception as e:
            print(f"Failed to generate thumbnail for {path}: {e}")

    def _create_or_update_library_card(self, nb_key, nb_title, path, surface, thumb_w, thumb_h, mtime):
        if not hasattr(self, "_library_card_widgets"):
            self._library_card_widgets = {}

        existing = self._library_card_widgets.get(path)
        if existing is not None:
            existing["surface"] = surface
            existing["thumb_w"] = thumb_w
            existing["thumb_h"] = thumb_h
            da = existing["drawing_area"]
            da.set_size_request(thumb_w, thumb_h)
            existing["card_box"].set_size_request(thumb_w, -1)
            sp = existing.get("spinner")
            if sp is not None:
                sp.set_visible(surface is None)
            da.queue_draw()
            return

        ext = os.path.splitext(path)[1].lower()
        name_without_ext = os.path.splitext(os.path.basename(path))[0]

        card_state = {
            "surface": surface,
            "thumb_w": thumb_w,
            "thumb_h": thumb_h,
        }

        # 1. Build Grid View Card with Libadwaita Spinner Overlay
        drawing_area = Gtk.DrawingArea()
        drawing_area.set_size_request(thumb_w, thumb_h)
        drawing_area.set_halign(Gtk.Align.START)

        def draw_thumb(da, cr, width, height, state=card_state):
            surf = state.get("surface")
            if surf is not None:
                cr.set_source_surface(surf, 0, 0)
                cr.paint()
            else:
                # Subtle rounded paper placeholder while Adw.Spinner spins
                self.canvas._rounded_rect(cr, 0, 0, width, height, 12.0)
                cr.set_source_rgba(0.96, 0.96, 0.97, 1.0)
                cr.fill_preserve()
                cr.set_source_rgba(0.0, 0.0, 0.0, 0.12)
                cr.set_line_width(1.5)
                cr.stroke()

        drawing_area.set_draw_func(draw_thumb)

        overlay = Gtk.Overlay()
        overlay.set_child(drawing_area)
        if hasattr(self, "create_adw_spinner"):
            spinner = self.create_adw_spinner(size=28)
        elif hasattr(Adw, "Spinner"):
            spinner = Adw.Spinner()
            spinner.set_size_request(28, 28)
            spinner.set_halign(Gtk.Align.CENTER)
            spinner.set_valign(Gtk.Align.CENTER)
        else:
            spinner = Gtk.Spinner(spinning=True, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
            spinner.set_size_request(28, 28)

        spinner.set_visible(surface is None)
        overlay.add_overlay(spinner)

        preview_btn = Gtk.Button()
        preview_btn.set_child(overlay)
        preview_btn.add_css_class("flat")
        preview_btn.add_css_class("cover-btn")
        preview_btn.set_halign(Gtk.Align.START)
        preview_btn.set_tooltip_text(os.path.basename(path))
        preview_btn.connect("clicked", lambda *_, p=path: self.load_document(p))

        card_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        card_box.add_css_class("note-card")
        card_box.set_halign(Gtk.Align.START)
        card_box.set_size_request(thumb_w, -1)
        card_box.notebook_key = nb_key
        card_box.file_path = path
        card_box.file_name = os.path.basename(path)
        card_box.notebook_title = nb_title
        card_box.mtime = mtime
        card_box.file_ext = ext

        card_box.append(preview_btn)

        title_lbl = Gtk.Label(
            label=name_without_ext,
            xalign=0.0,
            hexpand=True,
            margin_top=4
        )
        title_lbl.add_css_class("card-title")
        title_lbl.set_ellipsize(Pango.EllipsizeMode.END)
        title_lbl.set_max_width_chars(18)
        card_box.append(title_lbl)

        meta_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        meta_row.set_halign(Gtk.Align.FILL)

        sub_lbl = Gtk.Label(
            label=nb_title,
            xalign=0.0,
            hexpand=True
        )
        sub_lbl.add_css_class("card-subtitle")
        sub_lbl.set_ellipsize(Pango.EllipsizeMode.END)
        sub_lbl.set_max_width_chars(15)
        meta_row.append(sub_lbl)

        menu_btn = Gtk.MenuButton(
            icon_name="view-more-horizontal-symbolic",
            tooltip_text="More Options"
        )
        menu_btn.add_css_class("flat")
        menu_btn.add_css_class("card-more-btn")
        popover = self._build_card_popover(path)
        menu_btn.set_popover(popover)
        meta_row.append(menu_btn)

        card_box.append(meta_row)

        right_click = Gtk.GestureClick.new()
        right_click.set_button(3)
        right_click.connect("pressed", lambda gesture, n_press, x, y, mb=menu_btn: mb.popup())
        card_box.add_controller(right_click)

        self.library_flowbox.append(card_box)

        # 2. Build List View Row
        list_row = Gtk.ListBoxRow()
        list_row.add_css_class("library-list-row")
        list_row.notebook_key = nb_key
        list_row.file_path = path
        list_row.file_name = os.path.basename(path)
        list_row.notebook_title = nb_title
        list_row.mtime = mtime
        list_row.file_ext = ext

        row_hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)

        info_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, valign=Gtk.Align.CENTER, hexpand=True)
        row_title_lbl = Gtk.Label(
            label=name_without_ext,
            xalign=0.0,
            ellipsize=Pango.EllipsizeMode.END
        )
        row_title_lbl.add_css_class("card-title")
        info_vbox.append(row_title_lbl)

        row_sub_lbl = Gtk.Label(
            label=nb_title,
            xalign=0.0,
            ellipsize=Pango.EllipsizeMode.END
        )
        row_sub_lbl.add_css_class("card-subtitle")
        info_vbox.append(row_sub_lbl)
        row_hbox.append(info_vbox)

        type_badge = Gtk.Label(
            label=ext.lstrip(".").upper(),
            valign=Gtk.Align.CENTER
        )
        type_badge.add_css_class("type-pill")
        row_hbox.append(type_badge)

        date_str = (
            datetime.datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
            if mtime > 0 else ""
        )
        date_lbl = Gtk.Label(label=date_str, valign=Gtk.Align.CENTER, margin_start=6, margin_end=6)
        date_lbl.add_css_class("card-subtitle")
        row_hbox.append(date_lbl)

        row_menu_btn = Gtk.MenuButton(
            icon_name="view-more-horizontal-symbolic",
            valign=Gtk.Align.CENTER,
            tooltip_text="More Options"
        )
        row_menu_btn.add_css_class("flat")
        row_menu_btn.add_css_class("card-more-btn")
        row_menu_btn.set_popover(self._build_card_popover(path))
        row_hbox.append(row_menu_btn)

        list_row.set_child(row_hbox)
        self.library_listbox.append(list_row)

        card_state["drawing_area"] = drawing_area
        card_state["card_box"] = card_box
        card_state["spinner"] = spinner
        self._library_card_widgets[path] = card_state

    def _append_thumbnail_widget(self, nb_key, nb_title, path, surface, thumb_w, thumb_h, load_id):
        if self._folder_load_id != load_id:
            return False

        try:
            mtime = os.path.getmtime(path)
        except OSError:
            mtime = 0.0

        self._create_or_update_library_card(nb_key, nb_title, path, surface, thumb_w, thumb_h, mtime)
        self._apply_collection_filters()
        self.library_flowbox.invalidate_sort()
        self.library_listbox.invalidate_sort()
        return False

    def _build_card_popover(self, path):
        popover = Gtk.Popover()
        popover.set_autohide(True)
        vbox = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=4,
            margin_top=6,
            margin_bottom=6,
            margin_start=6,
            margin_end=6
        )

        view_btn = Gtk.Button(label="Open")
        view_btn.add_css_class("flat")
        view_btn.connect("clicked", lambda *_: (popover.popdown(), self.load_document(path)))
        vbox.append(view_btn)

        choose_btn = Gtk.Button(label="Open With...")
        choose_btn.add_css_class("flat")
        choose_btn.connect("clicked", lambda *_: (popover.popdown(), self.choose_app_for_file(path)))
        vbox.append(choose_btn)

        rename_btn = Gtk.Button(label="Rename…")
        rename_btn.add_css_class("flat")
        rename_btn.connect("clicked", lambda *_: (popover.popdown(), self.rename_note_file(path)))
        vbox.append(rename_btn)

        vbox.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        folder_btn = Gtk.Button(label="Show in Folder")
        folder_btn.add_css_class("flat")
        folder_btn.connect("clicked", lambda *_: (popover.popdown(), self.open_containing_folder(path)))
        vbox.append(folder_btn)

        del_btn = Gtk.Button(label="Delete")
        del_btn.add_css_class("flat")
        del_btn.add_css_class("destructive-action")
        del_btn.connect("clicked", lambda *_: (popover.popdown(), self.delete_note_file(path)))
        vbox.append(del_btn)

        popover.set_child(vbox)
        return popover

    def delete_note_file(self, path):
        if not path or not os.path.exists(path):
            self.show_error("Note file does not exist.")
            return

        file_name = os.path.basename(path)
        heading = f"Delete “{file_name}”?"
        body = f"Are you sure you want to delete “{file_name}”? This action cannot be undone."

        def do_delete():
            try:
                if getattr(self, "current_file_path", None):
                    if os.path.abspath(self.current_file_path) == os.path.abspath(path):
                        self._perform_return_to_collection()

                if os.path.isfile(path):
                    os.remove(path)
                elif os.path.isdir(path):
                    shutil.rmtree(path)

                thumb_cache = getattr(self, "_library_thumb_cache", None)
                if thumb_cache:
                    keys_to_del = [k for k in list(thumb_cache.keys()) if k[0] == path]
                    for k in keys_to_del:
                        thumb_cache.pop(k, None)

                if self.current_root_folder and os.path.isdir(self.current_root_folder):
                    self.load_folder(self.current_root_folder)
                self.show_toast(f"Deleted “{file_name}”")
            except Exception as e:
                self.show_error(f"Failed to delete note: {e}")

        if hasattr(Adw, "AlertDialog"):
            dialog = Adw.AlertDialog.new(heading, body)
            dialog.add_response("cancel", "Cancel")
            dialog.add_response("delete", "Delete")
            dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
            dialog.set_default_response("cancel")
            dialog.set_close_response("cancel")

            def on_response(dlg, res):
                choice = dlg.choose_finish(res)
                if choice == "delete":
                    do_delete()

            dialog.choose(self, None, on_response)
        elif hasattr(Adw, "MessageDialog"):
            dialog = Adw.MessageDialog.new(self, heading, body)
            dialog.add_response("cancel", "Cancel")
            dialog.add_response("delete", "Delete")
            dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
            dialog.set_default_response("cancel")
            dialog.set_close_response("cancel")

            def on_legacy_response(dlg, response_id):
                dlg.destroy()
                if response_id == "delete":
                    do_delete()

            dialog.connect("response", on_legacy_response)
            dialog.present()


    def rename_note_file(self, path):
        if not path or not os.path.exists(path):
            self.show_error("Note file does not exist.")
            return

        parent_dir = os.path.dirname(os.path.abspath(path))
        full_filename = os.path.basename(path)
        base_name, ext = os.path.splitext(full_filename)

        heading = "Rename Note"
        body = f"Enter a new name for “{base_name}”:"

        entry = Gtk.Entry(
            text=base_name,
            placeholder_text="Note Title…",
            hexpand=True,
            margin_top=6
        )

        def do_rename(raw_name):
            new_title = raw_name.strip()
            if not new_title or new_title in (".", ".."):
                return
            if not new_title.lower().endswith(ext.lower()):
                new_filename = f"{new_title}{ext}"
            else:
                new_filename = new_title
                new_title = os.path.splitext(new_filename)[0]

            if new_filename == full_filename:
                return

            new_path = os.path.join(parent_dir, new_filename)
            if os.path.exists(new_path):
                self.show_error(f"A file named “{new_filename}” already exists.")
                return

            try:
                os.rename(path, new_path)

                if getattr(self, "current_file_path", None):
                    if os.path.abspath(self.current_file_path) == os.path.abspath(path):
                        self.current_file_path = new_path
                        if hasattr(self, "window_title"):
                            self.window_title.set_title(new_title)

                thumb_cache = getattr(self, "_library_thumb_cache", None)
                if thumb_cache:
                    keys_to_update = [k for k in list(thumb_cache.keys()) if k[0] == path]
                    for old_k in keys_to_update:
                        val = thumb_cache.pop(old_k)
                        try:
                            new_mtime = os.path.getmtime(new_path)
                        except OSError:
                            new_mtime = old_k[1]
                        thumb_cache[(new_path, new_mtime)] = val

                if self.current_root_folder and os.path.isdir(self.current_root_folder):
                    self.load_folder(self.current_root_folder)
                self.show_toast(f"Renamed to “{new_title}”")
            except Exception as e:
                self.show_error(f"Failed to rename note: {e}")

        if hasattr(Adw, "AlertDialog"):
            dialog = Adw.AlertDialog.new(heading, body)
            dialog.set_extra_child(entry)
            dialog.add_response("cancel", "Cancel")
            dialog.add_response("rename", "Rename")
            dialog.set_response_appearance("rename", Adw.ResponseAppearance.SUGGESTED)
            dialog.set_default_response("rename")
            dialog.set_close_response("cancel")

            entry.connect("activate", lambda *_: dialog.response("rename"))

            def on_response(dlg, res):
                choice = dlg.choose_finish(res)
                if choice == "rename":
                    do_rename(entry.get_text())

            dialog.choose(self, None, on_response)
        elif hasattr(Adw, "MessageDialog"):
            dialog = Adw.MessageDialog.new(self, heading, body)
            dialog.set_extra_child(entry)
            dialog.add_response("cancel", "Cancel")
            dialog.add_response("rename", "Rename")
            dialog.set_response_appearance("rename", Adw.ResponseAppearance.SUGGESTED)
            dialog.set_default_response("rename")
            dialog.set_close_response("cancel")

            entry.connect("activate", lambda *_: dialog.response("rename"))

            def on_legacy_response(dlg, response_id):
                txt = entry.get_text()
                dlg.destroy()
                if response_id == "rename":
                    do_rename(txt)

            dialog.connect("response", on_legacy_response)
            dialog.present()

        GLib.idle_add(lambda: (entry.grab_focus(), entry.select_region(0, -1)))
