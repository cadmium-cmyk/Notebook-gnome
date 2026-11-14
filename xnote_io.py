import os
import math
import io
import re
import json
import html
import shutil
import hashlib
import zipfile
import tempfile
import datetime
import subprocess
import xml.etree.ElementTree as ET
from collections import OrderedDict
import cairo
import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Gdk', '4.0')
gi.require_version('GdkPixbuf', '2.0')
from gi.repository import Gio, GLib, Gdk, GdkPixbuf

try:
    gi.require_version('Poppler', '0.18')
    from gi.repository import Poppler
    HAS_POPPLER = True
except (ValueError, ImportError):
    Poppler = None
    HAS_POPPLER = False

SUPPORTED_EXTENSIONS = ('.xnote', '.xcanvas', '.xtemplate', '.md', '.markdown', '.rnote')

DEFAULT_RULED_TEMPLATE_ID = "6f16f292a66422f3"
DEFAULT_RULED_TEMPLATE = {
    "xtemplate": 1,
    "id": "xtemplate.examples.ruled",
    "name": "Ruled with margin",
    "description": "Horizontal lines below a header, with a margin line on the left.",
    "license": "MIT",
    "tags": ["lines", "writing"],
    "params": {
        "spacing": {"type": "length", "default": "8mm", "min": "5mm", "max": "12mm", "label": "Line spacing", "role": "spacing"},
        "header": {"type": "length", "default": "24mm", "min": "0mm", "max": "60mm", "label": "Header height"},
        "margin": {"type": "length", "default": "28mm", "min": "0mm", "max": "60mm", "label": "Margin width"}
    },
    "items": [
        {
            "type": "group", "y": "header", "h": "100% - header",
            "children": [
                {
                    "type": "repeat", "axis": "y", "period": "spacing",
                    "children": [{"type": "hline", "y": 0}]
                }
            ]
        },
        {"type": "vline", "x": "margin", "stroke": "accent"}
    ]
}

DEFAULT_CHECKLIST_TEMPLATE_ID = "58ba828c343ad504"
DEFAULT_CHECKLIST_TEMPLATE = {
    "xtemplate": 1,
    "id": "xtemplate.examples.todo",
    "name": "Checklist",
    "description": "Rows with a tick box each, stretched slightly so the rows fill the page exactly.",
    "license": "MIT",
    "tags": ["planner", "lists"],
    "params": {
        "row": {"type": "length", "default": "9mm", "min": "6mm", "max": "14mm", "label": "Row height", "role": "spacing"}
    },
    "items": [
        {"type": "text", "text": "To do", "x": "15mm", "y": "18mm", "size": "5mm", "weight": "bold"},
        {
            "type": "group", "inset": ["25mm", "15mm", "15mm", "15mm"],
            "children": [
                {
                    "type": "repeat", "axis": "y", "period": "row", "fit": "round",
                    "children": [
                        {"type": "rect", "y": "50% - 2.25mm", "w": "4.5mm", "h": "4.5mm", "rx": "0.8mm"},
                        {"type": "hline", "y": "100%", "x1": "8mm"}
                    ]
                }
            ]
        }
    ]
}

DEFAULT_FLOW_XML = """<?xml version="1.0" encoding="UTF-8"?>
<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" xmlns:style="urn:oasis:names:tc:opendocument:xmlns:style:1.0" xmlns:fo="urn:oasis:names:tc:opendocument:xmlns:xsl-fo-compatible:1.0" xmlns:xnotes="urn:xnotes:flow:1.0" office:version="1.2" xnotes:margin-left-mm="20.0" xnotes:margin-top-mm="20.0" xnotes:margin-right-mm="20.0" xnotes:margin-bottom-mm="20.0" xnotes:default-face="sans" xnotes:default-size-pt="13.0">
 <office:automatic-styles>
 </office:automatic-styles>
 <office:body>
  <office:text>
  </office:text>
 </office:body>
</office:document-content>
"""


class XTemplateEvaluator:
    """Evaluates xtemplate v1 length expressions, parameters, and scene graph items."""

    @staticmethod
    def eval_expr(expr, box_w=210.0, box_h=297.0, ref_dim=210.0, params=None, i=0, j=0, extra_vars=None):
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

        s_clean = re.sub(r"(?<![a-zA-Z_])(\d+(?:\.\d+)?|\.\d+)\s*(mm|cm|in|pt|deg|%)", repl_unit, s, flags=re.IGNORECASE)

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
                        env[k] = XTemplateEvaluator.eval_expr(raw, box_w, box_h, ref_dim, params=None)
                    except Exception:
                        env[k] = 0.0
        if extra_vars:
            env.update(extra_vars)

        try:
            return float(eval(s_clean, {"__builtins__": {}}, env))
        except Exception:
            return 0.0

    @staticmethod
    def eval_length(expr, dpi=150.0, total=0.0, params=None):
        if expr is None:
            return 0.0
        if isinstance(expr, (int, float)):
            return float(expr)
        if not isinstance(expr, str):
            return 0.0
        s = expr.strip()
        if not s:
            return 0.0
        ref_mm = total / (dpi / 25.4) if dpi > 0 else total
        val_mm = XTemplateEvaluator.eval_expr(s, box_w=ref_mm, box_h=ref_mm, ref_dim=ref_mm, params=params)
        return val_mm * (dpi / 25.4)


