import json
import math
import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Pango', '1.0')
from gi.repository import Gtk, Gdk, Gio, GLib, Pango

from xnote_io import (
    XNotePackageParser,
    DEFAULT_RULED_TEMPLATE_ID,
    DEFAULT_RULED_TEMPLATE,
)

PAPER_COLOR_PRESETS = [
    ("Pure White", [255, 255, 255, 255]),
    ("Warm Cream", [253, 249, 238, 255]),
    ("Soft Sepia", [244, 236, 216, 255]),
    ("Sage Green", [235, 243, 237, 255]),
    ("Pastel Blue", [235, 243, 250, 255]),
    ("Dark Slate", [34, 37, 42, 255]),
]


class TemplateSwitcherMixin:
    """Mixin class for XNotesViewerWindow handling template popover creation, paper swatches, rule spacing, and pattern color settings."""

    def _build_template_switcher_popover(self):
        popover = Gtk.Popover()
        popover.connect("show", self._on_template_popover_shown)

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        vbox.add_css_class("template-popover-box")
        vbox.set_size_request(290, -1)

        hdr = Gtk.Label(label="Page Template", xalign=0.0)
        hdr.add_css_class("heading")
        vbox.append(hdr)

        self.template_listbox = Gtk.ListBox()
        self.template_listbox.add_css_class("boxed-list")
        self.template_listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.template_listbox.connect("row-activated", self._on_template_row_activated)
        vbox.append(self.template_listbox)

        import_tpl_btn = Gtk.Button(
            icon_name="document-open-symbolic",
            label="Import .xtemplate or Cover Image…",
            tooltip_text="Load an external .xtemplate or PNG/JPEG cover image into this notebook"
        )
        import_tpl_btn.connect("clicked", self._on_import_template_or_cover_clicked)
        vbox.append(import_tpl_btn)

        vbox.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        # Quick Paper Colour Presets + Custom Colour Picker
        paper_hdr = Gtk.Label(label="Page Background Colour", xalign=0.0)
        paper_hdr.add_css_class("heading")
        vbox.append(paper_hdr)

        swatches_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6, halign=Gtk.Align.FILL)
        for preset_name, rgba_arr in PAPER_COLOR_PRESETS:
            swatch_btn = Gtk.Button(tooltip_text=preset_name)
            swatch_btn.add_css_class("flat")
            swatch_btn.add_css_class("paper-swatch-btn")

            swatch_da = Gtk.DrawingArea()
            swatch_da.set_size_request(22, 22)

            def draw_swatch(da, cr, w, h, col=rgba_arr):
                cr.arc(w / 2.0, h / 2.0, min(w, h) / 2.0 - 1.5, 0, 2 * math.pi)
                cr.set_source_rgb(col[0] / 255.0, col[1] / 255.0, col[2] / 255.0)
                cr.fill_preserve()
                cr.set_source_rgba(0, 0, 0, 0.25)
                cr.set_line_width(1.0)
                cr.stroke()

            swatch_da.set_draw_func(draw_swatch)
            swatch_btn.set_child(swatch_da)
            swatch_btn.connect("clicked", lambda *_, c=rgba_arr: self._apply_page_background_color(c))
            swatches_row.append(swatch_btn)

        swatches_row.append(Gtk.Box(hexpand=True))

        self.paper_color_btn = Gtk.ColorButton(tooltip_text="Custom Page Background Colour")
        self.paper_color_btn.connect("color-set", self._on_paper_color_changed)
        swatches_row.append(self.paper_color_btn)
        vbox.append(swatches_row)

        vbox.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        # Rule Spacing & Rule Line Colour
        spacing_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        spacing_lbl = Gtk.Label(label="Rule Spacing (mm):", xalign=0.0, hexpand=True)
        spacing_row.append(spacing_lbl)

        self.template_spacing_spin = Gtk.SpinButton.new_with_range(4.0, 25.0, 1.0)
        self.template_spacing_spin.set_value(8.0)
        self.template_spacing_spin.connect("value-changed", self._on_template_spacing_changed)
        spacing_row.append(self.template_spacing_spin)
        vbox.append(spacing_row)

        rule_color_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        rule_lbl = Gtk.Label(label="Rule Line Colour:", xalign=0.0, hexpand=True)
        rule_color_row.append(rule_lbl)

        self.pattern_color_btn = Gtk.ColorButton(tooltip_text="Rule / Pattern Line Colour", use_alpha=True)
        self.pattern_color_btn.connect("color-set", self._on_pattern_color_changed)
        rule_color_row.append(self.pattern_color_btn)
        vbox.append(rule_color_row)

        apply_all_btn = Gtk.Button(
            icon_name="edit-copy-symbolic",
            label="Apply to All Body Pages",
            tooltip_text="Set Template & Page Colour as Default for All Non-Cover Pages"
        )
        apply_all_btn.connect("clicked", self._on_apply_template_all_pages)
        vbox.append(apply_all_btn)

        popover.set_child(vbox)
        return popover

    def _sync_toolbar_page_color_button(self):
        if not self.canvas.document:
            return
        pages = self.canvas.document.get("pages", [])
        if not (0 <= self.canvas.current_page < len(pages)):
            return
        page = pages[self.canvas.current_page]
        style = page.get("style", {}) if isinstance(page.get("style"), dict) else {}
        doc_style = self.canvas.document.get("style", {}) if isinstance(self.canvas.document.get("style"), dict) else {}
        bg_col = style.get("page_color", doc_style.get("page_color", [255, 255, 255, 255]))

        gdk_bg = Gdk.RGBA()
        gdk_bg.red = bg_col[0] / 255.0
        gdk_bg.green = bg_col[1] / 255.0
        gdk_bg.blue = bg_col[2] / 255.0
        gdk_bg.alpha = 1.0

        self._updating_template_ui = True
        self.toolbar_page_color_btn.set_rgba(gdk_bg)
        self.paper_color_btn.set_rgba(gdk_bg)
        self._updating_template_ui = False

    def _apply_page_background_color(self, color_arr):
        if not self.canvas.document:
            return
        pages = self.canvas.document.get("pages", [])
        if 0 <= self.canvas.current_page < len(pages):
            style = pages[self.canvas.current_page].setdefault("style", {})
            style["page_color"] = list(color_arr)
            self._sync_toolbar_page_color_button()
            self.canvas.invalidate_page(self.canvas.current_page)
            self._on_canvas_modified(True)
            self._queue_redraw_current_sidebar_thumb()

    def _on_import_template_or_cover_clicked(self, *_):
        self.template_popover.popdown()
        if not self.canvas.document:
            return
        if hasattr(Gtk, "FileDialog"):
            dialog = Gtk.FileDialog(title="Import .xtemplate or Cover Image")
            filters = Gio.ListStore.new(Gtk.FileFilter)
            tpl_filter = Gtk.FileFilter()
            tpl_filter.set_name("Templates & Cover Images (*.xtemplate, *.png, *.jpg, *.webp)")
            for pat in ("*.xtemplate", "*.png", "*.jpg", "*.jpeg", "*.webp"):
                tpl_filter.add_pattern(pat)
            filters.append(tpl_filter)
            dialog.set_filters(filters)
            dialog.open(self, None, self._on_template_or_cover_file_selected)

    def _on_template_or_cover_file_selected(self, dialog, result):
        try:
            gfile = dialog.open_finish(result)
            if gfile and gfile.get_path():
                path = gfile.get_path()
                t_entry = XNotePackageParser.import_template_or_cover_file(
                    path, self.canvas.document, self.canvas.assets, self.canvas.templates
                )
                pages = self.canvas.document.get("pages", [])
                if 0 <= self.canvas.current_page < len(pages):
                    page = pages[self.canvas.current_page]
                    style = page.setdefault("style", {})
                    style["template"] = t_entry["id"]
                    page["cover"] = bool(t_entry.get("is_cover", False))
                    self.canvas.invalidate_page(self.canvas.current_page)
                    self._on_canvas_modified(True)
                    self._rebuild_page_sidebar()
                    self.show_toast(f"Applied imported template: {t_entry.get('meta', {}).get('name', t_entry['id'])}")
        except GLib.Error:
            pass
        except Exception as e:
            self.show_error(f"Failed to import template/cover: {e}")

    def _get_effective_page_template_id(self, page_idx):
        if not self.canvas.document:
            return "none"
        pages = self.canvas.document.get("pages", [])
        if not (0 <= page_idx < len(pages)):
            return "none"
        page = pages[page_idx]
        style = page.get("style", {}) if isinstance(page.get("style"), dict) else {}
        doc_style = self.canvas.document.get("style", {}) if isinstance(self.canvas.document.get("style"), dict) else {}

        t_ref = style.get("template") or style.get("pattern") or page.get("template")
        if not t_ref and not page.get("cover", False) and page.get("pdf_page") is None:
            t_ref = doc_style.get("template") or doc_style.get("pattern")
        return t_ref or "none"

    def _on_template_popover_shown(self, popover):
        if not self.canvas.document:
            return
        self._updating_template_ui = True

        child = self.template_listbox.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.template_listbox.remove(child)
            child = nxt

        if DEFAULT_RULED_TEMPLATE_ID not in self.canvas.templates:
            t_bytes = json.dumps(DEFAULT_RULED_TEMPLATE, indent=2).encode("utf-8")
            self.canvas.templates[DEFAULT_RULED_TEMPLATE_ID] = {
                "id": DEFAULT_RULED_TEMPLATE_ID,
                "path": f"templates/{DEFAULT_RULED_TEMPLATE_ID}.xtemplate",
                "is_cover": False,
                "meta": DEFAULT_RULED_TEMPLATE,
                "surface": None,
                "images": {},
                "raw_bytes": t_bytes,
            }

        options = []
        seen_ids = set()

        for t_info in self.canvas.templates.values():
            if not isinstance(t_info, dict):
                continue
            tid = t_info.get("id")
            if not tid or tid in seen_ids:
                continue
            seen_ids.add(tid)
            meta = t_info.get("meta", {}) if isinstance(t_info.get("meta"), dict) else {}
            display_name = meta.get("name") or tid
            is_cover = t_info.get("is_cover", False)
            badge = "Cover (.xtemplate)" if is_cover else "Template (.xtemplate)"
            icon = "image-x-generic-symbolic" if is_cover else "view-continuous-symbolic"
            options.append((tid, display_name, badge, icon, is_cover))

        builtins = [
            ("none", "Blank Paper", "Built-in", "document-new-symbolic", False),
            ("lines", "Simple Horizontal Lines", "Built-in", "view-list-symbolic", False),
            ("dots", "Dot Grid", "Built-in", "view-app-grid-symbolic", False),
            ("grid", "Square Graph Grid", "Built-in", "view-grid-symbolic", False),
        ]
        for b_id, b_name, b_sub, b_icon, b_cov in builtins:
            if b_id not in seen_ids:
                options.append((b_id, b_name, b_sub, b_icon, b_cov))

        active_tid = self._get_effective_page_template_id(self.canvas.current_page)

        for tid, title, subtitle, icon_name, is_cover in options:
            row = Gtk.ListBoxRow()
            row.template_id = tid
            row.is_cover_template = is_cover

            hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10, margin_start=8, margin_end=8, margin_top=6, margin_bottom=6)
            hbox.append(Gtk.Image.new_from_icon_name(icon_name))

            tbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True)
            tlbl = Gtk.Label(label=title, xalign=0.0, ellipsize=Pango.EllipsizeMode.END, max_width_chars=24)
            tlbl.add_css_class("card-title")
            slbl = Gtk.Label(label=subtitle, xalign=0.0)
            slbl.add_css_class("card-subtitle")
            tbox.append(tlbl)
            tbox.append(slbl)
            hbox.append(tbox)

            if tid == active_tid or XNotePackageParser.extract_template_id(active_tid) == tid:
                check = Gtk.Image.new_from_icon_name("object-select-symbolic")
                hbox.append(check)

            row.set_child(hbox)
            self.template_listbox.append(row)
            if tid == active_tid:
                self.template_listbox.select_row(row)

        pages = self.canvas.document.get("pages", [])
        if 0 <= self.canvas.current_page < len(pages):
            page = pages[self.canvas.current_page]
            style = page.get("style", {}) if isinstance(page.get("style"), dict) else {}
            doc_style = self.canvas.document.get("style", {}) if isinstance(self.canvas.document.get("style"), dict) else {}

            p_col = style.get("pattern_color", doc_style.get("pattern_color", [0, 56, 183, 95]))
            gdk_p = Gdk.RGBA()
            gdk_p.red, gdk_p.green, gdk_p.blue, gdk_p.alpha = p_col[0] / 255.0, p_col[1] / 255.0, p_col[2] / 255.0, p_col[3] / 255.0
            self.pattern_color_btn.set_rgba(gdk_p)

            bg_col = style.get("page_color", doc_style.get("page_color", [255, 255, 255, 255]))
            gdk_bg = Gdk.RGBA()
            gdk_bg.red, gdk_bg.green, gdk_bg.blue, gdk_bg.alpha = bg_col[0] / 255.0, bg_col[1] / 255.0, bg_col[2] / 255.0, 1.0
            self.paper_color_btn.set_rgba(gdk_bg)
            self.toolbar_page_color_btn.set_rgba(gdk_bg)

            sp_val = style.get("spacing", doc_style.get("spacing", "8mm"))
            if isinstance(sp_val, str) and sp_val.endswith("mm"):
                try:
                    self.template_spacing_spin.set_value(float(sp_val[:-2]))
                except ValueError:
                    pass
            elif isinstance(sp_val, (int, float)):
                dpi = float(self.canvas.document.get("dpi", 150.0))
                self.template_spacing_spin.set_value(round(float(sp_val) / (dpi / 25.4), 1))

        self._updating_template_ui = False

    def _on_template_row_activated(self, listbox, row):
        if self._updating_template_ui or row is None or not self.canvas.document:
            return
        tid = getattr(row, "template_id", "none")
        is_cover = getattr(row, "is_cover_template", False)
        pages = self.canvas.document.get("pages", [])
        if not (0 <= self.canvas.current_page < len(pages)):
            return

        page = pages[self.canvas.current_page]
        style = page.setdefault("style", {})
        style["template"] = tid
        if "pattern" in style:
            del style["pattern"]
        page["cover"] = bool(is_cover)

        self.canvas.invalidate_page(self.canvas.current_page)
        self._on_canvas_modified(True)
        self._rebuild_page_sidebar()
        self.template_popover.popdown()

    def _on_template_spacing_changed(self, spin):
        if self._updating_template_ui or not self.canvas.document:
            return
        mm_val = spin.get_value()
        dpi = float(self.canvas.document.get("dpi", 150.0))
        pages = self.canvas.document.get("pages", [])
        if not (0 <= self.canvas.current_page < len(pages)):
            return

        page = pages[self.canvas.current_page]
        style = page.setdefault("style", {})
        tid = self._get_effective_page_template_id(self.canvas.current_page)
        if tid in ("lines", "dots", "grid"):
            style["spacing"] = round(mm_val * (dpi / 25.4), 2)
        else:
            style["spacing"] = f"{mm_val:.1f}mm"

        self.canvas.invalidate_page(self.canvas.current_page)
        self._on_canvas_modified(True)

    def _on_pattern_color_changed(self, btn):
        if self._updating_template_ui or not self.canvas.document:
            return
        rgba = btn.get_rgba()
        color_arr = [
            int(round(rgba.red * 255)),
            int(round(rgba.green * 255)),
            int(round(rgba.blue * 255)),
            max(20, int(round(rgba.alpha * 255)))
        ]
        pages = self.canvas.document.get("pages", [])
        if 0 <= self.canvas.current_page < len(pages):
            style = pages[self.canvas.current_page].setdefault("style", {})
            style["pattern_color"] = color_arr
            self.canvas.invalidate_page(self.canvas.current_page)
            self._on_canvas_modified(True)

    def _on_paper_color_changed(self, btn):
        if self._updating_template_ui or not self.canvas.document:
            return
        rgba = btn.get_rgba()
        color_arr = [
            int(round(rgba.red * 255)),
            int(round(rgba.green * 255)),
            int(round(rgba.blue * 255)),
            255
        ]
        self._apply_page_background_color(color_arr)

    def _on_apply_template_all_pages(self, *_):
        if not self.canvas.document:
            return
        pages = self.canvas.document.get("pages", [])
        if not (0 <= self.canvas.current_page < len(pages)):
            return

        cur_page = pages[self.canvas.current_page]
        cur_style = cur_page.get("style", {}) if isinstance(cur_page.get("style"), dict) else {}
        tid = self._get_effective_page_template_id(self.canvas.current_page)

        doc_style = self.canvas.document.setdefault("style", {})
        doc_style["template"] = tid
        if "pattern_color" in cur_style:
            doc_style["pattern_color"] = list(cur_style["pattern_color"])
        if "page_color" in cur_style:
            doc_style["page_color"] = list(cur_style["page_color"])
        if "spacing" in cur_style:
            doc_style["spacing"] = cur_style["spacing"]

        for idx, p in enumerate(pages):
            if p.get("cover", False) and idx == 0 and idx != self.canvas.current_page:
                continue
            p_style = p.setdefault("style", {})
            p_style["template"] = tid
            if "pattern_color" in cur_style:
                p_style["pattern_color"] = list(cur_style["pattern_color"])
            if "page_color" in cur_style:
                p_style["page_color"] = list(cur_style["page_color"])
            if "spacing" in cur_style:
                p_style["spacing"] = cur_style["spacing"]

        self.canvas.invalidate_page(None)
        self._on_canvas_modified(True)
        self._rebuild_page_sidebar()
        self.template_popover.popdown()
        self.show_toast("Applied template & colour to all body pages")
