import os
import re
import html
import math
import cairo
import configparser
from dataclasses import dataclass, replace
from typing import List, Tuple, Optional, Dict
import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Gdk', '4.0')
gi.require_version('Gsk', '4.0')
gi.require_version('Graphene', '1.0')
gi.require_version('Pango', '1.0')
gi.require_version('PangoCairo', '1.0')
gi.require_version('GdkPixbuf', '2.0')
from gi.repository import Gtk, Gdk, Gsk, Graphene, GLib, Gio, Pango, PangoCairo, GdkPixbuf


def extract_markdown_headers(text: str) -> List[Dict]:
    """
    Extract headings (# Heading 1, ## Heading 2, etc.) from markdown text,
    ignoring headings inside fenced code blocks.
    Returns list of dicts:
      {"level": int (1..6), "title": str, "line": int (0-indexed), "offset": int}
    """
    if not text:
        return []
    headers = []
    in_code_block = False
    lines = text.split("\n")
    char_offset = 0
    for line_idx, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_code_block = not in_code_block
            char_offset += len(line) + 1
            continue
        if not in_code_block:
            m = re.match(r'^(#{1,6})\s+(.+)$', stripped)
            if m:
                level = len(m.group(1))
                title = re.sub(r'\s+#+\s*$', '', m.group(2)).strip()
                headers.append({
                    "level": level,
                    "title": title,
                    "line": line_idx,
                    "offset": char_offset,
                })
        char_offset += len(line) + 1
    return headers


# =============================================================================
# 1. Scribe Semantic Editor Color Schemes (ported from scribe/tema.rs)
# =============================================================================

@dataclass(Clone=False) if False else dataclass
class ScribeTheme:
    id: str
    name: str
    bg: str           # fondo: editor background (#rrggbb)
    fg: str           # primer_plano: body text
    heading: str      # encabezado: H1..H6
    accent: str       # acento: links, bullets, checkboxes, list markers
    muted: str        # atenuado: fence, html, footnote defs
    marks: str        # marcas: visible syntax delimiters (**, *, #, [url])
    code: str         # codigo: inline code (`...`)
    codeblock: str    # bloque_codigo: fenced code block text
    quote: str        # cita: blockquote text
    table: str        # tabla: table text
    border: str       # borde: horizontal rules, table grid lines, unchecked box border
    block: str        # bloque: base RGB (#rrggbb) for code/table box fill
    block_alpha: float  # bloque_alfa: opacity (0.0..1.0) for code/table box fill
    quote_bar: str    # barra_cita: vertical bar on blockquotes
    on_accent: str    # sobre_acento: checkmark stroke over accent fill
    unfocused: str    # desenfocado: dimmed text outside active paragraph in focus mode
    is_dark: bool = False

    def rgba(self, hex_color: str, alpha: float = 1.0) -> Gdk.RGBA:
        c = Gdk.RGBA()
        s = hex_color.strip().lstrip("#")
        if len(s) == 6:
            try:
                r = int(s[0:2], 16) / 255.0
                g = int(s[2:4], 16) / 255.0
                b = int(s[4:6], 16) / 255.0
                c.red = r
                c.green = g
                c.blue = b
                c.alpha = max(0.0, min(1.0, float(alpha)))
                return c
            except ValueError:
                pass
        c.parse(hex_color)
        c.alpha = max(0.0, min(1.0, float(alpha)))
        return c

    def rgb_tuple(self, hex_color: str) -> Tuple[float, float, float]:
        s = hex_color.strip().lstrip("#")
        if len(s) == 6:
            try:
                return (
                    int(s[0:2], 16) / 255.0,
                    int(s[2:4], 16) / 255.0,
                    int(s[4:6], 16) / 255.0,
                )
            except ValueError:
                pass
        return (0.12, 0.12, 0.12)


SCRIBE_BUILTIN_THEMES: Dict[str, ScribeTheme] = {
    "light": ScribeTheme(
        id="light",
        name="Light",
        bg="#ffffff",
        fg="#1f1f1f",
        heading="#1f1f1f",
        accent="#1a6ed8",
        muted="#8b8a88",
        marks="#b5b4b1",
        code="#a34a00",
        codeblock="#1f1f1f",
        quote="#54535a",
        table="#1f1f1f",
        border="#c0bfbc",
        block="#000000",
        block_alpha=0.05,
        quote_bar="#c0bfbc",
        on_accent="#ffffff",
        unfocused="#bdbcba",
        is_dark=False,
    ),
    "dark": ScribeTheme(
        id="dark",
        name="Dark",
        bg="#1e1e1e",
        fg="#e4e4e4",
        heading="#e4e4e4",
        accent="#82b8f0",
        muted="#8f8f8f",
        marks="#787878",
        code="#f0a868",
        codeblock="#e4e4e4",
        quote="#c2c2c2",
        table="#e0e0e0",
        border="#6f6f6f",
        block="#ffffff",
        block_alpha=0.07,
        quote_bar="#6f6f6f",
        on_accent="#1b1b1b",
        unfocused="#5c5c5c",
        is_dark=True,
    ),
    "sepia": ScribeTheme(
        id="sepia",
        name="Sepia",
        bg="#f5efe0",
        fg="#43392a",
        heading="#3a2f21",
        accent="#8a5a2b",
        muted="#9c8d74",
        marks="#b3a488",
        code="#8a4a1f",
        codeblock="#43392a",
        quote="#6b5b44",
        table="#43392a",
        border="#c9bb9d",
        block="#43392a",
        block_alpha=0.06,
        quote_bar="#c9bb9d",
        on_accent="#fdf8ec",
        unfocused="#bdae8f",
        is_dark=False,
    ),
    "solarized-dark": ScribeTheme(
        id="solarized-dark",
        name="Solarized Dark",
        bg="#002b36",
        fg="#839496",
        heading="#93a1a1",
        accent="#268bd2",
        muted="#586e75",
        marks="#586e75",
        code="#cb4b16",
        codeblock="#93a1a1",
        quote="#93a1a1",
        table="#93a1a1",
        border="#586e75",
        block="#ffffff",
        block_alpha=0.06,
        quote_bar="#586e75",
        on_accent="#002b36",
        unfocused="#586e75",
        is_dark=True,
    ),
    "gruvbox-dark": ScribeTheme(
        id="gruvbox-dark",
        name="Gruvbox Dark",
        bg="#282828",
        fg="#ebdbb2",
        heading="#fbf1c7",
        accent="#83a598",
        muted="#928374",
        marks="#928374",
        code="#fe8019",
        codeblock="#ebdbb2",
        quote="#d5c4a1",
        table="#ebdbb2",
        border="#928374",
        block="#ffffff",
        block_alpha=0.07,
        quote_bar="#928374",
        on_accent="#282828",
        unfocused="#665c54",
        is_dark=True,
    ),
}


def _valid_hex(val: str) -> Optional[str]:
    v = val.strip()
    if len(v) == 7 and v.startswith("#") and all(c in "0123456789abcdefABCDEF" for c in v[1:]):
        return v.lower()
    return None


def load_user_scribe_theme(path: str, base: ScribeTheme) -> Optional[ScribeTheme]:
    if not os.path.isfile(path):
        return None
    cp = configparser.ConfigParser(interpolation=None)
    try:
        cp.read(path, encoding="utf-8")
    except Exception:
        return None
    if not cp.has_section("theme"):
        return None
    sec = cp["theme"]
    stem = os.path.splitext(os.path.basename(path))[0]
    if not stem:
        return None

    def color(key: str, default: str) -> str:
        raw = sec.get(key, "")
        return _valid_hex(raw) or default

    try:
        alpha = float(sec.get("block-alpha", str(base.block_alpha)))
        if not (0.0 <= alpha <= 1.0):
            alpha = base.block_alpha
    except ValueError:
        alpha = base.block_alpha

    bg = color("bg", base.bg)
    rgb = base.rgb_tuple(bg)
    lum = 0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]

    return ScribeTheme(
        id=stem,
        name=sec.get("name", stem).strip() or stem,
        bg=bg,
        fg=color("fg", base.fg),
        heading=color("heading", base.heading),
        accent=color("accent", base.accent),
        muted=color("muted", base.muted),
        marks=color("marks", base.marks),
        code=color("code", base.code),
        codeblock=color("codeblock", base.codeblock),
        quote=color("quote", base.quote),
        table=color("table", base.table),
        border=color("border", base.border),
        block=color("block", base.block),
        block_alpha=alpha,
        quote_bar=color("quote-bar", base.quote_bar),
        on_accent=color("on-accent", base.on_accent),
        unfocused=color("unfocused", base.unfocused),
        is_dark=(lum < 0.5),
    )


def list_all_scribe_themes(is_dark: bool = False) -> List[ScribeTheme]:
    themes = list(SCRIBE_BUILTIN_THEMES.values())
    base = SCRIBE_BUILTIN_THEMES["dark" if is_dark else "light"]
    builtin_ids = set(SCRIBE_BUILTIN_THEMES.keys()) | {"auto"}
    data_dir = GLib.get_user_data_dir()
    for subdir in ("scribe/themes", "xnotes/themes"):
        folder = os.path.join(data_dir, subdir)
        if not os.path.isdir(folder):
            continue
        try:
            entries = sorted(os.listdir(folder))
        except OSError:
            continue
        for fname in entries:
            if not fname.endswith(".ini"):
                continue
            stem = fname[:-4]
            if stem in builtin_ids:
                continue
            loaded = load_user_scribe_theme(os.path.join(folder, fname), base)
            if loaded:
                themes.append(loaded)
                builtin_ids.add(stem)
    return themes


def resolve_scribe_theme(theme_id: str, is_dark: bool = False) -> ScribeTheme:
    base = SCRIBE_BUILTIN_THEMES["dark" if is_dark else "light"]
    if not theme_id or theme_id == "auto":
        return base
    if theme_id in SCRIBE_BUILTIN_THEMES:
        return SCRIBE_BUILTIN_THEMES[theme_id]
    data_dir = GLib.get_user_data_dir()
    for subdir in ("scribe/themes", "xnotes/themes"):
        candidate = os.path.join(data_dir, subdir, f"{theme_id}.ini")
        loaded = load_user_scribe_theme(candidate, base)
        if loaded:
            return loaded
    return base


# =============================================================================
# 2. Scribe Live Markdown Analysis & Ornament Constants
#    (ported from scribe/markdown_render.rs & scribe/markdown_view.rs)
# =============================================================================

BULLET_OFFSET = 15.0
BULLET_RADIUS = 3.0
CHECKBOX_OFFSET = 25.0
CHECKBOX_SIZE = 18.0
CHECKBOX_RADIUS = 4.0
CHECKBOX_TOP_CROP = 1.5
RULE_THICKNESS = 2.0
TABLE_GRID_THICKNESS = 1.0
QUOTE_BAR_WIDTH = 3.0
QUOTE_BAR_OFFSET = 20.0
BLOCK_RADIUS = 8.0
BLOCK_PADDING = 8.0

