import base64
import os
import re
import math
import copy
import cairo
import gi

gi.require_version('Pango', '1.0')
gi.require_version('PangoCairo', '1.0')
gi.require_version('Rsvg', '2.0')
gi.require_version('Gdk', '4.0')
from gi.repository import Pango, PangoCairo, Rsvg, Gdk

from xnote_io import XTemplateEvaluator, XNotePackageParser

_UNIT_REGEX = re.compile(r'(?<![a-zA-Z_])(\d+(?:\.\d+)?|\.\d+)\s*(mm|cm|in|pt|px|deg|%)', re.IGNORECASE)
_SIMPLE_LEN_REGEX = re.compile(r'^[+-]?\d+(?:\.\d+)?\s*(mm|cm|in|pt|px)?$', re.IGNORECASE)
_SVG_TOKEN_REGEX = re.compile(r'[MLHVAZmlhvaz]|[-+]?\d*\.?\d+')
_NUM_REGEX = re.compile(r'[-+]?\d*\.?\d+')

CSS_COLORS = {
    "aliceblue": (0.94, 0.97, 1.0, 1.0),
    "antiquewhite": (0.98, 0.92, 0.84, 1.0),
    "aqua": (0.0, 1.0, 1.0, 1.0),
    "aquamarine": (0.5, 1.0, 0.83, 1.0),
    "azure": (0.94, 1.0, 1.0, 1.0),
    "beige": (0.96, 0.96, 0.86, 1.0),
    "bisque": (1.0, 0.89, 0.77, 1.0),
    "black": (0.0, 0.0, 0.0, 1.0),
    "blanchedalmond": (1.0, 0.92, 0.8, 1.0),
    "blue": (0.0, 0.0, 1.0, 1.0),
    "blueviolet": (0.54, 0.17, 0.89, 1.0),
    "brown": (0.65, 0.16, 0.16, 1.0),
    "burlywood": (0.87, 0.72, 0.53, 1.0),
    "cadetblue": (0.37, 0.62, 0.63, 1.0),
    "chartreuse": (0.5, 1.0, 0.0, 1.0),
    "chocolate": (0.82, 0.41, 0.12, 1.0),
    "coral": (1.0, 0.5, 0.31, 1.0),
    "cornflowerblue": (0.39, 0.58, 0.93, 1.0),
    "cornsilk": (1.0, 0.97, 0.86, 1.0),
    "crimson": (0.86, 0.08, 0.24, 1.0),
    "cyan": (0.0, 1.0, 1.0, 1.0),
    "darkblue": (0.0, 0.0, 0.55, 1.0),
    "darkcyan": (0.0, 0.55, 0.55, 1.0),
    "darkgoldenrod": (0.72, 0.53, 0.04, 1.0),
    "darkgray": (0.66, 0.66, 0.66, 1.0),
    "darkgreen": (0.0, 0.39, 0.0, 1.0),
    "darkgrey": (0.66, 0.66, 0.66, 1.0),
    "darkkhaki": (0.74, 0.72, 0.42, 1.0),
    "darkmagenta": (0.55, 0.0, 0.55, 1.0),
    "darkolivegreen": (0.33, 0.42, 0.18, 1.0),
    "darkorange": (1.0, 0.55, 0.0, 1.0),
    "darkorchid": (0.6, 0.2, 0.8, 1.0),
    "darkred": (0.55, 0.0, 0.0, 1.0),
    "darksalmon": (0.91, 0.59, 0.48, 1.0),
    "darkseagreen": (0.56, 0.74, 0.56, 1.0),
    "darkslateblue": (0.28, 0.24, 0.55, 1.0),
    "darkslategray": (0.18, 0.31, 0.31, 1.0),
    "darkslategrey": (0.18, 0.31, 0.31, 1.0),
    "darkturquoise": (0.0, 0.81, 0.82, 1.0),
    "darkviolet": (0.58, 0.0, 0.83, 1.0),
    "deeppink": (1.0, 0.08, 0.58, 1.0),
    "deepskyblue": (0.0, 0.75, 1.0, 1.0),
    "dimgray": (0.41, 0.41, 0.41, 1.0),
    "dimgrey": (0.41, 0.41, 0.41, 1.0),
    "dodgerblue": (0.12, 0.56, 1.0, 1.0),
    "firebrick": (0.7, 0.13, 0.13, 1.0),
    "floralwhite": (1.0, 0.98, 0.94, 1.0),
    "forestgreen": (0.13, 0.55, 0.13, 1.0),
    "fuchsia": (1.0, 0.0, 1.0, 1.0),
    "gainsboro": (0.86, 0.86, 0.86, 1.0),
    "ghostwhite": (0.97, 0.97, 1.0, 1.0),
    "gold": (1.0, 0.84, 0.0, 1.0),
    "goldenrod": (0.85, 0.65, 0.13, 1.0),
    "gray": (0.5, 0.5, 0.5, 1.0),
    "green": (0.0, 0.5, 0.0, 1.0),
    "greenyellow": (0.68, 1.0, 0.18, 1.0),
    "grey": (0.5, 0.5, 0.5, 1.0),
    "honeydew": (0.94, 1.0, 0.94, 1.0),
    "hotpink": (1.0, 0.41, 0.71, 1.0),
    "indianred": (0.8, 0.36, 0.36, 1.0),
    "indigo": (0.29, 0.0, 0.51, 1.0),
    "ivory": (1.0, 1.0, 0.94, 1.0),
    "khaki": (0.94, 0.9, 0.55, 1.0),
    "lavender": (0.9, 0.9, 0.98, 1.0),
    "lavenderblush": (1.0, 0.94, 0.96, 1.0),
    "lawngreen": (0.49, 0.99, 0.0, 1.0),
    "lemonchiffon": (1.0, 0.98, 0.8, 1.0),
    "lightblue": (0.68, 0.85, 0.9, 1.0),
    "lightcoral": (0.94, 0.5, 0.5, 1.0),
    "lightcyan": (0.88, 1.0, 1.0, 1.0),
    "lightgoldenrodyellow": (0.98, 0.98, 0.82, 1.0),
    "lightgray": (0.83, 0.83, 0.83, 1.0),
    "lightgreen": (0.56, 0.93, 0.56, 1.0),
    "lightgrey": (0.83, 0.83, 0.83, 1.0),
    "lightpink": (1.0, 0.71, 0.76, 1.0),
    "lightsalmon": (1.0, 0.63, 0.48, 1.0),
    "lightseagreen": (0.13, 0.7, 0.67, 1.0),
    "lightskyblue": (0.53, 0.81, 0.98, 1.0),
    "lightslategray": (0.47, 0.53, 0.6, 1.0),
    "lightslategrey": (0.47, 0.53, 0.6, 1.0),
    "lightsteelblue": (0.69, 0.77, 0.87, 1.0),
    "lightyellow": (1.0, 1.0, 0.88, 1.0),
    "lime": (0.0, 1.0, 0.0, 1.0),
    "limegreen": (0.2, 0.8, 0.2, 1.0),
    "linen": (0.98, 0.94, 0.9, 1.0),
    "magenta": (1.0, 0.0, 1.0, 1.0),
    "maroon": (0.5, 0.0, 0.0, 1.0),
    "mediumaquamarine": (0.4, 0.8, 0.67, 1.0),
    "mediumblue": (0.0, 0.0, 0.8, 1.0),
    "mediumorchid": (0.73, 0.33, 0.83, 1.0),
    "mediumpurple": (0.58, 0.44, 0.86, 1.0),
    "mediumseagreen": (0.24, 0.7, 0.44, 1.0),
    "mediumslateblue": (0.48, 0.41, 0.93, 1.0),
    "mediumspringgreen": (0.0, 0.98, 0.6, 1.0),
    "mediumturquoise": (0.28, 0.82, 0.8, 1.0),
    "mediumvioletred": (0.78, 0.08, 0.52, 1.0),
    "midnightblue": (0.1, 0.1, 0.44, 1.0),
    "mintcream": (0.96, 1.0, 0.98, 1.0),
    "mistyrose": (1.0, 0.89, 0.88, 1.0),
    "moccasin": (1.0, 0.89, 0.71, 1.0),
    "navajowhite": (1.0, 0.87, 0.68, 1.0),
    "navy": (0.0, 0.0, 0.5, 1.0),
    "oldlace": (0.99, 0.96, 0.9, 1.0),
    "olive": (0.5, 0.5, 0.0, 1.0),
    "olivedrab": (0.42, 0.56, 0.14, 1.0),
    "orange": (1.0, 0.65, 0.0, 1.0),
    "orangered": (1.0, 0.27, 0.0, 1.0),
    "orchid": (0.85, 0.44, 0.84, 1.0),
    "palegoldenrod": (0.93, 0.91, 0.67, 1.0),
    "palegreen": (0.6, 0.98, 0.6, 1.0),
    "paleturquoise": (0.69, 0.93, 0.93, 1.0),
    "palevioletred": (0.86, 0.44, 0.58, 1.0),
    "papayawhip": (1.0, 0.94, 0.84, 1.0),
    "peachpuff": (1.0, 0.85, 0.73, 1.0),
    "peru": (0.8, 0.52, 0.25, 1.0),
    "pink": (1.0, 0.75, 0.8, 1.0),
    "plum": (0.87, 0.63, 0.87, 1.0),
    "powderblue": (0.69, 0.88, 0.9, 1.0),
    "purple": (0.5, 0.0, 0.5, 1.0),
    "rebeccapurple": (0.4, 0.2, 0.6, 1.0),
    "red": (1.0, 0.0, 0.0, 1.0),
    "rosybrown": (0.74, 0.56, 0.56, 1.0),
    "royalblue": (0.25, 0.41, 0.88, 1.0),
    "saddlebrown": (0.55, 0.27, 0.07, 1.0),
    "salmon": (0.98, 0.5, 0.45, 1.0),
    "sandybrown": (0.96, 0.64, 0.38, 1.0),
    "seagreen": (0.18, 0.55, 0.34, 1.0),
    "seashell": (1.0, 0.96, 0.93, 1.0),
    "sienna": (0.63, 0.32, 0.18, 1.0),
    "silver": (0.75, 0.75, 0.75, 1.0),
    "skyblue": (0.53, 0.81, 0.92, 1.0),
    "slateblue": (0.42, 0.35, 0.8, 1.0),
    "slategray": (0.44, 0.5, 0.56, 1.0),
    "slategrey": (0.44, 0.5, 0.56, 1.0),
    "snow": (1.0, 0.98, 0.98, 1.0),
    "springgreen": (0.0, 1.0, 0.5, 1.0),
    "steelblue": (0.27, 0.51, 0.71, 1.0),
    "tan": (0.82, 0.71, 0.55, 1.0),
    "teal": (0.0, 0.5, 0.5, 1.0),
    "thistle": (0.85, 0.75, 0.85, 1.0),
    "tomato": (1.0, 0.39, 0.28, 1.0),
    "turquoise": (0.25, 0.88, 0.82, 1.0),
    "violet": (0.93, 0.51, 0.93, 1.0),
    "wheat": (0.96, 0.87, 0.7, 1.0),
    "white": (1.0, 1.0, 1.0, 1.0),
    "whitesmoke": (0.96, 0.96, 0.96, 1.0),
    "yellow": (1.0, 1.0, 0.0, 1.0),
    "yellowgreen": (0.6, 0.8, 0.2, 1.0),
}