class LazyAssetDict(dict):
    """
    Dictionary of asset surfaces that supports on-demand lazy decoding.
    Pre-cached or already decoded surfaces are returned immediately in O(1).
    Deferred archive assets are decoded when first accessed and retained in-memory.
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._lazy = {}

    def register_lazy(self, key, raw_data, aliases=None, cache_key=None):
        entry = (raw_data, cache_key, set(aliases or []))
        self._lazy[key] = entry
        if aliases:
            for a in aliases:
                self._lazy[a] = entry

    def __getitem__(self, key):
        if dict.__contains__(self, key):
            return dict.__getitem__(self, key)
        if hasattr(self, "_lazy") and key in self._lazy:
            raw_data, cache_key, aliases = self._lazy[key]
            surf = XNotePackageParser.bytes_to_surface(raw_data)
            dict.__setitem__(self, key, surf)
            for a in aliases:
                dict.__setitem__(self, a, surf)
                self._lazy.pop(a, None)
            self._lazy.pop(key, None)
            if cache_key and surf is not None:
                XNotePackageParser._archive_asset_cache[cache_key] = surf
                if len(XNotePackageParser._archive_asset_cache) > XNotePackageParser.MAX_ARCHIVE_ENTRIES:
                    XNotePackageParser._archive_asset_cache.popitem(last=False)
            return surf
        raise KeyError(key)

    def get(self, key, default=None):
        try:
            return self[key]
        except KeyError:
            return default

    def __contains__(self, key):
        return dict.__contains__(self, key) or (hasattr(self, "_lazy") and key in self._lazy)

    def keys(self):
        s = set(dict.keys(self))
        if hasattr(self, "_lazy"):
            s.update(self._lazy.keys())
        return s

    def __iter__(self):
        return iter(self.keys())

    def __len__(self):
        return len(self.keys())

    def copy(self):
        cp = LazyAssetDict(self)
        if hasattr(self, "_lazy"):
            cp._lazy = dict(self._lazy)
        return cp

    def clear(self):
        super().clear()
        if hasattr(self, "_lazy"):
            self._lazy.clear()


class XNotePackageParser:
    """Unified loader, PDF importer/exporter, and writer for legacy and v1 .xnote archives."""

    WRITER_NOTE = 67
    WRITER_CANVAS = 47

    _image_surface_cache = OrderedDict()
    _archive_asset_cache = OrderedDict()
    MAX_IMAGE_SURFACES = 150
    MAX_ARCHIVE_ENTRIES = 200
    _disk_cache_dir = None

    @classmethod
    def _clean_item_for_manifest(cls, item):
        if not isinstance(item, dict):
            return item
        clean = {}
        for k, v in item.items():
            if k.startswith('_'):
                continue
            if k == "samples" and isinstance(v, list):
                clean_samples = []
                for s in v:
                    if isinstance(s, dict):
                        clean_samples.append([round(float(s.get("x", 0)), 2), round(float(s.get("y", 0)), 2), round(float(s.get("pressure", 0.5)), 3)])
                    elif isinstance(s, (list, tuple)):
                        pt = [round(float(s[0]), 2), round(float(s[1]), 2)]
                        if len(s) > 2:
                            pt.append(round(float(s[2]), 3))
                        if len(s) > 3:
                            pt.append(int(s[3]))
                        clean_samples.append(pt)
                    else:
                        clean_samples.append(s)
                clean[k] = clean_samples
            elif k in ("rect", "start", "end", "pos") and isinstance(v, list):
                clean[k] = [round(float(x), 2) if isinstance(x, (int, float)) else x for x in v]
            elif k == "points" and isinstance(v, list):
                clean[k] = [
                    [round(float(c), 2) for c in pt] if isinstance(pt, (list, tuple)) else pt
                    for pt in v
                ]
            elif isinstance(v, dict):
                clean[k] = cls._clean_item_for_manifest(v)
            else:
                clean[k] = v
        return clean

    @classmethod
    def _parse_markups_xfdf(cls, xml_bytes):
        markups_by_page = {}
        try:
            root = ET.fromstring(xml_bytes)
            for annot in root.iter():
                tag = annot.tag.split("}")[-1].lower()
                if tag in ("highlight", "underline", "strikeout"):
                    p_idx = int(annot.attrib.get("page", 0))
                    color_hex = annot.attrib.get("color", "#FFFF00")
                    quads_val = (
                        annot.attrib.get("{urn:xnotes:markups:1.0}quads")
                        or annot.attrib.get("xnotes:quads")
                        or annot.attrib.get("coords", "")
                    )
                    text = "".join(annot.itertext()).strip()
                    m_entry = {
                        "type": tag,
                        "color": color_hex,
                        "quads": quads_val,
                        "text": text,
                    }
                    markups_by_page.setdefault(p_idx, []).append(m_entry)
        except Exception as e:
            print(f"Failed to parse markups.xfdf: {e}")
        return markups_by_page

    @classmethod
    def _write_markups_xfdf(cls, pages):
        has_markups = any(bool(p.get("markups")) for p in pages if isinstance(p, dict))
        if not has_markups:
            return None
        lines = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<xfdf xmlns="http://ns.adobe.com/xfdf/" xmlns:xnotes="urn:xnotes:markups:1.0" xml:space="preserve">',
            ' <annots>'
        ]
        now_str = datetime.datetime.now(datetime.timezone.utc).strftime("D:%Y%m%d%H%M%SZ")
        for i, page in enumerate(pages):
            for m in page.get("markups", []):
                m_type = m.get("type", "highlight")
                col = m.get("color", "#FFFF00")
                quads = m.get("quads", "")
                text = html.escape(m.get("text", "")) if "html" in globals() else m.get("text", "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                lines.append(
                    f'  <{m_type} page="{i}" color="{col}" creationdate="{now_str}" xnotes:quads="{quads}">'
                )
                if text:
                    lines.append(f'   <contents>{text}</contents>')
                lines.append(f'  </{m_type}>')
        lines.append(' </annots>')
        lines.append('</xfdf>')
        return "\n".join(lines).encode("utf-8")

    @classmethod
    def get_disk_cache_dir(cls):
        if cls._disk_cache_dir is None:
            cache_base = GLib.get_user_cache_dir()
            cls._disk_cache_dir = os.path.join(cache_base, "xnotes_viewer", "image_cache")
            try:
                os.makedirs(cls._disk_cache_dir, exist_ok=True)
            except Exception:
                pass
        return cls._disk_cache_dir

    @classmethod
    def clear_image_caches(cls):
        cls._image_surface_cache.clear()
        cls._archive_asset_cache.clear()

    @staticmethod
    def pixbuf_to_cairo_surface(pixbuf):
        w = pixbuf.get_width()
        h = pixbuf.get_height()
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
        cr = cairo.Context(surface)
        Gdk.cairo_set_source_pixbuf(cr, pixbuf, 0, 0)
        cr.paint()
        surface.flush()
        return surface

    @classmethod
    def bytes_to_surface(cls, data):
        if not data:
            return None

        # 1. In-memory LRU cache check by SHA-256 digest
        digest = hashlib.sha256(data).digest()
        cached = cls._image_surface_cache.get(digest)
        if cached is not None:
            cls._image_surface_cache.move_to_end(digest)
            return cached

        # 2. Decode via GdkPixbuf in memory
        surface = None
        try:
            bytes_data = GLib.Bytes.new(data)
            stream = Gio.MemoryInputStream.new_from_bytes(bytes_data)
            pixbuf = GdkPixbuf.Pixbuf.new_from_stream(stream, None)
            if pixbuf:
                surface = cls.pixbuf_to_cairo_surface(pixbuf)
        except Exception:
            pass

        # 3. Fallback for WebP images (e.g. strawberry-thief.webp) if webp-pixbuf-loader is missing
        if surface is None and data[:4] == b'RIFF' and data[8:12] == b'WEBP':
            with tempfile.TemporaryDirectory() as tmpdir:
                in_webp = os.path.join(tmpdir, "in.webp")
                out_png = os.path.join(tmpdir, "out.png")
                with open(in_webp, "wb") as f:
                    f.write(data)
                cmds = []
                if shutil.which("dwebp"):
                    cmds.append(["dwebp", in_webp, "-o", out_png])
                if shutil.which("ffmpeg"):
                    cmds.append(["ffmpeg", "-y", "-i", in_webp, out_png])
                if shutil.which("magick"):
                    cmds.append(["magick", in_webp, out_png])
                elif shutil.which("convert"):
                    cmds.append(["convert", in_webp, out_png])
                for cmd in cmds:
                    try:
                        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2)
                        if os.path.isfile(out_png):
                            surface = cairo.ImageSurface.create_from_png(out_png)
                            break
                    except Exception:
                        continue

        if surface is not None:
            cls._image_surface_cache[digest] = surface
            if len(cls._image_surface_cache) > cls.MAX_IMAGE_SURFACES:
                cls._image_surface_cache.popitem(last=False)

        return surface

    @staticmethod
    def extract_template_id(archive_path):
        if not archive_path:
            return ""
        base = os.path.splitext(os.path.basename(str(archive_path)))[0]
        if base.startswith("template-"):
            return base[len("template-"):]
        return base

    @classmethod
    def _load_poppler_document_from_bytes(cls, pdf_bytes):
        if not HAS_POPPLER or not pdf_bytes:
            return None
        try:
            gbytes = GLib.Bytes.new(pdf_bytes)
            return Poppler.Document.new_from_bytes(gbytes, None)
        except Exception as e:
            print(f"Poppler failed to load PDF bytes: {e}")
            return None

    @classmethod
    def _render_pdf_with_pdftoppm(cls, pdf_bytes, dpi=150, first_page_only=False):
        surfaces = []
        if not shutil.which("pdftoppm"):
            return surfaces
        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_path = os.path.join(tmpdir, "input.pdf")
            with open(pdf_path, "wb") as f:
                f.write(pdf_bytes)
            out_prefix = os.path.join(tmpdir, "page")
            cmd = ["pdftoppm", "-png", "-r", str(int(dpi))]
            if first_page_only:
                cmd.extend(["-f", "1", "-l", "1"])
            cmd.extend([pdf_path, out_prefix])
            try:
                subprocess.run(
                    cmd,
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
                generated = sorted(
                    f for f in os.listdir(tmpdir) if f.startswith("page") and f.endswith(".png")
                )
                for fname in generated:
                    with open(os.path.join(tmpdir, fname), "rb") as pf:
                        surf = cls.bytes_to_surface(pf.read())
                        if surf:
                            surfaces.append(surf)
            except Exception as e:
                print(f"pdftoppm fallback failed: {e}")
        return surfaces

    @classmethod
    def _register_builtin_templates(cls, templates):
        """Ensure standard v1 body templates (Ruled & Checklist) are always available."""
        builtins = [
            (DEFAULT_RULED_TEMPLATE_ID, DEFAULT_RULED_TEMPLATE),
            (DEFAULT_CHECKLIST_TEMPLATE_ID, DEFAULT_CHECKLIST_TEMPLATE),
        ]
        for tid, meta in builtins:
            if tid not in templates:
                raw_b = json.dumps(meta, indent=2).encode("utf-8")
                t_entry = {
                    "id": tid,
                    "path": f"templates/{tid}.xtemplate",
                    "is_cover": False,
                    "meta": meta,
                    "surface": None,
                    "images": {},
                    "raw_bytes": raw_b,
                }
                templates[tid] = t_entry
                if meta.get("id"):
                    templates[meta["id"]] = t_entry

    _system_templates_cache = None
    _system_assets_cache = None

    @classmethod
    def load_system_templates(cls, templates, assets=None):
        if cls._system_templates_cache is None:
            cache_t = {}
            cache_a = {}
            cls._register_builtin_templates(cache_t)
            base_dir = os.path.dirname(os.path.abspath(__file__))
            tpl_dir = os.path.join(base_dir, "templates")
            if os.path.isdir(tpl_dir):
                for root, _, files in os.walk(tpl_dir):
                    for f in sorted(files):
                        if f.endswith(".xtemplate"):
                            full_p = os.path.join(root, f)
                            try:
                                with open(full_p, "rb") as fp:
                                    data = fp.read()
                                cls._load_xtemplate_bytes(f, data, cache_a, cache_t)
                            except Exception:
                                pass
            cls._system_templates_cache = cache_t
            cls._system_assets_cache = cache_a

        templates.update(cls._system_templates_cache)
        if assets is not None:
            assets.update(cls._system_assets_cache)

    @classmethod
    def _load_xtemplate_bytes(cls, name, data, assets, templates):
        tid = cls.extract_template_id(name)
        base_name = os.path.basename(name)
        bio = io.BytesIO(data)

        if zipfile.is_zipfile(bio):
            bio.seek(0)
            t_meta = {}
            primary_surface = None
            t_images = {}

            with zipfile.ZipFile(bio, 'r') as tz:
                if 'template.json' in tz.namelist():
                    try:
                        t_meta = json.loads(tz.read('template.json').decode('utf-8', errors='replace'))
                    except Exception as e:
                        print(f"Failed to parse template.json in {name}: {e}")

                for inner_name in tz.namelist():
                    if inner_name.endswith('/') or inner_name == 'template.json':
                        continue
                    try:
                        inner_bytes = tz.read(inner_name)
                        surf = cls.bytes_to_surface(inner_bytes)
                        if surf:
                            t_images[inner_name] = surf
                            t_images[os.path.basename(inner_name)] = surf
                            assets[f"{name}/{inner_name}"] = surf
                            assets[f"{tid}/{inner_name}"] = surf
                            if inner_name == 'images/picture.png' or primary_surface is None:
                                primary_surface = surf
                    except Exception:
                        pass

            if primary_surface:
                for alias in (name, base_name, tid, f"template-{tid}"):
                    assets[alias] = primary_surface

            is_cover = (
                t_meta.get("kind") == "cover"
                or name.startswith("assets/")
                or base_name.startswith("template-")
            )
            t_entry = {
                "id": tid,
                "path": name,
                "is_cover": is_cover,
                "meta": t_meta,
                "surface": primary_surface,
                "images": t_images,
                "raw_bytes": data,
            }
            templates[tid] = t_entry
            templates[name] = t_entry
            templates[base_name] = t_entry
            if t_meta.get("id"):
                templates[t_meta["id"]] = t_entry
            return t_entry
        else:
            try:
                t_meta = json.loads(data.decode('utf-8', errors='replace'))
                if isinstance(t_meta, dict):
                    is_cover = t_meta.get("kind") == "cover"
                    t_entry = {
                        "id": tid,
                        "path": name,
                        "is_cover": is_cover,
                        "meta": t_meta,
                        "surface": None,
                        "images": {},
                        "raw_bytes": data,
                    }
                    templates[tid] = t_entry
                    templates[name] = t_entry
                    templates[base_name] = t_entry
                    if t_meta.get("id"):
                        templates[t_meta["id"]] = t_entry
                    return t_entry
            except Exception:
                pass

            try:
                surf = cls.bytes_to_surface(data)
                if surf:
                    for alias in (name, base_name, tid, f"template-{tid}"):
                        assets[alias] = surf
                    t_entry = {
                        "id": tid,
                        "path": name,
                        "is_cover": name.startswith("assets/"),
                        "meta": {},
                        "surface": surf,
                        "images": {},
                        "raw_bytes": data,
                    }
                    templates[tid] = t_entry
                    templates[name] = t_entry
                    templates[base_name] = t_entry
                    return t_entry
            except Exception:
                pass
        return None

    @classmethod
    def import_image_asset(cls, file_path, doc, assets):
        with open(file_path, "rb") as f:
            raw_data = f.read()

        surf = cls.bytes_to_surface(raw_data)
        if surf is None:
            raise ValueError(f"Could not decode image file: {os.path.basename(file_path)}")

        digest = hashlib.sha256(raw_data).hexdigest()[:16]
        ext = os.path.splitext(file_path)[1].lower() or ".png"
        asset_name = f"assets/image-{digest}{ext}"

        assets[asset_name] = surf
        assets[os.path.basename(asset_name)] = surf
        doc.setdefault("_raw_assets", {})[asset_name] = raw_data
        return asset_name, surf

    @classmethod
    def import_template_or_cover_file(cls, file_path, doc, assets, templates):
        with open(file_path, "rb") as f:
            raw_data = f.read()

        ext = os.path.splitext(file_path)[1].lower()
        base_title = os.path.splitext(os.path.basename(file_path))[0]
        digest = hashlib.sha256(raw_data).hexdigest()[:16]

        if ext == ".xtemplate" or zipfile.is_zipfile(io.BytesIO(raw_data)):
            is_zip = zipfile.is_zipfile(io.BytesIO(raw_data))
            archive_path = (
                f"assets/template-{digest}.xtemplate"
                if is_zip else f"templates/{digest}.xtemplate"
            )
            t_entry = cls._load_xtemplate_bytes(archive_path, raw_data, assets, templates)
            if t_entry:
                return t_entry
            raise ValueError("Invalid .xtemplate file.")

        surf = cls.bytes_to_surface(raw_data)
        if surf is None:
            raise ValueError("Unsupported image or template format.")

        png_bio = io.BytesIO()
        surf.write_to_png(png_bio)
        png_bytes = png_bio.getvalue()

        template_json = {
            "xtemplate": 1,
            "name": base_title,
            "kind": "cover",
            "items": [
                {"type": "image", "src": "images/picture.png", "fit": "cover"}
            ]
        }

        nested_zip_bio = io.BytesIO()
        with zipfile.ZipFile(nested_zip_bio, "w", compression=zipfile.ZIP_DEFLATED) as tz:
            tz.writestr("template.json", json.dumps(template_json, indent=2).encode("utf-8"))
            tz.writestr("images/picture.png", png_bytes)

        xtemplate_bytes = nested_zip_bio.getvalue()
        tid = hashlib.sha256(xtemplate_bytes).hexdigest()[:16]
        archive_path = f"assets/template-{tid}.xtemplate"

        t_entry = cls._load_xtemplate_bytes(archive_path, xtemplate_bytes, assets, templates)
        return t_entry

    @classmethod
    def import_pdf_into_document(cls, pdf_path, doc, assets):
        with open(pdf_path, "rb") as f:
            pdf_bytes = f.read()

        dpi = float(doc.get("dpi", 150.0))
        scale = dpi / 72.0
        pages_added = 0

        poppler_doc = cls._load_poppler_document_from_bytes(pdf_bytes)
        if poppler_doc is not None:
            n_pages = poppler_doc.get_n_pages()
            if n_pages <= 0:
                raise ValueError("Selected PDF has no pages.")

            doc["has_pdf"] = True
            doc["_pdf_bytes"] = pdf_bytes
            doc["_pdf_archive_name"] = doc.get("_pdf_archive_name", "document.pdf")
            doc["_poppler_doc"] = poppler_doc

            for i in range(n_pages):
                p_page = poppler_doc.get_page(i)
                pw_pt, ph_pt = p_page.get_size()
                pw = round(pw_pt * scale, 4)
                ph = round(ph_pt * scale, 4)
                doc.setdefault("pages", []).append({
                    "width": pw,
                    "height": ph,
                    "pdf_page": i,
                    "style": {"template": "none"},
                    "items": []
                })
                pages_added += 1
            return pages_added

        surfaces = cls._render_pdf_with_pdftoppm(pdf_bytes, dpi=dpi)
        if not surfaces:
            raise RuntimeError(
                "PDF import requires either 'gir1.2-poppler-0.18' (Poppler) or 'pdftoppm' (poppler-utils)."
            )

        digest = hashlib.sha256(pdf_bytes).hexdigest()[:12]
        for idx, surf in enumerate(surfaces):
            png_bio = io.BytesIO()
            surf.write_to_png(png_bio)
            png_bytes = png_bio.getvalue()
            asset_name = f"assets/pdf-{digest}-p{idx + 1}.png"
            assets[asset_name] = surf
            assets[os.path.basename(asset_name)] = surf
            doc.setdefault("_raw_assets", {})[asset_name] = png_bytes

            pw = float(surf.get_width())
            ph = float(surf.get_height())
            doc.setdefault("pages", []).append({
                "width": pw,
                "height": ph,
                "pdf_page": idx,
                "style": {"template": "none"},
                "items": [
                    {
                        "kind": "image",
                        "asset": asset_name,
                        "rect": [0.0, 0.0, pw, ph],
                        "angle": 0.0
                    }
                ]
            })
            pages_added += 1

        doc["has_pdf"] = True
        doc["_pdf_bytes"] = pdf_bytes
        doc["_pdf_archive_name"] = "document.pdf"
        return pages_added

    @classmethod
    def create_notebook_from_pdf(cls, pdf_path, dest_xnote_path):
        now_iso = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        doc = {
            "format": "xnote",
            "version": 1,
            "writer": 48,
            "created": now_iso,
            "dpi": 150,
            "has_pdf": True,
            "bookmarks": [],
            "pages": [],
            "style": {
                "page_color": [255, 255, 255, 255],
                "template": DEFAULT_RULED_TEMPLATE_ID,
                "pattern_color": [0, 56, 183, 95]
            }
        }
        assets = {}
        templates = {}
        cls._register_builtin_templates(templates)
        cls.import_pdf_into_document(pdf_path, doc, assets)
        cls.save_archive(dest_xnote_path, doc, templates=templates)
        return cls.load_archive(dest_xnote_path)

    @classmethod
    def export_to_pdf(cls, pdf_path, doc, canvas, with_background=True, with_pattern=True, optimize_for_printing=False):
        pages = doc.get("pages", [])
        if not pages:
            raise ValueError("Document has no pages to export.")

        dpi = float(doc.get("dpi", 150.0))
        px_to_pt = 72.0 / dpi

        pw0, ph0 = canvas.get_page_size(0)
        pdf_surface = cairo.PDFSurface(pdf_path, pw0 * px_to_pt, ph0 * px_to_pt)
        cr = cairo.Context(pdf_surface)

        for idx, page in enumerate(pages):
            pw, ph = canvas.get_page_size(idx)
            pdf_surface.set_size(pw * px_to_pt, ph * px_to_pt)

            cr.save()
            cr.scale(px_to_pt, px_to_pt)
            cr.rectangle(0.0, 0.0, pw, ph)
            cr.clip()
            canvas.render_page_contents(
                cr, doc, page, pw, ph, canvas.assets, canvas.templates, page_idx=idx,
                with_background=with_background, with_pattern=with_pattern, optimize_for_printing=optimize_for_printing
            )
            cr.restore()
            pdf_surface.show_page()

        pdf_surface.finish()

    @classmethod
    def _parse_flow_xml(cls, xml_bytes):
        flow_info = {
            "margin_left_mm": 20.0,
            "margin_top_mm": 20.0,
            "margin_right_mm": 20.0,
            "margin_bottom_mm": 20.0,
            "default_face": "sans",
            "default_size_pt": 13.0,
            "paragraphs": [],
        }
        try:
            root = ET.fromstring(xml_bytes)
        except Exception as e:
            print(f"Failed to parse flow.xml: {e}")
            return flow_info

        for attr_key, attr_val in root.attrib.items():
            short_key = attr_key.split('}')[-1]
            if short_key == "margin-left-mm":
                try:
                    flow_info["margin_left_mm"] = float(attr_val)
                except ValueError:
                    pass
            elif short_key == "margin-top-mm":
                try:
                    flow_info["margin_top_mm"] = float(attr_val)
                except ValueError:
                    pass
            elif short_key == "margin-right-mm":
                try:
                    flow_info["margin_right_mm"] = float(attr_val)
                except ValueError:
                    pass
            elif short_key == "margin-bottom-mm":
                try:
                    flow_info["margin_bottom_mm"] = float(attr_val)
                except ValueError:
                    pass
            elif short_key == "default-face":
                flow_info["default_face"] = attr_val
            elif short_key == "default-size-pt":
                try:
                    flow_info["default_size_pt"] = float(attr_val)
                except ValueError:
                    pass

        for elem in root.iter():
            tag = elem.tag.split('}')[-1].lower()
            if tag in ('p', 'h'):
                text_content = "".join(elem.itertext()).strip()
                if text_content:
                    flow_info["paragraphs"].append(text_content)

        return flow_info

    @classmethod
    def load_archive(cls, path, thumbnails_only=False):
        doc = {}
        assets = LazyAssetDict()
        templates = {}
        raw_assets = {}
        flow_info = None
        pdf_bytes = None
        pdf_archive_name = None
        markups_xfdf_bytes = None

        try:
            mtime = os.path.getmtime(path) if os.path.exists(path) else 0.0
        except OSError:
            mtime = 0.0

        if path.lower().endswith('.rnote'):
            from rnote.convert_rnote import convert_rnote_to_doc_and_assets
            return convert_rnote_to_doc_and_assets(path)

        if not zipfile.is_zipfile(path):
            try:
                with open(path, 'rb') as raw_f:
                    raw_data = raw_f.read()
                data_stripped = raw_data.lstrip()
                if data_stripped.startswith(b'{'):
                    t_entry = cls._load_xtemplate_bytes(os.path.basename(path), raw_data, assets, templates)
                    if t_entry:
                        cls._register_builtin_templates(templates)
                        cls.load_system_templates(templates, assets)
                        tid = t_entry.get("id") or cls.extract_template_id(path)
                        t_meta = t_entry.get("meta", {})
                        doc = {
                            "format": "xtemplate",
                            "version": 1,
                            "dpi": 150,
                            "pages": [{
                                "width": 1240.1574803149608,
                                "height": 1753.9370078740158,
                                "cover": t_meta.get("kind") == "cover",
                                "style": {"template": tid},
                                "items": []
                            }],
                            "style": {"page_color": [255, 255, 255, 255], "template": tid}
                        }
                        return doc, assets, templates
            except Exception:
                pass
            raise ValueError("Not a valid ZIP-based notebook archive.")

        with zipfile.ZipFile(path, 'r') as z:
            namelist = z.namelist()

            if 'manifest.json' in namelist:
                with z.open('manifest.json') as f:
                    doc = json.load(f)
                if doc.get("format") == "xcanvas":
                    canvas_items = doc.setdefault("items", [])
                    bg = doc.setdefault("background", {
                        "pattern": "none",
                        "pattern_color": [150, 150, 150, 64],
                        "spacing": 64,
                        "paper_color": [255, 255, 255, 255]
                    })
                    doc.setdefault("view", {
                        "cx": 0.0,
                        "cy": 0.0,
                        "zoom": 1.0
                    })
                    doc["pages"] = [{
                        "width": 0,
                        "height": 0,
                        "style": bg,
                        "items": canvas_items,
                        "is_canvas": True
                    }]
            elif 'template.json' in namelist:
                with z.open('template.json') as f:
                    t_meta = json.load(f)
                with open(path, 'rb') as raw_f:
                    cls._load_xtemplate_bytes(os.path.basename(path), raw_f.read(), assets, templates)
                cls._register_builtin_templates(templates)
                cls.load_system_templates(templates, assets)
                tid = cls.extract_template_id(path)
                doc = {
                    "format": "xtemplate",
                    "version": 1,
                    "dpi": 150,
                    "pages": [{
                        "width": 1240.1574803149608,
                        "height": 1753.9370078740158,
                        "cover": t_meta.get("kind") == "cover",
                        "style": {"template": tid},
                        "items": []
                    }],
                    "style": {"page_color": [255, 255, 255, 255], "template": tid}
                }
                return doc, assets, templates

            if 'flow.xml' in namelist:
                try:
                    flow_info = cls._parse_flow_xml(z.read('flow.xml'))
                except Exception as e:
                    print(f"Warning: Failed to read flow.xml in {path}: {e}")

            # In thumbnail generation mode, optimize loading by only fetching page 0 requirements
            pages = doc.get("pages") or [{}]
            page0 = pages[0] if pages else {}
            page0_needs_pdf = ("pdf_page" in page0 and page0.get("pdf_page") is not None)
            page0_assets = set()
            if doc.get("format") == "xcanvas":
                check_items = doc.get("items") or (page0.get("items", []) if page0 else [])
            else:
                check_items = page0.get("items", []) if page0 else []
            for it in check_items:
                if isinstance(it, dict):
                    for k in ("asset", "src", "image"):
                        v = it.get(k)
                        if v and isinstance(v, str):
                            page0_assets.add(v)
                            page0_assets.add(os.path.basename(v))

            for name in namelist:
                if name.endswith('/') or name in ('manifest.json', 'flow.xml', 'template.json'):
                    continue

                lower_name = name.lower()

                # Fast skip: don't extract large PDF bytes if thumbnail page 0 doesn't use PDF
                if thumbnails_only and lower_name.endswith('.pdf') and not page0_needs_pdf:
                    continue

                # Fast skip: don't decode non-cover assets when rendering cover thumbnail
                if thumbnails_only and name.startswith('assets/') and not lower_name.endswith('.xtemplate'):
                    if name not in page0_assets and os.path.basename(name) not in page0_assets:
                        continue

                # Check archive asset cache first: if cached and in thumbnails_only, skip z.read entirely
                if thumbnails_only and name.startswith('assets/') and not lower_name.endswith('.xtemplate'):
                    cache_key = (path, name, mtime)
                    cached_surf = cls._archive_asset_cache.get(cache_key)
                    if cached_surf is not None:
                        assets[name] = cached_surf
                        assets[os.path.basename(name)] = cached_surf
                        cls._archive_asset_cache.move_to_end(cache_key)
                        continue

                try:
                    raw_data = z.read(name)
                except Exception as e:
                    print(f"Failed to read {name} from archive: {e}")
                    continue

                if lower_name.endswith('.pdf') or name == 'assets/source.pdf':
                    pdf_bytes = raw_data
                    pdf_archive_name = name
                elif name == 'markups.xfdf':
                    markups_xfdf_bytes = raw_data
                elif lower_name.endswith('.xtemplate') or name.startswith('templates/'):
                    cls._load_xtemplate_bytes(name, raw_data, assets, templates)
                elif name.startswith('assets/'):
                    if lower_name.endswith('.xtemplate') or zipfile.is_zipfile(io.BytesIO(raw_data)):
                        cls._load_xtemplate_bytes(name, raw_data, assets, templates)
                    else:
                        cache_key = (path, name, mtime)
                        if not thumbnails_only:
                            raw_assets[name] = raw_data
                        cached_surf = cls._archive_asset_cache.get(cache_key)
                        if cached_surf is not None:
                            assets[name] = cached_surf
                            assets[os.path.basename(name)] = cached_surf
                            cls._archive_asset_cache.move_to_end(cache_key)
                        elif thumbnails_only or name in page0_assets or os.path.basename(name) in page0_assets:
                            try:
                                surf = cls.bytes_to_surface(raw_data)
                                if surf:
                                    assets[name] = surf
                                    assets[os.path.basename(name)] = surf
                                    cls._archive_asset_cache[cache_key] = surf
                                    if len(cls._archive_asset_cache) > cls.MAX_ARCHIVE_ENTRIES:
                                        cls._archive_asset_cache.popitem(last=False)
                            except Exception as e:
                                print(f"Failed to load asset {name}: {e}")
                        else:
                            base = os.path.basename(name)
                            assets.register_lazy(name, raw_data, aliases=[base] if base != name else None, cache_key=cache_key)

        if markups_xfdf_bytes and "pages" in doc:
            markups_by_page = cls._parse_markups_xfdf(markups_xfdf_bytes)
            for p_idx, marks in markups_by_page.items():
                if 0 <= p_idx < len(doc["pages"]):
                    doc["pages"][p_idx]["markups"] = marks

        if doc.get("format") == "xcanvas":
            return doc, assets, templates

        # Always register built-in and system templates
        cls._register_builtin_templates(templates)
        cls.load_system_templates(templates, assets)

        if flow_info:
            doc["_flow"] = flow_info
        if raw_assets:
            doc["_raw_assets"] = raw_assets
        if pdf_bytes:
            if not thumbnails_only:
                doc["_pdf_bytes"] = pdf_bytes
                doc["_pdf_archive_name"] = pdf_archive_name or "assets/source.pdf"
            poppler_doc = cls._load_poppler_document_from_bytes(pdf_bytes)
            if poppler_doc is not None:
                doc["_poppler_doc"] = poppler_doc
            else:
                dpi = float(doc.get("dpi", 150.0))
                doc["_pdf_surfaces"] = cls._render_pdf_with_pdftoppm(pdf_bytes, dpi=dpi, first_page_only=thumbnails_only)

        return doc, assets, templates

    @classmethod
    def create_new_canvas(cls, path):
        now_iso = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        doc = {
            "format": "xcanvas",
            "version": 1,
            "writer": cls.WRITER_CANVAS,
            "created": now_iso,
            "dpi": 150,
            "background": {
                "pattern": "none",
                "pattern_color": [150, 150, 150, 64],
                "spacing": 64,
                "paper_color": [255, 255, 255, 255]
            },
            "view": {
                "cx": 0.0,
                "cy": 0.0,
                "zoom": 1.0
            },
            "items": []
        }
        cls.save_archive(path, doc)
        return cls.load_archive(path)

    @classmethod
    def create_new_notebook(cls, path):
        now_iso = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        doc = {
            "format": "xnote",
            "version": 1,
            "writer": cls.WRITER_NOTE,
            "created": now_iso,
            "dpi": 150,
            "has_pdf": False,
            "bookmarks": [],
            "pages": [
                {
                    "width": 1240.1574803149608,
                    "height": 1753.9370078740158,
                    "pdf_page": None,
                    "items": []
                }
            ],
            "style": {
                "page_color": [255, 255, 255, 255],
                "template": DEFAULT_RULED_TEMPLATE_ID,
                "pattern_color": [0, 56, 183, 95]
            }
        }
        templates = {}
        cls._register_builtin_templates(templates)
        cls.save_archive(path, doc, templates=templates)
        return cls.load_archive(path)

    @classmethod
    def save_archive(cls, path, doc, templates=None):
        if doc.get("format") == "xcanvas":
            now_iso = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
            canvas_items = doc.get("items")
            if canvas_items is None and doc.get("pages"):
                canvas_items = doc["pages"][0].get("items", [])
            clean_items = [cls._clean_item_for_manifest(it) for it in (canvas_items or [])]
            manifest = {
                "format": "xcanvas",
                "version": doc.get("version", 1),
                "writer": cls.WRITER_CANVAS,
                "created": doc.get("created", now_iso),
                "dpi": doc.get("dpi", 150),
                "background": doc.get("background", {
                    "pattern": "none",
                    "pattern_color": [150, 150, 150, 64],
                    "spacing": 64,
                    "paper_color": [255, 255, 255, 255]
                }),
                "view": doc.get("view", {
                    "cx": 0.0,
                    "cy": 0.0,
                    "zoom": 1.0
                }),
                "items": clean_items
            }
            if doc.get("waypoints"):
                manifest["waypoints"] = doc["waypoints"]
            tmp_path = f"{path}.tmp"
            with zipfile.ZipFile(tmp_path, 'w') as zout:
                for asset_path, raw_bytes in doc.get("_raw_assets", {}).items():
                    if asset_path and raw_bytes:
                        zinfo = zipfile.ZipInfo(asset_path)
                        zinfo.compress_type = zipfile.ZIP_STORED
                        zout.writestr(zinfo, raw_bytes)
                zinfo = zipfile.ZipInfo("manifest.json")
                zinfo.compress_type = zipfile.ZIP_DEFLATED
                zout.writestr(zinfo, json.dumps(manifest, separators=(',', ':')).encode('utf-8'))
            os.replace(tmp_path, path)
            return
        existing_entries = {}
        if os.path.exists(path) and zipfile.is_zipfile(path):
            with zipfile.ZipFile(path, 'r') as zin:
                for item in zin.infolist():
                    if item.filename != 'manifest.json':
                        existing_entries[item.filename] = zin.read(item.filename)

        if 'flow.xml' not in existing_entries:
            existing_entries['flow.xml'] = DEFAULT_FLOW_XML.encode('utf-8')

        if templates:
            seen_paths = set()
            for t_info in templates.values():
                if not isinstance(t_info, dict):
                    continue
                t_path = t_info.get("path")
                raw_bytes = t_info.get("raw_bytes")
                if t_path and raw_bytes and t_path not in seen_paths:
                    existing_entries[t_path] = raw_bytes
                    seen_paths.add(t_path)

        if doc.get("_pdf_bytes"):
            doc["_pdf_archive_name"] = "assets/source.pdf"

        # Separate stored binary assets (images, audio, PDF) from deflated tail entries (flow, templates, manifest)
        # Spec 08 invariant: Stored assets first (ZIP_STORED), tail entries last (ZIP_DEFLATED)
        stored_entries = {}
        tail_entries = {}

        if doc.get("_pdf_bytes"):
            stored_entries["assets/source.pdf"] = doc["_pdf_bytes"]

        for asset_path, raw_bytes in doc.get("_raw_assets", {}).items():
            if asset_path and raw_bytes:
                stored_entries[asset_path] = raw_bytes

        # Existing entries carried over
        for filename, content in existing_entries.items():
            if filename.startswith("assets/"):
                stored_entries[filename] = content
            elif filename in ("flow.xml", "markups.xfdf") or filename.startswith("templates/"):
                tail_entries[filename] = content

        if "flow.xml" not in tail_entries:
            tail_entries["flow.xml"] = DEFAULT_FLOW_XML.encode("utf-8")

        if templates:
            for t_info in templates.values():
                if not isinstance(t_info, dict):
                    continue
                t_path = t_info.get("path")
                raw_bytes = t_info.get("raw_bytes")
                if t_path and raw_bytes:
                    if t_path.startswith("assets/"):
                        stored_entries[t_path] = raw_bytes
                    else:
                        tail_entries[t_path] = raw_bytes

        # Export markups if present
        markups_xml = cls._write_markups_xfdf(doc.get("pages", []))
        if markups_xml:
            tail_entries["markups.xfdf"] = markups_xml

        clean_doc = {k: v for k, v in doc.items() if not k.startswith('_')}
        clean_doc["writer"] = cls.WRITER_NOTE
        clean_pages = []
        for p in doc.get("pages", []):
            clean_p = {k: v for k, v in p.items() if not k.startswith('_')}
            if "items" in clean_p and isinstance(clean_p["items"], list):
                clean_p["items"] = [cls._clean_item_for_manifest(it) for it in clean_p["items"]]
            clean_pages.append(clean_p)
        clean_doc["pages"] = clean_pages

        tmp_path = f"{path}.tmp"
        with zipfile.ZipFile(tmp_path, 'w') as zout:
            # 1. STORED assets first
            for filename, content in stored_entries.items():
                zinfo = zipfile.ZipInfo(filename)
                zinfo.compress_type = zipfile.ZIP_STORED
                zout.writestr(zinfo, content)

            # 2. DEFLATED tail entries next
            for filename, content in tail_entries.items():
                zinfo = zipfile.ZipInfo(filename)
                zinfo.compress_type = zipfile.ZIP_DEFLATED
                zout.writestr(zinfo, content)

            # 3. Manifest LAST
            zinfo = zipfile.ZipInfo("manifest.json")
            zinfo.compress_type = zipfile.ZIP_DEFLATED
            zout.writestr(
                zinfo,
                json.dumps(clean_doc, separators=(',', ':')).encode('utf-8')
            )

        os.replace(tmp_path, path)

    @classmethod
    def resolve_template(cls, ref, templates):
        if not ref or not isinstance(ref, str):
            return None
        if ref in templates:
            return templates[ref]
        tid = cls.extract_template_id(ref)
        if tid in templates:
            return templates[tid]
        base = os.path.basename(ref)
        if base in templates:
            return templates[base]
        if ref.endswith('.xtemplate') and ref[:-10] in templates:
            return templates[ref[:-10]]
        if f"{ref}.xtemplate" in templates:
            return templates[f"{ref}.xtemplate"]
        return None