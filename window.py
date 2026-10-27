import os
import math
import copy
import json
import datetime
from concurrent.futures import ThreadPoolExecutor
import cairo
import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Pango', '1.0')
from gi.repository import Gtk, Adw, Gio, GLib, Gdk, Pango

from xnote_io import XNotePackageParser, DEFAULT_RULED_TEMPLATE_ID, DEFAULT_RULED_TEMPLATE
from canvas_editor import XNoteCanvas
from markdown_renderer import MarkdownRenderer

SUPPORTED_EXTENSIONS = ('.xnote', '.xcanvas', '.xtemplate', '.md', '.markdown')

PAPER_COLOR_PRESETS = [
    ("Pure White", [255, 255, 255, 255]),
    ("Warm Cream", [253, 249, 238, 255]),
    ("Soft Sepia", [244, 236, 216, 255]),
    ("Sage Green", [235, 243, 237, 255]),
    ("Pastel Blue", [235, 243, 250, 255]),
    ("Dark Slate", [34, 37, 42, 255]),
]


class XNotesViewerWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Notebooks Editor")
        self.set_default_size(1240, 820)

        # Register ./icons/svg/ with the display's Gtk.IconTheme
        self.icons_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icons", "svg")
        self.icon_theme = Gtk.IconTheme.get_for_display(Gdk.Display.get_default())
        if os.path.isdir(self.icons_dir):
            self.icon_theme.add_search_path(self.icons_dir)

        self.current_file_path = None
        self.current_file_type = None  # "xnote", "xcanvas", "xtemplate", or "md"
        self.markdown_content = ""
        self.md_view_mode = "split"  # "preview", "split", or "edit"
        self._loading_md_buffer = False
        self.current_root_folder = None
        self.active_notebook_filter = "ALL"
        self.notebooks_data = {}

        # Library display & sort state
        self.library_view_mode = "grid"   # "grid" or "list"
        self.library_sort_mode = "name_asc"  # "name_asc", "name_desc", "date_desc", "date_asc", "notebook", "type"

        self.is_modified = False
        self._force_closing = False
        self._updating_scroll = False
        self._updating_template_ui = False
        self._updating_page_sidebar = False
        self._page_thumb_draw_areas = []

        self.executor = ThreadPoolExecutor(max_workers=4)
        self._folder_load_id = 0

        self.connect("close-request", self._on_window_close_request)

        css_provider = Gtk.CssProvider()
        css_provider.load_from_data(b"""
            .page-badge {
                padding: 6px 14px;
                border-radius: 999px;
            }
            .sidebar-section-header {
                font-size: 11px;
                font-weight: 700;
                opacity: 0.55;
                margin-top: 12px;
                margin-bottom: 4px;
                margin-left: 12px;
                margin-right: 12px;
            }
            .note-card {
                padding: 4px;
                border-radius: 12px;
            }
            .cover-btn {
                padding: 0;
                border-radius: 12px;
                box-shadow: 0 3px 10px rgba(0, 0, 0, 0.12);
                transition: transform 150ms ease, box-shadow 150ms ease;
            }
            .cover-btn:hover {
                box-shadow: 0 6px 16px rgba(0, 0, 0, 0.18);
            }
            .card-title {
                font-weight: 700;
                font-size: 13px;
            }
            .card-subtitle {
                font-size: 12px;
                opacity: 0.55;
            }
            .card-more-btn {
                min-height: 24px;
                min-width: 24px;
                padding: 0;
                opacity: 0.65;
            }
            .card-more-btn:hover {
                opacity: 1.0;
            }
            .library-list-row {
                padding: 8px 12px;
                border-radius: 10px;
                margin-bottom: 4px;
            }
            .type-pill {
                font-size: 10px;
                font-weight: 700;
                padding: 2px 8px;
                border-radius: 999px;
                background-color: alpha(@view_fg_color, 0.08);
                opacity: 0.75;
            }
            .stylus-toolbar {
                padding: 6px 12px;
                background-color: alpha(@headerbar_bg_color, 0.95);
                border-bottom: 1px solid alpha(@borders, 0.5);
            }
            .page-sidebar-box {
                background-color: @sidebar_bg_color;
                border-right: 1px solid alpha(@borders, 0.55);
            }
            .page-sidebar-footer {
                padding: 6px 8px;
                border-top: 1px solid alpha(@borders, 0.45);
            }
            .page-thumb-row {
                padding: 8px 6px;
                border-radius: 10px;
                margin: 4px 6px;
            }
            .page-mini-btn {
                min-height: 24px;
                min-width: 24px;
                padding: 2px;
            }
            .template-popover-box {
                padding: 8px;
            }
            .paper-swatch-btn {
                min-width: 26px;
                min-height: 26px;
                padding: 2px;
                border-radius: 999px;
            }
            .md-paper {
                background-color: #ffffff;
                color: #1e1e1e;
                border-radius: 12px;
                padding: 36px 44px;
                border: 1px solid rgba(0, 0, 0, 0.14);
            }
            .md-editor-view {
                font-family: monospace;
                font-size: 13.5px;
                padding: 20px;
                background-color: @view_bg_color;
                color: @view_fg_color;
            }
            .md-code-block {
                background-color: #f4f4f6;
                color: #1e1e1e;
                border-radius: 8px;
                padding: 12px 16px;
                border: 1px solid rgba(0, 0, 0, 0.08);
            }
            .md-blockquote {
                border-left: 4px solid #3584e4;
                padding-left: 12px;
                background-color: rgba(53, 132, 228, 0.06);
                border-radius: 0 6px 6px 0;
                padding-top: 6px;
                padding-bottom: 6px;
            }
        """)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            css_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        self.toast_overlay = Adw.ToastOverlay()
        self.set_content(self.toast_overlay)

        self.root_stack = Gtk.Stack()
        self.root_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.toast_overlay.set_child(self.root_stack)

        # =====================================================================
        # 1. COLLECTION VIEW
        # =====================================================================
        sidebar_toolbar = Adw.ToolbarView()
        sidebar_header = Adw.HeaderBar()
        sidebar_header.set_show_end_title_buttons(False)
        sidebar_title = Adw.WindowTitle(title="Categories", subtitle="")
        sidebar_header.set_title_widget(sidebar_title)

        sidebar_menu = Gio.Menu()
        sidebar_menu.append("New Category…", "win.new-category")
        sidebar_menu.append("New .xnote Notebook…", "win.new-notebook")
        sidebar_menu.append("New .xnote from PDF…", "win.import-pdf-new")
        sidebar_menu.append("New Markdown Note…", "win.new-markdown")
        sidebar_menu.append("Open Collection Folder…", "win.open")
        sidebar_menu.append("Quit", "app.quit")
        sidebar_menu_btn = Gtk.MenuButton(
            icon_name="open-menu-symbolic",
            menu_model=sidebar_menu,
            tooltip_text="Main Menu"
        )
        sidebar_header.pack_end(sidebar_menu_btn)
        sidebar_toolbar.add_top_bar(sidebar_header)

        sidebar_scrolled = Gtk.ScrolledWindow(
            vexpand=True,
            hscrollbar_policy=Gtk.PolicyType.NEVER
        )
        self.notebook_listbox = Gtk.ListBox()
        self.notebook_listbox.add_css_class("navigation-sidebar")
        self.notebook_listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.notebook_listbox.set_header_func(self._sidebar_header_func)
        self.notebook_listbox.connect("row-selected", self.on_notebook_row_selected)
        sidebar_scrolled.set_child(self.notebook_listbox)
        sidebar_toolbar.set_content(sidebar_scrolled)

        content_toolbar = Adw.ToolbarView()
        content_header = Adw.HeaderBar()
        content_header.set_show_start_title_buttons(False)
        self.collection_title = Adw.WindowTitle(title="Library", subtitle="")
        content_header.set_title_widget(self.collection_title)

        self.open_btn = Gtk.Button(
            icon_name="folder-open-symbolic",
            tooltip_text="Open Collection Folder (Ctrl+O)"
        )
        self.open_btn.set_action_name("win.open")
        content_header.pack_start(self.open_btn)

        new_menu = Gio.Menu()
        new_menu.append("New .xnote Notebook (Ctrl+N)", "win.new-notebook")
        new_menu.append("New .xnote from PDF…", "win.import-pdf-new")
        new_menu.append("New Markdown Note (.md)", "win.new-markdown")
        new_menu.append("New Category Folder…", "win.new-category")
        self.new_note_btn = Gtk.MenuButton(
            icon_name="document-new-symbolic",
            menu_model=new_menu,
            tooltip_text="Create New Notebook, Import PDF, Markdown Note, or Category"
        )
        content_header.pack_start(self.new_note_btn)

        self.search_btn = Gtk.ToggleButton(
            icon_name="system-search-symbolic",
            tooltip_text="Filter Notes (Ctrl+F)"
        )
        self.search_btn.connect("toggled", self.on_search_toggled)
        content_header.pack_end(self.search_btn)

        sort_menu = Gio.Menu()
        sort_menu.append("Name (A → Z)", "win.sort-library('name_asc')")
        sort_menu.append("Name (Z → A)", "win.sort-library('name_desc')")
        sort_menu.append("Date Modified (Newest)", "win.sort-library('date_desc')")
        sort_menu.append("Date Modified (Oldest)", "win.sort-library('date_asc')")
        sort_menu.append("Category Folder", "win.sort-library('notebook')")
        sort_menu.append("File Type (.xnote, .md)", "win.sort-library('type')")
        self.sort_menu_btn = Gtk.MenuButton(
            icon_name=self._resolve_icon_name("view-sort-descending-symbolic", "content-loading-symbolic"),
            menu_model=sort_menu,
            tooltip_text="Sort Library Notes"
        )
        content_header.pack_end(self.sort_menu_btn)

        lib_mode_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        lib_mode_box.add_css_class("linked")

        self.lib_grid_btn = Gtk.ToggleButton(
            icon_name="view-grid-symbolic",
            active=True,
            tooltip_text="Grid View"
        )
        self.lib_grid_btn.connect("toggled", self._on_library_view_mode_toggled, "grid")
        lib_mode_box.append(self.lib_grid_btn)

        self.lib_list_btn = Gtk.ToggleButton(
            icon_name="view-list-symbolic",
            group=self.lib_grid_btn,
            tooltip_text="List View"
        )
        self.lib_list_btn.connect("toggled", self._on_library_view_mode_toggled, "list")
        lib_mode_box.append(self.lib_list_btn)

        content_header.pack_end(lib_mode_box)

        content_toolbar.add_top_bar(content_header)

        self.search_bar = Gtk.SearchBar()
        self.search_entry = Gtk.SearchEntry(
            placeholder_text="Search notes…",
            hexpand=True
        )
        self.search_entry.connect("search-changed", self.on_search_changed)
        self.search_bar.set_child(self.search_entry)
        self.search_bar.connect_entry(self.search_entry)
        content_toolbar.add_top_bar(self.search_bar)

        self.content_stack = Gtk.Stack()
        self.content_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        content_toolbar.set_content(self.content_stack)

        status_page = Adw.StatusPage(
            icon_name="folder-documents-symbolic",
            title="No Notebook Collection Open",
            description="Open a folder containing your notebooks (.xnote, .xcanvas, .xtemplate, and .md files)"
        )
        status_btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12, halign=Gtk.Align.CENTER)
        status_open_btn = Gtk.Button(label="Open Collection Folder")
        status_open_btn.add_css_class("pill")
        status_open_btn.add_css_class("suggested-action")
        status_open_btn.set_action_name("win.open")
        status_btn_box.append(status_open_btn)

        status_new_btn = Gtk.Button(label="New .xnote Notebook")
        status_new_btn.add_css_class("pill")
        status_new_btn.set_action_name("win.new-notebook")
        status_btn_box.append(status_new_btn)

        status_pdf_btn = Gtk.Button(label="Import PDF as .xnote")
        status_pdf_btn.add_css_class("pill")
        status_pdf_btn.set_action_name("win.import-pdf-new")
        status_btn_box.append(status_pdf_btn)

        status_page.set_child(status_btn_box)
        self.content_stack.add_named(status_page, "empty")

        # 1a. Library Cover Grid View
        self.grid_scrolled = Gtk.ScrolledWindow(hexpand=True, vexpand=True)
        self.library_flowbox = Gtk.FlowBox(
            valign=Gtk.Align.START,
            halign=Gtk.Align.START,
            column_spacing=24,
            row_spacing=24,
            margin_start=28,
            margin_end=28,
            margin_top=24,
            margin_bottom=28,
            homogeneous=False,
            selection_mode=Gtk.SelectionMode.NONE
        )
        self.library_flowbox.set_sort_func(self._flowbox_sort_func)
        self.grid_scrolled.set_child(self.library_flowbox)
        self.content_stack.add_named(self.grid_scrolled, "grid")

        # 1b. Library Detailed List View
        self.list_scrolled = Gtk.ScrolledWindow(hexpand=True, vexpand=True)
        list_outer = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            margin_start=28,
            margin_end=28,
            margin_top=20,
            margin_bottom=28
        )
        self.library_listbox = Gtk.ListBox()
        self.library_listbox.add_css_class("boxed-list")
        self.library_listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        self.library_listbox.set_activate_on_single_click(True)
        self.library_listbox.set_sort_func(self._listbox_sort_func)
        self.library_listbox.connect("row-activated", self._on_library_list_row_activated)
        list_outer.append(self.library_listbox)
        self.list_scrolled.set_child(list_outer)
        self.content_stack.add_named(self.list_scrolled, "list")

        self.content_stack.set_visible_child_name("empty")

        if hasattr(Adw, "NavigationSplitView"):
            self.split_view = Adw.NavigationSplitView()
            self.split_view.set_min_sidebar_width(200)
            self.split_view.set_max_sidebar_width(250)
            self.split_view.set_sidebar_width_fraction(0.22)
            self.split_view.set_sidebar(
                Adw.NavigationPage.new(sidebar_toolbar, "Categories")
            )
            self.split_view.set_content(
                Adw.NavigationPage.new(content_toolbar, "Library")
            )
        elif hasattr(Adw, "OverlaySplitView"):
            self.split_view = Adw.OverlaySplitView()
            self.split_view.set_min_sidebar_width(200)
            self.split_view.set_max_sidebar_width(250)
            self.split_view.set_sidebar_width_fraction(0.22)
            self.split_view.set_sidebar(sidebar_toolbar)
            self.split_view.set_content(content_toolbar)
        else:
            self.split_view = Adw.Flap()
            sidebar_toolbar.set_size_request(220, -1)
            self.split_view.set_flap(sidebar_toolbar)
            self.split_view.set_content(content_toolbar)

        self.root_stack.add_named(self.split_view, "collection")

        # =====================================================================
        # 2. DOCUMENT VIEWER & EDITOR VIEW (.xnote + Markdown)
        # =====================================================================
        viewer_toolbar = Adw.ToolbarView()
        viewer_header = Adw.HeaderBar()
        self.window_title = Adw.WindowTitle(title="Notebooks", subtitle="")
        viewer_header.set_title_widget(self.window_title)
        viewer_toolbar.add_top_bar(viewer_header)

        self.back_btn = Gtk.Button(
            icon_name="go-previous-symbolic",
            tooltip_text="Back to Library (Esc)"
        )
        self.back_btn.connect("clicked", self.on_back_clicked)
        viewer_header.pack_start(self.back_btn)

        # Page Management Sidebar Toggle Button (Hidden by default)
        self.page_sidebar_btn = Gtk.ToggleButton(
            icon_name="sidebar-show-symbolic",
            active=False,
            tooltip_text="Toggle Page Thumbnails Sidebar (F9)"
        )
        self.page_sidebar_btn.connect("toggled", self._on_page_sidebar_toggled)
        viewer_header.pack_start(self.page_sidebar_btn)

        self.save_btn = Gtk.Button(
            icon_name="document-save-symbolic",
            tooltip_text="Save Changes (Ctrl+S)"
        )
        self.save_btn.add_css_class("suggested-action")
        self.save_btn.set_sensitive(False)
        self.save_btn.connect("clicked", self.on_save_clicked)
        viewer_header.pack_start(self.save_btn)

        self.history_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self.history_box.add_css_class("linked")
        self.undo_btn = Gtk.Button(
            icon_name="edit-undo-symbolic",
            tooltip_text="Undo (Ctrl+Z)",
            sensitive=False
        )
        self.undo_btn.connect("clicked", lambda *_: self.canvas.undo())
        self.history_box.append(self.undo_btn)

        self.redo_btn = Gtk.Button(
            icon_name="edit-redo-symbolic",
            tooltip_text="Redo (Ctrl+Y)",
            sensitive=False
        )
        self.redo_btn.connect("clicked", lambda *_: self.canvas.redo())
        self.history_box.append(self.redo_btn)
        viewer_header.pack_start(self.history_box)

        # External App & Export PDF Box
        self.ext_open_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self.ext_open_box.add_css_class("linked")

        self.export_pdf_btn = Gtk.Button(
            icon_name="document-send-symbolic",
            tooltip_text="Export Notebook to PDF…"
        )
        self.export_pdf_btn.connect("clicked", self.on_export_pdf_clicked)
        self.ext_open_box.append(self.export_pdf_btn)

        self.open_default_btn = Gtk.Button(
            icon_name="external-link-symbolic",
            tooltip_text="Open with System Default Application"
        )
        self.open_default_btn.connect(
            "clicked", lambda *_: self.open_with_default_app(self.current_file_path)
        )
        self.ext_open_box.append(self.open_default_btn)

        self.open_with_btn = Gtk.Button(
            icon_name="applications-other-symbolic",
            tooltip_text="Select Application to Open With…"
        )
        self.open_with_btn.connect(
            "clicked", lambda *_: self.choose_app_for_file(self.current_file_path)
        )
        self.ext_open_box.append(self.open_with_btn)
        viewer_header.pack_end(self.ext_open_box)

        # Zoom controls + Fit Page Width button
        self.zoom_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self.zoom_box.add_css_class("linked")

        zoom_out_btn = Gtk.Button(
            icon_name="zoom-out-symbolic",
            tooltip_text="Zoom Out (Ctrl+-)"
        )
        zoom_out_btn.connect("clicked", lambda *_: self.set_zoom(self.canvas.zoom_level - 0.25))
        self.zoom_box.append(zoom_out_btn)

        self.zoom_reset_btn = Gtk.Button(
            label="100%",
            tooltip_text="Reset Zoom (Ctrl+0)"
        )
        self.zoom_reset_btn.connect("clicked", lambda *_: self.set_zoom(1.0))
        self.zoom_box.append(self.zoom_reset_btn)

        zoom_in_btn = Gtk.Button(
            icon_name="zoom-in-symbolic",
            tooltip_text="Zoom In (Ctrl++)"
        )
        zoom_in_btn.connect("clicked", lambda *_: self.set_zoom(self.canvas.zoom_level + 0.25))
        self.zoom_box.append(zoom_in_btn)

        self.fit_width_btn = Gtk.Button(
            icon_name="zoom-fit-best-symbolic",
            tooltip_text="Fit Page Width"
        )
        self.fit_width_btn.connect("clicked", self.on_fit_width_clicked)
        self.zoom_box.append(self.fit_width_btn)

        viewer_header.pack_end(self.zoom_box)

        # View Mode toggle controls (Paged vs Scrolled for .xnote)
        self.mode_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self.mode_box.add_css_class("linked")

        self.paged_btn = Gtk.ToggleButton(
            icon_name="view-paged-symbolic",
            tooltip_text="Paged View (Ctrl+M)"
        )
        self.paged_btn.set_active(True)
        self.paged_btn.connect("toggled", self.on_view_mode_toggled, "paged")
        self.mode_box.append(self.paged_btn)

        self.scrolled_btn = Gtk.ToggleButton(
            icon_name="view-continuous-symbolic",
            tooltip_text="Scrolled View (Ctrl+M)",
            group=self.paged_btn
        )
        self.scrolled_btn.connect("toggled", self.on_view_mode_toggled, "scrolled")
        self.mode_box.append(self.scrolled_btn)
        viewer_header.pack_end(self.mode_box)

        # =====================================================================
        # 2a. STYLUS BAR USING DEDICATED tool-* ICONS IN ./icons/svg/
        # =====================================================================
        self.stylus_bar = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=10,
            halign=Gtk.Align.FILL
        )
        self.stylus_bar.add_css_class("stylus-toolbar")

        tool_group_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        tool_group_box.add_css_class("linked")

        self.select_tool_btn = Gtk.ToggleButton(
            icon_name=self._resolve_icon_name("tool-pointer-symbolic", "input-mouse-symbolic"),
            tooltip_text="Select, Move, Resize & Rotate Images"
        )
        self.select_tool_btn.connect("toggled", self._on_tool_toggled, "select")
        tool_group_box.append(self.select_tool_btn)

        self.lasso_tool_btn = Gtk.ToggleButton(
            icon_name=self._resolve_icon_name("tool-lasso-symbolic", "edit-select-all-symbolic"),
            group=self.select_tool_btn,
            tooltip_text="Freehand Lasso Select (Move, Scale & Recolor Strokes)"
        )
        self.lasso_tool_btn.connect("toggled", self._on_tool_toggled, "lasso")
        tool_group_box.append(self.lasso_tool_btn)

        self.pen_tool_btn = Gtk.ToggleButton(
            icon_name=self._resolve_icon_name("tool-pen-symbolic", "document-edit-symbolic"),
            group=self.select_tool_btn,
            active=True,
            tooltip_text="Standard Pressure Pen"
        )
        self.pen_tool_btn.connect("toggled", self._on_tool_toggled, "pen")
        tool_group_box.append(self.pen_tool_btn)

        self.dashed_tool_btn = Gtk.ToggleButton(
            icon_name=self._resolve_icon_name("tool-dashed-symbolic", "view-more-horizontal-symbolic"),
            group=self.select_tool_btn,
            tooltip_text="Dashed Line Pen"
        )
        self.dashed_tool_btn.connect("toggled", self._on_tool_toggled, "dashed")
        tool_group_box.append(self.dashed_tool_btn)

        self.callig_tool_btn = Gtk.ToggleButton(
            icon_name=self._resolve_icon_name("tool-calligraphy-symbolic", "font-x-generic-symbolic"),
            group=self.select_tool_btn,
            tooltip_text="Calligraphic Chisel-Nib Pen (Pressure + Direction)"
        )
        self.callig_tool_btn.connect("toggled", self._on_tool_toggled, "calligraphy")
        tool_group_box.append(self.callig_tool_btn)

        self.vel_pen_tool_btn = Gtk.ToggleButton(
            icon_name=self._resolve_icon_name("tool-fountain-pen-symbolic", "applications-engineering-symbolic"),
            group=self.select_tool_btn,
            tooltip_text="Velocity / Speed Pen (Fast Strokes Taper Thin)"
        )
        self.vel_pen_tool_btn.connect("toggled", self._on_tool_toggled, "speed")
        tool_group_box.append(self.vel_pen_tool_btn)

        self.taper_tool_btn = Gtk.ToggleButton(
            icon_name=self._resolve_icon_name("tool-taper-symbolic", "go-last-symbolic"),
            group=self.select_tool_btn,
            tooltip_text="Tapered Brush Pen"
        )
        self.taper_tool_btn.connect("toggled", self._on_tool_toggled, "taper")
        tool_group_box.append(self.taper_tool_btn)

        self.hi_tool_btn = Gtk.ToggleButton(
            icon_name=self._resolve_icon_name("tool-highlighter-symbolic", "applications-graphics-symbolic"),
            group=self.select_tool_btn,
            tooltip_text="Translucent Highlighter"
        )
        self.hi_tool_btn.connect("toggled", self._on_tool_toggled, "highlighter")
        tool_group_box.append(self.hi_tool_btn)

        self.eraser_tool_btn = Gtk.ToggleButton(
            icon_name=self._resolve_icon_name("tool-eraser-symbolic", "edit-clear-symbolic"),
            group=self.select_tool_btn,
            tooltip_text="Stroke & Text Eraser (or use Stylus Eraser Tip)"
        )
        self.eraser_tool_btn.connect("toggled", self._on_tool_toggled, "eraser")
        tool_group_box.append(self.eraser_tool_btn)

        self.text_tool_btn = Gtk.ToggleButton(
            icon_name=self._resolve_icon_name("tool-text-symbolic", "insert-text-symbolic"),
            group=self.select_tool_btn,
            tooltip_text="Insert Text Box on Page"
        )
        self.text_tool_btn.connect("toggled", self._on_tool_toggled, "text")
        tool_group_box.append(self.text_tool_btn)

        self.stylus_bar.append(tool_group_box)

        insert_media_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        insert_media_box.add_css_class("linked")

        self.insert_img_btn = Gtk.Button(
            icon_name="insert-image-symbolic",
            tooltip_text="Import Image onto Current Page…"
        )
        self.insert_img_btn.connect("clicked", self.on_insert_image_clicked)
        insert_media_box.append(self.insert_img_btn)

        self.import_pdf_btn = Gtk.Button(
            icon_name="x-office-document-symbolic",
            tooltip_text="Import PDF Pages into Notebook…"
        )
        self.import_pdf_btn.connect("clicked", self.on_import_pdf_into_current_clicked)
        insert_media_box.append(self.import_pdf_btn)

        self.stylus_bar.append(insert_media_box)

        self.color_btn = Gtk.ColorButton(
            tooltip_text="Stroke / Text Ink Color (Also Recolors Lasso Selection)"
        )
        rgba = Gdk.RGBA()
        rgba.parse("rgb(213,68,37)")
        self.color_btn.set_rgba(rgba)
        self.color_btn.connect("color-set", self._on_color_changed)
        self.stylus_bar.append(self.color_btn)

        size_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        size_icon = Gtk.Image.new_from_icon_name(
            self._resolve_icon_name("stroke-size-symbolic", "media-record-symbolic")
        )
        size_icon.set_tooltip_text("Stroke Thickness / Eraser Size")
        size_box.append(size_icon)

        self.width_scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 1.0, 32.0, 0.5)
        self.width_scale.set_value(9.3)
        self.width_scale.set_size_request(115, -1)
        self.width_scale.set_draw_value(False)
        self.width_scale.set_tooltip_text("Adjust Tool Thickness (Also Resizes Lasso-Selected Strokes)")
        self.width_scale.connect("value-changed", self._on_width_changed)
        size_box.append(self.width_scale)
        self.stylus_bar.append(size_box)

        # Contextual Image / Lasso Manipulation Action Group
        self.img_ops_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0, visible=False)
        self.img_ops_box.add_css_class("linked")

        self.aspect_lock_btn = Gtk.ToggleButton(
            icon_name="view-pin-symbolic",
            active=True,
            tooltip_text="Lock Aspect Ratio When Resizing (Hold Shift to Override)"
        )
        self.aspect_lock_btn.connect("toggled", self._on_aspect_lock_toggled)
        self.img_ops_box.append(self.aspect_lock_btn)

        self.reset_aspect_btn = Gtk.Button(
            icon_name="view-restore-symbolic",
            tooltip_text="Reset Image to Native Proportions"
        )
        self.reset_aspect_btn.connect("clicked", lambda *_: self.canvas.reset_selected_image_aspect_ratio())
        self.img_ops_box.append(self.reset_aspect_btn)

        self.rot_l_btn = Gtk.Button(
            icon_name="object-rotate-left-symbolic",
            tooltip_text="Rotate Image 90° Counter-Clockwise"
        )
        self.rot_l_btn.connect("clicked", lambda *_: self.canvas.rotate_selected_image(-math.pi / 2.0))
        self.img_ops_box.append(self.rot_l_btn)

        self.rot_r_btn = Gtk.Button(
            icon_name="object-rotate-right-symbolic",
            tooltip_text="Rotate Image 90° Clockwise"
        )
        self.rot_r_btn.connect("clicked", lambda *_: self.canvas.rotate_selected_image(math.pi / 2.0))
        self.img_ops_box.append(self.rot_r_btn)

        self.flip_h_btn = Gtk.Button(
            icon_name="object-flip-horizontal-symbolic",
            tooltip_text="Flip Image Horizontally"
        )
        self.flip_h_btn.connect("clicked", lambda *_: self.canvas.flip_selected_image(horizontal=True))
        self.img_ops_box.append(self.flip_h_btn)

        self.flip_v_btn = Gtk.Button(
            icon_name="object-flip-vertical-symbolic",
            tooltip_text="Flip Image Vertically"
        )
        self.flip_v_btn.connect("clicked", lambda *_: self.canvas.flip_selected_image(horizontal=False))
        self.img_ops_box.append(self.flip_v_btn)

        self.front_btn = Gtk.Button(
            icon_name="go-top-symbolic",
            tooltip_text="Bring Image to Front"
        )
        self.front_btn.connect("clicked", lambda *_: self.canvas.reorder_selected_image(to_front=True))
        self.img_ops_box.append(self.front_btn)

        self.back_layer_btn = Gtk.Button(
            icon_name="go-bottom-symbolic",
            tooltip_text="Send Image to Back"
        )
        self.back_layer_btn.connect("clicked", lambda *_: self.canvas.reorder_selected_image(to_front=False))
        self.img_ops_box.append(self.back_layer_btn)

        self.del_img_btn = Gtk.Button(
            icon_name="user-trash-symbolic",
            tooltip_text="Delete Selected Item(s)"
        )
        self.del_img_btn.add_css_class("destructive-action")
        self.del_img_btn.connect("clicked", lambda *_: self.canvas.delete_selected_item())
        self.img_ops_box.append(self.del_img_btn)

        self.stylus_bar.append(self.img_ops_box)
        self.stylus_bar.append(Gtk.Box(hexpand=True))

        # Linked Page Background Colour + Page Template Switcher Controls
        page_style_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)

        self.toolbar_page_color_btn = Gtk.ColorButton(
            tooltip_text="Page Background Colour"
        )
        default_page_rgba = Gdk.RGBA()
        default_page_rgba.parse("rgb(255,255,255)")
        self.toolbar_page_color_btn.set_rgba(default_page_rgba)
        self.toolbar_page_color_btn.connect("color-set", self._on_paper_color_changed)
        page_style_box.append(self.toolbar_page_color_btn)

        self.template_popover = self._build_template_switcher_popover()
        self.template_menu_btn = Gtk.MenuButton(
            icon_name="view-grid-symbolic",
            popover=self.template_popover,
            tooltip_text="Change Page Template, Paper Colour & Rule Spacing…"
        )
        page_style_box.append(self.template_menu_btn)

        self.stylus_bar.append(page_style_box)

        self.add_page_btn = Gtk.Button(
            icon_name="list-add-symbolic",
            tooltip_text="Add New Page After Last Page"
        )
        self.add_page_btn.connect("clicked", self.on_add_page_clicked)
        self.stylus_bar.append(self.add_page_btn)

        viewer_toolbar.add_top_bar(self.stylus_bar)

        # =====================================================================
        # 2b. MARKDOWN EDITING BAR WITH SYMBOLIC ICONS (.md / .markdown)
        # =====================================================================
        self.md_toolbar_bar = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=12,
            halign=Gtk.Align.FILL,
            visible=False
        )
        self.md_toolbar_bar.add_css_class("stylus-toolbar")

        md_mode_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        md_mode_box.add_css_class("linked")

        self.md_preview_btn = Gtk.ToggleButton(
            icon_name="view-reveal-symbolic",
            tooltip_text="Read-Only Formatted Preview"
        )
        self.md_preview_btn.connect("toggled", self._on_md_mode_toggled, "preview")
        md_mode_box.append(self.md_preview_btn)

        self.md_split_btn = Gtk.ToggleButton(
            icon_name="view-dual-symbolic",
            group=self.md_preview_btn,
            active=True,
            tooltip_text="Side-by-Side Live Editor & Preview"
        )
        self.md_split_btn.connect("toggled", self._on_md_mode_toggled, "split")
        md_mode_box.append(self.md_split_btn)

        self.md_edit_btn = Gtk.ToggleButton(
            icon_name="document-properties-symbolic",
            group=self.md_preview_btn,
            tooltip_text="Full-Width Markdown Source Editor"
        )
        self.md_edit_btn.connect("toggled", self._on_md_mode_toggled, "edit")
        md_mode_box.append(self.md_edit_btn)

        self.md_toolbar_bar.append(md_mode_box)

        fmt_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        fmt_box.add_css_class("linked")

        fmt_specs = [
            ("format-text-bold-symbolic", "Bold (**text**)", "**", "**"),
            ("format-text-italic-symbolic", "Italic (*text*)", "*", "*"),
            ("format-text-strikethrough-symbolic", "Strikethrough (~~text~~)", "~~", "~~"),
            ("code-symbolic", "Inline Code (`code`)", "`", "`"),
            ("format-text-larger-symbolic", "Heading 1 (# )", "# ", ""),
            ("format-indent-more-symbolic", "Heading 2 (## )", "## ", ""),
            ("view-list-bullet-symbolic", "Bullet List Item (- )", "- ", ""),
            ("object-select-symbolic", "Task Checkbox (- [ ] )", "- [ ] ", ""),
            ("format-text-direction-ltr-symbolic", "Blockquote (> )", "> ", ""),
        ]
        for icon_name, tip, prefix, suffix in fmt_specs:
            b = Gtk.Button(icon_name=icon_name, tooltip_text=tip)
            b.connect("clicked", self._on_md_format_clicked, prefix, suffix)
            fmt_box.append(b)

        self.md_toolbar_bar.append(fmt_box)
        viewer_toolbar.add_top_bar(self.md_toolbar_bar)

        # Document Viewer Stack
        self.viewer_stack = Gtk.Stack(hexpand=True, vexpand=True)
        viewer_toolbar.set_content(self.viewer_stack)

        # =====================================================================
        # 2c. CANVAS WORKSPACE WITH COLLAPSIBLE PAGE SIDEBAR (HIDDEN BY DEFAULT)
        # =====================================================================
        self.canvas_workspace_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True, vexpand=True)

        self.page_sidebar_revealer = Gtk.Revealer(
            transition_type=Gtk.RevealerTransitionType.SLIDE_RIGHT,
            reveal_child=False
        )
        sidebar_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, width_request=215, vexpand=True)
        sidebar_vbox.add_css_class("page-sidebar-box")

        page_sidebar_scrolled = Gtk.ScrolledWindow(
            vexpand=True,
            hscrollbar_policy=Gtk.PolicyType.NEVER
        )
        self.page_sidebar_listbox = Gtk.ListBox()
        self.page_sidebar_listbox.add_css_class("navigation-sidebar")
        self.page_sidebar_listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.page_sidebar_listbox.connect("row-selected", self._on_page_sidebar_row_selected)
        page_sidebar_scrolled.set_child(self.page_sidebar_listbox)
        sidebar_vbox.append(page_sidebar_scrolled)

        sidebar_footer = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4, halign=Gtk.Align.CENTER)
        sidebar_footer.add_css_class("page-sidebar-footer")

        self.sb_move_up_btn = Gtk.Button(
            icon_name="go-up-symbolic",
            tooltip_text="Move Current Page Up"
        )
        self.sb_move_up_btn.add_css_class("flat")
        self.sb_move_up_btn.connect("clicked", lambda *_: self.move_page(self.canvas.current_page, -1))
        sidebar_footer.append(self.sb_move_up_btn)

        self.sb_move_down_btn = Gtk.Button(
            icon_name="go-down-symbolic",
            tooltip_text="Move Current Page Down"
        )
        self.sb_move_down_btn.add_css_class("flat")
        self.sb_move_down_btn.connect("clicked", lambda *_: self.move_page(self.canvas.current_page, 1))
        sidebar_footer.append(self.sb_move_down_btn)

        self.sb_dup_btn = Gtk.Button(
            icon_name="edit-copy-symbolic",
            tooltip_text="Duplicate Current Page"
        )
        self.sb_dup_btn.add_css_class("flat")
        self.sb_dup_btn.connect("clicked", lambda *_: self.duplicate_page(self.canvas.current_page))
        sidebar_footer.append(self.sb_dup_btn)

        self.sb_add_btn = Gtk.Button(
            icon_name="list-add-symbolic",
            tooltip_text="Insert New Page After Current Page"
        )
        self.sb_add_btn.add_css_class("flat")
        self.sb_add_btn.connect("clicked", lambda *_: self.insert_page_after(self.canvas.current_page))
        sidebar_footer.append(self.sb_add_btn)

        self.sb_del_btn = Gtk.Button(
            icon_name="user-trash-symbolic",
            tooltip_text="Delete Current Page"
        )
        self.sb_del_btn.add_css_class("flat")
        self.sb_del_btn.add_css_class("destructive-action")
        self.sb_del_btn.connect("clicked", lambda *_: self.delete_page(self.canvas.current_page))
        sidebar_footer.append(self.sb_del_btn)

        sidebar_vbox.append(sidebar_footer)
        self.page_sidebar_revealer.set_child(sidebar_vbox)
        self.canvas_workspace_box.append(self.page_sidebar_revealer)

        self.viewer_overlay = Gtk.Overlay(hexpand=True, vexpand=True)

        self.scrolled = Gtk.ScrolledWindow(hexpand=True, vexpand=True)
        self.vadj = self.scrolled.get_vadjustment()
        self.vadj.connect("value-changed", self.on_scroll_changed)

        self.canvas = XNoteCanvas(
            on_modified_cb=self._on_canvas_modified,
            on_history_changed_cb=self._on_history_changed,
            on_selection_changed_cb=self._on_canvas_selection_changed
        )
        self.canvas.vadj = self.vadj

        self.scrolled.set_child(self.canvas)
        self.viewer_overlay.set_child(self.scrolled)

        self.prev_btn = Gtk.Button(
            icon_name="go-previous-symbolic",
            tooltip_text="Previous Page (Page Up)",
            halign=Gtk.Align.START,
            valign=Gtk.Align.CENTER,
            margin_start=18
        )
        self.prev_btn.add_css_class("osd")
        self.prev_btn.add_css_class("circular")
        self.prev_btn.set_visible(False)
        self.prev_btn.connect("clicked", self.on_prev_page)
        self.viewer_overlay.add_overlay(self.prev_btn)

        self.next_btn = Gtk.Button(
            icon_name="go-next-symbolic",
            tooltip_text="Next Page (Page Down)",
            halign=Gtk.Align.END,
            valign=Gtk.Align.CENTER,
            margin_end=18
        )
        self.next_btn.add_css_class("osd")
        self.next_btn.add_css_class("circular")
        self.next_btn.set_visible(False)
        self.next_btn.connect("clicked", self.on_next_page)
        self.viewer_overlay.add_overlay(self.next_btn)

        self.page_badge = Gtk.Box(
            halign=Gtk.Align.CENTER,
            valign=Gtk.Align.END,
            margin_bottom=16
        )
        self.page_badge.add_css_class("osd")
        self.page_badge.add_css_class("page-badge")
        self.page_badge.set_visible(False)

        self.page_label = Gtk.Label(label="1 / 1")
        self.page_label.add_css_class("numeric")
        self.page_badge.append(self.page_label)
        self.viewer_overlay.add_overlay(self.page_badge)

        self.canvas_workspace_box.append(self.viewer_overlay)
        self.viewer_stack.add_named(self.canvas_workspace_box, "canvas_doc")

        # 2d. Markdown Split Editor & Live Paper Preview (.md / .markdown)
        self.md_paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL, wide_handle=True)
        self.md_paned.set_position(540)

        self.md_edit_scrolled = Gtk.ScrolledWindow(hexpand=True, vexpand=True)
        self.md_textview = Gtk.TextView(
            wrap_mode=Gtk.WrapMode.WORD_CHAR,
            left_margin=20,
            right_margin=20,
            top_margin=20,
            bottom_margin=20,
            monospace=True
        )
        self.md_textview.add_css_class("md-editor-view")
        self.md_buffer = self.md_textview.get_buffer()
        self.md_buffer.connect("changed", self._on_md_buffer_changed)
        self.md_edit_scrolled.set_child(self.md_textview)
        self.md_paned.set_start_child(self.md_edit_scrolled)

        self.md_scrolled = Gtk.ScrolledWindow(hexpand=True, vexpand=True)
        md_outer = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            halign=Gtk.Align.CENTER,
            valign=Gtk.Align.START,
            margin_top=28,
            margin_bottom=40,
            margin_start=24,
            margin_end=24
        )
        self.md_paper_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            width_request=680
        )
        self.md_paper_box.add_css_class("md-paper")
        md_outer.append(self.md_paper_box)
        self.md_scrolled.set_child(md_outer)
        self.md_paned.set_end_child(self.md_scrolled)

        self.viewer_stack.add_named(self.md_paned, "markdown_doc")

        self.root_stack.add_named(viewer_toolbar, "viewer")
        self.root_stack.set_visible_child_name("collection")

        self._setup_actions()
        self._setup_drag_and_drop()

    # =========================================================================
    # Custom Icon Resolver & Library View / Sort Methods
    # =========================================================================
    def _resolve_icon_name(self, custom_name, fallback_name):
        svg_file = os.path.join(self.icons_dir, f"{custom_name}.svg")
        if os.path.isfile(svg_file) or self.icon_theme.has_icon(custom_name):
            return custom_name
        return fallback_name

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
        else:
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

    # =========================================================================
    # Page Management Sidebar Methods
    # =========================================================================
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

    # =========================================================================
    # Fit Page Width & PDF Import / Export Callbacks
    # =========================================================================
    def on_fit_width_clicked(self, *_):
        if self.root_stack.get_visible_child_name() != "viewer":
            return

        if self.current_file_type == "md":
            avail_w = self.md_scrolled.get_width() or (self.get_width() // 2)
            target_zoom = max(0.25, min((avail_w - 64.0) / 680.0, 4.0))
            self.set_zoom(target_zoom)
            return

        if not self.canvas.document:
            return
        pw, _ = self.canvas.get_page_size(self.canvas.current_page)
        if pw <= 0:
            return
        avail_w = self.scrolled.get_width() or self.get_width() or 1180
        target_zoom = max(0.1, min((avail_w - 48.0) / pw, 4.0))
        self.set_zoom(target_zoom)

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

    # =========================================================================
    # Unsaved Changes Confirmation Dialog
    # =========================================================================
    def _confirm_unsaved_changes(self, on_proceed_callback):
        if not self.is_modified or self.root_stack.get_visible_child_name() != "viewer":
            on_proceed_callback()
            return

        doc_name = os.path.basename(self.current_file_path) if self.current_file_path else "this note"
        heading = "Save Changes?"
        body = f"“{doc_name}” has unsaved changes. Any unsaved changes will be lost if you discard them."

        if hasattr(Adw, "AlertDialog"):
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
        else:
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

    def _on_window_close_request(self, *_):
        if self._force_closing or not self.is_modified or self.root_stack.get_visible_child_name() != "viewer":
            return False

        def proceed_close():
            self._force_closing = True
            self.close()

        self._confirm_unsaved_changes(proceed_close)
        return True

    # =========================================================================
    # Image & Lasso Selection Callbacks
    # =========================================================================
    def _on_aspect_lock_toggled(self, btn):
        self.canvas.keep_aspect_ratio = btn.get_active()

    def _on_canvas_selection_changed(self, has_selection, sel_type=None):
        self.img_ops_box.set_visible(has_selection)
        is_image_sel = (sel_type == "image")
        self.reset_aspect_btn.set_visible(is_image_sel)
        self.rot_l_btn.set_visible(is_image_sel)
        self.rot_r_btn.set_visible(is_image_sel)
        self.flip_h_btn.set_visible(is_image_sel)
        self.flip_v_btn.set_visible(is_image_sel)
        self.front_btn.set_visible(is_image_sel)
        self.back_layer_btn.set_visible(is_image_sel)

    def on_insert_image_clicked(self, *_):
        if not self.canvas.document:
            return
        if hasattr(Gtk, "FileDialog"):
            dialog = Gtk.FileDialog(title="Import Image onto Page")
            filters = Gio.ListStore.new(Gtk.FileFilter)
            img_filter = Gtk.FileFilter()
            img_filter.set_name("Images (PNG, JPEG, WEBP, SVG)")
            for pat in ("*.png", "*.jpg", "*.jpeg", "*.webp", "*.svg"):
                img_filter.add_pattern(pat)
            filters.append(img_filter)
            dialog.set_filters(filters)
            dialog.open(self, None, self._on_insert_image_selected)
        else:
            self.show_error("Gtk.FileDialog is required to import images.")

    def _on_insert_image_selected(self, dialog, result):
        try:
            gfile = dialog.open_finish(result)
            if gfile and gfile.get_path():
                path = gfile.get_path()
                asset_name, surf = XNotePackageParser.import_image_asset(
                    path, self.canvas.document, self.canvas.assets
                )
                self.select_tool_btn.set_active(True)
                self.canvas.add_image_to_page(self.canvas.current_page, asset_name, surf)
                self._queue_redraw_current_sidebar_thumb()
                self.show_toast("Image imported — drag corner handles to resize (Shift toggles aspect lock)")
        except GLib.Error:
            pass
        except Exception as e:
            self.show_error(f"Failed to import image: {e}")

    # =========================================================================
    # Page Template Switcher & Page Colour Selection
    # =========================================================================
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

    # =========================================================================
    # Markdown Editing & Formatting Callbacks
    # =========================================================================
    def _on_md_mode_toggled(self, btn, mode):
        if not btn.get_active():
            return
        self.md_view_mode = mode
        if mode == "preview":
            self.md_edit_scrolled.set_visible(False)
            self.md_scrolled.set_visible(True)
        elif mode == "edit":
            self.md_edit_scrolled.set_visible(True)
            self.md_scrolled.set_visible(False)
        else:
            self.md_edit_scrolled.set_visible(True)
            self.md_scrolled.set_visible(True)
            win_w = self.get_width() or 1180
            self.md_paned.set_position(int(win_w * 0.46))

    def _on_md_buffer_changed(self, buffer):
        if self._loading_md_buffer:
            return
        start, end = buffer.get_bounds()
        self.markdown_content = buffer.get_text(start, end, False)
        MarkdownRenderer.populate_box(
            self.md_paper_box, self.markdown_content, self.canvas.zoom_level
        )
        self._on_canvas_modified(True)

    def _on_md_format_clicked(self, btn, prefix, suffix):
        if not self.md_edit_scrolled.get_visible():
            self.md_split_btn.set_active(True)

        buf = self.md_buffer
        bounds = buf.get_selection_bounds()
        if bounds:
            start, end = bounds
            selected_text = buf.get_text(start, end, False)
            replacement = f"{prefix}{selected_text}{suffix}"
            buf.begin_user_action()
            buf.delete(start, end)
            buf.insert_at_cursor(replacement, len(replacement.encode('utf-8')))
            buf.end_user_action()
        else:
            placeholder = "text" if suffix else ""
            snippet = f"{prefix}{placeholder}{suffix}"
            buf.begin_user_action()
            buf.insert_at_cursor(snippet, len(snippet.encode('utf-8')))
            buf.end_user_action()
        self.md_textview.grab_focus()

    # =========================================================================
    # Stylus Toolbar Callbacks & Live Lasso Recoloring
    # =========================================================================
    def _on_tool_toggled(self, btn, tool_name):
        if not btn.get_active():
            return
        self.canvas.active_tool = tool_name
        if tool_name not in ("select", "lasso"):
            self.canvas.clear_selection()
        if tool_name in ("pen", "dashed", "speed", "velocity_pen", "calligraphy", "taper"):
            self.width_scale.set_value(self.canvas.pen_width)
        elif tool_name == "highlighter":
            self.width_scale.set_value(self.canvas.highlighter_width)
        elif tool_name == "eraser":
            self.width_scale.set_value(self.canvas.eraser_radius)

    def _on_color_changed(self, color_btn):
        rgba = color_btn.get_rgba()
        new_rgba = [
            int(round(rgba.red * 255)),
            int(round(rgba.green * 255)),
            int(round(rgba.blue * 255)),
            255
        ]
        self.canvas.pen_rgba = new_rgba

        if self.canvas.has_lasso_selection():
            if self.canvas.recolor_lasso_selection(new_rgba):
                self._queue_redraw_current_sidebar_thumb()
                self.show_toast("Recolored selected strokes")

    def _on_width_changed(self, scale):
        val = scale.get_value()
        if self.canvas.active_tool in ("pen", "dashed", "speed", "velocity_pen", "calligraphy", "taper"):
            self.canvas.pen_width = val
        elif self.canvas.active_tool == "highlighter":
            self.canvas.highlighter_width = val
        elif self.canvas.active_tool == "eraser":
            self.canvas.eraser_radius = val
        elif self.canvas.active_tool == "lasso" and self.canvas.has_lasso_selection():
            self.canvas.pen_width = val
            self.canvas.resize_lasso_stroke_width(val)
            self._queue_redraw_current_sidebar_thumb()

    def _on_canvas_modified(self, modified):
        self.is_modified = modified
        self.save_btn.set_sensitive(modified)
        if self.current_file_path:
            base = os.path.splitext(os.path.basename(self.current_file_path))[0]
            self.window_title.set_title(f"• {base}" if modified else base)
        if modified and self.current_file_type in ("xnote", "xcanvas"):
            self._queue_redraw_current_sidebar_thumb()

    def _on_history_changed(self, can_undo, can_redo):
        self.undo_btn.set_sensitive(can_undo)
        self.redo_btn.set_sensitive(can_redo)
        self._queue_redraw_current_sidebar_thumb()

    def on_add_page_clicked(self, *args):
        if not self.canvas.document:
            return
        pages = self.canvas.document.get("pages", [])
        pw, ph = self.canvas.get_page_size(len(pages) - 1) if pages else (1240.1574803149608, 1753.9370078740158)
        new_page = {
            "width": pw,
            "height": ph,
            "pdf_page": None,
            "items": []
        }
        pages.append(new_page)
        self.canvas.page_sizes = [self.canvas.compute_page_size(p) for p in pages]
        self.canvas.current_page = len(pages) - 1
        self._on_canvas_modified(True)
        self.update_page_View()
        self._rebuild_page_sidebar()
        if self.canvas.view_mode == "scrolled":
            GLib.idle_add(self.scroll_to_page, self.canvas.current_page)

    def on_save_clicked(self, *args):
        if not self.current_file_path:
            return False

        if self.current_file_type == "md":
            try:
                with open(self.current_file_path, "w", encoding="utf-8") as f:
                    f.write(self.markdown_content)
                self._on_canvas_modified(False)
                self.show_toast(f"Saved {os.path.basename(self.current_file_path)}")
                if self.current_root_folder:
                    self.load_folder(self.current_root_folder)
                return True
            except Exception as e:
                self.show_error(f"Failed to save Markdown file: {e}")
                return False

        if not self.canvas.document or self.current_file_type not in ("xnote", "xcanvas"):
            self.show_error("Only .xnote, .xcanvas, and .md files can be saved directly.")
            return False

        try:
            XNotePackageParser.save_archive(
                self.current_file_path,
                self.canvas.document,
                templates=self.canvas.templates
            )
            self._on_canvas_modified(False)
            self.show_toast(f"Saved {os.path.basename(self.current_file_path)}")
            if self.current_root_folder:
                self.load_folder(self.current_root_folder)
            return True
        except Exception as e:
            self.show_error(f"Failed to save archive: {e}")
            return False

    def on_new_notebook_clicked(self, *args):
        if hasattr(Gtk, "FileDialog"):
            dialog = Gtk.FileDialog(title="Create New .xnote Notebook")
            dialog.set_initial_name("Untitled.xnote")
            target_folder = self._get_active_category_folder()
            if target_folder and os.path.isdir(target_folder):
                dialog.set_initial_folder(Gio.File.new_for_path(target_folder))
            dialog.save(self, None, self._on_new_notebook_saved)
        else:
            self.show_error("Gtk.FileDialog is required to create a new file.")

    def _on_new_notebook_saved(self, dialog, result):
        try:
            gfile = dialog.save_finish(result)
            if gfile and gfile.get_path():
                path = gfile.get_path()
                if not path.lower().endswith(".xnote"):
                    path += ".xnote"
                XNotePackageParser.create_new_notebook(path)
                if self.current_root_folder:
                    self.load_folder(self.current_root_folder)
                self.load_document(path)
        except GLib.Error:
            pass

    def on_new_markdown_clicked(self, *args):
        if hasattr(Gtk, "FileDialog"):
            dialog = Gtk.FileDialog(title="Create New Markdown Note")
            dialog.set_initial_name("Untitled.md")
            target_folder = self._get_active_category_folder()
            if target_folder and os.path.isdir(target_folder):
                dialog.set_initial_folder(Gio.File.new_for_path(target_folder))
            dialog.save(self, None, self._on_new_markdown_saved)
        else:
            self.show_error("Gtk.FileDialog is required to create a new file.")

    def _on_new_markdown_saved(self, dialog, result):
        try:
            gfile = dialog.save_finish(result)
            if gfile and gfile.get_path():
                path = gfile.get_path()
                if not path.lower().endswith((".md", ".markdown")):
                    path += ".md"
                title = os.path.splitext(os.path.basename(path))[0]
                with open(path, "w", encoding="utf-8") as f:
                    f.write(f"# {title}\n\nStart writing your note here...\n")
                if self.current_root_folder:
                    self.load_folder(self.current_root_folder)
                self.load_document(path)
        except GLib.Error:
            pass

    # =========================================================================
    # Config & Actions
    # =========================================================================
    def get_config_path(self):
        config_dir = os.path.join(GLib.get_user_config_dir(), "xnotes_viewer")
        os.makedirs(config_dir, exist_ok=True)
        return os.path.join(config_dir, "config.json")

    def save_config(self, path=None):
        try:
            config_path = self.get_config_path()
            data = {}
            if os.path.exists(config_path):
                with open(config_path, "r") as f:
                    data = json.load(f)
            if path is not None:
                data["last_folder"] = path
            data["view_mode"] = self.canvas.view_mode
            data["library_view_mode"] = self.library_view_mode
            data["library_sort_mode"] = self.library_sort_mode
            with open(config_path, "w") as f:
                json.dump(data, f)
        except Exception as e:
            print(f"Failed to save config: {e}")

    def load_last_folder(self):
        try:
            config_path = self.get_config_path()
            if os.path.exists(config_path):
                with open(config_path, "r") as f:
                    data = json.load(f)
                    saved_mode = data.get("view_mode", "paged")
                    if saved_mode in ("paged", "scrolled"):
                        self.canvas.view_mode = saved_mode
                        if saved_mode == "scrolled":
                            self.scrolled_btn.set_active(True)
                        else:
                            self.paged_btn.set_active(True)

                    saved_lib_view = data.get("library_view_mode", "grid")
                    if saved_lib_view in ("grid", "list"):
                        self.library_view_mode = saved_lib_view
                        if saved_lib_view == "list":
                            self.lib_list_btn.set_active(True)
                        else:
                            self.lib_grid_btn.set_active(True)

                    saved_sort = data.get("library_sort_mode", "name_asc")
                    if saved_sort in ("name_asc", "name_desc", "date_desc", "date_asc", "notebook", "type"):
                        self.library_sort_mode = saved_sort
                        if hasattr(self, "sort_action"):
                            self.sort_action.set_state(GLib.Variant.new_string(saved_sort))

                    last_folder = data.get("last_folder")
                    if last_folder and os.path.isdir(last_folder):
                        self.load_folder(last_folder)
        except Exception as e:
            print(f"Failed to load config: {e}")

    def _setup_actions(self):
        actions = [
            ("open", self.on_open_clicked),
            ("new-category", self.on_new_category_clicked),
            ("new-notebook", self.on_new_notebook_clicked),
            ("import-pdf-new", self.on_import_pdf_as_new_notebook_clicked),
            ("export-pdf", self.on_export_pdf_clicked),
            ("new-markdown", self.on_new_markdown_clicked),
            ("save", self.on_save_clicked),
            ("undo", lambda *_: self.canvas.undo() if self.current_file_type != "md" else None),
            ("redo", lambda *_: self.canvas.redo() if self.current_file_type != "md" else None),
            ("zoom-in", lambda *_: self.set_zoom(self.canvas.zoom_level + 0.25)),
            ("zoom-out", lambda *_: self.set_zoom(self.canvas.zoom_level - 0.25)),
            ("zoom-reset", lambda *_: self.set_zoom(1.0)),
            ("fit-width", self.on_fit_width_clicked),
            ("toggle-sidebar", lambda *_: self.page_sidebar_btn.set_active(not self.page_sidebar_btn.get_active())),
            ("next-page", self.on_next_page),
            ("prev-page", self.on_prev_page),
            ("toggle-view-mode", self.on_toggle_view_mode_action),
            ("back", self.on_back_clicked),
            ("find", self.on_find_shortcut),
        ]
        for name, callback in actions:
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", callback)
            self.add_action(action)

        self.sort_action = Gio.SimpleAction.new_stateful(
            "sort-library",
            GLib.VariantType.new("s"),
            GLib.Variant.new_string(self.library_sort_mode)
        )
        self.sort_action.connect("activate", self._on_sort_library_action)
        self.add_action(self.sort_action)

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

    def _sidebar_header_func(self, row, before):
        section = getattr(row, "section_label", None)
        if section:
            lbl = Gtk.Label(label=section, xalign=0.0)
            lbl.add_css_class("sidebar-section-header")
            row.set_header(lbl)
        else:
            row.set_header(None)

    def on_find_shortcut(self, *args):
        if self.root_stack.get_visible_child_name() == "collection":
            self.search_btn.set_active(not self.search_btn.get_active())

    def on_search_toggled(self, btn):
        active = btn.get_active()
        self.search_bar.set_search_mode(active)
        if active:
            self.search_entry.grab_focus()
        else:
            self.search_entry.set_text("")

    def on_search_changed(self, entry):
        self._apply_collection_filters()

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

    def on_toggle_view_mode_action(self, *args):
        if self.current_file_type == "md":
            if self.md_view_mode == "preview":
                self.md_split_btn.set_active(True)
            elif self.md_view_mode == "split":
                self.md_edit_btn.set_active(True)
            else:
                self.md_preview_btn.set_active(True)
            return
        if self.current_file_type not in ("xnote", "xcanvas", "xtemplate") or not self.canvas.document:
            return
        if self.canvas.view_mode == "paged":
            self.scrolled_btn.set_active(True)
        else:
            self.paged_btn.set_active(True)

    def on_view_mode_toggled(self, button, mode):
        if not button.get_active() or self.canvas.view_mode == mode:
            return
        target_page = self.canvas.current_page
        self.canvas.view_mode = mode
        self.save_config()
        if self.canvas.document and self.current_file_type in ("xnote", "xcanvas", "xtemplate"):
            self.update_page_View()
            if self.canvas.view_mode == "scrolled":
                GLib.idle_add(self.scroll_to_page, target_page)
            else:
                self.vadj.set_value(0)

    def on_scroll_changed(self, adj):
        if self.canvas.view_mode != "scrolled" or not self.canvas.document or self._updating_scroll:
            return
        self.canvas.queue_draw()
        if not self.canvas.page_geometries:
            return

        scroll_y = adj.get_value()
        viewport_center = scroll_y + (adj.get_page_size() / 2.0)

        best_page = self.canvas.current_page
        for idx, (y_offset, pw, ph, scaled_w, scaled_h) in enumerate(self.canvas.page_geometries):
            if y_offset <= viewport_center <= y_offset + scaled_h + 24.0:
                best_page = idx
                break

        if best_page != self.canvas.current_page:
            self.canvas.current_page = best_page
            total = len(self.canvas.document.get("pages", []))
            self.page_label.set_label(f"{self.canvas.current_page + 1} / {total}")
            self.prev_btn.set_sensitive(self.canvas.current_page > 0)
            self.next_btn.set_sensitive(self.canvas.current_page < total - 1)
            self._sync_page_sidebar_selection()
            self._sync_toolbar_page_color_button()

    def scroll_to_page(self, page_idx):
        if self.canvas.view_mode == "scrolled" and 0 <= page_idx < len(self.canvas.page_geometries):
            self._updating_scroll = True
            y_offset = self.canvas.page_geometries[page_idx][0]
            self.vadj.set_value(max(0.0, y_offset - 20.0))
            self._updating_scroll = False
            self.canvas.queue_draw()
        return False

    def _setup_drag_and_drop(self):
        drop_target = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)
        drop_target.connect("drop", self.on_file_dropped)
        self.add_controller(drop_target)

    def on_file_dropped(self, drop_target, value, x, y):
        files = value.get_files()
        if files:
            path = files[0].get_path()
            if path:
                if os.path.isdir(path):
                    self.load_folder(path)
                else:
                    self.load_folder(os.path.dirname(path))
                    self.load_document(path)
                return True
        return False

    def _perform_return_to_collection(self):
        self.canvas.clear_selection()
        self.canvas.page_cache.clear()
        self.canvas.assets.clear()
        self.canvas.templates.clear()
        self.canvas.undo_stack.clear()
        self.canvas.redo_stack.clear()
        self._page_thumb_draw_areas.clear()
        self.current_file_path = None
        self.current_file_type = None
        self.is_modified = False
        self.root_stack.set_visible_child_name("collection")

    def on_back_clicked(self, *args):
        if self.root_stack.get_visible_child_name() != "viewer":
            return
        self._confirm_unsaved_changes(self._perform_return_to_collection)

    def on_open_clicked(self, *args):
        if hasattr(Gtk, "FileDialog"):
            dialog = Gtk.FileDialog(title="Open Notebook Collection Folder")
            dialog.select_folder(self, None, self.on_folder_selected)
        else:
            self._native_dialog = Gtk.FileChooserNative.new(
                "Open Notebook Collection Folder",
                self,
                Gtk.FileChooserAction.SELECT_FOLDER,
                "_Open",
                "_Cancel"
            )
            self._native_dialog.connect("response", self.on_native_folder_response)
            self._native_dialog.show()

    def on_native_folder_response(self, dialog, response):
        if response == Gtk.ResponseType.ACCEPT:
            folder = dialog.get_file()
            if folder and folder.get_path():
                self.load_folder(folder.get_path())
        self._native_dialog = None

    def on_folder_selected(self, dialog, result):
        try:
            folder = dialog.select_folder_finish(result)
            if folder is not None:
                path = folder.get_path()
                if path:
                    self.load_folder(path)
                else:
                    self.show_error("Selected folder is not on the local filesystem.")
        except GLib.Error as e:
            if hasattr(Gtk, "DialogError") and e.matches(Gtk.DialogError.quark(), Gtk.DialogError.DISMISSED):
                return
            if "Dismissed" not in e.message and "cancelled" not in e.message.lower():
                self.show_error(f"Error opening folder: {e.message}")

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
                icon_name="folder-symbolic",
                section_label="Categories" if first_cat else None
            )
            first_cat = False
            self.notebook_listbox.append(row)

        new_cat_row = self._create_sidebar_row(
            "New Category…",
            "__NEW_CATEGORY__",
            icon_name=self._resolve_icon_name("folder-new-symbolic", "list-add-symbolic"),
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

        # -----------------------------------------------------------------
        # 1. Build Grid View Card
        # -----------------------------------------------------------------
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

        # -----------------------------------------------------------------
        # 2. Build List View Row
        # -----------------------------------------------------------------
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
        row_hbox.append(mini_da)

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

        view_btn = Gtk.Button(label="Open in Notebooks Editor")
        view_btn.add_css_class("flat")
        view_btn.connect("clicked", lambda *_: (popover.popdown(), self.load_document(path)))
        vbox.append(view_btn)

        default_btn = Gtk.Button(label="Open with Default Application")
        default_btn.add_css_class("flat")
        default_btn.connect("clicked", lambda *_: (popover.popdown(), self.open_with_default_app(path)))
        vbox.append(default_btn)

        choose_btn = Gtk.Button(label="Select Application to Open With...")
        choose_btn.add_css_class("flat")
        choose_btn.connect("clicked", lambda *_: (popover.popdown(), self.choose_app_for_file(path)))
        vbox.append(choose_btn)

        vbox.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        folder_btn = Gtk.Button(label="Open Containing Folder")
        folder_btn.add_css_class("flat")
        folder_btn.connect("clicked", lambda *_: (popover.popdown(), self.open_containing_folder(path)))
        vbox.append(folder_btn)

        popover.set_child(vbox)
        return popover

    # =========================================================================
    # Document Loading & Viewport Sizing
    # =========================================================================
    def load_document(self, path):
        if self.is_modified and self.root_stack.get_visible_child_name() == "viewer":
            self._confirm_unsaved_changes(lambda: self._do_load_document(path))
        else:
            self._do_load_document(path)

    def _do_load_document(self, path):
        ext = os.path.splitext(path)[1].lower()
        self.current_file_path = path

        if ext in ('.md', '.markdown'):
            self._load_markdown_document(path)
        else:
            self._load_xnote_document(path, ext)

    def _load_markdown_document(self, path):
        try:
            with open(path, 'r', encoding='utf-8', errors='replace') as f:
                self.markdown_content = f.read()

            self.current_file_type = "md"
            self.canvas.clear_selection()
            self.canvas.document = None
            self.canvas.zoom_level = 1.0

            self._loading_md_buffer = True
            self.md_buffer.set_text(self.markdown_content)
            self._loading_md_buffer = False

            self._on_canvas_modified(False)

            MarkdownRenderer.populate_box(
                self.md_paper_box, self.markdown_content, self.canvas.zoom_level
            )

            self.window_title.set_title(os.path.splitext(os.path.basename(path))[0])
            self.window_title.set_subtitle("Markdown")
            self.viewer_stack.set_visible_child_name("markdown_doc")
            self.page_sidebar_btn.set_visible(False)
            self.mode_box.set_visible(False)
            self.stylus_bar.set_visible(False)
            self.md_toolbar_bar.set_visible(True)
            self.save_btn.set_visible(True)
            self.export_pdf_btn.set_visible(False)
            self.history_box.set_visible(False)
            self.zoom_reset_btn.set_label("100%")
            self.root_stack.set_visible_child_name("viewer")
        except Exception as e:
            self.show_error(f"Failed to load Markdown file {os.path.basename(path)}: {e}")

    def _load_xnote_document(self, path, ext):
        try:
            self.canvas.clear_selection()
            self.canvas.assets.clear()
            self.canvas.templates.clear()
            self.canvas.page_cache.clear()
            self.canvas.undo_stack.clear()
            self.canvas.redo_stack.clear()
            self._on_history_changed(False, False)
            self._on_canvas_modified(False)

            doc, assets, templates = XNotePackageParser.load_archive(path)
            self.canvas.document = doc
            self.canvas.assets = assets
            self.canvas.templates = templates

            if ext == ".xcanvas":
                self.current_file_type = "xcanvas"
            elif ext == ".xtemplate":
                self.current_file_type = "xtemplate"
            else:
                self.current_file_type = "xnote"

            self.canvas.current_page = 0
            pages = self.canvas.document.get("pages", [])
            self.canvas.page_sizes = [self.canvas.compute_page_size(p) for p in pages]

            self.window_title.set_title(os.path.splitext(os.path.basename(path))[0])
            self.window_title.set_subtitle("")
            self.viewer_stack.set_visible_child_name("canvas_doc")
            is_editable = self.current_file_type in ("xnote", "xcanvas")

            # Ensure the page sidebar is hidden by default when opening a notebook
            self.page_sidebar_btn.set_visible(is_editable)
            self.page_sidebar_btn.set_active(False)
            self.page_sidebar_revealer.set_reveal_child(False)

            self.mode_box.set_visible(True)
            self.md_toolbar_bar.set_visible(False)
            self.stylus_bar.set_visible(is_editable)
            self.save_btn.set_visible(is_editable)
            self.export_pdf_btn.set_visible(True)
            self.history_box.set_visible(is_editable)
            self.root_stack.set_visible_child_name("viewer")

            if self.canvas.page_sizes:
                pw, ph = self.canvas.page_sizes[0]
                win_w = self.get_width() or 1240
                win_h = self.get_height() or 820
                avail_h = win_h - 110
                if pw > 0 and ph > 0:
                    scale_w = (win_w - 100) / pw
                    scale_h = (avail_h - 90) / ph
                    self.canvas.zoom_level = max(0.1, min(min(scale_w, scale_h), 4.0))
                else:
                    self.canvas.zoom_level = 1.0
            else:
                self.canvas.zoom_level = 1.0

            self.vadj.set_value(0)
            self.update_page_View()
            self._rebuild_page_sidebar()
        except Exception as e:
            self.show_error(f"Failed to load {os.path.basename(path)}: {e}")

    def update_page_View(self):
        if not self.canvas.document:
            return

        pages = self.canvas.document.get("pages", [])
        total = len(pages)
        has_multiple = total > 1

        self.prev_btn.set_visible(has_multiple)
        self.next_btn.set_visible(has_multiple)
        self.page_badge.set_visible(total > 0)

        if total > 0:
            self.canvas.current_page = max(0, min(self.canvas.current_page, total - 1))
            self.page_label.set_label(f"{self.canvas.current_page + 1} / {total}")
            self.prev_btn.set_sensitive(self.canvas.current_page > 0)
            self.next_btn.set_sensitive(self.canvas.current_page < total - 1)

            if self.canvas.view_mode == "paged":
                self.canvas.set_valign(Gtk.Align.CENTER)
                pw, ph = self.canvas.get_page_size(self.canvas.current_page)
                self.canvas.page_geometries = [(20.0, pw, ph, pw * self.canvas.zoom_level, ph * self.canvas.zoom_level)]
                self.canvas.set_content_width(int(pw * self.canvas.zoom_level) + 40)
                self.canvas.set_content_height(int(ph * self.canvas.zoom_level) + 84)
            else:
                self.canvas.set_valign(Gtk.Align.START)
                self.canvas.page_geometries = []
                max_w = 0.0
                current_y = 20.0
                page_gap = 24.0
                for idx in range(total):
                    pw, ph = self.canvas.get_page_size(idx)
                    scaled_w = pw * self.canvas.zoom_level
                    scaled_h = ph * self.canvas.zoom_level
                    self.canvas.page_geometries.append((current_y, pw, ph, scaled_w, scaled_h))
                    max_w = max(max_w, scaled_w)
                    current_y += scaled_h + page_gap

                total_h = current_y - page_gap + 64.0 if pages else 84.0
                self.canvas.set_content_width(int(max_w) + 40)
                self.canvas.set_content_height(int(total_h))

        self.zoom_reset_btn.set_label(f"{int(self.canvas.zoom_level * 100)}%")
        self._sync_page_sidebar_selection()
        self._sync_toolbar_page_color_button()
        self.canvas.queue_draw()

    def set_zoom(self, zoom):
        if self.root_stack.get_visible_child_name() != "viewer":
            return
        new_zoom = max(0.1, min(zoom, 4.0))
        if abs(new_zoom - self.canvas.zoom_level) > 1e-4:
            self.canvas.zoom_level = new_zoom
            self.zoom_reset_btn.set_label(f"{int(self.canvas.zoom_level * 100)}%")
            if self.current_file_type == "md":
                self.md_paper_box.set_size_request(int(680 * self.canvas.zoom_level), -1)
                MarkdownRenderer.populate_box(
                    self.md_paper_box, self.markdown_content, self.canvas.zoom_level
                )
            elif self.canvas.document:
                self.canvas.page_cache.clear()
                self.update_page_View()

    def on_prev_page(self, *args):
        if self.canvas.document and self.canvas.current_page > 0:
            self.canvas.clear_selection()
            self.canvas.current_page -= 1
            self.update_page_View()
            if self.canvas.view_mode == "scrolled":
                self.scroll_to_page(self.canvas.current_page)

    def on_next_page(self, *args):
        if self.canvas.document:
            pages = self.canvas.document.get("pages", [])
            if self.canvas.current_page < len(pages) - 1:
                self.canvas.clear_selection()
                self.canvas.current_page += 1
                self.update_page_View()
                if self.canvas.view_mode == "scrolled":
                    self.scroll_to_page(self.canvas.current_page)

    def show_toast(self, message, timeout=3):
        toast = Adw.Toast.new(message)
        toast.set_timeout(timeout)
        self.toast_overlay.add_toast(toast)

    def show_error(self, message):
        self.show_toast(message, timeout=4)