class CanvasRendererMixin:
    """Mixin class for XNoteCanvas providing page, stroke, text, image, and xtemplate rendering methods."""
    nib_angle_rad = -math.radians(35.0)

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
                pts.append((
                    float(s.get("x", 0.0)),
                    float(s.get("y", 0.0)),
                    float(s.get("pressure", 0.5)),
                    float(s.get("dt", 8.0))
                ))
            else:
                px = float(s[0]) if len(s) > 0 else 0.0
                py = float(s[1]) if len(s) > 1 else 0.0
                pr = float(s[2]) if len(s) > 2 else 0.5
                dt = float(s[3]) if len(s) > 3 else 8.0
                pts.append((px, py, pr, dt))

        for _ in range(iterations):
            if len(pts) < 3 or len(pts) > 1200:
                break
            new_pts = [pts[0]]
            for i in range(len(pts) - 1):
                p0 = pts[i]
                p1 = pts[i + 1]
                qx = 0.75 * p0[0] + 0.25 * p1[0]
                qy = 0.75 * p0[1] + 0.25 * p1[1]
                qp = 0.75 * p0[2] + 0.25 * p1[2]

                rx = 0.25 * p0[0] + 0.75 * p1[0]
                ry = 0.25 * p0[1] + 0.75 * p1[1]
                rp = 0.25 * p0[2] + 0.75 * p1[2]

                half_dt = max(1.0, p1[3] * 0.5)
                new_pts.append((qx, qy, qp, half_dt))
                new_pts.append((rx, ry, rp, half_dt))
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
    def _eval_xtemplate_expr(cls, expr, box_w=210.0, box_h=297.0, ref_dim=210.0, params=None, i=0, j=0, extra_vars=None, **kwargs):
        """Evaluate xtemplate v1 arithmetic/unit expressions in mm."""
        if isinstance(box_w, (int, float)) and isinstance(box_h, (int, float)) and isinstance(ref_dim, (int, float)) and params is not None and isinstance(params, (int, float)):
            dpi = float(box_w)
            total = float(box_h)
            actual_w = float(ref_dim)
            actual_h = float(params)
            actual_params = i if isinstance(i, dict) else kwargs.get("params")
            ref_mm = total / (dpi / 25.4) if dpi > 0 else total
            val_mm = cls._eval_xtemplate_expr(expr, actual_w / (dpi / 25.4), actual_h / (dpi / 25.4), ref_mm, actual_params)
            return val_mm * (dpi / 25.4)

        if expr is None:
            return 0.0
        if isinstance(expr, (int, float)):
            return float(expr)
        if not isinstance(expr, str):
            return 0.0
        s = expr.strip()
        if not s:
            return 0.0

        def repl_unit(m):
            val = float(m.group(1))
            unit = m.group(2).lower()
            if unit == "mm":
                return f"({val} * 1.0)"
            elif unit == "cm":
                return f"({val} * 10.0)"
            elif unit == "in":
                return f"({val} * 25.4)"
            elif unit == "pt":
                return f"({val} * {25.4 / 72.0})"
            elif unit == "deg":
                return f"({val} * 1.0)"
            elif unit == "%":
                return f"({val} * {ref_dim / 100.0})"
            return str(val)

        s_clean = _UNIT_REGEX.sub(repl_unit, s)

        env = {
            "W": float(box_w),
            "H": float(box_h),
            "i": float(i),
            "j": float(j),
            "pi": math.pi,
            "min": min,
            "max": max,
            "abs": abs,
            "floor": math.floor,
            "ceil": math.ceil,
            "round": lambda x: math.floor(x + 0.5) if x >= 0 else math.ceil(x - 0.5),
            "mod": lambda a, b: a % b,
            "sqrt": lambda x: math.sqrt(max(0.0, x)),
            "pow": math.pow,
            "sin": lambda deg: math.sin(math.radians(deg)),
            "cos": lambda deg: math.cos(math.radians(deg)),
            "tan": lambda deg: math.tan(math.radians(deg)),
            "log10": lambda x: math.log10(x) if x > 0 else 0.0,
        }
        if params:
            for k, v in params.items():
                if isinstance(v, dict):
                    raw = v.get("value", v.get("default", 0.0))
                else:
                    raw = v
                if isinstance(raw, (int, float)):
                    env[k] = float(raw)
                elif isinstance(raw, str):
                    try:
                        env[k] = cls._eval_xtemplate_expr(raw, box_w, box_h, ref_dim, params=None)
                    except Exception:
                        env[k] = 0.0
        if extra_vars:
            env.update(extra_vars)

        try:
            return float(eval(s_clean, {"__builtins__": {}}, env))
        except Exception:
            return 0.0

    @classmethod
    def _resolve_xtemplate_color(cls, col_spec, params, pattern_rgba, accent_rgba):
        if not col_spec or col_spec == "none":
            return None
        if col_spec in ("ink", "primary", "pattern"):
            return pattern_rgba
        if col_spec == "accent":
            return accent_rgba
        if params and isinstance(params, dict) and col_spec in params:
            p = params[col_spec]
            raw = p.get("value", p.get("default", col_spec)) if isinstance(p, dict) else p
            return cls._resolve_xtemplate_color(raw, params, pattern_rgba, accent_rgba)
        if isinstance(col_spec, (list, tuple)) and len(col_spec) >= 3:
            a = col_spec[3] / 255.0 if len(col_spec) >= 4 else 1.0
            return (col_spec[0] / 255.0, col_spec[1] / 255.0, col_spec[2] / 255.0, a)
        if isinstance(col_spec, str):
            c_str = col_spec.strip().lower()
            if c_str in CSS_COLORS:
                return CSS_COLORS[c_str]
            hx = c_str.lstrip("#")
            if len(hx) == 3:
                return (int(hx[0] * 2, 16) / 255.0, int(hx[1] * 2, 16) / 255.0, int(hx[2] * 2, 16) / 255.0, 1.0)
            elif len(hx) == 4:
                return (int(hx[0] * 2, 16) / 255.0, int(hx[1] * 2, 16) / 255.0, int(hx[2] * 2, 16) / 255.0, int(hx[3] * 2, 16) / 255.0)
            elif len(hx) == 6:
                return (int(hx[0:2], 16) / 255.0, int(hx[2:4], 16) / 255.0, int(hx[4:6], 16) / 255.0, 1.0)
            elif len(hx) == 8:
                return (int(hx[0:2], 16) / 255.0, int(hx[2:4], 16) / 255.0, int(hx[4:6], 16) / 255.0, int(hx[6:8], 16) / 255.0)
            rgba = Gdk.RGBA()
            if rgba.parse(c_str):
                return (rgba.red, rgba.green, rgba.blue, rgba.alpha)
        return pattern_rgba

    @classmethod
    def _derive_xtemplate_style(cls, parent_style, item, box_w, box_h, params, i, j, extra_vars):
        s = dict(parent_style)
        if "stroke" in item:
            s["stroke"] = item["stroke"]
        if "strokeWidth" in item:
            s["strokeWidth"] = cls._eval_xtemplate_expr(item["strokeWidth"], box_w, box_h, min(box_w, box_h), params, i, j, extra_vars)
        if "dash" in item:
            dash_val = item["dash"]
            if dash_val in ("none", None):
                s["dash"] = None
            elif isinstance(dash_val, (list, tuple)) and len(dash_val) >= 2:
                s["dash"] = [
                    cls._eval_xtemplate_expr(dash_val[0], box_w, box_h, min(box_w, box_h), params, i, j, extra_vars),
                    cls._eval_xtemplate_expr(dash_val[1], box_w, box_h, min(box_w, box_h), params, i, j, extra_vars)
                ]
        if "fill" in item:
            s["fill"] = item["fill"]
        if "opacity" in item:
            op = cls._eval_xtemplate_expr(item["opacity"], box_w, box_h, 1.0, params, i, j, extra_vars)
            s["opacity"] = s.get("opacity", 1.0) * max(0.0, min(1.0, op))
        if "font" in item:
            s["font"] = item["font"]
        if "size" in item:
            s["size"] = cls._eval_xtemplate_expr(item["size"], box_w, box_h, min(box_w, box_h), params, i, j, extra_vars)
        if "weight" in item:
            s["weight"] = item["weight"]
        if "italic" in item:
            s["italic"] = bool(item["italic"])
        if "align" in item:
            s["align"] = item["align"]
        if "baseline" in item:
            s["baseline"] = item["baseline"]
        if "rotate" in item:
            s["rotate"] = cls._eval_xtemplate_expr(item["rotate"], box_w, box_h, 1.0, params, i, j, extra_vars)
        return s

    @classmethod
    def _apply_xtemplate_stroke(cls, cr, style, params, pattern_rgba, accent_rgba):
        if style.get("stroke") in ("none", None):
            return False
        col = cls._resolve_xtemplate_color(style.get("stroke"), params, pattern_rgba, accent_rgba)
        if not col:
            return False
        cr.set_source_rgba(col[0], col[1], col[2], col[3] * style.get("opacity", 1.0))
        cr.set_line_width(max(0.01, style.get("strokeWidth", 0.25)))
        dash = style.get("dash")
        if dash and len(dash) >= 2:
            cr.set_dash([max(0.01, dash[0]), max(0.01, dash[1])], 0)
        else:
            cr.set_dash([], 0)
        return True

    @classmethod
    def _apply_xtemplate_fill(cls, cr, style, params, pattern_rgba, accent_rgba):
        if style.get("fill") in ("none", None):
            return False
        col = cls._resolve_xtemplate_color(style.get("fill"), params, pattern_rgba, accent_rgba)
        if not col:
            return False
        cr.set_source_rgba(col[0], col[1], col[2], col[3] * style.get("opacity", 1.0))
        return True

    @classmethod
    def _draw_xtemplate_items(cls, cr, items, box_x, box_y, box_w, box_h, page_w, page_h, params, pattern_rgba, accent_rgba, style, t_info, assets, i=0, j=0, extra_vars=None):
        if extra_vars is None:
            extra_vars = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            itype = item.get("type") or item.get("kind")
            node_style = cls._derive_xtemplate_style(style, item, box_w, box_h, params, i, j, extra_vars)

            if itype == "hline":
                y = cls._eval_xtemplate_expr(item.get("y", 0), box_w, box_h, box_h, params, i, j, extra_vars)
                actual_y = box_y + y
                if abs(actual_y) < 1e-4 or abs(actual_y - page_h) < 1e-4:
                    continue
                x1 = cls._eval_xtemplate_expr(item.get("x1", 0), box_w, box_h, box_w, params, i, j, extra_vars)
                x2 = cls._eval_xtemplate_expr(item.get("x2", "100%"), box_w, box_h, box_w, params, i, j, extra_vars)
                cr.move_to(box_x + x1, actual_y)
                cr.line_to(box_x + x2, actual_y)
                if cls._apply_xtemplate_stroke(cr, node_style, params, pattern_rgba, accent_rgba):
                    cr.stroke()

            elif itype == "vline":
                x = cls._eval_xtemplate_expr(item.get("x", 0), box_w, box_h, box_w, params, i, j, extra_vars)
                actual_x = box_x + x
                if abs(actual_x) < 1e-4 or abs(actual_x - page_w) < 1e-4:
                    continue
                y1 = cls._eval_xtemplate_expr(item.get("y1", 0), box_w, box_h, box_h, params, i, j, extra_vars)
                y2 = cls._eval_xtemplate_expr(item.get("y2", "100%"), box_w, box_h, box_h, params, i, j, extra_vars)
                cr.move_to(actual_x, box_y + y1)
                cr.line_to(actual_x, box_y + y2)
                if cls._apply_xtemplate_stroke(cr, node_style, params, pattern_rgba, accent_rgba):
                    cr.stroke()

            elif itype == "line":
                x1 = cls._eval_xtemplate_expr(item.get("x1", 0), box_w, box_h, box_w, params, i, j, extra_vars)
                y1 = cls._eval_xtemplate_expr(item.get("y1", 0), box_w, box_h, box_h, params, i, j, extra_vars)
                x2 = cls._eval_xtemplate_expr(item.get("x2", 0), box_w, box_h, box_w, params, i, j, extra_vars)
                y2 = cls._eval_xtemplate_expr(item.get("y2", 0), box_w, box_h, box_h, params, i, j, extra_vars)
                cr.move_to(box_x + x1, box_y + y1)
                cr.line_to(box_x + x2, box_y + y2)
                if cls._apply_xtemplate_stroke(cr, node_style, params, pattern_rgba, accent_rgba):
                    cr.stroke()

            elif itype == "dot":
                cx = cls._eval_xtemplate_expr(item.get("cx", 0), box_w, box_h, box_w, params, i, j, extra_vars)
                cy = cls._eval_xtemplate_expr(item.get("cy", 0), box_w, box_h, box_h, params, i, j, extra_vars)
                actual_cx = box_x + cx
                actual_cy = box_y + cy
                if abs(actual_cx) < 1e-4 or abs(actual_cx - page_w) < 1e-4 or abs(actual_cy) < 1e-4 or abs(actual_cy - page_h) < 1e-4:
                    continue
                r = cls._eval_xtemplate_expr(item.get("r", "0.35mm"), box_w, box_h, min(box_w, box_h), params, i, j, extra_vars)
                dot_col = node_style.get("fill") if (node_style.get("fill") not in ("none", None)) else node_style.get("stroke")
                col = cls._resolve_xtemplate_color(dot_col, params, pattern_rgba, accent_rgba)
                if col and r > 0.01:
                    cr.set_source_rgba(col[0], col[1], col[2], col[3] * node_style.get("opacity", 1.0))
                    cr.new_sub_path()
                    cr.arc(actual_cx, actual_cy, r, 0, 2 * math.pi)
                    cr.fill()

            elif itype == "circle":
                cx = cls._eval_xtemplate_expr(item.get("cx", 0), box_w, box_h, box_w, params, i, j, extra_vars)
                cy = cls._eval_xtemplate_expr(item.get("cy", 0), box_w, box_h, box_h, params, i, j, extra_vars)
                actual_cx = box_x + cx
                actual_cy = box_y + cy
# edge check omitted
                r = cls._eval_xtemplate_expr(item.get("r", 0), box_w, box_h, min(box_w, box_h), params, i, j, extra_vars)
                if r > 0.01:
                    cr.new_sub_path()
                    cr.arc(actual_cx, actual_cy, r, 0, 2 * math.pi)
                    has_fill = cls._apply_xtemplate_fill(cr, node_style, params, pattern_rgba, accent_rgba)
                    has_stroke = node_style.get("stroke") not in ("none", None)
                    if has_fill and has_stroke:
                        cr.fill_preserve()
                    elif has_fill:
                        cr.fill()
                    if has_stroke:
                        if cls._apply_xtemplate_stroke(cr, node_style, params, pattern_rgba, accent_rgba):
                            cr.stroke()

            elif itype == "ellipse":
                cx = cls._eval_xtemplate_expr(item.get("cx", 0), box_w, box_h, box_w, params, i, j, extra_vars)
                cy = cls._eval_xtemplate_expr(item.get("cy", 0), box_w, box_h, box_h, params, i, j, extra_vars)
                actual_cx = box_x + cx
                actual_cy = box_y + cy
