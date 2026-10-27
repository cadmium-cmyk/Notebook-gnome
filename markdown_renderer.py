import re
import html
import cairo
import math
import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Pango', '1.0')
gi.require_version('PangoCairo', '1.0')
from gi.repository import Gtk, Pango, PangoCairo


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
            return f"@@CODESPAN_{idx}@@"

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
            escaped = escaped.replace(f"@@CODESPAN_{idx}@@", span)

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
            pt = max(8, int(12 * zoom_level))
            try:
                lbl.set_markup(f'<span size="{pt * 1024}">{markup}</span>')
            except Exception:
                lbl.set_text(joined)
            lbl.set_margin_bottom(int(8 * zoom_level))
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
                sep.set_margin_top(int(10 * zoom_level))
                sep.set_margin_bottom(int(10 * zoom_level))
                container.append(sep)
                continue

            heading_match = re.match(r'^(#{1,6})\s+(.*)$', stripped)
            if heading_match:
                flush_paragraph()
                level = len(heading_match.group(1))
                content = cls.inline_to_pango(heading_match.group(2))
                scales = {1: 22, 2: 18, 3: 15, 4: 13, 5: 12, 6: 11}
                pt = max(9, int(scales.get(level, 12) * zoom_level))
                lbl = Gtk.Label(
                    xalign=0.0,
                    wrap=True,
                    wrap_mode=Pango.WrapMode.WORD_CHAR,
                    selectable=True
                )
                try:
                    lbl.set_markup(f'<span size="{pt * 1024}" weight="bold">{content}</span>')
                except Exception:
                    lbl.set_text(heading_match.group(2))
                lbl.set_margin_top(int(10 * zoom_level))
                lbl.set_margin_bottom(int(6 * zoom_level))
                container.append(lbl)
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

                row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
                row.set_margin_start(int((12 + indent * 16) * zoom_level))
                row.set_margin_bottom(int(4 * zoom_level))

                pt = max(8, int(12 * zoom_level))
                b_lbl = Gtk.Label(valign=Gtk.Align.START)
                b_lbl.set_markup(f'<span size="{pt * 1024}"><b>{bullet}</b></span>')
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
                        f'<span size="{pt * 1024}">{cls.inline_to_pango(item_text)}</span>'
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

                row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
                row.set_margin_start(int((12 + indent * 16) * zoom_level))
                row.set_margin_bottom(int(4 * zoom_level))

                pt = max(8, int(12 * zoom_level))
                n_lbl = Gtk.Label(valign=Gtk.Align.START)
                n_lbl.set_markup(f'<span size="{pt * 1024}"><b>{num}</b></span>')
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
                        f'<span size="{pt * 1024}">{cls.inline_to_pango(item_text)}</span>'
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

            # Fenced code block
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

            # Horizontal Rule
            if stripped in ("---", "***", "___"):
                flush_paragraph()
                cr.set_source_rgba(0, 0, 0, 0.18)
                cr.set_line_width(1.0)
                cr.move_to(pad_x, y_cursor + 2.0)
                cr.line_to(pad_x + avail_w, y_cursor + 2.0)
                cr.stroke()
                y_cursor += 6.0
                continue

            # Heading
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

            # Blockquote
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

            # Unordered or Task List
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

            # Ordered List
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

        # If note was completely empty, show subtle placeholder
        if y_cursor <= 13.0:
            render_pango_block("<i>Empty Note</i>", "Empty Note", "Sans Italic 7", color=(0.55, 0.55, 0.58))

        # Subtle bottom fade if content overflows thumbnail height
        if y_cursor >= max_y:
            grad = cairo.LinearGradient(0, height - 24, 0, height)
            grad.add_color_stop_rgba(0.0, 1.0, 1.0, 1.0, 0.0)
            grad.add_color_stop_rgba(1.0, 1.0, 1.0, 1.0, 0.95)
            cr.set_source(grad)
            cr.rectangle(0, height - 24, width, 24)
            cr.fill()

        # Card border
        cr.set_source_rgba(0, 0, 0, 0.14)
        cr.set_line_width(2.0)
        cls._rounded_rect(cr, 0, 0, width, height, radius)
        cr.stroke()
        cr.restore()
