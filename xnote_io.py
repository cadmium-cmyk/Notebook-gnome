import os
import io
import re
import json
import shutil
import hashlib
import zipfile
import tempfile
import datetime
import subprocess
import xml.etree.ElementTree as ET
import cairo
import gi

gi.require_version('Gtk', '4.0')
from gi.repository import Gio, GLib, Gdk, GdkPixbuf

try:
    gi.require_version('Poppler', '0.18')
    from gi.repository import Poppler
    HAS_POPPLER = True
except (ValueError, ImportError):
    Poppler = None
    HAS_POPPLER = False

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

        for i in range(len(s) - 1, 0, -1):
            ch = s[i]
            if ch in ('+', '-'):
                left = s[:i].strip()
                right = s[i + 1:].strip()
                if left and right:
                    lval = XTemplateEvaluator.eval_length(left, dpi, total, params)
                    rval = XTemplateEvaluator.eval_length(right, dpi, total, params)
                    return lval + rval if ch == '+' else lval - rval

        if params and s in params:
            p_val = params[s]
            if isinstance(p_val, dict):
                p_val = p_val.get("value", p_val.get("default", 0.0))
            return XTemplateEvaluator.eval_length(p_val, dpi, total, params)

        if s.endswith('%'):
            try:
                return (float(s[:-1].strip()) / 100.0) * total
            except ValueError:
                return 0.0

        m = re.match(r'^([+-]?\d+(?:\.\d+)?)\s*(mm|cm|in|pt|px)?$', s, re.IGNORECASE)
        if m:
            val = float(m.group(1))
            unit = (m.group(2) or 'px').lower()
            if unit == 'mm':
                return val * (dpi / 25.4)
            elif unit == 'cm':
                return val * (dpi / 2.54)
            elif unit == 'in':
                return val * dpi
            elif unit == 'pt':
                return val * (dpi / 72.0)
            return val

        return 0.0


