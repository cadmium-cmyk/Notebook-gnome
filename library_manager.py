import os
import datetime
import cairo
import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Pango', '1.0')
from gi.repository import Gtk, GLib, Pango

from xnote_io import XNotePackageParser, SUPPORTED_EXTENSIONS
from markdown_renderer import MarkdownRenderer


class LibraryManagerMixin:
    """Mixin class for XNotesViewerWindow handling folder scanning, thumbnail rendering, flowbox/listbox card generation, and library sorting."""

    def _get_active_category_folder(self):
        if (
            self.active_notebook_filter not in ("ALL", ".", None)
            and self.active_notebook_filter in self.notebooks_data
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

        import gi
        try:
            gi.require_version('Adw', '1')
            from gi.repository import Adw
            HAS_ADW = True
        except (ValueError, ImportError):
            HAS_ADW = False

        if HAS_ADW and hasattr(Adw, "AlertDialog"):
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
        elif HAS_ADW and hasattr(Adw, "MessageDialog"):
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

    def _on_library_view_mode_toggled(self, btn, mode):
        if not btn.get_active() or self.library_view_mode == mode:
            return
        self.library_view_mode = mode
        self.save_config()
        if self.content_stack.get_visible_child_name() != "empty":
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
        if getattr(row, "is_new_category_action", False):
            target_key = self.active_notebook_filter
            idx = 0
            while True:
                r = self.notebook_listbox.get_row_at_index(idx)
                if r is None:
                    break
                if not getattr(r, "is_new_category_action", False) and getattr(r, "notebook_key", None) == target_key:
                    self.notebook_listbox.select_row(r)
                    break
                idx += 1
            self.on_new_category_clicked()
            return

        notebook_key = getattr(row, "notebook_key", "ALL")
        notebook_title = getattr(row, "notebook_title", "Library")
        self.active_notebook_filter = notebook_key
        self.collection_title.set_title("Library" if notebook_key == "ALL" else notebook_title)
        self._apply_collection_filters()

    def _apply_collection_filters(self):
        query = self.search_entry.get_text().strip().lower()
        selected_nb = self.active_notebook_filter

        fb_child = self.library_flowbox.get_first_child()
        while fb_child is not None:
            card = fb_child.get_child()
            if card is not None:
                nb_key = getattr(card, "notebook_key", "")
                file_name = getattr(card, "file_name", "").lower()
                nb_title = getattr(card, "notebook_title", "").lower()

                nb_matches = (selected_nb == "ALL" or selected_nb == nb_key)
                query_matches = (not query) or (query in file_name) or (query in nb_title)
                fb_child.set_visible(nb_matches and query_matches)
            fb_child = fb_child.get_next_sibling()

        l_row = self.library_listbox.get_first_child()
        while l_row is not None:
            nb_key = getattr(l_row, "notebook_key", "")
            file_name = getattr(l_row, "file_name", "").lower()
            nb_title = getattr(l_row, "notebook_title", "").lower()

            nb_matches = (selected_nb == "ALL" or selected_nb == nb_key)
            query_matches = (not query) or (query in file_name) or (query in nb_title)
            l_row.set_visible(nb_matches and query_matches)
            l_row = l_row.get_next_sibling()

    def load_folder(self, path):
        try:
            self._folder_load_id += 1
            current_load_id = self._folder_load_id

            self.current_root_folder = path
            self.save_config(path=path)
            self.current_folder_name = os.path.basename(os.path.normpath(path))
            self.collection_title.set_title("Library")

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
                if not valid_files:
                    continue
                nb_key = "."
                nb_title = root_name
            else:
                nb_key = rel_dir
                nb_title = rel_dir.replace(os.sep, " / ")

            notebooks[nb_key] = {
                "key": nb_key,
                "title": nb_title,
                "folder_path": cur_root,
                "files": [os.path.join(cur_root, f) for f in valid_files]
            }

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

        row.set_child(hbox)
        return row

    def _build_notebooks_skeleton(self, notebooks, load_id):
        if self._folder_load_id != load_id:
            return False

        self.notebooks_data = notebooks
        self.active_notebook_filter = "ALL"

        all_row = self._create_sidebar_row(
            "All Notes",
            "ALL",
            icon_name="library-symbolic",
            section_label="Library"
        )
        self.notebook_listbox.append(all_row)
        self.notebook_listbox.select_row(all_row)

        first_cat = True
        for nb_key, nb_info in notebooks.items():
            row = self._create_sidebar_row(
                nb_info["title"],
                nb_key,
                icon_name="feather-tag-symbolic",
                section_label="Categories" if first_cat else None
            )
            first_cat = False
            self.notebook_listbox.append(row)

        new_cat_row = self._create_sidebar_row(
            "New",
            "__NEW_CATEGORY__",
            icon_name=self._resolve_icon_name("list-add-symbolic", "list-add-symbolic"),
            section_label="Categories" if first_cat else None,
            is_new_category_action=True
        )
        self.notebook_listbox.append(new_cat_row)

        self.content_stack.set_visible_child_name(self.library_view_mode)
        if self.root_stack.get_visible_child_name() != "viewer":
            self.root_stack.set_visible_child_name("collection")
        return False

    def _render_thumbnail_worker(self, nb_key, nb_title, path, load_id):
        if self._folder_load_id != load_id:
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

    def _append_thumbnail_widget(self, nb_key, nb_title, path, surface, thumb_w, thumb_h, load_id):
        if self._folder_load_id != load_id:
            return False

        try:
            mtime = os.path.getmtime(path)
        except OSError:
            mtime = 0.0
        ext = os.path.splitext(path)[1].lower()
        name_without_ext = os.path.splitext(os.path.basename(path))[0]

        # 1. Build Grid View Card
        drawing_area = Gtk.DrawingArea()
        drawing_area.set_size_request(thumb_w, thumb_h)
        drawing_area.set_halign(Gtk.Align.START)

        def draw_thumb(da, cr, width, height, surf=surface):
            cr.set_source_surface(surf, 0, 0)
            cr.paint()

        drawing_area.set_draw_func(draw_thumb)

        preview_btn = Gtk.Button()
        preview_btn.set_child(drawing_area)
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

        mini_h = 56
        mini_w = max(38, int(round(thumb_w * (mini_h / max(1.0, float(thumb_h))))))
        mini_da = Gtk.DrawingArea()
        mini_da.set_size_request(mini_w, mini_h)
        mini_da.set_valign(Gtk.Align.CENTER)

        def draw_mini_thumb(da, cr, w, h, surf=surface, sw=thumb_w, sh=thumb_h):
            cr.save()
            cr.scale(w / max(1.0, float(sw)), h / max(1.0, float(sh)))
            cr.set_source_surface(surf, 0, 0)
            cr.paint()
            cr.restore()

        mini_da.set_draw_func(draw_mini_thumb)
        # row_hbox.append(mini_da)

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

        self._apply_collection_filters()
        self.library_flowbox.invalidate_sort()
        self.library_listbox.invalidate_sort()
        return False

    def _build_card_popover(self, path):
        popover = Gtk.Popover()
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

        default_btn = Gtk.Button(label="Open in External Editor")
        default_btn.add_css_class("flat")
        default_btn.connect("clicked", lambda *_: (popover.popdown(), self.open_with_default_app(path)))
        vbox.append(default_btn)

        choose_btn = Gtk.Button(label="Open With...")
        choose_btn.add_css_class("flat")
        choose_btn.connect("clicked", lambda *_: (popover.popdown(), self.choose_app_for_file(path)))
        vbox.append(choose_btn)

        vbox.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        folder_btn = Gtk.Button(label="Show in Folder")
        folder_btn.add_css_class("flat")
        folder_btn.connect("clicked", lambda *_: (popover.popdown(), self.open_containing_folder(path)))
        vbox.append(folder_btn)

        popover.set_child(vbox)
        return popover
