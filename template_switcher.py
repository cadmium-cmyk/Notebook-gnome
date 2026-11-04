import os
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
        popover.set_autohide(True)
        popover.connect("show", self._on_template_popover_shown)

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        vbox.add_css_class("template-popover-box")
        vbox.set_size_request(290, -1)

        hdr = Gtk.Label(label="Page Template", xalign=0.0)
        hdr.add_css_class("heading")
        vbox.append(hdr)

        self.template_dropdown = Gtk.DropDown()
        self.template_dropdown.set_hexpand(True)
        self.template_dropdown.connect("notify::selected", self._on_template_dropdown_changed)
        vbox.append(self.template_dropdown)

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
        self.spacing_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.spacing_lbl = Gtk.Label(label="Line Spacing (mm):", xalign=0.0, hexpand=True)
        self.spacing_row.append(self.spacing_lbl)

        self.template_spacing_spin = Gtk.SpinButton.new_with_range(1.0, 50.0, 0.5)
        self.template_spacing_spin.set_value(8.0)
        self.template_spacing_spin.connect("value-changed", self._on_template_spacing_changed)
        self.spacing_row.append(self.template_spacing_spin)
        vbox.append(self.spacing_row)

        rule_color_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        rule_lbl = Gtk.Label(label="Rule Line Colour:", xalign=0.0, hexpand=True)
        rule_color_row.append(rule_lbl)

        self.pattern_color_btn = Gtk.ColorButton(tooltip_text="Rule / Pattern Line Colour", use_alpha=True)
        self.pattern_color_btn.connect("color-set", self._on_pattern_color_changed)
        rule_color_row.append(self.pattern_color_btn)
        vbox.append(rule_color_row)

        actions_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

        self.apply_page_btn = Gtk.Button(
            icon_name="document-page-setup-symbolic",
            label="Apply to Page",
            tooltip_text="Apply template and colour to the current page only",
            hexpand=True
        )
        self.apply_page_btn.connect("clicked", self._on_apply_template_current_page)
        actions_box.append(self.apply_page_btn)

        self.apply_all_btn = Gtk.Button(
            icon_name="edit-copy-symbolic",
            label="Apply to All",
            tooltip_text="Set template and page colour as default for all body pages",
            hexpand=True
        )
        self.apply_all_btn.connect("clicked", self._on_apply_template_all_pages)
        actions_box.append(self.apply_all_btn)

        vbox.append(actions_box)

        popover.set_child(vbox)
        return popover

    def _sync_toolbar_page_color_button(self):
        if not self.canvas.document:
            return
        if self.canvas.is_infinite_canvas():
            bg = self.canvas.document.get("background", {}) if isinstance(self.canvas.document.get("background"), dict) else {}
            bg_col = bg.get("paper_color", [255, 255, 255, 255])
        else:
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
        if hasattr(self, "toolbar_page_color_btn") and self.toolbar_page_color_btn:
            self.toolbar_page_color_btn.set_rgba(gdk_bg)
        self.paper_color_btn.set_rgba(gdk_bg)
        self._updating_template_ui = False

    def _apply_page_background_color(self, color_arr):
        if not self.canvas.document:
            return
        if self.canvas.is_infinite_canvas():
            bg = self.canvas.document.setdefault("background", {})
            bg["paper_color"] = list(color_arr)
            self._sync_toolbar_page_color_button()
            self._on_canvas_modified(True)
            self.canvas.queue_draw()
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

    def _load_system_templates(self):
        XNotePackageParser.load_system_templates(self.canvas.templates, self.canvas.assets)

    def _get_template_spacing_param(self, tid):
        t_info = XNotePackageParser.resolve_template(tid, self.canvas.templates)
        if not t_info or not isinstance(t_info.get("meta"), dict):
            return None, None
        params = t_info["meta"].get("params", {})
        if not isinstance(params, dict):
            return None, None
        for pk, pv in params.items():
            if isinstance(pv, dict) and pv.get("role") == "spacing":
                return pk, pv
        if "spacing" in params and isinstance(params["spacing"], dict):
            return "spacing", params["spacing"]
        return None, None

    @staticmethod
    def _parse_length_to_mm(val, default_val=8.0):
        if val is None:
            return default_val
        if isinstance(val, (int, float)):
            return float(val)
        if isinstance(val, str):
            s = val.strip().lower()
            if s.endswith("mm"):
                try: return float(s[:-2].strip())
                except ValueError: pass
            elif s.endswith("cm"):
                try: return float(s[:-2].strip()) * 10.0
                except ValueError: pass
            elif s.endswith("in"):
                try: return float(s[:-2].strip()) * 25.4
                except ValueError: pass
            elif s.endswith("pt"):
                try: return float(s[:-2].strip()) * (25.4 / 72.0)
                except ValueError: pass
            else:
                try: return float(s)
                except ValueError: pass
        return default_val

    def _update_spacing_ui_for_template(self, tid, page_style=None):
        if page_style is None:
            page_style = {}
        sp_key, sp_def = self._get_template_spacing_param(tid)
        if not sp_def:
            if hasattr(self, "spacing_row") and self.spacing_row:
                self.spacing_row.set_visible(False)
            return

        if hasattr(self, "spacing_row") and self.spacing_row:
            self.spacing_row.set_visible(True)
        lbl_text = sp_def.get("label") or "Line Spacing"
        if hasattr(self, "spacing_lbl") and self.spacing_lbl:
            self.spacing_lbl.set_text(f"{lbl_text} (mm):")

        min_mm = self._parse_length_to_mm(sp_def.get("min"), 1.0)
        max_mm = self._parse_length_to_mm(sp_def.get("max"), 50.0)
        step_mm = self._parse_length_to_mm(sp_def.get("step"), 0.5 if min_mm < 3.0 else 1.0)
        default_mm = self._parse_length_to_mm(sp_def.get("default"), 8.0)

        cur_val = None
        if sp_key in page_style:
            cur_val = self._parse_length_to_mm(page_style[sp_key])
        elif "spacing" in page_style:
            cur_val = self._parse_length_to_mm(page_style["spacing"])
        else:
            doc_style = self.canvas.document.get("style", {}) if self.canvas.document else {}
            if doc_style.get("template") == tid:
                if sp_key in doc_style:
                    cur_val = self._parse_length_to_mm(doc_style[sp_key])
                elif "spacing" in doc_style:
                    cur_val = self._parse_length_to_mm(doc_style["spacing"])

        if cur_val is None:
            val_to_set = default_mm
        else:
            val_to_set = max(min_mm, min(max_mm, cur_val))

        digits = 1 if (step_mm < 1.0 or min_mm != int(min_mm) or default_mm != int(default_mm) or val_to_set != int(val_to_set)) else 0
        if hasattr(self, "template_spacing_spin") and self.template_spacing_spin:
            self.template_spacing_spin.set_digits(digits)
            self.template_spacing_spin.set_range(min_mm, max_mm)
            self.template_spacing_spin.set_increments(step_mm, step_mm * 2)
            self.template_spacing_spin.set_value(round(val_to_set, 2))

    def _on_template_popover_shown(self, popover):
        if not self.canvas.document:
            return
        self._updating_template_ui = True

        self._load_system_templates()

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

        builtins = [
            ("none", "Blank Paper", False),
            ("lines", "Simple Horizontal Lines", False),
            ("dots", "Dot Grid", False),
            ("grid", "Square Graph Grid", False),
            ("isometric", "Isometric Grid", False),
            ("isometric-dots", "Isometric Dots", False),
        ]

        seen_ids = set()
        options = []
        for b_id, b_name, b_cov in builtins:
            seen_ids.add(b_id)
            options.append((b_id, b_name, b_cov))

        system_tpls = []
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
            system_tpls.append((tid, display_name, is_cover))

        system_tpls.sort(key=lambda t: t[1].lower())
        options.extend(system_tpls)
        self._template_dropdown_options = options

        str_list = Gtk.StringList.new([opt[1] for opt in options])
        self.template_dropdown.set_model(str_list)

        active_tid = self._get_effective_page_template_id(self.canvas.current_page)
        matching_idx = 0
        for idx, (tid, name, cov) in enumerate(options):
            if tid == active_tid or XNotePackageParser.extract_template_id(active_tid) == tid or active_tid.endswith(f"/{tid}"):
                matching_idx = idx
                break
        self.template_dropdown.set_selected(matching_idx)

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
            if hasattr(self, "toolbar_page_color_btn") and self.toolbar_page_color_btn:
                self.toolbar_page_color_btn.set_rgba(gdk_bg)

            self._update_spacing_ui_for_template(active_tid, style)

        self._updating_template_ui = False

    def _on_template_dropdown_changed(self, dropdown, pspec):
        if self._updating_template_ui or not self.canvas.document:
            return
        idx = dropdown.get_selected()
        if idx == Gtk.INVALID_LIST_POSITION or not hasattr(self, "_template_dropdown_options"):
            return
        if not (0 <= idx < len(self._template_dropdown_options)):
            return
        tid, name, is_cover = self._template_dropdown_options[idx]
        pages = self.canvas.document.get("pages", [])
        if not (0 <= self.canvas.current_page < len(pages)):
            return

        if self.canvas.is_infinite_canvas():
            bg = self.canvas.document.setdefault("background", {})
            bg["pattern"] = tid if tid in ("grid", "lines", "dots", "isometric", "isometric-dots", "isometric_grid", "isometric_dots") else "none"
            self._on_canvas_modified(True)
            self.canvas.queue_draw()
            return

        page = pages[self.canvas.current_page]
        style = page.setdefault("style", {})
        style["template"] = tid
        if "pattern" in style:
            del style["pattern"]
        page["cover"] = bool(is_cover)

        style.pop("spacing", None)
        for k in list(style.keys()):
            if k not in ("template", "pattern_color", "page_color", "accent_color"):
                style.pop(k, None)

        self._update_spacing_ui_for_template(tid, style)
        self.canvas.invalidate_page(self.canvas.current_page)
        self._on_canvas_modified(True)
        self._rebuild_page_sidebar()

    def _on_template_row_activated(self, listbox, row):
        pass

    def _on_template_spacing_changed(self, spin):
        if self._updating_template_ui or not self.canvas.document:
            return
        mm_val = spin.get_value()
        pages = self.canvas.document.get("pages", [])
        if not (0 <= self.canvas.current_page < len(pages)):
            return

        page = pages[self.canvas.current_page]
        style = page.setdefault("style", {})
        tid = self._get_effective_page_template_id(self.canvas.current_page)
        sp_key, _ = self._get_template_spacing_param(tid)
        if sp_key:
            style[sp_key] = f"{mm_val:.2f}mm"
        style["spacing"] = f"{mm_val:.2f}mm"

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
        if self.canvas.is_infinite_canvas():
            bg = self.canvas.document.setdefault("background", {})
            bg["pattern_color"] = color_arr
            self._on_canvas_modified(True)
            self.canvas.queue_draw()
            return

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

    def _on_apply_template_current_page(self, *_):
        if not self.canvas.document:
            return

        if self.canvas.is_infinite_canvas():
            self.template_popover.popdown()
            self.show_toast("Applied template and colour to canvas")
            return

        pages = self.canvas.document.get("pages", [])
        if not (0 <= self.canvas.current_page < len(pages)):
            return

        idx = self.template_dropdown.get_selected()
        if (
            idx != Gtk.INVALID_LIST_POSITION
            and hasattr(self, "_template_dropdown_options")
            and (0 <= idx < len(self._template_dropdown_options))
        ):
            tid, name, is_cover = self._template_dropdown_options[idx]
            page = pages[self.canvas.current_page]
            style = page.setdefault("style", {})
            style["template"] = tid
            if "pattern" in style:
                del style["pattern"]
            page["cover"] = bool(is_cover)
            self._update_spacing_ui_for_template(tid, style)

        self.canvas.invalidate_page(self.canvas.current_page)
        self._on_canvas_modified(True)
        self._rebuild_page_sidebar()
        self.template_popover.popdown()
        self.show_toast(f"Applied template and colour to page {self.canvas.current_page + 1}")

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
        self.show_toast("Applied template and colour to all body pages")