BLOCK_INDENTS = [
    ("md-quote", 32, 16),
    ("md-list1", 28, 0),
    ("md-list2", 52, 0),
    ("md-list3", 76, 0),
    ("md-code-block", 24, 24),
    ("md-fence", 24, 24),
]


@dataclass
class MarkdownOrnament:
    kind: str  # 'bullet', 'checkbox', 'rule', 'quote', 'codeblock', 'table', 'tablerule', 'headingrule'
    line: int = 0
    first: int = 0
    last: int = 0
    depth: int = 1
    checked: bool = False


@dataclass
class MarkdownLinkRef:
    start: int
    end: int
    dest: str


class ScribeMarkdownView(Gtk.TextView):
    """
    GTK4 TextView subclass that paints Scribe's live Markdown ornaments
    in BELOW_TEXT (code boxes, table boxes, quote bars) and ABOVE_TEXT
    (multi-depth bullets, interactive checkboxes, horizontal rules, table rules).
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.ornaments: List[MarkdownOrnament] = []
        self.links: List[MarkdownLinkRef] = []
        self.line_count: int = 0
        self.theme: ScribeTheme = SCRIBE_BUILTIN_THEMES["light"]
        self.markup_visibility: str = "dim"  # Always dim

    def set_ornaments(self, ornaments: List[MarkdownOrnament], links: List[MarkdownLinkRef], line_count: int):
        self.ornaments = ornaments
        self.links = links
        self.line_count = line_count
        self.queue_draw()

    def set_theme(self, theme: ScribeTheme):
        self.theme = theme
        self.queue_draw()

    def _is_stale(self) -> bool:
        buf = self.get_buffer()
        if not buf:
            return True
        return self.line_count != (buf.get_end_iter().get_line() + 1)

    def _cursor_line(self) -> int:
        buf = self.get_buffer()
        if not buf:
            return -1
        return buf.get_iter_at_mark(buf.get_insert()).get_line()

    def _visible_lines(self) -> Tuple[int, int]:
        vis = self.get_visible_rect()
        top_iter, _ = self.get_line_at_y(vis.y)
        bot_iter, _ = self.get_line_at_y(vis.y + vis.height)
        return (top_iter.get_line(), bot_iter.get_line())

    def _line_origin(self, line: int) -> Optional[Tuple[float, float, float]]:
        buf = self.get_buffer()
        ok_res = buf.get_iter_at_line(line)
        if isinstance(ok_res, tuple):
            ok, it = ok_res
            if not ok:
                return None
        else:
            it = ok_res
        rect = self.get_iter_location(it)
        wx, wy = self.buffer_to_window_coords(Gtk.TextWindowType.WIDGET, rect.x, rect.y)
        return (float(wx), float(wy), float(rect.height))

    def _line_extent(self, line: int) -> Optional[Tuple[float, float]]:
        buf = self.get_buffer()
        ok_res = buf.get_iter_at_line(line)
        if isinstance(ok_res, tuple):
            ok, start_it = ok_res
            if not ok:
                return None
        else:
            start_it = ok_res
        end_it = start_it.copy()
        if not end_it.ends_line():
            end_it.forward_to_line_end()
        top_r = self.get_iter_location(start_it)
        bot_r = self.get_iter_location(end_it)
        _, wy = self.buffer_to_window_coords(Gtk.TextWindowType.WIDGET, 0, top_r.y)
        height = (bot_r.y + bot_r.height) - top_r.y
        return (float(wy), float(max(height, top_r.height)))

    def _column(self) -> Tuple[float, float]:
        left = float(self.get_left_margin())
        right = float(max(left + 60.0, self.get_width() - self.get_right_margin()))
        return (left, right)

    def checkbox_at_position(self, x: float, y: float) -> Optional[int]:
        if self._is_stale():
            return None
        margin = 6.0
        for orn in self.ornaments:
            if orn.kind != "checkbox":
                continue
            orig = self._line_origin(orn.line)
            if not orig:
                continue
            ox, oy, h = orig
            cx = ox - CHECKBOX_OFFSET
            cy = oy + h / 2.0 - CHECKBOX_SIZE / 2.0
            if (cx - margin) <= x <= (cx + CHECKBOX_SIZE + margin) and (cy - margin) <= y <= (cy + CHECKBOX_SIZE + margin):
                return orn.line
        return None

    def link_at_position(self, x: float, y: float) -> Optional[str]:
        bx, by = self.window_to_buffer_coords(Gtk.TextWindowType.WIDGET, int(x), int(y))
        res = self.get_iter_at_position(bx, by)
        if isinstance(res, tuple):
            if len(res) == 3:
                ok, it, _ = res
            else:
                ok, it = True, res[0]
            if not ok:
                return None
        else:
            it = res
        offset = it.get_offset()
        for lnk in self.links:
            if lnk.start <= offset <= lnk.end:
                return lnk.dest
        return None

    def do_snapshot_layer(self, layer: Gtk.TextViewLayer, snapshot: Gtk.Snapshot):
        try:
            if layer == Gtk.TextViewLayer.BELOW_TEXT:
                self._draw_blocks(snapshot)
            elif layer == Gtk.TextViewLayer.ABOVE_TEXT:
                self._draw_glyphs(snapshot)
        except Exception:
            pass

    def _draw_blocks(self, snapshot: Gtk.Snapshot):
        if not self.ornaments or self._is_stale():
            return
        first_vis, last_vis = self._visible_lines()
        left, right = self._column()
        t = self.theme
        block_rgba = t.rgba(t.block, t.block_alpha)
        table_rgba = t.rgba(t.block, t.block_alpha * 0.75)
        quote_rgba = t.rgba(t.quote_bar, 1.0)
        border_rgba = t.rgba(t.border, 0.55)

        for orn in self.ornaments:
            if orn.kind not in ("codeblock", "table", "quote"):
                continue
            if orn.last < first_vis or orn.first > last_vis:
                continue
            anchor_top = max(0, max(orn.first, first_vis - 1))
            anchor_bot = min(orn.last, last_vis + 1)
            top_ext = self._line_extent(anchor_top)
            bot_ext = self._line_extent(anchor_bot)
            if not top_ext or not bot_ext:
                continue
            top_y = top_ext[0] - 2.0
            bot_y = bot_ext[0] + bot_ext[1] + 2.0
            box_h = max(0.0, bot_y - top_y)
            if box_h <= 0.0:
                continue

            if orn.kind in ("codeblock", "table"):
                fill = table_rgba if orn.kind == "table" else block_rgba
                bx = left - BLOCK_PADDING
                bw = max(20.0, (right - left) + BLOCK_PADDING * 2.0)
                rect = Graphene.Rect().init(bx, top_y, bw, box_h)
                rounded = Gsk.RoundedRect()
                rounded.init_from_rect(rect, BLOCK_RADIUS)
                snapshot.push_rounded_clip(rounded)
                snapshot.append_color(fill, rect)
                snapshot.pop()
                if orn.kind == "table":
                    snapshot.append_border(
                        rounded,
                        [1.0, 1.0, 1.0, 1.0],
                        [border_rgba, border_rgba, border_rgba, border_rgba],
                    )
            elif orn.kind == "quote":
                orig = self._line_origin(anchor_top)
                qx = (orig[0] - QUOTE_BAR_OFFSET) if orig else (left + BLOCK_PADDING)
                rect = Graphene.Rect().init(qx, top_y, QUOTE_BAR_WIDTH, box_h)
                rounded = Gsk.RoundedRect()
                rounded.init_from_rect(rect, QUOTE_BAR_WIDTH / 2.0)
                snapshot.push_rounded_clip(rounded)
                snapshot.append_color(quote_rgba, rect)
                snapshot.pop()

    def _draw_glyphs(self, snapshot: Gtk.Snapshot):
        if not self.ornaments or self._is_stale():
            return
        if self.markup_visibility == "dim":
            # In 'dim' mode, syntax tokens remain visible so replacement glyphs are skipped
            return
        first_vis, last_vis = self._visible_lines()
        left, right = self._column()
        cursor_line = self._cursor_line()
        t = self.theme
        accent_rgba = t.rgba(t.accent, 1.0)
        muted_rgba = t.rgba(t.border, 1.0)
        on_accent_rgba = t.rgba(t.on_accent, 1.0)

        for orn in self.ornaments:
            if orn.kind not in ("bullet", "checkbox", "rule", "tablerule", "headingrule"):
                continue
            if orn.line < first_vis or orn.line > last_vis or orn.line == cursor_line:
                continue
            orig = self._line_origin(orn.line)
            if not orig:
                continue
            ox, oy, h = orig
            middle = oy + h / 2.0

            if orn.kind == "bullet":
                bx = ox - BULLET_OFFSET
                by = middle
                if orn.depth <= 1:
                    rect = Graphene.Rect().init(bx - BULLET_RADIUS, by - BULLET_RADIUS, BULLET_RADIUS * 2.0, BULLET_RADIUS * 2.0)
                    rounded = Gsk.RoundedRect()
                    rounded.init_from_rect(rect, BULLET_RADIUS)
                    snapshot.push_rounded_clip(rounded)
                    snapshot.append_color(accent_rgba, rect)
                    snapshot.pop()
                elif orn.depth == 2:
                    r = BULLET_RADIUS
                    rect = Graphene.Rect().init(bx - r, by - r, r * 2.0, r * 2.0)
                    rounded = Gsk.RoundedRect()
                    rounded.init_from_rect(rect, r)
                    snapshot.append_border(
                        rounded,
                        [1.4, 1.4, 1.4, 1.4],
                        [accent_rgba, accent_rgba, accent_rgba, accent_rgba],
                    )
                else:
                    side = BULLET_RADIUS * 1.7
                    rect = Graphene.Rect().init(bx - side / 2.0, by - side / 2.0, side, side)
                    rounded = Gsk.RoundedRect()
                    rounded.init_from_rect(rect, 1.0)
                    snapshot.push_rounded_clip(rounded)
                    snapshot.append_color(accent_rgba, rect)
                    snapshot.pop()

            elif orn.kind == "checkbox":
                cx = ox - CHECKBOX_OFFSET
                cy = middle - CHECKBOX_SIZE / 2.0 + CHECKBOX_TOP_CROP
                ch = CHECKBOX_SIZE - CHECKBOX_TOP_CROP
                rect = Graphene.Rect().init(cx, cy, CHECKBOX_SIZE, ch)
                rounded = Gsk.RoundedRect()
                rounded.init_from_rect(rect, CHECKBOX_RADIUS)
                if orn.checked:
                    snapshot.push_rounded_clip(rounded)
                    snapshot.append_color(accent_rgba, rect)
                    snapshot.pop()
                    if hasattr(Gsk, "PathBuilder"):
                        pb = Gsk.PathBuilder.new()
                        pb.move_to(cx + CHECKBOX_SIZE * 0.25, cy + ch * 0.52)
                        pb.line_to(cx + CHECKBOX_SIZE * 0.44, cy + ch * 0.72)
                        pb.line_to(cx + CHECKBOX_SIZE * 0.76, cy + ch * 0.30)
                        stroke = Gsk.Stroke.new(2.2)
                        stroke.set_line_cap(Gsk.LineCap.ROUND)
                        stroke.set_line_join(Gsk.LineJoin.ROUND)
                        snapshot.append_stroke(pb.to_path(), stroke, on_accent_rgba)
                else:
                    snapshot.append_border(
                        rounded,
                        [1.8, 1.8, 1.8, 1.8],
                        [muted_rgba, muted_rgba, muted_rgba, muted_rgba],
                    )

            elif orn.kind == "rule":
                rect = Graphene.Rect().init(
                    left,
                    round(middle - RULE_THICKNESS / 2.0),
                    max(0.0, right - left),
                    RULE_THICKNESS,
                )
                snapshot.append_color(muted_rgba, rect)

            elif orn.kind == "tablerule":
                rect = Graphene.Rect().init(
                    left,
                    round(middle - 0.75),
                    max(0.0, right - left),
                    1.5,
                )
                snapshot.append_color(muted_rgba, rect)


# =============================================================================
# 3. MarkdownInlineStyler (Live WYSIWYG Styler, Themes, Markup Visibility, Focus Mode)
# =============================================================================

class MarkdownInlineStyler:
    """
    Live syntax highlighter, ornament analyzer, and semantic theme controller
    for ScribeMarkdownView / Gtk.TextView, faithfully implementing Scribe's
    live Markdown engine (scribe/editor.rs, scribe/markdown_render.rs, scribe/tema.rs).
    """

    def __init__(self, text_view: Gtk.TextView):
        self.text_view = text_view
        self.buffer = text_view.get_buffer()
        self.inline_render = True
        self.markup_visibility = "dim"  # Always dim
        self.focus_mode = False
        self._typewriter_mode = False
        self._vadj_connected = False
        self.continue_lists = True
        self.font_family = "sans"         # 'sans', 'serif', 'mono'
        self.font_size = 16               # px
        self.line_spacing = 1.4
        self.column_width = 720           # px
        self.theme_id = "auto"
        self.is_dark = False
        self.zoom_level = 1.0
        self._updating = False
        self._last_cursor_line = -1
        self._last_cursor_offset = -1
        self._css_provider = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            self._css_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 2,
        )

        self.on_document_link_clicked: Optional[callable] = None
        self._setup_tags()
        self._setup_controllers()

        try:
            gi.require_version("Adw", "1")
            from gi.repository import Adw
            self.is_dark = Adw.StyleManager.get_default().get_dark()
        except Exception:
            self.is_dark = False

        self.apply_theme()
        self.buffer.connect("changed", self._on_buffer_changed)
        self.buffer.connect("mark-set", self._on_mark_set)

    @property
    def current_theme(self) -> ScribeTheme:
        return resolve_scribe_theme(self.theme_id, self.is_dark)

    def _setup_controllers(self):
        # Interactive checkbox toggle and Ctrl+click link opening
        click = Gtk.GestureClick.new()
        click.set_button(Gdk.BUTTON_PRIMARY)
        click.connect("pressed", self._on_view_clicked)
        self.text_view.add_controller(click)

        motion = Gtk.EventControllerMotion.new()
        motion.connect("motion", self._on_view_motion)
        self.text_view.add_controller(motion)

    def _on_view_motion(self, _ctrl, x: float, y: float):
        if isinstance(self.text_view, ScribeMarkdownView):
            if self.text_view.checkbox_at_position(x, y) is not None:
                self.text_view.set_cursor_from_name("pointer")
                return
            if self.text_view.link_at_position(x, y) is not None:
                self.text_view.set_cursor_from_name("pointer")
                return
        self.text_view.set_cursor_from_name("text")

    def _on_view_clicked(self, gesture, n_press: int, x: float, y: float):
        if n_press != 1 or not isinstance(self.text_view, ScribeMarkdownView):
            return
        # 1. Interactive Checkbox toggle
        cb_line = self.text_view.checkbox_at_position(x, y)
        if cb_line is not None and self.text_view.get_editable():
            if self._toggle_checkbox_at_line(cb_line):
                gesture.set_state(Gtk.EventSequenceState.CLAIMED)
                return

        # 2. Ctrl+Click (or Read-only Click) on Markdown link
        state = gesture.get_current_event_state()
        ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)
        if ctrl or not self.text_view.get_editable():
            dest = self.text_view.link_at_position(x, y)
            if dest:
                if dest.startswith(("http://", "https://", "mailto:")):
                    try:
                        Gio.AppInfo.launch_default_for_uri(dest, None)
                        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
                    except Exception:
                        pass
                else:
                    if hasattr(self, "on_document_link_clicked") and callable(self.on_document_link_clicked):
                        self.on_document_link_clicked(dest)
                        gesture.set_state(Gtk.EventSequenceState.CLAIMED)

    def _toggle_checkbox_at_line(self, line: int) -> bool:
        ok_res = self.buffer.get_iter_at_line(line)
        if isinstance(ok_res, tuple):
            ok, line_start = ok_res
            if not ok:
                return False
        else:
            line_start = ok_res
        line_end = line_start.copy()
        if not line_end.ends_line():
            line_end.forward_to_line_end()
        line_text = self.buffer.get_text(line_start, line_end, True)
        m = re.match(r'^(\s*(?:[-*+]|\d+[.)])\s+\[)([ xX])(\])', line_text)
        if not m:
            return False
        state_char_offset = line_start.get_offset() + len(m.group(1))
        new_char = " " if m.group(2).lower() == "x" else "x"
        s_it = self.buffer.get_iter_at_offset(state_char_offset)
        e_it = self.buffer.get_iter_at_offset(state_char_offset + 1)
        self.buffer.begin_user_action()
        self.buffer.delete(s_it, e_it)
        ins_it = self.buffer.get_iter_at_offset(state_char_offset)
        self.buffer.insert(ins_it, new_char, 1)
        self.buffer.end_user_action()
        return True

    def _setup_tags(self):
        table = self.buffer.get_tag_table()

        def ensure_tag(name, **kwargs):
            tag = table.lookup(name)
            if not tag:
                tag = self.buffer.create_tag(name, **kwargs)
            return tag

        # Delimiter / syntax token tags (Scribe's syn_shown, syn_shrink, unfocused)
        ensure_tag("md-syntax")
        transparent = Gdk.RGBA()
        transparent.red = 0.0
        transparent.green = 0.0
        transparent.blue = 0.0
        transparent.alpha = 0.0
        # Scribe's safe shrink tag (scale=0.0, alpha=0.0) instead of invisible=True
        ensure_tag("md-conceal", scale=0.0, foreground_rgba=transparent)

        # Headings (Scribe scales & weights: H1 1.9/800, H2 1.5/750, H3 1.24/700, H4 1.1/700, H5 1.0/700, H6 1.0/700)
        ensure_tag("md-h1", weight=800, scale=1.9, pixels_above_lines=24, pixels_below_lines=10)
        ensure_tag("md-h2", weight=750, scale=1.5, pixels_above_lines=20, pixels_below_lines=8)
        ensure_tag("md-h3", weight=700, scale=1.24, pixels_above_lines=16, pixels_below_lines=6)
        ensure_tag("md-h4", weight=700, scale=1.1, pixels_above_lines=12, pixels_below_lines=4)
        ensure_tag("md-h5", weight=700, scale=1.0, pixels_above_lines=10, pixels_below_lines=4)
        ensure_tag("md-h6", weight=700, scale=1.0, pixels_above_lines=10, pixels_below_lines=4)

        # Block tags
        ensure_tag("md-quote", style=Pango.Style.ITALIC, pixels_above_lines=3, pixels_below_lines=3)
        ensure_tag("md-list1", pixels_above_lines=2, pixels_below_lines=2)
        ensure_tag("md-list2", pixels_above_lines=2, pixels_below_lines=2)
        ensure_tag("md-list3", pixels_above_lines=2, pixels_below_lines=2)
        ensure_tag("md-code-block", family="Monospace", scale=0.92, pixels_above_lines=2, pixels_below_lines=2)
        ensure_tag("md-fence", family="Monospace", scale=0.85)
        ensure_tag("md-table", family="Monospace", pixels_above_lines=2, pixels_below_lines=2)
        ensure_tag("md-tablehead", family="Monospace", weight=700)
        ensure_tag("md-tablerule", family="Monospace", scale=0.0, foreground_rgba=transparent)

        # Inline formatting
        ensure_tag("md-bold", weight=700)
        ensure_tag("md-italic", style=Pango.Style.ITALIC)
        ensure_tag("md-bold-italic", weight=700, style=Pango.Style.ITALIC)
        ensure_tag("md-strike", strikethrough=True)
        ensure_tag("md-inline-code", family="Monospace", scale=0.9)
        ensure_tag("md-code-keyword", weight=700)
        ensure_tag("md-code-string")
        ensure_tag("md-code-comment", style=Pango.Style.ITALIC)

        # Lists, tasks, links, footnotes
        ensure_tag("md-list-marker", weight=700)
        ensure_tag("md-task-done", weight=700)
        ensure_tag("md-hr", weight=700)
        ensure_tag("md-link-label", underline=Pango.Underline.SINGLE)
        ensure_tag("md-link-url", scale=0.88)
        ensure_tag("md-footnote", scale=0.8, rise=4000)
        ensure_tag("md-img-mark", weight=700)
        ensure_tag("md-img-alt", style=Pango.Style.ITALIC)
        ensure_tag("md-img-url", scale=0.88)

        # Focus mode tag (placed after style tags so it overrides their foreground color)
        ensure_tag("md-unfocused")

        # Search in page highlights
        ensure_tag("md-search-match", background="#f9f06b", foreground="#1e1e1e")
        ensure_tag("md-search-current", background="#ff7800", foreground="#ffffff", weight=Pango.Weight.BOLD)

        self.managed_tags = [
            "md-syntax", "md-conceal",
            "md-h1", "md-h2", "md-h3", "md-h4", "md-h5", "md-h6",
            "md-quote", "md-list1", "md-list2", "md-list3",
            "md-code-block", "md-fence", "md-table", "md-tablehead", "md-tablerule",
            "md-bold", "md-italic", "md-bold-italic", "md-strike",
            "md-inline-code",
            "md-code-keyword", "md-code-string", "md-code-comment",
            "md-list-marker", "md-task-done", "md-hr",
            "md-link-label", "md-link-url", "md-footnote", "md-img-mark", "md-img-alt", "md-img-url",
            "md-unfocused",
        ]

    def update_theme_colors(self, is_dark: bool):
        self.is_dark = bool(is_dark)
        self.apply_theme()

    def set_scribe_theme(self, theme_id: str):
        self.theme_id = theme_id or "auto"
        self.apply_theme()

    def set_markup_visibility(self, mode: str):
        if mode in ("hidden", "focus", "dim"):
            self.markup_visibility = mode
            self.inline_render = (mode != "dim")
            if isinstance(self.text_view, ScribeMarkdownView):
                self.text_view.markup_visibility = mode
            self.refresh()

    def set_focus_mode(self, enabled: bool):
        self.focus_mode = bool(enabled)
        self.refresh()

    @property
    def typewriter_mode(self) -> bool:
        return getattr(self, "_typewriter_mode", False)

    @typewriter_mode.setter
    def typewriter_mode(self, enabled: bool):
        self.set_typewriter_mode(enabled)

    def _hook_vadj_listener(self):
        parent = self.text_view.get_parent()
        if isinstance(parent, Gtk.ScrolledWindow):
            vadj = parent.get_vadjustment()
            if vadj and not getattr(self, "_vadj_connected", False):
                vadj.connect("notify::page-size", self._on_vadj_page_size_changed)
                self._vadj_connected = True

    def _on_vadj_page_size_changed(self, vadj, _pspec):
        if getattr(self, "_typewriter_mode", False):
            page_size = vadj.get_page_size()
            if page_size > 0:
                half_page = int(page_size / 2)
                if self.text_view.get_top_margin() != half_page or self.text_view.get_bottom_margin() != half_page:
                    self.text_view.set_top_margin(half_page)
                    self.text_view.set_bottom_margin(half_page)
                GLib.idle_add(self._center_cursor_line)

    def set_typewriter_mode(self, enabled: bool):
        self._typewriter_mode = bool(enabled)
        self._hook_vadj_listener()
        parent = self.text_view.get_parent()
        if isinstance(parent, Gtk.ScrolledWindow):
            vadj = parent.get_vadjustment()
            if vadj:
                page_size = vadj.get_page_size()
                if self._typewriter_mode:
                    if page_size > 0:
                        half_page = int(page_size / 2)
                        self.text_view.set_top_margin(half_page)
                        self.text_view.set_bottom_margin(half_page)
                else:
                    self.text_view.set_top_margin(36)
                    self.text_view.set_bottom_margin(36)
        elif not self._typewriter_mode:
            self.text_view.set_top_margin(36)
            self.text_view.set_bottom_margin(36)

        if self._typewriter_mode:
            GLib.idle_add(self._center_cursor_line)

    def apply_theme(self):
        t = self.current_theme
        table = self.buffer.get_tag_table()

        if isinstance(self.text_view, ScribeMarkdownView):
            self.text_view.set_theme(t)
            self.text_view.markup_visibility = self.markup_visibility

        # Update CSS for the editor and preview paper to match Scribe's semantic theme
        font_css_map = {
            "sans": '"Cantarell", "Inter", system-ui, sans-serif',
            "serif": '"Merriweather", "Georgia", "Liberation Serif", serif',
            "mono": '"JetBrains Mono", "Source Code Pro", monospace',
        }
        font_family_css = font_css_map.get(self.font_family, font_css_map["sans"])
        px_size = max(10, int(self.font_size * self.zoom_level))
        above_below = max(2, int((self.line_spacing - 1.0) * px_size * 0.5))
        self.text_view.set_pixels_above_lines(above_below)
        self.text_view.set_pixels_below_lines(above_below)
        self.text_view.set_Pixels_inside_wrap(above_below) if hasattr(self.text_view, "set_Pixels_inside_wrap") else self.text_view.set_pixels_inside_wrap(above_below)

        css = f"""
        textview.md-editor-view,
        textview.md-editor-view text {{
            background-color: {t.bg};
            color: {t.fg};
            font-family: {font_family_css};
            font-size: {px_size}px;
            caret-color: {t.accent};
        }}
        .md-split-preview-box,
        .md-paper {{
            background-color: {t.bg};
            color: {t.fg};
        }}
        """
        try:
            self._css_provider.load_from_string(css)
        except Exception:
            try:
                self._css_provider.load_from_data(css.encode("utf-8"))
            except Exception:
                pass

        # Update block indents relative to base margin
        base_left = self.text_view.get_left_margin()
        base_right = self.text_view.get_right_margin()
        for tag_name, l_off, r_off in BLOCK_INDENTS:
            tag = table.lookup(tag_name)
            if tag:
                tag.set_property("left-margin", base_left + l_off)
                tag.set_property("right-margin", base_right + r_off)

        search_bg = "#735c0f" if t.is_dark else "#f9f06b"
        search_fg = "#f0f6fc" if t.is_dark else "#1e1e1e"

        color_map = [
            ("md-syntax", "foreground", t.marks),
            ("md-h1", "foreground", t.heading),
            ("md-h2", "foreground", t.heading),
            ("md-h3", "foreground", t.heading),
            ("md-h4", "foreground", t.heading),
            ("md-h5", "foreground", t.heading),
            ("md-h6", "foreground", t.heading),
            ("md-bold", "foreground", t.fg),
            ("md-italic", "foreground", t.fg),
            ("md-bold-italic", "foreground", t.fg),
            ("md-strike", "foreground", t.muted),
            ("md-inline-code", "foreground", t.code),
            ("md-code-block", "foreground", t.codeblock),
            ("md-fence", "foreground", t.muted),
            ("md-table", "foreground", t.table),
            ("md-tablehead", "foreground", t.table),
            ("md-code-keyword", "foreground", t.accent),
            ("md-code-string", "foreground", t.code),
            ("md-code-comment", "foreground", t.muted),
            ("md-quote", "foreground", t.quote),
            ("md-list-marker", "foreground", t.accent),
            ("md-task-done", "foreground", t.accent),
            ("md-hr", "foreground", t.border),
            ("md-link-label", "foreground", t.accent),
            ("md-link-url", "foreground", t.marks),
            ("md-img-mark", "foreground", t.marks),
            ("md-img-alt", "foreground", t.accent),
            ("md-img-url", "foreground", t.marks),
            ("md-footnote", "foreground", t.accent),
            ("md-unfocused", "foreground", t.unfocused),
            ("md-search-match", "background", search_bg),
            ("md-search-match", "foreground", search_fg),
        ]
        for name, prop, val in color_map:
            tag = table.lookup(name)
            if tag:
                tag.set_property(prop, val)

        if hasattr(self, "refresh") and hasattr(self, "managed_tags"):
            self.refresh()

    def _get_cursor_line_and_offset(self) -> Tuple[int, int]:
        insert_mark = self.buffer.get_insert()
        it = self.buffer.get_iter_at_mark(insert_mark)
        return (it.get_line(), it.get_offset())

    def _center_cursor_line(self):
        if not getattr(self, "_typewriter_mode", False):
            return
        self._hook_vadj_listener()
        parent = self.text_view.get_parent()
        if not isinstance(parent, Gtk.ScrolledWindow):
            return
        vadj = parent.get_vadjustment()
        if not vadj:
            return
        page_size = vadj.get_page_size()
        if page_size <= 0:
            return
        half_page = int(page_size / 2)
        if self.text_view.get_top_margin() != half_page or self.text_view.get_bottom_margin() != half_page:
            self.text_view.set_top_margin(half_page)
            self.text_view.set_bottom_margin(half_page)

        insert_mark = self.buffer.get_insert()
        it = self.buffer.get_iter_at_mark(insert_mark)
        rect = self.text_view.get_iter_location(it)
        target = max(0.0, min(vadj.get_upper() - page_size, rect.y + rect.height / 2.0))
        vadj.set_value(target)

    def _on_buffer_changed(self, _buffer):
        if not self._updating:
            self.refresh()
            if self.typewriter_mode:
                GLib.idle_add(self._center_cursor_line)

    def _on_mark_set(self, _buffer, location, mark):
        if self._updating or mark != self.buffer.get_insert():
            return
        new_line = location.get_line()
        new_offset = location.get_offset()
        if new_line != self._last_cursor_line or new_offset != self._last_cursor_offset:
            self._last_cursor_line = new_line
            self._last_cursor_offset = new_offset
            self.refresh()
            if self.typewriter_mode:
                GLib.idle_add(self._center_cursor_line)

    def _apply_tag(self, name: str, start_char: int, end_char: int):
        if start_char >= end_char:
            return
        s_iter = self.buffer.get_iter_at_offset(start_char)
        e_iter = self.buffer.get_iter_at_offset(end_char)
        self.buffer.apply_tag_by_name(name, s_iter, e_iter)

    def _style_marker(self, start_char: int, end_char: int, elem_start: int, elem_end: int, cursor_offset: int, is_active_line: bool):
        """
        Applies Scribe's element-aware marker visibility:
        - 'dim': always shows markers in `marks` color (`md-syntax`).
        - 'focus': shows markers when cursor is inside/touching element OR on active line; shrinks otherwise.
        - 'hidden': shows markers only when cursor is inside/touching the element (`elem_start <= cursor <= elem_end`); shrinks otherwise.
        """
        self._apply_tag("md-syntax", start_char, end_char)
        if not self.inline_render or self.markup_visibility == "dim":
            return
        in_element = (elem_start <= cursor_offset <= elem_end)
        if self.markup_visibility == "focus":
            reveal = in_element or is_active_line
        else:  # 'hidden'
            reveal = in_element
        if not reveal:
            self._apply_tag("md-conceal", start_char, end_char)

    def _style_replaced(self, start_char: int, end_char: int, is_active_line: bool):
        """
        Applies Scribe's replaced-token visibility (e.g. `# `, `- `, `- [ ] `, `> `, `---`):
        - Shown on the cursor line so the user can edit it; shrunk on other lines so the drawn ornament replaces it.
        """
        self._apply_tag("md-syntax", start_char, end_char)
        if not self.inline_render or self.markup_visibility == "dim":
            return
        if not is_active_line:
            self._apply_tag("md-conceal", start_char, end_char)

    def _highlight_code_line(self, line: str, line_start: int):
        comment_match = re.search(r'(#.*|//.*)$', line)
        code_end = len(line)
        if comment_match:
            c_start = comment_match.start()
            self._apply_tag("md-code-comment", line_start + c_start, line_start + len(line))
            code_end = c_start

        code_slice = line[:code_end]
        for m in re.finditer(r'("[^"\\]*(?:\\.[^"\\]*)*"|\'[^\'\\]*(?:\\.[^\'\\]*)*\')', code_slice):
            self._apply_tag("md-code-string", line_start + m.start(), line_start + m.end())

        kw_pattern = r'\b(def|class|return|import|from|if|elif|else|for|while|try|except|finally|with|as|pass|break|continue|lambda|yield|async|await|True|False|None|function|const|let|var|struct|impl|fn|pub|use)\b'
        for m in re.finditer(kw_pattern, code_slice):
            self._apply_tag("md-code-keyword", line_start + m.start(), line_start + m.end())

    def _apply_focus_shade(self, lines: List[str], line_offsets: List[Tuple[int, int]], cursor_line: int):
        if not lines:
            return
        first = cursor_line
        while first > 0 and lines[first - 1].strip() != "":
            first -= 1
        last = cursor_line
        while last + 1 < len(lines) and lines[last + 1].strip() != "":
            last += 1
        if first > 0:
            self._apply_tag("md-unfocused", 0, line_offsets[first][0])
        if last + 1 < len(lines):
            self._apply_tag("md-unfocused", line_offsets[last + 1][0], line_offsets[-1][1])

    def refresh(self):
        if self._updating:
            return
        self._updating = True
        try:
            start_iter, end_iter = self.buffer.get_bounds()
            for tag_name in self.managed_tags:
                self.buffer.remove_tag_by_name(tag_name, start_iter, end_iter)

            full_text = self.buffer.get_text(start_iter, end_iter, True)
            cursor_line, cursor_offset = self._get_cursor_line_and_offset()
            self._last_cursor_line = cursor_line
            self._last_cursor_offset = cursor_offset

            lines = full_text.split("\n")
            line_count = len(lines)
            ornaments: List[MarkdownOrnament] = []
            links: List[MarkdownLinkRef] = []
            line_offsets: List[Tuple[int, int]] = []

            in_code_block = False
            code_block_first = 0
            quote_first: Optional[int] = None
            table_first: Optional[int] = None
            offset = 0

            for line_idx, line in enumerate(lines):
                line_len = len(line)
                line_start = offset
                line_end = offset + line_len
                line_offsets.append((line_start, line_end))
                is_active = (line_idx == cursor_line)
                stripped = line.strip()

                # Close open quote/table ranges when line no longer matches
                if quote_first is not None and not re.match(r'^\s*>', line):
                    ornaments.append(MarkdownOrnament(kind="quote", first=quote_first, last=line_idx - 1))
                    quote_first = None
                if table_first is not None and not (stripped.startswith("|") and stripped.endswith("|")):
                    ornaments.append(MarkdownOrnament(kind="table", first=table_first, last=line_idx - 1))
                    table_first = None

                # Code fences
                if stripped.startswith("```"):
                    self._apply_tag("md-fence", line_start, line_end)
                    self._style_replaced(line_start, line_end, is_active)
                    if not in_code_block:
                        in_code_block = True
                        code_block_first = line_idx
                    else:
                        in_code_block = False
                        ornaments.append(MarkdownOrnament(kind="codeblock", first=code_block_first, last=line_idx))
                    offset += line_len + 1
                    continue

                if in_code_block:
                    self._apply_tag("md-code-block", line_start, line_end)
                    self._highlight_code_line(line, line_start)
                    offset += line_len + 1
                    continue

                # Horizontal Rule
                if stripped in ("---", "***", "___"):
                    self._apply_tag("md-hr", line_start, line_end)
                    self._style_replaced(line_start, line_end, is_active)
                    ornaments.append(MarkdownOrnament(kind="rule", line=line_idx))
                    offset += line_len + 1
                    continue

                # Table rows
                if stripped.startswith("|") and stripped.endswith("|") and len(stripped) >= 3:
                    if table_first is None:
                        table_first = line_idx
                    cells = [c.strip() for c in stripped.strip("|").split("|")]
                    is_delim = all(bool(re.match(r'^:?-+:?$', c)) for c in cells if c) and any(cells)
                    if is_delim:
                        self._apply_tag("md-table", line_start, line_end)
                        if not is_active and self.inline_render and self.markup_visibility != "dim":
                            self._apply_tag("md-tablerule", line_start, line_end)
                        ornaments.append(MarkdownOrnament(kind="tablerule", line=line_idx))
                    else:
                        tag_name = "md-tablehead" if line_idx == table_first else "md-table"
                        self._apply_tag(tag_name, line_start, line_end)
                        for pm in re.finditer(r'\|', line):
                            p_pos = line_start + pm.start()
                            self._apply_tag("md-syntax", p_pos, p_pos + 1)
                    offset += line_len + 1
                    continue

                # Headings H1..H6
                h_match = re.match(r'^(\s*)(#{1,6})(\s+)(.*)$', line)
                if h_match:
                    level = min(6, len(h_match.group(2)))
                    prefix_end = line_start + len(h_match.group(1)) + len(h_match.group(2)) + len(h_match.group(3))
                    self._apply_tag(f"md-h{level}", line_start, line_end)
                    self._style_replaced(line_start, prefix_end, is_active)
                    if level in (1, 2):
                        ornaments.append(MarkdownOrnament(kind="headingrule", line=line_idx))

                # Blockquote
                q_match = re.match(r'^(\s*>\s?)(.*)$', line)
                if q_match:
                    if quote_first is None:
                        quote_first = line_idx
                    prefix_len = len(q_match.group(1))
                    self._apply_tag("md-quote", line_start, line_end)
                    self._style_replaced(line_start, line_start + prefix_len, is_active)

                # Task List & Bullet/Ordered List
                task_match = re.match(r'^(\s*)([-*+]|\d+[.)])(\s+\[([ xX])\]\s+)', line)
                if task_match:
                    indent_len = len(task_match.group(1))
                    depth = min(3, max(1, (indent_len // 2) + 1))
                    marker_len = len(task_match.group(0))
                    is_checked = task_match.group(4).lower() == 'x'
                    self._apply_tag(f"md-list{depth}", line_start, line_end)
                    self._style_replaced(line_start, line_start + marker_len, is_active)
                    if is_checked:
                        self._apply_tag("md-strike", line_start + marker_len, line_end)
                    ornaments.append(MarkdownOrnament(kind="checkbox", line=line_idx, checked=is_checked, depth=depth))
                else:
                    ul_match = re.match(r'^(\s*)([-*+])(\s+)', line)
                    if ul_match:
                        indent_len = len(ul_match.group(1))
                        depth = min(3, max(1, (indent_len // 2) + 1))
                        marker_len = len(ul_match.group(0))
                        self._apply_tag(f"md-list{depth}", line_start, line_end)
                        self._style_replaced(line_start, line_start + marker_len, is_active)
                        ornaments.append(MarkdownOrnament(kind="bullet", line=line_idx, depth=depth))
                    else:
                        ol_match = re.match(r'^(\s*)(\d+[.)]\s+)', line)
                        if ol_match:
                            indent_len = len(ol_match.group(1))
                            depth = min(3, max(1, (indent_len // 2) + 1))
                            self._apply_tag(f"md-list{depth}", line_start, line_end)
                            self._apply_tag("md-list-marker", line_start, line_start + len(ol_match.group(0)))

                # Inline Code (`...`)
                code_ranges = []
                for m in re.finditer(r'(`+)([^`]+?)\1', line):
                    s = line_start + m.start()
                    e = line_start + m.end()
                    d_len = len(m.group(1))
                    code_ranges.append((m.start(), m.end()))
                    self._apply_tag("md-inline-code", s, e)
                    self._style_marker(s, s + d_len, s, e, cursor_offset, is_active)
                    self._style_marker(e - d_len, e, s, e, cursor_offset, is_active)

                def in_code_span(pos_start, pos_end):
                    for cs, ce in code_ranges:
                        if pos_start < ce and pos_end > cs:
                            return True
                    return False

                # Bold + Italic (***text*** or ___text___)
                for m in re.finditer(r'(\*\*\*|___)(.+?)\1', line):
                    if in_code_span(m.start(), m.end()):
                        continue
                    s, e = line_start + m.start(), line_start + m.end()
                    self._apply_tag("md-bold-italic", s, e)
                    self._style_marker(s, s + 3, s, e, cursor_offset, is_active)
                    self._style_marker(e - 3, e, s, e, cursor_offset, is_active)

                # Bold (**text** or __text__)
                for m in re.finditer(r'(?<!\*)(\*\*)(?!\*)(.+?)(?<!\*)\1(?!\*)|(?<!_)(__)(?!_)(.+?)(?<!_)\3(?!_)', line):
                    if in_code_span(m.start(), m.end()):
                        continue
                    s, e = line_start + m.start(), line_start + m.end()
                    self._apply_tag("md-bold", s, e)
                    self._style_marker(s, s + 2, s, e, cursor_offset, is_active)
                    self._style_marker(e - 2, e, s, e, cursor_offset, is_active)

                # Italic (*text* or _text_)
                for m in re.finditer(r'(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)|(?<!_)_(?!_)(.+?)(?<!_)_(?!_)', line):
                    if in_code_span(m.start(), m.end()):
                        continue
                    s, e = line_start + m.start(), line_start + m.end()
                    self._apply_tag("md-italic", s, e)
                    self._style_marker(s, s + 1, s, e, cursor_offset, is_active)
                    self._style_marker(e - 1, e, s, e, cursor_offset, is_active)

                # Strikethrough (~~text~~)
                for m in re.finditer(r'(~~)(.+?)\1', line):
                    if in_code_span(m.start(), m.end()):
                        continue
                    s, e = line_start + m.start(), line_start + m.end()
                    self._apply_tag("md-strike", s, e)
                    self._style_marker(s, s + 2, s, e, cursor_offset, is_active)
                    self._style_marker(e - 2, e, s, e, cursor_offset, is_active)

                # Links ([label](url)), excluding image tags (![alt](url))
                for m in re.finditer(r'(\[)([^\]]+)(\]\()([^\)]+)(\))', line):
                    if in_code_span(m.start(), m.end()):
                        continue
                    if m.start() > 0 and line[m.start() - 1] == '!':
                        continue
                    s = line_start + m.start()
                    lbl_start = s + 1
                    lbl_end = lbl_start + len(m.group(2))
                    url_start = lbl_end + 2
                    url_end = url_start + len(m.group(4))
                    e = url_end + 1

                    self._style_marker(s, lbl_start, s, e, cursor_offset, is_active)
                    self._apply_tag("md-link-label", lbl_start, lbl_end)
                    self._style_marker(lbl_end, e, s, e, cursor_offset, is_active)
                    self._apply_tag("md-link-url", url_start, url_end)
                    links.append(MarkdownLinkRef(start=s, end=e, dest=m.group(4).strip()))

                # Wiki-links ([[target|label]] or [[target]])
                for m in re.finditer(r'(\[\[)([^\|\]]+)(?:\|([^\]]+))?(\]\])', line):
                    if in_code_span(m.start(), m.end()):
                        continue
                    s = line_start + m.start()
                    e = line_start + m.end()
                    raw_dest = m.group(2).strip()
                    self._style_marker(s, s + 2, s, e, cursor_offset, is_active)
                    self._apply_tag("md-link-label", s + 2, e - 2)
                    self._style_marker(e - 2, e, s, e, cursor_offset, is_active)
                    links.append(MarkdownLinkRef(start=s, end=e, dest=raw_dest))

                # Image tags (![alt](url))
                for m in re.finditer(r'(!\[)([^\]]*)(\]\()([^\)]+)(\))', line):
                    if in_code_span(m.start(), m.end()):
                        continue
                    s = line_start + m.start()
                    e = line_start + m.end()
                    alt_len = len(m.group(2))
                    self._apply_tag("md-img-mark", s, s + 2)
                    if alt_len > 0:
                        self._apply_tag("md-img-alt", s + 2, s + 2 + alt_len)
                    self._style_marker(s + 2 + alt_len, s + 2 + alt_len + 2, s, e, cursor_offset, is_active)
                    self._apply_tag("md-img-url", s + 2 + alt_len + 2, e - 1)
                    self._style_marker(e - 1, e, s, e, cursor_offset, is_active)

                offset += line_len + 1

            # Flush trailing block ornaments
            if in_code_block:
                ornaments.append(MarkdownOrnament(kind="codeblock", first=code_block_first, last=max(0, line_count - 1)))
            if quote_first is not None:
                ornaments.append(MarkdownOrnament(kind="quote", first=quote_first, last=max(0, line_count - 1)))
            if table_first is not None:
                ornaments.append(MarkdownOrnament(kind="table", first=table_first, last=max(0, line_count - 1)))

            # Focus Mode: dim everything outside current paragraph
            if self.focus_mode:
                self._apply_focus_shade(lines, line_offsets, cursor_line)

            if isinstance(self.text_view, ScribeMarkdownView):
                self.text_view.set_ornaments(ornaments, links, line_count)
        finally:
            self._updating = False


class MarkdownRenderer:
    """Helper to convert Markdown text into styled GTK4 widgets and rich Cairo thumbnails using Scribe themes."""

    @staticmethod
    def inline_to_pango(text, is_dark=None, theme: Optional[ScribeTheme] = None):
        if theme is None:
            if is_dark is None:
                try:
                    gi.require_version("Adw", "1")
                    from gi.repository import Adw
                    is_dark = Adw.StyleManager.get_default().get_dark()
                except Exception:
                    is_dark = False
            theme = resolve_scribe_theme("auto", bool(is_dark))

        escaped = html.escape(text, quote=False)

        code_spans = []
        def save_code(match):
            idx = len(code_spans)
            code_bg = "#2b3036" if theme.is_dark else "#eaeef2"
            code_fg = theme.code
            code_spans.append(
                f'<tt><span background="{code_bg}" foreground="{code_fg}"> {match.group(1)} </span></tt>'
            )
            return f"\x00CODESPAN{idx}\x00"

        escaped = re.sub(r'`([^`]+)`', save_code, escaped)
        link_fg = theme.accent
        def _replace_md_link(m):
            lbl = m.group(1)
            target = html.escape(m.group(2).strip(), quote=True)
            return f'<a href="{target}"><span foreground="{link_fg}" underline="single">{lbl}</span></a>'

        escaped = re.sub(r'(?<!\!)\[([^\]]+)\]\(([^)]+)\)', _replace_md_link, escaped)

        def _replace_wiki_link(m):
            raw = m.group(1).strip()
            if "|" in raw:
                target, lbl = raw.split("|", 1)
            else:
                target, lbl = raw, raw
            target_esc = html.escape(target.strip(), quote=True)
            lbl_esc = lbl.strip()
            return f'<a href="{target_esc}"><span foreground="{link_fg}" underline="single">{lbl_esc}</span></a>'

        escaped = re.sub(r'\[\[([^\]]+)\]\]', _replace_wiki_link, escaped)
        escaped = re.sub(r'\*\*\*(.+?)\*\*\*', r'<b><i>\1</i></b>', escaped)
        escaped = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', escaped)
        escaped = re.sub(r'__(.+?)__', r'<b>\1</b>', escaped)
        escaped = re.sub(r'(?<!\*)\*([^*]+)\*(?!\*)', r'<i>\1</i>', escaped)
        escaped = re.sub(r'(?<!_)_([^_]+)_(?!_)', r'<i>\1</i>', escaped)
        escaped = re.sub(r'~~(.+?)~~', rf'<span foreground="{theme.muted}"><s>\1</s></span>', escaped)

        for idx, span in enumerate(code_spans):
            escaped = escaped.replace(f"\x00CODESPAN{idx}\x00", span)

        return escaped

    @classmethod
    def populate_box(cls, container, md_text, zoom_level=1.0, theme: Optional[ScribeTheme] = None, base_dir: Optional[str] = None, on_link_clicked: Optional[callable] = None):
        if theme is None:
            try:
                gi.require_version("Adw", "1")
                from gi.repository import Adw
                is_dark = Adw.StyleManager.get_default().get_dark()
            except Exception:
                is_dark = False
            theme = resolve_scribe_theme("auto", is_dark)

        child = container.get_first_child()
        while child is not None:
            next_child = child.get_next_sibling()
            container.remove(child)
            child = next_child

        lines = md_text.splitlines()
        in_code_block = False
        code_lines = []
        paragraph_lines = []
        heading_widgets = []

        def flush_paragraph():
            if not paragraph_lines:
                return
            joined = " ".join(paragraph_lines)
            paragraph_lines.clear()
            lbl = Gtk.Label(
                xalign=0.0,
                wrap=True,
                wrap_mode=Pango.WrapMode.WORD_CHAR,
                selectable=True
            )
            markup = cls.inline_to_pango(joined, theme=theme)
            pt = max(8, int(11.5 * zoom_level))
            try:
                lbl.set_markup(f'<span size="{pt * 1024}" foreground="{theme.fg}">{markup}</span>')
            except Exception:
                lbl.set_text(joined)
            if on_link_clicked:
                lbl.connect("activate-link", lambda _l, uri: on_link_clicked(uri))
            lbl.set_margin_bottom(int(10 * zoom_level))
            container.append(lbl)

        for line_idx, raw_line in enumerate(lines):
            stripped = raw_line.strip()

            if stripped.startswith("```"):
                flush_paragraph()
                if in_code_block:
                    code_text = "\n".join(code_lines)
                    code_lines.clear()
                    in_code_block = False

                    code_frame = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
                    code_frame.add_css_class("md-code-block")
                    code_frame.set_margin_top(int(6 * zoom_level))
                    code_frame.set_margin_bottom(int(10 * zoom_level))

                    code_lbl = Gtk.Label(
                        label=code_text,
                        xalign=0.0,
                        selectable=True,
                        wrap=True,
                        wrap_mode=Pango.WrapMode.CHAR
                    )
                    code_lbl.add_css_class("monospace")
                    pt = max(7, int(10.5 * zoom_level))
                    attrs = Pango.AttrList()
                    attrs.insert(Pango.AttrSize.new(pt * Pango.SCALE))
                    code_lbl.set_attributes(attrs)
                    code_frame.append(code_lbl)
                    container.append(code_frame)
                else:
                    in_code_block = True
                continue

            if in_code_block:
                code_lines.append(raw_line)
                continue

            if not stripped:
                flush_paragraph()
                continue

            if stripped in ("---", "***", "___"):
                flush_paragraph()
                sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
                sep.set_margin_top(int(12 * zoom_level))
                sep.set_margin_bottom(int(12 * zoom_level))
                container.append(sep)
                continue

            # Image: ![alt](url "title")
            img_match = re.match(r"""^\s*!\[([^\]]*)\]\(([^)\s]+)(?:\s+["']([^"']*)["'])?\)\s*$""", stripped)
            if img_match:
                flush_paragraph()
                alt_text = img_match.group(1)
                img_src = img_match.group(2).strip()
                title = img_match.group(3) or alt_text

                clean_src = img_src[7:] if img_src.startswith("file://") else img_src
                if not os.path.isabs(clean_src) and base_dir:
                    resolved_path = os.path.normpath(os.path.join(base_dir, clean_src))
                    if not os.path.isfile(resolved_path):
                        for pfx, alt_pfx in [("assets/", ".assets/"), (".assets/", "assets/")]:
                            if clean_src.startswith(pfx):
                                alt_p = os.path.normpath(os.path.join(base_dir, clean_src.replace(pfx, alt_pfx, 1)))
                                if os.path.isfile(alt_p):
                                    resolved_path = alt_p
                                    break
                else:
                    resolved_path = clean_src

                img_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=int(6 * zoom_level), halign=Gtk.Align.CENTER)
                img_box.add_css_class("md-image-box")
                img_box.set_margin_top(int(10 * zoom_level))
                img_box.set_margin_bottom(int(14 * zoom_level))

                if os.path.isfile(resolved_path):
                    try:
                        pic = Gtk.Picture.new_for_filename(resolved_path)
                        pic.set_can_shrink(True)
                        pic.set_content_fit(Gtk.ContentFit.CONTAIN)
                        pic.add_css_class("md-preview-picture")
                        img_box.append(pic)
                    except Exception as err:
                        err_lbl = Gtk.Label(label=f"Failed to display image: {err}")
                        err_lbl.add_css_class("error")
                        img_box.append(err_lbl)
                else:
                    placeholder = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
                    placeholder.add_css_class("md-image-placeholder")
                    placeholder.append(Gtk.Image.new_from_icon_name("image-missing-symbolic"))
                    placeholder.append(Gtk.Label(label=f"Image not found: {img_src}", halign=Gtk.Align.START))
                    img_box.append(placeholder)

                if title:
                    caption_lbl = Gtk.Label(label=title, halign=Gtk.Align.CENTER)
                    caption_lbl.add_css_class("dim-label")
                    caption_lbl.add_css_class("caption")
                    img_box.append(caption_lbl)

                container.append(img_box)
                continue

            heading_match = re.match(r'^(#{1,6})\s+(.*)$', stripped)
            if heading_match:
                flush_paragraph()
                level = len(heading_match.group(1))
                content = cls.inline_to_pango(heading_match.group(2), theme=theme)
                scales = {1: 22, 2: 17, 3: 14, 4: 12.5, 5: 11.5, 6: 11}
                pt = max(9, int(scales.get(level, 12) * zoom_level))

                hbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=int(6 * zoom_level))
                hbox.set_margin_top(int(8 * zoom_level))
                hbox.set_margin_bottom(int(14 * zoom_level))

                hbox._md_line = line_idx
                heading_widgets.append((line_idx, hbox))

                lbl = Gtk.Label(
                    xalign=0.0,
                    wrap=True,
                    wrap_mode=Pango.WrapMode.WORD_CHAR,
                    selectable=True
                )
                try:
                    lbl.set_markup(
                        f'<span size="{pt * 1024}" weight="bold" foreground="{theme.heading}">{content}</span>'
                    )
                except Exception:
                    lbl.set_text(heading_match.group(2))
                if on_link_clicked:
                    lbl.connect("activate-link", lambda _l, uri: on_link_clicked(uri))
                hbox.append(lbl)

                if level in (1, 2):
                    h_sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
                    h_sep.add_css_class("md-heading-rule")
                    hbox.append(h_sep)

                container.append(hbox)
                continue

            if stripped.startswith(">"):
                flush_paragraph()
                quote_content = cls.inline_to_pango(stripped.lstrip(">").strip(), theme=theme)
                qbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
                qbox.add_css_class("md-blockquote")
                qbox.set_margin_bottom(int(8 * zoom_level))
                lbl = Gtk.Label(
                    xalign=0.0,
                    wrap=True,
                    wrap_mode=Pango.WrapMode.WORD_CHAR,
                    selectable=True
                )
                pt = max(8, int(11.5 * zoom_level))
                try:
                    lbl.set_markup(
                        f'<span size="{pt * 1024}" style="italic" foreground="{theme.quote}">{quote_content}</span>'
                    )
                except Exception:
                    lbl.set_text(stripped.lstrip(">").strip())
                qbox.append(lbl)
                container.append(qbox)
                continue

            ul_match = re.match(r'^(\s*)([-*+])\s+(.*)$', raw_line)
            if ul_match:
                flush_paragraph()
                indent = len(ul_match.group(1)) // 2
                item_text = ul_match.group(3)
                bullet = "•"
                if item_text.startswith("[ ] "):
                    bullet = "☐"
                    item_text = item_text[4:]
                elif item_text.lower().startswith("[x] "):
                    bullet = "☑"
                    item_text = item_text[4:]

                row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
                row.set_margin_start(int((14 + indent * 18) * zoom_level))
                row.set_margin_bottom(int(6 * zoom_level))

                pt = max(8, int(11.5 * zoom_level))
                b_lbl = Gtk.Label(valign=Gtk.Align.START)
                b_lbl.set_markup(f'<span size="{pt * 1024}" foreground="{theme.accent}"><b>{bullet}</b></span>')
                row.append(b_lbl)

                t_lbl = Gtk.Label(
                    xalign=0.0,
                    wrap=True,
                    wrap_mode=Pango.WrapMode.WORD_CHAR,
                    selectable=True,
                    hexpand=True
                )
                try:
                    t_lbl.set_markup(
                        f'<span size="{pt * 1024}" foreground="{theme.fg}">{cls.inline_to_pango(item_text, theme=theme)}</span>'
                    )
                except Exception:
                    t_lbl.set_text(item_text)
                if on_link_clicked:
                    t_lbl.connect("activate-link", lambda _l, uri: on_link_clicked(uri))
                row.append(t_lbl)
                container.append(row)
                continue

            ol_match = re.match(r'^(\s*)(\d+\.)\s+(.*)$', raw_line)
            if ol_match:
                flush_paragraph()
                indent = len(ol_match.group(1)) // 2
                num = ol_match.group(2)
                item_text = ol_match.group(3)

                row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
                row.set_margin_start(int((14 + indent * 18) * zoom_level))
                row.set_margin_bottom(int(6 * zoom_level))

                pt = max(8, int(11.5 * zoom_level))
                n_lbl = Gtk.Label(valign=Gtk.Align.START)
                n_lbl.set_markup(f'<span size="{pt * 1024}" foreground="{theme.accent}"><b>{num}</b></span>')
                row.append(n_lbl)

                t_lbl = Gtk.Label(
                    xalign=0.0,
                    wrap=True,
                    wrap_mode=Pango.WrapMode.WORD_CHAR,
                    selectable=True,
                    hexpand=True
                )
                try:
                    t_lbl.set_markup(
                        f'<span size="{pt * 1024}" foreground="{theme.fg}">{cls.inline_to_pango(item_text, theme=theme)}</span>'
                    )
                except Exception:
                    t_lbl.set_text(item_text)
                if on_link_clicked:
                    t_lbl.connect("activate-link", lambda _l, uri: on_link_clicked(uri))
                row.append(t_lbl)
                container.append(row)
                continue

            paragraph_lines.append(stripped)

        flush_paragraph()
        container._heading_widgets = heading_widgets

    @staticmethod
    def _rounded_rect(cr, x, y, w, h, r):
        cr.new_sub_path()
        cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
        cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
        cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
        cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
        cr.close_path()

    @classmethod
    def draw_markdown_thumb(cls, cr, width, height, md_text):
        def inline(txt):
            return cls.inline_to_pango(txt, is_dark=False)
        """Render a miniature formatted preview of the Markdown document onto a Cairo thumbnail."""
        cr.save()
        radius = 12.0
        cls._rounded_rect(cr, 0, 0, width, height, radius)
        cr.clip()

        # Paper background
        cr.set_source_rgb(1.0, 1.0, 1.0)
        cr.rectangle(0, 0, width, height)
        cr.fill()

        pad_x = 12.0
        y_cursor = 12.0
        avail_w = max(20.0, width - pad_x * 2.0)
        max_y = height - 10.0

        lines = md_text.splitlines()
        in_code_block = False
        code_lines = []
        paragraph_lines = []

        def render_pango_block(markup_str, fallback_text, font_str, x_off=0.0, color=(0.14, 0.14, 0.16)):
            nonlocal y_cursor
            if y_cursor >= max_y:
                return
            layout = PangoCairo.create_layout(cr)
            layout.set_font_description(Pango.FontDescription.from_string(font_str))
            layout.set_width(int(max(10.0, avail_w - x_off) * Pango.SCALE))
            layout.set_wrap(Pango.WrapMode.WORD_CHAR)
            try:
                layout.set_markup(markup_str, -1)
            except Exception:
                layout.set_text(fallback_text, -1)

            _, logical = layout.get_pixel_extents()
            cr.set_source_rgb(*color)
            cr.move_to(pad_x + x_off, y_cursor)
            PangoCairo.show_layout(cr, layout)
            y_cursor += logical.height + 4.0

        def flush_paragraph():
            nonlocal y_cursor
            if not paragraph_lines or y_cursor >= max_y:
                paragraph_lines.clear()
                return
            joined = " ".join(paragraph_lines)
            paragraph_lines.clear()
            render_pango_block(inline(joined), joined, "Sans 6.5")

        for raw_line in lines:
            if y_cursor >= max_y:
                break
            stripped = raw_line.strip()

            if stripped.startswith("```"):
                flush_paragraph()
                if in_code_block:
                    code_str = "\n".join(code_lines[:6])
                    code_lines.clear()
                    in_code_block = False
                    if code_str and y_cursor < max_y:
                        layout = PangoCairo.create_layout(cr)
                        layout.set_font_description(Pango.FontDescription.from_string("Monospace 5.5"))
                        layout.set_width(int(max(10.0, avail_w - 8.0) * Pango.SCALE))
                        layout.set_wrap(Pango.WrapMode.CHAR)
                        layout.set_text(code_str, -1)
                        _, logical = layout.get_pixel_extents()
                        box_h = logical.height + 6.0

                        cr.set_source_rgb(0.95, 0.95, 0.97)
                        cls._rounded_rect(cr, pad_x, y_cursor, avail_w, box_h, 4.0)
                        cr.fill()
                        cr.set_source_rgba(0, 0, 0, 0.08)
                        cr.set_line_width(0.8)
                        cls._rounded_rect(cr, pad_x, y_cursor, avail_w, box_h, 4.0)
                        cr.stroke()

                        cr.set_source_rgb(0.18, 0.18, 0.20)
                        cr.move_to(pad_x + 4.0, y_cursor + 3.0)
                        PangoCairo.show_layout(cr, layout)
                        y_cursor += box_h + 4.0
                else:
                    in_code_block = True
                continue

            if in_code_block:
                code_lines.append(raw_line)
                continue

            if not stripped:
                flush_paragraph()
                continue

            if stripped in ("---", "***", "___"):
                flush_paragraph()
                cr.set_source_rgba(0, 0, 0, 0.18)
                cr.set_line_width(1.0)
                cr.move_to(pad_x, y_cursor + 2.0)
                cr.line_to(pad_x + avail_w, y_cursor + 2.0)
                cr.stroke()
                y_cursor += 6.0
                continue

            heading_match = re.match(r'^(#{1,6})\s+(.*)$', stripped)
            if heading_match:
                flush_paragraph()
                level = len(heading_match.group(1))
                text_body = heading_match.group(2)
                markup = f"<b>{inline(text_body)}</b>"
                sizes = {1: "9.5", 2: "8.2", 3: "7.4", 4: "6.8", 5: "6.5", 6: "6.2"}
                pt_str = sizes.get(level, "6.8")
                y_cursor += 1.5
                render_pango_block(markup, text_body, f"Sans Bold {pt_str}", color=(0.08, 0.08, 0.10))
                continue

            if stripped.startswith(">"):
                flush_paragraph()
                q_body = stripped.lstrip(">").strip()
                q_markup = f"<i>{inline(q_body)}</i>"
                start_y = y_cursor
                render_pango_block(q_markup, q_body, "Sans Italic 6.2", x_off=6.0, color=(0.30, 0.34, 0.40))
                end_y = max(start_y + 6.0, y_cursor - 3.0)
                cr.set_source_rgb(0.21, 0.52, 0.89)
                cr.set_line_width(2.0)
                cr.move_to(pad_x + 1.5, start_y)
                cr.line_to(pad_x + 1.5, end_y)
                cr.stroke()
                continue

            ul_match = re.match(r'^(\s*)([-*+])\s+(.*)$', raw_line)
            if ul_match:
                flush_paragraph()
                indent = min(3, len(ul_match.group(1)) // 2)
                item_text = ul_match.group(3)
                bullet = "•"
                if item_text.startswith("[ ] "):
                    bullet = "☐"
                    item_text = item_text[4:]
                elif item_text.lower().startswith("[x] "):
                    bullet = "☑"
                    item_text = item_text[4:]
                markup = f"<b>{bullet}</b> {inline(item_text)}"
                render_pango_block(markup, f"{bullet} {item_text}", "Sans 6.3", x_off=indent * 6.0)
                continue

            ol_match = re.match(r'^(\s*)(\d+\.)\s+(.*)$', raw_line)
            if ol_match:
                flush_paragraph()
                indent = min(3, len(ol_match.group(1)) // 2)
                num = ol_match.group(2)
                item_text = ol_match.group(3)
                markup = f"<b>{html.escape(num)}</b> {inline(item_text)}"
                render_pango_block(markup, f"{num} {item_text}", "Sans 6.3", x_off=indent * 6.0)
                continue

            paragraph_lines.append(stripped)

        flush_paragraph()

        if y_cursor <= 13.0:
            render_pango_block("<i>Empty Note</i>", "Empty Note", "Sans Italic 7", color=(0.55, 0.55, 0.58))

        if y_cursor >= max_y:
            grad = cairo.LinearGradient(0, height - 24, 0, height)
            grad.add_color_stop_rgba(0.0, 1.0, 1.0, 1.0, 0.0)
            grad.add_color_stop_rgba(1.0, 1.0, 1.0, 1.0, 0.95)
            cr.set_source(grad)
            cr.rectangle(0, height - 24, width, 24)
            cr.fill()

        cr.set_source_rgba(0, 0, 0, 0.14)
        cr.set_line_width(2.0)
        cls._rounded_rect(cr, 0, 0, width, height, radius)
        cr.stroke()
        cr.restore()
        del inline

    @classmethod
    def export_to_pdf(cls, pdf_path: str, md_text: str, page_width: float = 612.0, page_height: float = 792.0, base_dir: Optional[str] = None):
        def inline(txt):
            return cls.inline_to_pango(txt, is_dark=False)
        """Export the rendered Markdown document as a multi-page vector PDF."""
        surface = cairo.PDFSurface(pdf_path, page_width, page_height)
        cr = cairo.Context(surface)

        pad_x = 54.0
        pad_top = 54.0
        pad_bottom = 54.0
        avail_w = page_width - pad_x * 2.0
        max_y = page_height - pad_bottom
        y_cursor = pad_top
        page_num = 1

        def start_page():
            cr.save()
            cr.set_source_rgb(1.0, 1.0, 1.0)
            cr.rectangle(0, 0, page_width, page_height)
            cr.fill()
            cr.restore()

        def new_page():
            nonlocal y_cursor, page_num
            cr.show_page()
            page_num += 1
            start_page()
            y_cursor = pad_top

        start_page()

        lines = md_text.splitlines()
        in_code_block = False
        code_lines = []
        paragraph_lines = []

        def render_pango_block(markup_str, fallback_text, font_str, x_off=0.0, color=(0.14, 0.16, 0.18), space_below=6.0):
            nonlocal y_cursor
            layout = PangoCairo.create_layout(cr)
            layout.set_font_description(Pango.FontDescription.from_string(font_str))
            layout.set_width(int(max(10.0, avail_w - x_off) * Pango.SCALE))
            layout.set_wrap(Pango.WrapMode.WORD_CHAR)
            try:
                layout.set_markup(markup_str, -1)
            except Exception:
                layout.set_text(fallback_text, -1)

            _, logical = layout.get_pixel_extents()
            block_h = logical.height

            if y_cursor > pad_top and (y_cursor + block_h > max_y):
                new_page()

            cr.save()
            cr.set_source_rgb(*color)
            cr.move_to(pad_x + x_off, y_cursor)
            PangoCairo.show_layout(cr, layout)
            cr.restore()
            y_cursor += block_h + space_below

        def flush_paragraph():
            nonlocal y_cursor
            if not paragraph_lines:
                return
            joined = " ".join(paragraph_lines)
            paragraph_lines.clear()
            render_pango_block(inline(joined), joined, "Sans 11", space_below=8.0)

        for raw_line in lines:
            stripped = raw_line.strip()

            if stripped.startswith("```"):
                flush_paragraph()
                if in_code_block:
                    code_str = "\n".join(code_lines)
                    code_lines.clear()
                    in_code_block = False
                    if code_str:
                        layout = PangoCairo.create_layout(cr)
                        layout.set_font_description(Pango.FontDescription.from_string("Monospace 9.5"))
                        layout.set_width(int(max(10.0, avail_w - 20.0) * Pango.SCALE))
                        layout.set_wrap(Pango.WrapMode.CHAR)
                        layout.set_text(code_str, -1)
                        _, logical = layout.get_pixel_extents()
                        box_h = logical.height + 16.0

                        if y_cursor > pad_top and (y_cursor + box_h > max_y):
                            new_page()

                        cr.save()
                        cr.set_source_rgb(0.96, 0.97, 0.98)
                        cls._rounded_rect(cr, pad_x, y_cursor, avail_w, box_h, 4.0)
                        cr.fill()
                        cr.set_source_rgba(0, 0, 0, 0.12)
                        cr.set_line_width(0.8)
                        cls._rounded_rect(cr, pad_x, y_cursor, avail_w, box_h, 4.0)
                        cr.stroke()

                        cr.set_source_rgb(0.18, 0.19, 0.21)
                        cr.move_to(pad_x + 10.0, y_cursor + 8.0)
                        PangoCairo.show_layout(cr, layout)
                        cr.restore()
                        y_cursor += box_h + 10.0
                else:
                    in_code_block = True
                continue

            if in_code_block:
                code_lines.append(raw_line)
                continue

            if not stripped:
                flush_paragraph()
                continue

            if stripped in ("---", "***", "___"):
                flush_paragraph()
                if y_cursor > pad_top and (y_cursor + 16.0 > max_y):
                    new_page()
                cr.save()
                cr.set_source_rgba(0, 0, 0, 0.16)
                cr.set_line_width(1.0)
                cr.move_to(pad_x, y_cursor + 8.0)
                cr.line_to(pad_x + avail_w, y_cursor + 8.0)
                cr.stroke()
                cr.restore()
                y_cursor += 18.0
                continue

            img_match = re.match(r"""^\s*!\[([^\]]*)\]\(([^)\s]+)(?:\s+["']([^"']*)["'])?\)\s*$""", stripped)
            if img_match:
                flush_paragraph()
                img_src = img_match.group(2).strip()
                clean_src = img_src[7:] if img_src.startswith("file://") else img_src
                if not os.path.isabs(clean_src) and base_dir:
                    resolved_path = os.path.normpath(os.path.join(base_dir, clean_src))
                    if not os.path.isfile(resolved_path):
                        for pfx, alt_pfx in [("assets/", ".assets/"), (".assets/", "assets/")]:
                            if clean_src.startswith(pfx):
                                alt_p = os.path.normpath(os.path.join(base_dir, clean_src.replace(pfx, alt_pfx, 1)))
                                if os.path.isfile(alt_p):
                                    resolved_path = alt_p
                                    break
                else:
                    resolved_path = clean_src

                if os.path.isfile(resolved_path):
                    try:
                        pb = GdkPixbuf.Pixbuf.new_from_file(resolved_path)
                        orig_w = pb.get_width()
                        orig_h = pb.get_height()
                        target_w = min(float(orig_w), avail_w)
                        scale = target_w / max(1.0, float(orig_w))
                        target_h = orig_h * scale

                        if y_cursor > pad_top and (y_cursor + target_h > max_y):
                            new_page()

                        if scale < 0.99 or scale > 1.01:
                            pb_scaled = pb.scale_simple(max(1, int(target_w)), max(1, int(target_h)), GdkPixbuf.InterpType.BILINEAR)
                        else:
                            pb_scaled = pb

                        cr.save()
                        x_pos = pad_x + (avail_w - target_w) / 2.0
                        Gdk.cairo_set_source_pixbuf(cr, pb_scaled, x_pos, y_cursor)
                        cr.paint()
                        cr.restore()
                        y_cursor += target_h + 12.0
                    except Exception:
                        pass
                continue

            heading_match = re.match(r'^(#{1,6})\s+(.*)$', stripped)
            if heading_match:
                flush_paragraph()
                level = len(heading_match.group(1))
                text_body = heading_match.group(2)
                markup = f"<b>{inline(text_body)}</b>"
                sizes = {
                    1: ("19", 14.0, 8.0),
                    2: ("15.5", 12.0, 6.0),
                    3: ("13", 10.0, 5.0),
                    4: ("11.5", 8.0, 4.0)
                }
                pt_str, m_above, m_below = sizes.get(level, ("10.5", 6.0, 3.0))
                if y_cursor > pad_top and (y_cursor + m_above + 35 > max_y):
                    new_page()
                else:
                    y_cursor += m_above
                render_pango_block(markup, text_body, f"Sans Bold {pt_str}", color=(0.08, 0.08, 0.10), space_below=m_below)
                continue

            if stripped.startswith(">"):
                flush_paragraph()
                q_body = stripped.lstrip(">").strip()
                q_markup = f"<i>{inline(q_body)}</i>"
                layout = PangoCairo.create_layout(cr)
                layout.set_font_description(Pango.FontDescription.from_string("Sans Italic 10.5"))
                layout.set_width(int(max(10.0, avail_w - 20.0) * Pango.SCALE))
                layout.set_wrap(Pango.WrapMode.WORD_CHAR)
                try:
                    layout.set_markup(q_markup, -1)
                except Exception:
                    layout.set_text(q_body, -1)
                _, logical = layout.get_pixel_extents()
                block_h = logical.height + 4.0
                if y_cursor > pad_top and (y_cursor + block_h > max_y):
                    new_page()
                start_y = y_cursor
                cr.save()
                cr.set_source_rgb(0.21, 0.52, 0.89)
                cr.set_line_width(3.0)
                cr.move_to(pad_x + 2.0, start_y)
                cr.line_to(pad_x + 2.0, start_y + block_h)
                cr.stroke()
                cr.set_source_rgb(0.25, 0.28, 0.32)
                cr.move_to(pad_x + 14.0, start_y + 2.0)
                PangoCairo.show_layout(cr, layout)
                cr.restore()
                y_cursor += block_h + 8.0
                continue

            ul_match = re.match(r'^(\s*)([-*+])\s+(.*)$', raw_line)
            if ul_match:
                flush_paragraph()
                indent = min(4, len(ul_match.group(1)) // 2)
                item_text = ul_match.group(3)
                bullet = "•"
                if item_text.startswith("[ ] "):
                    bullet = "☐"
                    item_text = item_text[4:]
                elif item_text.lower().startswith("[x] "):
                    bullet = "☑"
                    item_text = item_text[4:]
                markup = f"<b>{bullet}</b>  {inline(item_text)}"
                render_pango_block(markup, f"{bullet} {item_text}", "Sans 11", x_off=indent * 18.0, space_below=5.0)
                continue

            ol_match = re.match(r'^(\s*)(\d+\.)\s+(.*)$', raw_line)
            if ol_match:
                flush_paragraph()
                indent = min(4, len(ol_match.group(1)) // 2)
                num = ol_match.group(2)
                item_text = ol_match.group(3)
                markup = f"<b>{html.escape(num)}</b>  {inline(item_text)}"
                render_pango_block(markup, f"{num} {item_text}", "Sans 11", x_off=indent * 18.0, space_below=5.0)
                continue

            paragraph_lines.append(stripped)

        flush_paragraph()

        if y_cursor <= pad_top + 1.0:
            render_pango_block("<i>Empty Note</i>", "Empty Note", "Sans Italic 11", color=(0.55, 0.55, 0.58))

        cr.show_page()
        surface.finish()
        del inline
