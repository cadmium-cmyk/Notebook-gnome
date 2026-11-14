import os
import datetime
import shutil
import cairo
import json
import uuid
import time
import math
import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Pango', '1.0')
from gi.repository import Gtk, Gdk, Adw, GLib, Pango

from xnote_io import XNotePackageParser, SUPPORTED_EXTENSIONS
from markdown_renderer import MarkdownRenderer

SIDECAR_DIR = ".xnote"
SIDECAR_FILE = "colors.json"
COLOR_NAMES_FILE = "color-names.json"

DEFAULT_SWATCHES = [
    ("#e06c75", "Red"),
    ("#e57042", "Coral"),
    ("#d19a66", "Orange"),
    ("#e5c07b", "Yellow"),
    ("#98c379", "Green"),
    ("#56b6c2", "Teal"),
    ("#4dd0e1", "Cyan"),
    ("#61afef", "Blue"),
    ("#7986cb", "Indigo"),
    ("#c678dd", "Purple"),
    ("#f06292", "Pink"),
    ("#8d6e63", "Brown"),
    ("#abb2bf", "Slate"),
    ("#374151", "Dark"),
]

COLOR_NAMES_MAP = {hex_code.lower(): name for hex_code, name in DEFAULT_SWATCHES}


def _format_file_size(size_bytes):
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.1f} GB"

