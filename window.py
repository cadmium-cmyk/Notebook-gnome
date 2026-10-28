import os
import re
import math
import json
from concurrent.futures import ThreadPoolExecutor
import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, Gio, GLib, Gdk

from xnote_io import XNotePackageParser
from canvas_editor import XNoteCanvas
from markdown_renderer import MarkdownRenderer, MarkdownInlineStyler

from library_manager import LibraryManagerMixin
from page_sidebar import PageSidebarMixin
from template_switcher import TemplateSwitcherMixin
from document_actions import DocumentActionsMixin


class XNotesViewerWindow(
    Adw.ApplicationWindow,
    LibraryManagerMixin,
    PageSidebarMixin,
    TemplateSwitcherMixin,
    DocumentActionsMixin
):
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
        self.md_view_mode = "edit"  # Show editor view by default, not split pane
        self._loading_md_buffer = False
        self._syncing_md_scroll = False
        self.md_search_matches = []
        self.md_search_idx = -1

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
            .md-editor-view {
                font-family: monospace;
                font-size: 14px;
                background-color: #ffffff;
                color: #2e3436;
            }
            .md-editor-view text {
                background-color: #ffffff;
                color: #2e3436;
            }
            .md-preview-scroll {
                background-color: #f6f6f6;
                border-left: 1px solid #e1e1e1;
            }
            .md-split-preview-box {
                background-color: transparent;
                color: #2e3436;
                padding: 32px 36px;
            }
            .md-paper {
                background-color: #ffffff;
                color: #1e1e1e;
                border-radius: 12px;
                padding: 36px 44px;
                border: 1px solid rgba(0, 0, 0, 0.14);
            }
            .md-heading-rule {
                background-color: #dcdcdc;
                min-height: 1px;
            }
            .md-code-block {
                background-color: #ececef;
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
        sidebar_title = Adw.WindowTitle(title="Notebook", subtitle="")
        sidebar_header.set_title_widget(sidebar_title)

        sidebar_menu = Gio.Menu()
        sidebar_menu.append("New Category", "win.new-category")
        sidebar_menu.append("New Notebook", "win.new-notebook")
        sidebar_menu.append("Import from PDF", "win.import-pdf-new")
        sidebar_menu.append("New Text Document", "win.new-markdown")
        sidebar_menu.append("Open Folder", "win.open")
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
        self.collection_title = Adw.WindowTitle(title="", subtitle="")
        content_header.set_title_widget(self.collection_title)

        self.open_btn = Gtk.Button(
            icon_name="folder-open-symbolic",
            tooltip_text="Open Collection Folder (Ctrl+O)"
        )
        self.open_btn.set_action_name("win.open")
        # content_header.pack_start(self.open_btn)

        new_menu = Gio.Menu()
        new_menu.append("New Notebook", "win.new-notebook")
        new_menu.append("Import from PDF", "win.import-pdf-new")
        new_menu.append("New Text Note", "win.new-markdown")
        new_menu.append("New Category", "win.new-category")
        self.new_note_btn = Gtk.MenuButton(
            icon_name="document-new-symbolic",
            menu_model=new_menu,
            tooltip_text="Create New Notebook, Import PDF, Markdown Note, or Category"
        )

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
                Adw.NavigationPage.new(content_toolbar, "Documents")
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

        # Page Management Sidebar Toggle Button
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
        self.undo_btn.connect("clicked", self.on_undo_activated)
        self.history_box.append(self.undo_btn)

        self.redo_btn = Gtk.Button(
            icon_name="edit-redo-symbolic",
            tooltip_text="Redo (Ctrl+Y / Ctrl+Shift+Z)",
            sensitive=False
        )
        self.redo_btn.connect("clicked", self.on_redo_activated)
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
            icon_name="document-export-symbolic",
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
        # 2a. STYLUS BAR USING DEDICATED tool-* ICONS
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
        # 2b. REFINED CENTERED MARKDOWN EDITING BAR (.md / .markdown)
        # =====================================================================
        self.md_toolbar_bar = Gtk.CenterBox(
            halign=Gtk.Align.FILL,
            visible=False
        )
        self.md_toolbar_bar.add_css_class("stylus-toolbar")

        # Start widget: View Mode Switcher (Editor default, Split, Preview)
        md_mode_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        md_mode_box.add_css_class("linked")

        self.md_edit_btn = Gtk.ToggleButton(
            icon_name=self._resolve_icon_name("document-edit-symbolic", "accessories-text-editor-symbolic"),
            active=True,
            tooltip_text="Markdown Editor View (Ctrl+M)"
        )
        self.md_edit_btn.connect("toggled", self._on_md_mode_toggled, "edit")
        md_mode_box.append(self.md_edit_btn)

        self.md_split_btn = Gtk.ToggleButton(
            icon_name="view-dual-symbolic",
            group=self.md_edit_btn,
            tooltip_text="Side-by-Side Split View (Ctrl+M)"
        )
        self.md_split_btn.connect("toggled", self._on_md_mode_toggled, "split")
        md_mode_box.append(self.md_split_btn)

        self.md_preview_btn = Gtk.ToggleButton(
            icon_name="view-reveal-symbolic",
            group=self.md_edit_btn,
            tooltip_text="Formatted Paper Preview (Ctrl+M)"
        )
        self.md_preview_btn.connect("toggled", self._on_md_mode_toggled, "preview")
        md_mode_box.append(self.md_preview_btn)

        self.md_toolbar_bar.set_start_widget(md_mode_box)

        # Center widget: Symbolic Formatting Toolbar + Find in Page button
        center_fmt_wrap = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=8,
            halign=Gtk.Align.CENTER
        )

        fmt_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        fmt_box.add_css_class("linked")

        fmt_specs = [
            ("format-text-bold-symbolic", "Bold (Ctrl+B)", "**", "**"),
            ("format-text-italic-symbolic", "Italic (Ctrl+I)", "*", "*"),
            ("format-text-strikethrough-symbolic", "Strikethrough (Ctrl+Shift+X)", "~~", "~~"),
            (self._resolve_icon_name("code-context-symbolic", "utilities-terminal-symbolic"), "Inline Code (Ctrl+E)", "`", "`"),
            ("format-text-larger-symbolic", "Heading 1 (Ctrl+1)", "# ", ""),
            ("format-text-smaller-symbolic", "Heading 2 (Ctrl+2)", "## ", ""),
            ("view-list-bullet-symbolic", "Bullet List (Ctrl+Shift+L)", "- ", ""),
            (self._resolve_icon_name("view-list-ordered-symbolic", "view-list-symbolic"), "Numbered List", "1. ", ""),
            ("object-select-symbolic", "Task Checkbox", "- [ ] ", ""),
            ("format-text-blockquote-symbolic", "Blockquote (Ctrl+Shift+Q)", "> ", ""),
            ("link-symbolic", "Insert Link (Ctrl+K)", "[", "](https://)"),
            ("list-remove-symbolic", "Horizontal Rule", "\n---\n", ""),
        ]
        for icon_name, tip, prefix, suffix in fmt_specs:
            b = Gtk.Button(icon_name=icon_name, tooltip_text=tip)
            b.connect("clicked", self._on_md_format_clicked, prefix, suffix)
            fmt_box.append(b)

        center_fmt_wrap.append(fmt_box)

        # Inline Rendering toggle + Find in Page toggle button
        md_tools_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)

        self.md_inline_render_btn = Gtk.ToggleButton(
            icon_name=self._resolve_icon_name("view-conceal-symbolic", "applications-science-symbolic"),
            active=True,
            tooltip_text="Toggle Live Inline Markdown Rendering"
        )
        self.md_inline_render_btn.connect("toggled", self._on_md_inline_render_toggled)
        md_tools_box.append(self.md_inline_render_btn)

        self.md_find_btn = Gtk.ToggleButton(
            icon_name="system-search-symbolic",
            tooltip_text="Find in Page (Ctrl+F)"
        )
        self.md_find_btn.connect("toggled", self._on_md_find_toggled)
        md_tools_box.append(self.md_find_btn)

        center_fmt_wrap.append(md_tools_box)
        self.md_toolbar_bar.set_center_widget(center_fmt_wrap)

        viewer_toolbar.add_top_bar(self.md_toolbar_bar)

        # =====================================================================
        # 2b-ii. MARKDOWN FIND IN PAGE SEARCH BAR
        # =====================================================================
        self.md_search_bar = Gtk.SearchBar()
        self.md_search_bar.set_show_close_button(True)

        md_search_box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=6,
            halign=Gtk.Align.CENTER
        )
        self.md_search_entry = Gtk.SearchEntry(
            placeholder_text="Find in note…",
            width_chars=28
        )
        self.md_search_entry.connect("search-changed", self._on_md_search_changed)
        self.md_search_entry.connect("activate", lambda *_: self._step_md_search(1))
        self.md_search_entry.connect("next-match", lambda *_: self._step_md_search(1))
        self.md_search_entry.connect("previous-match", lambda *_: self._step_md_search(-1))
        self.md_search_entry.connect("stop-search", lambda *_: self.md_find_btn.set_active(False))
        md_search_box.append(self.md_search_entry)

        nav_match_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        nav_match_box.add_css_class("linked")

        self.md_search_prev_btn = Gtk.Button(
            icon_name="go-up-symbolic",
            tooltip_text="Previous Match (Shift+Enter)"
        )
        self.md_search_prev_btn.connect("clicked", lambda *_: self._step_md_search(-1))
        nav_match_box.append(self.md_search_prev_btn)

        self.md_search_next_btn = Gtk.Button(
            icon_name="go-down-symbolic",
            tooltip_text="Next Match (Enter)"
        )
        self.md_search_next_btn.connect("clicked", lambda *_: self._step_md_search(1))
        nav_match_box.append(self.md_search_next_btn)

        md_search_box.append(nav_match_box)

        self.md_search_count_lbl = Gtk.Label(label="")
        self.md_search_count_lbl.add_css_class("dim-label")
        self.md_search_count_lbl.add_css_class("numeric")
        self.md_search_count_lbl.set_margin_start(6)
        md_search_box.append(self.md_search_count_lbl)

        self.md_search_bar.set_child(md_search_box)
        self.md_search_bar.connect_entry(self.md_search_entry)
        self.md_search_bar.connect(
            "notify::search-mode-enabled",
            self._on_md_search_bar_mode_notify
        )
        viewer_toolbar.add_top_bar(self.md_search_bar)

        # Document Viewer Stack
        self.viewer_stack = Gtk.Stack(hexpand=True, vexpand=True)
        viewer_toolbar.set_content(self.viewer_stack)

        # =====================================================================
        # 2c. CANVAS WORKSPACE WITH PAGE SIDEBAR
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

        # =====================================================================
        # 2d. Markdown Split Editor & Live Preview (.md / .markdown)
        # =====================================================================
        self.md_paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL, wide_handle=False)
        self.md_paned.set_position(600)

        self.md_edit_scrolled = Gtk.ScrolledWindow(hexpand=True, vexpand=True)
        self.md_textview = Gtk.TextView(
            wrap_mode=Gtk.WrapMode.WORD_CHAR,
            left_margin=64,
            right_margin=40,
            top_margin=36,
            bottom_margin=36,
            pixels_above_lines=3,
            pixels_below_lines=3,
            monospace=True
        )
        self.md_textview.add_css_class("md-editor-view")
        self.md_buffer = self.md_textview.get_buffer()
        if hasattr(self.md_buffer, "set_enable_undo"):
            self.md_buffer.set_enable_undo(True)
        self.md_inline_styler = MarkdownInlineStyler(self.md_textview)
        self.md_buffer.connect("changed", self._on_md_buffer_changed)
        self.md_buffer.connect("notify::can-undo", lambda *_: self._sync_md_undo_redo_buttons())
        self.md_buffer.connect("notify::can-redo", lambda *_: self._sync_md_undo_redo_buttons())

        # CAPTURE phase key controller so Return/Enter & shortcuts are handled cleanly
        md_key_ctrl = Gtk.EventControllerKey()
        md_key_ctrl.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        md_key_ctrl.connect("key-pressed", self._on_md_key_pressed)
        self.md_textview.add_controller(md_key_ctrl)

        self.md_edit_scrolled.set_child(self.md_textview)
        self.md_paned.set_start_child(self.md_edit_scrolled)

        self.md_scrolled = Gtk.ScrolledWindow(hexpand=True, vexpand=True, visible=False)
        self.md_scrolled.add_css_class("md-preview-scroll")

        # Setup synchronized scrolling adjustments between editor and preview
        self.md_edit_vadj = self.md_edit_scrolled.get_vadjustment()
        self.md_preview_vadj = self.md_scrolled.get_vadjustment()
        self.md_edit_vadj.connect("value-changed", self._on_md_edit_scroll_changed)
        self.md_preview_vadj.connect("value-changed", self._on_md_preview_scroll_changed)

        self.md_outer = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            halign=Gtk.Align.FILL,
            valign=Gtk.Align.START,
            hexpand=True
        )
        self.md_paper_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            hexpand=True
        )
        self.md_paper_box.add_css_class("md-split-preview-box")
        self.md_outer.append(self.md_paper_box)
        self.md_scrolled.set_child(self.md_outer)
        self.md_paned.set_end_child(self.md_scrolled)

        self.viewer_stack.add_named(self.md_paned, "markdown_doc")

        self.root_stack.add_named(viewer_toolbar, "viewer")
        self.root_stack.set_visible_child_name("collection")

        self._setup_actions()
        self._setup_drag_and_drop()

    # =========================================================================
    # Synchronized Scrolling Handlers
    # =========================================================================
    def _on_md_edit_scroll_changed(self, adj):
        if self._syncing_md_scroll or self.md_view_mode != "split":
            return
        self._syncing_md_scroll = True
        edit_upper = adj.get_upper() - adj.get_page_size()
        prev_upper = self.md_preview_vadj.get_upper() - self.md_preview_vadj.get_page_size()
        if edit_upper > 0 and prev_upper > 0:
            fraction = adj.get_value() / edit_upper
            self.md_preview_vadj.set_value(fraction * prev_upper)
        self._syncing_md_scroll = False

    def _on_md_preview_scroll_changed(self, adj):
        if self._syncing_md_scroll or self.md_view_mode != "split":
            return
        self._syncing_md_scroll = True
        prev_upper = adj.get_upper() - adj.get_page_size()
        edit_upper = self.md_edit_vadj.get_upper() - self.md_edit_vadj.get_page_size()
        if prev_upper > 0 and edit_upper > 0:
            fraction = adj.get_value() / prev_upper
            self.md_edit_vadj.set_value(fraction * edit_upper)
        self._syncing_md_scroll = False

    # =========================================================================
    # Custom Icon Resolver
    # =========================================================================
    def _resolve_icon_name(self, custom_name, fallback_name):
        svg_file = os.path.join(self.icons_dir, f"{custom_name}.svg")
        if os.path.isfile(svg_file) or self.icon_theme.has_icon(custom_name):
            return custom_name
        return fallback_name

    # =========================================================================
    # Unified Undo / Redo Handlers (.xnote Canvas + Markdown Editor)
    # =========================================================================
    def on_undo_activated(self, *_):
        if self.current_file_type == "md":
            if self.md_buffer.get_can_undo():
                self.md_buffer.undo()
                self._sync_md_undo_redo_buttons()
        else:
            self.canvas.undo()

    def on_redo_activated(self, *_):
        if self.current_file_type == "md":
            if self.md_buffer.get_can_redo():
                self.md_buffer.redo()
                self._sync_md_undo_redo_buttons()
        else:
            self.canvas.redo()

    def _sync_md_undo_redo_buttons(self):
        if self.current_file_type == "md":
            self.undo_btn.set_sensitive(self.md_buffer.get_can_undo())
            self.redo_btn.set_sensitive(self.md_buffer.get_can_redo())

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
    # Markdown Editing, Formatting, Shortcuts & Find in Page Callbacks
    # =========================================================================
    def _iter_at_line(self, line_num: int) -> Gtk.TextIter:
        """Unpack PyGObject's (found, Gtk.TextIter) _ResultTuple from get_iter_at_line."""
        res = self.md_buffer.get_iter_at_line(line_num)
        return res[1] if isinstance(res, tuple) else res

    def _apply_md_preview_layout_style(self, mode):
        if mode == "preview":
            self.md_outer.set_halign(Gtk.Align.CENTER)
            self.md_outer.set_margin_top(28)
            self.md_outer.set_margin_bottom(40)
            self.md_outer.set_margin_start(24)
            self.md_outer.set_margin_end(24)
            self.md_paper_box.remove_css_class("md-split-preview-box")
            self.md_paper_box.add_css_class("md-paper")
            self.md_paper_box.set_size_request(int(680 * self.canvas.zoom_level), -1)
        else:
            self.md_outer.set_halign(Gtk.Align.FILL)
            self.md_outer.set_margin_top(0)
            self.md_outer.set_margin_bottom(0)
            self.md_outer.set_margin_start(0)
            self.md_outer.set_margin_end(0)
            self.md_paper_box.remove_css_class("md-paper")
            self.md_paper_box.add_css_class("md-split-preview-box")
            self.md_paper_box.set_size_request(-1, -1)

    def _on_md_mode_toggled(self, btn, mode):
        if not btn.get_active():
            return
        self.md_view_mode = mode
        self._apply_md_preview_layout_style(mode)

        if mode == "preview":
            self.md_edit_scrolled.set_visible(False)
            self.md_scrolled.set_visible(True)
        elif mode == "edit":
            self.md_edit_scrolled.set_visible(True)
            self.md_scrolled.set_visible(False)
        else:
            self.md_edit_scrolled.set_visible(True)
            self.md_scrolled.set_visible(True)
            win_w = self.get_width() or 1200
            self.md_paned.set_position(int(win_w * 0.50))

    def _on_md_inline_render_toggled(self, btn):
        self.md_inline_styler.inline_render = btn.get_active()
        self.md_inline_styler.refresh()

    def _on_md_buffer_changed(self, buffer):
        if self._loading_md_buffer:
            return
        start, end = buffer.get_bounds()
        self.markdown_content = buffer.get_text(start, end, False)
        MarkdownRenderer.populate_box(
            self.md_paper_box, self.markdown_content, self.canvas.zoom_level
        )
        if self.md_search_bar.get_search_mode():
            self._update_md_search_matches(scroll_to_current=False)
        self._sync_md_undo_redo_buttons()
        self._on_canvas_modified(True)

    def _toggle_line_block_format(self, prefix: str):
        buf = self.md_buffer
        bounds = buf.get_selection_bounds()
        if bounds:
            start_iter, end_iter = bounds
            if end_iter.starts_line() and end_iter.get_line() > start_iter.get_line():
                end_iter.backward_char()
        else:
            insert_mark = buf.get_insert()
            start_iter = buf.get_iter_at_mark(insert_mark)
            end_iter = start_iter.copy()

        start_line = start_iter.get_line()
        end_line = end_iter.get_line()

        block_start = self._iter_at_line(start_line)
        block_end = self._iter_at_line(end_line)
        if not block_end.ends_line():
            block_end.forward_to_line_end()

        raw_block = buf.get_text(block_start, block_end, False)
        lines = raw_block.split("\n")
        non_empty_lines = [ln for ln in lines if ln.strip()]

        is_ordered = (prefix == "1. ")
        is_task = (prefix == "- [ ] ")

        def line_has_target_prefix(ln: str) -> bool:
            if is_ordered:
                return bool(re.match(r'^\s*\d+\.\s+', ln))
            if is_task:
                return bool(re.match(r'^\s*[-*+]\s+\[[ xX]\]\s+', ln))
            return bool(re.match(r'^\s*' + re.escape(prefix), ln))

        def strip_existing_block_prefix(ln: str) -> tuple[str, str]:
            m_indent = re.match(r'^(\s*)', ln)
            indent = m_indent.group(1) if m_indent else ""
            rest = ln[len(indent):]
            for pat in (
                r'^[-*+]\s+\[[ xX]\]\s+',
                r'^[-*+]\s+',
                r'^\d+\.\s+',
                r'^#{1,6}\s+',
                r'^>\s?',
            ):
                m = re.match(pat, rest)
                if m:
                    return indent, rest[m.end():]
            return indent, rest

        all_already_formatted = bool(non_empty_lines) and all(
            line_has_target_prefix(ln) for ln in non_empty_lines
        )

        new_lines = []
        num_counter = 1
        for ln in lines:
            if not ln.strip() and len(lines) > 1:
                new_lines.append(ln)
                continue

            if all_already_formatted:
                if is_ordered:
                    new_lines.append(re.sub(r'^(\s*)\d+\.\s+', r'\1', ln, count=1))
                elif is_task:
                    new_lines.append(re.sub(r'^(\s*)[-*+]\s+\[[ xX]\]\s+', r'\1', ln, count=1))
                else:
                    new_lines.append(re.sub(r'^(\s*)' + re.escape(prefix), r'\1', ln, count=1))
            else:
                indent, stripped_body = strip_existing_block_prefix(ln)
                if is_ordered:
                    new_lines.append(f"{indent}{num_counter}. {stripped_body}")
                    num_counter += 1
                else:
                    new_lines.append(f"{indent}{prefix}{stripped_body}")

        replacement = "\n".join(new_lines)
        buf.begin_user_action()
        buf.delete(block_start, block_end)
        insert_pos = self._iter_at_line(start_line)
        buf.insert(insert_pos, replacement, len(replacement.encode("utf-8")))
        buf.end_user_action()

        if bounds and start_line != end_line:
            new_start = self._iter_at_line(start_line)
            new_end = self._iter_at_line(end_line)
            if not new_end.ends_line():
                new_end.forward_to_line_end()
            buf.select_range(new_start, new_end)

    def _on_md_format_clicked(self, btn, prefix, suffix):
        if not self.md_edit_scrolled.get_visible():
            self.md_edit_btn.set_active(True)

        buf = self.md_buffer

        if suffix == "" and not prefix.startswith("\n"):
            self._toggle_line_block_format(prefix)
            self.md_textview.grab_focus()
            return

        bounds = buf.get_selection_bounds()
        if bounds:
            start, end = bounds
            start_offset = start.get_offset()
            selected_text = buf.get_text(start, end, False)

            already_inside = (
                selected_text.startswith(prefix)
                and selected_text.endswith(suffix)
                and len(selected_text) >= len(prefix) + len(suffix)
            )

            outer_start = start.copy()
            outer_end = end.copy()
            has_outer = (
                outer_start.backward_chars(len(prefix))
                and outer_end.forward_chars(len(suffix))
                and buf.get_text(outer_start, start, False) == prefix
                and buf.get_text(end, outer_end, False) == suffix
            )

            buf.begin_user_action()
            if already_inside:
                replacement = selected_text[len(prefix):len(selected_text) - len(suffix)]
                buf.delete(start, end)
                ins_iter = buf.get_iter_at_offset(start_offset)
                buf.insert(ins_iter, replacement, len(replacement.encode("utf-8")))
                s_it = buf.get_iter_at_offset(start_offset)
                e_it = buf.get_iter_at_offset(start_offset + len(replacement))
                buf.select_range(s_it, e_it)
            elif has_outer:
                outer_offset = outer_start.get_offset()
                buf.delete(outer_start, outer_end)
                ins_iter = buf.get_iter_at_offset(outer_offset)
                buf.insert(ins_iter, selected_text, len(selected_text.encode("utf-8")))
                s_it = buf.get_iter_at_offset(outer_offset)
                e_it = buf.get_iter_at_offset(outer_offset + len(selected_text))
                buf.select_range(s_it, e_it)
            else:
                if "\n" in selected_text and prefix in ("**", "*", "~~", "`"):
                    lines = selected_text.split("\n")
                    non_empty = [ln for ln in lines if ln.strip()]
                    all_wrapped = bool(non_empty) and all(
                        ln.strip().startswith(prefix) and ln.strip().endswith(suffix)
                        and len(ln.strip()) >= len(prefix) + len(suffix)
                        for ln in non_empty
                    )
                    processed = []
                    for ln in lines:
                        if not ln.strip():
                            processed.append(ln)
                        elif all_wrapped:
                            m = re.match(r'^(\s*)(.*?)(\s*)$', ln)
                            lead, core, trail = m.group(1), m.group(2), m.group(3)
                            core_unwrapped = core[len(prefix):len(core) - len(suffix)]
                            processed.append(f"{lead}{core_unwrapped}{trail}")
                        else:
                            m = re.match(r'^(\s*)(.*?)(\s*)$', ln)
                            lead, core, trail = m.group(1), m.group(2), m.group(3)
                            processed.append(f"{lead}{prefix}{core}{suffix}{trail}")
                    replacement = "\n".join(processed)
                else:
                    replacement = f"{prefix}{selected_text}{suffix}"

                buf.delete(start, end)
                ins_iter = buf.get_iter_at_offset(start_offset)
                buf.insert(ins_iter, replacement, len(replacement.encode("utf-8")))
                s_it = buf.get_iter_at_offset(start_offset)
                e_it = buf.get_iter_at_offset(start_offset + len(replacement))
                buf.select_range(s_it, e_it)
            buf.end_user_action()
        else:
            placeholder = "text" if suffix else ""
            snippet = f"{prefix}{placeholder}{suffix}"
            buf.begin_user_action()
            buf.insert_at_cursor(snippet, len(snippet.encode("utf-8")))
            buf.end_user_action()
            if suffix and placeholder:
                insert_iter = buf.get_iter_at_mark(buf.get_insert())
                sel_end = insert_iter.copy()
                sel_end.backward_chars(len(suffix))
                sel_start = sel_end.copy()
                sel_start.backward_chars(len(placeholder))
                buf.select_range(sel_start, sel_end)

        self.md_textview.grab_focus()

    def _handle_md_return_key(self) -> bool:
        buf = self.md_buffer
        if buf.get_selection_bounds():
            return False

        insert_mark = buf.get_insert()
        cursor_iter = buf.get_iter_at_mark(insert_mark)

        line_start = cursor_iter.copy()
        line_start.set_line_offset(0)
        line_end = line_start.copy()
        if not line_end.ends_line():
            line_end.forward_to_line_end()

        full_line = buf.get_text(line_start, line_end, False)
        text_before_cursor = buf.get_text(line_start, cursor_iter, False)

        task_match = re.match(r'^(\s*)([-*+])\s+\[([ xX])\]\s+(.*)$', full_line)
        if task_match:
            indent, bullet, _state, content = task_match.groups()
            if not content.strip():
                buf.begin_user_action()
                buf.delete(line_start, line_end)
                buf.end_user_action()
                return True
            if len(text_before_cursor) >= len(full_line) - len(content):
                continuation = f"\n{indent}{bullet} [ ] "
                buf.begin_user_action()
                buf.insert_at_cursor(continuation, len(continuation.encode("utf-8")))
                buf.end_user_action()
                self.md_textview.scroll_mark_onscreen(buf.get_insert())
                return True

        ul_match = re.match(r'^(\s*)([-*+])\s+(.*)$', full_line)
        if ul_match:
            indent, bullet, content = ul_match.groups()
            if not content.strip():
                buf.begin_user_action()
                buf.delete(line_start, line_end)
                buf.end_user_action()
                return True
            if len(text_before_cursor) >= len(full_line) - len(content):
                continuation = f"\n{indent}{bullet} "
                buf.begin_user_action()
                buf.insert_at_cursor(continuation, len(continuation.encode("utf-8")))
                buf.end_user_action()
                self.md_textview.scroll_mark_onscreen(buf.get_insert())
                return True

        ol_match = re.match(r'^(\s*)(\d+)\.\s+(.*)$', full_line)
        if ol_match:
            indent, num_str, content = ol_match.groups()
            if not content.strip():
                buf.begin_user_action()
                buf.delete(line_start, line_end)
                buf.end_user_action()
                return True
            if len(text_before_cursor) >= len(full_line) - len(content):
                next_num = int(num_str) + 1
                continuation = f"\n{indent}{next_num}. "
                buf.begin_user_action()
                buf.insert_at_cursor(continuation, len(continuation.encode("utf-8")))
                buf.end_user_action()
                self.md_textview.scroll_mark_onscreen(buf.get_insert())
                return True

        return False

    def _on_md_key_pressed(self, controller, keyval, keycode, state):
        ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)
        shift = bool(state & Gdk.ModifierType.SHIFT_MASK)
        alt = bool(state & Gdk.ModifierType.ALT_MASK)

        if keyval == Gdk.KEY_Escape and self.md_search_bar.get_search_mode():
            self.md_find_btn.set_active(False)
            self.md_textview.grab_focus()
            return True

        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and not ctrl and not shift and not alt:
            if self._handle_md_return_key():
                return True

        if not ctrl:
            return False

        key_name = Gdk.keyval_name(keyval)
        if not key_name:
            return False
        key_lower = key_name.lower()

        if key_lower == "z" and not shift:
            if self.md_buffer.get_can_undo():
                self.md_buffer.undo()
                self._sync_md_undo_redo_buttons()
            return True

        if (key_lower == "y" and not shift) or (key_lower == "z" and shift):
            if self.md_buffer.get_can_redo():
                self.md_buffer.redo()
                self._sync_md_undo_redo_buttons()
            return True

        if key_lower == "c" and not shift:
            clipboard = self.get_clipboard()
            self.md_buffer.copy_clipboard(clipboard)
            return True

        if key_lower == "x" and not shift:
            clipboard = self.get_clipboard()
            self.md_buffer.cut_clipboard(clipboard, self.md_textview.get_editable())
            return True

        if key_lower == "v" and not shift:
            clipboard = self.get_clipboard()
            self.md_buffer.paste_clipboard(clipboard, None, self.md_textview.get_editable())
            return True

        if key_lower == "f" and not shift:
            self.md_find_btn.set_active(True)
            self.md_search_entry.grab_focus()
            return True

        if key_lower == "b" and not shift:
            self._on_md_format_clicked(None, "**", "**")
            return True

        if key_lower == "i" and not shift:
            self._on_md_format_clicked(None, "*", "*")
            return True

        if key_lower == "x" and shift:
            self._on_md_format_clicked(None, "~~", "~~")
            return True

        if (key_lower == "e" and not shift) or key_lower in ("grave", "asciitilde"):
            self._on_md_format_clicked(None, "`", "`")
            return True

        if key_lower == "k" and not shift:
            self._on_md_format_clicked(None, "[", "](https://)")
            return True

        if key_lower == "q" and shift:
            self._on_md_format_clicked(None, "> ", "")
            return True

        if key_lower == "l" and shift:
            self._on_md_format_clicked(None, "- ", "")
            return True

        if key_lower in ("1", "2", "3") and not shift:
            level = int(key_lower)
            self._on_md_format_clicked(None, "#" * level + " ", "")
            return True

        return False

    def _on_md_find_toggled(self, btn):
        active = btn.get_active()
        self.md_search_bar.set_search_mode(active)
        if active:
            bounds = self.md_buffer.get_selection_bounds()
            if bounds:
                sel_text = self.md_buffer.get_text(bounds[0], bounds[1], False)
                if sel_text and "\n" not in sel_text:
                    self.md_search_entry.set_text(sel_text)
            self.md_search_entry.grab_focus()
            self._update_md_search_matches(scroll_to_current=True)
        else:
            self._clear_md_search_tags()
            self.md_search_count_lbl.set_label("")
            if self.current_file_type == "md":
                self.md_textview.grab_focus()

    def _on_md_search_bar_mode_notify(self, search_bar, _pspec):
        enabled = search_bar.get_search_mode()
        if self.md_find_btn.get_active() != enabled:
            self.md_find_btn.set_active(enabled)

    def _clear_md_search_tags(self):
        start, end = self.md_buffer.get_bounds()
        self.md_buffer.remove_tag_by_name("md-search-match", start, end)
        self.md_buffer.remove_tag_by_name("md-search-current", start, end)

    def _on_md_search_changed(self, _entry):
        self._update_md_search_matches(scroll_to_current=True)

    def _update_md_search_matches(self, scroll_to_current=True):
        self._clear_md_search_tags()
        self.md_search_matches.clear()

        query = self.md_search_entry.get_text()
        if not query:
            self.md_search_idx = -1
            self.md_search_count_lbl.set_label("")
            return

        start, end = self.md_buffer.get_bounds()
        full_text = self.md_buffer.get_text(start, end, True)

        for m in re.finditer(re.escape(query), full_text, re.IGNORECASE):
            self.md_search_matches.append((m.start(), m.end()))

        total = len(self.md_search_matches)
        if total == 0:
            self.md_search_idx = -1
            self.md_search_count_lbl.set_label("0 results")
            return

        if self.md_search_idx < 0 or self.md_search_idx >= total:
            self.md_search_idx = 0

        self._highlight_md_search_matches(scroll_to_current=scroll_to_current)

    def _highlight_md_search_matches(self, scroll_to_current=True):
        self._clear_md_search_tags()
        total = len(self.md_search_matches)
        if total == 0:
            self.md_search_count_lbl.set_label("0 results")
            return

        for idx, (s_off, e_off) in enumerate(self.md_search_matches):
            s_iter = self.md_buffer.get_iter_at_offset(s_off)
            e_iter = self.md_buffer.get_iter_at_offset(e_off)
            tag = "md-search-current" if idx == self.md_search_idx else "md-search-match"
            self.md_buffer.apply_tag_by_name(tag, s_iter, e_iter)
            if idx == self.md_search_idx and scroll_to_current:
                self.md_textview.scroll_to_iter(s_iter, 0.15, False, 0.0, 0.5)

        self.md_search_count_lbl.set_label(f"{self.md_search_idx + 1} of {total}")

    def _step_md_search(self, delta: int):
        total = len(self.md_search_matches)
        if total == 0:
            return
        self.md_search_idx = (self.md_search_idx + delta) % total
        self._highlight_md_search_matches(scroll_to_current=True)

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
        if self.current_file_type != "md":
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
            ("undo", self.on_undo_activated),
            ("redo", self.on_redo_activated),
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

    def on_find_shortcut(self, *args):
        if self.root_stack.get_visible_child_name() == "collection":
            self.search_btn.set_active(not self.search_btn.get_active())
        elif self.root_stack.get_visible_child_name() == "viewer" and self.current_file_type == "md":
            self.md_find_btn.set_active(not self.md_find_btn.get_active())

    def on_search_toggled(self, btn):
        active = btn.get_active()
        self.search_bar.set_search_mode(active)
        if active:
            self.search_entry.grab_focus()
        else:
            self.search_entry.set_text("")

    def on_search_changed(self, entry):
        self._apply_collection_filters()

    def on_toggle_view_mode_action(self, *args):
        if self.current_file_type == "md":
            if self.md_view_mode == "edit":
                self.md_split_btn.set_active(True)
            elif self.md_view_mode == "split":
                self.md_preview_btn.set_active(True)
            else:
                self.md_edit_btn.set_active(True)
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
        self.md_find_btn.set_active(False)
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
            self.md_buffer.begin_irreversible_action()
            self.md_buffer.set_text(self.markdown_content)
            self.md_buffer.end_irreversible_action()
            self._loading_md_buffer = False
            self.md_inline_styler.refresh()

            self._on_canvas_modified(False)
            self._sync_md_undo_redo_buttons()

            MarkdownRenderer.populate_box(
                self.md_paper_box, self.markdown_content, self.canvas.zoom_level
            )

            self.md_edit_btn.set_active(True)
            self._apply_md_preview_layout_style("edit")
            self.md_edit_scrolled.set_visible(True)
            self.md_scrolled.set_visible(False)

            self.window_title.set_title(os.path.splitext(os.path.basename(path))[0])
            self.window_title.set_subtitle("")
            self.viewer_stack.set_visible_child_name("markdown_doc")
            self.page_sidebar_btn.set_visible(False)
            self.mode_box.set_visible(False)
            self.stylus_bar.set_visible(False)
            self.md_toolbar_bar.set_visible(True)
            self.save_btn.set_visible(True)
            self.export_pdf_btn.set_visible(False)
            self.history_box.set_visible(True)
            self.zoom_reset_btn.set_label("100%")
            self.root_stack.set_visible_child_name("viewer")
            self.md_textview.grab_focus()
        except Exception as e:
            self.show_error(f"Failed to load Markdown file {os.path.basename(path)}: {e}")

    def _load_xnote_document(self, path, ext):
        try:
            self.md_find_btn.set_active(False)
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
                if self.md_view_mode == "preview":
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
