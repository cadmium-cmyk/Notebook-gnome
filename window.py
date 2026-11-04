import os
import re
import math
import json
from concurrent.futures import ThreadPoolExecutor
import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, Gio, GLib, Gdk, GObject

from xnote_io import XNotePackageParser
from canvas_editor import XNoteCanvas
from markdown_renderer import MarkdownRenderer, MarkdownInlineStyler

from library_manager import LibraryManagerMixin
from page_sidebar import PageSidebarMixin
from template_switcher import TemplateSwitcherMixin
from document_actions import DocumentActionsMixin


TOOLS_WITH_SETTINGS = {
    "speed", "velocity_pen", "pen", "calligraphy", "dashed", "taper", "shape", "highlighter", "eraser", "text"
}


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

        self.icons_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icons", "svg")
        self.icons_root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "icons")
        self.icon_theme = Gtk.IconTheme.get_for_display(Gdk.Display.get_default())
        if self.icons_root not in self.icon_theme.get_search_path():
            self.icon_theme.add_search_path(self.icons_root)
        if os.path.isdir(self.icons_dir) and self.icons_dir not in self.icon_theme.get_search_path():
            self.icon_theme.add_search_path(self.icons_dir)

        self.theme_mode = "system"
        Adw.StyleManager.get_default().connect(
            "notify::dark", lambda sm, _: self._on_style_dark_changed(sm.get_dark())
        )

        self.current_file_path = None
        self.current_file_type = None  # "xnote", "xcanvas", "xtemplate", or "md"
        self.markdown_content = ""
        self.md_view_mode = "edit"  # Show editor view by default, not split pane
        self._loading_md_buffer = False
        self._syncing_md_scroll = False
        self.md_search_matches = []
        self.md_search_idx = -1
        self._md_mode_animation = None

        self.current_root_folder = None
        self.active_notebook_filter = "ALL"
        self.active_subfolder_filter = None
        self.notebooks_data = {}

        # Library display & sort state
        self.library_view_mode = "grid"   # "grid" or "list"
        self.library_sort_mode = "name_asc"  # "name_asc", "name_desc", "date_desc", "date_asc", "notebook", "type"
        self.show_markdown_files = True

        self.is_modified = False
        self._force_closing = False
        self._updating_scroll = False
        self._updating_template_ui = False
        self._updating_page_sidebar = False
        self._page_thumb_draw_areas = []

        # Async thumbnail & spinner management
        self._thumb_render_generation = 0
        self._thumb_idle_source_id = 0
        self._thumb_dirty_debounce_id = 0
        self._thumb_pending_queue = []
        self._thumb_spinner_widgets = {}
        self._library_thumb_cache = {}  # Maps (filepath, mtime) -> cairo.ImageSurface
        self._library_card_spinners = {}

        self.executor = ThreadPoolExecutor(max_workers=4)
        self._folder_load_id = 0

        self.connect("close-request", self._on_window_close_request)

        css_provider = Gtk.CssProvider()
        css_provider.load_from_data(b"""
            .page-badge {
                padding: 6px 14px;
                border-radius: 999px;
                opacity: 0.0;
                transition: opacity 400ms cubic-bezier(0.25, 1, 0.5, 1);
            }
            .page-badge.visible {
                opacity: 1.0;
            }
            .selection-toast {
                background-color: #505053;
                color: #ffffff;
                border-radius: 12px;
                box-shadow: 0 1px 3px 1px rgba(0, 0, 0, 0.18), 0 2px 6px 2px rgba(0, 0, 0, 0.12);
                padding: 4px 6px;
                border: none;
            }
            .selection-toast button {
                border-radius: 8px;
                color: #ffffff;
                background: transparent;
                border: none;
                padding: 6px 8px;
                margin: 0 1px;
            }
            .selection-toast button:hover {
                background-color: rgba(255, 255, 255, 0.15);
            }
            .selection-toast button:active,
            .selection-toast button:checked {
                background-color: rgba(255, 255, 255, 0.25);
            }
            .selection-toast button.destructive-action {
                color: #ff7b72;
            }
            .selection-toast button.destructive-action:hover {
                background-color: rgba(255, 123, 114, 0.25);
            }
            /* Default GNOME icon styling */
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
            .category-flowbox flowboxchild {
                padding: 0;
                margin: 0;
                background: none;
                border: none;
                box-shadow: none;
            }
            .category-flowbox flowboxchild:hover,
            .category-flowbox flowboxchild:selected {
                background: none;
            }
            .category-folder-btn {
                padding: 6px 14px;
                font-size: 13px;
                font-weight: 500;
            }
            .seamless-toolbar-container {
                background-color: @theme_bg_color;
            }
            .stylus-toolbar {
                padding: 6px 12px;
                background-color: @theme_bg_color;
                border-bottom: 1px solid alpha(@borders, 0.55);
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
            .thumb-spinner-placeholder {
                background-color: alpha(@view_fg_color, 0.04);
                border-radius: 8px;
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
                background-color: @view_bg_color;
                color: @view_fg_color;
            }
            .md-editor-view text {
                background-color: @view_bg_color;
                color: @view_fg_color;
            }
            .md-preview-scroll {
                background-color: @window_bg_color;
                border-left: 1px solid alpha(@borders, 0.55);
            }
            .md-split-preview-box {
                background-color: transparent;
                color: @view_fg_color;
                padding: 32px 36px;
            }
            .md-paper {
                background-color: @card_bg_color;
                color: @card_fg_color;
                border-radius: 12px;
                padding: 36px 44px;
                border: 1px solid alpha(@borders, 0.45);
                box-shadow: 0 2px 8px rgba(0, 0, 0, 0.08);
            }
            .md-heading-rule {
                background-color: alpha(@borders, 0.65);
                min-height: 1px;
            }
            .md-code-block {
                background-color: alpha(@view_fg_color, 0.07);
                color: @view_fg_color;
                border-radius: 8px;
                padding: 12px 16px;
                border: 1px solid alpha(@borders, 0.35);
            }
            .md-blockquote {
                border-left: 4px solid @accent_bg_color;
                padding-left: 12px;
                background-color: alpha(@accent_bg_color, 0.08);
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
        self.root_stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)
        self.root_stack.set_transition_duration(250)
        self.toast_overlay.set_child(self.root_stack)

        # =====================================================================
        # 1. COLLECTION VIEW
        # =====================================================================
        sidebar_toolbar = Adw.ToolbarView()
        sidebar_header = Adw.HeaderBar()
        sidebar_header.set_show_end_title_buttons(False)
        sidebar_title = Adw.WindowTitle(title="Notebook", subtitle="")
        sidebar_header.set_title_widget(sidebar_title)

        # Hamburger Menu on headerbar with New, Import, Show Markdown Files, Preferences, Keyboard Shortcuts, About, Quit
        sidebar_menu = Gio.Menu()

        # 1. New (Submenu)
        new_submenu = Gio.Menu()
        new_submenu.append("New Notebook", "win.new-notebook")
        new_submenu.append("New Infinite Canvas", "win.new-canvas")
        new_submenu.append("New Text Note", "win.new-markdown")
        new_submenu.append("New Category", "win.new-category")
        sidebar_menu.append_submenu("New", new_submenu)

        # 2. Import (Submenu)
        import_submenu = Gio.Menu()
        import_submenu.append("Import from PDF…", "win.import-pdf-new")
        import_submenu.append("Import from Rnote…", "win.import-rnote-new")
        sidebar_menu.append_submenu("Import", import_submenu)

        # 3. Show Markdown Files (Check item in its own section)
        md_section = Gio.Menu()
        md_section.append("Show Markdown Files", "win.show-markdown")
        sidebar_menu.append_section(None, md_section)

        # 4, 5, 6. Preferences, Keyboard Shortcuts, About
        pref_section = Gio.Menu()
        pref_section.append("Preferences", "win.preferences")
        pref_section.append("Keyboard Shortcuts", "win.shortcuts")
        pref_section.append("About", "win.about")
        sidebar_menu.append_section(None, pref_section)

        # 7. Quit
        quit_section = Gio.Menu()
        quit_section.append("Quit", "app.quit")
        sidebar_menu.append_section(None, quit_section)
        self.sidebar_menu = sidebar_menu
        # Hamburger menu is always on far right of main headerbar (content_header)
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
        title_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6, halign=Gtk.Align.CENTER)
        self.collection_title = Adw.WindowTitle(title="", subtitle="")
        title_box.append(self.collection_title)

        self.category_edit_btn = Gtk.MenuButton(
            icon_name=self._resolve_icon_name("document-edit-symbolic", "document-edit-symbolic"),
            tooltip_text="Edit Category",
            valign=Gtk.Align.CENTER
        )
        self.category_edit_btn.add_css_class("flat")
        self.category_edit_btn.add_css_class("circular")
        self.category_edit_btn.set_visible(False)
        title_box.append(self.category_edit_btn)

        content_header.set_title_widget(title_box)

        self.library_sidebar_btn = Gtk.ToggleButton(
            icon_name=self._resolve_icon_name("sidebar", "sidebar-show-symbolic"),
            active=False,
            visible=False,
            tooltip_text="Toggle Categories Sidebar (F9)"
        )
        self.library_sidebar_btn.connect("toggled", self.on_library_sidebar_toggled)
        content_header.pack_start(self.library_sidebar_btn)

        self.search_btn = Gtk.ToggleButton(
            icon_name="system-search-symbolic",
            tooltip_text="Filter Notes (Ctrl+F)"
        )
        self.search_btn.connect("toggled", self.on_search_toggled)
        content_header.pack_start(self.search_btn)

        self.open_btn = Gtk.Button(
            icon_name="folder-open-symbolic",
            tooltip_text="Open Collection Folder (Ctrl+O)"
        )
        self.open_btn.set_action_name("win.open")

        new_menu = Gio.Menu()
        new_menu.append("New Notebook", "win.new-notebook")
        new_menu.append("New Infinite Canvas", "win.new-canvas")
        new_menu.append("Import from PDF", "win.import-pdf-new")
        new_menu.append("Import from Rnote", "win.import-rnote-new")
        new_menu.append("New Text Note", "win.new-markdown")
        new_menu.append("New Category", "win.new-category")
        self.new_note_btn = Gtk.MenuButton(
            icon_name="document-new-symbolic",
            menu_model=new_menu,
            tooltip_text="Create New Notebook, Import PDF, Markdown Note, or Category"
        )

        self.content_menu_btn = Gtk.MenuButton(
            icon_name="open-menu-symbolic",
            menu_model=sidebar_menu,
            tooltip_text="Main Menu",
            visible=True
        )
        content_header.pack_end(self.content_menu_btn)

        sort_menu = Gio.Menu()
        sort_section = Gio.Menu()
        sort_section.append("Name (A → Z)", "win.sort-library('name_asc')")
        sort_section.append("Name (Z → A)", "win.sort-library('name_desc')")
        sort_section.append("Date Modified (Newest)", "win.sort-library('date_desc')")
        sort_section.append("Date Modified (Oldest)", "win.sort-library('date_asc')")
        sort_section.append("Category Folder", "win.sort-library('notebook')")
        sort_section.append("File Type (.xnote, .md)", "win.sort-library('type')")
        sort_menu.append_section(None, sort_section)

        filter_section = Gio.Menu()
        filter_section.append("Show Markdown Files", "win.show-markdown")
        sort_menu.append_section(None, filter_section)

        self.sort_menu_btn = Gtk.MenuButton(
            icon_name=self._resolve_icon_name("view-sort-descending-symbolic", "content-loading-symbolic"),
            menu_model=sort_menu,
            tooltip_text="Sort & Filter Library Notes"
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

        self.content_stack = Gtk.Stack(hexpand=True, vexpand=True)
        self.content_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        content_toolbar.set_content(self.content_stack)

        status_page = Adw.StatusPage(
            icon_name=self._resolve_icon_name("library-symbolic", "folder-documents-symbolic"),
            title="Notebook",
            description="Create a new notebook or import existing files to get started"
        )
        status_btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12, halign=Gtk.Align.CENTER)

        status_new_btn = Gtk.Button(label="New Notebook")
        status_new_btn.add_css_class("pill")
        status_new_btn.add_css_class("suggested-action")
        status_new_btn.set_action_name("win.new-notebook")
        status_btn_box.append(status_new_btn)

        import_menu = Gio.Menu()
        import_menu.append("Import PDF as .xnote…", "win.import-pdf-new")
        import_menu.append("Import Rnote as .xnote…", "win.import-rnote-new")
        import_menu.append("Open Collection Folder…", "win.open")

        status_import_btn = Gtk.MenuButton(
            label="Import",
            menu_model=import_menu,
            tooltip_text="Import files or open folder"
        )
        status_import_btn.add_css_class("pill")
        status_btn_box.append(status_import_btn)

        status_page.set_child(status_btn_box)
        self.content_stack.add_named(status_page, "empty")

        # Empty state prompt for All Notes (Library) view
        self.empty_notes_page = Adw.StatusPage(
            icon_name=self._resolve_icon_name("document-new-symbolic", "document-new-symbolic"),
            title="No Notes",
            description="Create a new note to get started"
        )
        empty_notes_box = Gtk.FlowBox(
            orientation=Gtk.Orientation.HORIZONTAL,
            halign=Gtk.Align.CENTER,
            max_children_per_line=5,
            selection_mode=Gtk.SelectionMode.NONE,
            column_spacing=12,
            row_spacing=12
        )

        btn_new_note = Gtk.Button(label="New Notebook")
        btn_new_note.add_css_class("pill")
        btn_new_note.add_css_class("suggested-action")
        btn_new_note.set_action_name("win.new-notebook")
        empty_notes_box.append(btn_new_note)

        btn_new_canvas = Gtk.Button(label="New Infinite Canvas")
        btn_new_canvas.add_css_class("pill")
        btn_new_canvas.set_action_name("win.new-canvas")
        empty_notes_box.append(btn_new_canvas)

        btn_new_md = Gtk.Button(label="New Text Note")
        btn_new_md.add_css_class("pill")
        btn_new_md.set_action_name("win.new-markdown")
        empty_notes_box.append(btn_new_md)

        btn_import_pdf = Gtk.Button(label="Import PDF")
        btn_import_pdf.add_css_class("pill")
        btn_import_pdf.set_action_name("win.import-pdf-new")
        empty_notes_box.append(btn_import_pdf)

        btn_import_rnote = Gtk.Button(label="Import Rnote")
        btn_import_rnote.add_css_class("pill")
        btn_import_rnote.set_action_name("win.import-rnote-new")
        empty_notes_box.append(btn_import_rnote)

        self.empty_notes_page.set_child(empty_notes_box)
        self.content_stack.add_named(self.empty_notes_page, "empty_notes")

        # Empty state prompt for Canvases view
        self.empty_canvases_page = Adw.StatusPage(
            icon_name=self._resolve_icon_name("draw-freehand-symbolic", "draw-freehand-symbolic"),
            title="No Canvases",
            description="Create an infinite canvas for sketching, diagrams, and notes"
        )
        empty_canvases_box = Gtk.FlowBox(
            orientation=Gtk.Orientation.HORIZONTAL,
            halign=Gtk.Align.CENTER,
            max_children_per_line=3,
            selection_mode=Gtk.SelectionMode.NONE,
            column_spacing=12,
            row_spacing=12
        )

        btn_create_canvas = Gtk.Button(label="New Infinite Canvas")
        btn_create_canvas.add_css_class("pill")
        btn_create_canvas.add_css_class("suggested-action")
        btn_create_canvas.set_action_name("win.new-canvas")
        empty_canvases_box.append(btn_create_canvas)

        self.empty_canvases_page.set_child(empty_canvases_box)
        self.content_stack.add_named(self.empty_canvases_page, "empty_canvases")

        # Empty state prompt for Category view
        self.empty_category_page = Adw.StatusPage(
            icon_name=self._resolve_icon_name("feather-tag-symbolic", "folder-symbolic"),
            title="No Notes in Category",
            description="Create a note or canvas in this category"
        )
        empty_cat_box = Gtk.FlowBox(
            orientation=Gtk.Orientation.HORIZONTAL,
            halign=Gtk.Align.CENTER,
            max_children_per_line=3,
            selection_mode=Gtk.SelectionMode.NONE,
            column_spacing=12,
            row_spacing=12
        )

        btn_cat_note = Gtk.Button(label="New Notebook")
        btn_cat_note.add_css_class("pill")
        btn_cat_note.add_css_class("suggested-action")
        btn_cat_note.set_action_name("win.new-notebook")
        empty_cat_box.append(btn_cat_note)

        btn_cat_canvas = Gtk.Button(label="New Infinite Canvas")
        btn_cat_canvas.add_css_class("pill")
        btn_cat_canvas.set_action_name("win.new-canvas")
        empty_cat_box.append(btn_cat_canvas)

        btn_cat_back = Gtk.Button(label="Back to Library")
        btn_cat_back.add_css_class("pill")
        btn_cat_back.connect("clicked", lambda *_: self._select_category("ALL"))
        empty_cat_box.append(btn_cat_back)

        self.empty_category_page.set_child(empty_cat_box)
        self.content_stack.add_named(self.empty_category_page, "empty_category")

        # Empty state prompt for Search view
        self.empty_search_page = Adw.StatusPage(
            icon_name=self._resolve_icon_name("system-search-symbolic", "system-search-symbolic"),
            title="No Matching Notes",
            description="Try a different search term"
        )
        self.content_stack.add_named(self.empty_search_page, "empty_search")

        # 1a. Library Cover Grid View
        self.grid_scrolled = Gtk.ScrolledWindow(hexpand=True, vexpand=True)
        self.grid_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            hexpand=True,
            vexpand=True
        )

        self.category_flowbox_grid = Gtk.FlowBox(
            valign=Gtk.Align.START,
            halign=Gtk.Align.START,
            column_spacing=10,
            row_spacing=10,
            margin_start=28,
            margin_end=28,
            margin_top=20,
            margin_bottom=4,
            selection_mode=Gtk.SelectionMode.NONE
        )
        self.category_flowbox_grid.add_css_class("category-flowbox")
        self.category_flowbox_grid.set_visible(False)
        self.grid_box.append(self.category_flowbox_grid)

        self.library_flowbox = Gtk.FlowBox(
            valign=Gtk.Align.START,
            halign=Gtk.Align.START,
            column_spacing=24,
            row_spacing=24,
            margin_start=28,
            margin_end=28,
            margin_top=16,
            margin_bottom=28,
            homogeneous=False,
            selection_mode=Gtk.SelectionMode.NONE
        )
        self.library_flowbox.set_sort_func(self._flowbox_sort_func)
        self.grid_box.append(self.library_flowbox)
        self.grid_scrolled.set_child(self.grid_box)
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
        self.category_flowbox_list = Gtk.FlowBox(
            valign=Gtk.Align.START,
            halign=Gtk.Align.START,
            column_spacing=10,
            row_spacing=10,
            margin_bottom=14,
            selection_mode=Gtk.SelectionMode.NONE
        )
        self.category_flowbox_list.add_css_class("category-flowbox")
        self.category_flowbox_list.set_visible(False)
        list_outer.append(self.category_flowbox_list)

        self.library_listbox = Gtk.ListBox()
        self.library_listbox.add_css_class("boxed-list")
        self.library_listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        self.library_listbox.set_activate_on_single_click(True)
        self.library_listbox.set_sort_func(self._listbox_sort_func)
        self.library_listbox.connect("row-activated", self._on_library_list_row_activated)
        list_outer.append(self.library_listbox)
        self.list_scrolled.set_child(list_outer)
        self.content_stack.add_named(self.list_scrolled, "list")

        # 1c. Library Loading Spinner View
        lib_loading_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=14,
            halign=Gtk.Align.CENTER,
            valign=Gtk.Align.CENTER,
            hexpand=True,
            vexpand=True
        )
        self.library_loading_spinner = self.create_adw_spinner(size=40)
        lib_loading_lbl = Gtk.Label(label="Loading notebook thumbnails…")
        lib_loading_lbl.add_css_class("dim-label")
        lib_loading_box.append(self.library_loading_spinner)
        lib_loading_box.append(lib_loading_lbl)
        self.content_stack.add_named(lib_loading_box, "loading")

        self.content_stack.set_visible_child_name("empty")

        if hasattr(Adw, "OverlaySplitView"):
            self.split_view = Adw.OverlaySplitView()
            self.split_view.set_min_sidebar_width(200)
            self.split_view.set_max_sidebar_width(250)
            self.split_view.set_sidebar_width_fraction(0.22)
            self.split_view.set_pin_sidebar(True)
            self.split_view.set_show_sidebar(False)
            self.split_view.set_sidebar(sidebar_toolbar)
            self.split_view.set_content(content_toolbar)
            if hasattr(self, "library_sidebar_btn"):
                self.split_view.connect(
                    "notify::show-sidebar",
                    lambda sv, _: self.library_sidebar_btn.set_active(sv.get_show_sidebar())
                    if hasattr(self, "library_sidebar_btn") and self.library_sidebar_btn.get_active() != sv.get_show_sidebar() else None
                )
        elif hasattr(Adw, "NavigationSplitView"):
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
            icon_name=self._resolve_icon_name("prev", "go-previous-symbolic"),
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
        self.page_sidebar_btn.connect("toggled", self._on_page_sidebar_toggled_optimized)
        viewer_header.pack_start(self.page_sidebar_btn)

        save_menu = Gio.Menu()
        save_menu.append("Save", "win.save")
        save_menu.append("Save As..", "win.save-as")
        save_menu.append("Export…", "win.export")

        self.save_btn = Gtk.MenuButton(
            icon_name="document-save-symbolic",
            menu_model=save_menu,
            tooltip_text="Save, Save As, or Export"
        )

        self.export_pdf_btn = Gtk.Button(
            icon_name="document-send-symbolic",
            tooltip_text="Export to PDF…",
            visible=False
        )
        self.export_pdf_btn.connect("clicked", self.on_export_pdf_clicked)

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

        # Clipboard controls (Cut / Copy / Paste) — live in the toolbar
        self.clipboard_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self.clipboard_box.add_css_class("linked")
        for icon, tip, act in (
            ("edit-cut-symbolic", "Cut (Ctrl+X)", "win.cut"),
            ("edit-copy-symbolic", "Copy (Ctrl+C)", "win.copy"),
            ("edit-paste-symbolic", "Paste (Ctrl+V)", "win.paste"),
        ):
            cb_btn = Gtk.Button(icon_name=icon, tooltip_text=tip, action_name=act)
            self.clipboard_box.append(cb_btn)

        # External App & Export PDF Box (Synced with window_2.py)
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
        # self.ext_open_box.append(self.open_default_btn)

        self.open_with_btn = Gtk.Button(
            icon_name="applications-other-symbolic",
            tooltip_text="Select Application to Open With…"
        )
        self.open_with_btn.connect(
            "clicked", lambda *_: self.choose_app_for_file(self.current_file_path)
        )
        # self.ext_open_box.append(self.open_with_btn)
        #viewer_header.pack_end(self.ext_open_box)

        # Zoom controls (Zoom Out, Page Fit Pop up / Reset, Zoom In)
        self.zoom_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self.zoom_box.add_css_class("linked")

        self.zoom_out_btn = Gtk.Button(
            icon_name=self._resolve_icon_name("zoom-out-symbolic"),
            tooltip_text="Zoom Out (Ctrl+-)"
        )
        self.zoom_out_btn.connect("clicked", lambda *_: self.set_zoom(self.canvas.zoom_level - 0.25))
        self.zoom_box.append(self.zoom_out_btn)

        fit_menu = Gio.Menu()
        fit_menu.append("Fit Page Width", "win.fit-width")
        fit_menu.append("Fit Page Height", "win.fit-height")
        fit_menu.append("Reset Zoom", "win.zoom-reset")
        fit_menu.append("Lock Zoom", "win.toggle-zoom-lock")
        fit_menu.append("Double Page View", "win.toggle-double-page")

        self.zoom_reset_btn = Gtk.MenuButton(
            label="100%",
            menu_model=fit_menu,
            tooltip_text="Page Fit & Zoom Options"
        )
        if hasattr(self.zoom_reset_btn, "set_always_show_arrow"):
            self.zoom_reset_btn.set_always_show_arrow(False)
        self.zoom_box.append(self.zoom_reset_btn)

        self.zoom_in_btn = Gtk.Button(
            icon_name=self._resolve_icon_name("zoom-in-symbolic"),
            tooltip_text="Zoom In (Ctrl++)"
        )
        self.zoom_in_btn.connect("clicked", lambda *_: self.set_zoom(self.canvas.zoom_level + 0.25))
        self.zoom_box.append(self.zoom_in_btn)

        self.zoom_lock_btn = Gtk.ToggleButton(
            icon_name=self._resolve_icon_name("unlock-symbolic"),
            tooltip_text="Lock Zoom (Ctrl+L)"
        )
        self.zoom_lock_btn.connect("toggled", self._on_zoom_lock_toggled)
        self.zoom_box.append(self.zoom_lock_btn)


        # Markdown View Mode Switcher (Editor, Split, Preview) on headerbar to the left of zoom controls
        self.md_mode_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self.md_mode_box.add_css_class("linked")
        self.md_mode_box.set_visible(False)

        self.md_edit_btn = Gtk.ToggleButton(
            icon_name=self._resolve_icon_name("document-edit-symbolic", "accessories-text-editor-symbolic"),
            active=True,
            tooltip_text="Markdown Editor View (Ctrl+M)"
        )
        self.md_edit_btn.connect("toggled", self._on_md_mode_toggled, "edit")
        self.md_mode_box.append(self.md_edit_btn)

        self.md_split_btn = Gtk.ToggleButton(
            icon_name="view-dual-symbolic",
            group=self.md_edit_btn,
            tooltip_text="Side-by-Side Split View (Ctrl+M)"
        )
        self.md_split_btn.connect("toggled", self._on_md_mode_toggled, "split")
        self.md_mode_box.append(self.md_split_btn)

        self.md_preview_btn = Gtk.ToggleButton(
            icon_name="view-reveal-symbolic",
            group=self.md_edit_btn,
            tooltip_text="Formatted Paper Preview (Ctrl+M)"
        )
        self.md_preview_btn.connect("toggled", self._on_md_mode_toggled, "preview")
        self.md_mode_box.append(self.md_preview_btn)


        # View Mode toggle controls (Single Page vs Scrolled vs Double Page for .xnote)
        self.mode_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self.mode_box.add_css_class("linked")

        self.paged_btn = Gtk.ToggleButton(
            icon_name="view-paged-symbolic",
            tooltip_text="Single Page View (Ctrl+M)"
        )
        self.paged_btn.set_active(True)
        self.paged_btn.connect("toggled", self.on_view_mode_toggled, "paged")
        self.mode_box.append(self.paged_btn)

        self.scrolled_btn = Gtk.ToggleButton(
            icon_name="view-continuous-symbolic",
            tooltip_text="Continuous Scrolled View (Ctrl+M)",
            group=self.paged_btn
        )
        self.scrolled_btn.connect("toggled", self.on_view_mode_toggled, "scrolled")
        self.mode_box.append(self.scrolled_btn)

        self.double_page_btn = Gtk.ToggleButton(
            icon_name="view-dual-symbolic",
            tooltip_text="Double Page View (Ctrl+D)",
            group=self.paged_btn
        )
        self.double_page_btn.connect("toggled", self.on_view_mode_toggled, "double")
        self.mode_box.append(self.double_page_btn)



        self.template_popover = self._build_template_switcher_popover()
        self.template_menu_btn = Gtk.MenuButton(
            icon_name="view-grid-symbolic",
            popover=self.template_popover,
            tooltip_text="Change Page Template, Paper Colour and Rule Spacing…"
        )

        self.viewer_menu_btn = Gtk.MenuButton(
            icon_name="open-menu-symbolic",
            menu_model=sidebar_menu,
            tooltip_text="Main Menu"
        )
        viewer_header.pack_end(self.viewer_menu_btn)

        # =====================================================================
        # 2a. STYLUS BAR USING DEDICATED tool-* ICONS
        # =====================================================================
        self.stylus_bar = Gtk.CenterBox(
            halign=Gtk.Align.FILL
        )
        self.stylus_bar.add_css_class("stylus-toolbar")

        start_tools_box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=6,
            halign=Gtk.Align.START
        )
        # Shared document slot (Save, Undo/Redo, Cut/Copy/Paste) — reparented between toolbars
        self.stylus_doc_slot = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.stylus_doc_slot.append(self.save_btn)
        self.stylus_doc_slot.append(self.history_box)
        self.stylus_doc_slot.append(self.clipboard_box)
        start_tools_box.append(self.stylus_doc_slot)

        # Page tools (Add Page, Page Template)
        self.page_tools_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self.page_tools_box.add_css_class("linked")
        self.add_page_btn = Gtk.Button(
            icon_name=self._resolve_icon_name("new-page-symbolic"),
            tooltip_text="Add New Page After Last Page (Ctrl+N)"
        )
        self.add_page_btn.connect("clicked", self.on_add_page_clicked)
        self.page_tools_box.append(self.add_page_btn)
        self.page_tools_box.append(self.template_menu_btn)
        start_tools_box.append(self.page_tools_box)
        self.stylus_bar.set_start_widget(start_tools_box)

        # End: View types + Zoom tools
        end_tools_box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=6,
            halign=Gtk.Align.END
        )
        end_tools_box.append(self.mode_box)
        self.stylus_zoom_slot = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self.stylus_zoom_slot.append(self.zoom_box)
        end_tools_box.append(self.stylus_zoom_slot)
        self.stylus_bar.set_end_widget(end_tools_box)

        center_tools_box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL,
            spacing=10,
            halign=Gtk.Align.CENTER,
            margin_start=10,
            margin_end=10
        )
        self.center_tools_box = center_tools_box

        tool_group_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        tool_group_box.add_css_class("linked")

        self.select_tool_btn = Gtk.ToggleButton(
            icon_name=self._resolve_icon_name("tool-pointer-symbolic"),
            tooltip_text="Select, Move, Resize & Rotate Images"
        )
        self.select_tool_btn.connect("toggled", self._on_tool_toggled, "select")
        tool_group_box.append(self.select_tool_btn)

        self.lasso_tool_btn = Gtk.ToggleButton(
            icon_name=self._resolve_icon_name("tool-lasso-symbolic"),
            group=self.select_tool_btn,
            tooltip_text="Freehand Lasso Select (Move, Scale & Recolor Strokes)"
        )
        self.lasso_tool_btn.connect("toggled", self._on_tool_toggled, "lasso")
        tool_group_box.append(self.lasso_tool_btn)

        self.pen_tool_btn = Gtk.ToggleButton(
            icon_name=self._resolve_icon_name("tool-pen-symbolic"),
            group=self.select_tool_btn,
            active=True,
            tooltip_text="Standard Pressure Pen"
        )
        self.pen_tool_btn.connect("toggled", self._on_tool_toggled, "pen")
        tool_group_box.append(self.pen_tool_btn)

        self.dashed_tool_btn = Gtk.ToggleButton(
            icon_name=self._resolve_icon_name("tool-dashed-symbolic"),
            group=self.select_tool_btn,
            tooltip_text="Dashed Line Pen"
        )
        self.dashed_tool_btn.connect("toggled", self._on_tool_toggled, "dashed")
        tool_group_box.append(self.dashed_tool_btn)

        self.callig_tool_btn = Gtk.ToggleButton(
            icon_name=self._resolve_icon_name("tool-calligraphy-symbolic"),
            group=self.select_tool_btn,
            tooltip_text="Calligraphic Chisel-Nib Pen (Pressure + Direction)"
        )
        self.callig_tool_btn.connect("toggled", self._on_tool_toggled, "calligraphy")
        tool_group_box.append(self.callig_tool_btn)

        self.vel_pen_tool_btn = Gtk.ToggleButton(
            icon_name=self._resolve_icon_name("tool-fountain-pen-symbolic"),
            group=self.select_tool_btn,
            tooltip_text="Velocity / Speed Pen (Fast Strokes Taper Thin)"
        )
        self.vel_pen_tool_btn.connect("toggled", self._on_tool_toggled, "speed")
        tool_group_box.append(self.vel_pen_tool_btn)

        self.taper_tool_btn = Gtk.ToggleButton(
            icon_name=self._resolve_icon_name("tool-taper-symbolic"),
            group=self.select_tool_btn,
            tooltip_text="Tapered Brush Pen"
        )
        self.taper_tool_btn.connect("toggled", self._on_tool_toggled, "taper")
        tool_group_box.append(self.taper_tool_btn)

        self.shape_tool_btn = Gtk.ToggleButton(
            icon_name=self._resolve_icon_name("shape-symbolic"),
            group=self.select_tool_btn,
            tooltip_text="Shapes Tool (Rectangle, Circle, Line, Arrow, Triangle, Star)"
        )
        self.shape_tool_btn.connect("toggled", self._on_tool_toggled, "shape")
        tool_group_box.append(self.shape_tool_btn)

        self.hi_tool_btn = Gtk.ToggleButton(
            icon_name=self._resolve_icon_name("tool-highlighter-symbolic"),
            group=self.select_tool_btn,
            tooltip_text="Translucent Highlighter"
        )
        self.hi_tool_btn.connect("toggled", self._on_tool_toggled, "highlighter")
        tool_group_box.append(self.hi_tool_btn)

        self.eraser_tool_btn = Gtk.ToggleButton(
            icon_name=self._resolve_icon_name("tool-eraser-symbolic"),
            group=self.select_tool_btn,
            tooltip_text="Stroke & Text Eraser (or use Stylus Eraser Tip)"
        )
        self.eraser_tool_btn.connect("toggled", self._on_tool_toggled, "eraser")
        tool_group_box.append(self.eraser_tool_btn)

        self.text_tool_btn = Gtk.ToggleButton(
            icon_name=self._resolve_icon_name("tool-text-symbolic"),
            group=self.select_tool_btn,
            tooltip_text="Insert Text Box on Page"
        )
        self.text_tool_btn.connect("toggled", self._on_tool_toggled, "text")
        tool_group_box.append(self.text_tool_btn)

        self.tool_buttons = {
            "select": self.select_tool_btn,
            "lasso": self.lasso_tool_btn,
            "pen": self.pen_tool_btn,
            "dashed": self.dashed_tool_btn,
            "calligraphy": self.callig_tool_btn,
            "speed": self.vel_pen_tool_btn,
            "velocity_pen": self.vel_pen_tool_btn,
            "taper": self.taper_tool_btn,
            "shape": self.shape_tool_btn,
            "highlighter": self.hi_tool_btn,
            "eraser": self.eraser_tool_btn,
            "text": self.text_tool_btn,
        }

        for btn, tname in [
            (self.select_tool_btn, "select"),
            (self.lasso_tool_btn, "lasso"),
            (self.pen_tool_btn, "pen"),
            (self.dashed_tool_btn, "dashed"),
            (self.callig_tool_btn, "calligraphy"),
            (self.vel_pen_tool_btn, "speed"),
            (self.taper_tool_btn, "taper"),
            (self.shape_tool_btn, "shape"),
            (self.hi_tool_btn, "highlighter"),
            (self.eraser_tool_btn, "eraser"),
            (self.text_tool_btn, "text"),
        ]:
            click = Gtk.GestureClick()
            click.set_button(0)
            click.connect("pressed", self._on_tool_btn_pressed, tname, btn)
            click.connect("released", self._on_tool_btn_released, tname, btn)
            btn.add_controller(click)

        center_tools_box.append(tool_group_box)

        insert_media_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        insert_media_box.add_css_class("linked")
        self.insert_media_box = insert_media_box

        self.insert_img_btn = Gtk.Button(
            icon_name=self._resolve_icon_name("insert-image-symbolic"),
            tooltip_text="Import Image onto Current Page…"
        )
        self.insert_img_btn.connect("clicked", self.on_insert_image_clicked)
        insert_media_box.append(self.insert_img_btn)

        self.import_pdf_btn = Gtk.Button(
            icon_name=self._resolve_icon_name("x-office-document-symbolic", "x-office-document-symbolic"),
            tooltip_text="Import PDF Pages into Notebook…"
        )
        self.import_pdf_btn.connect("clicked", self.on_import_pdf_into_current_clicked)
        insert_media_box.append(self.import_pdf_btn)

        self.audio_rec_btn = Gtk.Button(
            icon_name="audio-input-microphone-symbolic",
            tooltip_text="Record Voice Note"
        )
        self.audio_rec_btn.connect("clicked", self._on_record_audio_clicked)
        insert_media_box.append(self.audio_rec_btn)

        self.audio_attach_btn = Gtk.Button(
            icon_name="mail-attachment-symbolic",
            tooltip_text="Attach Audio File…"
        )
        self.audio_attach_btn.connect("clicked", self._on_attach_audio_clicked)
        insert_media_box.append(self.audio_attach_btn)

        center_tools_box.append(insert_media_box)

        # Active recording banner in the stylus toolbar
        self.rec_banner = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.rec_banner.add_css_class("linked")
        self.rec_banner.set_visible(False)

        rec_dot = Gtk.Label(label="🔴")
        self.rec_time_lbl = Gtk.Label(label="00:00")
        self.rec_time_lbl.add_css_class("heading")
        self.stop_rec_btn = Gtk.Button(label="Stop & Insert", icon_name="media-playback-stop-symbolic")
        self.stop_rec_btn.add_css_class("suggested-action")
        self.stop_rec_btn.connect("clicked", self._on_stop_recording_clicked)
        self.cancel_rec_btn = Gtk.Button(label="Cancel", icon_name="window-close-symbolic")
        self.cancel_rec_btn.connect("clicked", self._on_cancel_recording_clicked)

        self.rec_banner.append(rec_dot)
        self.rec_banner.append(self.rec_time_lbl)
        self.rec_banner.append(self.stop_rec_btn)
        self.rec_banner.append(self.cancel_rec_btn)
        center_tools_box.append(self.rec_banner)

        self.color_btn = Gtk.ColorButton(
            tooltip_text="Stroke / Text Ink Color (Also Recolors Lasso Selection)"
        )
        rgba = Gdk.RGBA()
        rgba.parse("rgb(213,68,37)")
        self.color_btn.set_rgba(rgba)
        self.color_btn.connect("color-set", self._on_color_changed)
        center_tools_box.append(self.color_btn)

        # Tool Settings Popover (directly attaches under active tool)
        self.tool_settings_popover = self._build_tool_settings_popover()
        self.tool_settings_popover.set_parent(self.pen_tool_btn)

        # Shape Selection Drop-down List Popover (opens as soon as shape tool is clicked)
        self.shape_popover = self._build_shape_dropdown_popover()
        self.shape_popover.set_parent(self.shape_tool_btn)

        # Contextual Image / Lasso / Item Manipulation Action Overlay Toolbar (styled as a Toast)
        self.img_ops_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2, visible=False)
        self.img_ops_box.add_css_class("selection-toast")
        self.img_ops_box.set_halign(Gtk.Align.CENTER)
        self.img_ops_box.set_valign(Gtk.Align.END)
        self.img_ops_box.set_margin_bottom(24)

        self.aspect_lock_btn = Gtk.ToggleButton(
            icon_name=self._resolve_icon_name("view-pin-symbolic"),
            active=True,
            tooltip_text="Lock Aspect Ratio When Resizing (Hold Shift to Override)"
        )
        self.aspect_lock_btn.connect("toggled", self._on_aspect_lock_toggled)
        self.img_ops_box.append(self.aspect_lock_btn)

        self.reset_aspect_btn = Gtk.Button(
            icon_name=self._resolve_icon_name("view-restore-symbolic"),
            tooltip_text="Reset Image to Native Proportions"
        )
        self.reset_aspect_btn.connect("clicked", lambda *_: self.canvas.reset_selected_image_aspect_ratio())
        self.img_ops_box.append(self.reset_aspect_btn)

        self.rot_l_btn = Gtk.Button(
            icon_name=self._resolve_icon_name("object-rotate-left-symbolic"),
            tooltip_text="Rotate Image 90° Counter-Clockwise"
        )
        self.rot_l_btn.connect("clicked", lambda *_: self.canvas.rotate_selected_image(-math.pi / 2.0))
        self.img_ops_box.append(self.rot_l_btn)

        self.rot_r_btn = Gtk.Button(
            icon_name=self._resolve_icon_name("object-rotate-right-symbolic"),
            tooltip_text="Rotate Image 90° Clockwise"
        )
        self.rot_r_btn.connect("clicked", lambda *_: self.canvas.rotate_selected_image(math.pi / 2.0))
        self.img_ops_box.append(self.rot_r_btn)

        self.flip_h_btn = Gtk.Button(
            icon_name=self._resolve_icon_name("object-flip-horizontal-symbolic"),
            tooltip_text="Flip Image Horizontally"
        )
        self.flip_h_btn.connect("clicked", lambda *_: self.canvas.flip_selected_image(horizontal=True))
        self.img_ops_box.append(self.flip_h_btn)

        self.flip_v_btn = Gtk.Button(
            icon_name=self._resolve_icon_name("object-flip-vertical-symbolic"),
            tooltip_text="Flip Image Vertically"
        )
        self.flip_v_btn.connect("clicked", lambda *_: self.canvas.flip_selected_image(horizontal=False))
        self.img_ops_box.append(self.flip_v_btn)

        self.front_btn = Gtk.Button(
            icon_name=self._resolve_icon_name("front-symbolic"),
            tooltip_text="Bring Image to Front"
        )
        self.front_btn.connect("clicked", lambda *_: self.canvas.reorder_selected_image(to_front=True))
        self.img_ops_box.append(self.front_btn)

        self.back_layer_btn = Gtk.Button(
            icon_name=self._resolve_icon_name("go-bottom-symbolic"),
            tooltip_text="Send Image to Back"
        )
        self.back_layer_btn.connect("clicked", lambda *_: self.canvas.reorder_selected_image(to_front=False))
        self.img_ops_box.append(self.back_layer_btn)

        self.del_img_btn = Gtk.Button(
            icon_name=self._resolve_icon_name("user-trash-symbolic"),
            tooltip_text="Delete Selected Item(s)"
        )
        self.del_img_btn.add_css_class("destructive-action")
        self.del_img_btn.connect("clicked", lambda *_: self.canvas.delete_selected_item())
        self.img_ops_box.append(self.del_img_btn)

        self.stylus_bar.set_center_widget(center_tools_box)

        # =====================================================================
        # 2b. REFINED CENTERED MARKDOWN EDITING BAR (.md / .markdown)
        # =====================================================================
        self.md_toolbar_bar = Gtk.CenterBox(
            halign=Gtk.Align.FILL,
            visible=False
        )
        self.md_toolbar_bar.add_css_class("stylus-toolbar")

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

        # Find in Page toggle button
        md_tools_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)

        self.md_find_btn = Gtk.ToggleButton(
            icon_name="system-search-symbolic",
            tooltip_text="Find in Page (Ctrl+F)"
        )
        self.md_find_btn.connect("toggled", self._on_md_find_toggled)
        md_tools_box.append(self.md_find_btn)

        center_fmt_wrap.append(md_tools_box)
        center_fmt_wrap.set_margin_start(10)
        center_fmt_wrap.set_margin_end(10)
        self.md_toolbar_bar.set_center_widget(center_fmt_wrap)

        self.md_doc_slot = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6, halign=Gtk.Align.START)
        self.md_toolbar_bar.set_start_widget(self.md_doc_slot)

        md_end_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6, halign=Gtk.Align.END)
        md_end_box.append(self.md_mode_box)
        self.md_zoom_slot = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        md_end_box.append(self.md_zoom_slot)
        self.md_toolbar_bar.set_end_widget(md_end_box)

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

        # Seamless Toolbar Container placed right below the HeaderBar
        self.toolbar_container = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=0,
            hexpand=True
        )
        self.toolbar_container.add_css_class("seamless-toolbar-container")
        self.stylus_scroll = Gtk.ScrolledWindow(
            vscrollbar_policy=Gtk.PolicyType.NEVER,
            hscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
            hexpand=True
        )
        self.stylus_scroll.set_child(self.stylus_bar)
        self.toolbar_container.append(self.stylus_scroll)
        self.md_toolbar_scroll = Gtk.ScrolledWindow(
            vscrollbar_policy=Gtk.PolicyType.NEVER,
            hscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
            hexpand=True
        )
        self.md_toolbar_scroll.set_child(self.md_toolbar_bar)
        self.md_toolbar_bar.bind_property(
            "visible", self.md_toolbar_scroll, "visible", GObject.BindingFlags.SYNC_CREATE
        )
        self.toolbar_container.append(self.md_toolbar_scroll)
        self.toolbar_container.append(self.md_search_bar)

        # Document Viewer Stack
        self.viewer_stack = Gtk.Stack(hexpand=True, vexpand=True)

        viewer_content_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=0,
            hexpand=True,
            vexpand=True
        )
        viewer_content_box.append(self.toolbar_container)
        viewer_content_box.append(self.viewer_stack)

        viewer_toolbar.set_content(viewer_content_box)

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

        self.page_sidebar_scrolled = Gtk.ScrolledWindow(
            vexpand=True,
            hscrollbar_policy=Gtk.PolicyType.NEVER
        )
        self.page_sidebar_vadj = self.page_sidebar_scrolled.get_vadjustment()
        self.page_sidebar_vadj.connect("value-changed", self._on_page_sidebar_scroll_changed)

        self.page_sidebar_listbox = Gtk.ListBox()
        self.page_sidebar_listbox.add_css_class("navigation-sidebar")
        self.page_sidebar_listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.page_sidebar_listbox.connect("row-selected", self._on_page_sidebar_row_selected)
        self.page_sidebar_scrolled.set_child(self.page_sidebar_listbox)
        sidebar_vbox.append(self.page_sidebar_scrolled)

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
            on_selection_changed_cb=self._on_canvas_selection_changed,
            on_tool_change_cb=self._on_canvas_tool_change_requested
        )
        self.canvas.vadj = self.vadj

        self.scrolled.set_child(self.canvas)
        self.viewer_overlay.set_child(self.scrolled)

        self.prev_btn = Gtk.Button(
            icon_name=self._resolve_icon_name("prev-symbolic"),
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
            icon_name=self._resolve_icon_name("next-symbolic"),
            tooltip_text="Next Page (Page Down)",
            halign=Gtk.Align.END,
            valign=Gtk.Align.CENTER,
            margin_end=18
        )
        self.next_btn.add_css_class("osd")
        self.next_btn.add_css_class("circular")
        self.next_btn.set_visible(False)
        self.next_btn.connect("clicked", self._on_next_overlay_btn_clicked)
        self.viewer_overlay.add_overlay(self.next_btn)

        self.page_badge = Gtk.Box(
            halign=Gtk.Align.CENTER,
            valign=Gtk.Align.END,
            margin_bottom=76
        )
        self.page_badge.add_css_class("osd")
        self.page_badge.add_css_class("page-badge")
        self.page_badge.set_visible(False)

        self.page_label = Gtk.Label(label="1 / 1")
        self.page_label.add_css_class("numeric")
        self.page_badge.append(self.page_label)
        self.viewer_overlay.add_overlay(self.page_badge)
        self.viewer_overlay.add_overlay(self.img_ops_box)

        self.canvas_workspace_box.append(self.viewer_overlay)
        self.viewer_stack.add_named(self.canvas_workspace_box, "canvas_doc")

        # =====================================================================
        # 2d. Markdown Split Editor & Live Preview (.md / .markdown)
        # =====================================================================
        self.md_paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL, wide_handle=False)
        self.md_paned.set_shrink_start_child(True)
        self.md_paned.set_shrink_end_child(True)
        self.md_paned.set_resize_start_child(True)
        self.md_paned.set_resize_end_child(True)
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

        # Capture clicks anywhere in the window to close open popovers on outside click
        self._window_click_gesture = Gtk.GestureClick.new()
        self._window_click_gesture.set_button(0)
        self._window_click_gesture.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        self._window_click_gesture.connect("pressed", self._on_window_capture_click_pressed)
        self.add_controller(self._window_click_gesture)

        if hasattr(Adw, "Breakpoint"):
            bp_compact = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 820px"))
            bp_compact.add_setter(self.split_view, "collapsed", True)
            bp_compact.add_setter(self.history_box, "visible", False)
            bp_compact.add_setter(self.zoom_out_btn, "visible", False)
            bp_compact.add_setter(self.zoom_in_btn, "visible", False)
            bp_compact.add_setter(self.zoom_lock_btn, "visible", False)
            self.add_breakpoint(bp_compact)

    # =========================================================================
    # Libadwaita Spinner Helper & Async Thumbnail Scheduler
    # =========================================================================
    @staticmethod
    def create_adw_spinner(size=24):
        """Create an Adw.Spinner (Libadwaita 1.6+) or fallback cleanly to Gtk.Spinner."""
        if hasattr(Adw, "Spinner"):
            spinner = Adw.Spinner()
        else:
            spinner = Gtk.Spinner(spinning=True)
        spinner.set_size_request(size, size)
        spinner.set_halign(Gtk.Align.CENTER)
        spinner.set_valign(Gtk.Align.CENTER)
        return spinner

    def wrap_drawing_area_with_spinner(self, draw_area, page_idx, spinner_size=22):
        """
        Wrap a thumbnail Gtk.DrawingArea inside a Gtk.Overlay with a centered
        Libadwaita spinner that remains visible until the thumbnail surface is cached.
        """
        parent = draw_area.get_parent()
        if isinstance(parent, Gtk.Overlay):
            return parent

        overlay = Gtk.Overlay()
        overlay.set_halign(draw_area.get_halign())
        overlay.set_valign(draw_area.get_valign())

        spinner = self.create_adw_spinner(size=spinner_size)
        is_cached = self.canvas.has_cached_thumb(page_idx)
        spinner.set_visible(not is_cached)
        self._thumb_spinner_widgets[page_idx] = spinner

        if parent is not None:
            if isinstance(parent, Gtk.Box):
                parent.remove(draw_area)
                overlay.set_child(draw_area)
                overlay.add_overlay(spinner)
                parent.prepend(overlay)
                return overlay
            elif hasattr(parent, "set_child"):
                parent.set_child(None)
                overlay.set_child(draw_area)
                overlay.add_overlay(spinner)
                parent.set_child(overlay)
                return overlay

        overlay.set_child(draw_area)
        overlay.add_overlay(spinner)
        return overlay

    def wrap_library_card_with_spinner(self, cover_widget, file_path, is_ready=False, spinner_size=26):
        """
        Wrap a library grid/list thumbnail widget in a Gtk.Overlay with a centered
        Adw.Spinner that automatically hides once the asynchronous thumbnail finishes loading.
        """
        overlay = Gtk.Overlay()
        overlay.set_child(cover_widget)
        spinner = self.create_adw_spinner(size=spinner_size)
        spinner.set_visible(not is_ready)
        overlay.add_overlay(spinner)
        self._library_card_spinners[file_path] = spinner
        return overlay

    def set_library_card_thumb_ready(self, file_path):
        """Hide and stop the Libadwaita spinner for a library card once its thumbnail is ready."""
        sp = self._library_card_spinners.pop(file_path, None)
        if sp is not None:
            sp.set_visible(False)

    def _cancel_pending_thumb_jobs(self):
        self._thumb_render_generation += 1
        if self._thumb_idle_source_id:
            GLib.source_remove(self._thumb_idle_source_id)
            self._thumb_idle_source_id = 0
        if self._thumb_dirty_debounce_id:
            GLib.source_remove(self._thumb_dirty_debounce_id)
            self._thumb_dirty_debounce_id = 0
        self._thumb_pending_queue.clear()

    def _on_page_sidebar_toggled_optimized(self, btn):
        # Delegate to PageSidebarMixin's handler first
        if hasattr(super(), "_on_page_sidebar_toggled"):
            super()._on_page_sidebar_toggled(btn)
        else:
            self.page_sidebar_revealer.set_reveal_child(btn.get_active())

        if btn.get_active():
            self._schedule_sidebar_thumbnails()

    def _on_page_sidebar_scroll_changed(self, _adj):
        if self.page_sidebar_revealer.get_reveal_child():
            self._reprioritize_thumb_queue_by_scroll()

    def _get_visible_sidebar_page_range(self):
        total = len(self._page_thumb_draw_areas)
        if total == 0:
            return 0, 0
        adj = self.page_sidebar_vadj
        scroll_y = adj.get_value() if adj else 0.0
        page_h = adj.get_page_size() if adj else 600.0
        if page_h <= 0:
            page_h = 600.0
        est_row_h = 165.0
        first_vis = max(0, int(scroll_y // est_row_h) - 1)
        last_vis = min(total - 1, int((scroll_y + page_h) // est_row_h) + 2)
        return first_vis, last_vis

    def _reprioritize_thumb_queue_by_scroll(self):
        if not self._thumb_pending_queue:
            return
        first_vis, last_vis = self._get_visible_sidebar_page_range()
        cur = self.canvas.current_page

        def sort_key(p_idx):
            in_view = 0 if (first_vis <= p_idx <= last_vis) else 1
            return (in_view, abs(p_idx - cur))

        self._thumb_pending_queue.sort(key=sort_key)

    def _rebuild_page_sidebar(self):
        """
        Override/wrap PageSidebarMixin._rebuild_page_sidebar to attach Libadwaita
        spinners and schedule non-blocking progressive thumbnail rendering.
        """
        self._cancel_pending_thumb_jobs()
        self._thumb_spinner_widgets.clear()

        if hasattr(super(), "_rebuild_page_sidebar"):
            super()._rebuild_page_sidebar()

        # Attach Adw.Spinner overlays to each thumbnail drawing area
        for idx, da in enumerate(self._page_thumb_draw_areas):
            self.wrap_drawing_area_with_spinner(da, idx, spinner_size=22)

        # Only start background rasterization if sidebar is open or for current page
        if self.page_sidebar_revealer.get_reveal_child():
            self._schedule_sidebar_thumbnails()
        else:
            # Pre-warm just the current page thumbnail on idle
            self._schedule_sidebar_thumbnails(only_indices=[self.canvas.current_page])

    def _schedule_sidebar_thumbnails(self, only_indices=None):
        if not self.canvas.document:
            return
        pages = self.canvas.document.get("pages", [])
        if not pages:
            return

        if only_indices is not None:
            candidates = [i for i in only_indices if 0 <= i < len(pages)]
        else:
            candidates = list(range(len(pages)))

        for idx in candidates:
            if not self.canvas.has_cached_thumb(idx) and idx not in self._thumb_pending_queue:
                self._thumb_pending_queue.append(idx)
                sp = self._thumb_spinner_widgets.get(idx)
                if sp is not None:
                    sp.set_visible(True)

        self._reprioritize_thumb_queue_by_scroll()

        if self._thumb_pending_queue and not self._thumb_idle_source_id:
            gen = self._thumb_render_generation
            self._thumb_idle_source_id = GLib.idle_add(
                self._process_next_sidebar_thumbnail,
                gen,
                priority=GLib.PRIORITY_LOW
            )

    def _process_next_sidebar_thumbnail(self, generation):
        if generation != self._thumb_render_generation or not self.canvas.document:
            self._thumb_idle_source_id = 0
            return GLib.SOURCE_REMOVE

        if not self._thumb_pending_queue:
            self._thumb_idle_source_id = 0
            return GLib.SOURCE_REMOVE

        page_idx = self._thumb_pending_queue.pop(0)
        pages = self.canvas.document.get("pages", [])
        if 0 <= page_idx < len(pages):
            da = self._page_thumb_draw_areas[page_idx] if page_idx < len(self._page_thumb_draw_areas) else None
            tw = (da.get_width() or da.get_content_width() or 116) if da else 116
            th = (da.get_height() or da.get_content_height() or 156) if da else 156
            pw, ph = self.canvas.get_page_size(page_idx)

            # Render and store in canvas.thumb_cache
            self.canvas.ensure_page_thumb_surface(
                page_idx, pages[page_idx], pw, ph, tw, th
            )

            sp = self._thumb_spinner_widgets.get(page_idx)
            if sp is not None:
                sp.set_visible(False)
            if da is not None:
                da.queue_draw()

        if self._thumb_pending_queue:
            return GLib.SOURCE_CONTINUE

        self._thumb_idle_source_id = 0
        return GLib.SOURCE_REMOVE

    def _queue_redraw_current_sidebar_thumb(self):
        """
        Debounced thumbnail invalidation so continuous stylus strokes never trigger
        synchronous thumbnail re-renders on every stroke or property change.
        """
        idx = self.canvas.current_page
        self.canvas.invalidate_thumb(idx)

        # If the sidebar is hidden, don't waste CPU rendering thumbnails at all
        if not self.page_sidebar_revealer.get_reveal_child():
            return

        if self._thumb_dirty_debounce_id:
            GLib.source_remove(self._thumb_dirty_debounce_id)

        def _commit_dirty_thumb():
            self._thumb_dirty_debounce_id = 0
            if not self.canvas.document:
                return GLib.SOURCE_REMOVE
            self._schedule_sidebar_thumbnails(only_indices=[idx])
            return GLib.SOURCE_REMOVE

        self._thumb_dirty_debounce_id = GLib.timeout_add(180, _commit_dirty_thumb)

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
    def _place_shared_toolbar_widgets(self, target):
        """Move the shared Save / Undo-Redo / Clipboard / Zoom groups into the active toolbar."""
        if target == "md":
            doc_slot, zoom_slot = self.md_doc_slot, self.md_zoom_slot
        else:
            doc_slot, zoom_slot = self.stylus_doc_slot, self.stylus_zoom_slot
        for slot, widgets in (
            (doc_slot, (self.save_btn, self.history_box, self.clipboard_box)),
            (zoom_slot, (self.zoom_box,)),
        ):
            for w in widgets:
                parent = w.get_parent()
                if parent is slot:
                    continue
                if parent is not None:
                    parent.remove(w)
                slot.append(w)

    def _on_clipboard_action(self, op):
        """Route Cut / Copy / Paste to the focused text widget, the Markdown editor, or the canvas."""
        focus = self.get_focus()
        # Text entries / text boxes / the Markdown editor handle their own clipboard
        w = focus
        while w is not None:
            if isinstance(w, (Gtk.Text, Gtk.TextView)):
                try:
                    w.activate_action(f"clipboard.{op}", None)
                except Exception:
                    pass
                return
            w = w.get_parent()

        if self.root_stack.get_visible_child_name() != "viewer":
            return

        if self.current_file_type == "md":
            clipboard = self.get_clipboard()
            editable = self.md_textview.get_editable()
            if op == "copy":
                self.md_buffer.copy_clipboard(clipboard)
            elif op == "cut":
                self.md_buffer.cut_clipboard(clipboard, editable)
            elif op == "paste":
                self.md_buffer.paste_clipboard(clipboard, None, editable)
            return

        if self.current_file_type not in ("xnote", "xcanvas") or not self.canvas.document:
            return

        import page_sidebar as _ps
        if op == "copy":
            if self.canvas.copy_selection():
                self.show_toast("Copied selection")
            else:
                self.show_toast("Nothing selected to copy")
        elif op == "cut":
            if self.canvas.cut_selection():
                self.show_toast("Cut selection")
            else:
                self.show_toast("Nothing selected to cut")
        elif op == "paste":
            if _ps._APP_ITEMS_CLIPBOARD:
                self.canvas.paste_selection()
                self._queue_redraw_current_sidebar_thumb()
            elif _ps._APP_PAGE_CLIPBOARD is not None and self.current_file_type == "xnote":
                self.paste_page(self.canvas.current_page)
            else:
                self.show_toast("Clipboard is empty")

    def _resolve_icon_name(self, custom_name, fallback_name=None):
        if not custom_name:
            return fallback_name or ""
        base = custom_name.replace("-symbolic", "").replace("_symbolic", "")
        b_dash = base.replace("_", "-")
        b_under = base.replace("-", "_")

        # Prioritize symbolic names so GTK4 treats them as symbolic paintables and recolors them for dark/light themes
        candidates = [
            f"{b_dash}-symbolic",
            f"{b_under}-symbolic",
            f"{base}-symbolic",
            custom_name if custom_name.endswith("-symbolic") else f"{custom_name}-symbolic",
            b_dash,
            b_under,
            base,
            custom_name,
        ]
        if fallback_name:
            fb_base = fallback_name.replace("-symbolic", "").replace("_symbolic", "")
            fb_dash = fb_base.replace("_", "-")
            candidates.extend([
                f"{fb_dash}-symbolic",
                fallback_name if fallback_name.endswith("-symbolic") else f"{fallback_name}-symbolic",
                fallback_name,
                fb_base,
            ])

        for name in candidates:
            if os.path.isfile(os.path.join(self.icons_root, f"{name}.svg")):
                return name
            if os.path.isfile(os.path.join(self.icons_dir, f"{name}.svg")):
                return name
            if hasattr(self, "icon_theme") and self.icon_theme.has_icon(name):
                return name

        return fallback_name or (f"{b_dash}-symbolic" if hasattr(self, "icon_theme") and self.icon_theme.has_icon(f"{b_dash}-symbolic") else custom_name)

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

        if self.current_file_type == "xcanvas":
            return

        if self.current_file_type == "md":
            if hasattr(self, "zoom_box") and not self.zoom_box.get_sensitive():
                return
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

    def on_fit_height_clicked(self, *_):
        if self.root_stack.get_visible_child_name() != "viewer":
            return

        if self.current_file_type == "xcanvas":
            return

        if self.current_file_type == "md":
            if hasattr(self, "zoom_box") and not self.zoom_box.get_sensitive():
                return
            avail_h = self.md_scrolled.get_height() or (self.get_height() - 100)
            target_zoom = max(0.25, min((avail_h - 64.0) / 880.0, 4.0))
            self.set_zoom(target_zoom)
            return

        if not self.canvas.document:
            return
        _, ph = self.canvas.get_page_size(self.canvas.current_page)
        if ph <= 0:
            return
        avail_h = self.scrolled.get_height() or (self.get_height() - 100) or 800
        target_zoom = max(0.1, min((avail_h - 48.0) / ph, 4.0))
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

    def _on_canvas_tool_change_requested(self, tool_name):
        btn = self.tool_buttons.get(tool_name)
        if btn and not btn.get_active():
            btn.set_active(True)
        else:
            self.canvas.active_tool = tool_name

    def _on_canvas_selection_changed(self, has_selection, sel_type=None):
        self.img_ops_box.set_visible(has_selection)
        is_image_sel = (sel_type in ("image", "picture"))
        is_shape_sel = (sel_type == "shape")
        is_text_sel = (sel_type == "text")
        self.reset_aspect_btn.set_visible(is_image_sel)
        self.rot_l_btn.set_visible(is_image_sel or is_shape_sel or is_text_sel)
        self.rot_r_btn.set_visible(is_image_sel or is_shape_sel or is_text_sel)
        self.flip_h_btn.set_visible(is_image_sel or is_shape_sel)
        self.flip_v_btn.set_visible(is_image_sel or is_shape_sel)
        self.front_btn.set_visible(is_image_sel or is_shape_sel or is_text_sel)
        self.back_layer_btn.set_visible(is_image_sel or is_shape_sel or is_text_sel)

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

        prev_mode = getattr(self, "md_view_mode", "edit")
        self.md_view_mode = mode
        self._apply_md_preview_layout_style(mode)

        # Grey out zoom controls when preview is not visible
        is_preview_visible = (mode in ("split", "preview"))
        if hasattr(self, "zoom_box"):
            self.zoom_box.set_sensitive(is_preview_visible)

        if hasattr(self, "_md_mode_animation") and self._md_mode_animation:
            try:
                self._md_mode_animation.skip()
            except Exception:
                pass
            self._md_mode_animation = None

        total_w = self.md_paned.get_width()
        if total_w <= 10:
            total_w = self.get_width() or 1200

        if prev_mode == "preview" or not self.md_edit_scrolled.get_visible():
            start_pos = 0
        elif prev_mode == "edit" or not self.md_scrolled.get_visible():
            start_pos = total_w
        else:
            start_pos = self.md_paned.get_position()
            if start_pos <= 0:
                start_pos = int(total_w * 0.50)

        if mode == "preview":
            end_pos = 0
        elif mode == "edit":
            end_pos = total_w
        else:
            end_pos = int(total_w * 0.50)

        self.md_edit_scrolled.set_visible(True)
        self.md_scrolled.set_visible(True)
        self.md_paned.set_position(int(start_pos))

        if int(start_pos) == int(end_pos):
            if mode == "preview":
                self.md_edit_scrolled.set_visible(False)
            elif mode == "edit":
                self.md_scrolled.set_visible(False)
            return

        if hasattr(Adw, "TimedAnimation") and hasattr(Adw, "CallbackAnimationTarget"):
            def _step_anim(val):
                self.md_paned.set_position(int(val))

            target = Adw.CallbackAnimationTarget.new(_step_anim)
            anim = Adw.TimedAnimation.new(self, float(start_pos), float(end_pos), 250, target)
            anim.set_easing(Adw.Easing.EASE_OUT_CUBIC)

            def _on_anim_state(a, _pspec):
                if a.get_state() == Adw.AnimationState.FINISHED:
                    if self.md_view_mode == "preview":
                        self.md_edit_scrolled.set_visible(False)
                    elif self.md_view_mode == "edit":
                        self.md_scrolled.set_visible(False)
                    self._md_mode_animation = None

            anim.connect("notify::state", _on_anim_state)
            self._md_mode_animation = anim
            anim.play()
        else:
            self.md_paned.set_position(int(end_pos))
            if mode == "preview":
                self.md_edit_scrolled.set_visible(False)
            elif mode == "edit":
                self.md_scrolled.set_visible(False)

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

    def _on_window_capture_click_pressed(self, gesture, n_press, x, y):
        open_popovers = []
        for p_name in ("tool_settings_popover", "template_popover", "shape_popover"):
            p = getattr(self, p_name, None)
            if p and p.get_visible():
                open_popovers.append(p)

        if not open_popovers:
            return

        picked = self.pick(x, y, Gtk.PickFlags.DEFAULT)

        for pop in open_popovers:
            if picked and (picked == pop or picked.is_ancestor(pop)):
                continue
            pop_parent = pop.get_parent()
            if picked and pop_parent and (picked == pop_parent or picked.is_ancestor(pop_parent)):
                continue
            pop.popdown()

    # =========================================================================
    # Stylus Toolbar Callbacks & Live Lasso Recoloring
    # =========================================================================
    def _on_tool_toggled(self, btn, tool_name):
        if not btn.get_active():
            return
        self._just_toggled_tool = tool_name
        self.canvas.active_tool = tool_name
        if tool_name not in ("select", "lasso"):
            self.canvas.clear_selection()
        if hasattr(self, "width_scale") and self.width_scale:
            if tool_name in ("pen", "dashed", "speed", "velocity_pen", "calligraphy", "taper"):
                self.width_scale.set_value(self.canvas.pen_width)
            elif tool_name == "shape":
                self.width_scale.set_value(self.canvas.shape_stroke_width)
            elif tool_name == "highlighter":
                self.width_scale.set_value(self.canvas.highlighter_width)
            elif tool_name == "eraser":
                self.width_scale.set_value(self.canvas.eraser_radius)

        if hasattr(self, "tool_settings_popover") and self.tool_settings_popover.get_visible():
            self.tool_settings_popover.popdown()

        if tool_name == "shape":
            GLib.idle_add(self._show_shape_dropdown)
        else:
            if hasattr(self, "shape_popover") and self.shape_popover.get_visible():
                self.shape_popover.popdown()

    def _on_tool_btn_pressed(self, gesture, n_press, x, y, tname, btn):
        button = gesture.get_current_button()
        btn._was_active_on_press = btn.get_active()
        if button == 3:  # Right-click on tool button
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
            if tname == "shape":
                if not btn.get_active():
                    btn.set_active(True)
                self._show_shape_dropdown()
                return
            tool_key = "speed" if tname == "velocity_pen" else tname
            if tool_key not in TOOLS_WITH_SETTINGS:
                if hasattr(self, "tool_settings_popover") and self.tool_settings_popover.get_visible():
                    self.tool_settings_popover.popdown()
                return
            if not btn.get_active():
                btn.set_active(True)
            self._show_tool_settings_popover(btn)

    def _on_tool_btn_released(self, gesture, n_press, x, y, tname, btn):
        button = gesture.get_current_button()
        if button == 3:
            return  # Handled on pressed

        was_active = getattr(btn, "_was_active_on_press", False)
        btn._was_active_on_press = False

        if tname == "shape":
            if was_active and btn.get_active():
                self._show_shape_dropdown()
            return

        if was_active and btn.get_active():
            # Clicked on the already selected tool!
            tool_key = "speed" if tname == "velocity_pen" else tname
            if tool_key not in TOOLS_WITH_SETTINGS:
                # Do not show pop up for tools with no settings
                return
            self._show_tool_settings_popover(btn)

    def _on_tool_btn_clicked_again(self, gesture, n_press, x, y, tname, btn):
        self._on_tool_btn_released(gesture, n_press, x, y, tname, btn)

    def _show_tool_settings_popover(self, tool_btn=None):
        tool = self.canvas.active_tool
        if tool == "velocity_pen":
            tool = "speed"
        if tool not in TOOLS_WITH_SETTINGS:
            if hasattr(self, "tool_settings_popover") and self.tool_settings_popover.get_visible():
                self.tool_settings_popover.popdown()
            return

        target_btn = tool_btn or self.tool_buttons.get(tool)
        if not target_btn:
            return

        if self.tool_settings_popover.get_visible():
            if self.tool_settings_popover.get_parent() == target_btn:
                self.tool_settings_popover.popdown()
                return

        self._refresh_tool_settings_popover()

        if self.tool_settings_popover.get_parent() != target_btn:
            if self.tool_settings_popover.get_parent() is not None:
                self.tool_settings_popover.unparent()
            self.tool_settings_popover.set_parent(target_btn)

        self.tool_settings_popover.set_position(Gtk.PositionType.BOTTOM)
        self.tool_settings_popover.popup()

    def _show_shape_dropdown(self):
        cur_shape = getattr(self.canvas, "shape_type", "rectangle") if hasattr(self, "canvas") and self.canvas else "rectangle"
        if hasattr(self, "_shape_rows"):
            for sid, (_, check) in self._shape_rows.items():
                check.set_visible(sid == cur_shape)
        if hasattr(self, "_shape_scale") and hasattr(self, "canvas") and self.canvas:
            self._shape_scale.set_value(float(getattr(self.canvas, "shape_stroke_width", 3.5)))
        if hasattr(self, "_shape_fill_sw") and hasattr(self, "canvas") and self.canvas:
            self._shape_fill_sw.set_active(bool(getattr(self.canvas, "shape_fill", False)))
        if hasattr(self, "tool_settings_popover") and self.tool_settings_popover.get_visible():
            self.tool_settings_popover.popdown()
        if hasattr(self, "shape_popover"):
            self.shape_popover.popup()

    def _build_shape_dropdown_popover(self):
        popover = Gtk.Popover()
        popover.set_position(Gtk.PositionType.BOTTOM)

        container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        container.set_size_request(280, 390)
        container.set_margin_top(8)
        container.set_margin_bottom(8)
        container.set_margin_start(8)
        container.set_margin_end(8)

        # Header
        hdr_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        hdr_icon = Gtk.Image.new_from_icon_name(self._resolve_icon_name("shape-symbolic"))
        hdr_lbl = Gtk.Label(label="Select Shape", xalign=0.0, hexpand=True)
        hdr_lbl.add_css_class("heading")
        hdr_box.append(hdr_icon)
        hdr_box.append(hdr_lbl)
        container.append(hdr_box)

        # Search filter
        search_entry = Gtk.SearchEntry(placeholder_text="Filter shapes…")
        container.append(search_entry)

        # Scrolled Boxed List of Shapes
        scrolled = Gtk.ScrolledWindow(hexpand=True, vexpand=True)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        listbox = Gtk.ListBox()
        listbox.add_css_class("boxed-list")
        listbox.set_selection_mode(Gtk.SelectionMode.NONE)

        shape_definitions = [
            ("rectangle", "shape-rect-symbolic", "Rectangle"),
            ("rounded_rect", "shape-rect-symbolic", "Rounded Rectangle"),
            ("capsule", "shape-rect-symbolic", "Capsule / Pill"),
            ("ellipse", "shape-circle-symbolic", "Ellipse (Circle with Shift)"),
            ("diamond", "media-record-symbolic", "Diamond"),
            ("triangle", "shape-triangle-symbolic", "Triangle"),
            ("right_triangle", "shape-triangle-symbolic", "Right Triangle"),
            ("parallelogram", "view-more-horizontal-symbolic", "Parallelogram"),
            ("trapezoid", "shape-triangle-symbolic", "Trapezoid"),
            ("pentagon", "media-record-symbolic", "Pentagon"),
            ("hexagon", "media-record-symbolic", "Hexagon"),
            ("star", "starred-symbolic", "5-Point Star"),
            ("cross", "list-add-symbolic", "Cross / Plus"),
            ("heart", "heart-symbolic", "Heart"),
            ("cloud", "weather-overcast-symbolic", "Cloud"),
            ("line", "shape-line-symbolic", "Straight Line"),
            ("arrow", "shape-arrow-symbolic", "Directional Arrow"),
            ("double_arrow", "view-more-horizontal-symbolic", "Double Arrow"),
            ("flow_process", "shape-rect-symbolic", "Process (Flowchart)"),
            ("flow_alt_process", "shape-rect-symbolic", "Alternate Process"),
            ("flow_decision", "media-record-symbolic", "Decision (Flowchart)"),
            ("flow_data", "view-more-horizontal-symbolic", "Data / I-O (Flowchart)"),
            ("flow_terminator", "shape-rect-symbolic", "Terminator (Flowchart)"),
            ("flow_document", "document-symbolic", "Document (Flowchart)"),
            ("flow_database", "drive-harddisk-symbolic", "Database Drum"),
            ("speech_bubble", "chat-message-new-symbolic", "Speech Bubble Callout"),
            ("solid_cube", "view-grid-symbolic", "3D Cube"),
            ("solid_cylinder", "drive-harddisk-symbolic", "3D Cylinder"),
            ("solid_cone", "shape-triangle-symbolic", "3D Cone"),
            ("solid_sphere", "shape-circle-symbolic", "3D Sphere"),
            ("math_axes_2d", "pan-end-symbolic", "2D Coordinate Axes"),
            ("math_curly_brace", "format-indent-more-symbolic", "Curly Brace"),
        ]

        self._shape_rows = {}
        for s_id, s_icon, s_name in shape_definitions:
            row = Gtk.ListBoxRow()
            row._shape_id = s_id
            row._shape_name = s_name

            row_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
            row_box.set_margin_top(6)
            row_box.set_margin_bottom(6)
            row_box.set_margin_start(10)
            row_box.set_margin_end(10)

            icon_img = Gtk.Image.new_from_icon_name(self._resolve_icon_name(s_icon))
            name_lbl = Gtk.Label(label=s_name, xalign=0.0, hexpand=True)
            check_img = Gtk.Image.new_from_icon_name("object-select-symbolic")
            cur_shape = getattr(self.canvas, "shape_type", "rectangle") if hasattr(self, "canvas") and self.canvas else "rectangle"
            check_img.set_visible(cur_shape == s_id)

            row_box.append(icon_img)
            row_box.append(name_lbl)
            row_box.append(check_img)

            row.set_child(row_box)
            listbox.append(row)
            self._shape_rows[s_id] = (row, check_img)

        def _filter_shapes(row):
            q = search_entry.get_text().strip().lower()
            if not q:
                return True
            return q in getattr(row, "_shape_name", "").lower() or q in getattr(row, "_shape_id", "").lower()

        listbox.set_filter_func(_filter_shapes)
        search_entry.connect("search-changed", lambda *_: listbox.invalidate_filter())

        def _on_row_activated(_box, row):
            if hasattr(row, "_shape_id"):
                sid = row._shape_id
                self.canvas.shape_type = sid
                ts = getattr(self.canvas, "tool_settings", {})
                cfg = ts.setdefault("shape", {})
                cfg["shape_type"] = sid
                for other_id, (_, check) in self._shape_rows.items():
                    check.set_visible(other_id == sid)
                self.shape_tool_btn.set_tooltip_text(f"Shapes Tool ({row._shape_name})")
                popover.popdown()

        listbox.connect("row-activated", _on_row_activated)
        scrolled.set_child(listbox)
        container.append(scrolled)

        # Stroke Width & Fill at bottom of drop down
        bottom_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        bottom_box.add_css_class("card")
        bottom_box.set_margin_top(2)

        sw_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        sw_row.set_margin_start(8)
        sw_row.set_margin_end(8)
        sw_row.set_margin_top(4)
        sw_lbl = Gtk.Label(label="Stroke", xalign=0.0)
        sw_lbl.add_css_class("caption")
        sw_scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 1.0, 32.0, 0.5)
        sw_scale.set_hexpand(True)
        init_w = float(getattr(self.canvas, "shape_stroke_width", 3.5)) if hasattr(self, "canvas") and self.canvas else 3.5
        sw_scale.set_value(init_w)
        sw_scale.connect("value-changed", lambda sc: self._on_popover_shape_width_changed(sc.get_value()))
        self._shape_scale = sw_scale
        sw_row.append(sw_lbl)
        sw_row.append(sw_scale)
        bottom_box.append(sw_row)

        fill_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        fill_row.set_margin_start(8)
        fill_row.set_margin_end(8)
        fill_row.set_margin_bottom(6)
        fill_lbl = Gtk.Label(label="Fill Shape", xalign=0.0, hexpand=True)
        fill_lbl.add_css_class("caption")
        init_fill = bool(getattr(self.canvas, "shape_fill", False)) if hasattr(self, "canvas") and self.canvas else False
        fill_sw = Gtk.Switch(active=init_fill)
        self._shape_fill_sw = fill_sw
        fill_sw.connect("notify::active", lambda sw, _: self._on_popover_shape_fill_toggled(sw.get_active()))
        fill_row.append(fill_lbl)
        fill_row.append(fill_sw)
        bottom_box.append(fill_row)

        container.append(bottom_box)

        popover.set_child(container)
        return popover

    def on_library_sidebar_toggled(self, btn):
        show = btn.get_active()
        if hasattr(self.split_view, "set_show_sidebar"):
            self.split_view.set_show_sidebar(show)
        elif hasattr(self.split_view, "set_reveal_flap"):
            self.split_view.set_reveal_flap(show)
        elif hasattr(self.split_view, "set_collapsed"):
            self.split_view.set_collapsed(not show)

    def on_toggle_sidebar_action(self, *args):
        if self.root_stack.get_visible_child_name() == "collection":
            if hasattr(self, "library_sidebar_btn"):
                self.library_sidebar_btn.set_active(not self.library_sidebar_btn.get_active())
        else:
            if hasattr(self, "page_sidebar_btn") and self.page_sidebar_btn.get_visible():
                self.page_sidebar_btn.set_active(not self.page_sidebar_btn.get_active())

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
        else:
            sel_item = self.canvas.get_selected_item()
            if sel_item and (sel_item.get("kind") or sel_item.get("type")) in ("shape", "text"):
                sel_item["rgba"] = list(new_rgba)
                self.canvas.invalidate_page(self.canvas.selected_page_idx, clear_bg=False)
                self.canvas.queue_draw()
                self._queue_redraw_current_sidebar_thumb()

    def _on_width_changed(self, scale):
        val = scale.get_value()
        ts = getattr(self.canvas, "tool_settings", {})
        if self.canvas.active_tool in ("pen", "dashed", "speed", "velocity_pen", "calligraphy", "taper"):
            self.canvas.pen_width = val
            tool_key = "speed" if self.canvas.active_tool == "velocity_pen" else self.canvas.active_tool
            if tool_key in ts:
                ts[tool_key]["width"] = val
        elif self.canvas.active_tool == "shape":
            self.canvas.shape_stroke_width = val
            if "shape" in ts:
                ts["shape"]["stroke_width"] = val
        elif self.canvas.active_tool == "highlighter":
            self.canvas.highlighter_width = val
            if "highlighter" in ts:
                ts["highlighter"]["width"] = val
        elif self.canvas.active_tool == "eraser":
            self.canvas.eraser_radius = val
            if "eraser" in ts:
                ts["eraser"]["radius"] = val
        elif self.canvas.active_tool == "lasso" and self.canvas.has_lasso_selection():
            self.canvas.pen_width = val
            self.canvas.resize_lasso_stroke_width(val)
            self._queue_redraw_current_sidebar_thumb()

        sel_item = self.canvas.get_selected_item()
        if sel_item and (sel_item.get("kind") or sel_item.get("type")) == "shape":
            sel_item["stroke_width"] = val
            self.canvas.invalidate_page(self.canvas.selected_page_idx, clear_bg=False)
            self.canvas.queue_draw()
            self._queue_redraw_current_sidebar_thumb()

    # =========================================================================
    # Tool Settings Popover (Velocity Pen, Calligraphy, Shapes, etc.)
    # =========================================================================
    def _build_tool_settings_popover(self):
        popover = Gtk.Popover()
        popover.set_autohide(True)
        popover.set_position(Gtk.PositionType.BOTTOM)
        popover.connect("show", lambda *_: self._refresh_tool_settings_popover())

        self.tool_settings_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.tool_settings_box.set_size_request(285, -1)
        self.tool_settings_box.set_margin_start(12)
        self.tool_settings_box.set_margin_end(12)
        self.tool_settings_box.set_margin_top(10)
        self.tool_settings_box.set_margin_bottom(12)
        popover.set_child(self.tool_settings_box)
        return popover

    def _refresh_tool_settings_popover(self):
        while child := self.tool_settings_box.get_first_child():
            self.tool_settings_box.remove(child)

        tool = self.canvas.active_tool
        if tool == "velocity_pen":
            tool = "speed"

        if tool not in TOOLS_WITH_SETTINGS:
            return

        ts = getattr(self.canvas, "tool_settings", {})
        cur_cfg = ts.setdefault(tool, {})

        tool_titles = {
            "speed": ("Velocity / Speed Pen", "tool-fountain-pen-symbolic"),
            "pen": ("Standard Pen", "tool-pen-symbolic"),
            "calligraphy": ("Calligraphy Pen", "tool-calligraphy-symbolic"),
            "dashed": ("Dashed Line Pen", "tool-dashed-symbolic"),
            "taper": ("Tapered Brush Pen", "tool-taper-symbolic"),
            "shape": ("Geometric Shapes Tool", "shape-symbolic"),
            "highlighter": ("Highlighter", "tool-highlighter-symbolic"),
            "eraser": ("Stroke & Text Eraser", "tool-eraser-symbolic"),
            "text": ("Text Box Tool", "tool-text-symbolic"),
        }

        title, icon_custom = tool_titles.get(tool, ("Tool Settings", "sliders-symbolic"))

        hdr_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        hdr_icon = Gtk.Image.new_from_icon_name(self._resolve_icon_name(icon_custom))
        hdr_lbl = Gtk.Label(label=title, xalign=0.0, hexpand=True)
        hdr_lbl.add_css_class("heading")
        hdr_box.append(hdr_icon)
        hdr_box.append(hdr_lbl)
        self.tool_settings_box.append(hdr_box)

        sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        self.tool_settings_box.append(sep)

        if tool == "speed":
            # Velocity Pen: Brush Size, Thinning Speed, Thinning Strength, Pressure Sensitivity (at bottom)
            cur_bw = float(cur_cfg.get("width", self.canvas.pen_width))
            self._add_popover_slider(
                "Brush Size", 1.0, 48.0, 0.5, cur_bw, "{:.1f} px",
                lambda val: self._on_popover_brush_size_changed("speed", val)
            )

            spd_scale = float(cur_cfg.get("speed_scale", 0.592))
            self._add_popover_slider(
                "Thinning Speed", 0.10, 2.50, 0.05, spd_scale, "{:.2f}x",
                lambda val: cur_cfg.update({"speed_scale": val})
            )

            spd_str = float(cur_cfg.get("speed_strength", 0.869))
            self._add_popover_slider(
                "Thinning Strength", 0.20, 1.00, 0.05, spd_str, "{:.0f}%",
                lambda val: cur_cfg.update({"speed_strength": val}),
                display_transform=lambda v: int(round(v * 100))
            )

            press_en = bool(cur_cfg.get("pressure_enabled", True))
            press_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            press_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            press_lbl = Gtk.Label(label="Pressure Sensitivity", xalign=0.0, hexpand=True)
            press_sw = Gtk.Switch(active=press_en)
            press_row.append(press_lbl)
            press_row.append(press_sw)
            press_box.append(press_row)

            press_min = float(cur_cfg.get("pressure_min_factor", 0.393))
            sub_slider_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, visible=press_en)
            self._append_sub_slider(
                sub_slider_box, "Pressure Dynamic Range", 0.15, 0.85, 0.05, press_min, "{:.0f}%",
                lambda v: cur_cfg.update({"pressure_min_factor": v}),
                display_transform=lambda v: int(round((1.0 - v) * 100))
            )
            press_box.append(sub_slider_box)

            def _on_press_toggled(sw, _):
                active = sw.get_active()
                cur_cfg["pressure_enabled"] = active
                sub_slider_box.set_visible(active)

            press_sw.connect("notify::active", _on_press_toggled)
            self.tool_settings_box.append(press_box)

        elif tool == "shape":
            st_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            cur_shape = getattr(self.canvas, "shape_type", "rectangle")

            shape_categories = [
                ("Basic Shapes", [
                    ("rectangle", "media-record-symbolic", "Rectangle (Square with Shift)"),
                    ("rounded_rect", "media-record-symbolic", "Rounded Rectangle"),
                    ("capsule", "media-record-symbolic", "Capsule / Pill"),
                    ("ellipse", "media-record-symbolic", "Ellipse (Circle with Shift)"),
                    ("diamond", "media-record-symbolic", "Diamond"),
                    ("triangle", "go-up-symbolic", "Triangle"),
                    ("right_triangle", "go-up-symbolic", "Right Triangle"),
                    ("parallelogram", "view-more-horizontal-symbolic", "Parallelogram"),
                    ("trapezoid", "go-up-symbolic", "Trapezoid"),
                    ("pentagon", "media-record-symbolic", "Pentagon"),
                    ("hexagon", "media-record-symbolic", "Hexagon"),
                    ("star", "starred-symbolic", "5-Point Star"),
                    ("cross", "list-add-symbolic", "Cross / Plus"),
                    ("heart", "starred-symbolic", "Heart"),
                    ("cloud", "weather-overcast-symbolic", "Cloud"),
                    ("line", "view-more-horizontal-symbolic", "Straight Line"),
                    ("arrow", "pan-end-symbolic", "Directional Arrow"),
                    ("double_arrow", "view-more-horizontal-symbolic", "Double-Headed Arrow"),
                ]),
                ("Flowchart", [
                    ("flow_process", "media-record-symbolic", "Process"),
                    ("flow_alt_process", "media-record-symbolic", "Alternate Process"),
                    ("flow_decision", "media-record-symbolic", "Decision"),
                    ("flow_data", "view-more-horizontal-symbolic", "Data / I-O"),
                    ("flow_terminator", "media-record-symbolic", "Terminator"),
                    ("flow_preparation", "media-record-symbolic", "Preparation"),
                    ("flow_predefined", "media-record-symbolic", "Predefined Process"),
                    ("flow_document", "document-symbolic", "Document"),
                    ("flow_database", "drive-harddisk-symbolic", "Database Drum"),
                ]),
                ("Callouts & Arrows", [
                    ("speech_bubble", "chat-message-new-symbolic", "Speech Bubble Callout"),
                    ("arrow_right", "go-next-symbolic", "Block Arrow Right"),
                    ("arrow_left", "go-previous-symbolic", "Block Arrow Left"),
                    ("arrow_up", "go-up-symbolic", "Block Arrow Up"),
                    ("arrow_down", "go-down-symbolic", "Block Arrow Down"),
                ]),
                ("3D & Math", [
                    ("solid_cube", "view-grid-symbolic", "3D Cube"),
                    ("solid_cylinder", "drive-harddisk-symbolic", "3D Cylinder"),
                    ("solid_cone", "go-up-symbolic", "3D Cone"),
                    ("solid_sphere", "media-record-symbolic", "3D Sphere"),
                    ("math_axes_2d", "pan-end-symbolic", "2D Cartesian Axes"),
                    ("math_axes_3d", "pan-end-symbolic", "3D Coordinate Axes"),
                    ("math_curly_brace", "format-indent-more-symbolic", "Curly Brace"),
                ])
            ]

            shape_buttons = {}
            for cat_title, shapes_list in shape_categories:
                sec_lbl = Gtk.Label(label=cat_title, xalign=0.0)
                sec_lbl.add_css_class("caption")
                sec_lbl.add_css_class("dim-label")
                st_box.append(sec_lbl)

                flow = Gtk.FlowBox()
                flow.set_valign(Gtk.Align.START)
                flow.set_max_children_per_line(6)
                flow.set_selection_mode(Gtk.SelectionMode.NONE)
                flow.set_row_spacing(3)
                flow.set_column_spacing(3)

                for s_id, s_icon, s_tip in shapes_list:
                    btn = Gtk.Button(
                        icon_name=s_icon,
                        tooltip_text=s_tip
                    )
                    btn.set_size_request(34, 34)
                    if s_id == cur_shape:
                        btn.add_css_class("suggested-action")

                    def _make_handler(sid=s_id):
                        def _handler(_btn):
                            self.canvas.shape_type = sid
                            cur_cfg["shape_type"] = sid
                            for other_id, other_btn in shape_buttons.items():
                                if other_id == sid:
                                    other_btn.add_css_class("suggested-action")
                                else:
                                    other_btn.remove_css_class("suggested-action")
                        return _handler

                    btn.connect("clicked", _make_handler())
                    shape_buttons[s_id] = btn
                    flow.append(btn)

                st_box.append(flow)

            self.tool_settings_box.append(st_box)

            cur_sw = float(getattr(self.canvas, "shape_stroke_width", cur_cfg.get("stroke_width", 3.5)))
            self._add_popover_slider(
                "Border Thickness", 1.0, 32.0, 0.5, cur_sw, "{:.1f} px",
                lambda val: self._on_popover_shape_width_changed(val)
            )

            cur_fill = bool(getattr(self.canvas, "shape_fill", cur_cfg.get("fill", False)))
            fill_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            fill_lbl = Gtk.Label(label="Fill Shape Interior", xalign=0.0, hexpand=True)
            fill_sw = Gtk.Switch(active=cur_fill)
            fill_row.append(fill_lbl)
            fill_row.append(fill_sw)

            def _on_fill_toggled(sw, _):
                val = sw.get_active()
                self.canvas.shape_fill = val
                cur_cfg["fill"] = val

            fill_sw.connect("notify::active", _on_fill_toggled)
            self.tool_settings_box.append(fill_row)

        elif tool == "pen":
            cur_bw = float(cur_cfg.get("width", self.canvas.pen_width))
            self._add_popover_slider(
                "Brush Size", 1.0, 48.0, 0.5, cur_bw, "{:.1f} px",
                lambda val: self._on_popover_brush_size_changed("pen", val)
            )

            press_en = bool(cur_cfg.get("pressure_enabled", True))
            press_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            press_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            press_lbl = Gtk.Label(label="Pressure Sensitivity", xalign=0.0, hexpand=True)
            press_sw = Gtk.Switch(active=press_en)
            press_row.append(press_lbl)
            press_row.append(press_sw)
            press_box.append(press_row)

            press_min = float(cur_cfg.get("pressure_min_factor", 0.453))
            sub_slider_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, visible=press_en)
            self._append_sub_slider(
                sub_slider_box, "Pressure Dynamic Range", 0.15, 0.85, 0.05, press_min, "{:.0f}%",
                lambda v: cur_cfg.update({"pressure_min_factor": v}),
                display_transform=lambda v: int(round((1.0 - v) * 100))
            )
            press_box.append(sub_slider_box)

            def _on_pen_press_toggled(sw, _):
                active = sw.get_active()
                cur_cfg["pressure_enabled"] = active
                sub_slider_box.set_visible(active)

            press_sw.connect("notify::active", _on_pen_press_toggled)
            self.tool_settings_box.append(press_box)

        elif tool == "calligraphy":
            cur_bw = float(cur_cfg.get("width", self.canvas.pen_width))
            self._add_popover_slider(
                "Brush Size", 1.0, 48.0, 0.5, cur_bw, "{:.1f} px",
                lambda val: self._on_popover_brush_size_changed("calligraphy", val)
            )

            cur_angle = float(cur_cfg.get("nib_angle_deg", -35.0))
            self._add_popover_slider(
                "Chisel / Nib Angle", -90.0, 90.0, 5.0, cur_angle, "{:.0f}°",
                lambda val: self._on_popover_nib_angle_changed(val)
            )

            cur_ds = float(cur_cfg.get("direction_strength", 0.78))
            self._add_popover_slider(
                "Directional Variation", 0.20, 1.00, 0.05, cur_ds, "{:.0f}%",
                lambda val: cur_cfg.update({"direction_strength": val}),
                display_transform=lambda v: int(round(v * 100))
            )

            press_en = bool(cur_cfg.get("pressure_enabled", True))
            press_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            press_lbl = Gtk.Label(label="Pressure Sensitivity", xalign=0.0, hexpand=True)
            press_sw = Gtk.Switch(active=press_en)
            press_row.append(press_lbl)
            press_row.append(press_sw)
            press_sw.connect("notify::active", lambda sw, _: cur_cfg.update({"pressure_enabled": sw.get_active()}))
            self.tool_settings_box.append(press_row)

        elif tool == "dashed":
            cur_bw = float(cur_cfg.get("width", self.canvas.pen_width))
            self._add_popover_slider(
                "Line Thickness", 1.0, 32.0, 0.5, cur_bw, "{:.1f} px",
                lambda val: self._on_popover_brush_size_changed("dashed", val)
            )

            dash_len = float(cur_cfg.get("dash_length", 18.0))
            self._add_popover_slider(
                "Dash Length", 4.0, 60.0, 1.0, dash_len, "{:.0f} px",
                lambda val: cur_cfg.update({"dash_length": val})
            )

            dash_gap = float(cur_cfg.get("dash_gap", 12.0))
            self._add_popover_slider(
                "Dash Gap", 3.0, 40.0, 1.0, dash_gap, "{:.0f} px",
                lambda val: cur_cfg.update({"dash_gap": val})
            )

        elif tool == "taper":
            cur_bw = float(cur_cfg.get("width", self.canvas.pen_width))
            self._add_popover_slider(
                "Brush Size", 1.0, 48.0, 0.5, cur_bw, "{:.1f} px",
                lambda val: self._on_popover_brush_size_changed("taper", val)
            )

            taper_min = float(cur_cfg.get("taper_min_factor", 0.345))
            self._add_popover_slider(
                "Taper Thinning", 0.10, 0.80, 0.05, taper_min, "{:.0f}%",
                lambda val: cur_cfg.update({"taper_min_factor": val}),
                display_transform=lambda v: int(round((1.0 - v) * 100))
            )

            press_en = bool(cur_cfg.get("pressure_enabled", True))
            press_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            press_lbl = Gtk.Label(label="Pressure Sensitivity", xalign=0.0, hexpand=True)
            press_sw = Gtk.Switch(active=press_en)
            press_row.append(press_lbl)
            press_row.append(press_sw)
            press_sw.connect("notify::active", lambda sw, _: cur_cfg.update({"pressure_enabled": sw.get_active()}))
            self.tool_settings_box.append(press_row)

        elif tool == "highlighter":
            cur_w = float(cur_cfg.get("width", self.canvas.highlighter_width))
            self._add_popover_slider(
                "Highlighter Width", 8.0, 64.0, 1.0, cur_w, "{:.1f} px",
                lambda val: self._on_popover_highlighter_width_changed(val)
            )

            cur_alpha = float(cur_cfg.get("alpha", 0.50))
            self._add_popover_slider(
                "Highlighter Opacity", 0.15, 0.85, 0.05, cur_alpha, "{:.0f}%",
                lambda val: cur_cfg.update({"alpha": val}),
                display_transform=lambda v: int(round(v * 100))
            )

        elif tool == "eraser":
            cur_rad = float(cur_cfg.get("radius", self.canvas.eraser_radius))
            self._add_popover_slider(
                "Eraser Radius", 4.0, 64.0, 1.0, cur_rad, "{:.1f} px",
                lambda val: self._on_popover_eraser_radius_changed(val)
            )

        elif tool == "text":
            cur_font = getattr(self.canvas, "text_font_family", cur_cfg.get("font_family", "Sans"))
            cur_size = float(getattr(self.canvas, "text_size_pt", cur_cfg.get("point_size", 14.0)))

            sel_item = self.canvas.get_selected_item()
            is_text_sel = bool(sel_item and (sel_item.get("kind") or sel_item.get("type")) == "text")
            if is_text_sel:
                cur_font = sel_item.get("font_family", cur_font)
                cur_size = float(sel_item.get("point_size", cur_size))

            font_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            font_lbl = Gtk.Label(label="Font Family", xalign=0.0)
            font_box.append(font_lbl)

            fonts = ["Sans", "Serif", "Monospace", "Cantarell", "DejaVu Sans", "Liberation Sans", "Ubuntu"]
            font_dd = Gtk.DropDown.new_from_strings(fonts)
            font_dd.set_hexpand(True)
            if cur_font in fonts:
                font_dd.set_selected(fonts.index(cur_font))

            def _on_popover_font_changed(dd, _):
                sel = dd.get_selected_item()
                f_name = sel.get_string() if sel else "Sans"
                self.canvas.text_font_family = f_name
                cur_cfg["font_family"] = f_name
                s_item = self.canvas.get_selected_item()
                if s_item and (s_item.get("kind") or s_item.get("type")) == "text":
                    s_item["font_family"] = f_name
                    self.canvas.invalidate_page(self.canvas.selected_page_idx, clear_bg=False)
                    self.canvas.queue_draw()
                    self._queue_redraw_current_sidebar_thumb()
                    if self.canvas.on_modified_cb:
                        self.canvas.on_modified_cb(True)

            font_dd.connect("notify::selected", _on_popover_font_changed)
            font_box.append(font_dd)
            self.tool_settings_box.append(font_box)

            def _on_popover_text_size_changed(sz_val):
                self.canvas.text_size_pt = sz_val
                cur_cfg["point_size"] = sz_val
                s_item = self.canvas.get_selected_item()
                if s_item and (s_item.get("kind") or s_item.get("type")) == "text":
                    s_item["point_size"] = sz_val
                    self.canvas.invalidate_page(self.canvas.selected_page_idx, clear_bg=False)
                    self.canvas.queue_draw()
                    self._queue_redraw_current_sidebar_thumb()
                    if self.canvas.on_modified_cb:
                        self.canvas.on_modified_cb(True)

            self._add_popover_slider(
                "Font Size", 8.0, 72.0, 1.0, cur_size, "{:.0f} pt",
                _on_popover_text_size_changed
            )

            if is_text_sel:
                edit_btn = Gtk.Button(
                    icon_name="document-edit-symbolic",
                    label="Edit Text Content…"
                )
                def _on_edit_text_clicked(_):
                    if hasattr(self, "tool_settings_popover") and self.tool_settings_popover.get_visible():
                        self.tool_settings_popover.popdown()
                    p_idx = self.canvas.selected_page_idx
                    if p_idx is not None and sel_item:
                        r = sel_item.get("rect") or [sel_item.get("pos", [50, 50])[0], sel_item.get("pos", [50, 50])[1], sel_item.get("width", 260), 60]
                        wx, wy = self.canvas.page_to_widget_coords(p_idx, r[0], r[1])
                        self.canvas._prompt_text_box_creation(wx, wy, p_idx, r, existing_item=sel_item)

                edit_btn.connect("clicked", _on_edit_text_clicked)
                self.tool_settings_box.append(edit_btn)

    def _add_popover_slider(self, label_text, min_val, max_val, step, cur_val, fmt, on_change, display_transform=None):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        lbl = Gtk.Label(label=label_text, xalign=0.0, hexpand=True)
        disp_val = display_transform(cur_val) if display_transform else cur_val
        val_lbl = Gtk.Label(label=fmt.format(disp_val))
        row.append(lbl)
        row.append(val_lbl)
        box.append(row)

        scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, min_val, max_val, step)
        scale.set_value(cur_val)
        scale.set_draw_value(False)

        def _on_val_changed(sc):
            v = sc.get_value()
            dv = display_transform(v) if display_transform else v
            val_lbl.set_text(fmt.format(dv))
            on_change(v)

        scale.connect("value-changed", _on_val_changed)
        box.append(scale)
        self.tool_settings_box.append(box)

    def _append_sub_slider(self, parent_box, label_text, min_val, max_val, step, cur_val, fmt, on_change, display_transform=None):
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        lbl = Gtk.Label(label=label_text, xalign=0.0, hexpand=True)
        disp_val = display_transform(cur_val) if display_transform else cur_val
        val_lbl = Gtk.Label(label=fmt.format(disp_val))
        row.append(lbl)
        row.append(val_lbl)
        parent_box.append(row)

        scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, min_val, max_val, step)
        scale.set_value(cur_val)
        scale.set_draw_value(False)

        def _on_val_changed(sc):
            v = sc.get_value()
            dv = display_transform(v) if display_transform else v
            val_lbl.set_text(fmt.format(dv))
            on_change(v)

        scale.connect("value-changed", _on_val_changed)
        parent_box.append(scale)

    def _on_popover_brush_size_changed(self, tool_name, val):
        self.canvas.pen_width = val
        ts = getattr(self.canvas, "tool_settings", {})
        if tool_name in ts:
            ts[tool_name]["width"] = val
        if hasattr(self, "width_scale") and self.width_scale:
            self.width_scale.set_value(val)

    def _on_popover_shape_fill_toggled(self, val):
        self.canvas.shape_fill = val
        ts = getattr(self.canvas, "tool_settings", {})
        if "shape" in ts:
            ts["shape"]["fill"] = val

    def _on_popover_shape_width_changed(self, val):
        self.canvas.shape_stroke_width = val
        ts = getattr(self.canvas, "tool_settings", {})
        if "shape" in ts:
            ts["shape"]["stroke_width"] = val
        if hasattr(self, "width_scale") and self.width_scale:
            self.width_scale.set_value(val)
        sel_item = self.canvas.get_selected_item()
        if sel_item and (sel_item.get("kind") or sel_item.get("type")) == "shape":
            sel_item["stroke_width"] = val
            self.canvas.invalidate_page(self.canvas.selected_page_idx, clear_bg=False)
            self.canvas.queue_draw()
            self._queue_redraw_current_sidebar_thumb()

    def _on_popover_highlighter_width_changed(self, val):
        self.canvas.highlighter_width = val
        ts = getattr(self.canvas, "tool_settings", {})
        if "highlighter" in ts:
            ts["highlighter"]["width"] = val
        if hasattr(self, "width_scale") and self.width_scale:
            self.width_scale.set_value(val)

    def _on_popover_eraser_radius_changed(self, val):
        self.canvas.eraser_radius = val
        ts = getattr(self.canvas, "tool_settings", {})
        if "eraser" in ts:
            ts["eraser"]["radius"] = val
        if hasattr(self, "width_scale") and self.width_scale:
            self.width_scale.set_value(val)

    def _on_popover_nib_angle_changed(self, val):
        self.canvas.nib_angle_rad = math.radians(val)
        ts = getattr(self.canvas, "tool_settings", {})
        if "calligraphy" in ts:
            ts["calligraphy"]["nib_angle_deg"] = val

    def _on_canvas_modified(self, modified):
        self.is_modified = modified
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

    def on_save_as_clicked(self, *args):
        if hasattr(Gtk, "FileDialog"):
            dialog = Gtk.FileDialog(title="Save As")
            if self.current_file_path:
                base_name = os.path.basename(self.current_file_path)
                folder = Gio.File.new_for_path(os.path.dirname(self.current_file_path))
                dialog.set_initial_folder(folder)
            else:
                if self.current_file_type == "md":
                    base_name = "Untitled.md"
                elif self.current_file_type == "xcanvas":
                    base_name = "Untitled.xcanvas"
                else:
                    base_name = "Untitled.xnote"
                target_folder = self._get_active_category_folder()
                if target_folder and os.path.isdir(target_folder):
                    dialog.set_initial_folder(Gio.File.new_for_path(target_folder))
            dialog.set_initial_name(base_name)

            filters = Gio.ListStore.new(Gtk.FileFilter)
            if self.current_file_type == "md":
                md_filter = Gtk.FileFilter()
                md_filter.set_name("Markdown Documents (*.md, *.markdown)")
                md_filter.add_pattern("*.md")
                md_filter.add_pattern("*.markdown")
                filters.append(md_filter)
            elif self.current_file_type == "xcanvas":
                c_filter = Gtk.FileFilter()
                c_filter.set_name("Infinite Canvases (*.xcanvas)")
                c_filter.add_pattern("*.xcanvas")
                filters.append(c_filter)
            else:
                n_filter = Gtk.FileFilter()
                n_filter.set_name("Notebooks (*.xnote)")
                n_filter.add_pattern("*.xnote")
                filters.append(n_filter)
            dialog.set_filters(filters)
            dialog.save(self, None, self._on_save_as_finished)
        else:
            self.show_error("Gtk.FileDialog is required for Save As.")

    def _on_save_as_finished(self, dialog, result):
        try:
            gfile = dialog.save_finish(result)
            if not gfile or not gfile.get_path():
                return
            new_path = gfile.get_path()
            if self.current_file_type == "md":
                if not new_path.lower().endswith((".md", ".markdown")):
                    new_path += ".md"
                if hasattr(self, "md_buffer"):
                    start, end = self.md_buffer.get_bounds()
                    self.markdown_content = self.md_buffer.get_text(start, end, False)
                with open(new_path, "w", encoding="utf-8") as f:
                    f.write(self.markdown_content)
                self.current_file_path = new_path
                self._on_canvas_modified(False)
                self.window_title.set_title(os.path.splitext(os.path.basename(new_path))[0])
                self.window_title.set_subtitle(os.path.basename(new_path))
                self.show_toast(f"Saved copy to {os.path.basename(new_path)}")
                if self.current_root_folder:
                    self.load_folder(self.current_root_folder)
            else:
                ext = ".xcanvas" if self.current_file_type == "xcanvas" else ".xnote"
                if not new_path.lower().endswith(ext):
                    new_path += ext
                XNotePackageParser.save_archive(
                    new_path,
                    self.canvas.document,
                    templates=self.canvas.templates
                )
                self.current_file_path = new_path
                self.current_file_type = "xcanvas" if new_path.lower().endswith(".xcanvas") else "xnote"
                self._on_canvas_modified(False)
                self.window_title.set_title(os.path.splitext(os.path.basename(new_path))[0])
                self.window_title.set_subtitle(os.path.basename(new_path))
                self.show_toast(f"Saved copy to {os.path.basename(new_path)}")
                if self.current_root_folder:
                    self.load_folder(self.current_root_folder)
        except GLib.Error:
            pass
        except Exception as e:
            self.show_error(f"Failed to save copy: {e}")

    def on_new_canvas_clicked(self, *args):
        if hasattr(Gtk, "FileDialog"):
            dialog = Gtk.FileDialog(title="Create New .xcanvas Infinite Canvas")
            dialog.set_initial_name("Untitled.xcanvas")
            target_folder = self._get_active_category_folder()
            if target_folder and os.path.isdir(target_folder):
                dialog.set_initial_folder(Gio.File.new_for_path(target_folder))
            dialog.save(self, None, self._on_new_canvas_saved)
        else:
            self.show_error("Gtk.FileDialog is required to create a new file.")

    def _on_new_canvas_saved(self, dialog, result):
        try:
            gfile = dialog.save_finish(result)
            if gfile and gfile.get_path():
                path = gfile.get_path()
                if not path.lower().endswith(".xcanvas"):
                    path += ".xcanvas"
                XNotePackageParser.create_new_canvas(path)
                if self.current_root_folder:
                    self.load_folder(self.current_root_folder)
                self.load_document(path)
        except GLib.Error:
            pass

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
            data["show_markdown_files"] = getattr(self, "show_markdown_files", True)
            data["theme"] = getattr(self, "theme_mode", "system")
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

                    saved_show_md = data.get("show_markdown_files", True)
                    self.show_markdown_files = bool(saved_show_md)
                    if hasattr(self, "show_markdown_action"):
                        self.show_markdown_action.set_state(GLib.Variant.new_boolean(self.show_markdown_files))

                    saved_theme = data.get("theme", "system")
                    self.set_theme_mode(saved_theme)

                    last_folder = data.get("last_folder")
                    if last_folder and os.path.isdir(last_folder):
                        self.load_folder(last_folder)
        except Exception as e:
            print(f"Failed to load config: {e}")

    def _setup_actions(self):
        actions = [
            ("open", self.on_open_clicked),
            ("new-category", self.on_new_category_clicked),
            ("rename-category", self.on_rename_category_clicked),
            ("delete-category", self.on_delete_category_clicked),
            ("new-notebook", self.on_new_notebook_clicked),
            ("new-canvas", self.on_new_canvas_clicked),
            ("import-pdf-new", self.on_import_pdf_as_new_notebook_clicked),
            ("import-rnote-new", self.on_import_rnote_as_new_notebook_clicked),
            ("export", self.on_export_document_clicked),
            ("export-pdf", self.on_export_document_clicked),
            ("export-rnote", self.on_export_document_clicked),
            ("new-markdown", self.on_new_markdown_clicked),
            ("save", self.on_save_clicked),
            ("save-as", self.on_save_as_clicked),
            ("undo", self.on_undo_activated),
            ("redo", self.on_redo_activated),
            ("cut", lambda *_: self._on_clipboard_action("cut")),
            ("copy", lambda *_: self._on_clipboard_action("copy")),
            ("paste", lambda *_: self._on_clipboard_action("paste")),
            ("zoom-in", lambda *_: self.set_zoom(self.canvas.zoom_level + 0.25)),
            ("zoom-out", lambda *_: self.set_zoom(self.canvas.zoom_level - 0.25)),
            ("zoom-reset", lambda *_: self.set_zoom(1.0)),
            ("fit-width", self.on_fit_width_clicked),
            ("fit-height", self.on_fit_height_clicked),
            ("toggle-sidebar", self.on_toggle_sidebar_action),
            ("next-page", self.on_next_page),
            ("prev-page", self.on_prev_page),
            ("toggle-view-mode", self.on_toggle_view_mode_action),
            ("toggle-zoom-lock", self.on_toggle_zoom_lock_action),
            ("toggle-double-page", self.on_toggle_double_page_action),
            ("record-audio", lambda *_: self._on_record_audio_clicked(None)),
            ("attach-audio", lambda *_: self._on_attach_audio_clicked(None)),
            ("back", self.on_back_clicked),
            ("find", self.on_find_shortcut),
            ("preferences", self.show_preferences_dialog),
            ("shortcuts", self.show_shortcuts_dialog),
            ("about", self.show_about_dialog),
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

        self.show_markdown_action = Gio.SimpleAction.new_stateful(
            "show-markdown",
            None,
            GLib.Variant.new_boolean(self.show_markdown_files)
        )
        self.show_markdown_action.connect("change-state", self._on_show_markdown_change_state)
        self.show_markdown_action.connect("activate", self._on_show_markdown_activate)
        self.add_action(self.show_markdown_action)

    def _on_show_markdown_change_state(self, action, value):
        action.set_state(value)
        self.show_markdown_files = value.get_boolean()
        self.save_config()
        self._apply_collection_filters()

    def _on_show_markdown_activate(self, action, param):
        current = action.get_state().get_boolean()
        action.change_state(GLib.Variant.new_boolean(not current))

    def _on_style_dark_changed(self, is_dark):
        if hasattr(self, "md_inline_styler") and self.md_inline_styler:
            self.md_inline_styler.update_theme_colors(is_dark)
        if hasattr(self, "md_paper_box") and self.md_paper_box and getattr(self, "current_file_type", None) == "md":
            MarkdownRenderer.populate_box(
                self.md_paper_box, getattr(self, "markdown_content", ""), getattr(self.canvas, "zoom_level", 1.0)
            )

    def set_theme_mode(self, mode):
        self.theme_mode = mode
        sm = Adw.StyleManager.get_default()
        if mode == "light":
            sm.set_color_scheme(Adw.ColorScheme.FORCE_LIGHT)
        elif mode == "dark":
            sm.set_color_scheme(Adw.ColorScheme.FORCE_DARK)
        else:
            sm.set_color_scheme(Adw.ColorScheme.DEFAULT)
        if hasattr(self, "md_inline_styler") and self.md_inline_styler:
            self.md_inline_styler.update_theme_colors(sm.get_dark())
        if hasattr(self, "md_paper_box") and self.md_paper_box and getattr(self, "current_file_type", None) == "md":
            MarkdownRenderer.populate_box(
                self.md_paper_box, getattr(self, "markdown_content", ""), getattr(self.canvas, "zoom_level", 1.0)
            )
        self.save_config()

    def show_preferences_dialog(self, *args):
        dlg = Adw.PreferencesDialog()
        dlg.set_title("Preferences")

        page = Adw.PreferencesPage()
        page.set_title("General")
        page.set_icon_name("preferences-system-symbolic")

        # Appearance Group
        grp_theme = Adw.PreferencesGroup()
        grp_theme.set_title("Appearance")
        grp_theme.set_description("Select theme for the user interface and text notes.")

        theme_row = Adw.ComboRow()
        theme_row.set_title("Theme")
        theme_row.set_subtitle("Notebook pages always retain their original paper color.")
        theme_model = Gtk.StringList.new(["Follow System", "Light", "Dark"])
        theme_row.set_model(theme_model)

        cur_theme = getattr(self, "theme_mode", "system")
        if cur_theme == "light":
            theme_row.set_selected(1)
        elif cur_theme == "dark":
            theme_row.set_selected(2)
        else:
            theme_row.set_selected(0)

        def _on_theme_row_selected(row, _param):
            idx = row.get_selected()
            if idx == 1:
                self.set_theme_mode("light")
            elif idx == 2:
                self.set_theme_mode("dark")
            else:
                self.set_theme_mode("system")

        theme_row.connect("notify::selected", _on_theme_row_selected)
        grp_theme.add(theme_row)
        page.add(grp_theme)

        # Library Group
        grp_lib = Adw.PreferencesGroup()
        grp_lib.set_title("Library")

        md_row = Adw.SwitchRow()
        md_row.set_title("Show Markdown Files")
        md_row.set_subtitle("Display text notes (.md) in the library alongside notebooks.")
        md_row.set_active(bool(getattr(self, "show_markdown_files", True)))

        def _on_md_row_active(row, _param):
            want = row.get_active()
            if self.show_markdown_action.get_state().get_boolean() != want:
                self.show_markdown_action.change_state(GLib.Variant.new_boolean(want))

        md_row.connect("notify::active", _on_md_row_active)
        grp_lib.add(md_row)
        page.add(grp_lib)

        # Storage Group
        grp_dir = Adw.PreferencesGroup()
        grp_dir.set_title("Notebook Storage")
        grp_dir.set_description("Select the default directory for your notebooks and notes.")

        dir_row = Adw.ActionRow()
        dir_row.set_title("Notebook Directory")
        cur_dir = self.current_root_folder or os.path.expanduser("~/Notebooks")
        dir_row.set_subtitle(cur_dir)

        choose_btn = Gtk.Button(label="Select Folder…")
        choose_btn.set_valign(Gtk.Align.CENTER)

        def _on_choose_folder_clicked(_btn):
            dialog = Gtk.FileChooserNative.new(
                "Select Notebook Directory",
                self,
                Gtk.FileChooserAction.SELECT_FOLDER,
                "Select",
                "Cancel"
            )
            if self.current_root_folder and os.path.isdir(self.current_root_folder):
                try:
                    dialog.set_current_folder(Gio.File.new_for_path(self.current_root_folder))
                except Exception:
                    pass

            def _on_response(d, resp):
                if resp == Gtk.ResponseType.ACCEPT:
                    f = d.get_file()
                    if f and f.get_path():
                        chosen = f.get_path()
                        dir_row.set_subtitle(chosen)
                        self.load_folder(chosen)
                        self.save_config(chosen)
                        self.show_toast(f"Notebook directory set to {os.path.basename(chosen)}")

            dialog.connect("response", _on_response)
            dialog.show()

        choose_btn.connect("clicked", _on_choose_folder_clicked)
        dir_row.add_suffix(choose_btn)
        dir_row.set_activatable_widget(choose_btn)
        grp_dir.add(dir_row)
        page.add(grp_dir)

        dlg.add(page)
        dlg.present(self)

    def show_shortcuts_dialog(self, *args):
        categories = [
            ("general", "General", "preferences-system-symbolic", [
                ("Application", [
                    ("Preferences", "<Control>comma"),
                    ("Keyboard Shortcuts", "<Control>question"),
                    ("About", "F1"),
                    ("Quit", "<Control>q"),
                ]),
                ("Library", [
                    ("New Notebook", "<Control>n"),
                    ("Open Collection Folder", "<Control>o"),
                    ("Filter / Search Notes", "<Control>f"),
                    ("Back to Library", "Escape"),
                ]),
            ]),
            ("editing", "Editing", "document-edit-symbolic", [
                ("Document", [
                    ("Save", "<Control>s"),
                    ("Save As", "<Control><Shift>s"),
                    ("Export", "<Control>e"),
                ]),
                ("History", [
                    ("Undo", "<Control>z"),
                    ("Redo", "<Control>y <Control><Shift>z"),
                ]),
                ("Clipboard", [
                    ("Cut", "<Control>x"),
                    ("Copy", "<Control>c"),
                    ("Paste", "<Control>v"),
                ]),
            ]),
            ("view", "View", "zoom-in-symbolic", [
                ("Zoom", [
                    ("Zoom In", "<Control>plus"),
                    ("Zoom Out", "<Control>minus"),
                    ("Reset Zoom", "<Control>0"),
                    ("Fit Page Width", "<Control><Alt>w"),
                    ("Fit Page Height", "<Control><Alt>h"),
                    ("Lock Zoom", "<Control>l"),
                ]),
                ("Pages", [
                    ("Toggle Page Sidebar", "F9"),
                    ("Cycle View Type", "<Control>m"),
                    ("Double Page View", "<Control>d"),
                    ("Next Page", "Page_Down"),
                    ("Previous Page", "Page_Up"),
                ]),
            ]),
            ("markdown", "Text Notes", "text-x-generic-symbolic", [
                ("Formatting", [
                    ("Bold", "<Control>b"),
                    ("Italic", "<Control>i"),
                    ("Strikethrough", "<Control><Shift>x"),
                    ("Inline Code", "<Control>e"),
                ]),
                ("Structure", [
                    ("Heading 1", "<Control>1"),
                    ("Heading 2", "<Control>2"),
                    ("Bullet List", "<Control><Shift>l"),
                    ("Blockquote", "<Control><Shift>q"),
                    ("Insert Link", "<Control>k"),
                ]),
                ("Search", [
                    ("Find in Note", "<Control>f"),
                    ("Next Match", "Return"),
                    ("Previous Match", "<Shift>Return"),
                ]),
            ]),
        ]

        stack = Adw.ViewStack()
        for name, title, icon, groups in categories:
            pref_page = Adw.PreferencesPage()
            for grp_title, items in groups:
                grp = Adw.PreferencesGroup(title=grp_title)
                for label, accel in items:
                    row = Adw.ActionRow(title=label)
                    sc = Gtk.ShortcutLabel(accelerator=accel, valign=Gtk.Align.CENTER)
                    row.add_suffix(sc)
                    grp.add(row)
                pref_page.add(grp)
            stack.add_titled_with_icon(pref_page, name, title, icon)

        switcher = Adw.ViewSwitcher(stack=stack, policy=Adw.ViewSwitcherPolicy.WIDE)
        header = Adw.HeaderBar()
        header.set_title_widget(switcher)

        toolbar_view = Adw.ToolbarView()
        toolbar_view.add_top_bar(header)
        toolbar_view.set_content(stack)

        dlg = Adw.Dialog()
        dlg.set_title("Keyboard Shortcuts")
        dlg.set_content_width(620)
        dlg.set_content_height(560)
        dlg.set_child(toolbar_view)
        dlg.present(self)

    def show_about_dialog(self, *args):
        about = Adw.AboutDialog()
        about.set_application_name("Notebook")
        about.set_application_icon("io.github.xnotes.Notebook")
        about.set_version("1.0.0")
        about.set_developer_name("Notebook Developers")
        about.set_developers(["Notebook Developers"])
        about.set_comments("Note-taking, sketching, and infinite canvas editor for GNOME")
        about.set_website("https://github.com/GNOME/Notebook")
        about.set_issue_url("https://github.com/GNOME/Notebook/issues")
        about.set_license_type(Gtk.License.GPL_3_0)
        about.present(self)

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
        if not button.get_active():
            return
        target_page = self.canvas.current_page
        if mode == "double":
            self.canvas.view_mode = "paged"
            self.canvas.viewing_mode = "double"
            self.canvas.current_page = target_page - (target_page % 2)
        elif mode == "paged":
            self.canvas.view_mode = "paged"
            self.canvas.viewing_mode = "single"
        elif mode == "scrolled":
            self.canvas.view_mode = "scrolled"
            self.canvas.viewing_mode = "single"

        self.canvas.page_cache.clear()
        self.canvas.bg_cache.clear()
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
        for idx, geom in enumerate(self.canvas.page_geometries):
            y_offset = geom[1] if len(geom) >= 6 else geom[0]
            scaled_h = geom[5] if len(geom) >= 6 else geom[4]
            if y_offset <= viewport_center <= y_offset + scaled_h + 24.0:
                best_page = idx
                break

        if best_page != self.canvas.current_page:
            self.canvas.current_page = best_page
            total = len(self.canvas.document.get("pages", []))
            self._sync_overlay_nav_buttons()
            self._sync_page_sidebar_selection()
            self._sync_toolbar_page_color_button()

    def scroll_to_page(self, page_idx):
        if self.canvas.view_mode == "scrolled" and 0 <= page_idx < len(self.canvas.page_geometries):
            self._updating_scroll = True
            geom = self.canvas.page_geometries[page_idx]
            y_offset = geom[1] if len(geom) >= 6 else geom[0]
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
        if hasattr(self, "md_mode_box"):
            self.md_mode_box.set_visible(False)
        if hasattr(self, "zoom_box"):
            self.zoom_box.set_sensitive(True)
        if hasattr(self, "toolbar_container"):
            self.toolbar_container.set_visible(False)
        self._cancel_pending_thumb_jobs()
        self._thumb_spinner_widgets.clear()
        self.md_find_btn.set_active(False)
        self.canvas.clear_selection()
        self.canvas.page_cache.clear()
        self.canvas.bg_cache.clear()
        self.canvas.thumb_cache.clear()
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
        # Use native file chooser directly for 100% reliable folder selection
        try:
            self._fallback_open_folder()
        except Exception as e:
            if hasattr(Gtk, "FileDialog"):
                dialog = Gtk.FileDialog(title="Open Notebook Collection Folder")
                dialog.select_folder(self, None, self.on_folder_selected)

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
            self._cancel_pending_thumb_jobs()
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
            win_w = self.get_width() or 1200
            self.md_paned.set_position(win_w)

            self.window_title.set_title(os.path.splitext(os.path.basename(path))[0])
            self.window_title.set_subtitle("")
            self.viewer_stack.set_visible_child_name("markdown_doc")
            self.page_sidebar_btn.set_visible(False)
            if hasattr(self, "md_mode_box"):
                self.md_mode_box.set_visible(True)
            self.mode_box.set_visible(False)
            if hasattr(self, "template_menu_btn"):
                self.template_menu_btn.set_visible(False)
            self.stylus_bar.set_visible(False)
            if hasattr(self, "stylus_scroll"):
                self.stylus_scroll.set_visible(False)
            if hasattr(self, "add_page_btn"):
                self.add_page_btn.set_visible(False)
            self._place_shared_toolbar_widgets("md")
            self.clipboard_box.set_visible(True)
            self.md_toolbar_bar.set_visible(True)
            if hasattr(self, "toolbar_container"):
                self.toolbar_container.set_visible(True)
            self.save_btn.set_visible(True)
            self.export_pdf_btn.set_visible(False)
            self.history_box.set_visible(True)
            self.zoom_reset_btn.set_label("100%")
            if hasattr(self, "zoom_box"):
                self.zoom_box.set_sensitive(self.md_view_mode in ("split", "preview"))
            self.root_stack.set_visible_child_name("viewer")
            self.md_textview.grab_focus()
            GLib.idle_add(self.on_fit_width_clicked)
        except Exception as e:
            self.show_error(f"Failed to load Markdown file {os.path.basename(path)}: {e}")

    def _load_xnote_document(self, path, ext):
        try:
            self._cancel_pending_thumb_jobs()
            self._thumb_spinner_widgets.clear()
            self.md_find_btn.set_active(False)
            self.canvas.clear_selection()
            self.canvas.assets.clear()
            self.canvas.templates.clear()
            self.canvas.page_cache.clear()
            self.canvas.bg_cache.clear()
            self.canvas.thumb_cache.clear()
            self.canvas.undo_stack.clear()
            self.canvas.redo_stack.clear()
            self._on_history_changed(False, False)
            self._on_canvas_modified(False)

            doc, assets, templates = XNotePackageParser.load_archive(path)
            self.canvas.document = doc
            self.canvas.assets = assets
            self.canvas.templates = templates
            if ext != ".xcanvas":
                self._load_system_templates()

            if ext == ".xcanvas" or doc.get("format") == "xcanvas":
                self.current_file_type = "xcanvas"
            elif ext == ".xtemplate" or doc.get("format") == "xtemplate":
                self.current_file_type = "xtemplate"
            else:
                self.current_file_type = "xnote"

            is_canvas = (self.current_file_type == "xcanvas")
            self.canvas.current_page = 0
            pages = self.canvas.document.get("pages", [])
            self.canvas.page_sizes = [self.canvas.compute_page_size(p) for p in pages]

            self.window_title.set_title(os.path.splitext(os.path.basename(path))[0])
            self.window_title.set_subtitle("")
            self.viewer_stack.set_visible_child_name("canvas_doc")
            is_editable = self.current_file_type in ("xnote", "xcanvas")

            self.page_sidebar_btn.set_visible(is_editable and not is_canvas)
            self.page_sidebar_btn.set_active(False)
            self.page_sidebar_revealer.set_reveal_child(False)

            if hasattr(self, "md_mode_box"):
                self.md_mode_box.set_visible(False)
            self.mode_box.set_visible(not is_canvas)
            if hasattr(self, "template_menu_btn"):
                self.template_menu_btn.set_visible(is_editable)
            self.md_toolbar_bar.set_visible(False)
            self._place_shared_toolbar_widgets("stylus")
            # Toolbar always visible so view types & zoom remain reachable; editing groups follow is_editable
            self.stylus_bar.set_visible(True)
            if hasattr(self, "stylus_scroll"):
                self.stylus_scroll.set_visible(True)
            self.center_tools_box.set_visible(is_editable)
            self.clipboard_box.set_visible(is_editable)
            self.page_tools_box.set_visible(is_editable)
            if hasattr(self, "add_page_btn"):
                self.add_page_btn.set_visible(is_editable and not is_canvas)
            if hasattr(self, "toolbar_container"):
                self.toolbar_container.set_visible(True)
            self.save_btn.set_visible(True)
            self.export_pdf_btn.set_visible(False)
            if hasattr(self, "zoom_box"):
                self.zoom_box.set_sensitive(True)
            self.history_box.set_visible(is_editable)
            self.root_stack.set_visible_child_name("viewer")

            if is_canvas:
                view = self.canvas.document.get("view", {})
                self.canvas.zoom_level = float(view.get("zoom", 1.0))
                self.canvas.set_halign(Gtk.Align.FILL)
                self.canvas.set_valign(Gtk.Align.FILL)
                self.canvas.set_hexpand(True)
                self.canvas.set_vexpand(True)
                self.canvas.set_content_width(0)
                self.canvas.set_content_height(0)
            elif self.canvas.page_sizes:
                self.canvas.set_hexpand(False)
                self.canvas.set_vexpand(False)
                pw, _ = self.canvas.page_sizes[0]
                avail_w = self.scrolled.get_width() or self.get_width() or 1240
                if pw > 0:
                    self.canvas.zoom_level = max(0.1, min((avail_w - 48.0) / pw, 4.0))
                else:
                    self.canvas.zoom_level = 1.0
            else:
                self.canvas.set_hexpand(False)
                self.canvas.set_vexpand(False)
                self.canvas.zoom_level = 1.0

            self.vadj.set_value(0)
            self.update_page_View()
            if not is_canvas:
                self._rebuild_page_sidebar()
                GLib.idle_add(self.on_fit_width_clicked)
        except Exception as e:
            self.show_error(f"Failed to load {os.path.basename(path)}: {e}")

    def update_page_View(self):
        if not self.canvas.document:
            return

        if self.current_file_type == "xcanvas":
            self.prev_btn.set_visible(False)
            self.next_btn.set_visible(False)
            self.page_badge.set_visible(False)
            self.canvas.set_halign(Gtk.Align.FILL)
            self.canvas.set_valign(Gtk.Align.FILL)
            self.canvas.set_hexpand(True)
            self.canvas.set_vexpand(True)
            self.canvas.set_content_width(0)
            self.canvas.set_content_height(0)
            self.zoom_reset_btn.set_label(f"{int(self.canvas.zoom_level * 100)}%")
            self.canvas.queue_draw()
            return

        pages = self.canvas.document.get("pages", [])
        total = len(pages)

        if total > 0:
            self.canvas.current_page = max(0, min(self.canvas.current_page, total - 1))
            self._sync_overlay_nav_buttons()
            is_double = (getattr(self.canvas, "viewing_mode", "single") == "double")
            self.canvas.page_geometries = []

            if self.canvas.view_mode == "paged":
                self.canvas.set_valign(Gtk.Align.CENTER)
                if is_double:
                    left_idx = self.canvas.current_page - (self.canvas.current_page % 2)
                    right_idx = left_idx + 1 if left_idx + 1 < total else None

                    pw_l, ph_l = self.canvas.get_page_size(left_idx)
                    sw_l = pw_l * self.canvas.zoom_level
                    sh_l = ph_l * self.canvas.zoom_level

                    if right_idx is not None:
                        pw_r, ph_r = self.canvas.get_page_size(right_idx)
                        sw_r = pw_r * self.canvas.zoom_level
                        sh_r = ph_r * self.canvas.zoom_level
                        gap = 20.0
                        total_w = sw_l + gap + sw_r
                        max_h = max(sh_l, sh_r)
                        for idx in range(total):
                            if idx == left_idx:
                                ox_l = 20.0
                                oy_l = 20.0 + (max_h - sh_l) / 2.0
                                self.canvas.page_geometries.append((ox_l, oy_l, pw_l, ph_l, sw_l, sh_l))
                            elif idx == right_idx:
                                ox_r = 20.0 + sw_l + gap
                                oy_r = 20.0 + (max_h - sh_r) / 2.0
                                self.canvas.page_geometries.append((ox_r, oy_r, pw_r, ph_r, sw_r, sh_r))
                            else:
                                p_w, p_h = self.canvas.get_page_size(idx)
                                self.canvas.page_geometries.append((20.0, 20.0, p_w, p_h, p_w * self.canvas.zoom_level, p_h * self.canvas.zoom_level))
                        self.canvas.set_content_width(int(total_w) + 40)
                        self.canvas.set_content_height(int(max_h) + 84)
                    else:
                        for idx in range(total):
                            p_w, p_h = self.canvas.get_page_size(idx)
                            self.canvas.page_geometries.append((20.0, 20.0, p_w, p_h, p_w * self.canvas.zoom_level, p_h * self.canvas.zoom_level))
                        self.canvas.set_content_width(int(sw_l) + 40)
                        self.canvas.set_content_height(int(sh_l) + 84)
                else:
                    pw, ph = self.canvas.get_page_size(self.canvas.current_page)
                    for idx in range(total):
                        p_w, p_h = self.canvas.get_page_size(idx)
                        self.canvas.page_geometries.append((20.0, 20.0, p_w, p_h, p_w * self.canvas.zoom_level, p_h * self.canvas.zoom_level))
                    self.canvas.set_content_width(int(pw * self.canvas.zoom_level) + 40)
                    self.canvas.set_content_height(int(ph * self.canvas.zoom_level) + 84)
            else:
                self.canvas.set_valign(Gtk.Align.START)
                if is_double:
                    gap = 20.0
                    vert_gap = 24.0
                    rows = []
                    idx = 0
                    while idx < total:
                        if idx + 1 < total:
                            rows.append((idx, idx + 1))
                            idx += 2
                        else:
                            rows.append((idx,))
                            idx += 1

                    row_sizes = []
                    max_w = 0.0
                    for row in rows:
                        if len(row) == 2:
                            pw1, ph1 = self.canvas.get_page_size(row[0])
                            pw2, ph2 = self.canvas.get_page_size(row[1])
                            sw1 = pw1 * self.canvas.zoom_level
                            sh1 = ph1 * self.canvas.zoom_level
                            sw2 = pw2 * self.canvas.zoom_level
                            sh2 = ph2 * self.canvas.zoom_level
                            rw = sw1 + gap + sw2
                            rh = max(sh1, sh2)
                            row_sizes.append((rw, rh, sw1, sh1, sw2, sh2, pw1, ph1, pw2, ph2))
                            max_w = max(max_w, rw)
                        else:
                            pw1, ph1 = self.canvas.get_page_size(row[0])
                            sw1 = pw1 * self.canvas.zoom_level
                            sh1 = ph1 * self.canvas.zoom_level
                            row_sizes.append((sw1, sh1, sw1, sh1, 0, 0, pw1, ph1, 0, 0))
                            max_w = max(max_w, sw1)

                    current_y = 20.0
                    geom_by_idx = {}
                    for row, rsize in zip(rows, row_sizes):
                        rw, rh = rsize[0], rsize[1]
                        row_start_x = 20.0 + max(0.0, (max_w - rw) / 2.0)
                        if len(row) == 2:
                            sw1, sh1, sw2, sh2 = rsize[2], rsize[3], rsize[4], rsize[5]
                            pw1, ph1, pw2, ph2 = rsize[6], rsize[7], rsize[8], rsize[9]
                            geom_by_idx[row[0]] = (row_start_x, current_y + (rh - sh1) / 2.0, pw1, ph1, sw1, sh1)
                            geom_by_idx[row[1]] = (row_start_x + sw1 + gap, current_y + (rh - sh2) / 2.0, pw2, ph2, sw2, sh2)
                        else:
                            sw1, sh1 = rsize[2], rsize[3]
                            pw1, ph1 = rsize[6], rsize[7]
                            geom_by_idx[row[0]] = (row_start_x, current_y, pw1, ph1, sw1, sh1)
                        current_y += rh + vert_gap

                    self.canvas.page_geometries = [geom_by_idx[i] for i in range(total)]
                    total_h = current_y - vert_gap + 64.0
                    self.canvas.set_content_width(int(max_w) + 40)
                    self.canvas.set_content_height(int(total_h))
                else:
                    max_w = 0.0
                    current_y = 20.0
                    page_gap = 24.0
                    for idx in range(total):
                        pw, ph = self.canvas.get_page_size(idx)
                        scaled_w = pw * self.canvas.zoom_level
                        scaled_h = ph * self.canvas.zoom_level
                        self.canvas.page_geometries.append((20.0, current_y, pw, ph, scaled_w, scaled_h))
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
        if getattr(self.canvas, "zoom_locked", False):
            return
        if self.current_file_type == "md" and hasattr(self, "zoom_box") and not self.zoom_box.get_sensitive():
            return
        new_zoom = max(0.1, min(zoom, 4.0))
        if abs(new_zoom - self.canvas.zoom_level) > 1e-4:
            self.canvas.zoom_level = new_zoom
            self.zoom_reset_btn.set_label(f"{int(self.canvas.zoom_level * 100)}%")
            if self.current_file_type == "xcanvas":
                self.canvas.document.setdefault("view", {})["zoom"] = self.canvas.zoom_level
                self.canvas.queue_draw()
            elif self.current_file_type == "md":
                if self.md_view_mode == "preview":
                    self.md_paper_box.set_size_request(int(680 * self.canvas.zoom_level), -1)
                MarkdownRenderer.populate_box(
                    self.md_paper_box, self.markdown_content, self.canvas.zoom_level
                )
            elif self.canvas.document:
                self.canvas.page_cache.clear()
                self.canvas.bg_cache.clear()
                self.update_page_View()

    def on_prev_page(self, *args):
        if not self.canvas.document:
            return
        is_double = (getattr(self.canvas, "viewing_mode", "single") == "double" and self.canvas.view_mode == "paged")
        if is_double:
            left = self.canvas.current_page - (self.canvas.current_page % 2)
            if left > 0:
                self.canvas.clear_selection()
                old_p = self.canvas.current_page
                self.canvas.current_page = max(0, left - 2)
                self.update_page_View()
                self._flash_page_badge()
                self.canvas.start_page_slide_animation(old_p, self.canvas.current_page)
        else:
            if self.canvas.current_page > 0:
                self.canvas.clear_selection()
                old_p = self.canvas.current_page
                self.canvas.current_page -= 1
                self.update_page_View()
                self._flash_page_badge()
                if self.canvas.view_mode == "paged":
                    self.canvas.start_page_slide_animation(old_p, self.canvas.current_page)
                elif self.canvas.view_mode == "scrolled":
                    self.scroll_to_page(self.canvas.current_page)

    def _on_next_overlay_btn_clicked(self, *args):
        self._flash_page_badge()
        if not self.canvas.document or self.current_file_type == "xcanvas":
            return
        if self.next_btn.get_sensitive():
            self.on_next_page()

    def _sync_overlay_nav_buttons(self):
        if not self.canvas.document or self.current_file_type == "xcanvas":
            self.prev_btn.set_visible(False)
            self.next_btn.set_visible(False)
            self.page_badge.set_visible(False)
            return

        pages = self.canvas.document.get("pages", [])
        total = len(pages)
        cur = self.canvas.current_page
        is_scrolled = (getattr(self.canvas, "view_mode", "paged") == "scrolled")
        is_double = (getattr(self.canvas, "viewing_mode", "single") == "double" and not is_scrolled)

        if is_double:
            left = cur - (cur % 2)
            can_prev = (left > 0)
            can_next = (left + 2 < total)
        else:
            can_prev = (cur > 0)
            can_next = (cur < total - 1)

        self.prev_btn.set_visible(total > 1 and not is_scrolled)
        self.prev_btn.set_sensitive(can_prev)

        self.page_badge.set_visible(total > 0)
        if total > 0:
            if is_double:
                left = cur - (cur % 2)
                right = left + 1 if left + 1 < total else None
                if right is not None:
                    self.page_label.set_label(f"{left + 1}-{right + 1} / {total}")
                else:
                    self.page_label.set_label(f"{left + 1} / {total}")
            else:
                self.page_label.set_label(f"{cur + 1} / {total}")

        if total > 1 and not is_scrolled:
            self.next_btn.set_visible(True)
            self.next_btn.set_sensitive(can_next)
            self.next_btn.set_icon_name("go-next-symbolic")
            self.next_btn.set_tooltip_text("Next Page (Page Down)")
        else:
            self.next_btn.set_visible(False)

    def on_next_page(self, *args):
        if not self.canvas.document:
            return
        pages = self.canvas.document.get("pages", [])
        total = len(pages)
        is_double = (getattr(self.canvas, "viewing_mode", "single") == "double" and self.canvas.view_mode == "paged")
        if is_double:
            left = self.canvas.current_page - (self.canvas.current_page % 2)
            if left + 2 < total:
                self.canvas.clear_selection()
                old_p = self.canvas.current_page
                self.canvas.current_page = left + 2
                self.update_page_View()
                self._flash_page_badge()
                self.canvas.start_page_slide_animation(old_p, self.canvas.current_page)
        else:
            if self.canvas.current_page < total - 1:
                self.canvas.clear_selection()
                old_p = self.canvas.current_page
                self.canvas.current_page += 1
                self.update_page_View()
                self._flash_page_badge()
                if self.canvas.view_mode == "paged":
                    self.canvas.start_page_slide_animation(old_p, self.canvas.current_page)
                elif self.canvas.view_mode == "scrolled":
                    self.scroll_to_page(self.canvas.current_page)

    def show_toast(self, message, timeout=3):
        toast = Adw.Toast.new(message)
        toast.set_timeout(timeout)
        self.toast_overlay.add_toast(toast)

    def show_error(self, message):
        self.show_toast(message, timeout=4)

    # =========================================================================
    # Zoom Lock, Double Page View, and Audio Recording & Attachment
    # =========================================================================
    def _on_zoom_lock_toggled(self, btn):
        locked = btn.get_active()
        self.canvas.zoom_locked = locked
        lock_icon = self._resolve_icon_name("lock-symbolic")
        unlock_icon = self._resolve_icon_name("unlock-symbolic")
        btn.set_icon_name(lock_icon if locked else unlock_icon)
        btn.set_tooltip_text("Unlock Zoom (Ctrl+L)" if locked else "Lock Zoom (Ctrl+L)")
        self.zoom_in_btn.set_sensitive(not locked)
        self.zoom_out_btn.set_sensitive(not locked)
        self.zoom_reset_btn.set_sensitive(not locked)
        self.show_toast(f"Zoom locked at {int(self.canvas.zoom_level * 100)}%" if locked else "Zoom unlocked")

    def on_toggle_zoom_lock_action(self, *args):
        if hasattr(self, "zoom_lock_btn"):
            self.zoom_lock_btn.set_active(not self.zoom_lock_btn.get_active())

    def _on_double_page_toggled(self, btn):
        is_double = btn.get_active()
        self.canvas.viewing_mode = "double" if is_double else "single"
        self.canvas.page_cache.clear()
        self.canvas.bg_cache.clear()
        self.update_page_View()
        self.show_toast("Double Page View" if is_double else "Single Page View")

    def on_toggle_double_page_action(self, *args):
        if hasattr(self, "double_page_btn"):
            self.double_page_btn.set_active(not self.double_page_btn.get_active())

    def _on_record_audio_clicked(self, _btn):
        from audio_manager import AudioManager
        am = AudioManager.get_default()
        if am.is_recording:
            return

        all_items = []
        if self.canvas.document:
            for p in self.canvas.document.get("pages", []):
                all_items.extend(p.get("items", []))
            all_items.extend(self.canvas.document.get("items", []))
        rids = [it.get("rid", 0) for it in all_items if it.get("kind") == "audio"]
        am._active_rid = (max(rids) + 1) if rids else 1

        def _on_tick(elapsed_ms):
            sec = elapsed_ms // 1000
            self.rec_time_lbl.set_label(f"{sec // 60:02d}:{sec % 60:02d}")

        success = am.start_recording(on_tick=_on_tick)
        if success:
            self.rec_banner.set_visible(True)
            if hasattr(self, "insert_media_box"): self.insert_media_box.set_visible(False)
            self.show_toast("Recording voice note… You can draw notes while recording!")
        else:
            self.show_toast("Failed to start audio recording.")

    def _on_stop_recording_clicked(self, _btn):
        import hashlib
        from audio_manager import AudioManager
        am = AudioManager.get_default()
        res = am.stop_recording()
        self.rec_banner.set_visible(False)
        if hasattr(self, "insert_media_box"): self.insert_media_box.set_visible(True)

        if not res or not self.canvas.document:
            return

        digest = hashlib.sha1(res["data"]).hexdigest()[:16]
        ext = res.get("ext", ".m4a")
        asset_name = f"assets/audio-{digest}{ext}"
        self.canvas.document.setdefault("_raw_assets", {})[asset_name] = res["data"]

        p_idx = self.canvas.current_page
        pages = self.canvas.document.get("pages", [])
        if 0 <= p_idx < len(pages):
            page = pages[p_idx]
            pw, ph = self.canvas.get_page_size(p_idx)
            cx = pw / 2.0
            cy = ph / 2.0
        else:
            page = self.canvas.document
            cx, cy = 300.0, 300.0

        pill_w, pill_h = 230.0, 48.0
        rect = [round(cx - pill_w / 2.0, 1), round(cy - pill_h / 2.0, 1), pill_w, pill_h]
        rec_num = getattr(am, "_active_rid", 1)

        audio_item = {
            "kind": "audio",
            "asset": asset_name,
            "rect": rect,
            "name": f"Recording {rec_num}",
            "rid": rec_num,
            "duration_ms": res["duration_ms"],
            "peaks": res["peaks"],
            "playhead": -1,
            "playing": False
        }

        old_items = list(page.get("items", []))
        page.setdefault("items", []).append(audio_item)
        self.canvas._record_undo_step(p_idx, old_items, list(page["items"]), clone=False)
        self.canvas.invalidate_page(p_idx, clear_bg=False)
        self.canvas.queue_draw()
        self.show_toast(f"Voice note placed on page (Recording {rec_num})")

    def _on_cancel_recording_clicked(self, _btn):
        from audio_manager import AudioManager
        am = AudioManager.get_default()
        am.cancel_recording()
        self.rec_banner.set_visible(False)
        if hasattr(self, "insert_media_box"): self.insert_media_box.set_visible(True)
        self.show_toast("Recording cancelled")

    def _on_attach_audio_clicked(self, _btn):
        if not self.canvas.document:
            return
        dialog = Gtk.FileDialog.new()
        dialog.set_title("Attach Audio File")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        f_audio = Gtk.FileFilter.new()
        f_audio.set_name("Audio Files (*.mp3, *.wav, *.m4a, *.ogg, *.flac, *.aac)")
        for pat in ("*.mp3", "*.wav", "*.m4a", "*.ogg", "*.flac", "*.aac", "*.opus"):
            f_audio.add_pattern(pat)
        filters.append(f_audio)
        dialog.set_filters(filters)

        def _on_open_done(dlg, res):
            try:
                gfile = dlg.open_finish(res)
                if gfile:
                    file_path = gfile.get_path()
                    if file_path:
                        self._attach_audio_path(file_path)
            except Exception:
                pass

        dialog.open(self, None, _on_open_done)

    def _attach_audio_path(self, file_path):
        from audio_manager import AudioManager
        am = AudioManager.get_default()
        p_idx = self.canvas.current_page
        pages = self.canvas.document.get("pages", [])
        if 0 <= p_idx < len(pages):
            page = pages[p_idx]
            pw, ph = self.canvas.get_page_size(p_idx)
            cx = pw / 2.0
            cy = ph / 2.0
        else:
            page = self.canvas.document
            cx, cy = 300.0, 300.0

        item = am.create_audio_item_from_file(file_path, self.canvas.document, center_x=cx, center_y=cy)
        if item:
            old_items = list(page.get("items", []))
            page.setdefault("items", []).append(item)
            self.canvas._record_undo_step(p_idx, old_items, list(page["items"]), clone=False)
            self.canvas.invalidate_page(p_idx, clear_bg=False)
            self.canvas.queue_draw()
            self.show_toast(f"Attached audio: {item['name']}")

    def _flash_page_badge(self):
        if not hasattr(self, "page_badge"):
            return
        if hasattr(self, "_badge_fade_timer") and self._badge_fade_timer:
            GLib.source_remove(self._badge_fade_timer)
            self._badge_fade_timer = None
        self.page_badge.set_visible(True)
        self.page_badge.add_css_class("visible")

        def _do_fade():
            self.page_badge.remove_css_class("visible")
            self._badge_fade_timer = None
            return False

        self._badge_fade_timer = GLib.timeout_add(1600, _do_fade)

    def _fallback_open_folder(self):
        self._native_dialog = Gtk.FileChooserNative.new(
            "Open Notebook Collection Folder",
            self,
            Gtk.FileChooserAction.SELECT_FOLDER,
            "_Open",
            "_Cancel"
        )
        self._native_dialog.connect("response", self.on_native_folder_response)
        self._native_dialog.show()
