import copy
import json
import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Gdk', '4.0')
gi.require_version('Pango', '1.0')
from gi.repository import Gtk, Gdk, GLib, Pango, Gio

# Shared cross-document clipboard for Notebook
_APP_PAGE_CLIPBOARD = None
_APP_ITEMS_CLIPBOARD = None


def adopt_recording_ids(page, doc):
    """Ensure recording IDs in pasted items/page do not collide with target document."""
    existing_rids = set()
    for p in doc.get("pages", []):
        for it in p.get("items", []):
            if it.get("kind") == "audio" and "rid" in it:
                existing_rids.add(it["rid"])
            if it.get("rec") is not None:
                existing_rids.add(it["rec"])

    fresh_rid = (max(existing_rids) + 1) if existing_rids else 1
    remap = {}
    for it in page.get("items", []):
        if it.get("kind") == "audio" and "rid" in it:
            old_rid = it["rid"]
            if old_rid in existing_rids:
                it["rid"] = fresh_rid
                remap[old_rid] = fresh_rid
                fresh_rid += 1
            existing_rids.add(it["rid"])

    if remap:
        for it in page.get("items", []):
            old_rec = it.get("rec")
            if old_rec in remap:
                it["rec"] = remap[old_rec]


class PageSidebarMixin:
    """Mixin class for XNotesViewerWindow managing the page thumbnails sidebar listbox,
    drag-and-drop reordering, insertion, deletion, and cross-note copy/cut/paste."""

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

            # --- Drag and Drop Reordering ---
            drag_source = Gtk.DragSource.new()
            drag_source.set_actions(Gdk.DragAction.MOVE)

            def _on_drag_prepare(src, x, y, p_i=idx):
                return Gdk.ContentProvider.new_for_value(f"page:{p_i}")

            drag_source.connect("prepare", _on_drag_prepare)
            row.add_controller(drag_source)

            drop_target = Gtk.DropTarget.new(str, Gdk.DragAction.MOVE)

            def _on_drop(target, value, x, y, target_i=idx):
                if isinstance(value, str) and value.startswith("page:"):
                    try:
                        src_i = int(value.split(":", 1)[1])
                        if src_i != target_i:
                            self.reorder_page(src_i, target_i)
                            return True
                    except Exception:
                        pass
                return False

            drop_target.connect("drop", _on_drop)
            row.add_controller(drop_target)

            # Secondary click context menu
            right_click = Gtk.GestureClick.new()
            right_click.set_button(3)

            def _on_row_right_click(gesture, n_press, x, y, p_i=idx):
                self._show_page_context_menu(row, p_i, x, y)

            right_click.connect("pressed", _on_row_right_click)
            row.add_controller(right_click)

            vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, halign=Gtk.Align.CENTER)

            # Thumbnail dimensions preserving exact page aspect ratio without cropping
            pw, ph = self.canvas.get_page_size(idx)
            thumb_h = 144
            aspect = pw / max(1.0, ph)
            thumb_w = max(70, min(168, int(round(thumb_h * aspect))))

            da = Gtk.DrawingArea()
            da.set_size_request(thumb_w, thumb_h)
            da.set_halign(Gtk.Align.CENTER)
            da.set_valign(Gtk.Align.CENTER)

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

            # Copy Button
            copy_b = Gtk.Button(icon_name="edit-copy-symbolic", tooltip_text="Copy Page (Ctrl+C)")
            copy_b.add_css_class("flat")
            copy_b.add_css_class("page-mini-btn")
            copy_b.connect("clicked", lambda *_, i=idx: self.copy_page(i))
            meta_box.append(copy_b)

            # Cut Button
            cut_b = Gtk.Button(icon_name="edit-cut-symbolic", tooltip_text="Cut Page (Ctrl+X)", sensitive=(total > 1))
            cut_b.add_css_class("flat")
            cut_b.add_css_class("page-mini-btn")
            cut_b.connect("clicked", lambda *_, i=idx: self.cut_page(i))
            meta_box.append(cut_b)

            # Move Up
            up_b = Gtk.Button(icon_name="go-up-symbolic", tooltip_text="Move Page Up", sensitive=(idx > 0))
            up_b.add_css_class("flat")
            up_b.add_css_class("page-mini-btn")
            up_b.connect("clicked", lambda *_, i=idx: self.move_page(i, -1))
            meta_box.append(up_b)

            # Move Down
            dn_b = Gtk.Button(icon_name="go-down-symbolic", tooltip_text="Move Page Down", sensitive=(idx < total - 1))
            dn_b.add_css_class("flat")
            dn_b.add_css_class("page-mini-btn")
            dn_b.connect("clicked", lambda *_, i=idx: self.move_page(i, 1))
            meta_box.append(dn_b)

            # Duplicate Page
            dup_b = Gtk.Button(icon_name="list-add-symbolic", tooltip_text="Duplicate Page")
            dup_b.add_css_class("flat")
            dup_b.add_css_class("page-mini-btn")
            dup_b.connect("clicked", lambda *_, i=idx: self.duplicate_page(i))
            meta_box.append(dup_b)

            # Delete Page
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
        if hasattr(self, "sb_paste_btn"):
            self.sb_paste_btn.set_sensitive(_APP_PAGE_CLIPBOARD is not None)

    def _queue_redraw_current_sidebar_thumb(self):
        cur = self.canvas.current_page
        if 0 <= cur < len(self._page_thumb_draw_areas):
            self._page_thumb_draw_areas[cur].queue_draw()

    def _on_page_sidebar_row_selected(self, listbox, row):
        if self._updating_page_sidebar or row is None or not self.canvas.document:
            return
        target_idx = getattr(row, "page_index", 0)
        is_double = (getattr(self.canvas, "viewing_mode", "single") == "double" and self.canvas.view_mode == "paged")
        if is_double:
            target_left = target_idx - (target_idx % 2)
            current_left = self.canvas.current_page - (self.canvas.current_page % 2)
            if target_left != current_left:
                old_p = self.canvas.current_page
                self.canvas.clear_selection()
                self.canvas.current_page = target_left
                self.update_page_View()
                self.canvas.start_page_slide_animation(old_p, target_left)
            else:
                self.canvas.current_page = target_left
        else:
            if target_idx != self.canvas.current_page:
                old_p = self.canvas.current_page
                self.canvas.clear_selection()
                self.canvas.current_page = target_idx
                self.update_page_View()
                if self.canvas.view_mode == "paged":
                    self.canvas.start_page_slide_animation(old_p, target_idx)
                if self.canvas.view_mode == "scrolled":
                    GLib.idle_add(self.scroll_to_page, target_idx)
        self._update_page_sidebar_footer_sensitivity()

    # =========================================================================
    # Drag and Drop Reordering & Movement
    # =========================================================================
    def reorder_page(self, src_idx, dst_idx):
        """Move page from src_idx to dst_idx via Drag and Drop."""
        if not self.canvas.document:
            return
        pages = self.canvas.document.get("pages", [])
        if not (0 <= src_idx < len(pages) and 0 <= dst_idx < len(pages)):
            return
        if src_idx == dst_idx:
            return

        self.canvas.clear_selection()
        page_obj = pages.pop(src_idx)
        pages.insert(dst_idx, page_obj)
        self.canvas.page_sizes = [self.canvas.compute_page_size(p) for p in pages]
        self.canvas.current_page = dst_idx
        self.canvas.invalidate_page(None)
        self._on_canvas_modified(True)
        self.update_page_View()
        self._rebuild_page_sidebar()
        if self.canvas.view_mode == "scrolled":
            GLib.idle_add(self.scroll_to_page, dst_idx)
        self.show_toast(f"Moved Page {src_idx + 1} to Page {dst_idx + 1}")

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

    # =========================================================================
    # Cross-Note Copy, Cut, and Paste
    # =========================================================================
    def copy_page(self, page_idx):
        """Copy page and its referenced binary assets to shared clipboard across notes."""
        global _APP_PAGE_CLIPBOARD
        if not self.canvas.document:
            return
        pages = self.canvas.document.get("pages", [])
        if not (0 <= page_idx < len(pages)):
            return

        page = pages[page_idx]
        cloned_page = copy.deepcopy(page)

        # Collect raw assets referenced by this page
        raw_assets = self.canvas.document.get("_raw_assets", {})
        assets_payload = {}
        for it in cloned_page.get("items", []):
            asset_ref = it.get("asset") or it.get("src")
            if asset_ref and asset_ref in raw_assets:
                assets_payload[asset_ref] = raw_assets[asset_ref]

        _APP_PAGE_CLIPBOARD = {
            "type": "xnote_page",
            "page": cloned_page,
            "assets": assets_payload
        }

        # Also place JSON string into system clipboard
        try:
            clip = Gdk.Display.get_default().get_clipboard()
            json_text = json.dumps({
                "notebook_clip_type": "page",
                "page": cloned_page
            })
            clip.set(json_text)
        except Exception:
            pass

        self._update_page_sidebar_footer_sensitivity()
        self.show_toast(f"Page {page_idx + 1} copied to clipboard")

    def cut_page(self, page_idx):
        """Cut page to clipboard and delete it from current note."""
        if not self.canvas.document:
            return
        pages = self.canvas.document.get("pages", [])
        if len(pages) <= 1:
            self.show_error("Cannot cut the only page in the notebook.")
            return

        self.copy_page(page_idx)
        self.delete_page(page_idx)
        self.show_toast(f"Page {page_idx + 1} cut to clipboard")

    def paste_page(self, target_idx=None):
        """Paste page from clipboard after target_idx, supporting pasting across notes."""
        global _APP_PAGE_CLIPBOARD
        if not self.canvas.document:
            return

        payload = _APP_PAGE_CLIPBOARD
        if not payload or payload.get("type") != "xnote_page":
            self.show_toast("No page copied on clipboard")
            return

        cloned_page = copy.deepcopy(payload["page"])
        assets_dict = payload.get("assets", {})

        # Merge binary assets into destination document
        doc_raw_assets = self.canvas.document.setdefault("_raw_assets", {})
        for k, v in assets_dict.items():
            doc_raw_assets[k] = v

        # Remap recording IDs to avoid conflicts with target note
        adopt_recording_ids(cloned_page, self.canvas.document)

        pages = self.canvas.document.setdefault("pages", [])
        insert_pos = (self.canvas.current_page + 1) if target_idx is None else (target_idx + 1)
        insert_pos = max(0, min(insert_pos, len(pages)))

        self.canvas.clear_selection()
        pages.insert(insert_pos, cloned_page)
        self.canvas.page_sizes = [self.canvas.compute_page_size(p) for p in pages]
        self.canvas.current_page = insert_pos
        self.canvas.invalidate_page(None)
        self._on_canvas_modified(True)
        self.update_page_View()
        self._rebuild_page_sidebar()
        if self.canvas.view_mode == "scrolled":
            GLib.idle_add(self.scroll_to_page, insert_pos)
        self.show_toast(f"Pasted Page into position {insert_pos + 1}")

    def _show_page_context_menu(self, row, page_idx, x, y):
        """Show contextual right-click menu for a page."""
        pages = self.canvas.document.get("pages", []) if self.canvas.document else []
        total = len(pages)

        menu = Gio.Menu()
        menu.append("Copy Page", f"win.copy-page-{page_idx}")
        if total > 1:
            menu.append("Cut Page", f"win.cut-page-{page_idx}")
        menu.append("Paste Page After", f"win.paste-page-{page_idx}")
        menu.append("Duplicate Page", f"win.dup-page-{page_idx}")
        if total > 1:
            menu.append("Delete Page", f"win.del-page-{page_idx}")

        # Popover menu
        pop = Gtk.PopoverMenu.new_from_model(menu)
        pop.set_parent(row)
        pop.set_pointing_to(Gdk.Rectangle(x=int(x), y=int(y), width=1, height=1))
        pop.popup()

    def duplicate_page(self, page_idx):
        if not self.canvas.document:
            return
        pages = self.canvas.document.get("pages", [])
        if not (0 <= page_idx < len(pages)):
            return

        self.canvas.clear_selection()
        cloned_page = copy.deepcopy(pages[page_idx])
        adopt_recording_ids(cloned_page, self.canvas.document)
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