class LibraryManagerMixin:
    """Mixin class for XNotesViewerWindow handling folder scanning, cached cover thumbnail rendering with Libadwaita spinners, flowbox/listbox card generation, and library sorting."""

    # --- Per-folder colour sidecar (.xnote/colors.json & .xnote/color-names.json) ---

    def _get_folder_sidecar_dir(self, folder_path, create=False):
        if not folder_path or not os.path.isdir(folder_path):
            return None
        sidecar_dir = os.path.join(folder_path, SIDECAR_DIR)
        if create and not os.path.exists(sidecar_dir):
            try:
                os.makedirs(sidecar_dir, exist_ok=True)
            except OSError:
                return None
        return sidecar_dir if os.path.isdir(sidecar_dir) else None

    def _load_folder_colors(self, folder_path):
        sidecar_dir = self._get_folder_sidecar_dir(folder_path, create=False)
        if not sidecar_dir:
            return {}
        colors_file = os.path.join(sidecar_dir, SIDECAR_FILE)
        if not os.path.exists(colors_file):
            return {}
        try:
            with open(colors_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("colors", {}) if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _save_folder_colors(self, folder_path, colors_dict):
        sidecar_dir = self._get_folder_sidecar_dir(folder_path, create=bool(colors_dict))
        if not sidecar_dir:
            return False
        colors_file = os.path.join(sidecar_dir, SIDECAR_FILE)
        if not colors_dict:
            if os.path.exists(colors_file):
                try:
                    os.remove(colors_file)
                except OSError:
                    pass
            return True
        try:
            data = {
                "version": 1,
                "colors": colors_dict
            }
            with open(colors_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            return True
        except Exception as e:
            print(f"Failed to write {SIDECAR_FILE} in {folder_path}: {e}")
            return False

    def _load_color_names(self):
        root = getattr(self, "current_root_folder", None)
        if not root:
            return {}
        sidecar_dir = self._get_folder_sidecar_dir(root, create=False)
        if not sidecar_dir:
            return {}
        names_file = os.path.join(sidecar_dir, COLOR_NAMES_FILE)
        if not os.path.exists(names_file):
            return {}
        try:
            with open(names_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("names", {}) if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _save_color_name(self, hex_code, name):
        root = getattr(self, "current_root_folder", None)
        if not root:
            return False
        hex_code = hex_code.strip().lower()
        names = self._load_color_names()
        if name and name.strip():
            names[hex_code] = name.strip()
        else:
            names.pop(hex_code, None)
        sidecar_dir = self._get_folder_sidecar_dir(root, create=bool(names))
        if not sidecar_dir:
            return False
        names_file = os.path.join(sidecar_dir, COLOR_NAMES_FILE)
        if not names:
            if os.path.exists(names_file):
                try:
                    os.remove(names_file)
                except OSError:
                    pass
            return True
        try:
            data = {
                "version": 1,
                "names": names
            }
            with open(names_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            return True
        except Exception as e:
            print(f"Failed to write {COLOR_NAMES_FILE}: {e}")
            return False

    def get_item_color(self, path):
        if not path:
            return None
        folder = os.path.dirname(os.path.abspath(path))
        filename = os.path.basename(path)
        colors = self._load_folder_colors(folder)
        return colors.get(filename)

    def set_item_color(self, path, hex_color):
        if not path:
            return False
        folder = os.path.dirname(os.path.abspath(path))
        filename = os.path.basename(path)
        colors = self._load_folder_colors(folder)
        if hex_color:
            clean = hex_color.strip().lower()
            if not clean.startswith("#"):
                clean = "#" + clean
            colors[filename] = clean
        else:
            colors.pop(filename, None)
        success = self._save_folder_colors(folder, colors)
        if success:
            self._update_card_color_ui(path)
        return success

    def _hex_to_rgba(self, hex_code):
        if not hex_code:
            return None
        s = hex_code.strip().lstrip("#")
        if len(s) == 3:
            s = "".join([c*2 for c in s])
        if len(s) == 6:
            try:
                r = int(s[0:2], 16) / 255.0
                g = int(s[2:4], 16) / 255.0
                b = int(s[4:6], 16) / 255.0
                return (r, g, b, 1.0)
            except ValueError:
                return None
        elif len(s) == 8:
            try:
                r = int(s[0:2], 16) / 255.0
                g = int(s[2:4], 16) / 255.0
                b = int(s[4:6], 16) / 255.0
                a = int(s[6:8], 16) / 255.0
                return (r, g, b, a)
            except ValueError:
                return None
        return None

    def _get_contrast_text_color(self, hex_code):
        rgba = self._hex_to_rgba(hex_code)
        if not rgba:
            return "#ffffff"
        lum = 0.299 * rgba[0] + 0.587 * rgba[1] + 0.114 * rgba[2]
        return "#1e1e1e" if lum > 0.6 else "#ffffff"

    def _get_friendly_color_name(self, hex_code):
        rgba = self._hex_to_rgba(hex_code)
        if not rgba:
            return "Coloured"
        r, g, b, _ = rgba
        max_c = max(r, g, b)
        min_c = min(r, g, b)
        d = max_c - min_c
        if max_c < 0.15:
            return "Black"
        if max_c > 0 and (d / max_c) < 0.15:
            return "White" if max_c > 0.85 else "Grey"
        if d == 0:
            return "Grey"
        if max_c == r:
            h = 60 * ((g - b) / d)
        elif max_c == g:
            h = 60 * ((b - r) / d + 2)
        else:
            h = 60 * ((r - g) / d + 4)
        if h < 0:
            h += 360

        if h < 15 or h >= 345:
            return "Red"
        elif h < 40:
            return "Orange"
        elif h < 65:
            return "Yellow"
        elif h < 160:
            return "Green"
        elif h < 195:
            return "Teal"
        elif h < 255:
            return "Blue"
        elif h < 290:
            return "Purple"
        else:
            return "Pink"

    def get_color_label(self, hex_code):
        if not hex_code:
            return None
        norm = hex_code.strip().lower()
        custom_names = self._load_color_names()
        if norm in custom_names:
            return custom_names[norm]
        if norm in COLOR_NAMES_MAP:
            return COLOR_NAMES_MAP[norm]
        return self._get_friendly_color_name(norm)

    def _register_color_css(self, hex_code):
        if not hasattr(self, "_color_css_provider"):
            self._color_css_provider = Gtk.CssProvider()
            self._color_css_rules = {}
            display = Gdk.Display.get_default()
            if display:
                Gtk.StyleContext.add_provider_for_display(
                    display,
                    self._color_css_provider,
                    Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 20
                )

        clean = hex_code.strip().lstrip("#").lower()
        class_name = f"color-pill-{clean}"
        if class_name not in getattr(self, "_color_css_rules", {}):
            fg = self._get_contrast_text_color(hex_code)
            rule = (
                f".{class_name} {{\n"
                f"  background-color: #{clean};\n"
                f"  color: {fg};\n"
                f"  border-radius: 999px;\n"
                f"  padding: 2px 8px;\n"
                f"  font-size: 10px;\n"
                f"  font-weight: 700;\n"
                f"}}\n"
            )
            self._color_css_rules[class_name] = rule
            all_css = "".join(self._color_css_rules.values())
            self._color_css_provider.load_from_data(all_css.encode("utf-8"))
        return class_name

    def _create_color_circle_dot(self, state_dict, size=10):
        dot = Gtk.DrawingArea()
        dot.set_size_request(size, size)
        dot.set_valign(Gtk.Align.CENTER)
        dot.set_halign(Gtk.Align.CENTER)

        def draw_dot(da, cr, w, h, s=state_dict):
            c_hex = s.get("color")
            if not c_hex:
                return
            rgba = self._hex_to_rgba(c_hex)
            if not rgba:
                return
            cx, cy = w / 2.0, h / 2.0
            r = min(w, h) / 2.0 - 0.75
            cr.arc(cx, cy, r, 0, 2 * math.pi)
            cr.set_source_rgba(rgba[0], rgba[1], rgba[2], 1.0)
            cr.fill_preserve()
            cr.set_source_rgba(0.0, 0.0, 0.0, 0.28)
            cr.set_line_width(0.8)
            cr.stroke()

        dot.set_draw_func(draw_dot)
        return dot

    def _update_card_color_ui(self, path):
        color = self.get_item_color(path)
        card_state = getattr(self, "_library_card_widgets", {}).get(path)
        if not card_state:
            return

        card_state["color"] = color
        label_text = self.get_color_label(color) if color else ""
        card_state["color_label"] = label_text

        # 1. Update card dot in grid
        color_badge = card_state.get("color_badge")
        if color_badge:
            if color:
                color_badge.set_tooltip_text(label_text)
                color_badge.set_visible(True)
                color_badge.queue_draw()
            else:
                color_badge.set_visible(False)

        # 2. Update list row dot
        row_color_badge = card_state.get("row_color_badge")
        if row_color_badge:
            if color:
                row_color_badge.set_tooltip_text(label_text)
                row_color_badge.set_visible(True)
                row_color_badge.queue_draw()
            else:
                row_color_badge.set_visible(False)

        # 3. Update drawing area thumbnail border
        da = card_state.get("drawing_area")
        if da:
            da.queue_draw()

        # 4. Refresh context menus
        mb = card_state.get("menu_btn")
        if mb:
            mb.set_popover(self._build_card_popover(path))
        rmb = card_state.get("row_menu_btn")
        if rmb:
            rmb.set_popover(self._build_card_popover(path))

    def _create_swatch_button(self, hex_code, tooltip_text, is_selected=False):
        btn = Gtk.Button(tooltip_text=tooltip_text)
        btn.add_css_class("flat")
        btn.add_css_class("color-swatch-circle")

        da = Gtk.DrawingArea()
        da.set_size_request(22, 22)
        rgba = self._hex_to_rgba(hex_code) or (0.5, 0.5, 0.5, 1.0)

        def draw_circle(area, cr, w, h):
            cx, cy = w / 2.0, h / 2.0
            r = min(w, h) / 2.0 - 2.0
            cr.arc(cx, cy, r, 0, 2 * math.pi)
            cr.set_source_rgba(rgba[0], rgba[1], rgba[2], 1.0)
            cr.fill_preserve()
            cr.set_source_rgba(0.0, 0.0, 0.0, 0.25)
            cr.set_line_width(1.0)
            cr.stroke()
            if is_selected:
                cr.arc(cx, cy, r + 1.5, 0, 2 * math.pi)
                cr.set_source_rgba(1.0, 1.0, 1.0, 0.95)
                cr.set_line_width(2.0)
                cr.stroke()

        da.set_draw_func(draw_circle)
        btn.set_child(da)
        return btn

    def prompt_name_color(self, hex_code):
        if not hex_code:
            return
        current_name = self.get_color_label(hex_code)
        heading = "Name Colour"
        body = f"Enter a label for {hex_code.upper()} across your collection:"
        entry = Gtk.Entry(
            text=current_name if current_name != hex_code else "",
            placeholder_text="e.g. Urgent, Work, Personal…",
            hexpand=True,
            margin_top=6
        )

        def do_save(new_name):
            self._save_color_name(hex_code, new_name)
            for p in list(getattr(self, "_library_card_widgets", {}).keys()):
                if self.get_item_color(p) == hex_code:
                    self._update_card_color_ui(p)
            self.show_toast(f"Saved label “{new_name}” for {hex_code.upper()}")

        if hasattr(Adw, "AlertDialog"):
            dialog = Adw.AlertDialog.new(heading, body)
            dialog.set_extra_child(entry)
            dialog.add_response("cancel", "Cancel")
            dialog.add_response("clear", "Reset to Default")
            dialog.add_response("save", "Save")
            dialog.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
            dialog.set_default_response("save")
            dialog.set_close_response("cancel")

            entry.connect("activate", lambda *_: dialog.response("save"))

            def on_response(dlg, res):
                choice = dlg.choose_finish(res)
                if choice == "save":
                    do_save(entry.get_text())
                elif choice == "clear":
                    do_save("")

            dialog.choose(self, None, on_response)
        else:
            dlg = Gtk.MessageDialog(
                transient_for=self,
                flags=Gtk.DialogFlags.MODAL,
                message_type=Gtk.MessageType.QUESTION,
                buttons=Gtk.ButtonsType.OK_CANCEL,
                text=heading
            )
            dlg.format_secondary_text(body)
            dlg.get_content_area().append(entry)
            dlg.connect("response", lambda d, r: (
                do_save(entry.get_text()) if r == Gtk.ResponseType.OK else None,
                d.destroy()
            ))
            dlg.present()

    def prompt_custom_hex_color(self, path, current_hex=None):
        heading = "Custom Colour"
        body = "Enter a HEX colour code (e.g. #3b82f6 or #ff5722):"
        entry = Gtk.Entry(
            text=current_hex or "#",
            placeholder_text="#RRGGBB",
            hexpand=True,
            margin_top=6
        )

        def do_apply(raw_hex):
            raw = raw_hex.strip()
            if not raw.startswith("#"):
                raw = "#" + raw
            rgba = self._hex_to_rgba(raw)
            if not rgba:
                self.show_error("Invalid HEX colour. Please use format #RRGGBB.")
                return
            self.set_item_color(path, raw)
            self.show_toast(f"Colour set to {self.get_color_label(raw)}")

        if hasattr(Adw, "AlertDialog"):
            dialog = Adw.AlertDialog.new(heading, body)
            dialog.set_extra_child(entry)
            dialog.add_response("cancel", "Cancel")
            dialog.add_response("apply", "Apply")
            dialog.set_response_appearance("apply", Adw.ResponseAppearance.SUGGESTED)
            dialog.set_default_response("apply")
            dialog.set_close_response("cancel")

            entry.connect("activate", lambda *_: dialog.response("apply"))

            def on_response(dlg, res):
                choice = dlg.choose_finish(res)
                if choice == "apply":
                    do_apply(entry.get_text())

            dialog.choose(self, None, on_response)
        else:
            dlg = Gtk.MessageDialog(
                transient_for=self,
                flags=Gtk.DialogFlags.MODAL,
                message_type=Gtk.MessageType.QUESTION,
                buttons=Gtk.ButtonsType.OK_CANCEL,
                text=heading
            )
            dlg.format_secondary_text(body)
            dlg.get_content_area().append(entry)
            dlg.connect("response", lambda d, r: (
                do_apply(entry.get_text()) if r == Gtk.ResponseType.OK else None,
                d.destroy()
            ))
            dlg.present()

    def _build_color_popover_view(self, popover, stack, path, current_color):
        vbox = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=6,
            margin_top=6,
            margin_bottom=6,
            margin_start=6,
            margin_end=6
        )

        header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        back_btn = Gtk.Button()
        back_btn.add_css_class("flat")
        back_icon = Gtk.Image.new_from_icon_name("go-previous-symbolic")
        back_btn.set_child(back_icon)
        back_btn.set_tooltip_text("Back")
        back_btn.connect("clicked", lambda *_: stack.set_visible_child_name("main"))
        header_box.append(back_btn)

        title_lbl = Gtk.Label(label="Colour Palette", xalign=0.0, hexpand=True)
        title_lbl.add_css_class("card-title")
        header_box.append(title_lbl)

        vbox.append(header_box)

        grid = Gtk.Grid()
        grid.set_column_spacing(4)
        grid.set_row_spacing(4)
        grid.set_halign(Gtk.Align.CENTER)

        for i, (hex_code, s_name) in enumerate(DEFAULT_SWATCHES):
            row = i // 7
            col = i % 7
            swatch_btn = self._create_swatch_button(hex_code, s_name, is_selected=(current_color == hex_code))
            swatch_btn.connect("clicked", lambda *_, h=hex_code: (
                popover.popdown(),
                self.set_item_color(path, h),
                self.show_toast(f"Colour set to {self.get_color_label(h)}")
            ))
            grid.attach(swatch_btn, col, row, 1, 1)

        vbox.append(grid)
        vbox.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        custom_btn = Gtk.Button(label="Custom Colour (HEX)…")
        custom_btn.add_css_class("flat")
        custom_btn.connect("clicked", lambda *_: (
            popover.popdown(),
            self.prompt_custom_hex_color(path, current_color)
        ))
        vbox.append(custom_btn)

        if current_color:
            name_btn = Gtk.Button(label="Name Current Colour…")
            name_btn.add_css_class("flat")
            name_btn.connect("clicked", lambda *_: (
                popover.popdown(),
                self.prompt_name_color(current_color)
            ))
            vbox.append(name_btn)

            clear_btn = Gtk.Button(label="Remove Colour")
            clear_btn.add_css_class("flat")
            clear_btn.add_css_class("destructive-action")
            clear_btn.connect("clicked", lambda *_: (
                popover.popdown(),
                self.set_item_color(path, None),
                self.show_toast("Colour removed")
            ))
            vbox.append(clear_btn)

        return vbox

    def _get_active_category_folder(self):
        sub = getattr(self, "active_subfolder_filter", None)
        if sub and sub in getattr(self, "notebooks_data", {}):
            folder = self.notebooks_data[sub].get("folder_path")
            if folder and os.path.isdir(folder):
                return folder
        if (
            self.active_notebook_filter not in ("ALL", ".", "UNCATEGORIZED", "TRASH", None)
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
        if not target_key or target_key in ("ALL", "CANVASES", "__NEW_CATEGORY__", ".", "UNCATEGORIZED", "TRASH"):
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
        if not target_key or target_key in ("ALL", "CANVASES", "__NEW_CATEGORY__", ".", "UNCATEGORIZED", "TRASH"):
            self.show_error("Please select a category to delete.")
            return

        nb_info = self.notebooks_data.get(target_key, {})
        folder_path = nb_info.get("folder_path") or os.path.join(self.current_root_folder, target_key)
        if not os.path.isdir(folder_path):
            self.show_error(f"Category folder “{target_key}” does not exist.")
            return

        cat_name = os.path.basename(os.path.normpath(folder_path))
        heading = f"Move Category “{cat_name}” to Trash?"
        body = f"Are you sure you want to move “{cat_name}” and all its notes to Trash? You can restore it later from Trash."

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

                if getattr(self, "active_notebook_filter", None) == target_key:
                    self.active_notebook_filter = "ALL"

                self.move_to_trash(folder_path)
            except Exception as e:
                self.show_error(f"Failed to move category to Trash: {e}")

        if hasattr(Adw, "AlertDialog"):
            dialog = Adw.AlertDialog.new(heading, body)
            dialog.add_response("cancel", "Cancel")
            dialog.add_response("trash", "Move to Trash")
            dialog.set_response_appearance("trash", Adw.ResponseAppearance.DESTRUCTIVE)
            dialog.set_default_response("cancel")
            dialog.set_close_response("cancel")

            def on_response(dlg, res):
                choice = dlg.choose_finish(res)
                if choice == "trash":
                    do_delete()

            dialog.choose(self, None, on_response)
        elif hasattr(Adw, "MessageDialog"):
            dialog = Adw.MessageDialog.new(self, heading, body)
            dialog.add_response("cancel", "Cancel")
            dialog.add_response("trash", "Move to Trash")
            dialog.set_response_appearance("trash", Adw.ResponseAppearance.DESTRUCTIVE)
            dialog.set_default_response("cancel")
            dialog.set_close_response("cancel")

            def on_legacy_response(dlg, response_id):
                dlg.destroy()
                if response_id == "trash":
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
        d_box.append(Gtk.Label(label="Move Category to Trash…", xalign=0.0, hexpand=True))
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
        if cat_key in ("ALL", "CANVASES", ".", "__NEW_CATEGORY__", "UNCATEGORIZED", "TRASH"):
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
        if cat_key in ("ALL", "CANVASES", ".", "UNCATEGORIZED", "TRASH", None):
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
        elif notebook_key == "TRASH":
            self.collection_title.set_title("Trash")
        else:
            self.collection_title.set_title(notebook_title)

        if notebook_key == "TRASH":
            if hasattr(self, "empty_trash_btn"):
                self.empty_trash_btn.set_visible(True)
                self.empty_trash_btn.set_sensitive(self._get_trash_count() > 0)
            if hasattr(self, "sort_menu_btn"):
                self.sort_menu_btn.set_visible(False)
            if hasattr(self, "lib_mode_box"):
                self.lib_mode_box.set_visible(False)
            if hasattr(self, "new_note_btn"):
                self.new_note_btn.set_sensitive(False)
            self._update_category_edit_button()
            self._update_category_buttons()
            self._render_trash_view()
        else:
            if hasattr(self, "empty_trash_btn"):
                self.empty_trash_btn.set_visible(False)
            if hasattr(self, "sort_menu_btn"):
                self.sort_menu_btn.set_visible(True)
            if hasattr(self, "lib_mode_box"):
                self.lib_mode_box.set_visible(True)
            if hasattr(self, "new_note_btn"):
                self.new_note_btn.set_sensitive(True)
            self._update_category_edit_button()
            self._update_category_buttons()
            self._apply_collection_filters()

    def _apply_collection_filters(self):
        query = self.search_entry.get_text().strip().lower()
        selected_nb = self.active_notebook_filter
        if selected_nb == "TRASH":
            vis = self._filter_trash_listbox(query)
            if vis == 0:
                if query and hasattr(self, "empty_search_page"):
                    self.content_stack.set_visible_child_name("empty_search")
                elif hasattr(self, "empty_trash_page"):
                    self.content_stack.set_visible_child_name("empty_trash")
            else:
                self.content_stack.set_visible_child_name("trash")
            return
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

    def _create_sidebar_row(self, title, notebook_key, icon_name="folder-symbolic", section_label=None, is_new_category_action=False, count=None):
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

        if count is not None and count > 0:
            count_lbl = Gtk.Label(label=str(count))
            count_lbl.add_css_class("dim-label")
            count_lbl.add_css_class("numeric")
            hbox.append(count_lbl)
            row.count_lbl = count_lbl

        if notebook_key not in ("ALL", "CANVASES", "__NEW_CATEGORY__", ".", "UNCATEGORIZED", "TRASH"):
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
        elif prev_filter == "TRASH":
            target_filter = "TRASH"
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
                icon_name="category-symbolic",
                section_label=None
            )
            self.notebook_listbox.append(row)
            if target_filter == nb_key or parent_cat == nb_key:
                self.notebook_listbox.select_row(row)

        uncat_row = self._create_sidebar_row(
            "Uncategorized",
            "UNCATEGORIZED",
            icon_name="uncategorized-symbolic",
            section_label=None
        )
        self.notebook_listbox.append(uncat_row)
        if target_filter == "UNCATEGORIZED":
            self.notebook_listbox.select_row(uncat_row)

        trash_count = self._get_trash_count()
        trash_row = self._create_sidebar_row(
            "Trash",
            "TRASH",
            icon_name=self._resolve_icon_name("user-trash-symbolic", "user-trash-symbolic"),
            section_label=None,
            count=trash_count
        )
        self.trash_sidebar_row = trash_row
        self.notebook_listbox.append(trash_row)
        if target_filter == "TRASH":
            self.notebook_listbox.select_row(trash_row)

        if target_filter != parent_cat and target_filter not in ("UNCATEGORIZED", "TRASH"):
            self.active_notebook_filter = parent_cat
            self.active_subfolder_filter = target_filter

        if target_filter == "ALL":
            self.collection_title.set_title("Library")
        elif target_filter == "CANVASES":
            self.collection_title.set_title("Canvases")
        elif target_filter == "UNCATEGORIZED":
            self.collection_title.set_title("Uncategorized")
        elif target_filter == "TRASH":
            self.collection_title.set_title("Trash")
        elif parent_cat in notebooks:
            self.collection_title.set_title(notebooks[parent_cat]["title"])
        self._update_category_edit_button()
        self._update_category_buttons()

        if target_filter == "TRASH":
            if hasattr(self, "empty_trash_btn"):
                self.empty_trash_btn.set_visible(True)
                self.empty_trash_btn.set_sensitive(trash_count > 0)
            if hasattr(self, "sort_menu_btn"):
                self.sort_menu_btn.set_visible(False)
            if hasattr(self, "lib_mode_box"):
                self.lib_mode_box.set_visible(False)
            if hasattr(self, "new_note_btn"):
                self.new_note_btn.set_sensitive(False)
            self._render_trash_view()
        else:
            if hasattr(self, "empty_trash_btn"):
                self.empty_trash_btn.set_visible(False)
            if hasattr(self, "sort_menu_btn"):
                self.sort_menu_btn.set_visible(True)
            if hasattr(self, "lib_mode_box"):
                self.lib_mode_box.set_visible(True)
            if hasattr(self, "new_note_btn"):
                self.new_note_btn.set_sensitive(True)

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
            self._update_card_color_ui(path)
            da.queue_draw()
            return

        ext = os.path.splitext(path)[1].lower()
        name_without_ext = os.path.splitext(os.path.basename(path))[0]
        color = self.get_item_color(path)
        color_label = self.get_color_label(color) if color else ""

        card_state = {
            "surface": surface,
            "thumb_w": thumb_w,
            "thumb_h": thumb_h,
            "color": color,
            "color_label": color_label,
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

            color_hex = state.get("color")
            if color_hex:
                rgba = self._hex_to_rgba(color_hex)
                if rgba:
                    cr.save()
                    self.canvas._rounded_rect(cr, 1.5, 1.5, width - 3.0, height - 3.0, 11.0)
                    cr.set_source_rgba(rgba[0], rgba[1], rgba[2], 0.95)
                    cr.set_line_width(2.5)
                    cr.stroke()
                    cr.restore()

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

        color_badge = self._create_color_circle_dot(card_state, size=10)
        color_badge.set_margin_start(2)
        color_badge.set_margin_end(2)
        if color:
            color_badge.set_tooltip_text(color_label)
            color_badge.set_visible(True)
        else:
            color_badge.set_visible(False)
        meta_row.append(color_badge)

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

        row_color_badge = self._create_color_circle_dot(card_state, size=10)
        row_color_badge.set_margin_start(2)
        row_color_badge.set_margin_end(2)
        if color:
            row_color_badge.set_tooltip_text(color_label)
            row_color_badge.set_visible(True)
        else:
            row_color_badge.set_visible(False)
        row_hbox.append(row_color_badge)

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

        list_right_click = Gtk.GestureClick.new()
        list_right_click.set_button(3)
        list_right_click.connect("pressed", lambda gesture, n_press, x, y, mb=row_menu_btn: mb.popup())
        list_row.add_controller(list_right_click)

        list_row.set_child(row_hbox)
        self.library_listbox.append(list_row)

        card_state["drawing_area"] = drawing_area
        card_state["card_box"] = card_box
        card_state["spinner"] = spinner
        card_state["list_row"] = list_row
        card_state["color_badge"] = color_badge
        card_state["row_color_badge"] = row_color_badge
        card_state["menu_btn"] = menu_btn
        card_state["row_menu_btn"] = row_menu_btn
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

        current_color = self.get_item_color(path)
        current_name = self.get_color_label(current_color) if current_color else None

        stack = Gtk.Stack()
        stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)
        stack.set_transition_duration(200)

        main_vbox = Gtk.Box(
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
        main_vbox.append(view_btn)

        choose_btn = Gtk.Button(label="Open With...")
        choose_btn.add_css_class("flat")
        choose_btn.connect("clicked", lambda *_: (popover.popdown(), self.choose_app_for_file(path)))
        main_vbox.append(choose_btn)

        rename_btn = Gtk.Button(label="Rename…")
        rename_btn.add_css_class("flat")
        rename_btn.connect("clicked", lambda *_: (popover.popdown(), self.rename_note_file(path)))
        main_vbox.append(rename_btn)

        main_vbox.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        # Colour Code Header & Active Circle
        color_header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6, margin_start=4, margin_end=4, margin_top=2)
        color_lbl = Gtk.Label(label="Colour Code", xalign=0.0, hexpand=True)
        color_lbl.add_css_class("card-subtitle")
        color_header_box.append(color_lbl)

        if current_color:
            dot = self._create_color_circle_dot({"color": current_color}, size=12)
            dot.set_visible(True)
            if current_name:
                dot.set_tooltip_text(current_name)
            color_header_box.append(dot)
        main_vbox.append(color_header_box)

        # Quick swatches row
        swatches_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4, margin_start=2, margin_end=2, margin_bottom=2)

        # None / Clear Swatch
        clear_btn = Gtk.Button(tooltip_text="Clear Colour")
        clear_btn.add_css_class("flat")
        clear_btn.add_css_class("color-swatch-circle")
        clear_icon = Gtk.Image.new_from_icon_name("edit-clear-symbolic")
        clear_icon.set_pixel_size(14)
        clear_btn.set_child(clear_icon)
        clear_btn.connect("clicked", lambda *_: (
            popover.popdown(),
            self.set_item_color(path, None),
            self.show_toast("Colour removed")
        ))
        swatches_row.append(clear_btn)

        QUICK_SWATCHES = [
            ("#e06c75", "Red"),
            ("#e57042", "Coral"),
            ("#e5c07b", "Yellow"),
            ("#98c379", "Green"),
            ("#61afef", "Blue"),
            ("#c678dd", "Purple"),
            ("#abb2bf", "Slate"),
        ]
        for hex_code, s_name in QUICK_SWATCHES:
            swatch_btn = self._create_swatch_button(hex_code, s_name, is_selected=(current_color == hex_code))
            swatch_btn.connect("clicked", lambda *_, h=hex_code: (
                popover.popdown(),
                self.set_item_color(path, h),
                self.show_toast(f"Colour set to {self.get_color_label(h)}")
            ))
            swatches_row.append(swatch_btn)

        more_colors_btn = Gtk.Button(tooltip_text="More Colours & Naming…")
        more_colors_btn.add_css_class("flat")
        more_colors_btn.add_css_class("color-swatch-circle")
        more_icon = Gtk.Image.new_from_icon_name("view-more-symbolic")
        more_icon.set_pixel_size(14)
        more_colors_btn.set_child(more_icon)
        more_colors_btn.connect("clicked", lambda *_: stack.set_visible_child_name("colors"))
        swatches_row.append(more_colors_btn)

        main_vbox.append(swatches_row)

        if current_color:
            name_color_btn = Gtk.Button(label="Name This Colour…")
            name_color_btn.add_css_class("flat")
            name_color_btn.connect("clicked", lambda *_: (popover.popdown(), self.prompt_name_color(current_color)))
            main_vbox.append(name_color_btn)

        main_vbox.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        folder_btn = Gtk.Button(label="Show in Folder")
        folder_btn.add_css_class("flat")
        folder_btn.connect("clicked", lambda *_: (popover.popdown(), self.open_containing_folder(path)))
        main_vbox.append(folder_btn)

        del_btn = Gtk.Button(label="Move to Trash")
        del_btn.add_css_class("flat")
        del_btn.add_css_class("destructive-action")
        del_btn.connect("clicked", lambda *_: (popover.popdown(), self.delete_note_file(path)))
        main_vbox.append(del_btn)

        stack.add_named(main_vbox, "main")

        color_view = self._build_color_popover_view(popover, stack, path, current_color)
        stack.add_named(color_view, "colors")

        popover.set_child(stack)
        return popover

    def get_trash_dir(self):
        if not getattr(self, "current_root_folder", None) or not os.path.isdir(self.current_root_folder):
            return None
        return os.path.join(self.current_root_folder, ".xnote", "trash")

    def _get_trash_count(self):
        trash_dir = self.get_trash_dir()
        if not trash_dir or not os.path.isdir(trash_dir):
            return 0
        try:
            entries = os.listdir(trash_dir)
            count = 0
            for e in entries:
                p = os.path.join(trash_dir, e)
                if os.path.isdir(p):
                    count += 1
            return count
        except OSError:
            return 0

    def _update_trash_sidebar_badge(self):
        count = self._get_trash_count()
        if hasattr(self, "empty_trash_btn"):
            if getattr(self, "active_notebook_filter", None) == "TRASH":
                self.empty_trash_btn.set_sensitive(count > 0)
        row = getattr(self, "trash_sidebar_row", None)
        if row is not None:
            lbl = getattr(row, "count_lbl", None)
            if count > 0:
                if lbl is not None:
                    lbl.set_text(str(count))
                    lbl.set_visible(True)
                else:
                    hbox = row.get_child()
                    if isinstance(hbox, Gtk.Box):
                        count_lbl = Gtk.Label(label=str(count))
                        count_lbl.add_css_class("dim-label")
                        count_lbl.add_css_class("numeric")
                        hbox.append(count_lbl)
                        row.count_lbl = count_lbl
            else:
                if lbl is not None:
                    lbl.set_visible(False)

    def list_trash_items(self):
        trash_dir = self.get_trash_dir()
        if not trash_dir or not os.path.isdir(trash_dir):
            return []
        items = []
        try:
            for wrapper in os.listdir(trash_dir):
                wrapper_path = os.path.join(trash_dir, wrapper)
                if not os.path.isdir(wrapper_path):
                    continue

                info_file = os.path.join(wrapper_path, "trash.json")
                info = {}
                if os.path.isfile(info_file):
                    try:
                        with open(info_file, "r", encoding="utf-8") as f:
                            info = json.load(f)
                    except Exception:
                        pass

                contents = [c for c in os.listdir(wrapper_path) if c != "trash.json"]
                if not contents:
                    continue

                item_name = contents[0]
                item_path = os.path.join(wrapper_path, item_name)
                is_dir = os.path.isdir(item_path)

                orig_name = info.get("name") or item_name
                path_parts = info.get("path") or []
                if isinstance(path_parts, str):
                    path_parts = [path_parts]

                deleted_raw = info.get("deleted")
                deleted_ts = 0.0
                deleted_str = ""
                if deleted_raw:
                    if isinstance(deleted_raw, (int, float)):
                        deleted_ts = float(deleted_raw)
                        if deleted_ts > 1e11:  # milliseconds
                            deleted_ts /= 1000.0
                        deleted_str = datetime.datetime.fromtimestamp(deleted_ts).strftime("%Y-%m-%d %H:%M")
                    elif isinstance(deleted_raw, str):
                        try:
                            clean_iso = deleted_raw.replace("Z", "+00:00")
                            dt = datetime.datetime.fromisoformat(clean_iso)
                            deleted_ts = dt.timestamp()
                            deleted_str = dt.astimezone().strftime("%Y-%m-%d %H:%M")
                        except Exception:
                            deleted_str = str(deleted_raw)

                if not deleted_ts:
                    try:
                        ts_prefix = wrapper.split("-")[0]
                        deleted_ts = float(ts_prefix) / 1000.0
                        deleted_str = datetime.datetime.fromtimestamp(deleted_ts).strftime("%Y-%m-%d %H:%M")
                    except Exception:
                        try:
                            deleted_ts = os.path.getmtime(item_path)
                            deleted_str = datetime.datetime.fromtimestamp(deleted_ts).strftime("%Y-%m-%d %H:%M")
                        except Exception:
                            pass

                size_bytes = 0
                try:
                    if is_dir:
                        for r, _, fs in os.walk(item_path):
                            for fn in fs:
                                size_bytes += os.path.getsize(os.path.join(r, fn))
                    else:
                        size_bytes = os.path.getsize(item_path)
                except OSError:
                    pass

                ext = os.path.splitext(orig_name)[1].lower() if not is_dir else ""
                if is_dir:
                    kind_label = "Category Folder"
                    icon_name = "folder-symbolic"
                elif ext == ".xcanvas":
                    kind_label = "Infinite Canvas"
                    icon_name = "image-alt-symbolic"
                elif ext == ".xnote":
                    kind_label = "Notebook"
                    icon_name = "x-office-document-symbolic"
                elif ext in (".md", ".markdown"):
                    kind_label = "Markdown Note"
                    icon_name = "text-x-generic-symbolic"
                elif ext == ".rnote":
                    kind_label = "Rnote File"
                    icon_name = "draw-freehand-symbolic"
                elif ext == ".pdf":
                    kind_label = "PDF Document"
                    icon_name = "x-office-document-symbolic"
                else:
                    kind_label = "Document"
                    icon_name = "text-x-generic-symbolic"

                items.append({
                    "wrapper_id": wrapper,
                    "wrapper_path": wrapper_path,
                    "item_name": item_name,
                    "item_path": item_path,
                    "orig_name": orig_name,
                    "orig_path_parts": path_parts,
                    "orig_rel_dir": " / ".join(path_parts) if path_parts else "Library",
                    "deleted_timestamp": deleted_ts,
                    "deleted_str": deleted_str,
                    "size_bytes": size_bytes,
                    "size_str": _format_file_size(size_bytes),
                    "is_dir": is_dir,
                    "file_ext": ext,
                    "kind_label": kind_label,
                    "icon_name": icon_name,
                })
        except OSError:
            pass

        items.sort(key=lambda x: x["deleted_timestamp"], reverse=True)
        return items

    def move_to_trash(self, path):
        if not path or not os.path.exists(path):
            self.show_error("Note file or folder does not exist.")
            return False

        if not self.current_root_folder or not os.path.isdir(self.current_root_folder):
            self.show_error("Open a collection folder first.")
            return False

        try:
            if getattr(self, "current_file_path", None):
                if os.path.abspath(self.current_file_path) == os.path.abspath(path):
                    self._perform_return_to_collection()

            trash_dir = self.get_trash_dir()
            if not trash_dir:
                return False
            os.makedirs(trash_dir, exist_ok=True)

            file_name = os.path.basename(os.path.normpath(path))
            parent_dir = os.path.dirname(os.path.abspath(path))
            root_abs = os.path.abspath(self.current_root_folder)
            rel_dir = os.path.relpath(parent_dir, root_abs)
            if rel_dir == ".":
                path_parts = []
            else:
                path_parts = [p for p in rel_dir.split(os.sep) if p]

            now_ms = int(time.time() * 1000)
            uid = uuid.uuid4().hex[:6]
            wrapper_name = f"{now_ms}-{uid}"
            wrapper_path = os.path.join(trash_dir, wrapper_name)
            os.makedirs(wrapper_path, exist_ok=True)

            dest_item_path = os.path.join(wrapper_path, file_name)
            shutil.move(path, dest_item_path)

            info = {
                "version": 1,
                "name": file_name,
                "path": path_parts,
                "deleted": datetime.datetime.now(datetime.timezone.utc).isoformat()
            }
            with open(os.path.join(wrapper_path, "trash.json"), "w", encoding="utf-8") as f:
                json.dump(info, f, indent=2)

            thumb_cache = getattr(self, "_library_thumb_cache", None)
            if thumb_cache:
                keys_to_del = [k for k in list(thumb_cache.keys()) if k[0] == path]
                for k in keys_to_del:
                    thumb_cache.pop(k, None)

            item_info = {
                "wrapper_id": wrapper_name,
                "wrapper_path": wrapper_path,
                "item_name": file_name,
                "item_path": dest_item_path,
                "orig_name": file_name,
                "orig_path_parts": path_parts,
                "orig_rel_dir": " / ".join(path_parts) if path_parts else "Library",
                "is_dir": os.path.isdir(dest_item_path),
            }
            self._last_trashed_item = item_info

            self._update_trash_sidebar_badge()

            if self.current_root_folder and os.path.isdir(self.current_root_folder):
                self.load_folder(self.current_root_folder)

            self.show_toast(
                f"Moved “{file_name}” to Trash",
                timeout=5,
                button_label="Undo",
                on_button_clicked=self.undo_last_trash
            )
            return True
        except Exception as e:
            self.show_error(f"Failed to move to Trash: {e}")
            return False

    def undo_last_trash(self):
        last = getattr(self, "_last_trashed_item", None)
        if last:
            self._last_trashed_item = None
            self.restore_trash_item(last)

    def restore_trash_item(self, item_info):
        try:
            if not self.current_root_folder or not os.path.isdir(self.current_root_folder):
                self.show_error("Open a collection folder first.")
                return False

            orig_path_parts = item_info.get("orig_path_parts", [])
            dest_dir = self.current_root_folder
            if orig_path_parts:
                dest_dir = os.path.join(self.current_root_folder, *orig_path_parts)
            os.makedirs(dest_dir, exist_ok=True)

            orig_name = item_info.get("orig_name") or item_info.get("item_name")
            target_name = orig_name
            target_path = os.path.join(dest_dir, target_name)
            if os.path.exists(target_path):
                is_dir = item_info.get("is_dir", False)
                stem, ext = os.path.splitext(target_name) if not is_dir else (target_name, "")
                n = 2
                while os.path.exists(os.path.join(dest_dir, f"{stem} ({n}){ext}")):
                    n += 1
                target_name = f"{stem} ({n}){ext}"
                target_path = os.path.join(dest_dir, target_name)

            src_item_path = item_info.get("item_path")
            if not src_item_path or not os.path.exists(src_item_path):
                self.show_error("Trashed file no longer exists.")
                return False

            shutil.move(src_item_path, target_path)

            wrapper = item_info.get("wrapper_path")
            if wrapper and os.path.exists(wrapper):
                shutil.rmtree(wrapper, ignore_errors=True)

            self._update_trash_sidebar_badge()
            if getattr(self, "active_notebook_filter", None) == "TRASH":
                self._render_trash_view()

            if self.current_root_folder and os.path.isdir(self.current_root_folder):
                self.load_folder(self.current_root_folder)

            rel_where = item_info.get("orig_rel_dir", "Library")
            self.show_toast(f"Restored “{target_name}” to {rel_where}")
            return True
        except Exception as e:
            self.show_error(f"Failed to restore item: {e}")
            return False

    def delete_trash_item_permanently(self, item_info):
        file_name = item_info.get("orig_name") or item_info.get("item_name")
        heading = f"Delete “{file_name}” permanently?"
        body = f"Are you sure you want to permanently delete “{file_name}”? This action cannot be undone."

        def do_delete():
            try:
                wrapper = item_info.get("wrapper_path")
                if wrapper and os.path.exists(wrapper):
                    shutil.rmtree(wrapper, ignore_errors=True)
                self._update_trash_sidebar_badge()
                self._render_trash_view()
                self.show_toast(f"Permanently deleted “{file_name}”")
            except Exception as e:
                self.show_error(f"Failed to delete item: {e}")

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

    def on_empty_trash_clicked(self, *_):
        items = self.list_trash_items()
        if not items:
            self.show_toast("Trash is already empty.")
            return

        count = len(items)
        heading = "Empty Trash?"
        body = f"Are you sure you want to permanently delete all {count} item(s) in Trash? This action cannot be undone."

        def do_empty():
            try:
                trash_dir = self.get_trash_dir()
                if trash_dir and os.path.exists(trash_dir):
                    for entry in os.listdir(trash_dir):
                        p = os.path.join(trash_dir, entry)
                        if os.path.isdir(p):
                            shutil.rmtree(p, ignore_errors=True)
                        elif os.path.isfile(p):
                            os.remove(p)
                self._update_trash_sidebar_badge()
                self._render_trash_view()
                self.show_toast("Emptied Trash")
            except Exception as e:
                self.show_error(f"Failed to empty trash: {e}")

        if hasattr(Adw, "AlertDialog"):
            dialog = Adw.AlertDialog.new(heading, body)
            dialog.add_response("cancel", "Cancel")
            dialog.add_response("empty", "Empty Trash")
            dialog.set_response_appearance("empty", Adw.ResponseAppearance.DESTRUCTIVE)
            dialog.set_default_response("cancel")
            dialog.set_close_response("cancel")

            def on_response(dlg, res):
                choice = dlg.choose_finish(res)
                if choice == "empty":
                    do_empty()

            dialog.choose(self, None, on_response)
        elif hasattr(Adw, "MessageDialog"):
            dialog = Adw.MessageDialog.new(self, heading, body)
            dialog.add_response("cancel", "Cancel")
            dialog.add_response("empty", "Empty Trash")
            dialog.set_response_appearance("empty", Adw.ResponseAppearance.DESTRUCTIVE)
            dialog.set_default_response("cancel")
            dialog.set_close_response("cancel")

            def on_legacy_response(dlg, response_id):
                dlg.destroy()
                if response_id == "empty":
                    do_empty()

            dialog.connect("response", on_legacy_response)
            dialog.present()

    def _render_trash_view(self):
        if not hasattr(self, "trash_listbox"):
            return

        child = self.trash_listbox.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling()
            self.trash_listbox.remove(child)
            child = nxt

        trash_items = self.list_trash_items()

        if hasattr(self, "empty_trash_btn"):
            self.empty_trash_btn.set_sensitive(len(trash_items) > 0)

        if not trash_items:
            if hasattr(self, "empty_trash_page"):
                self.content_stack.set_visible_child_name("empty_trash")
            return

        for item in trash_items:
            row = Adw.ActionRow()
            row.set_title(item["orig_name"])
            row.set_subtitle(f"{item['kind_label']} · from {item['orig_rel_dir']} · Deleted {item['deleted_str']} · {item['size_str']}")
            row.item_title = item["orig_name"]
            row.item_subtitle = row.get_subtitle()

            icon_name = self._resolve_icon_name(item["icon_name"], "text-x-generic-symbolic")
            icon = Gtk.Image.new_from_icon_name(icon_name)
            icon.set_pixel_size(24)
            row.add_prefix(icon)

            restore_btn = Gtk.Button(
                icon_name=self._resolve_icon_name("restore-symbolic", "edit-undo-symbolic"),
                tooltip_text="Restore Note to Original Folder",
                valign=Gtk.Align.CENTER
            )
            restore_btn.add_css_class("flat")
            restore_btn.connect("clicked", lambda *_, it=item: self.restore_trash_item(it))
            row.add_suffix(restore_btn)

            del_btn = Gtk.Button(
                icon_name=self._resolve_icon_name("user-trash-symbolic", "user-trash-symbolic"),
                tooltip_text="Delete Permanently",
                valign=Gtk.Align.CENTER
            )
            del_btn.add_css_class("flat")
            del_btn.add_css_class("destructive-action")
            del_btn.connect("clicked", lambda *_, it=item: self.delete_trash_item_permanently(it))
            row.add_suffix(del_btn)

            self.trash_listbox.append(row)

        query = self.search_entry.get_text().strip().lower() if hasattr(self, "search_entry") else ""
        if query:
            vis = self._filter_trash_listbox(query)
            if vis == 0 and hasattr(self, "empty_search_page"):
                self.content_stack.set_visible_child_name("empty_search")
            else:
                self.content_stack.set_visible_child_name("trash")
        else:
            self.content_stack.set_visible_child_name("trash")

    def _filter_trash_listbox(self, query=""):
        if not hasattr(self, "trash_listbox"):
            return 0
        q = (query or "").strip().lower()
        row = self.trash_listbox.get_first_child()
        count = 0
        while row is not None:
            title = getattr(row, "item_title", "").lower()
            sub = getattr(row, "item_subtitle", "").lower()
            matches = (not q) or (q in title) or (q in sub)
            row.set_visible(matches)
            if matches:
                count += 1
            row = row.get_next_sibling()
        return count

    def delete_note_file(self, path):
        if not path or not os.path.exists(path):
            self.show_error("Note file does not exist.")
            return
        self.move_to_trash(path)


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

                old_color = self.get_item_color(path)
                if old_color:
                    colors = self._load_folder_colors(parent_dir)
                    colors.pop(full_filename, None)
                    colors[new_filename] = old_color
                    self._save_folder_colors(parent_dir, colors)

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