class XNotePackageParser:
    """Unified loader, PDF importer/exporter, and writer for legacy and v1 .xnote archives."""

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
        bytes_data = GLib.Bytes.new(data)
        stream = Gio.MemoryInputStream.new_from_bytes(bytes_data)
        pixbuf = GdkPixbuf.Pixbuf.new_from_stream(stream, None)
        if pixbuf:
            return cls.pixbuf_to_cairo_surface(pixbuf)
        return None

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
    def _render_pdf_with_pdftoppm(cls, pdf_bytes, dpi=150):
        """Fallback PDF rasterizer using pdftoppm if gir1.2-poppler-0.18 is not installed."""
        surfaces = []
        if not shutil.which("pdftoppm"):
            return surfaces
        with tempfile.TemporaryDirectory() as tmpdir:
            pdf_path = os.path.join(tmpdir, "input.pdf")
            with open(pdf_path, "wb") as f:
                f.write(pdf_bytes)
            out_prefix = os.path.join(tmpdir, "page")
            try:
                subprocess.run(
                    ["pdftoppm", "-png", "-r", str(int(dpi)), pdf_path, out_prefix],
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
        """
        Import pages from an external PDF file into an existing .xnote document.
        Uses Poppler vector rendering if available, or pdftoppm image assets as fallback.
        """
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

            # Store PDF inside archive for native Poppler rendering
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

        # Fallback: rasterize via pdftoppm into assets/
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
        """Create a new .xnote archive populated with all pages from a PDF file."""
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
        t_bytes = json.dumps(DEFAULT_RULED_TEMPLATE, indent=2).encode("utf-8")
        templates = {
            DEFAULT_RULED_TEMPLATE_ID: {
                "id": DEFAULT_RULED_TEMPLATE_ID,
                "path": f"templates/{DEFAULT_RULED_TEMPLATE_ID}.xtemplate",
                "is_cover": False,
                "meta": DEFAULT_RULED_TEMPLATE,
                "surface": None,
                "images": {},
                "raw_bytes": t_bytes,
            }
        }
        cls.import_pdf_into_document(pdf_path, doc, assets)
        cls.save_archive(dest_xnote_path, doc, templates=templates)
        return cls.load_archive(dest_xnote_path)

    @classmethod
    def export_to_pdf(cls, pdf_path, doc, canvas):
        """
        Export all pages of the current .xnote document (including templates, embedded PDF
        backgrounds, calligraphic strokes, text, and images) to a vector PDF file.
        """
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
                cr, doc, page, pw, ph, canvas.assets, canvas.templates, page_idx=idx
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
        if not zipfile.is_zipfile(path):
            raise ValueError("Not a valid ZIP-based notebook archive.")

        doc = {}
        assets = {}
        templates = {}
        raw_assets = {}
        flow_info = None
        pdf_bytes = None
        pdf_archive_name = None

        with zipfile.ZipFile(path, 'r') as z:
            namelist = z.namelist()

            if 'manifest.json' in namelist:
                with z.open('manifest.json') as f:
                    doc = json.load(f)
            elif 'template.json' in namelist:
                with z.open('template.json') as f:
                    t_meta = json.load(f)
                with open(path, 'rb') as raw_f:
                    cls._load_xtemplate_bytes(os.path.basename(path), raw_f.read(), assets, templates)
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

            for name in namelist:
                if name.endswith('/') or name in ('manifest.json', 'flow.xml', 'template.json'):
                    continue

                lower_name = name.lower()
                try:
                    raw_data = z.read(name)
                except Exception as e:
                    print(f"Failed to read {name} from archive: {e}")
                    continue

                if lower_name.endswith('.pdf'):
                    pdf_bytes = raw_data
                    pdf_archive_name = name
                elif lower_name.endswith('.xtemplate') or name.startswith('templates/'):
                    cls._load_xtemplate_bytes(name, raw_data, assets, templates)
                elif name.startswith('assets/'):
                    if lower_name.endswith('.xtemplate') or zipfile.is_zipfile(io.BytesIO(raw_data)):
                        cls._load_xtemplate_bytes(name, raw_data, assets, templates)
                    else:
                        try:
                            surf = cls.bytes_to_surface(raw_data)
                            if surf:
                                assets[name] = surf
                                assets[os.path.basename(name)] = surf
                                raw_assets[name] = raw_data
                        except Exception as e:
                            print(f"Failed to load asset {name}: {e}")

        if flow_info:
            doc["_flow"] = flow_info
        if raw_assets:
            doc["_raw_assets"] = raw_assets
        if pdf_bytes:
            doc["_pdf_bytes"] = pdf_bytes
            doc["_pdf_archive_name"] = pdf_archive_name or "document.pdf"
            poppler_doc = cls._load_poppler_document_from_bytes(pdf_bytes)
            if poppler_doc is not None:
                doc["_poppler_doc"] = poppler_doc
            else:
                dpi = float(doc.get("dpi", 150.0))
                doc["_pdf_surfaces"] = cls._render_pdf_with_pdftoppm(pdf_bytes, dpi=dpi)

        return doc, assets, templates

    @classmethod
    def create_new_notebook(cls, path):
        now_iso = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        doc = {
            "format": "xnote",
            "version": 1,
            "writer": 48,
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
        t_bytes = json.dumps(DEFAULT_RULED_TEMPLATE, indent=2).encode("utf-8")
        t_path = f"templates/{DEFAULT_RULED_TEMPLATE_ID}.xtemplate"
        templates = {
            DEFAULT_RULED_TEMPLATE_ID: {
                "id": DEFAULT_RULED_TEMPLATE_ID,
                "path": t_path,
                "is_cover": False,
                "meta": DEFAULT_RULED_TEMPLATE,
                "surface": None,
                "images": {},
                "raw_bytes": t_bytes,
            }
        }
        cls.save_archive(path, doc, templates=templates)
        return cls.load_archive(path)

    @classmethod
    def save_archive(cls, path, doc, templates=None):
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

        for asset_path, raw_bytes in doc.get("_raw_assets", {}).items():
            if asset_path and raw_bytes:
                existing_entries[asset_path] = raw_bytes

        if doc.get("_pdf_bytes"):
            pdf_name = doc.get("_pdf_archive_name", "document.pdf")
            existing_entries[pdf_name] = doc["_pdf_bytes"]

        clean_doc = {k: v for k, v in doc.items() if not k.startswith('_')}
        clean_pages = []
        for p in doc.get("pages", []):
            clean_p = {k: v for k, v in p.items() if not k.startswith('_')}
            clean_pages.append(clean_p)
        clean_doc["pages"] = clean_pages

        tmp_path = f"{path}.tmp"
        with zipfile.ZipFile(tmp_path, 'w', compression=zipfile.ZIP_DEFLATED) as zout:
            for filename, content in existing_entries.items():
                zout.writestr(filename, content)
            zout.writestr(
                'manifest.json',
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
        return None
