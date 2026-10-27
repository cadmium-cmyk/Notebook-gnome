import os
import re
import math
import copy
import cairo
import gi

gi.require_version('Pango', '1.0')
gi.require_version('PangoCairo', '1.0')
from gi.repository import Pango, PangoCairo

from xnote_io import XTemplateEvaluator, XNotePackageParser


class CanvasRendererMixin:
    """Mixin class for XNoteCanvas providing page, stroke, text, image, and xtemplate rendering methods."""

    @staticmethod
    def smooth_samples_chaikin(samples, iterations=1):
        """
        Smooth a sequence of [x, y, pressure] or [x, y, pressure, dt_ms] samples
        while preserving any 4th delta-time element.
        """
        if not samples or len(samples) < 3:
            return samples

        pts = []
        for s in samples:
            if isinstance(s, dict):
                pts.append([
                    float(s.get("x", 0.0)),
                    float(s.get("y", 0.0)),
                    float(s.get("pressure", 0.5)),
                    float(s.get("dt", 0.0))
                ])
            else:
                px = float(s[0]) if len(s) > 0 else 0.0
                py = float(s[1]) if len(s) > 1 else 0.0
                pr = float(s[2]) if len(s) > 2 else 0.5
                dt = float(s[3]) if len(s) > 3 else None
                pts.append([px, py, pr] if dt is None else [px, py, pr, dt])

        for _ in range(iterations):
            if len(pts) < 3 or len(pts) > 900:
                break
            has_dt = len(pts[0]) >= 4
            new_pts = [pts[0]]
            for i in range(len(pts) - 1):
                p0 = pts[i]
                p1 = pts[i + 1]
                qx = round(0.75 * p0[0] + 0.25 * p1[0], 2)
                qy = round(0.75 * p0[1] + 0.25 * p1[1], 2)
                qp = round(0.75 * p0[2] + 0.25 * p1[2], 3)

                rx = round(0.25 * p0[0] + 0.75 * p1[0], 2)
                ry = round(0.25 * p0[1] + 0.75 * p1[1], 2)
                rp = round(0.25 * p0[2] + 0.75 * p1[2], 3)

                if has_dt:
                    dt1 = p1[3] if len(p1) >= 4 else 8.0
                    half_dt = max(1, int(round(dt1 * 0.5)))
                    new_pts.append([qx, qy, qp, half_dt])
                    new_pts.append([rx, ry, rp, half_dt])
                else:
                    new_pts.append([qx, qy, qp])
                    new_pts.append([rx, ry, rp])
            new_pts.append(pts[-1])
            pts = new_pts

        return pts

    def _rounded_rect(self, cr, x, y, w, h, r):
        r = max(0.0, min(r, min(w, h) / 2.0))
        if r <= 1e-3:
            cr.rectangle(x, y, w, h)
            return
        cr.new_sub_path()
        cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
        cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
        cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
        cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
        cr.close_path()

    @classmethod
    def _eval_xtemplate_expr(cls, expr, dpi, total, box_w, box_h, params):
        """Evaluate xtemplate v1 arithmetic/unit expressions including W, H, min(), max()."""
        if expr is None:
            return 0.0
        if isinstance(expr, (int, float)):
            return float(expr)
        if not isinstance(expr, str):
            return 0.0

        s = expr.strip()
        if not s:
            return 0.0

        # Parameter name lookup
        if params and s in params:
            pval = params[s]
            if isinstance(pval, dict):
                pval = pval.get("value", pval.get("default", 0.0))
            return cls._eval_xtemplate_expr(pval, dpi, total, box_w, box_h, params)

        # Direct physical unit or percentage
        if s.endswith('%') or re.match(r'^[+-]?\d+(?:\.\d+)?\s*(mm|cm|in|pt|px)?$', s, re.IGNORECASE):
            return XTemplateEvaluator.eval_length(s, dpi, total, params)

        # Replace parameter identifiers and physical units inside formula expressions
        def replace_unit(m):
            val = float(m.group(1))
            unit = m.group(2).lower()
            if unit == 'mm':
                return str(val * (dpi / 25.4))
            elif unit == 'cm':
                return str(val * (dpi / 2.54))
            elif unit == 'in':
                return str(val * dpi)
            elif unit == 'pt':
                return str(val * (dpi / 72.0))
            elif unit == '%':
                return str((val / 100.0) * total)
            return str(val)

        s_eval = re.sub(r'(\d+(?:\.\d+)?)\s*(mm|cm|in|pt|px|%)', replace_unit, s)
        env = {"W": float(box_w), "H": float(box_h), "min": min, "max": max, "abs": abs, "round": round}
        if params:
            for k, v in params.items():
                raw_v = v.get("value", v.get("default", 0.0)) if isinstance(v, dict) else v
                if isinstance(raw_v, (int, float)):
                    env[k] = float(raw_v)
                elif isinstance(raw_v, str):
                    env[k] = XTemplateEvaluator.eval_length(raw_v, dpi, total, None)
        try:
            return float(eval(s_eval, {"__builtins__": {}}, env))
        except Exception:
            return XTemplateEvaluator.eval_length(s, dpi, total, params)

    @classmethod
    def _resolve_xtemplate_color(cls, col_spec, params, pattern_rgba, accent_rgba):
        if not col_spec or col_spec == "none":
            return None
        if col_spec == "accent":
            return accent_rgba
        if col_spec in ("primary", "pattern"):
            return pattern_rgba
        if params and col_spec in params:
            p = params[col_spec]
            if isinstance(p, dict):
                col_spec = p.get("value", p.get("default", col_spec))
            else:
                col_spec = p
        if isinstance(col_spec, (list, tuple)) and len(col_spec) >= 3:
            a = col_spec[3] / 255.0 if len(col_spec) >= 4 else 1.0
            return (col_spec[0] / 255.0, col_spec[1] / 255.0, col_spec[2] / 255.0, a)
        if isinstance(col_spec, str):
            hx = col_spec.strip().lstrip('#')
            if len(hx) == 6:
                return (int(hx[0:2], 16) / 255.0, int(hx[2:4], 16) / 255.0, int(hx[4:6], 16) / 255.0, 1.0)
            elif len(hx) == 8:
                return (
                    int(hx[0:2], 16) / 255.0,
                    int(hx[2:4], 16) / 255.0,
                    int(hx[4:6], 16) / 255.0,
                    int(hx[6:8], 16) / 255.0
                )
        return pattern_rgba

    @staticmethod
    def _draw_svg_path_d(cr, d_str):
        """Render SVG path commands (M, L, H, V, A, Z) used in xtemplate bookplates/shapes."""
        tokens = re.findall(r'[MLHVAZmlhvaz]|[-+]?\d*\.?\d+', d_str)
        idx = 0
        cx, cy = 0.0, 0.0
        sx, sy = 0.0, 0.0
        cr.new_path()

        while idx < len(tokens):
            cmd = tokens[idx]
            idx += 1
            if cmd == 'M':
                cx, cy = float(tokens[idx]), float(tokens[idx + 1])
                sx, sy = cx, cy
                cr.move_to(cx, cy)
                idx += 2
            elif cmd == 'L':
                cx, cy = float(tokens[idx]), float(tokens[idx + 1])
                cr.line_to(cx, cy)
                idx += 2
            elif cmd == 'H':
                cx = float(tokens[idx])
                cr.line_to(cx, cy)
                idx += 1
            elif cmd == 'V':
                cy = float(tokens[idx])
                cr.line_to(cx, cy)
                idx += 1
            elif cmd == 'A':
                # A rx ry x-axis-rotation large-arc-flag sweep-flag x y
                rx = float(tokens[idx])
                ry = float(tokens[idx + 1])
                x_end = float(tokens[idx + 5])
                y_end = float(tokens[idx + 6])
                idx += 7
                # Smoothly approximate quarter/rounded corner arcs with a cubic Bezier
                cpx1 = cx + (x_end - cx) * 0.55
                cpy1 = cy
                cpx2 = x_end
                cpy2 = cy + (y_end - cy) * 0.45
                cr.curve_to(cpx1, cpy1, cpx2, cpy2, x_end, y_end)
                cx, cy = x_end, y_end
            elif cmd in ('Z', 'z'):
                cr.close_path()
                cx, cy = sx, sy

    def _draw_xtemplate_items(self, cr, items, box_x, box_y, box_w, box_h, dpi, params, pattern_rgba, accent_rgba, t_info, assets, templates):
        for item in items:
            if not isinstance(item, dict):
                continue
            itype = item.get("type") or item.get("kind")

            if itype == "image":
                src = item.get("src") or item.get("asset")
                surf = None
                if t_info and src in t_info.get("images", {}):
                    surf = t_info["images"][src]
                elif t_info and os.path.basename(str(src)) in t_info.get("images", {}):
                    surf = t_info["images"][os.path.basename(str(src))]
                elif t_info and t_info.get("surface") is not None:
                    surf = t_info["surface"]
                else:
                    surf = self._resolve_asset_surface(src, assets, templates)

                if surf and surf.get_width() > 0 and surf.get_height() > 0:
                    iw = surf.get_width()
                    ih = surf.get_height()
                    fit = item.get("fit", "cover")
                    cr.save()
                    cr.rectangle(box_x, box_y, box_w, box_h)
                    cr.clip()
                    if fit == "cover":
                        scale = max(box_w / iw, box_h / ih)
                        dx = box_x + (box_w - iw * scale) / 2.0
                        dy = box_y + (box_h - ih * scale) / 2.0
                        cr.translate(dx, dy)
                        cr.scale(scale, scale)
                    elif fit == "contain":
                        scale = min(box_w / iw, box_h / ih)
                        dx = box_x + (box_w - iw * scale) / 2.0
                        dy = box_y + (box_h - ih * scale) / 2.0
                        cr.translate(dx, dy)
                        cr.scale(scale, scale)
                    else:
                        cr.translate(box_x, box_y)
                        cr.scale(box_w / iw, box_h / ih)
                    cr.set_source_surface(surf, 0, 0)
                    cr.paint()
                    cr.restore()

            elif itype == "rect":
                rx_pos = box_x + self._eval_xtemplate_expr(item.get("x", 0), dpi, box_w, box_w, box_h, params)
                ry_pos = box_y + self._eval_xtemplate_expr(item.get("y", 0), dpi, box_h, box_w, box_h, params)
                rw = (
                    self._eval_xtemplate_expr(item["w"], dpi, box_w, box_w, box_h, params)
                    if "w" in item else box_w
                )
                rh = (
                    self._eval_xtemplate_expr(item["h"], dpi, box_h, box_w, box_h, params)
                    if "h" in item else box_h
                )
                corner_r = self._eval_xtemplate_expr(item.get("rx", item.get("ry", 0)), dpi, min(rw, rh), box_w, box_h, params)
                fill_spec = item.get("fill", "none")
                stroke_spec = item.get("stroke", "primary")
                lw = self._eval_xtemplate_expr(item.get("strokeWidth", item.get("width", 1.5)), dpi, box_w, box_w, box_h, params)

                cr.save()
                self._rounded_rect(cr, rx_pos, ry_pos, rw, rh, corner_r)
                if fill_spec and fill_spec != "none":
                    fill_rgba = self._resolve_xtemplate_color(fill_spec, params, pattern_rgba, accent_rgba)
                    if fill_rgba:
                        cr.set_source_rgba(*fill_rgba)
                        if stroke_spec and stroke_spec != "none":
                            cr.fill_preserve()
                        else:
                            cr.fill()
                if stroke_spec and stroke_spec != "none":
                    s_rgba = self._resolve_xtemplate_color(stroke_spec, params, pattern_rgba, accent_rgba)
                    if s_rgba:
                        cr.set_source_rgba(*s_rgba)
                        cr.set_line_width(lw)
                        cr.stroke()
                cr.restore()

            elif itype == "text":
                txt = str(item.get("text", ""))
                if txt:
                    tx = box_x + self._eval_xtemplate_expr(item.get("x", 0), dpi, box_w, box_w, box_h, params)
                    ty = box_y + self._eval_xtemplate_expr(item.get("y", 0), dpi, box_h, box_w, box_h, params)
                    fsize = self._eval_xtemplate_expr(item.get("size", "4.5mm"), dpi, box_h, box_w, box_h, params)
                    weight = item.get("weight", "normal")
                    col_rgba = self._resolve_xtemplate_color(item.get("fill", item.get("color", "primary")), params, pattern_rgba, accent_rgba)

                    layout = PangoCairo.create_layout(cr)
                    layout.set_text(txt, -1)
                    weight_str = "Bold" if str(weight).lower() == "bold" else ""
                    font_desc = Pango.FontDescription.from_string(f"Sans {weight_str} {max(6.0, fsize)}px")
                    layout.set_font_description(font_desc)
                    _, logical = layout.get_pixel_extents()

                    cr.save()
                    if col_rgba:
                        cr.set_source_rgba(*col_rgba)
                    # Position baseline/top naturally around ty
                    cr.move_to(tx, ty - logical.height * 0.75)
                    PangoCairo.show_layout(cr, layout)
                    cr.restore()

            elif itype == "path":
                d_str = item.get("d", "")
                vb = item.get("viewBox", "0 0 100 100")
                vb_parts = [float(v) for v in re.findall(r'[-+]?\d*\.?\d+', str(vb))]
                vb_w = vb_parts[2] if len(vb_parts) >= 4 else box_w
                vb_h = vb_parts[3] if len(vb_parts) >= 4 else box_h
                px_off = self._eval_xtemplate_expr(item.get("x", 0), dpi, box_w, box_w, box_h, params)
                py_off = self._eval_xtemplate_expr(item.get("y", 0), dpi, box_h, box_w, box_h, params)

                if d_str and vb_w > 0 and vb_h > 0:
                    cr.save()
                    cr.translate(box_x + px_off, box_y + py_off)
                    sx = box_w / vb_w
                    sy = box_h / vb_h
                    cr.scale(sx, sy)
                    self._draw_svg_path_d(cr, d_str)

                    fill_spec = item.get("fill")
                    if fill_spec and fill_spec != "none":
                        f_rgba = self._resolve_xtemplate_color(fill_spec, params, pattern_rgba, accent_rgba)
                        if f_rgba:
                            cr.set_source_rgba(*f_rgba)
                            if item.get("stroke") and item.get("stroke") != "none":
                                cr.fill_preserve()
                            else:
                                cr.fill()

                    stroke_spec = item.get("stroke")
                    if stroke_spec and stroke_spec != "none":
                        s_rgba = self._resolve_xtemplate_color(stroke_spec, params, pattern_rgba, accent_rgba)
                        if s_rgba:
                            lw = self._eval_xtemplate_expr(item.get("strokeWidth", 1.5), dpi, box_w, box_w, box_h, params)
                            cr.set_source_rgba(*s_rgba)
                            cr.set_line_width(lw / max(0.1, (sx + sy) * 0.5))
                            cr.stroke()
                    cr.restore()

            elif itype == "group":
                inset = item.get("inset")
                if isinstance(inset, (list, tuple)) and len(inset) >= 4:
                    # [top, right, bottom, left]
                    itop = self._eval_xtemplate_expr(inset[0], dpi, box_h, box_w, box_h, params)
                    iright = self._eval_xtemplate_expr(inset[1], dpi, box_w, box_w, box_h, params)
                    ibottom = self._eval_xtemplate_expr(inset[2], dpi, box_h, box_w, box_h, params)
                    ileft = self._eval_xtemplate_expr(inset[3], dpi, box_w, box_w, box_h, params)
                    gx = box_x + ileft
                    gy = box_y + itop
                    gw = max(0.0, box_w - ileft - iright)
                    gh = max(0.0, box_h - itop - ibottom)
                else:
                    gx = box_x + self._eval_xtemplate_expr(item.get("x", 0), dpi, box_w, box_w, box_h, params)
                    gy = box_y + self._eval_xtemplate_expr(item.get("y", 0), dpi, box_h, box_w, box_h, params)
                    gw = (
                        self._eval_xtemplate_expr(item["w"], dpi, box_w, box_w, box_h, params)
                        if "w" in item else (box_x + box_w - gx)
                    )
                    gh = (
                        self._eval_xtemplate_expr(item["h"], dpi, box_h, box_w, box_h, params)
                        if "h" in item else (box_y + box_h - gy)
                    )

                opacity = 1.0
                if "opacity" in item:
                    opacity = max(0.0, min(1.0, self._eval_xtemplate_expr(item["opacity"], dpi, 1.0, box_w, box_h, params)))

                children = item.get("children", [])
                if children and gw > 0 and gh > 0 and opacity > 0.01:
                    if opacity < 0.99:
                        cr.save()
                        cr.push_group()
                        self._draw_xtemplate_items(
                            cr, children, gx, gy, gw, gh, dpi, params, pattern_rgba, accent_rgba, t_info, assets, templates
                        )
                        cr.pop_group_to_source()
                        cr.paint_with_alpha(opacity)
                        cr.restore()
                    else:
                        self._draw_xtemplate_items(
                            cr, children, gx, gy, gw, gh, dpi, params, pattern_rgba, accent_rgba, t_info, assets, templates
                        )

            elif itype == "repeat":
                axis = item.get("axis", "y").lower()
                total_len = box_h if axis == "y" else box_w
                period = self._eval_xtemplate_expr(item.get("period", "8mm"), dpi, total_len, box_w, box_h, params)
                fit_mode = item.get("fit")
                children = item.get("children", [])
                if period > 0.5 and children:
                    if fit_mode == "round":
                        count = max(1, int(round(total_len / period)))
                        step_period = total_len / count
                        for i in range(count):
                            offset = i * step_period
                            if axis == "y":
                                self._draw_xtemplate_items(
                                    cr, children, box_x, box_y + offset, box_w, step_period,
                                    dpi, params, pattern_rgba, accent_rgba, t_info, assets, templates
                                )
                            else:
                                self._draw_xtemplate_items(
                                    cr, children, box_x + offset, box_y, step_period, box_h,
                                    dpi, params, pattern_rgba, accent_rgba, t_info, assets, templates
                                )
                    else:
                        offset = 0.0
                        while offset <= total_len + 0.5:
                            if axis == "y":
                                self._draw_xtemplate_items(
                                    cr, children, box_x, box_y + offset, box_w, min(period, box_h - offset),
                                    dpi, params, pattern_rgba, accent_rgba, t_info, assets, templates
                                )
                            else:
                                self._draw_xtemplate_items(
                                    cr, children, box_x + offset, box_y, min(period, box_w - offset), box_h,
                                    dpi, params, pattern_rgba, accent_rgba, t_info, assets, templates
                                )
                            offset += period

            elif itype == "hline":
                ly = box_y + self._eval_xtemplate_expr(item.get("y", 0), dpi, box_h, box_w, box_h, params)
                x1 = box_x + self._eval_xtemplate_expr(item.get("x1", 0), dpi, box_w, box_w, box_h, params)
                x2 = (
                    box_x + self._eval_xtemplate_expr(item["x2"], dpi, box_w, box_w, box_h, params)
                    if "x2" in item else (box_x + box_w)
                )
                stroke_role = item.get("stroke", "primary")
                rgba = self._resolve_xtemplate_color(stroke_role, params, pattern_rgba, accent_rgba)
                lw = float(item.get("width", 1.25))
                if rgba:
                    cr.save()
                    cr.set_source_rgba(*rgba)
                    cr.set_line_width(lw)
                    cr.move_to(x1, ly)
                    cr.line_to(x2, ly)
                    cr.stroke()
                    cr.restore()

            elif itype == "vline":
                lx = box_x + self._eval_xtemplate_expr(item.get("x", 0), dpi, box_w, box_w, box_h, params)
                y1 = box_y + self._eval_xtemplate_expr(item.get("y1", 0), dpi, box_h, box_w, box_h, params)
                y2 = (
                    box_y + self._eval_xtemplate_expr(item["y2"], dpi, box_h, box_w, box_h, params)
                    if "y2" in item else (box_y + box_h)
                )
                stroke_role = item.get("stroke", "primary")
                rgba = self._resolve_xtemplate_color(stroke_role, params, pattern_rgba, accent_rgba)
                lw = float(item.get("width", 1.5))
                if rgba:
                    cr.save()
                    cr.set_source_rgba(*rgba)
                    cr.set_line_width(lw)
                    cr.move_to(lx, y1)
                    cr.line_to(lx, y2)
                    cr.stroke()
                    cr.restore()

    def render_page_contents(self, cr, doc, page, pw, ph, assets, templates, page_idx=0):
        dpi = float(doc.get("dpi", 150.0))
        doc_style = doc.get("style", {}) if isinstance(doc.get("style"), dict) else {}
        style = page.get("style", {}) if isinstance(page.get("style"), dict) else {}

        bg = style.get("page_color", doc_style.get("page_color", [255, 255, 255, 255]))
        cr.set_source_rgba(bg[0] / 255.0, bg[1] / 255.0, bg[2] / 255.0, bg[3] / 255.0)
        cr.rectangle(0, 0, pw, ph)
        cr.fill()

        pdf_page_num = page.get("pdf_page")
        if pdf_page_num is not None and isinstance(pdf_page_num, int) and pdf_page_num >= 0:
            poppler_doc = doc.get("_poppler_doc")
            if poppler_doc is not None and pdf_page_num < poppler_doc.get_n_pages():
                p_page = poppler_doc.get_page(pdf_page_num)
                pw_pt, ph_pt = p_page.get_size()
                if pw_pt > 0 and ph_pt > 0:
                    cr.save()
                    cr.scale(pw / pw_pt, ph / ph_pt)
                    p_page.render(cr)
                    cr.restore()
            elif doc.get("_pdf_surfaces") and pdf_page_num < len(doc["_pdf_surfaces"]):
                surf = doc["_pdf_surfaces"][pdf_page_num]
                if surf and surf.get_width() > 0 and surf.get_height() > 0:
                    cr.save()
                    cr.scale(pw / surf.get_width(), ph / surf.get_height())
                    cr.set_source_surface(surf, 0, 0)
                    cr.paint()
                    cr.restore()

        is_cover_page = bool(page.get("cover", False))
        t_ref = (
            style.get("template")
            or style.get("pattern")
            or page.get("template")
        )
        if not t_ref and not is_cover_page and pdf_page_num is None:
            t_ref = doc_style.get("template") or doc_style.get("pattern")

        t_info = XNotePackageParser.resolve_template(t_ref, templates) if (t_ref and t_ref != "none") else None
        if t_info is not None and t_info.get("is_cover") and not is_cover_page and page_idx > 0:
            t_info = XNotePackageParser.resolve_template(doc_style.get("template"), templates)

        pattern_color = style.get("pattern_color", doc_style.get("pattern_color", [0, 56, 183, 95]))
        pattern_rgba = (
            pattern_color[0] / 255.0,
            pattern_color[1] / 255.0,
            pattern_color[2] / 255.0,
            pattern_color[3] / 255.0
        )
        accent_color = style.get("accent_color", doc_style.get("accent_color", [215, 45, 75, max(95, pattern_color[3])]))
        accent_rgba = (
            accent_color[0] / 255.0,
            accent_color[1] / 255.0,
            accent_color[2] / 255.0,
            accent_color[3] / 255.0
        )

        if t_info is not None:
            t_meta = t_info.get("meta", {}) if isinstance(t_info.get("meta"), dict) else {}
            t_items = t_meta.get("items", [])
            t_params = copy.deepcopy(t_meta.get("params", {}))
            for k, v in doc_style.items():
                if k in t_params:
                    t_params[k] = v
                elif k == "spacing":
                    for pk, pv in list(t_params.items()):
                        if isinstance(pv, dict) and pv.get("role") == "spacing":
                            t_params[pk] = v
            for k, v in style.items():
                if k in t_params:
                    t_params[k] = v
                elif k == "spacing":
                    for pk, pv in list(t_params.items()):
                        if isinstance(pv, dict) and pv.get("role") == "spacing":
                            t_params[pk] = v

            if t_items:
                self._draw_xtemplate_items(
                    cr, t_items, 0.0, 0.0, pw, ph, dpi, t_params,
                    pattern_rgba, accent_rgba, t_info, assets, templates
                )
            elif t_info.get("surface") is not None:
                surf = t_info["surface"]
                cr.save()
                cr.scale(pw / surf.get_width(), ph / surf.get_height())
                cr.set_source_surface(surf, 0, 0)
                cr.paint()
                cr.restore()

        elif t_ref in ("lines", "dots", "grid"):
            spacing = float(style.get("spacing", doc_style.get("spacing", 64.0)))
            cr.set_source_rgba(*pattern_rgba)
            cr.set_line_width(1.5)

            if t_ref == "lines":
                y = spacing
                while y < ph:
                    cr.move_to(0, y)
                    cr.line_to(pw, y)
                    y += spacing
                cr.stroke()
            elif t_ref == "dots":
                y = spacing
                while y < ph:
                    x = spacing
                    while x < pw:
                        cr.arc(x, y, 2.0, 0, 2 * math.pi)
                        cr.fill()
                        x += spacing
                    y += spacing
            elif t_ref == "grid":
                y = spacing
                while y < ph:
                    cr.move_to(0, y)
                    cr.line_to(pw, y)
                    y += spacing
                x = spacing
                while x < pw:
                    cr.move_to(x, 0)
                    cr.line_to(x, ph)
                    x += spacing
                cr.stroke()

        flow_info = doc.get("_flow")
        if flow_info and flow_info.get("paragraphs") and not is_cover_page and page_idx <= 1:
            ml = XTemplateEvaluator.eval_length(f"{flow_info['margin_left_mm']}mm", dpi, pw)
            mt = XTemplateEvaluator.eval_length(f"{flow_info['margin_top_mm']}mm", dpi, ph)
            mr = XTemplateEvaluator.eval_length(f"{flow_info['margin_right_mm']}mm", dpi, pw)
            font_face = flow_info.get("default_face", "sans")
            font_pt = flow_info.get("default_size_pt", 13.0) * (dpi / 72.0)

            flow_text = "\n\n".join(flow_info["paragraphs"])
            layout = PangoCairo.create_layout(cr)
            layout.set_text(flow_text, -1)
            layout.set_font_description(Pango.FontDescription.from_string(f"{font_face} {font_pt}px"))
            layout.set_width(int(max(100.0, pw - ml - mr) * Pango.SCALE))
            layout.set_wrap(Pango.WrapMode.WORD_CHAR)
            cr.set_source_rgba(0.12, 0.12, 0.14, 1.0)
            cr.move_to(ml, mt)
            PangoCairo.show_layout(cr, layout)

        for item in page.get("items", []):
            if not isinstance(item, dict):
                continue
            kind = item.get("kind") or item.get("type")
            if kind == "stroke":
                self.draw_stroke(cr, item)
            elif kind == "text":
                self.draw_text(cr, item)
            elif kind in ("image", "picture"):
                self.draw_image(cr, item, assets, templates)

    def draw_page_thumb(self, cr, width, height, doc, page, pw, ph, assets=None, templates=None, page_idx=0):
        assets = assets if assets is not None else self.assets
        templates = templates if templates is not None else self.templates

        scale = min(width / pw, height / ph) if pw > 0 and ph > 0 else 1.0
        cr.save()
        cr.scale(scale, scale)

        radius = 12.0 / scale
        self._rounded_rect(cr, 0, 0, pw, ph, radius)
        cr.clip()

        self.render_page_contents(cr, doc, page, pw, ph, assets, templates, page_idx=page_idx)

        cr.set_source_rgba(0, 0, 0, 0.18)
        cr.set_line_width(3.0 / scale)
        self._rounded_rect(cr, 0, 0, pw, ph, radius)
        cr.stroke()
        cr.restore()

    def _get_cached_page_surface(self, page_idx, page, pw, ph):
        key = (page_idx, round(self.zoom_level, 4))
        if key in self.page_cache:
            return self.page_cache[key]

        scaled_w = max(1, int(math.ceil(pw * self.zoom_level)))
        scaled_h = max(1, int(math.ceil(ph * self.zoom_level)))
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, scaled_w, scaled_h)
        cr = cairo.Context(surface)
        cr.scale(self.zoom_level, self.zoom_level)

        cr.rectangle(0, 0, pw, ph)
        cr.clip()

        self.render_page_contents(
            cr, self.document, page, pw, ph, self.assets, self.templates, page_idx=page_idx
        )

        surface.flush()
        self.page_cache[key] = surface
        return surface

    def draw_image(self, cr, item, assets=None, templates=None):
        assets = assets if assets is not None else self.assets
        templates = templates if templates is not None else self.templates

        asset_name = item.get("asset") or item.get("src") or item.get("image")
        surface = self._resolve_asset_surface(asset_name, assets, templates)
        if surface is None:
            return

        rect = item.get("rect")
        if not rect or len(rect) < 4:
            x = float(item.get("x", 0.0))
            y = float(item.get("y", 0.0))
            w = float(item.get("width", surface.get_width()))
            h = float(item.get("height", surface.get_height()))
        else:
            x, y, w, h = float(rect[0]), float(rect[1]), float(rect[2]), float(rect[3])

        if surface.get_width() <= 0 or surface.get_height() <= 0:
            return

        cr.save()
        cx = x + w / 2.0
        cy = y + h / 2.0
        cr.translate(cx, cy)

        angle = float(item.get("angle", 0.0))
        if angle != 0.0:
            cr.rotate(angle)

        flip_h = -1.0 if item.get("flip_h", False) else 1.0
        flip_v = -1.0 if item.get("flip_v", False) else 1.0
        if flip_h != 1.0 or flip_v != 1.0:
            cr.scale(flip_h, flip_v)

        cr.translate(-w / 2.0, -h / 2.0)
        scale_x = w / surface.get_width()
        scale_y = h / surface.get_height()
        cr.scale(scale_x, scale_y)
        cr.set_source_surface(surface, 0, 0)
        cr.paint()
        cr.restore()

    def draw_text(self, cr, text_item):
        text = text_item.get("text", "")
        if not text:
            return

        pos = text_item.get("pos", [text_item.get("x", 0.0), text_item.get("y", 0.0)])
        x = pos[0] if len(pos) > 0 else 0.0
        y = pos[1] if len(pos) > 1 else 0.0

        rgba = text_item.get("rgba", text_item.get("color", [30, 30, 30, 255]))
        r, g, b, a = rgba[0] / 255.0, rgba[1] / 255.0, rgba[2] / 255.0, rgba[3] / 255.0
        cr.set_source_rgba(r, g, b, a)

        width = text_item.get("width", 300.0)
        point_size = text_item.get("point_size", text_item.get("font_size", 13.0))

        layout = PangoCairo.create_layout(cr)
        layout.set_text(text, -1)
        font_desc = Pango.FontDescription.from_string(f"monospace {point_size}px")
        layout.set_font_description(font_desc)
        if width > 0:
            layout.set_width(int(width * Pango.SCALE))
            layout.set_wrap(Pango.WrapMode.WORD_CHAR)

        cr.move_to(x, y)
        PangoCairo.show_layout(cr, layout)

    @staticmethod
    def _fill_variable_capsule(cr, x0, y0, r0, x1, y1, r1):
        dx = x1 - x0
        dy = y1 - y0
        dist = math.hypot(dx, dy)

        cr.arc(x0, y0, r0, 0.0, 2.0 * math.pi)
        cr.fill()
        if dist < 1e-3:
            return

        ux = dx / dist
        uy = dy / dist
        nx = -uy
        ny = ux

        cr.move_to(x0 + nx * r0, y0 + ny * r0)
        cr.line_to(x1 + nx * r1, y1 + ny * r1)
        cr.line_to(x1 - nx * r1, y1 - ny * r1)
        cr.line_to(x0 - nx * r0, y0 - ny * r0)
        cr.close_path()
        cr.fill()

        cr.arc(x1, y1, r1, 0.0, 2.0 * math.pi)
        cr.fill()

    def draw_stroke(self, cr, stroke):
        raw_samples = stroke.get("samples") or stroke.get("points")
        if not raw_samples:
            return

        tool = stroke.get("tool", "pen")
        cfg = stroke.get("config") if isinstance(stroke.get("config"), dict) else {}

        # 1. Extract RGBA & Alpha
        rgba = cfg.get("rgba") or stroke.get("stroke_rgba") or stroke.get("color") or [0, 0, 0, 255]
        r = rgba[0] / 255.0
        g = rgba[1] / 255.0
        b = rgba[2] / 255.0
        a = rgba[3] / 255.0 if len(rgba) >= 4 else 1.0
        if "highlighter_alpha" in cfg:
            a = float(cfg["highlighter_alpha"])
        elif tool == "highlighter" and a > 0.9:
            a = 0.5

        base_width = float(cfg.get("base_width", stroke.get("stroke_width", stroke.get("width", 3.2))))

        # 2. Smooth sample coordinates
        if len(raw_samples) >= 3 and tool != "dashed":
            samples = self.smooth_samples_chaikin(raw_samples, iterations=1)
        else:
            samples = raw_samples

        # 3. Dashed Line Tool ("dashed")
        if tool == "dashed" or "dash_length" in cfg:
            dash_len = float(cfg.get("dash_length", 10.0))
            dash_gap = float(cfg.get("dash_gap", 8.0))
            cr.save()
            cr.set_source_rgba(r, g, b, a)
            cr.set_line_width(base_width)
            cr.set_line_join(cairo.LINE_JOIN_ROUND)
            cr.set_line_cap(cairo.LINE_CAP_ROUND)
            cr.set_dash([dash_len, dash_gap])

            cr.new_path()
            for idx, sample in enumerate(samples):
                x = float(sample["x"]) if isinstance(sample, dict) else float(sample[0])
                y = float(sample["y"]) if isinstance(sample, dict) else float(sample[1])
                if idx == 0:
                    cr.move_to(x, y)
                else:
                    cr.line_to(x, y)
            cr.stroke()
            cr.restore()
            return

        # 4. Highlighter or Uniform-Width Stroke
        pressure_enabled = bool(cfg.get("pressure_enabled", True))
        direction_strength = float(cfg.get("direction_strength", 0.576 if tool == "calligraphy" else 0.0))
        speed_strength = float(cfg.get("speed_strength", 0.869 if tool in ("speed", "velocity_pen") else 0.0))
        taper_enabled = bool(cfg.get("taper_enabled", tool == "taper"))

        if tool == "highlighter" or (
            not pressure_enabled
            and direction_strength <= 0.0
            and speed_strength <= 0.0
            and not taper_enabled
        ):
            cr.save()
            if a < 0.99:
                cr.push_group()
                cr.set_source_rgb(r, g, b)
                cr.set_line_width(base_width)
                cr.set_line_join(cairo.LINE_JOIN_ROUND)
                cr.set_line_cap(cairo.LINE_CAP_ROUND)
                cr.new_path()
                for idx, sample in enumerate(samples):
                    x = float(sample["x"]) if isinstance(sample, dict) else float(sample[0])
                    y = float(sample["y"]) if isinstance(sample, dict) else float(sample[1])
                    if idx == 0:
                        cr.move_to(x, y)
                    else:
                        cr.line_to(x, y)
                cr.stroke()
                cr.pop_group_to_source()
                cr.paint_with_alpha(a)
            else:
                cr.set_source_rgb(r, g, b)
                cr.set_line_width(base_width)
                cr.set_line_join(cairo.LINE_JOIN_ROUND)
                cr.set_line_cap(cairo.LINE_CAP_ROUND)
                cr.new_path()
                for idx, sample in enumerate(samples):
                    x = float(sample["x"]) if isinstance(sample, dict) else float(sample[0])
                    y = float(sample["y"]) if isinstance(sample, dict) else float(sample[1])
                    if idx == 0:
                        cr.move_to(x, y)
                    else:
                        cr.line_to(x, y)
                cr.stroke()
            cr.restore()
            return

        # 5. Variable-Width Stroke Engine
        pressure_min = float(cfg.get("pressure_min_factor", 0.45))
        taper_min = float(cfg.get("taper_min_factor", 0.345))
        speed_scale = float(stroke.get("speed_scale", 0.592))

        pts = []
        cum_lengths = [0.0]
        for idx, s in enumerate(samples):
            if isinstance(s, dict):
                px = float(s.get("x", 0.0))
                py = float(s.get("y", 0.0))
                pr = float(s.get("pressure", 0.5))
                dt = float(s.get("dt", 8.0))
            else:
                px = float(s[0]) if len(s) > 0 else 0.0
                py = float(s[1]) if len(s) > 1 else 0.0
                pr = float(s[2]) if len(s) > 2 else 0.5
                dt = float(s[3]) if len(s) > 3 else 8.0
            pts.append((px, py, max(0.0, min(1.0, pr)), max(1.0, dt)))
            if idx > 0:
                seg_d = math.hypot(px - pts[idx - 1][0], py - pts[idx - 1][1])
                cum_lengths.append(cum_lengths[-1] + seg_d)

        total_len = cum_lengths[-1]
        base_r = max(0.35, base_width / 2.0)
        radii = []

        for i, (px, py, pr, dt) in enumerate(pts):
            if pressure_enabled:
                p_factor = pressure_min + (1.0 - pressure_min) * (pr ** 0.85)
            else:
                p_factor = 1.0

            if i < len(pts) - 1:
                dx = pts[i + 1][0] - px
                dy = pts[i + 1][1] - py
                seg_dist = math.hypot(dx, dy)
            elif i > 0:
                dx = px - pts[i - 1][0]
                dy = py - pts[i - 1][1]
                seg_dist = math.hypot(dx, dy)
            else:
                dx, dy, seg_dist = 1.0, 0.0, 0.0

            if direction_strength > 0.0 and seg_dist > 1e-3:
                angle = math.atan2(dy, dx)
                nib_mod = abs(math.sin(angle - self.nib_angle_rad))
                dir_factor = (1.0 - direction_strength) + direction_strength * nib_mod
            else:
                dir_factor = 1.0

            if (tool in ("speed", "velocity_pen") or speed_strength > 0.0) and i > 0:
                d_prev = math.hypot(px - pts[i - 1][0], py - pts[i - 1][1])
                vel_px_ms = d_prev / max(1.0, dt)
                vel_atten = 1.0 / (1.0 + speed_scale * vel_px_ms * 1.6)
                spd_factor = (1.0 - speed_strength) + speed_strength * vel_atten
            else:
                spd_factor = 1.0

            if taper_enabled and total_len > 1.0:
                t_norm = cum_lengths[i] / total_len
                edge_env = math.sin(math.pi * t_norm) ** 0.65
                taper_factor = taper_min + (1.0 - taper_min) * edge_env
            else:
                taper_factor = 1.0

            rad = max(0.30, base_r * p_factor * dir_factor * spd_factor * taper_factor)
            radii.append(rad)

        if len(radii) >= 3:
            smoothed_r = [radii[0]]
            for i in range(1, len(radii) - 1):
                smoothed_r.append(0.25 * radii[i - 1] + 0.5 * radii[i] + 0.25 * radii[i + 1])
            smoothed_r.append(radii[-1])
            radii = smoothed_r

        cr.save()
        if a < 0.99:
            cr.push_group()
            cr.set_source_rgb(r, g, b)
            if len(pts) == 1:
                cr.arc(pts[0][0], pts[0][1], radii[0], 0.0, 2.0 * math.pi)
                cr.fill()
            else:
                for i in range(len(pts) - 1):
                    self._fill_variable_capsule(
                        cr,
                        pts[i][0], pts[i][1], radii[i],
                        pts[i + 1][0], pts[i + 1][1], radii[i + 1]
                    )
            cr.pop_group_to_source()
            cr.paint_with_alpha(a)
        else:
            cr.set_source_rgb(r, g, b)
            if len(pts) == 1:
                cr.arc(pts[0][0], pts[0][1], radii[0], 0.0, 2.0 * math.pi)
                cr.fill()
            else:
                for i in range(len(pts) - 1):
                    self._fill_variable_capsule(
                        cr,
                        pts[i][0], pts[i][1], radii[i],
                        pts[i + 1][0], pts[i + 1][1], radii[i + 1]
                    )
        cr.restore()
