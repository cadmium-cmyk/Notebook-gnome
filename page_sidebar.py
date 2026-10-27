import copy
import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Pango', '1.0')
from gi.repository import Gtk, GLib, Pango


class PageSidebarMixin:
    """Mixin class for XNotesViewerWindow managing the page thumbnails sidebar listbox and page reordering/insertion/deletion operations."""

    def _on_page_sidebar_toggled(self, btn):
        self.page_sidebar_revealer.set_reveal_child(btn.get_active())

    def _rebuild_page_sidebar(self):
        self._updating_page_sidebar = True
        self._page_thumb_draw_areas.clear()

        child = self.page_sidebar_listbox.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.page_sidebar_listbox.remove(child)
            child = nxt

        if not self.canvas.document:
            self._updating_page_sidebar = False
            return

        pages = self.canvas.document.get("pages", [])
        total = len(pages)

        for idx, page in enumerate(pages):
            row = Gtk.ListBoxRow()
            row.page_index = idx
            row.add_css_class("page-thumb-row")

            vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, halign=Gtk.Align.CENTER)

            pw, ph = self.canvas.get_page_size(idx)
            thumb_h = 148
            thumb_w = max(80, min(175, int(round(thumb_h * (pw / max(1.0, ph))))))

            da = Gtk.DrawingArea()
            da.set_size_request(thumb_w, thumb_h)
            da.set_halign(Gtk.Align.CENTER)

            def draw_sidebar_page(area, cr, w, h, p_idx=idx):
                if not self.canvas.document:
                    return
                doc_pages = self.canvas.document.get("pages", [])
                if not (0 <= p_idx < len(doc_pages)):
                    return
                p_obj = doc_pages[p_idx]
                page_w, page_h = self.canvas.get_page_size(p_idx)
                self.canvas.draw_page_thumb(
                    cr, w, h,
                    self.canvas.document, p_obj,
                    page_w, page_h,
                    self.canvas.assets, self.canvas.templates,
                    page_idx=p_idx
                )

            da.set_draw_func(draw_sidebar_page)
            self._page_thumb_draw_areas.append(da)
            vbox.append(da)

            meta_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2, hexpand=True)
            tag_suffix = ""
            if page.get("cover", False):
                tag_suffix = " • Cover"
            elif page.get("pdf_page") is not None:
                tag_suffix = f" • PDF {page['pdf_page'] + 1}"

            lbl = Gtk.Label(
                label=f"Page {idx + 1}{tag_suffix}",
                xalign=0.0,
                hexpand=True,
                ellipsize=Pango.EllipsizeMode.END
            )
            lbl.add_css_class("card-subtitle")
            meta_box.append(lbl)

            up_b = Gtk.Button(icon_name="go-up-symbolic", tooltip_text="Move Page Up", sensitive=(idx > 0))
            up_b.add_css_class("flat")
            up_b.add_css_class("page-mini-btn")
            up_b.connect("clicked", lambda *_, i=idx: self.move_page(i, -1))
            meta_box.append(up_b)

            dn_b = Gtk.Button(icon_name="go-down-symbolic", tooltip_text="Move Page Down", sensitive=(idx < total - 1))
            dn_b.add_css_class("flat")
            dn_b.add_css_class("page-mini-btn")
            dn_b.connect("clicked", lambda *_, i=idx: self.move_page(i, 1))
            meta_box.append(dn_b)

            dup_b = Gtk.Button(icon_name="edit-copy-symbolic", tooltip_text="Duplicate Page")
            dup_b.add_css_class("flat")
            dup_b.add_css_class("page-mini-btn")
            dup_b.connect("clicked", lambda *_, i=idx: self.duplicate_page(i))
            meta_box.append(dup_b)

            del_b = Gtk.Button(icon_name="user-trash-symbolic", tooltip_text="Delete Page", sensitive=(total > 1))
            del_b.add_css_class("flat")
            del_b.add_css_class("page-mini-btn")
            del_b.connect("clicked", lambda *_, i=idx: self.delete_page(i))
            meta_box.append(del_b)

            vbox.append(meta_box)
            row.set_child(vbox)
            self.page_sidebar_listbox.append(row)

            if idx == self.canvas.current_page:
                self.page_sidebar_listbox.select_row(row)

        self._update_page_sidebar_footer_sensitivity()
        self._updating_page_sidebar = False

    def _sync_page_sidebar_selection(self):
        if self._updating_page_sidebar:
            return
        self._updating_page_sidebar = True
        row = self.page_sidebar_listbox.get_row_at_index(self.canvas.current_page)
        if row is not None:
            self.page_sidebar_listbox.select_row(row)
        self._update_page_sidebar_footer_sensitivity()
        self._updating_page_sidebar = False

    def _update_page_sidebar_footer_sensitivity(self):
        if not self.canvas.document:
            return
        total = len(self.canvas.document.get("pages", []))
        cur = self.canvas.current_page
        self.sb_move_up_btn.set_sensitive(cur > 0)
        self.sb_move_down_btn.set_sensitive(cur < total - 1)
        self.sb_del_btn.set_sensitive(total > 1)

    def _queue_redraw_current_sidebar_thumb(self):
        cur = self.canvas.current_page
        if 0 <= cur < len(self._page_thumb_draw_areas):
            self._page_thumb_draw_areas[cur].queue_draw()

    def _on_page_sidebar_row_selected(self, listbox, row):
        if self._updating_page_sidebar or row is None or not self.canvas.document:
            return
        target_idx = getattr(row, "page_index", 0)
        if target_idx != self.canvas.current_page:
            self.canvas.clear_selection()
            self.canvas.current_page = target_idx
            self.update_page_View()
            if self.canvas.view_mode == "scrolled":
                GLib.idle_add(self.scroll_to_page, target_idx)
        self._update_page_sidebar_footer_sensitivity()

    def move_page(self, page_idx, direction):
        if not self.canvas.document:
            return
        pages = self.canvas.document.get("pages", [])
        target_idx = page_idx + direction
        if not (0 <= page_idx < len(pages) and 0 <= target_idx < len(pages)):
            return

        self.canvas.clear_selection()
        pages[page_idx], pages[target_idx] = pages[target_idx], pages[page_idx]
        self.canvas.page_sizes = [self.canvas.compute_page_size(p) for p in pages]
        self.canvas.current_page = target_idx
        self.canvas.invalidate_page(None)
        self._on_canvas_modified(True)
        self.update_page_View()
        self._rebuild_page_sidebar()
        if self.canvas.view_mode == "scrolled":
            GLib.idle_add(self.scroll_to_page, target_idx)

    def duplicate_page(self, page_idx):
        if not self.canvas.document:
            return
        pages = self.canvas.document.get("pages", [])
        if not (0 <= page_idx < len(pages)):
            return

        self.canvas.clear_selection()
        cloned_page = copy.deepcopy(pages[page_idx])
        pages.insert(page_idx + 1, cloned_page)
        self.canvas.page_sizes = [self.canvas.compute_page_size(p) for p in pages]
        self.canvas.current_page = page_idx + 1
        self.canvas.invalidate_page(None)
        self._on_canvas_modified(True)
        self.update_page_View()
        self._rebuild_page_sidebar()
        if self.canvas.view_mode == "scrolled":
            GLib.idle_add(self.scroll_to_page, self.canvas.current_page)
        self.show_toast(f"Duplicated Page {page_idx + 1}")

    def insert_page_after(self, page_idx):
        if not self.canvas.document:
            return
        pages = self.canvas.document.get("pages", [])
        pw, ph = self.canvas.get_page_size(page_idx) if pages else (1240.1574803149608, 1753.9370078740158)
        new_page = {
            "width": pw,
            "height": ph,
            "pdf_page": None,
            "items": []
        }
        insert_pos = min(len(pages), max(0, page_idx + 1))
        pages.insert(insert_pos, new_page)
        self.canvas.page_sizes = [self.canvas.compute_page_size(p) for p in pages]
        self.canvas.current_page = insert_pos
        self.canvas.invalidate_page(None)
        self._on_canvas_modified(True)
        self.update_page_View()
        self._rebuild_page_sidebar()
        if self.canvas.view_mode == "scrolled":
            GLib.idle_add(self.scroll_to_page, self.canvas.current_page)

    def delete_page(self, page_idx):
        if not self.canvas.document:
            return
        pages = self.canvas.document.get("pages", [])
        if len(pages) <= 1:
            self.show_error("Cannot delete the only page in the notebook.")
            return
        if not (0 <= page_idx < len(pages)):
            return

        self.canvas.clear_selection()
        pages.pop(page_idx)
        self.canvas.page_sizes = [self.canvas.compute_page_size(p) for p in pages]
        self.canvas.current_page = max(0, min(self.canvas.current_page, len(pages) - 1))
        self.canvas.invalidate_page(None)
        self._on_canvas_modified(True)
        self.update_page_View()
        self._rebuild_page_sidebar()
        self.show_toast(f"Deleted Page {page_idx + 1}")
