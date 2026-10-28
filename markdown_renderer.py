import re
import html
import cairo
import math
import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Pango', '1.0')
gi.require_version('PangoCairo', '1.0')
from gi.repository import Gtk, Pango, PangoCairo


class MarkdownInlineStyler:
    """
    Live syntax highlighter and inline Markdown renderer for Gtk.TextView.
    Applies Pango styling tags across the Gtk.TextBuffer and hides raw Markdown
    delimiters (**, *, ~~, `, #, [text](url)) on lines where the cursor is not
    currently positioned.
    """

    def __init__(self, text_view: Gtk.TextView):
        self.text_view = text_view
        self.buffer = text_view.get_buffer()
        self.inline_render = True
        self.zoom_level = 1.0
        self._updating = False
        self._last_cursor_line = -1

        self._setup_tags()

        self.buffer.connect("changed", self._on_buffer_changed)
        self.buffer.connect("mark-set", self._on_mark_set)

    def _setup_tags(self):
        table = self.buffer.get_tag_table()

        def ensure_tag(name, **kwargs):
            tag = table.lookup(name)
            if not tag:
                tag = self.buffer.create_tag(name, **kwargs)
            return tag

        # Delimiter / syntax token tags
        ensure_tag("md-syntax", foreground="#6e7781", weight=Pango.Weight.BOLD)
        ensure_tag("md-conceal", invisible=True)

        # Headings
        ensure_tag("md-h1", weight=Pango.Weight.BOLD, scale=1.18, foreground="#24292f", pixels_above_lines=10, pixels_below_lines=6)
        ensure_tag("md-h2", weight=Pango.Weight.BOLD, scale=1.12, foreground="#24292f", pixels_above_lines=8, pixels_below_lines=4)
        ensure_tag("md-h3", weight=Pango.Weight.BOLD, scale=1.06, foreground="#24292f", pixels_above_lines=6, pixels_below_lines=3)
        ensure_tag("md-h4", weight=Pango.Weight.BOLD, scale=1.02, foreground="#24292f", pixels_above_lines=4, pixels_below_lines=2)

        # Inline formatting
        ensure_tag("md-bold", weight=Pango.Weight.BOLD, foreground="#24292f")
        ensure_tag("md-italic", style=Pango.Style.ITALIC)
        ensure_tag("md-bold-italic", weight=Pango.Weight.BOLD, style=Pango.Style.ITALIC)
        ensure_tag("md-strike", strikethrough=True, foreground="#77767b")

        # Code & preformatted blocks
        ensure_tag(
            "md-inline-code",
            family="Monospace",
            background="#eaeef2",
            foreground="#c01c28",
        )
        ensure_tag(
            "md-code-block",
            family="Monospace",
            background="#f4f4f6",
            paragraph_background="#f4f4f6",
            foreground="#1e1e1e",
            indent=12,
        )
        ensure_tag(
            "md-code-keyword",
            foreground="#813d9c",
            weight=Pango.Weight.BOLD,
        )
        ensure_tag(
            "md-code-string",
            foreground="#26a269",
        )
        ensure_tag(
            "md-code-comment",
            foreground="#77767b",
            style=Pango.Style.ITALIC,
        )

        # Blockquote & Lists & HR
        ensure_tag(
            "md-quote",
            style=Pango.Style.ITALIC,
            foreground="#57606a",
            paragraph_background="#3584e410",
            indent=16,
        )
        ensure_tag("md-list-marker", foreground="#57606a")
        ensure_tag("md-task-done", foreground="#26a269", weight=Pango.Weight.BOLD)
        ensure_tag("md-hr", foreground="#9a9996", weight=Pango.Weight.BOLD)

        # Links
        ensure_tag("md-link-label", foreground="#1c71d8", underline=Pango.Underline.SINGLE)
        ensure_tag("md-link-url", foreground="#8a8594", scale=0.88)

        # Search in page highlights
        ensure_tag("md-search-match", background="#f9f06b", foreground="#1e1e1e")
        ensure_tag("md-search-current", background="#ff7800", foreground="#ffffff", weight=Pango.Weight.BOLD)

        self.managed_tags = [
            "md-syntax", "md-conceal",
            "md-h1", "md-h2", "md-h3", "md-h4",
            "md-bold", "md-italic", "md-bold-italic", "md-strike",
            "md-inline-code", "md-code-block",
            "md-code-keyword", "md-code-string", "md-code-comment",
            "md-quote", "md-list-marker", "md-task-done", "md-hr",
            "md-link-label", "md-link-url",
        ]

    def _get_cursor_line(self) -> int:
        insert_mark = self.buffer.get_insert()
        it = self.buffer.get_iter_at_mark(insert_mark)
        return it.get_line()

    def _on_buffer_changed(self, _buffer):
        if not self._updating:
            self.refresh()

    def _on_mark_set(self, _buffer, location, mark):
        if self._updating or mark != self.buffer.get_insert():
            return
        new_line = location.get_line()
        if new_line != self._last_cursor_line:
            self._last_cursor_line = new_line
            self.refresh()

    def _apply_tag(self, name: str, start_char: int, end_char: int):
        if start_char >= end_char:
            return
        s_iter = self.buffer.get_iter_at_offset(start_char)
        e_iter = self.buffer.get_iter_at_offset(end_char)
        self.buffer.apply_tag_by_name(name, s_iter, e_iter)

    def _style_token(self, start_char: int, end_char: int, is_active_line: bool):
        self._apply_tag("md-syntax", start_char, end_char)
        if self.inline_render and not is_active_line:
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

    def refresh(self):
        if self._updating:
            return
        self._updating = True
        try:
            start_iter, end_iter = self.buffer.get_bounds()
            for tag_name in self.managed_tags:
                self.buffer.remove_tag_by_name(tag_name, start_iter, end_iter)

            full_text = self.buffer.get_text(start_iter, end_iter, True)
            cursor_line = self._get_cursor_line()
            self._last_cursor_line = cursor_line

            lines = full_text.split("\n")
            in_code_block = False
            offset = 0

            for line_idx, line in enumerate(lines):
                line_len = len(line)
                line_start = offset
                line_end = offset + line_len
                is_active = (line_idx == cursor_line)
                stripped = line.strip()

                if stripped.startswith("```"):
                    self._apply_tag("md-code-block", line_start, line_end)
                    self._style_token(line_start, line_end, is_active)
                    in_code_block = not in_code_block
                    offset += line_len + 1
                    continue

                if in_code_block:
                    self._apply_tag("md-code-block", line_start, line_end)
                    self._highlight_code_line(line, line_start)
                    offset += line_len + 1
                    continue

                if stripped in ("---", "***", "___"):
                    self._apply_tag("md-hr", line_start, line_end)
                    offset += line_len + 1
                    continue

                h_match = re.match(r'^(\s*)(#{1,6})(\s+)(.*)$', line)
                if h_match:
                    level = min(4, len(h_match.group(2)))
                    prefix_end = line_start + len(h_match.group(1)) + len(h_match.group(2)) + len(h_match.group(3))
                    self._apply_tag(f"md-h{level}", line_start, line_end)
                    # Apply syntax coloring to the # symbols without concealing them
                    self._apply_tag("md-syntax", line_start, prefix_end)

                q_match = re.match(r'^(\s*>\s?)(.*)$', line)
                if q_match:
                    prefix_len = len(q_match.group(1))
                    self._apply_tag("md-quote", line_start, line_end)
                    self._style_token(line_start, line_start + prefix_len, is_active)

                task_match = re.match(r'^(\s*[-*+]\s+\[([ xX])\]\s+)', line)
                if task_match:
                    marker_len = len(task_match.group(1))
                    is_checked = task_match.group(2).lower() == 'x'
                    tag = "md-task-done" if is_checked else "md-list-marker"
                    self._apply_tag(tag, line_start, line_start + marker_len)
                    if is_checked:
                        self._apply_tag("md-strike", line_start + marker_len, line_end)
                else:
                    ul_match = re.match(r'^(\s*(?:[-*+]|\d+\.)\s+)', line)
                    if ul_match:
                        self._apply_tag("md-list-marker", line_start, line_start + len(ul_match.group(1)))

                code_ranges = []
                for m in re.finditer(r'(`+)([^`]+?)\1', line):
                    s = line_start + m.start()
                    e = line_start + m.end()
                    d_len = len(m.group(1))
                    code_ranges.append((m.start(), m.end()))
                    self._apply_tag("md-inline-code", s, e)
                    self._style_token(s, s + d_len, is_active)
                    self._style_token(e - d_len, e, is_active)

                def in_code_span(pos_start, pos_end):
                    for cs, ce in code_ranges:
                        if pos_start < ce and pos_end > cs:
                            return True
                    return False

                for m in re.finditer(r'(\*\*\*|___)(.+?)\1', line):
                    if in_code_span(m.start(), m.end()):
                        continue
                    s, e = line_start + m.start(), line_start + m.end()
                    self._apply_tag("md-bold-italic", s, e)
                    self._style_token(s, s + 3, is_active)
                    self._style_token(e - 3, e, is_active)

                for m in re.finditer(r'(?<!\*)(\*\*)(?!\*)(.+?)(?<!\*)\1(?!\*)|(?<!_)(__)(?!_)(.+?)(?<!_)\3(?!_)', line):
                    if in_code_span(m.start(), m.end()):
                        continue
                    s, e = line_start + m.start(), line_start + m.end()
                    self._apply_tag("md-bold", s, e)
                    self._style_token(s, s + 2, is_active)
                    self._style_token(e - 2, e, is_active)

                for m in re.finditer(r'(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)|(?<!_)_(?!_)(.+?)(?<!_)_(?!_)', line):
                    if in_code_span(m.start(), m.end()):
                        continue
                    s, e = line_start + m.start(), line_start + m.end()
                    self._apply_tag("md-italic", s, e)
                    self._style_token(s, s + 1, is_active)
                    self._style_token(e - 1, e, is_active)

                for m in re.finditer(r'(~~)(.+?)\1', line):
                    if in_code_span(m.start(), m.end()):
                        continue
                    s, e = line_start + m.start(), line_start + m.end()
                    self._apply_tag("md-strike", s, e)
                    self._style_token(s, s + 2, is_active)
                    self._style_token(e - 2, e, is_active)

                for m in re.finditer(r'(\[)([^\]]+)(\]\()([^\)]+)(\))', line):
                    if in_code_span(m.start(), m.end()):
                        continue
                    s = line_start + m.start()
                    lbl_start = s + 1
                    lbl_end = lbl_start + len(m.group(2))
                    url_start = lbl_end + 2
                    url_end = url_start + len(m.group(4))
                    e = url_end + 1

                    self._style_token(s, lbl_start, is_active)
                    self._apply_tag("md-link-label", lbl_start, lbl_end)
                    self._style_token(lbl_end, e, is_active)
                    self._apply_tag("md-link-url", url_start, url_end)

                offset += line_len + 1
        finally:
            self._updating = False


