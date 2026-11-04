import os
import math
import cairo
import gi

gi.require_version('Gtk', '4.0')
from gi.repository import Gtk, Gio, GLib, Gdk

try:
    gi.require_version('Adw', '1')
    from gi.repository import Adw
    HAS_ADW = True
except (ValueError, ImportError):
    Adw = None
    HAS_ADW = False

from xnote_io import XNotePackageParser
from markdown_renderer import MarkdownRenderer


EXPORT_FORMATS = [
    {
        "id": "pdf",
        "name": "Pdf",
        "ext": ".pdf",
        "mime": "application/pdf",
        "description": "Portable Document Format (*.pdf)",
    },
    {
        "id": "rnote",
        "name": "Rnote (.rnote)",
        "ext": ".rnote",
        "mime": "application/gzip",
        "description": "Rnote Vector Document (*.rnote)",
    },
    {
        "id": "png",
        "name": "Png Image",
        "ext": ".png",
        "mime": "image/png",
        "description": "Portable Network Graphics (*.png)",
    },
    {
        "id": "svg",
        "name": "Svg Vector",
        "ext": ".svg",
        "mime": "image/svg+xml",
        "description": "Scalable Vector Graphics (*.svg)",
    },
]


class ExportDocumentDialog(Adw.Dialog if HAS_ADW else Gtk.Window):
    """
    Unified Export Dialog presenting document preview and preferences
    for exporting to PDF, Rnote, PNG, SVG, and future formats.
    """

    def __init__(self, parent_window, doc, canvas, current_file_path=None, current_file_type=None, on_export_done_cb=None):
        super().__init__()
        self.parent_window = parent_window
        self.doc = doc
        self.canvas = canvas
        self.current_file_path = current_file_path
        self.current_file_type = current_file_type
        self.on_export_done_cb = on_export_done_cb

        self.current_page_idx = 0
        self.export_path = None
        self._preview_surface = None

        # Determine total pages
        if self.doc and "pages" in self.doc:
            self.total_pages = max(1, len(self.doc["pages"]))
        else:
            self.total_pages = 1

        # Default export base name
        if self.current_file_path:
            self.base_name = os.path.splitext(os.path.basename(self.current_file_path))[0]
            self.initial_folder = os.path.dirname(self.current_file_path)
        else:
            self.base_name = "Note" if self.current_file_type in ("md", "markdown") else "Notebook"
            self.initial_folder = (
                getattr(parent_window, "_get_active_category_folder", lambda: None)()
                or GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DOCUMENTS)
                or os.path.expanduser("~")
            )

        # Set default export destination path
        default_ext = EXPORT_FORMATS[0]["ext"]
        self.export_path = os.path.join(self.initial_folder, f"{self.base_name}{default_ext}")

        self.set_title("Export Document")
        if hasattr(self, "set_content_width"):
            self.set_content_width(840)
            self.set_content_height(540)
        else:
            self.set_default_size(840, 540)
            if hasattr(self, "set_transient_for"):
                self.set_transient_for(parent_window)
            if hasattr(self, "set_modal"):
                self.set_modal(True)

        self._build_ui()
        self._update_preview()

    def _dismiss(self):
        try:
            if hasattr(self, "close"):
                self.close()
            elif hasattr(self, "destroy"):
                self.destroy()
        except Exception:
            pass

    def _build_ui(self):
        toolbar_view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        header.set_show_end_title_buttons(False)
        header.set_show_start_title_buttons(False)

        # Cancel button (left)
        cancel_btn = Gtk.Button(label="Cancel")
        cancel_btn.connect("clicked", lambda *_: self._dismiss())
        header.pack_start(cancel_btn)

        # Window title (center)
        title_widget = Adw.WindowTitle(title="Export Document", subtitle="")
        header.set_title_widget(title_widget)

        # Export button (right, suggested-action)
        self.export_btn = Gtk.Button(label="Export")
        self.export_btn.add_css_class("suggested-action")
        self.export_btn.connect("clicked", self._on_export_clicked)
        header.pack_end(self.export_btn)

        toolbar_view.add_top_bar(header)

        # Main horizontal split: Preview on left, Preferences on right
        main_box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=20,
            margin_start=18,
            margin_end=18,
            margin_top=16,
            margin_bottom=18,
            hexpand=True,
            vexpand=True,
        )

        # =====================================================================
        # LEFT COLUMN: Document Preview Frame & Navigation
        # =====================================================================
        preview_vbox = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=10,
            hexpand=False,
            halign=Gtk.Align.CENTER,
            valign=Gtk.Align.FILL,
        )
        preview_vbox.set_size_request(320, -1)

        # Card container for preview
        self.preview_frame = Gtk.Frame()
        self.preview_frame.add_css_class("card")
        self.preview_frame.set_hexpand(True)
        self.preview_frame.set_vexpand(True)

        self.preview_area = Gtk.DrawingArea()
        self.preview_area.set_hexpand(True)
        self.preview_area.set_vexpand(True)
        self.preview_area.set_size_request(300, 390)
        self.preview_area.set_draw_func(self._on_draw_preview)
        self.preview_frame.set_child(self.preview_area)
        preview_vbox.append(self.preview_frame)

        # Multi-page navigation (only visible if multi-page document)
        self.nav_box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=8,
            halign=Gtk.Align.CENTER,
            visible=(self.total_pages > 1),
        )
        self.prev_page_btn = Gtk.Button(icon_name="go-previous-symbolic")
        self.prev_page_btn.add_css_class("flat")
        self.prev_page_btn.connect("clicked", self._on_prev_page_clicked)
        self.nav_box.append(self.prev_page_btn)

        self.page_label = Gtk.Label(label=f"Page 1 of {self.total_pages}")
        self.page_label.add_css_class("dim-label")
        self.nav_box.append(self.page_label)

        self.next_page_btn = Gtk.Button(icon_name="go-next-symbolic")
        self.next_page_btn.add_css_class("flat")
        self.next_page_btn.connect("clicked", self._on_next_page_clicked)
        self.nav_box.append(self.next_page_btn)

        preview_vbox.append(self.nav_box)
        main_box.append(preview_vbox)

        # =====================================================================
        # RIGHT COLUMN: Preferences & Destination File
        # =====================================================================
        right_scroll = Gtk.ScrolledWindow()
        right_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        right_scroll.set_hexpand(True)
        right_scroll.set_vexpand(True)

        right_vbox = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=16,
            hexpand=True,
            valign=Gtk.Align.START,
        )

        # 1. Export File Group
        file_group = Adw.PreferencesGroup()
        self.file_row = Adw.ActionRow(
            title="Export File",
            subtitle="Select the export file"
        )
        file_suffix_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)

        self.file_label = Gtk.Label(
            label=os.path.basename(self.export_path) if self.export_path else "- no file selected -",
            ellipsize=3,  # PANGO_ELLIPSIZE_END
            max_width_chars=22,
            halign=Gtk.Align.END,
        )
        self.file_label.add_css_class("dim-label")
        file_suffix_box.append(self.file_label)

        choose_file_btn = Gtk.Button(icon_name="folder-symbolic")
        choose_file_btn.set_tooltip_text("Choose Export Destination")
        choose_file_btn.add_css_class("flat")
        choose_file_btn.connect("clicked", self._on_choose_destination_clicked)
        file_suffix_box.append(choose_file_btn)

        self.file_row.add_suffix(file_suffix_box)
        file_group.add(self.file_row)
        right_vbox.append(file_group)

        # 2. Export Preferences Group
        prefs_group = Adw.PreferencesGroup(title="Export Preferences")

        # "With Background" SwitchRow
        self.with_bg_row = Adw.SwitchRow(
            title="With Background",
            subtitle="Set whether the background should be exported",
            active=True
        )
        self.with_bg_row.connect("notify::active", self._on_preference_changed)
        prefs_group.add(self.with_bg_row)

        # "With Pattern" SwitchRow
        self.with_pattern_row = Adw.SwitchRow(
            title="With Pattern",
            subtitle="Set whether the background pattern should be exported",
            active=True
        )
        self.with_pattern_row.connect("notify::active", self._on_preference_changed)
        prefs_group.add(self.with_pattern_row)

        # "Optimize for Printing" SwitchRow
        self.optimize_print_row = Adw.SwitchRow(
            title="Optimize for Printing",
            subtitle="Set whether the content should be optimized for printing",
            active=False
        )
        self.optimize_print_row.connect("notify::active", self._on_preference_changed)
        prefs_group.add(self.optimize_print_row)

        # "Export Format" ComboRow
        self.format_row = Adw.ComboRow(
            title="Export Format",
            subtitle="The export format"
        )
        format_names = [f["name"] for f in EXPORT_FORMATS]
        self.format_model = Gtk.StringList.new(format_names)
        self.format_row.set_model(self.format_model)
        self.format_row.set_selected(0)
        self.format_row.connect("notify::selected", self._on_format_changed)
        prefs_group.add(self.format_row)

        # "Page Order" ComboRow
        self.page_order_row = Adw.ComboRow(
            title="Page Order",
            subtitle="The page order when documents with layouts that expand in horizontal and vertical directions are cut into pages"
        )
        self.page_order_model = Gtk.StringList.new(["Horizontal First", "Vertical First"])
        self.page_order_row.set_model(self.page_order_model)
        self.page_order_row.set_selected(0)
        # Only relevant for unbounded/continuous canvases
        is_canvas = bool(self.doc and self.doc.get("format") == "xcanvas")
        self.page_order_row.set_sensitive(is_canvas)
        prefs_group.add(self.page_order_row)

        # Format Notice Banner (informational alert for Rnote or specific formats)
        self.notice_banner = Adw.Banner(
            title="Exporting to .rnote preserves multi-page layouts, background rulings, colors, vector strokes, and images.",
            revealed=False
        )
        self.notice_banner.set_use_markup(False)
        prefs_group.add(self.notice_banner)

        right_vbox.append(prefs_group)
        right_scroll.set_child(right_vbox)
        main_box.append(right_scroll)

        toolbar_view.set_content(main_box)
        self.set_child(toolbar_view)

    def _get_current_format(self):
        sel_idx = self.format_row.get_selected()
        if 0 <= sel_idx < len(EXPORT_FORMATS):
            return EXPORT_FORMATS[sel_idx]
        return EXPORT_FORMATS[0]

    def _on_format_changed(self, *_):
        fmt = self._get_current_format()
        new_ext = fmt["ext"]

        # Show warning banner if Rnote format is selected
        self.notice_banner.set_revealed(fmt["id"] == "rnote")

        # Update destination path extension
        if self.export_path:
            cur_root, _ = os.path.splitext(self.export_path)
            self.export_path = cur_root + new_ext
            self.file_label.set_label(os.path.basename(self.export_path))

    def _on_preference_changed(self, *_):
        self._update_preview()

    def _on_prev_page_clicked(self, *_):
        if self.current_page_idx > 0:
            self.current_page_idx -= 1
            self.page_label.set_label(f"Page {self.current_page_idx + 1} of {self.total_pages}")
            self._update_preview()

    def _on_next_page_clicked(self, *_):
        if self.current_page_idx < self.total_pages - 1:
            self.current_page_idx += 1
            self.page_label.set_label(f"Page {self.current_page_idx + 1} of {self.total_pages}")
            self._update_preview()

    def _update_preview(self):
        """Renders an ImageSurface preview for the current page and options."""
        w_px = max(240, self.preview_area.get_width() or 300)
        h_px = max(300, self.preview_area.get_height() or 390)

        with_bg = self.with_bg_row.get_active()
        with_pattern = self.with_pattern_row.get_active()
        opt_print = self.optimize_print_row.get_active()

        if self.current_file_type in ("md", "markdown"):
            # Markdown preview
            surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, w_px, h_px)
            cr = cairo.Context(surf)
            cr.set_source_rgb(1.0, 1.0, 1.0)
            cr.paint()
            cr.set_source_rgb(0.2, 0.2, 0.2)
            cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
            cr.set_font_size(14)
            cr.move_to(20, 36)
            cr.show_text(f"Markdown Document ({self.base_name})")
            cr.move_to(20, 60)
            cr.set_font_size(11)
            cr.show_text("Preview rendered for export.")
            self._preview_surface = surf
            self.preview_area.queue_draw()
            return

        if not self.canvas or not self.doc:
            self._preview_surface = None
            self.preview_area.queue_draw()
            return

        pages = self.doc.get("pages", [])
        if not pages:
            self._preview_surface = None
            self.preview_area.queue_draw()
            return

        p_idx = min(self.current_page_idx, len(pages) - 1)
        page = pages[p_idx]
        pw, ph = self.canvas.get_page_size(p_idx) if hasattr(self.canvas, "get_page_size") else (1240.157, 1753.937)
        if pw <= 0 or ph <= 0:
            pw, ph = 1240.157, 1753.937

        surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, w_px, h_px)
        cr = cairo.Context(surf)

        # Calculate fitting scale preserving aspect ratio
        scale = min((w_px - 20) / pw, (h_px - 20) / ph)
        target_pw = pw * scale
        target_ph = ph * scale
        off_x = (w_px - target_pw) / 2.0
        off_y = (h_px - target_ph) / 2.0

        cr.save()
        cr.translate(off_x, off_y)
        cr.scale(scale, scale)

        # Clip to page boundary
        cr.rectangle(0.0, 0.0, pw, ph)
        cr.clip()

        # Render background and items
        self.canvas.render_page_contents(
            cr,
            self.doc,
            page,
            pw,
            ph,
            getattr(self.canvas, "assets", {}),
            getattr(self.canvas, "templates", {}),
            page_idx=p_idx,
            with_background=with_bg,
            with_pattern=with_pattern,
            optimize_for_printing=opt_print,
        )
        cr.restore()

        self._preview_surface = surf
        self.preview_area.queue_draw()

    def _on_draw_preview(self, area, cr, width, height):
        # Draw background container fill
        cr.set_source_rgba(0.95, 0.95, 0.96, 1.0)
        cr.paint()

        if self._preview_surface is not None:
            cr.set_source_surface(self._preview_surface, 0, 0)
            cr.paint()

        # Subtle inner border
        cr.set_source_rgba(0.0, 0.0, 0.0, 0.12)
        cr.set_line_width(1.0)
        cr.rectangle(0.5, 0.5, width - 1.0, height - 1.0)
        cr.stroke()

    def _on_choose_destination_clicked(self, *_):
        if not hasattr(Gtk, "FileDialog"):
            return

        fmt = self._get_current_format()
        dialog = Gtk.FileDialog(title=f"Export as {fmt['name']}")
        dialog.set_initial_name(os.path.basename(self.export_path))

        filters = Gio.ListStore.new(Gtk.FileFilter)
        target_filter = Gtk.FileFilter()
        target_filter.set_name(fmt["description"])
        target_filter.add_pattern(f"*{fmt['ext']}")
        filters.append(target_filter)
        dialog.set_filters(filters)

        if self.initial_folder and os.path.isdir(self.initial_folder):
            dialog.set_initial_folder(Gio.File.new_for_path(self.initial_folder))

        dialog.save(self.parent_window, None, self._on_dest_selected)

    def _on_dest_selected(self, dialog, result):
        try:
            gfile = dialog.save_finish(result)
            if gfile and gfile.get_path():
                fmt = self._get_current_format()
                path = gfile.get_path()
                if not path.lower().endswith(fmt["ext"]):
                    path += fmt["ext"]
                self.export_path = path
                self.file_label.set_label(os.path.basename(path))
        except GLib.Error:
            pass

    def _on_export_clicked(self, *_):
        if not self.export_path:
            self._on_choose_destination_clicked()
            return

        fmt = self._get_current_format()
        dest_path = self.export_path
        if not dest_path.lower().endswith(fmt["ext"]):
            dest_path += fmt["ext"]

        with_bg = self.with_bg_row.get_active()
        with_pattern = self.with_pattern_row.get_active()
        opt_print = self.optimize_print_row.get_active()

        try:
            if fmt["id"] == "pdf":
                if self.current_file_type in ("md", "markdown"):
                    content = getattr(self.parent_window, "markdown_content", "")
                    if hasattr(self.parent_window, "md_buffer"):
                        start, end = self.parent_window.md_buffer.get_bounds()
                        content = self.parent_window.md_buffer.get_text(start, end, False)
                    MarkdownRenderer.export_to_pdf(dest_path, content)
                else:
                    XNotePackageParser.export_to_pdf(
                        dest_path,
                        self.doc,
                        self.canvas,
                        with_background=with_bg,
                        with_pattern=with_pattern,
                        optimize_for_printing=opt_print,
                    )

            elif fmt["id"] == "rnote":
                from rnote.convert_rnote import convert_xnote_to_rnote
                if self.current_file_type in ("md", "markdown"):
                    content = getattr(self.parent_window, "markdown_content", "")
                    if hasattr(self.parent_window, "md_buffer"):
                        start, end = self.parent_window.md_buffer.get_bounds()
                        content = self.parent_window.md_buffer.get_text(start, end, False)
                    doc = {
                        "format": "xnote",
                        "pages": [{
                            "width": 1240.157,
                            "height": 1753.937,
                            "items": [{
                                "kind": "text",
                                "text": content or "",
                                "pos": [60.0, 60.0],
                                "width": 800.0,
                                "font_size": 16.0,
                                "font_family": "Monospace",
                            }],
                        }],
                    }
                    convert_xnote_to_rnote(doc, dest_path)
                else:
                    convert_xnote_to_rnote(self.doc, dest_path, canvas=self.canvas)

            elif fmt["id"] == "png":
                # Export as high-resolution PNG
                pages = self.doc.get("pages", [])
                p_idx = min(self.current_page_idx, len(pages) - 1) if pages else 0
                page = pages[p_idx] if pages else {}
                pw, ph = self.canvas.get_page_size(p_idx) if hasattr(self.canvas, "get_page_size") else (1240.157, 1753.937)
                surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, int(pw), int(ph))
                cr = cairo.Context(surf)
                self.canvas.render_page_contents(
                    cr,
                    self.doc,
                    page,
                    pw,
                    ph,
                    getattr(self.canvas, "assets", {}),
                    getattr(self.canvas, "templates", {}),
                    page_idx=p_idx,
                    with_background=with_bg,
                    with_pattern=with_pattern,
                    optimize_for_printing=opt_print,
                )
                surf.write_to_png(dest_path)

            elif fmt["id"] == "svg":
                # Export page as SVG
                pages = self.doc.get("pages", [])
                p_idx = min(self.current_page_idx, len(pages) - 1) if pages else 0
                page = pages[p_idx] if pages else {}
                pw, ph = self.canvas.get_page_size(p_idx) if hasattr(self.canvas, "get_page_size") else (1240.157, 1753.937)
                dpi = float(self.doc.get("dpi", 150.0))
                px_to_pt = 72.0 / dpi
                svg_surf = cairo.SVGSurface(dest_path, pw * px_to_pt, ph * px_to_pt)
                cr = cairo.Context(svg_surf)
                cr.scale(px_to_pt, px_to_pt)
                self.canvas.render_page_contents(
                    cr,
                    self.doc,
                    page,
                    pw,
                    ph,
                    getattr(self.canvas, "assets", {}),
                    getattr(self.canvas, "templates", {}),
                    page_idx=p_idx,
                    with_background=with_bg,
                    with_pattern=with_pattern,
                    optimize_for_printing=opt_print,
                )
                svg_surf.finish()

            if hasattr(self.parent_window, "show_toast"):
                self.parent_window.show_toast(f"Exported {fmt['name']} to {os.path.basename(dest_path)}")

            if self.on_export_done_cb:
                self.on_export_done_cb(dest_path, fmt["id"])

            self._dismiss()

        except Exception as e:
            if hasattr(self.parent_window, "show_error"):
                self.parent_window.show_error(f"Failed to export {fmt['name']}: {e}")