# edge check omitted
                rx = cls._eval_xtemplate_expr(item.get("rx", 0), box_w, box_h, box_w, params, i, j, extra_vars)
                ry = cls._eval_xtemplate_expr(item.get("ry", 0), box_w, box_h, box_h, params, i, j, extra_vars)
                if rx > 0.01 and ry > 0.01:
                    cr.save()
                    cr.translate(actual_cx, actual_cy)
                    cr.scale(rx, ry)
                    cr.arc(0, 0, 1.0, 0, 2 * math.pi)
                    cr.restore()
                    has_fill = cls._apply_xtemplate_fill(cr, node_style, params, pattern_rgba, accent_rgba)
                    has_stroke = node_style.get("stroke") not in ("none", None)
                    if has_fill and has_stroke:
                        cr.fill_preserve()
                    elif has_fill:
                        cr.fill()
                    if has_stroke:
                        if cls._apply_xtemplate_stroke(cr, node_style, params, pattern_rgba, accent_rgba):
                            cr.stroke()

            elif itype == "rect":
                x = cls._eval_xtemplate_expr(item.get("x", 0), box_w, box_h, box_w, params, i, j, extra_vars)
                y = cls._eval_xtemplate_expr(item.get("y", 0), box_w, box_h, box_h, params, i, j, extra_vars)
                w = cls._eval_xtemplate_expr(item.get("w", "100%"), box_w, box_h, box_w, params, i, j, extra_vars)
                h = cls._eval_xtemplate_expr(item.get("h", "100%"), box_w, box_h, box_h, params, i, j, extra_vars)
                rx = cls._eval_xtemplate_expr(item.get("rx", 0), box_w, box_h, min(w, h), params, i, j, extra_vars)
                if w > 0.01 and h > 0.01:
                    rx = min(rx, min(w, h) / 2.0)
                    cr.new_sub_path()
                    if rx > 0.01:
                        cr.arc(box_x + x + w - rx, box_y + y + rx, rx, -math.pi / 2, 0)
                        cr.arc(box_x + x + w - rx, box_y + y + h - rx, rx, 0, math.pi / 2)
                        cr.arc(box_x + x + rx, box_y + y + h - rx, rx, math.pi / 2, math.pi)
                        cr.arc(box_x + x + rx, box_y + y + rx, rx, math.pi, 3 * math.pi / 2)
                        cr.close_path()
                    else:
                        cr.rectangle(box_x + x, box_y + y, w, h)
                    has_fill = cls._apply_xtemplate_fill(cr, node_style, params, pattern_rgba, accent_rgba)
                    has_stroke = node_style.get("stroke") not in ("none", None)
                    if has_fill and has_stroke:
                        cr.fill_preserve()
                    elif has_fill:
                        cr.fill()
                    if has_stroke:
                        if cls._apply_xtemplate_stroke(cr, node_style, params, pattern_rgba, accent_rgba):
                            cr.stroke()

            elif itype in ("polyline", "polygon"):
                pts = item.get("points", [])
                if isinstance(pts, str):
                    raw_coords = [float(v) for v in re.findall(r"[-+]?\d+(?:\.\d+)?", pts)]
                    pt_list = [[raw_coords[k], raw_coords[k + 1]] for k in range(0, len(raw_coords) - 1, 2)]
                else:
                    pt_list = pts
                if pt_list:
                    cr.new_sub_path()
                    for idx_pt, pt in enumerate(pt_list):
                        px = cls._eval_xtemplate_expr(pt[0], box_w, box_h, box_w, params, i, j, extra_vars)
                        py = cls._eval_xtemplate_expr(pt[1], box_w, box_h, box_h, params, i, j, extra_vars)
                        if idx_pt == 0:
                            cr.move_to(box_x + px, box_y + py)
                        else:
                            cr.line_to(box_x + px, box_y + py)
                    if itype == "polygon":
                        cr.close_path()
                    has_fill = cls._apply_xtemplate_fill(cr, node_style, params, pattern_rgba, accent_rgba) if itype == "polygon" else False
                    has_stroke = node_style.get("stroke") not in ("none", None)
                    if has_fill and has_stroke:
                        cr.fill_preserve()
                    elif has_fill:
                        cr.fill()
                    if has_stroke:
                        if cls._apply_xtemplate_stroke(cr, node_style, params, pattern_rgba, accent_rgba):
                            cr.stroke()

            elif itype == "hatch":
                angle = cls._eval_xtemplate_expr(item.get("angle", 0), box_w, box_h, 1.0, params, i, j, extra_vars)
                period = cls._eval_xtemplate_expr(item.get("period"), box_w, box_h, min(box_w, box_h), params, i, j, extra_vars)
                offset = cls._eval_xtemplate_expr(item.get("offset", 0), box_w, box_h, min(box_w, box_h), params, i, j, extra_vars)
                extend = bool(item.get("extend", False))
                if period > 0.1:
                    hx, hy, hw, hh = (0.0, 0.0, page_w, page_h) if extend else (box_x, box_y, box_w, box_h)
                    cr.save()
                    cr.rectangle(hx, hy, hw, hh)
                    cr.clip()

                    theta = math.radians(angle)
                    dx = math.cos(theta)
                    dy = -math.sin(theta)
                    nx = -dy
                    ny = dx

                    corners = [(0, 0), (hw, 0), (hw, hh), (0, hh)]
                    projs = [cx_c * nx + cy_c * ny for (cx_c, cy_c) in corners]
                    p_min = min(projs)
                    p_max = max(projs)

                    diag = 2.0 * (hw + hh)
                    k_start = int(math.floor((p_min - offset) / period)) - 1
                    k_end = int(math.ceil((p_max - offset) / period)) + 1

                    for k in range(k_start, k_end + 1):
                        dist = k * period + offset
                        mid_x = hx + dist * nx
                        mid_y = hy + dist * ny
                        cr.move_to(mid_x - diag * dx, mid_y - diag * dy)
                        cr.line_to(mid_x + diag * dx, mid_y + diag * dy)

                    if cls._apply_xtemplate_stroke(cr, node_style, params, pattern_rgba, accent_rgba):
                        cr.stroke()
                    cr.restore()

            elif itype == "text":
                raw_text = item.get("text", "")
                if isinstance(raw_text, list) and raw_text:
                    sel_expr = item.get("select", "i")
                    sel_val = cls._eval_xtemplate_expr(sel_expr, box_w, box_h, 1.0, params, i, j, extra_vars)
                    idx_txt = int(math.floor(sel_val)) % len(raw_text)
                    txt_template = str(raw_text[idx_txt])
                else:
                    txt_template = str(raw_text)

                def _fmt_match(m):
                    expr_inner = m.group(1)
                    fmt_part = None
                    if ":" in expr_inner:
                        expr_inner, fmt_part = expr_inner.rsplit(":", 1)
                    val = cls._eval_xtemplate_expr(expr_inner, box_w, box_h, 1.0, params, i, j, extra_vars)
                    if fmt_part and fmt_part.startswith("0") and fmt_part[1:].isdigit():
                        digits = int(fmt_part[1:])
                        return f"{int(round(val)):0{digits}d}"
                    if abs(val - round(val)) < 1e-4:
                        return str(int(round(val)))
                    return f"{round(val, 3):.3f}".rstrip("0").rstrip(".")

                txt = re.sub(r"\{([^}]+)\}", _fmt_match, txt_template)
                if txt:
                    tx = cls._eval_xtemplate_expr(item.get("x", 0), box_w, box_h, box_w, params, i, j, extra_vars)
                    ty = cls._eval_xtemplate_expr(item.get("y", 0), box_w, box_h, box_h, params, i, j, extra_vars)
                    actual_tx = box_x + tx
                    actual_ty = box_y + ty

                    font_family = node_style.get("font", "sans")
                    if font_family == "sans": font_family = "Sans"
                    elif font_family == "serif": font_family = "Serif"
                    elif font_family == "mono": font_family = "Monospace"

                    font_size_mm = node_style.get("size", 3.5)
                    font_weight = "Bold" if str(node_style.get("weight")).lower() == "bold" else ""
                    font_italic = "Italic" if node_style.get("italic") else ""

                    layout = PangoCairo.create_layout(cr)
                    layout.set_text(txt, -1)
                    font_desc = Pango.FontDescription(f"{font_family} {font_weight} {font_italic}")
                    font_desc.set_absolute_size(int(round(font_size_mm * Pango.SCALE)))
                    layout.set_font_description(font_desc)
                    PangoCairo.update_layout(cr, layout)

                    _, log_ext = layout.get_extents()
                    tw_mm = log_ext.width / Pango.SCALE
                    th_mm = log_ext.height / Pango.SCALE

                    align = node_style.get("align", "start")
                    ox = 0.0
                    if align == "middle": ox = -tw_mm / 2.0
                    elif align == "end": ox = -tw_mm

                    baseline = node_style.get("baseline", "alphabetic")
                    bl = layout.get_baseline()
                    base_dist_mm = (bl / Pango.SCALE) if bl > 0 else (th_mm * 0.75)
                    oy = 0.0
                    if baseline == "top": oy = 0.0
                    elif baseline == "middle": oy = -base_dist_mm * 0.5
                    else: oy = -base_dist_mm

                    txt_col = node_style.get("fill") if (node_style.get("fill") not in ("none", None)) else node_style.get("stroke")
                    col = cls._resolve_xtemplate_color(txt_col, params, pattern_rgba, accent_rgba)
                    if col:
                        cr.save()
                        cr.set_source_rgba(col[0], col[1], col[2], col[3] * node_style.get("opacity", 1.0))
                        cr.translate(actual_tx, actual_ty)
                        rot = node_style.get("rotate", 0)
                        rot_deg = round(rot / 90.0) * 90.0
                        if abs(rot_deg) > 1e-4:
                            cr.rotate(math.radians(rot_deg))
                        cr.move_to(ox, oy)
                        PangoCairo.show_layout(cr, layout)
                        cr.restore()

            elif itype == "image":
                src = item.get("src")
                surf = None
                if t_info and src in t_info.get("images", {}):
                    surf = t_info["images"][src]
                elif t_info and os.path.basename(str(src)) in t_info.get("images", {}):
                    surf = t_info["images"][os.path.basename(str(src))]
                elif assets and src in assets:
                    surf = assets[src]
                elif assets and os.path.basename(str(src)) in assets:
                    surf = assets[os.path.basename(str(src))]

                if surf and surf.get_width() > 0 and surf.get_height() > 0:
                    ix = cls._eval_xtemplate_expr(item.get("x", 0), box_w, box_h, box_w, params, i, j, extra_vars)
                    iy = cls._eval_xtemplate_expr(item.get("y", 0), box_w, box_h, box_h, params, i, j, extra_vars)
                    iw_box = cls._eval_xtemplate_expr(item.get("w", "100%"), box_w, box_h, box_w, params, i, j, extra_vars)
                    ih_box = cls._eval_xtemplate_expr(item.get("h", "100%"), box_w, box_h, box_h, params, i, j, extra_vars)

                    img_w = surf.get_width()
                    img_h = surf.get_height()
                    fit = item.get("fit", "contain")
                    position = item.get("position", "center")

                    cr.save()
                    cr.rectangle(box_x + ix, box_y + iy, iw_box, ih_box)
                    cr.clip()

                    if fit == "cover":
                        s_img = max(iw_box / img_w, ih_box / img_h)
                    elif fit == "contain":
                        s_img = min(iw_box / img_w, ih_box / img_h)
                    else:
                        cr.translate(box_x + ix, box_y + iy)
                        cr.scale(iw_box / img_w, ih_box / img_h)
                        cr.set_source_surface(surf, 0, 0)
                        cr.paint_with_alpha(node_style.get("opacity", 1.0))
                        cr.restore()
                        continue

                    draw_w = img_w * s_img
                    draw_h = img_h * s_img

                    dx = box_x + ix + (iw_box - draw_w) / 2.0
                    dy = box_y + iy + (ih_box - draw_h) / 2.0
                    if "left" in position: dx = box_x + ix
                    elif "right" in position: dx = box_x + ix + iw_box - draw_w
                    if "top" in position: dy = box_y + iy
                    elif "bottom" in position: dy = box_y + iy + ih_box - draw_h

                    cr.translate(dx, dy)
                    cr.scale(s_img, s_img)
                    cr.set_source_surface(surf, 0, 0)
                    cr.paint_with_alpha(node_style.get("opacity", 1.0))
                    cr.restore()

            elif itype == "path":
                d_str = item.get("d", "")
                if not d_str:
                    continue
                vb = item.get("viewBox")
                x = cls._eval_xtemplate_expr(item.get("x", 0), box_w, box_h, box_w, params, i, j, extra_vars)
                y = cls._eval_xtemplate_expr(item.get("y", 0), box_w, box_h, box_h, params, i, j, extra_vars)
                w = cls._eval_xtemplate_expr(item.get("w", "100%"), box_w, box_h, box_w, params, i, j, extra_vars)
                h = cls._eval_xtemplate_expr(item.get("h", "100%"), box_w, box_h, box_h, params, i, j, extra_vars)

                fill_col = cls._resolve_xtemplate_color(node_style.get("fill"), params, pattern_rgba, accent_rgba)
                stroke_col = cls._resolve_xtemplate_color(node_style.get("stroke"), params, pattern_rgba, accent_rgba)

                op = node_style.get("opacity", 1.0)
                f_hex = f"rgba({int(fill_col[0]*255)}, {int(fill_col[1]*255)}, {int(fill_col[2]*255)}, {fill_col[3]*op})" if fill_col else "none"
                s_hex = f"rgba({int(stroke_col[0]*255)}, {int(stroke_col[1]*255)}, {int(stroke_col[2]*255)}, {stroke_col[3]*op})" if stroke_col else "none"
                sw = max(0.01, node_style.get("strokeWidth", 0.25))

                aspect = item.get("aspect", "meet")
                aspect_attr = 'preserveAspectRatio="none"' if aspect == "stretch" else 'preserveAspectRatio="xMidYMid meet"'
                if isinstance(vb, (list, tuple)) and len(vb) >= 4:
                    vb_attr = f'viewBox="{vb[0]} {vb[1]} {vb[2]} {vb[3]}"'
                elif isinstance(vb, str) and vb.strip():
                    vb_attr = f'viewBox="{vb.strip()}"'
                else:
                    vb_attr = ""

                svg_data = f"""<svg xmlns="http://www.w3.org/2000/svg" width="{w}mm" height="{h}mm" {vb_attr} {aspect_attr}>
                  <path d="{d_str}" fill="{f_hex}" stroke="{s_hex}" stroke-width="{sw}" stroke-linecap="round" stroke-linejoin="round" />
                </svg>"""
                try:
                    handle = Rsvg.Handle.new_from_data(svg_data.encode("utf-8"))
                    cr.save()
                    cr.translate(box_x + x, box_y + y)
                    vp = Rsvg.Rectangle()
                    vp.x, vp.y, vp.width, vp.height = 0, 0, w, h
                    handle.render_document(cr, vp)
                    cr.restore()
                except Exception:
                    pass

            elif itype == "group":
                inset = item.get("inset")
                if inset is not None:
                    if isinstance(inset, (list, tuple)):
                        if len(inset) == 1:
                            itop = ibottom = ileft = iright = inset[0]
                        elif len(inset) == 2:
                            itop = ibottom = inset[0]
                            ileft = iright = inset[1]
                        elif len(inset) == 3:
                            itop = inset[0]
                            ileft = iright = inset[1]
                            ibottom = inset[2]
                        else:
                            itop, iright, ibottom, ileft = inset[0], inset[1], inset[2], inset[3]
                    else:
                        itop = ibottom = ileft = iright = inset

                    t_val = cls._eval_xtemplate_expr(itop, box_w, box_h, box_h, params, i, j, extra_vars)
                    b_val = cls._eval_xtemplate_expr(ibottom, box_w, box_h, box_h, params, i, j, extra_vars)
                    l_val = cls._eval_xtemplate_expr(ileft, box_w, box_h, box_w, params, i, j, extra_vars)
                    r_val = cls._eval_xtemplate_expr(iright, box_w, box_h, box_w, params, i, j, extra_vars)

                    gx = box_x + l_val
                    gy = box_y + t_val
                    gw = max(0.0, box_w - l_val - r_val)
                    gh = max(0.0, box_h - t_val - b_val)
                else:
                    x = cls._eval_xtemplate_expr(item.get("x", 0), box_w, box_h, box_w, params, i, j, extra_vars)
                    y = cls._eval_xtemplate_expr(item.get("y", 0), box_w, box_h, box_h, params, i, j, extra_vars)
                    gw = cls._eval_xtemplate_expr(item.get("w", "100%"), box_w, box_h, box_w, params, i, j, extra_vars)
                    gh = cls._eval_xtemplate_expr(item.get("h", "100%"), box_w, box_h, box_h, params, i, j, extra_vars)
                    gx = box_x + x
                    gy = box_y + y

                clip = bool(item.get("clip", False))
                children = item.get("children", [])
                if gw > 0.01 and gh > 0.01 and children:
                    cr.save()
                    if clip:
                        cr.rectangle(gx, gy, gw, gh)
                        cr.clip()
                    cls._draw_xtemplate_items(cr, children, gx, gy, gw, gh, page_w, page_h, params, pattern_rgba, accent_rgba, node_style, t_info, assets, i, j, extra_vars)
                    cr.restore()

            elif itype == "repeat":
                axis = str(item.get("axis", "y")).lower()
                children = item.get("children", [])
                if not children:
                    continue

                extend = bool(item.get("extend", False))
                stagger_expr = item.get("stagger", 0)
                stagger = cls._eval_xtemplate_expr(stagger_expr, box_w, box_h, box_w, params, i, j, extra_vars) if stagger_expr else 0.0

                raw_period = item.get("period")
                raw_count = item.get("count")
                raw_fit = item.get("fit", "repeat")

                idx_names = item.get("index")

                if axis == "none":
                    count = int(round(cls._eval_xtemplate_expr(raw_count, box_w, box_h, 1.0, params, i, j, extra_vars))) if raw_count else 1
                    for rep_i in range(count):
                        rep_vars = dict(extra_vars)
                        if isinstance(idx_names, str):
                            rep_vars[idx_names] = float(rep_i)
                        cls._draw_xtemplate_items(cr, children, box_x, box_y, box_w, box_h, page_w, page_h, params, pattern_rgba, accent_rgba, node_style, t_info, assets, rep_i, j, rep_vars)
                    continue

                def get_axis_plan(axis_name, dim_len, raw_p, raw_c, fit_mode):
                    ref_dim = box_w if axis_name == "x" else box_h
                    p = cls._eval_xtemplate_expr(raw_p, box_w, box_h, ref_dim, params, i, j, extra_vars) if raw_p else None
                    c = int(round(cls._eval_xtemplate_expr(raw_c, box_w, box_h, 1.0, params, i, j, extra_vars))) if raw_c else None

                    if p is None and c is not None:
                        n = max(1, c)
                        cell_size = dim_len / n
                        return [(k, k * cell_size, cell_size) for k in range(n)]

                    if p is None or p < 0.1:
                        return []

                    if fit_mode == "round":
                        n = c if c is not None else max(1, int(round(dim_len / p)))
                        cell_size = dim_len / n
                        return [(k, k * cell_size, cell_size) for k in range(n)]

                    elif fit_mode == "space":
                        n = int(math.floor(dim_len / p))
                        if c is not None: n = min(n, c)
                        if n < 1:
                            return []
                        if n > 1:
                            spacing = (dim_len - n * p) / (n - 1)
                            return [(k, k * (p + spacing), p) for k in range(n)]
                        else:
                            start = (dim_len - p) / 2.0
                            return [(0, start, p)]

                    elif fit_mode == "center":
                        n = int(math.floor(dim_len / p))
                        if c is not None: n = min(n, c)
                        if n < 1:
                            return []
                        start = (dim_len - n * p) / 2.0
                        return [(k, start + k * p, p) for k in range(n)]

                    else:  # repeat
                        if extend:
                            start_k = -int(math.ceil(box_x / p)) - 2 if axis_name == "x" else -int(math.ceil(box_y / p)) - 2
                            end_k = int(math.ceil((page_w - box_x) / p)) + 2 if axis_name == "x" else int(math.ceil((page_h - box_y) / p)) + 2
                            return [(k, k * p, p) for k in range(start_k, end_k + 1)]
                        else:
                            start_k = -1 if (axis_name == "x" and stagger != 0) else 0
                            max_c = c if c is not None else int(math.ceil(dim_len / p)) + 2
                            return [(k, k * p, p) for k in range(start_k, max_c) if (k * p < dim_len and ((k + 1) * p + (stagger if (axis_name == "x" and k < 0) else 0)) > 0)]

                if axis == "y":
                    plan = get_axis_plan("y", box_h, raw_period, raw_count, raw_fit)
                    cr.save()
                    if extend: cr.rectangle(0, 0, page_w, page_h)
                    else: cr.rectangle(box_x, box_y, box_w, box_h)
                    cr.clip()
                    for cell_idx, off_y, cell_h in plan:
                        rep_vars = dict(extra_vars)
                        if isinstance(idx_names, str): rep_vars[idx_names] = float(cell_idx)
                        cls._draw_xtemplate_items(cr, children, 0.0 if extend else box_x, box_y + off_y, page_w if extend else box_w, cell_h, page_w, page_h, params, pattern_rgba, accent_rgba, node_style, t_info, assets, cell_idx, j, rep_vars)
                    cr.restore()

                elif axis == "x":
                    plan = get_axis_plan("x", box_w, raw_period, raw_count, raw_fit)
                    cr.save()
                    if extend: cr.rectangle(0, 0, page_w, page_h)
                    else: cr.rectangle(box_x, box_y, box_w, box_h)
                    cr.clip()
                    for cell_idx, off_x, cell_w in plan:
                        rep_vars = dict(extra_vars)
                        if isinstance(idx_names, str): rep_vars[idx_names] = float(cell_idx)
                        cls._draw_xtemplate_items(cr, children, box_x + off_x, 0.0 if extend else box_y, cell_w, page_h if extend else box_h, page_w, page_h, params, pattern_rgba, accent_rgba, node_style, t_info, assets, cell_idx, j, rep_vars)
                    cr.restore()

                elif axis == "xy":
                    px_val = raw_period[0] if isinstance(raw_period, (list, tuple)) else raw_period
                    py_val = raw_period[1] if isinstance(raw_period, (list, tuple)) else raw_period
                    cx_val = raw_count[0] if isinstance(raw_count, (list, tuple)) else raw_count
                    cy_val = raw_count[1] if isinstance(raw_count, (list, tuple)) else raw_count
                    fx_val = raw_fit[0] if isinstance(raw_fit, (list, tuple)) else raw_fit
                    fy_val = raw_fit[1] if isinstance(raw_fit, (list, tuple)) else raw_fit

                    plan_x = get_axis_plan("x", box_w, px_val, cx_val, fx_val)
                    plan_y = get_axis_plan("y", box_h, py_val, cy_val, fy_val)

                    cr.save()
                    if extend: cr.rectangle(0, 0, page_w, page_h)
                    else: cr.rectangle(box_x, box_y, box_w, box_h)
                    cr.clip()

                    col_var = idx_names[0] if isinstance(idx_names, (list, tuple)) and len(idx_names) >= 1 else None
                    row_var = idx_names[1] if isinstance(idx_names, (list, tuple)) and len(idx_names) >= 2 else None

                    for row_idx, off_y, cell_h in plan_y:
                        x_shift = stagger if (row_idx % 2 != 0) else 0.0
                        for col_idx, off_x, cell_w in plan_x:
                            rep_vars = dict(extra_vars)
                            if col_var: rep_vars[col_var] = float(col_idx)
                            if row_var: rep_vars[row_var] = float(row_idx)
                            cls._draw_xtemplate_items(cr, children, box_x + off_x + x_shift, box_y + off_y, cell_w, cell_h, page_w, page_h, params, pattern_rgba, accent_rgba, node_style, t_info, assets, col_idx, row_idx, rep_vars)
                    cr.restore()

    def render_page_background(self, cr, doc, page, pw, ph, assets, templates, page_idx=0, with_background=True, with_pattern=True, optimize_for_printing=False):
        """Render only the static page background (paper color, PDF layer, xtemplate grid, flow text)."""
        dpi = float(doc.get("dpi", 150.0))
        doc_style = doc.get("style", {}) if isinstance(doc.get("style"), dict) else {}
        style = page.get("style", {}) if isinstance(page.get("style"), dict) else {}

        if with_background:
            if optimize_for_printing:
                cr.set_source_rgba(1.0, 1.0, 1.0, 1.0)
            else:
                bg = style.get("page_color", doc_style.get("page_color", [255, 255, 255, 255]))
                cr.set_source_rgba(bg[0] / 255.0, bg[1] / 255.0, bg[2] / 255.0, bg[3] / 255.0)
            cr.rectangle(0, 0, pw, ph)
            cr.fill()

        pdf_page_num = page.get("pdf_page")
        if with_background and pdf_page_num is not None and isinstance(pdf_page_num, int) and pdf_page_num >= 0:
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
        ) if (with_pattern and not optimize_for_printing) else None
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
            scale_mm = dpi / 25.4
            page_w_mm = pw / scale_mm
            page_h_mm = ph / scale_mm

            doc_tid = doc_style.get("template")
            matches_doc_template = (not doc_tid) or (doc_tid == t_ref) or (doc_tid == t_info.get("id")) or (XNotePackageParser.extract_template_id(doc_tid) == t_info.get("id"))

            for source_dict, is_doc in [(doc_style, True), (style, False)]:
                if is_doc and not matches_doc_template:
                    continue
                for k, v in source_dict.items():
                    target_pk = None
                    if k in t_params:
                        target_pk = k
                    elif k == "spacing":
                        for pk, pv in t_params.items():
                            if isinstance(pv, dict) and pv.get("role") == "spacing":
                                target_pk = pk
                                break
                        if not target_pk and "spacing" in t_params:
                            target_pk = "spacing"
                    if not target_pk:
                        continue

                    pv = t_params[target_pk]
                    if not isinstance(pv, dict):
                        pv = {"value": v}
                        t_params[target_pk] = pv

                    # Convert number or expression to mm
                    if isinstance(v, (int, float)):
                        if float(v) > 20.0 or float(v) == 64.0:
                            v_mm = float(v) / (dpi / 25.4)
                        else:
                            v_mm = float(v)
                    elif isinstance(v, str):
                        v_mm = self._eval_xtemplate_expr(v, page_w_mm, page_h_mm, page_w_mm, t_params)
                    else:
                        continue

                    # Clamp to min and max if defined in parameter spec (§4)
                    if "min" in pv:
                        min_mm = self._eval_xtemplate_expr(pv["min"], page_w_mm, page_h_mm, page_w_mm, t_params)
                        v_mm = max(v_mm, min_mm)
                    if "max" in pv:
                        max_mm = self._eval_xtemplate_expr(pv["max"], page_w_mm, page_h_mm, page_w_mm, t_params)
                        v_mm = min(v_mm, max_mm)

                    pv["value"] = f"{v_mm:.4f}mm"

            if t_items:
                cr.save()
                cr.scale(scale_mm, scale_mm)
                cr.set_line_cap(cairo.LINE_CAP_ROUND)
                cr.set_line_join(cairo.LINE_JOIN_ROUND)

                top_style = {
                    "stroke": "ink",
                    "strokeWidth": 0.25,
                    "dash": None,
                    "fill": "none",
                    "opacity": 1.0,
                    "font": "sans",
                    "size": 3.5,
                    "weight": "normal",
                    "italic": False,
                    "align": "start",
                    "baseline": "alphabetic",
                    "rotate": 0.0,
                }
                top_style = self._derive_xtemplate_style(top_style, t_meta, page_w_mm, page_h_mm, t_params, 0, 0, {})
                self._draw_xtemplate_items(
                    cr, t_items, 0.0, 0.0, page_w_mm, page_h_mm, page_w_mm, page_h_mm,
                    t_params, pattern_rgba, accent_rgba, top_style, t_info, assets
                )
                cr.restore()
            elif t_info.get("surface") is not None:
                surf = t_info["surface"]
                cr.save()
                cr.scale(pw / surf.get_width(), ph / surf.get_height())
                cr.set_source_surface(surf, 0, 0)
                cr.paint()
                cr.restore()

        elif t_ref in ("lines", "dots", "grid", "isometric", "isometric_grid", "isometric_dots", "isometric-dots"):
            spacing_val = style.get("spacing", doc_style.get("spacing", 64.0))
            spacing = XTemplateEvaluator.eval_length(spacing_val, dpi, min(pw, ph)) if isinstance(spacing_val, str) else float(spacing_val)
            cr.set_source_rgba(*pattern_rgba)
            cr.set_line_width(1.2)

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
            elif t_ref in ("isometric", "isometric_grid"):
                dx = spacing * (math.sqrt(3.0) / 2.0)
                x = dx
                while x < pw:
                    cr.move_to(x, 0)
                    cr.line_to(x, ph)
                    x += dx

                tan30 = math.tan(math.radians(30))
                min_b = -pw * tan30
                b = math.floor(min_b / spacing) * spacing
                while b < ph + spacing:
                    cr.move_to(0, b)
                    cr.line_to(pw, pw * tan30 + b)
                    b += spacing

                max_b = ph + pw * tan30
                b = 0.0
                while b < max_b + spacing:
                    cr.move_to(0, b)
                    cr.line_to(pw, -pw * tan30 + b)
                    b += spacing
                cr.stroke()
            elif t_ref in ("isometric_dots", "isometric-dots"):
                dy = spacing * (math.sqrt(3.0) / 2.0)
                row = 0
                y = dy
                while y < ph:
                    x_offset = (spacing / 2.0) if (row % 2 == 1) else spacing
                    x = x_offset
                    while x < pw:
                        cr.arc(x, y, 2.0, 0, 2 * math.pi)
                        cr.fill()
                        x += spacing
                    y += dy
                    row += 1

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

    def draw_page_item(self, cr, item, assets=None, templates=None):
        if not isinstance(item, dict):
            return

        fade_active = False
        audio_fade = getattr(self, "_active_audio_fade", None)
        if audio_fade is not None:
            f_rid, f_playhead = audio_fade
            if f_rid is not None and f_playhead >= 0:
                rec_id = item.get("rec")
                rec_ms = item.get("rec_ms")
                if rec_id is None and isinstance(item.get("sync"), dict):
                    rec_id = item["sync"].get("rid")
                    rec_ms = item["sync"].get("ms")
                if rec_id == f_rid and rec_ms is not None and rec_ms > f_playhead:
                    fade_active = True

        if fade_active:
            cr.save()
            cr.push_group()

        kind = item.get("kind") or item.get("type")
        if kind == "stroke":
            self.draw_stroke(cr, item, is_active=False)
        elif kind == "text":
            self.draw_text(cr, item)
        elif kind in ("image", "picture"):
            self.draw_image(cr, item, assets, templates)
        elif kind == "shape":
            self.draw_shape(cr, item)
        elif kind == "audio":
            self.draw_audio_pill(cr, item)

        if fade_active:
            cr.pop_group_to_source()
            cr.paint_with_alpha(0.25)
            cr.restore()

    def render_page_contents(self, cr, doc, page, pw, ph, assets, templates, page_idx=0, skip_indices=None, with_background=True, with_pattern=True, optimize_for_printing=False):
        self.render_page_background(cr, doc, page, pw, ph, assets, templates, page_idx=page_idx, with_background=with_background, with_pattern=with_pattern, optimize_for_printing=optimize_for_printing)
        for idx, item in enumerate(page.get("items", [])):
            if skip_indices and idx in skip_indices:
                continue
            self.draw_page_item(cr, item, assets, templates)

    def has_cached_thumb(self, page_idx):
        thumb_dict = getattr(self, "thumb_cache", None)
        return bool(thumb_dict and page_idx in thumb_dict)

    def ensure_page_thumb_surface(self, page_idx, page, pw, ph, width, height, doc=None, assets=None, templates=None):
        """
        Rasterize a lightweight, cached thumbnail ImageSurface for `page_idx`.
        Preserves exact page aspect ratio without distortion or cropping.
        """
        thumb_dict = getattr(self, "thumb_cache", None)
        if thumb_dict is None:
            self.thumb_cache = {}
            thumb_dict = self.thumb_cache

        doc = doc if doc is not None else self.document
        assets = assets if assets is not None else self.assets
        templates = templates if templates is not None else self.templates
        if not doc or pw <= 0 or ph <= 0:
            return None

        max_w = max(24, int(round(width)))
        max_h = max(32, int(round(height)))
        scale = min(max_w / pw, max_h / ph)
        w_px = max(24, int(round(pw * scale)))
        h_px = max(32, int(round(ph * scale)))

        cached = thumb_dict.get(page_idx)
        if cached is not None and cached.get_width() == w_px and cached.get_height() == h_px:
            return cached

        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, w_px, h_px)
        cr = cairo.Context(surface)
        cr.save()
        cr.scale(scale, scale)

        radius = 12.0 / scale
        self._rounded_rect(cr, 0, 0, pw, ph, radius)
        cr.clip()

        # Reuse full-page or background cache if already in memory for instant O(1) thumbnail blit
        page_surf = None
        for (p_idx, _z), surf in getattr(self, "page_cache", {}).items():
            if p_idx == page_idx and surf.get_width() > 0 and surf.get_height() > 0:
                page_surf = surf
                break

        if page_surf is not None:
            cr.save()
            cr.scale(pw / page_surf.get_width(), ph / page_surf.get_height())
            cr.set_source_surface(page_surf, 0, 0)
            cr.paint()
            cr.restore()
        else:
            self.render_page_background(cr, doc, page, pw, ph, assets, templates, page_idx=page_idx)
            for item in page.get("items", []):
                if not isinstance(item, dict):
                    continue
                kind = item.get("kind") or item.get("type")
                if kind == "stroke":
                    self.draw_stroke_fast(cr, item)
                elif kind == "text":
                    self.draw_text(cr, item)
                elif kind in ("image", "picture"):
                    self.draw_image(cr, item, assets, templates)
                elif kind == "shape":
                    self.draw_shape(cr, item)
                elif kind == "audio":
                    self.draw_audio_pill(cr, item)

        cr.set_source_rgba(0, 0, 0, 0.18)
        cr.set_line_width(3.0 / scale)
        self._rounded_rect(cr, 0, 0, pw, ph, radius)
        cr.stroke()
        cr.restore()

        surface.flush()
        thumb_dict[page_idx] = surface
        return surface

    def _render_thumb_direct(self, cr, width, height, doc, page, pw, ph, assets, templates, page_idx=0):
        """Render a complete page thumbnail centered cleanly without cropping."""
        scale = min(width / pw, height / ph) if pw > 0 and ph > 0 else 1.0
        ox = (width - pw * scale) / 2.0
        oy = (height - ph * scale) / 2.0
        cr.save()
        cr.translate(ox, oy)
        cr.scale(scale, scale)

        radius = 12.0 / scale
        self._rounded_rect(cr, 0, 0, pw, ph, radius)
        cr.clip()

        self.render_page_background(cr, doc, page, pw, ph, assets, templates, page_idx=page_idx)
        for item in page.get("items", []):
            if not isinstance(item, dict):
                continue
            kind = item.get("kind") or item.get("type")
            if kind == "stroke":
                self.draw_stroke_fast(cr, item)
            elif kind == "text":
                self.draw_text(cr, item)
            elif kind in ("image", "picture"):
                self.draw_image(cr, item, assets, templates)
            elif kind == "shape":
                self.draw_shape(cr, item)
            elif kind == "audio":
                self.draw_audio_pill(cr, item)

        cr.set_source_rgba(0, 0, 0, 0.18)
        cr.set_line_width(3.0 / scale)
        self._rounded_rect(cr, 0, 0, pw, ph, radius)
        cr.stroke()
        cr.restore()

    def draw_canvas_thumb(self, cr, width, height, doc, assets=None, templates=None):
        """Render a clean library thumbnail card for an .xcanvas document."""
        assets = assets if assets is not None else self.assets
        templates = templates if templates is not None else self.templates

        bg = doc.get("background", {}) if isinstance(doc.get("background"), dict) else {}
        paper_col = bg.get("paper_color", [255, 255, 255, 255])
        pattern = bg.get("pattern", "none")
        pat_color = bg.get("pattern_color", [150, 150, 150, 64])
        spacing = float(bg.get("spacing", 64.0))

        radius = 12.0
        self._rounded_rect(cr, 0, 0, width, height, radius)
        cr.save()
        cr.clip()

        cr.set_source_rgba(paper_col[0] / 255.0, paper_col[1] / 255.0, paper_col[2] / 255.0, paper_col[3] / 255.0)
        cr.rectangle(0, 0, width, height)
        cr.fill()

        if pattern != "none":
            cr.set_source_rgba(pat_color[0] / 255.0, pat_color[1] / 255.0, pat_color[2] / 255.0, pat_color[3] / 255.0)
            pat_spacing = max(14.0, min(36.0, spacing * (width / 1240.0) * 4.0))
            if pattern in ("grid", "lines"):
                cr.set_line_width(0.75)
                y = pat_spacing
                while y < height:
                    cr.move_to(0, y)
                    cr.line_to(width, y)
                    y += pat_spacing
                if pattern == "grid":
                    x = pat_spacing
                    while x < width:
                        cr.move_to(x, 0)
                        cr.line_to(x, height)
                        x += pat_spacing
                cr.stroke()
            elif pattern == "dots":
                y = pat_spacing
                while y < height:
                    x = pat_spacing
                    while x < width:
                        cr.arc(x, y, 1.2, 0, 2 * math.pi)
                        cr.fill()
                        x += pat_spacing
                    y += pat_spacing
            elif pattern in ("isometric", "isometric_grid"):
                cr.set_line_width(0.75)
                dx = pat_spacing * (math.sqrt(3.0) / 2.0)
                x = dx
                while x < width:
                    cr.move_to(x, 0)
                    cr.line_to(x, height)
                    x += dx
                tan30 = math.tan(math.radians(30))
                min_b = -width * tan30
                b = math.floor(min_b / pat_spacing) * pat_spacing
                while b < height + pat_spacing:
                    cr.move_to(0, b)
                    cr.line_to(width, width * tan30 + b)
                    b += pat_spacing
                max_b = height + width * tan30
                b = 0.0
                while b < max_b + pat_spacing:
                    cr.move_to(0, b)
                    cr.line_to(width, -width * tan30 + b)
                    b += pat_spacing
                cr.stroke()
            elif pattern in ("isometric_dots", "isometric-dots"):
                dy = pat_spacing * (math.sqrt(3.0) / 2.0)
                row = 0
                y = dy
                while y < height:
                    x_offset = (pat_spacing / 2.0) if (row % 2 == 1) else pat_spacing
                    x = x_offset
                    while x < width:
                        cr.arc(x, y, 1.2, 0, 2 * math.pi)
                        cr.fill()
                        x += pat_spacing
                    y += dy
                    row += 1

        items = doc.get("items", [])
        if not items and doc.get("pages"):
            items = doc["pages"][0].get("items", [])

        if items:
            min_x, min_y, max_x, max_y = 1e9, 1e9, -1e9, -1e9
            for it in items:
                kind = it.get("kind") or it.get("type")
                if kind == "stroke":
                    bb = self._get_or_compute_stroke_bbox(it)
                    if bb:
                        min_x = min(min_x, bb[0])
                        min_y = min(min_y, bb[1])
                        max_x = max(max_x, bb[2])
                        max_y = max(max_y, bb[3])
                elif kind == "text":
                    pos = it.get("pos", [0, 0])
                    w = it.get("width", 200)
                    min_x = min(min_x, pos[0])
                    min_y = min(min_y, pos[1])
                    max_x = max(max_x, pos[0] + w)
                    max_y = max(max_y, pos[1] + 60)
                elif kind in ("image", "picture", "shape", "audio"):
                    r = it.get("rect", [0, 0, 100, 100])
                    min_x = min(min_x, r[0])
                    min_y = min(min_y, r[1])
                    max_x = max(max_x, r[0] + r[2])
                    max_y = max(max_y, r[1] + r[3])

            if min_x < max_x and min_y < max_y:
                bw = max(100.0, max_x - min_x)
                bh = max(100.0, max_y - min_y)
                scale = min((width - 32.0) / bw, (height - 32.0) / bh)
                cr.save()
                cr.translate(width / 2.0, height / 2.0)
                cr.scale(scale, scale)
                cr.translate(-(min_x + max_x) / 2.0, -(min_y + max_y) / 2.0)

                for item in items:
                    kind = item.get("kind") or item.get("type")
                    if kind == "stroke":
                        self.draw_stroke_fast(cr, item)
                    elif kind == "text":
                        self.draw_text(cr, item)
                    elif kind in ("image", "picture"):
                        self.draw_image(cr, item, assets, templates)
                    elif kind == "shape":
                        self.draw_shape(cr, item)
                    elif kind == "audio":
                        self.draw_audio_pill(cr, item)
                cr.restore()
        else:
            cr.save()
            cr.set_source_rgba(0.0, 0.0, 0.0, 0.12)
            cr.set_line_width(2.0)
            cx, cy = width / 2.0, height / 2.0
            cr.arc(cx - 16, cy, 14, 0, 2 * math.pi)
            cr.arc(cx + 16, cy, 14, 0, 2 * math.pi)
            cr.stroke()
            cr.restore()

        cr.restore()

        cr.set_source_rgba(0, 0, 0, 0.18)
        cr.set_line_width(1.5)
        self._rounded_rect(cr, 0, 0, width, height, radius)
        cr.stroke()

    def draw_page_thumb(self, cr, width, height, doc, page, pw, ph, assets=None, templates=None, page_idx=0):
        """
        Render a page or library cover thumbnail.
        - When called for an external library document (`doc is not self.document`), renders
          the cover/first page directly using fast decimated strokes (`_render_thumb_direct`).
        - When called for the active document's page sidebar, blits the cached surface from
          `thumb_cache` in O(1) or draws a lightweight paper placeholder while the `Adw.Spinner`
          and idle queue complete the render.
        """
        assets = assets if assets is not None else self.assets
        templates = templates if templates is not None else self.templates

        if doc is not None and doc is not self.document:
            self._render_thumb_direct(
                cr, width, height, doc, page, pw, ph, assets, templates, page_idx=page_idx
            )
            return

        thumb_dict = getattr(self, "thumb_cache", None)
        if thumb_dict is not None and page_idx in thumb_dict:
            surf = thumb_dict[page_idx]
            if surf is not None:
                sw = surf.get_width()
                sh = surf.get_height()
                if sw > 0 and sh > 0:
                    scale = min(width / float(sw), height / float(sh))
                    ox = (width - sw * scale) / 2.0
                    oy = (height - sh * scale) / 2.0
                    cr.save()
                    cr.translate(ox, oy)
                    cr.scale(scale, scale)
                    cr.set_source_surface(surf, 0, 0)
                    cr.paint()
                    cr.restore()
                    return

        scale = min(width / pw, height / ph) if pw > 0 and ph > 0 else 1.0
        ox = (width - pw * scale) / 2.0
        oy = (height - ph * scale) / 2.0
        cr.save()
        cr.translate(ox, oy)
        cr.scale(scale, scale)

        radius = 12.0 / scale
        self._rounded_rect(cr, 0, 0, pw, ph, radius)
        cr.clip()

        doc_style = doc.get("style", {}) if isinstance(doc, dict) and isinstance(doc.get("style"), dict) else {}
        style = page.get("style", {}) if isinstance(page, dict) and isinstance(page.get("style"), dict) else {}
        bg = style.get("page_color", doc_style.get("page_color", [248, 248, 250, 255]))
        cr.set_source_rgba(bg[0] / 255.0, bg[1] / 255.0, bg[2] / 255.0, bg[3] / 255.0)
        cr.rectangle(0, 0, pw, ph)
        cr.fill()

        cr.set_source_rgba(0, 0, 0, 0.14)
        cr.set_line_width(3.0 / scale)
        self._rounded_rect(cr, 0, 0, pw, ph, radius)
        cr.stroke()
        cr.restore()

    MAX_PAGE_CACHE_ENTRIES = 8

    def _get_cached_bg_surface(self, page_idx, page, pw, ph):
        bg_dict = getattr(self, "bg_cache", None)
        if bg_dict is None:
            self.bg_cache = {}
            bg_dict = self.bg_cache

        cur_zoom = round(self.zoom_level, 4)
        key = (page_idx, cur_zoom)
        if key in bg_dict:
            return bg_dict[key]

        # Evict obsolete surfaces from other zoom levels to free memory
        for sk in [k for k in bg_dict if k[1] != cur_zoom]:
            del bg_dict[sk]

        # Bounded LRU capacity limit
        if len(bg_dict) >= self.MAX_PAGE_CACHE_ENTRIES:
            del bg_dict[next(iter(bg_dict))]

        scaled_w = max(1, int(math.ceil(pw * self.zoom_level)))
        scaled_h = max(1, int(math.ceil(ph * self.zoom_level)))
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, scaled_w, scaled_h)
        cr = cairo.Context(surface)
        cr.scale(self.zoom_level, self.zoom_level)
        cr.rectangle(0, 0, pw, ph)
        cr.clip()
        self.render_page_background(
            cr, self.document, page, pw, ph, self.assets, self.templates, page_idx=page_idx
        )
        surface.flush()
        bg_dict[key] = surface
        return surface

    def _get_cached_page_surface(self, page_idx, page, pw, ph):
        cur_zoom = round(self.zoom_level, 4)
        key = (page_idx, cur_zoom)
        if key in self.page_cache:
            return self.page_cache[key]

        # Evict obsolete surfaces from other zoom levels to free memory
        for sk in [k for k in self.page_cache if k[1] != cur_zoom]:
            del self.page_cache[sk]

        # Bounded LRU capacity limit
        if len(self.page_cache) >= self.MAX_PAGE_CACHE_ENTRIES:
            del self.page_cache[next(iter(self.page_cache))]

        scaled_w = max(1, int(math.ceil(pw * self.zoom_level)))
        scaled_h = max(1, int(math.ceil(ph * self.zoom_level)))
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, scaled_w, scaled_h)
        cr = cairo.Context(surface)

        # Blit pre-rendered background (PDF + xtemplate) in O(1)
        bg_surf = self._get_cached_bg_surface(page_idx, page, pw, ph)
        cr.set_source_surface(bg_surf, 0, 0)
        cr.paint()

        cr.scale(self.zoom_level, self.zoom_level)
        cr.rectangle(0, 0, pw, ph)
        cr.clip()

        skip = self._transient_skip_indices if getattr(self, "_transient_skip_page", None) == page_idx else None
        for idx, item in enumerate(page.get("items", [])):
            if skip and idx in skip:
                continue
            self.draw_page_item(cr, item, self.assets, self.templates)

        surface.flush()
        self.page_cache[key] = surface
        return surface

    def append_stroke_to_cached_page(self, page_idx, stroke):
        """Incrementally paint a newly finished stroke onto the cached page surface in O(1) time."""
        key = (page_idx, round(self.zoom_level, 4))
        surface = self.page_cache.get(key)
        if surface is not None:
            pw, ph = self.get_page_size(page_idx)
            cr = cairo.Context(surface)
            cr.scale(self.zoom_level, self.zoom_level)
            cr.rectangle(0, 0, pw, ph)
            cr.clip()
            self.draw_stroke(cr, stroke, is_active=False)
            surface.flush()

    def draw_audio_pill(self, cr, item, is_preview=False):
        rect = item.get("rect")
        if not rect or len(rect) < 4:
            return
        x, y, w, h = float(rect[0]), float(rect[1]), float(rect[2]), float(rect[3])
        if w <= 10.0 or h <= 10.0:
            return

        cr.save()
        # Capsule body
        r = h / 2.0
        self._rounded_rect(cr, x, y, w, h, r)
        # Background: subtle modern card
        cr.set_source_rgba(0.96, 0.97, 0.98, 0.95)
        cr.fill_preserve()
        cr.set_source_rgba(0.2, 0.25, 0.3, 0.20)
        cr.set_line_width(1.5)
        cr.stroke()

        # Play/Pause disc on left
        disc_r = r * 0.72
        disc_cx = x + r
        disc_cy = y + r
        cr.arc(disc_cx, disc_cy, disc_r, 0, 2.0 * math.pi)
        cr.set_source_rgba(0.20, 0.52, 0.89, 1.0)  # GNOME Accent Blue
        cr.fill()

        # Play/Pause icon inside disc
        playing = bool(item.get("playing", False))
        cr.set_source_rgba(1.0, 1.0, 1.0, 1.0)
        if playing:
            bar_w = disc_r * 0.28
            bar_h = disc_r * 0.95
            gap = disc_r * 0.28
            top = disc_cy - bar_h / 2.0
            cr.rectangle(disc_cx - gap / 2.0 - bar_w, top, bar_w, bar_h)
            cr.rectangle(disc_cx + gap / 2.0, top, bar_w, bar_h)
            cr.fill()
        else:
            pr = disc_r * 0.55
            cr.move_to(disc_cx - pr * 0.5, disc_cy - pr * 0.8)
            cr.line_to(disc_cx + pr * 0.85, disc_cy)
            cr.line_to(disc_cx - pr * 0.5, disc_cy + pr * 0.8)
            cr.close_path()
            cr.fill()

        # Text labels: Name and Time
        name = item.get("name", "Voice Note")
        duration_ms = int(item.get("duration_ms", 0))
        playhead = int(item.get("playhead", -1))

        def _fmt_t(ms):
            sec = max(0, int(ms / 1000))
            return f"{sec // 60:02d}:{sec % 60:02d}"

        if playhead >= 0:
            time_str = f"{_fmt_t(playhead)} / {_fmt_t(duration_ms)}"
        else:
            time_str = _fmt_t(duration_ms)

        text_x = x + 2.0 * r + 4.0
        text_y = y + h * 0.38

        # Draw Name
        cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
        cr.set_font_size(max(9.0, h * 0.25))
        cr.set_source_rgba(0.15, 0.18, 0.22, 1.0)
        cr.move_to(text_x, text_y)
        cr.show_text(name[:22] + ("…" if len(name) > 22 else ""))

        # Draw Time right aligned
        cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
        cr.set_font_size(max(8.0, h * 0.21))
        cr.set_source_rgba(0.45, 0.48, 0.52, 1.0)
        ext = cr.text_extents(time_str)
        time_x = x + w - r - ext.width
        cr.move_to(max(text_x + 60.0, time_x), text_y)
        cr.show_text(time_str)

        # Waveform bars
        wave_x0 = text_x
        wave_x1 = x + w - r
        wave_y = y + h * 0.74
        wave_h = h * 0.32
        bar_w = max(2.0, h * 0.055)
        bar_gap = max(1.5, h * 0.035)
        step = bar_w + bar_gap
        num_bars = int(max(1, (wave_x1 - wave_x0) / step))

        peaks_raw = item.get("peaks")
        peaks = []
        if isinstance(peaks_raw, str) and peaks_raw:
            try:
                peaks = list(base64.b64decode(peaks_raw))
            except Exception:
                pass
        elif isinstance(peaks_raw, (bytes, bytearray, list)):
            peaks = list(peaks_raw)

        played_idx = 0
        if playhead >= 0 and duration_ms > 0:
            played_idx = int((playhead / float(duration_ms)) * num_bars)

        for i in range(num_bars):
            if peaks:
                p_idx = int((i / float(num_bars)) * len(peaks))
                level = peaks[min(len(peaks) - 1, p_idx)] / 255.0
            else:
                level = 0.35 + 0.3 * math.sin(i * 0.4)

            bh = max(bar_w, level * wave_h)
            bx = wave_x0 + i * step
            by = wave_y - bh / 2.0

            if i <= played_idx and playhead >= 0:
                cr.set_source_rgba(0.20, 0.52, 0.89, 0.95)
            else:
                cr.set_source_rgba(0.25, 0.28, 0.32, 0.30)

            self._rounded_rect(cr, bx, by, bar_w, bh, bar_w / 2.0)
            cr.fill()

        if is_preview:
            cr.set_source_rgba(0.2, 0.5, 0.9, 0.45)
            cr.set_line_width(1.0)
            cr.set_dash([4.0, 3.0])
            self._rounded_rect(cr, x, y, w, h, r)
            cr.stroke()
            cr.set_dash([])

        cr.restore()

    def draw_shape(self, cr, item, is_preview=False):
        rect = item.get("rect")
        if not rect or len(rect) < 4:
            return
        x, y, w, h = float(rect[0]), float(rect[1]), float(rect[2]), float(rect[3])
        if w <= 0.0 and h <= 0.0:
            return
        w = max(1.0, w)
        h = max(1.0, h)

        rgba = item.get("rgba") or [0, 0, 0, 255]
        r = rgba[0] / 255.0
        g = rgba[1] / 255.0
        b = rgba[2] / 255.0
        a = rgba[3] / 255.0 if len(rgba) >= 4 else 1.0
        if is_preview:
            a = min(1.0, a * 0.88)

        stroke_w = float(item.get("stroke_width", 3.5))
        fill = bool(item.get("fill", False))
        shape_type = item.get("shape_type", "rectangle")
        angle = float(item.get("angle", 0.0))

        cx = x + w / 2.0
        cy = y + h / 2.0
        hw = w / 2.0
        hh = h / 2.0

        cr.save()
        cr.translate(cx, cy)
        if angle != 0.0:
            cr.rotate(angle)

        cr.set_line_width(stroke_w)
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        cr.set_line_join(cairo.LINE_JOIN_ROUND)

        def _do_fill_and_stroke(close=True):
            if close:
                cr.close_path()
            if fill:
                cr.set_source_rgba(r, g, b, 0.22 if is_preview or not item.get("fill_solid") else a)
                cr.fill_preserve()
            cr.set_source_rgba(r, g, b, a)
            cr.stroke()

        if shape_type in ("rectangle", "rect", "square", "flow_process"):
            cr.rectangle(-hw, -hh, w, h)
            _do_fill_and_stroke(close=False)

        elif shape_type in ("rounded_rect", "flow_alt_process"):
            rad = min(hw, hh) * 0.25
            self._rounded_rect(cr, -hw, -hh, w, h, rad)
            _do_fill_and_stroke(close=False)

        elif shape_type in ("capsule", "flow_terminator"):
            if hw >= hh:
                rad = hh
                cr.arc(hw - rad, 0, rad, -math.pi / 2.0, math.pi / 2.0)
                cr.arc(-hw + rad, 0, rad, math.pi / 2.0, 3.0 * math.pi / 2.0)
            else:
                rad = hw
                cr.arc(0, -hh + rad, rad, math.pi, 2.0 * math.pi)
                cr.arc(0, hh - rad, rad, 0.0, math.pi)
            _do_fill_and_stroke(close=True)

        elif shape_type in ("ellipse", "circle", "flow_connector"):
            cr.save()
            cr.scale(max(1e-4, hw), max(1e-4, hh))
            cr.arc(0.0, 0.0, 1.0, 0.0, 2.0 * math.pi)
            cr.restore()
            _do_fill_and_stroke(close=False)

        elif shape_type == "ring":
            cr.save()
            cr.scale(max(1e-4, hw), max(1e-4, hh))
            cr.arc(0.0, 0.0, 1.0, 0.0, 2.0 * math.pi)
            cr.new_sub_path()
            cr.arc_negative(0.0, 0.0, 0.6, 2.0 * math.pi, 0.0)
            cr.restore()
            _do_fill_and_stroke(close=False)

        elif shape_type == "pie":
            cr.move_to(0, 0)
            cr.save()
            cr.scale(max(1e-4, hw), max(1e-4, hh))
            cr.arc(0.0, 0.0, 1.0, -math.pi / 4.0, math.pi * 3.0 / 4.0)
            cr.restore()
            cr.close_path()
            _do_fill_and_stroke(close=False)

        elif shape_type == "arc":
            cr.save()
            cr.scale(max(1e-4, hw), max(1e-4, hh))
            cr.arc(0.0, 0.0, 1.0, 0.0, math.pi)
            cr.restore()
            cr.set_source_rgba(r, g, b, a)
            cr.stroke()

        elif shape_type in ("diamond", "flow_decision"):
            cr.move_to(0.0, -hh)
            cr.line_to(hw, 0.0)
            cr.line_to(0.0, hh)
            cr.line_to(-hw, 0.0)
            _do_fill_and_stroke(close=True)

        elif shape_type in ("parallelogram", "flow_data"):
            s = hw * 0.4
            cr.move_to(-hw + s, -hh)
            cr.line_to(hw, -hh)
            cr.line_to(hw - s, hh)
            cr.line_to(-hw, hh)
            _do_fill_and_stroke(close=True)

        elif shape_type == "trapezoid":
            s = hw * 0.35
            cr.move_to(-hw + s, -hh)
            cr.line_to(hw - s, -hh)
            cr.line_to(hw, hh)
            cr.line_to(-hw, hh)
            _do_fill_and_stroke(close=True)

        elif shape_type in ("triangle", "flow_extract"):
            cr.move_to(0.0, -hh)
            cr.line_to(hw, hh)
            cr.line_to(-hw, hh)
            _do_fill_and_stroke(close=True)

        elif shape_type == "flow_merge":
            cr.move_to(0.0, hh)
            cr.line_to(hw, -hh)
            cr.line_to(-hw, -hh)
            _do_fill_and_stroke(close=True)

        elif shape_type == "right_triangle":
            cr.move_to(-hw, -hh)
            cr.line_to(hw, hh)
            cr.line_to(-hw, hh)
            _do_fill_and_stroke(close=True)

        elif shape_type in ("regular_polygon", "pentagon", "hexagon", "octagon"):
            n = 5 if shape_type == "pentagon" else (6 if shape_type == "hexagon" else (8 if shape_type == "octagon" else int(item.get("n", 5))))
            cr.new_sub_path()
            for i in range(n):
                ang = 2.0 * math.pi * i / n - math.pi / 2.0
                px_s = hw * math.cos(ang)
                py_s = hh * math.sin(ang)
                if i == 0:
                    cr.move_to(px_s, py_s)
                else:
                    cr.line_to(px_s, py_s)
            _do_fill_and_stroke(close=True)

        elif shape_type == "star":
            outer_r = min(hw, hh)
            inner_r = outer_r * 0.40
            cr.new_sub_path()
            for i in range(10):
                ang = i * math.pi / 5.0 - math.pi / 2.0
                radius = outer_r if i % 2 == 0 else inner_r
                px_s = radius * math.cos(ang)
                py_s = radius * math.sin(ang)
                if i == 0:
                    cr.move_to(px_s, py_s)
                else:
                    cr.line_to(px_s, py_s)
            _do_fill_and_stroke(close=True)

        elif shape_type == "cross":
            tw = min(hw, hh) * 0.35
            cr.move_to(-tw, -hh)
            cr.line_to(tw, -hh)
            cr.line_to(tw, -tw)
            cr.line_to(hw, -tw)
            cr.line_to(hw, tw)
            cr.line_to(tw, tw)
            cr.line_to(tw, hh)
            cr.line_to(-tw, hh)
            cr.line_to(-tw, tw)
            cr.line_to(-hw, tw)
            cr.line_to(-hw, -tw)
            cr.line_to(-tw, -tw)
            _do_fill_and_stroke(close=True)

        elif shape_type == "heart":
            def _hat(fx, fy):
                return -hw + fx * w, -hh + fy * h
            cr.move_to(*_hat(0.5, 0.22))
            cr.curve_to(*_hat(0.5, 0.08), *_hat(0.38, 0.0), *_hat(0.25, 0.0))
            cr.curve_to(*_hat(0.1, 0.0), *_hat(0.0, 0.12), *_hat(0.0, 0.3))
            cr.curve_to(*_hat(0.0, 0.55), *_hat(0.25, 0.75), *_hat(0.5, 1.0))
            cr.curve_to(*_hat(0.75, 0.75), *_hat(1.0, 0.55), *_hat(1.0, 0.3))
            cr.curve_to(*_hat(1.0, 0.12), *_hat(0.9, 0.0), *_hat(0.75, 0.0))
            cr.curve_to(*_hat(0.62, 0.0), *_hat(0.5, 0.08), *_hat(0.5, 0.22))
            _do_fill_and_stroke(close=True)

        elif shape_type == "cloud":
            turns = [0.0, 0.11, 0.24, 0.36, 0.47, 0.6, 0.71, 0.83, 0.93]
            cr.new_sub_path()
            for i in range(len(turns)):
                t1 = turns[i]
                t2 = turns[(i + 1) % len(turns)]
                if t2 < t1:
                    t2 += 1.0
                a1 = 2.0 * math.pi * t1 - math.pi / 2.0
                a2 = 2.0 * math.pi * t2 - math.pi / 2.0
                amid = (a1 + a2) / 2.0
                p1 = (hw * math.cos(a1), hh * math.sin(a1))
                p2 = (hw * math.cos(a2), hh * math.sin(a2))
                ctrl = (hw * 1.30 * math.cos(amid), hh * 1.30 * math.sin(amid))
                if i == 0:
                    cr.move_to(p1[0], p1[1])
                cr.curve_to(ctrl[0], ctrl[1], ctrl[0], ctrl[1], p2[0], p2[1])
            _do_fill_and_stroke(close=True)

        elif shape_type == "arrow_right":
            cr.move_to(-hw, -hh * 0.4)
            cr.line_to(0.0, -hh * 0.4)
            cr.line_to(0.0, -hh)
            cr.line_to(hw, 0.0)
            cr.line_to(0.0, hh)
            cr.line_to(0.0, hh * 0.4)
            cr.line_to(-hw, hh * 0.4)
            _do_fill_and_stroke(close=True)

        elif shape_type == "arrow_left":
            cr.move_to(hw, -hh * 0.4)
            cr.line_to(0.0, -hh * 0.4)
            cr.line_to(0.0, -hh)
            cr.line_to(-hw, 0.0)
            cr.line_to(0.0, hh)
            cr.line_to(0.0, hh * 0.4)
            cr.line_to(hw, hh * 0.4)
            _do_fill_and_stroke(close=True)

        elif shape_type == "arrow_up":
            cr.move_to(-hw * 0.4, hh)
            cr.line_to(-hw * 0.4, 0.0)
            cr.line_to(-hw, 0.0)
            cr.line_to(0.0, -hh)
            cr.line_to(hw, 0.0)
            cr.line_to(hw * 0.4, 0.0)
            cr.line_to(hw * 0.4, hh)
            _do_fill_and_stroke(close=True)

        elif shape_type == "arrow_down":
            cr.move_to(-hw * 0.4, -hh)
            cr.line_to(-hw * 0.4, 0.0)
            cr.line_to(-hw, 0.0)
            cr.line_to(0.0, hh)
            cr.line_to(hw, 0.0)
            cr.line_to(hw * 0.4, 0.0)
            cr.line_to(hw * 0.4, -hh)
            _do_fill_and_stroke(close=True)

        elif shape_type in ("speech_bubble", "callout_rect", "callout_round"):
            body_h = h * 0.76
            body_hh = body_h / 2.0
            rad = min(hw, body_hh) * 0.35
            cr.move_to(-hw + rad, -hh)
            cr.line_to(hw - rad, -hh)
            cr.arc(hw - rad, -hh + rad, rad, -math.pi / 2.0, 0.0)
            cr.line_to(hw, -hh + body_h - rad)
            cr.arc(hw - rad, -hh + body_h - rad, rad, 0.0, math.pi / 2.0)
            cr.line_to(-hw * 0.2, -hh + body_h)
            cr.line_to(-hw * 0.55, hh)
            cr.line_to(-hw * 0.5, -hh + body_h)
            cr.line_to(-hw + rad, -hh + body_h)
            cr.arc(-hw + rad, -hh + body_h - rad, rad, math.pi / 2.0, math.pi)
            cr.line_to(-hw, -hh + rad)
            cr.arc(-hw + rad, -hh + rad, rad, math.pi, 3.0 * math.pi / 2.0)
            _do_fill_and_stroke(close=True)

        elif shape_type == "flow_preparation":
            s = hw * 0.35
            cr.move_to(-hw + s, -hh)
            cr.line_to(hw - s, -hh)
            cr.line_to(hw, 0.0)
            cr.line_to(hw - s, hh)
            cr.line_to(-hw + s, hh)
            cr.line_to(-hw, 0.0)
            _do_fill_and_stroke(close=True)

        elif shape_type == "flow_predefined":
            cr.rectangle(-hw, -hh, w, h)
            _do_fill_and_stroke(close=False)
            cr.move_to(-hw + w * 0.15, -hh)
            cr.line_to(-hw + w * 0.15, hh)
            cr.move_to(hw - w * 0.15, -hh)
            cr.line_to(hw - w * 0.15, hh)
            cr.set_source_rgba(r, g, b, a)
            cr.stroke()

        elif shape_type == "flow_document":
            cr.move_to(-hw, -hh)
            cr.line_to(hw, -hh)
            cr.line_to(hw, hh - h * 0.18)
            cr.curve_to(hw * 0.45, hh + h * 0.14, -hw * 0.45, hh - h * 0.32, -hw, hh - h * 0.10)
            _do_fill_and_stroke(close=True)

        elif shape_type == "flow_database":
            ry = hh * 0.28
            cr.save()
            cr.translate(0, -hh + ry)
            cr.scale(hw, ry)
            cr.arc(0, 0, 1.0, 0, 2 * math.pi)
            cr.restore()
            _do_fill_and_stroke(close=False)

            cr.move_to(-hw, -hh + ry)
            cr.line_to(-hw, hh - ry)
            cr.save()
            cr.translate(0, hh - ry)
            cr.scale(hw, ry)
            cr.arc(0, 0, 1.0, 0, math.pi)
            cr.restore()
            cr.line_to(hw, -hh + ry)
            _do_fill_and_stroke(close=False)

        elif shape_type in ("solid_cube", "cube"):
            d = min(w, h) * 0.26
            # Front face
            cr.rectangle(-hw, -hh + d, w - d, h - d)
            _do_fill_and_stroke(close=False)
            # Top face
            cr.move_to(-hw, -hh + d)
            cr.line_to(-hw + d, -hh)
            cr.line_to(hw, -hh)
            cr.line_to(hw - d, -hh + d)
            _do_fill_and_stroke(close=True)
            # Right face
            cr.move_to(hw - d, -hh + d)
            cr.line_to(hw, -hh)
            cr.line_to(hw, hh - d)
            cr.line_to(hw - d, hh)
            _do_fill_and_stroke(close=True)
            # Dashed hidden back lines
            cr.save()
            cr.set_dash([4.0, 4.0])
            cr.move_to(-hw + d, -hh)
            cr.line_to(-hw + d, hh)
            cr.line_to(hw, hh - d)
            cr.move_to(-hw + d, hh)
            cr.line_to(-hw, hh)
            cr.set_source_rgba(r, g, b, a * 0.5)
            cr.stroke()
            cr.restore()

        elif shape_type in ("solid_cylinder", "cylinder"):
            ry = hh * 0.26
            # Top ellipse
            cr.save()
            cr.translate(0, -hh + ry)
            cr.scale(hw, ry)
            cr.arc(0, 0, 1.0, 0, 2 * math.pi)
            cr.restore()
            _do_fill_and_stroke(close=False)
            # Body
            cr.move_to(-hw, -hh + ry)
            cr.line_to(-hw, hh - ry)
            cr.save()
            cr.translate(0, hh - ry)
            cr.scale(hw, ry)
            cr.arc(0, 0, 1.0, 0, math.pi)
            cr.restore()
            cr.line_to(hw, -hh + ry)
            _do_fill_and_stroke(close=False)
            # Dashed back curve
            cr.save()
            cr.set_dash([4.0, 4.0])
            cr.translate(0, hh - ry)
            cr.scale(hw, ry)
            cr.arc(0, 0, 1.0, math.pi, 2 * math.pi)
            cr.set_source_rgba(r, g, b, a * 0.5)
            cr.stroke()
            cr.restore()

        elif shape_type in ("solid_cone", "cone"):
            ry = hh * 0.26
            cr.move_to(0, -hh)
            cr.line_to(hw, hh - ry)
            cr.save()
            cr.translate(0, hh - ry)
            cr.scale(hw, ry)
            cr.arc(0, 0, 1.0, 0, math.pi)
            cr.restore()
            cr.line_to(0, -hh)
            _do_fill_and_stroke(close=True)
            # Dashed back curve
            cr.save()
            cr.set_dash([4.0, 4.0])
            cr.translate(0, hh - ry)
            cr.scale(hw, ry)
            cr.arc(0, 0, 1.0, math.pi, 2 * math.pi)
            cr.set_source_rgba(r, g, b, a * 0.5)
            cr.stroke()
            cr.restore()

        elif shape_type in ("solid_sphere", "sphere"):
            cr.arc(0, 0, min(hw, hh), 0, 2 * math.pi)
            _do_fill_and_stroke(close=False)
            # Equator ellipse
            cr.save()
            cr.scale(min(hw, hh), min(hw, hh) * 0.32)
            cr.arc(0, 0, 1.0, 0, math.pi)
            cr.set_source_rgba(r, g, b, a)
            cr.stroke()
            cr.set_dash([4.0, 4.0])
            cr.arc(0, 0, 1.0, math.pi, 2 * math.pi)
            cr.set_source_rgba(r, g, b, a * 0.5)
            cr.stroke()
            cr.restore()

        elif shape_type in ("math_axes_2d", "coordinate_system"):
            cr.set_source_rgba(r, g, b, a)
            ox = -hw * 0.75
            oy = hh * 0.75
            # X axis
            cr.move_to(ox, oy)
            cr.line_to(hw * 0.9, oy)
            cr.stroke()
            # X arrow
            cr.move_to(hw * 0.9 - 10, oy - 6)
            cr.line_to(hw * 0.9, oy)
            cr.line_to(hw * 0.9 - 10, oy + 6)
            cr.stroke()
            # Y axis
            cr.move_to(ox, oy)
            cr.line_to(ox, -hh * 0.9)
            cr.stroke()
            # Y arrow
            cr.move_to(ox - 6, -hh * 0.9 + 10)
            cr.line_to(ox, -hh * 0.9)
            cr.line_to(ox + 6, -hh * 0.9 + 10)
            cr.stroke()

        elif shape_type == "math_axes_3d":
            cr.set_source_rgba(r, g, b, a)
            ox, oy = 0, hh * 0.2
            # Z up
            cr.move_to(ox, oy)
            cr.line_to(ox, -hh * 0.9)
            cr.stroke()
            # X right-down
            cr.move_to(ox, oy)
            cr.line_to(hw * 0.85, oy + hh * 0.45)
            cr.stroke()
            # Y left-down
            cr.move_to(ox, oy)
            cr.line_to(-hw * 0.85, oy + hh * 0.45)
            cr.stroke()

        elif shape_type in ("math_curly_brace", "brace"):
            cr.set_source_rgba(r, g, b, a)
            cr.move_to(0, -hh)
            cr.curve_to(hw * 0.4, -hh * 0.9, hw * 0.4, -hh * 0.1, hw * 0.85, 0.0)
            cr.curve_to(hw * 0.4, hh * 0.1, hw * 0.4, hh * 0.9, 0, hh)
            cr.stroke()

        elif shape_type == "line":
            start_pt = item.get("start_pt")
            end_pt = item.get("end_pt")
            if start_pt and end_pt:
                sx = 1.0 if end_pt[0] >= start_pt[0] else -1.0
                sy = 1.0 if end_pt[1] >= start_pt[1] else -1.0
                p1x, p1y = -hw * sx, -hh * sy
                p2x, p2y = hw * sx, hh * sy
            else:
                p1x, p1y = -hw, -hh
                p2x, p2y = hw, hh
            cr.set_source_rgba(r, g, b, a)
            cr.move_to(p1x, p1y)
            cr.line_to(p2x, p2y)
            cr.stroke()

        elif shape_type in ("arrow", "double_arrow", "arrow_left_right"):
            start_pt = item.get("start_pt")
            end_pt = item.get("end_pt")
            if start_pt and end_pt:
                sx = 1.0 if end_pt[0] >= start_pt[0] else -1.0
                sy = 1.0 if end_pt[1] >= start_pt[1] else -1.0
                p1x, p1y = -hw * sx, -hh * sy
                p2x, p2y = hw * sx, hh * sy
            else:
                p1x, p1y = -hw, -hh
                p2x, p2y = hw, hh

            cr.set_source_rgba(r, g, b, a)
            cr.move_to(p1x, p1y)
            cr.line_to(p2x, p2y)
            cr.stroke()

            arrow_len = min(40.0, max(12.0, stroke_w * 3.6))
            arrow_angle = math.radians(28.0)
            dir_angle = math.atan2(p2y - p1y, p2x - p1x)
            ah1_x = p2x - arrow_len * math.cos(dir_angle - arrow_angle)
            ah1_y = p2y - arrow_len * math.sin(dir_angle - arrow_angle)
            ah2_x = p2x - arrow_len * math.cos(dir_angle + arrow_angle)
            ah2_y = p2y - arrow_len * math.sin(dir_angle + arrow_angle)

            cr.move_to(ah1_x, ah1_y)
            cr.line_to(p2x, p2y)
            cr.line_to(ah2_x, ah2_y)
            cr.stroke()

            if shape_type in ("double_arrow", "arrow_left_right"):
                back_dir = dir_angle + math.pi
                bh1_x = p1x - arrow_len * math.cos(back_dir - arrow_angle)
                bh1_y = p1y - arrow_len * math.sin(back_dir - arrow_angle)
                bh2_x = p1x - arrow_len * math.cos(back_dir + arrow_angle)
                bh2_y = p1y - arrow_len * math.sin(back_dir + arrow_angle)
                cr.move_to(bh1_x, bh1_y)
                cr.line_to(p1x, p1y)
                cr.line_to(bh2_x, bh2_y)
                cr.stroke()

        if is_preview:
            cr.set_source_rgba(0.2, 0.5, 0.9, 0.45)
            cr.set_line_width(1.0)
            cr.set_dash([4.0, 3.0])
            cr.rectangle(-hw, -hh, w, h)
            cr.stroke()
            cr.set_dash([])

        cr.restore()

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

    _font_desc_cache = {}

    @classmethod
    def _get_cached_font_desc(cls, font_family, point_size):
        key = (str(font_family), round(float(point_size), 1))
        desc = cls._font_desc_cache.get(key)
        if desc is None:
            desc = Pango.FontDescription.from_string(f"{font_family} {point_size}px")
            cls._font_desc_cache[key] = desc
        return desc

    def draw_text(self, cr, text_item):
        text = text_item.get("text", "")
        if not text:
            return

        rect = text_item.get("rect")
        if rect and len(rect) >= 4:
            x, y, width, height = float(rect[0]), float(rect[1]), float(rect[2]), float(rect[3])
        else:
            pos = text_item.get("pos", [text_item.get("x", 0.0), text_item.get("y", 0.0)])
            x = float(pos[0]) if len(pos) > 0 else 0.0
            y = float(pos[1]) if len(pos) > 1 else 0.0
            width = float(text_item.get("width", 260.0))
            height = float(text_item.get("height", 50.0))

        angle = float(text_item.get("angle", 0.0))
        if abs(angle) > 1e-4:
            cr.save()
            cx = x + width / 2.0
            cy = y + height / 2.0
            cr.translate(cx, cy)
            cr.rotate(angle)
            cr.translate(-cx, -cy)

        rgba = text_item.get("rgba", text_item.get("color", [30, 30, 30, 255]))
        r, g, b, a = rgba[0] / 255.0, rgba[1] / 255.0, rgba[2] / 255.0, rgba[3] / 255.0
        cr.set_source_rgba(r, g, b, a)

        font_family = text_item.get("font_family", text_item.get("font", "Sans"))
        point_size = float(text_item.get("point_size", text_item.get("font_size", 14.0)))

        layout = PangoCairo.create_layout(cr)
        layout.set_text(text, -1)
        font_desc = self._get_cached_font_desc(font_family, point_size)
        layout.set_font_description(font_desc)
        if width > 0:
            layout.set_width(int(width * Pango.SCALE))
            layout.set_wrap(Pango.WrapMode.WORD_CHAR)

        cr.move_to(x, y)
        PangoCairo.show_layout(cr, layout)

        ink_rect, log_rect = layout.get_pixel_extents()
        measured_h = max(24.0, float(log_rect.height))
        if "rect" not in text_item:
            text_item["rect"] = [x, y, max(40.0, width), measured_h]
        else:
            text_item["rect"][3] = max(text_item["rect"][3], measured_h)

        if abs(angle) > 1e-4:
            cr.restore()

    # =========================================================================
    # Smooth Continuous-Spline & Tangent-Envelope Stroke Engine
    # =========================================================================
    @staticmethod
    def _trace_quadratic_midpoint_spline(cr, pts):
        """Trace a C1-continuous quadratic Bezier spline through sample midpoints."""
        n = len(pts)
        if n == 0:
            return
        if n == 1:
            cr.move_to(pts[0][0], pts[0][1])
            cr.line_to(pts[0][0] + 0.01, pts[0][1] + 0.01)
            return
        if n == 2:
            cr.move_to(pts[0][0], pts[0][1])
            cr.line_to(pts[1][0], pts[1][1])
            return

        cr.move_to(pts[0][0], pts[0][1])
        mid0_x = 0.5 * (pts[0][0] + pts[1][0])
        mid0_y = 0.5 * (pts[0][1] + pts[1][1])
        cr.line_to(mid0_x, mid0_y)

        for i in range(1, n - 1):
            px, py = pts[i][0], pts[i][1]
            nx, ny = pts[i + 1][0], pts[i + 1][1]
            mx = 0.5 * (px + nx)
            my = 0.5 * (py + ny)
            # Elevate quadratic Bezier (control point P_i, endpoint M_i) to Cairo cubic Bezier
            cx1, cy1 = cr.get_current_point()
            c1x = cx1 + (2.0 / 3.0) * (px - cx1)
            c1y = cy1 + (2.0 / 3.0) * (py - cy1)
            c2x = mx + (2.0 / 3.0) * (px - mx)
            c2y = my + (2.0 / 3.0) * (py - my)
            cr.curve_to(c1x, c1y, c2x, c2y, mx, my)

        cr.line_to(pts[-1][0], pts[-1][1])

    @classmethod
    def _draw_variable_width_envelope(cls, cr, pts, radii):
        """
        Build a continuous, smooth closed contour around variable-radius spine points
        using exact circle-to-circle tangents and quadratic midpoint smoothing.
        """
        n = len(pts)
        if n == 0:
            return
        if n == 1:
            cr.arc(pts[0][0], pts[0][1], radii[0], 0.0, 2.0 * math.pi)
            return

        # Filter out overlapping degenerate points
        f_pts = [pts[0]]
        f_rad = [radii[0]]
        for i in range(1, n):
            dx = pts[i][0] - f_pts[-1][0]
            dy = pts[i][1] - f_pts[-1][1]
            if dx * dx + dy * dy >= 0.04 or i == n - 1:
                f_pts.append(pts[i])
                f_rad.append(radii[i])

        m = len(f_pts)
        if m == 1:
            cr.arc(f_pts[0][0], f_pts[0][1], f_rad[0], 0.0, 2.0 * math.pi)
            return

        left_pts = []
        right_pts = []

        for i in range(m):
            x, y = f_pts[i][0], f_pts[i][1]
            r = f_rad[i]
            if i == 0:
                dx = f_pts[1][0] - x
                dy = f_pts[1][1] - y
                dr = f_rad[1] - r
            elif i == m - 1:
                dx = x - f_pts[m - 2][0]
                dy = y - f_pts[m - 2][1]
                dr = r - f_rad[m - 2]
            else:
                dx = f_pts[i + 1][0] - f_pts[i - 1][0]
                dy = f_pts[i + 1][1] - f_pts[i - 1][1]
                dr = (f_rad[i + 1] - f_rad[i - 1]) * 0.5

            dist = math.hypot(dx, dy)
            if dist < 1e-4:
                nx, ny = 0.0, 1.0
            else:
                ux, uy = dx / dist, dy / dist
                # Account for radius taper angle along segment
                sin_beta = max(-0.85, min(0.85, -dr / dist))
                cos_beta = math.sqrt(max(0.0, 1.0 - sin_beta * sin_beta))
                # Left normal and right normal
                lx_n = ux * sin_beta - uy * cos_beta
                ly_n = uy * sin_beta + ux * cos_beta
                rx_n = ux * sin_beta + uy * cos_beta
                ry_n = uy * sin_beta - ux * cos_beta
                left_pts.append((x + lx_n * r, y + ly_n * r))
                right_pts.append((x + rx_n * r, y + ry_n * r))
                continue

            left_pts.append((x - ny * r, y + nx * r))
            right_pts.append((x + ny * r, y - nx * r))

        cr.set_fill_rule(cairo.FILL_RULE_WINDING)
        cr.new_path()

        # Start cap & left contour
        cls._trace_quadratic_midpoint_spline(cr, left_pts)

        # End round cap around f_pts[-1]
        end_x, end_y = f_pts[-1][0], f_pts[-1][1]
        end_r = f_rad[-1]
        ang_l = math.atan2(left_pts[-1][1] - end_y, left_pts[-1][0] - end_x)
        ang_r = math.atan2(right_pts[-1][1] - end_y, right_pts[-1][0] - end_x)
        cr.arc_negative(end_x, end_y, end_r, ang_l, ang_r)

        # Right contour in reverse
        rev_right = list(reversed(right_pts))
        n_r = len(rev_right)
        if n_r >= 3:
            mid0_x = 0.5 * (rev_right[0][0] + rev_right[1][0])
            mid0_y = 0.5 * (rev_right[0][1] + rev_right[1][1])
            cr.line_to(mid0_x, mid0_y)
            for i in range(1, n_r - 1):
                px, py = rev_right[i][0], rev_right[i][1]
                nx, ny = rev_right[i + 1][0], rev_right[i + 1][1]
                mx = 0.5 * (px + nx)
                my = 0.5 * (py + ny)
                cx1, cy1 = cr.get_current_point()
                c1x = cx1 + (2.0 / 3.0) * (px - cx1)
                c1y = cy1 + (2.0 / 3.0) * (py - cy1)
                c2x = mx + (2.0 / 3.0) * (px - mx)
                c2y = my + (2.0 / 3.0) * (py - my)
                cr.curve_to(c1x, c1y, c2x, c2y, mx, my)
            cr.line_to(rev_right[-1][0], rev_right[-1][1])
        else:
            for pt in rev_right:
                cr.line_to(pt[0], pt[1])

        # Start round cap around f_pts[0]
        start_x, start_y = f_pts[0][0], f_pts[0][1]
        start_r = f_rad[0]
        ang_sr = math.atan2(right_pts[0][1] - start_y, right_pts[0][0] - start_x)
        ang_sl = math.atan2(left_pts[0][1] - start_y, left_pts[0][0] - start_x)
        cr.arc_negative(start_x, start_y, start_r, ang_sr, ang_sl)
        cr.close_path()

    def _compute_variable_stroke_geometry(self, stroke, samples, cfg, tool, base_width):
        pressure_enabled = bool(cfg.get("pressure_enabled", True))
        direction_strength = float(cfg.get("direction_strength", 0.78 if tool == "calligraphy" else 0.0))
        if tool == "calligraphy" and direction_strength <= 0.0:
            direction_strength = 0.78
        speed_strength = float(cfg.get("speed_strength", 0.869 if tool in ("speed", "velocity_pen") else 0.0))
        taper_enabled = bool(cfg.get("taper_enabled", tool == "taper"))
        pressure_min = float(cfg.get("pressure_min_factor", 0.45))
        taper_min = float(cfg.get("taper_min_factor", 0.345))
        speed_scale = float(stroke.get("speed_scale", 0.592))
        nib_angle = float(cfg.get("nib_angle_rad", stroke.get("nib_angle_rad", getattr(self, "nib_angle_rad", -math.radians(35.0)))))

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

            if 0 < i < len(pts) - 1:
                dx = pts[i + 1][0] - pts[i - 1][0]
                dy = pts[i + 1][1] - pts[i - 1][1]
                seg_dist = math.hypot(dx, dy)
            elif i < len(pts) - 1:
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
                nib_mod = abs(math.sin(angle - nib_angle))
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

        # Two-pass Gaussian radius smoothing for organic pressure/velocity transitions
        for _ in range(2):
            if len(radii) >= 3:
                smoothed_r = [0.7 * radii[0] + 0.3 * radii[1]]
                for i in range(1, len(radii) - 1):
                    smoothed_r.append(0.25 * radii[i - 1] + 0.5 * radii[i] + 0.25 * radii[i + 1])
                smoothed_r.append(0.3 * radii[-2] + 0.7 * radii[-1])
                radii = smoothed_r

        return pts, radii

    def draw_stroke(self, cr, stroke, is_active=False):
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

        # 2. Dashed Line Tool ("dashed")
        if tool == "dashed" or "dash_length" in cfg:
            dash_len = float(cfg.get("dash_length", 10.0))
            dash_gap = float(cfg.get("dash_gap", 8.0))
            cr.save()
            cr.set_source_rgba(r, g, b, a)
            cr.set_line_width(base_width)
            cr.set_line_join(cairo.LINE_JOIN_ROUND)
            cr.set_line_cap(cairo.LINE_CAP_ROUND)
            cr.set_dash([dash_len, dash_gap])

            pts = [(float(s["x"]), float(s["y"])) if isinstance(s, dict) else (float(s[0]), float(s[1])) for s in raw_samples]
            cr.new_path()
            self._trace_quadratic_midpoint_spline(cr, pts)
            cr.stroke()
            cr.restore()
            return

        # 3. Highlighter or Uniform-Width Stroke
        pressure_enabled = bool(cfg.get("pressure_enabled", True))
        direction_strength = float(cfg.get("direction_strength", 0.78 if tool == "calligraphy" else 0.0))
        if tool == "calligraphy" and direction_strength <= 0.0:
            direction_strength = 0.78
        speed_strength = float(cfg.get("speed_strength", 0.869 if tool in ("speed", "velocity_pen") else 0.0))
        taper_enabled = bool(cfg.get("taper_enabled", tool == "taper"))

        if tool == "highlighter" or (
            not pressure_enabled
            and direction_strength <= 0.0
            and speed_strength <= 0.0
            and not taper_enabled
        ):
            pts = [(float(s["x"]), float(s["y"])) if isinstance(s, dict) else (float(s[0]), float(s[1])) for s in raw_samples]
            cr.save()
            if a < 0.99:
                cr.push_group()
                cr.set_source_rgb(r, g, b)
                cr.set_line_width(base_width)
                cr.set_line_join(cairo.LINE_JOIN_ROUND)
                cr.set_line_cap(cairo.LINE_CAP_ROUND)
                cr.new_path()
                self._trace_quadratic_midpoint_spline(cr, pts)
                cr.stroke()
                cr.pop_group_to_source()
                cr.paint_with_alpha(a)
            else:
                cr.set_source_rgb(r, g, b)
                cr.set_line_width(base_width)
                cr.set_line_join(cairo.LINE_JOIN_ROUND)
                cr.set_line_cap(cairo.LINE_CAP_ROUND)
                cr.new_path()
                self._trace_quadratic_midpoint_spline(cr, pts)
                cr.stroke()
            cr.restore()
            return

        # 4. Variable-Width Stroke Engine (with geometry caching for static strokes)
        cache = stroke.get("_render_cache") if not is_active else None
        if cache is not None and cache.get("n") == len(raw_samples) and cache.get("bw") == base_width:
            pts, radii = cache["pts"], cache["radii"]
        else:
            if not is_active and len(raw_samples) >= 3:
                samples = self.smooth_samples_chaikin(raw_samples, iterations=1)
            else:
                samples = raw_samples
            pts, radii = self._compute_variable_stroke_geometry(stroke, samples, cfg, tool, base_width)
            if not is_active:
                stroke["_render_cache"] = {"n": len(raw_samples), "bw": base_width, "pts": pts, "radii": radii}

        cr.save()
        self._draw_variable_width_envelope(cr, pts, radii)

        if a < 0.99:
            cr.push_group()
            cr.set_source_rgb(r, g, b)
            cr.fill()
            cr.pop_group_to_source()
            cr.paint_with_alpha(a)
        else:
            cr.set_source_rgb(r, g, b)
            cr.fill()
        cr.restore()

    def draw_stroke_fast(self, cr, stroke):
        """
        Lightweight stroke renderer specifically for page/cover thumbnails (~0.08x scale).
        Avoids Chaikin subdivision and variable-width envelope tangents, rendering
        a decimated centerline polyline that is ~10x faster at thumbnail resolution.
        """
        raw_samples = stroke.get("samples") or stroke.get("points")
        if not raw_samples:
            return

        tool = stroke.get("tool", "pen")
        cfg = stroke.get("config") if isinstance(stroke.get("config"), dict) else {}
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

        cr.save()
        cr.set_source_rgba(r, g, b, a)
        cr.set_line_width(max(1.2, base_width * 0.78))
        cr.set_line_cap(cairo.LINE_CAP_ROUND)
        cr.set_line_join(cairo.LINE_JOIN_ROUND)

        n = len(raw_samples)
        step = 1 if n <= 40 else (2 if n <= 160 else 3)

        cr.new_path()
        for idx in range(0, n, step):
            s = raw_samples[idx]
            x = float(s["x"]) if isinstance(s, dict) else float(s[0])
            y = float(s["y"]) if isinstance(s, dict) else float(s[1])
            if idx == 0:
                cr.move_to(x, y)
            else:
                cr.line_to(x, y)

        if (n - 1) % step != 0:
            s = raw_samples[-1]
            x = float(s["x"]) if isinstance(s, dict) else float(s[0])
            y = float(s["y"]) if isinstance(s, dict) else float(s[1])
            cr.line_to(x, y)

        cr.stroke()
        cr.restore()