class MarkdownRenderer:
    """Helper to convert Markdown text into styled GTK4 widgets and rich Cairo thumbnails."""

    @staticmethod
    def inline_to_pango(text):
        escaped = html.escape(text, quote=False)

        code_spans = []
        def save_code(match):
            idx = len(code_spans)
            code_spans.append(
                f'<tt><span background="#e8e8e8" foreground="#1f1f1f"> {match.group(1)} </span></tt>'
            )
            return f"\x00CODESPAN{idx}\x00"

        escaped = re.sub(r'`([^`]+)`', save_code, escaped)
        escaped = re.sub(
            r'\[([^\]]+)\]\(([^)]+)\)',
            r'<span foreground="#1c71d8" underline="single">\1</span>',
            escaped
        )
        escaped = re.sub(r'\*\*\*(.+?)\*\*\*', r'<b><i>\1</i></b>', escaped)
        escaped = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', escaped)
        escaped = re.sub(r'__(.+?)__', r'<b>\1</b>', escaped)
        escaped = re.sub(r'(?<!\*)\*([^*]+)\*(?!\*)', r'<i>\1</i>', escaped)
        escaped = re.sub(r'(?<!_)_([^_]+)_(?!_)', r'<i>\1</i>', escaped)
        escaped = re.sub(r'~~(.+?)~~', r'<s>\1</s>', escaped)

        for idx, span in enumerate(code_spans):
            escaped = escaped.replace(f"\x00CODESPAN{idx}\x00", span)

        return escaped

    @classmethod
    def populate_box(cls, container, md_text, zoom_level=1.0):
        child = container.get_first_child()
        while child is not None:
            next_child = child.get_next_sibling()
            container.remove(child)
            child = next_child

        lines = md_text.splitlines()
        in_code_block = False
        code_lines = []
        paragraph_lines = []

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
            markup = cls.inline_to_pango(joined)
            pt = max(8, int(11.5 * zoom_level))
            try:
                lbl.set_markup(f'<span size="{pt * 1024}" foreground="#2e3436">{markup}</span>')
            except Exception:
                lbl.set_text(joined)
            lbl.set_margin_bottom(int(10 * zoom_level))
            container.append(lbl)

        for raw_line in lines:
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

            heading_match = re.match(r'^(#{1,6})\s+(.*)$', stripped)
            if heading_match:
                flush_paragraph()
                level = len(heading_match.group(1))
                content = cls.inline_to_pango(heading_match.group(2))
                scales = {1: 22, 2: 17, 3: 14, 4: 12.5, 5: 11.5, 6: 11}
                pt = max(9, int(scales.get(level, 12) * zoom_level))

                hbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=int(6 * zoom_level))
                hbox.set_margin_top(int(8 * zoom_level))
                hbox.set_margin_bottom(int(14 * zoom_level))

                lbl = Gtk.Label(
                    xalign=0.0,
                    wrap=True,
                    wrap_mode=Pango.WrapMode.WORD_CHAR,
                    selectable=True
                )
                try:
                    lbl.set_markup(
                        f'<span size="{pt * 1024}" weight="bold" foreground="#2e3436">{content}</span>'
                    )
                except Exception:
                    lbl.set_text(heading_match.group(2))
                hbox.append(lbl)

                if level in (1, 2):
                    h_sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
                    h_sep.add_css_class("md-heading-rule")
                    hbox.append(h_sep)

                container.append(hbox)
                continue

            if stripped.startswith(">"):
                flush_paragraph()
                quote_content = cls.inline_to_pango(stripped.lstrip(">").strip())
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
                        f'<span size="{pt * 1024}" style="italic" alpha="85%">{quote_content}</span>'
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
                b_lbl.set_markup(f'<span size="{pt * 1024}" foreground="#2e3436"><b>{bullet}</b></span>')
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
                        f'<span size="{pt * 1024}" foreground="#2e3436">{cls.inline_to_pango(item_text)}</span>'
                    )
                except Exception:
                    t_lbl.set_text(item_text)
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
                n_lbl.set_markup(f'<span size="{pt * 1024}" foreground="#2e3436"><b>{num}</b></span>')
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
                        f'<span size="{pt * 1024}" foreground="#2e3436">{cls.inline_to_pango(item_text)}</span>'
                    )
                except Exception:
                    t_lbl.set_text(item_text)
                row.append(t_lbl)
                container.append(row)
                continue

            paragraph_lines.append(stripped)

        flush_paragraph()

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
            render_pango_block(cls.inline_to_pango(joined), joined, "Sans 6.5")

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
                markup = f"<b>{cls.inline_to_pango(text_body)}</b>"
                sizes = {1: "9.5", 2: "8.2", 3: "7.4", 4: "6.8", 5: "6.5", 6: "6.2"}
                pt_str = sizes.get(level, "6.8")
                y_cursor += 1.5
                render_pango_block(markup, text_body, f"Sans Bold {pt_str}", color=(0.08, 0.08, 0.10))
                continue

            if stripped.startswith(">"):
                flush_paragraph()
                q_body = stripped.lstrip(">").strip()
                q_markup = f"<i>{cls.inline_to_pango(q_body)}</i>"
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
                markup = f"<b>{bullet}</b> {cls.inline_to_pango(item_text)}"
                render_pango_block(markup, f"{bullet} {item_text}", "Sans 6.3", x_off=indent * 6.0)
                continue

            ol_match = re.match(r'^(\s*)(\d+\.)\s+(.*)$', raw_line)
            if ol_match:
                flush_paragraph()
                indent = min(3, len(ol_match.group(1)) // 2)
                num = ol_match.group(2)
                item_text = ol_match.group(3)
                markup = f"<b>{html.escape(num)}</b> {cls.inline_to_pango(item_text)}"
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
