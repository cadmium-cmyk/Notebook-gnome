import os
import time
import math
import copy
import cairo
import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Pango', '1.0')
gi.require_version('PangoCairo', '1.0')
from gi.repository import Gtk, GLib, Gdk, Pango, PangoCairo

from xnote_io import XTemplateEvaluator, XNotePackageParser


class XNoteCanvas(Gtk.DrawingArea):
    """Interactive Cairo canvas supporting smoothed strokes, velocity pen, lasso selection, PDF pages, and images."""

    def __init__(self, on_modified_cb=None, on_history_changed_cb=None, on_selection_changed_cb=None):
        super().__init__()
        self.set_halign(Gtk.Align.CENTER)
        self.set_valign(Gtk.Align.CENTER)
        self.set_focusable(True)
        self.set_draw_func(self.on_draw)

        self.on_modified_cb = on_modified_cb
        self.on_history_changed_cb = on_history_changed_cb
        self.on_selection_changed_cb = on_selection_changed_cb

        # Document state
        self.document = None
        self.assets = {}
        self.templates = {}
        self.current_page = 0
        self.zoom_level = 1.0
        self.view_mode = "paged"  # "paged" or "scrolled"
        self.page_geometries = []
        self.page_sizes = []
        self.page_cache = {}
        self.vadj = None

        # Stylus / Tool settings:
        # "select", "lasso", "pen", "velocity_pen", "calligraphy", "highlighter", "eraser", "text"
        self.active_tool = "pen"
        self.pen_rgba = [20, 24, 35, 255]
        self.pen_width = 3.6
        self.highlighter_width = 22.0
        self.eraser_radius = 16.0
        self.text_size_pt = 14.0
        self.nib_angle_rad = math.radians(35.0)

        # Active stroke & smoothing state
        self._active_stroke = None
        self._active_page_idx = None
        self._stylus_active = False
        self._effective_tool = "pen"
        self._eraser_cursor = None
        self._erased_during_drag = False
        self._smoothed_pressure = 0.65
        self._last_sample_time = 0.0
        self._last_raw_pt = (0.0, 0.0)

        # Active single-image selection & transform state
        self.keep_aspect_ratio = True
        self.selected_page_idx = None
        self.selected_item_idx = None
        self._transform_mode = None  # "move", "rotate", "nw", "ne", "se", "sw"
        self._transform_start_page_pt = (0.0, 0.0)
        self._transform_orig_rect = None
        self._transform_orig_angle = 0.0
        self._transform_modified = False

        # Lasso multi-item selection & transform state
        self._lasso_polygon = []          # [(px, py), ...] while drawing freehand loop
        self.lasso_page_idx = None
        self.lasso_item_indices = []      # indices of selected items on lasso_page_idx
        self.lasso_bbox = None            # [x, y, w, h] in page coords
        self._lasso_transform_mode = None  # "move", "nw", "ne", "se", "sw"
        self._lasso_start_pt = (0.0, 0.0)
        self._lasso_orig_bbox = None
        self._lasso_orig_items = None
        self._lasso_modified = False

        # Undo / Redo stacks storing (page_idx, old_items_copy, new_items_copy)
        self.undo_stack = []
        self.redo_stack = []
        self._pre_drag_items_snapshot = None

        self._setup_controllers()

    def _setup_controllers(self):
        self.stylus_gesture = Gtk.GestureStylus.new()
        self.stylus_gesture.connect("down", self._on_stylus_down)
        self.stylus_gesture.connect("motion", self._on_stylus_motion)
        self.stylus_gesture.connect("up", self._on_stylus_up)
        self.add_controller(self.stylus_gesture)

        self.drag_gesture = Gtk.GestureDrag.new()
        self.drag_gesture.set_button(Gdk.BUTTON_PRIMARY)
        self.drag_gesture.connect("drag-begin", self._on_drag_begin)
        self.drag_gesture.connect("drag-update", self._on_drag_update)
        self.drag_gesture.connect("drag-end", self._on_drag_end)
        self.add_controller(self.drag_gesture)

    # =========================================================================
    # Coordinate Mapping & Geometry
    # =========================================================================
    def compute_page_size(self, page):
        w = page.get("width", 0)
        h = page.get("height", 0)
        if w > 0 and h > 0:
            return float(w), float(h)

        max_x, max_y = 1240.16, 1753.94
        for item in page.get("items", []):
            if not isinstance(item, dict):
                continue
            kind = item.get("kind") or item.get("type")
            if kind == "stroke":
                for s in item.get("samples", []):
                    if len(s) >= 2:
                        max_x = max(max_x, s[0] + 50.0)
                        max_y = max(max_y, s[1] + 50.0)
            elif kind == "text":
                pos = item.get("pos", [0, 0])
                width = item.get("width", 300.0)
                if len(pos) >= 2:
                    max_x = max(max_x, pos[0] + width + 50.0)
                    max_y = max(max_y, pos[1] + 100.0)
            elif kind in ("image", "picture"):
                rect = item.get("rect", [0, 0, 100, 100])
                if len(rect) >= 4:
                    max_x = max(max_x, rect[0] + rect[2] + 50.0)
                    max_y = max(max_y, rect[1] + rect[3] + 50.0)
        return max_x, max_y

    def get_page_size(self, page_idx):
        if 0 <= page_idx < len(self.page_sizes):
            return self.page_sizes[page_idx]
        pages = self.document.get("pages", []) if self.document else []
        if 0 <= page_idx < len(pages):
            return self.compute_page_size(pages[page_idx])
        return 1240.16, 1753.94

    def get_page_widget_offset(self, page_idx):
        width = self.get_width()
        height = self.get_height()
        pw, ph = self.get_page_size(page_idx)
        scaled_w = pw * self.zoom_level
        scaled_h = ph * self.zoom_level
        if self.view_mode == "paged":
            offset_x = max(20.0, (width - scaled_w) / 2.0)
            offset_y = max(20.0, (height - 44.0 - scaled_h) / 2.0)
            return offset_x, offset_y
        else:
            offset_x = max(20.0, (width - scaled_w) / 2.0)
            offset_y = self.page_geometries[page_idx][0] if page_idx < len(self.page_geometries) else 20.0
            return offset_x, offset_y

    def widget_to_page_coords(self, wx, wy):
        if not self.document:
            return None, 0.0, 0.0
        pages = self.document.get("pages", [])
        if not pages:
            return None, 0.0, 0.0

        width = self.get_width()
        height = self.get_height()

        if self.view_mode == "paged":
            if self.current_page >= len(pages):
                return None, 0.0, 0.0
            pw, ph = self.get_page_size(self.current_page)
            scaled_w = pw * self.zoom_level
            scaled_h = ph * self.zoom_level
            offset_x = max(20.0, (width - scaled_w) / 2.0)
            offset_y = max(20.0, (height - 44.0 - scaled_h) / 2.0)
            px = (wx - offset_x) / self.zoom_level
            py = (wy - offset_y) / self.zoom_level
            if 0.0 <= px <= pw and 0.0 <= py <= ph:
                return self.current_page, px, py
            return None, 0.0, 0.0
        else:
            for idx, (y_offset, pw, ph, scaled_w, scaled_h) in enumerate(self.page_geometries):
                offset_x = max(20.0, (width - scaled_w) / 2.0)
                if offset_x <= wx <= offset_x + scaled_w and y_offset <= wy <= y_offset + scaled_h:
                    px = (wx - offset_x) / self.zoom_level
                    py = (wy - y_offset) / self.zoom_level
                    return idx, px, py
            return None, 0.0, 0.0

    # =========================================================================
    # Brush Stroke Smoothing (Chaikin Corner-Cutting Subdivision)
    # =========================================================================
    @staticmethod
    def smooth_samples_chaikin(samples, iterations=2):
        """
        Smooth a sequence of [x, y, pressure] samples using Chaikin's corner-cutting
        algorithm while preserving start and end points and interpolating pressure.
        """
        if not samples or len(samples) < 3:
            return samples

        pts = []
        for s in samples:
            if isinstance(s, dict):
                pts.append([float(s.get("x", 0.0)), float(s.get("y", 0.0)), float(s.get("pressure", 0.65))])
            else:
                px = float(s[0]) if len(s) > 0 else 0.0
                py = float(s[1]) if len(s) > 1 else 0.0
                pr = float(s[2]) if len(s) > 2 else 0.65
                pts.append([px, py, pr])

        for _ in range(iterations):
            if len(pts) < 3 or len(pts) > 1200:
                break
            new_pts = [pts[0]]
            for i in range(len(pts) - 1):
                p0 = pts[i]
                p1 = pts[i + 1]
                q = [
                    round(0.75 * p0[0] + 0.25 * p1[0], 2),
                    round(0.75 * p0[1] + 0.25 * p1[1], 2),
                    round(0.75 * p0[2] + 0.25 * p1[2], 3),
                ]
                r = [
                    round(0.25 * p0[0] + 0.75 * p1[0], 2),
                    round(0.25 * p0[1] + 0.75 * p1[1], 2),
                    round(0.25 * p0[2] + 0.75 * p1[2], 3),
                ]
                new_pts.append(q)
                new_pts.append(r)
            new_pts.append(pts[-1])
            pts = new_pts

        return pts

    # =========================================================================
    # Lasso Selection & Recoloring / Transform API
    # =========================================================================
    @staticmethod
    def _point_in_polygon(px, py, poly):
        n = len(poly)
        if n < 3:
            return False
        inside = False
        j = n - 1
        for i in range(n):
            xi, yi = poly[i]
            xj, yj = poly[j]
            if ((yi > py) != (yj > py)) and (px < (xj - xi) * (py - yi) / ((yj - yi) or 1e-9) + xi):
                inside = not inside
            j = i
        return inside

    def has_lasso_selection(self):
        return bool(self.lasso_item_indices and self.lasso_page_idx is not None and self.lasso_bbox is not None)

    def _compute_items_bbox(self, page_idx, indices):
        if not self.document or page_idx is None or not indices:
            return None
        pages = self.document.get("pages", [])
        if not (0 <= page_idx < len(pages)):
            return None
        items = pages[page_idx].get("items", [])

        min_x, min_y = float("inf"), float("inf")
        max_x, max_y = float("-inf"), float("-inf")

        for idx in indices:
            if not (0 <= idx < len(items)):
                continue
            item = items[idx]
            kind = item.get("kind") or item.get("type")
            if kind == "stroke":
                hw = float(item.get("stroke_width", 3.0)) / 2.0
                for s in item.get("samples") or item.get("points") or []:
                    sx = float(s["x"]) if isinstance(s, dict) else float(s[0])
                    sy = float(s["y"]) if isinstance(s, dict) else float(s[1])
                    min_x = min(min_x, sx - hw)
                    min_y = min(min_y, sy - hw)
                    max_x = max(max_x, sx + hw)
                    max_y = max(max_y, sy + hw)
            elif kind == "text":
                pos = item.get("pos", [0.0, 0.0])
                tw = float(item.get("width", 240.0))
                th = float(item.get("point_size", 14.0)) * 2.2
                min_x = min(min_x, pos[0])
                min_y = min(min_y, pos[1])
                max_x = max(max_x, pos[0] + tw)
                max_y = max(max_y, pos[1] + th)
            elif kind in ("image", "picture"):
                rect = item.get("rect", [0.0, 0.0, 100.0, 100.0])
                min_x = min(min_x, rect[0])
                min_y = min(min_y, rect[1])
                max_x = max(max_x, rect[0] + rect[2])
                max_y = max(max_y, rect[1] + rect[3])

        if min_x == float("inf"):
            return None
        pad = 6.0
        return [min_x - pad, min_y - pad, max(16.0, (max_x - min_x) + pad * 2), max(16.0, (max_y - min_y) + pad * 2)]

    def _hit_test_lasso_handles(self, px, py):
        if not self.has_lasso_selection():
            return None
        bx, by, bw, bh = self.lasso_bbox
        tol = 14.0 / max(0.25, self.zoom_level)
        corners = {
            "nw": (bx, by),
            "ne": (bx + bw, by),
            "se": (bx + bw, by + bh),
            "sw": (bx, by + bh),
        }
        for name, (hx, hy) in corners.items():
            if math.hypot(px - hx, py - hy) <= tol:
                return name
        if bx <= px <= bx + bw and by <= py <= by + bh:
            return "move"
        return None

    def recolor_lasso_selection(self, rgba):
        if not self.has_lasso_selection() or not self.document:
            return False
        pages = self.document.get("pages", [])
        if not (0 <= self.lasso_page_idx < len(pages)):
            return False

        page = pages[self.lasso_page_idx]
        items = page.get("items", [])
        old_items = copy.deepcopy(items)
        changed = False

        for idx in self.lasso_item_indices:
            if not (0 <= idx < len(items)):
                continue
            item = items[idx]
            kind = item.get("kind") or item.get("type")
            if kind == "stroke":
                old_rgba = item.get("stroke_rgba", [0, 0, 0, 255])
                alpha = old_rgba[3] if len(old_rgba) >= 4 else 255
                item["stroke_rgba"] = [rgba[0], rgba[1], rgba[2], alpha]
                changed = True
            elif kind == "text":
                item["rgba"] = [rgba[0], rgba[1], rgba[2], 255]
                changed = True

        if changed:
            self._record_undo_step(self.lasso_page_idx, old_items, items)
            self.invalidate_page(self.lasso_page_idx)
        return changed

    def resize_lasso_stroke_width(self, new_width):
        if not self.has_lasso_selection() or not self.document:
            return False
        pages = self.document.get("pages", [])
        if not (0 <= self.lasso_page_idx < len(pages)):
            return False

        page = pages[self.lasso_page_idx]
        items = page.get("items", [])
        old_items = copy.deepcopy(items)
        changed = False

        for idx in self.lasso_item_indices:
            if 0 <= idx < len(items):
                item = items[idx]
                if (item.get("kind") or item.get("type")) == "stroke":
                    item["stroke_width"] = round(float(new_width), 2)
                    changed = True

        if changed:
            self.lasso_bbox = self._compute_items_bbox(self.lasso_page_idx, self.lasso_item_indices)
            self._record_undo_step(self.lasso_page_idx, old_items, items)
            self.invalidate_page(self.lasso_page_idx)
        return changed

    # =========================================================================
    # Image & Lasso Selection Management API
    # =========================================================================
    def get_selected_item(self):
        if self.document is None or self.selected_page_idx is None or self.selected_item_idx is None:
            return None
        pages = self.document.get("pages", [])
        if 0 <= self.selected_page_idx < len(pages):
            items = pages[self.selected_page_idx].get("items", [])
            if 0 <= self.selected_item_idx < len(items):
                return items[self.selected_item_idx]
        return None

    def clear_selection(self):
        had_sel = (
            self.selected_page_idx is not None
            or self.selected_item_idx is not None
            or bool(self.lasso_item_indices)
            or bool(self._lasso_polygon)
        )
        self.selected_page_idx = None
        self.selected_item_idx = None
        self.lasso_page_idx = None
        self.lasso_item_indices = []
        self.lasso_bbox = None
        self._lasso_polygon = []
        self._lasso_transform_mode = None
        if had_sel:
            if self.on_selection_changed_cb:
                self.on_selection_changed_cb(False, None)
            self.queue_draw()

    def select_item(self, page_idx, item_idx):
        self.lasso_page_idx = None
        self.lasso_item_indices = []
        self.lasso_bbox = None
        self.selected_page_idx = page_idx
        self.selected_item_idx = item_idx
        if self.on_selection_changed_cb:
            self.on_selection_changed_cb(self.get_selected_item() is not None, "image")
        self.queue_draw()

    def add_image_to_page(self, page_idx, asset_name, surface):
        if not self.document:
            return
        pages = self.document.get("pages", [])
        if not (0 <= page_idx < len(pages)):
            return

        page = pages[page_idx]
        old_items = copy.deepcopy(page.get("items", []))
        pw, ph = self.get_page_size(page_idx)
        iw = float(surface.get_width())
        ih = float(surface.get_height())

        max_w = pw * 0.55
        max_h = ph * 0.55
        scale = min(1.0, max_w / max(1.0, iw), max_h / max(1.0, ih))
        w = round(iw * scale, 2)
        h = round(ih * scale, 2)
        x = round((pw - w) / 2.0, 2)
        y = round((ph - h) / 2.0, 2)

        new_item = {
            "kind": "image",
            "asset": asset_name,
            "rect": [x, y, w, h],
            "angle": 0.0,
            "flip_h": False,
            "flip_v": False,
        }
        page.setdefault("items", []).append(new_item)
        self._record_undo_step(page_idx, old_items, page["items"])
        self.invalidate_page(page_idx)
        self.select_item(page_idx, len(page["items"]) - 1)

    def reset_selected_image_aspect_ratio(self):
        item = self.get_selected_item()
        if not item:
            return
        asset_name = item.get("asset") or item.get("src") or item.get("image")
        surf = self._resolve_asset_surface(asset_name, self.assets, self.templates)
        if not surf or surf.get_width() <= 0 or surf.get_height() <= 0:
            return

        pages = self.document.get("pages", [])
        page = pages[self.selected_page_idx]
        old_items = copy.deepcopy(page.get("items", []))

        x, y, w, h = item.get("rect", [0.0, 0.0, 100.0, 100.0])
        cx = x + w / 2.0
        cy = y + h / 2.0
        native_ratio = float(surf.get_width()) / float(surf.get_height())
        new_w = w
        new_h = round(new_w / native_ratio, 2)
        item["rect"] = [round(cx - new_w / 2.0, 2), round(cy - new_h / 2.0, 2), round(new_w, 2), new_h]

        self._record_undo_step(self.selected_page_idx, old_items, page["items"])
        self.invalidate_page(self.selected_page_idx)

    def rotate_selected_image(self, delta_radians):
        item = self.get_selected_item()
        if not item:
            return
        pages = self.document.get("pages", [])
        page = pages[self.selected_page_idx]
        old_items = copy.deepcopy(page.get("items", []))
        item["angle"] = round(float(item.get("angle", 0.0)) + delta_radians, 4)
        self._record_undo_step(self.selected_page_idx, old_items, page["items"])
        self.invalidate_page(self.selected_page_idx)

    def flip_selected_image(self, horizontal=True):
        item = self.get_selected_item()
        if not item:
            return
        pages = self.document.get("pages", [])
        page = pages[self.selected_page_idx]
        old_items = copy.deepcopy(page.get("items", []))
        if horizontal:
            item["flip_h"] = not bool(item.get("flip_h", False))
        else:
            item["flip_v"] = not bool(item.get("flip_v", False))
        self._record_undo_step(self.selected_page_idx, old_items, page["items"])
        self.invalidate_page(self.selected_page_idx)

    def reorder_selected_image(self, to_front=True):
        item = self.get_selected_item()
        if not item:
            return
        pages = self.document.get("pages", [])
        page = pages[self.selected_page_idx]
        items = page.get("items", [])
        if len(items) <= 1:
            return
        old_items = copy.deepcopy(items)
        popped = items.pop(self.selected_item_idx)
        if to_front:
            items.append(popped)
            self.selected_item_idx = len(items) - 1
        else:
            items.insert(0, popped)
            self.selected_item_idx = 0
        self._record_undo_step(self.selected_page_idx, old_items, items)
        self.invalidate_page(self.selected_page_idx)

    def delete_selected_item(self):
        if self.has_lasso_selection() and self.document:
            pages = self.document.get("pages", [])
            if 0 <= self.lasso_page_idx < len(pages):
                page = pages[self.lasso_page_idx]
                old_items = copy.deepcopy(page.get("items", []))
                remove_set = set(self.lasso_item_indices)
                page["items"] = [it for i, it in enumerate(page.get("items", [])) if i not in remove_set]
                p_idx = self.lasso_page_idx
                self.clear_selection()
                self._record_undo_step(p_idx, old_items, page["items"])
                self.invalidate_page(p_idx)
            return

        item = self.get_selected_item()
        if not item:
            return
        pages = self.document.get("pages", [])
        page = pages[self.selected_page_idx]
        old_items = copy.deepcopy(page.get("items", []))
        page["items"].pop(self.selected_item_idx)
        p_idx = self.selected_page_idx
        self.clear_selection()
        self._record_undo_step(p_idx, old_items, page["items"])
        self.invalidate_page(p_idx)

    @staticmethod
    def _page_to_item_local(px, py, item):
        rect = item.get("rect", [0.0, 0.0, 100.0, 100.0])
        cx = rect[0] + rect[2] / 2.0
        cy = rect[1] + rect[3] / 2.0
        angle = float(item.get("angle", 0.0))
        dx = px - cx
        dy = py - cy
        cos_a = math.cos(-angle)
        sin_a = math.sin(-angle)
        return dx * cos_a - dy * sin_a, dx * sin_a + dy * cos_a

    @staticmethod
    def _item_local_to_page(lx, ly, cx, cy, angle):
        cos_a = math.cos(angle)
        sin_a = math.sin(angle)
        return cx + lx * cos_a - ly * sin_a, cy + lx * sin_a + ly * cos_a

    def _hit_test_selection_handles(self, px, py):
        item = self.get_selected_item()
        if not item or (item.get("kind") or item.get("type")) not in ("image", "picture"):
            return None
        rect = item.get("rect", [0.0, 0.0, 100.0, 100.0])
        hw = rect[2] / 2.0
        hh = rect[3] / 2.0
        lx, ly = self._page_to_item_local(px, py, item)
        tol = 14.0 / max(0.25, self.zoom_level)
        rot_offset = 32.0 / max(0.25, self.zoom_level)

        handles = {
            "rotate": (0.0, -hh - rot_offset),
            "nw": (-hw, -hh),
            "ne": (hw, -hh),
            "se": (hw, hh),
            "sw": (-hw, hh),
        }
        for name, (hx, hy) in handles.items():
            if math.hypot(lx - hx, ly - hy) <= tol:
                return name
        if -hw <= lx <= hw and -hh <= ly <= hh:
            return "move"
        return None

    def _find_topmost_image_at(self, page_idx, px, py):
        pages = self.document.get("pages", [])
        if not (0 <= page_idx < len(pages)):
            return None
        items = pages[page_idx].get("items", [])
        for idx in range(len(items) - 1, -1, -1):
            item = items[idx]
            if (item.get("kind") or item.get("type")) in ("image", "picture"):
                rect = item.get("rect", [0.0, 0.0, 100.0, 100.0])
                hw, hh = rect[2] / 2.0, rect[3] / 2.0
                lx, ly = self._page_to_item_local(px, py, item)
                if -hw <= lx <= hw and -hh <= ly <= hh:
                    return idx
        return None

    # =========================================================================
    # Undo / Redo Management
    # =========================================================================
    def _record_undo_step(self, page_idx, old_items, new_items):
        self.undo_stack.append((page_idx, copy.deepcopy(old_items), copy.deepcopy(new_items)))
        self.redo_stack.clear()
        if self.on_history_changed_cb:
            self.on_history_changed_cb(len(self.undo_stack) > 0, len(self.redo_stack) > 0)
        if self.on_modified_cb:
            self.on_modified_cb(True)

    def undo(self):
        if not self.undo_stack or not self.document:
            return
        self.clear_selection()
        page_idx, old_items, new_items = self.undo_stack.pop()
        self.redo_stack.append((page_idx, old_items, new_items))
        pages = self.document.get("pages", [])
        if 0 <= page_idx < len(pages):
            pages[page_idx]["items"] = copy.deepcopy(old_items)
            self.invalidate_page(page_idx)
        if self.on_history_changed_cb:
            self.on_history_changed_cb(len(self.undo_stack) > 0, len(self.redo_stack) > 0)
        if self.on_modified_cb:
            self.on_modified_cb(True)

    def redo(self):
        if not self.redo_stack or not self.document:
            return
        self.clear_selection()
        page_idx, old_items, new_items = self.redo_stack.pop()
        self.undo_stack.append((page_idx, old_items, new_items))
        pages = self.document.get("pages", [])
        if 0 <= page_idx < len(pages):
            pages[page_idx]["items"] = copy.deepcopy(new_items)
            self.invalidate_page(page_idx)
        if self.on_history_changed_cb:
            self.on_history_changed_cb(len(self.undo_stack) > 0, len(self.redo_stack) > 0)
        if self.on_modified_cb:
            self.on_modified_cb(True)

    def invalidate_page(self, page_idx=None):
        if page_idx is None:
            self.page_cache.clear()
        else:
            keys_to_remove = [k for k in self.page_cache if k[0] == page_idx]
            for k in keys_to_remove:
                del self.page_cache[k]
        self.queue_draw()

    # =========================================================================
    # Stylus & Mouse Input Handling
    # =========================================================================
    def _is_shift_held(self, is_stylus=False):
        gesture = self.stylus_gesture if is_stylus else self.drag_gesture
        event = gesture.get_current_event()
        if event is not None:
            state = event.get_modifier_state()
            return bool(state & Gdk.ModifierType.SHIFT_MASK)
        return False

    def _detect_effective_tool(self, is_stylus=False):
        if is_stylus:
            device_tool = self.stylus_gesture.get_device_tool()
            if device_tool:
                tool_type = device_tool.get_tool_type()
                if tool_type == Gdk.DeviceToolType.ERASER:
                    return "eraser"
            if self.stylus_gesture.get_current_button() in (2, 3):
                return "eraser"
        return self.active_tool

    def _get_stylus_pressure(self):
        ok, pressure = self.stylus_gesture.get_axis(Gdk.AxisUse.PRESSURE)
        if ok and pressure is not None and pressure > 0.0:
            return max(0.05, min(1.0, float(pressure)))
        return 0.65

    def _begin_interaction(self, wx, wy, pressure=0.65, is_stylus=False):
        page_idx, px, py = self.widget_to_page_coords(wx, wy)
        if page_idx is None:
            self.clear_selection()
            return

        self._effective_tool = self._detect_effective_tool(is_stylus)
        self._active_page_idx = page_idx
        self._smoothed_pressure = pressure
        self._last_sample_time = time.perf_counter()
        self._last_raw_pt = (px, py)
        pages = self.document.get("pages", [])
        page = pages[page_idx]
        page.setdefault("items", [])

        if self._effective_tool == "lasso" or (self.lasso_page_idx == page_idx and self._hit_test_lasso_handles(px, py) is not None):
            handle = self._hit_test_lasso_handles(px, py) if self.lasso_page_idx == page_idx else None
            if handle is not None:
                self._effective_tool = "lasso"
                self._lasso_transform_mode = handle
                self._lasso_start_pt = (px, py)
                self._lasso_orig_bbox = list(self.lasso_bbox)
                self._lasso_orig_items = copy.deepcopy(page["items"])
                self._pre_drag_items_snapshot = copy.deepcopy(page["items"])
                self._lasso_modified = False
                return

            self.clear_selection()
            self._effective_tool = "lasso"
            self._lasso_polygon = [(px, py)]
            self.queue_draw()
            return

        if self._effective_tool == "select" or (self.selected_page_idx == page_idx and self._hit_test_selection_handles(px, py) is not None):
            handle = self._hit_test_selection_handles(px, py) if self.selected_page_idx == page_idx else None
            if handle is None:
                hit_idx = self._find_topmost_image_at(page_idx, px, py)
                if hit_idx is not None:
                    self.select_item(page_idx, hit_idx)
                    handle = "move"
                else:
                    self.clear_selection()
                    self._active_page_idx = None
                    return

            item = self.get_selected_item()
            if item:
                self._effective_tool = "select"
                self._transform_mode = handle
                self._transform_start_page_pt = (px, py)
                self._transform_orig_rect = list(item.get("rect", [0.0, 0.0, 100.0, 100.0]))
                self._transform_orig_angle = float(item.get("angle", 0.0))
                self._pre_drag_items_snapshot = copy.deepcopy(page["items"])
                self._transform_modified = False
            return

        self.clear_selection()

        if self._effective_tool == "text":
            self._prompt_text_insertion(wx, wy, page_idx, px, py)
            self._active_page_idx = None
            return

        self._pre_drag_items_snapshot = copy.deepcopy(page["items"])

        if self._effective_tool == "eraser":
            self._eraser_cursor = (wx, wy)
            self._erased_during_drag = self._erase_at(page_idx, px, py)
            self.queue_draw()
            return

        if self._effective_tool == "highlighter":
            rgba = [self.pen_rgba[0], self.pen_rgba[1], self.pen_rgba[2], 90]
            width = self.highlighter_width
            variable_width = False
        else:
            rgba = [self.pen_rgba[0], self.pen_rgba[1], self.pen_rgba[2], 255]
            width = self.pen_width
            variable_width = True

        self._active_stroke = {
            "kind": "stroke",
            "tool": self._effective_tool,
            "variable_width": variable_width,
            "stroke_rgba": rgba,
            "stroke_width": round(width, 2),
            "samples": [[round(px, 2), round(py, 2), round(pressure, 3)]]
        }
        self.queue_draw()

    def _update_interaction(self, wx, wy, pressure=0.65, is_stylus=False):
        if self._active_page_idx is None:
            return

        page_idx, px, py = self.widget_to_page_coords(wx, wy)
        if page_idx != self._active_page_idx:
            pw, ph = self.get_page_size(self._active_page_idx)
            offset_x, offset_y = self.get_page_widget_offset(self._active_page_idx)
            px = max(0.0, min(pw, (wx - offset_x) / self.zoom_level))
            py = max(0.0, min(ph, (wy - offset_y) / self.zoom_level))

        if self._effective_tool == "lasso":
            if self._lasso_transform_mode is not None and self._lasso_orig_bbox and self._lasso_orig_items is not None:
                pages = self.document.get("pages", [])
                page = pages[self._active_page_idx]
                obx, oby, obw, obh = self._lasso_orig_bbox

                if self._lasso_transform_mode == "move":
                    dx = px - self._lasso_start_pt[0]
                    dy = py - self._lasso_start_pt[1]
                    for idx in self.lasso_item_indices:
                        orig_it = self._lasso_orig_items[idx]
                        cur_it = page["items"][idx]
                        kind = orig_it.get("kind") or orig_it.get("type")
                        if kind == "stroke":
                            orig_s = orig_it.get("samples") or orig_it.get("points") or []
                            new_s = []
                            for s in orig_s:
                                if isinstance(s, dict):
                                    new_s.append([round(s["x"] + dx, 2), round(s["y"] + dy, 2), s.get("pressure", 0.65)])
                                else:
                                    pr = s[2] if len(s) > 2 else 0.65
                                    new_s.append([round(s[0] + dx, 2), round(s[1] + dy, 2), pr])
                            cur_it["samples"] = new_s
                        elif kind == "text":
                            opos = orig_it.get("pos", [0.0, 0.0])
                            cur_it["pos"] = [round(opos[0] + dx, 2), round(opos[1] + dy, 2)]
                        elif kind in ("image", "picture"):
                            orect = orig_it.get("rect", [0.0, 0.0, 100.0, 100.0])
                            cur_it["rect"] = [round(orect[0] + dx, 2), round(orect[1] + dy, 2), orect[2], orect[3]]

                    self.lasso_bbox = [obx + dx, oby + dy, obw, obh]
                    self._lasso_modified = True
                    self.invalidate_page(self._active_page_idx)
                    return

                elif self._lasso_transform_mode in ("nw", "ne", "se", "sw"):
                    min_s = 24.0
                    if self._lasso_transform_mode == "se":
                        ax, ay = obx, oby
                        nw = max(min_s, px - ax)
                        nh = max(min_s, py - ay)
                        nx, ny = ax, ay
                    elif self._lasso_transform_mode == "nw":
                        ax, ay = obx + obw, oby + obh
                        nw = max(min_s, ax - px)
                        nh = max(min_s, ay - py)
                        nx, ny = ax - nw, ay - nh
                    elif self._lasso_transform_mode == "ne":
                        ax, ay = obx, oby + obh
                        nw = max(min_s, px - ax)
                        nh = max(min_s, ay - py)
                        nx, ny = ax, ay - nh
                    else:  # "sw"
                        ax, ay = obx + obw, oby
                        nw = max(min_s, ax - px)
                        nh = max(min_s, py - ay)
                        nx, ny = ax - nw, ay

                    sx = nw / max(1.0, obw)
                    sy = nh / max(1.0, obh)
                    if self.keep_aspect_ratio ^ self._is_shift_held(is_stylus):
                        s_uni = max(sx, sy)
                        sx = sy = s_uni
                        nw = obw * s_uni
                        nh = obh * s_uni
                        if self._lasso_transform_mode in ("nw", "sw"):
                            nx = (obx + obw) - nw
                        if self._lasso_transform_mode in ("nw", "ne"):
                            ny = (oby + obh) - nh

                    for idx in self.lasso_item_indices:
                        orig_it = self._lasso_orig_items[idx]
                        cur_it = page["items"][idx]
                        kind = orig_it.get("kind") or orig_it.get("type")
                        if kind == "stroke":
                            orig_s = orig_it.get("samples") or orig_it.get("points") or []
                            new_s = []
                            for s in orig_s:
                                ox_pt = float(s["x"]) if isinstance(s, dict) else float(s[0])
                                oy_pt = float(s["y"]) if isinstance(s, dict) else float(s[1])
                                pr = s.get("pressure", 0.65) if isinstance(s, dict) else (s[2] if len(s) > 2 else 0.65)
                                new_s.append([
                                    round(nx + (ox_pt - obx) * sx, 2),
                                    round(ny + (oy_pt - oby) * sy, 2),
                                    pr
                                ])
                            cur_it["samples"] = new_s
                        elif kind == "text":
                            opos = orig_it.get("pos", [0.0, 0.0])
                            cur_it["pos"] = [
                                round(nx + (opos[0] - obx) * sx, 2),
                                round(ny + (opos[1] - oby) * sy, 2)
                            ]
                        elif kind in ("image", "picture"):
                            orect = orig_it.get("rect", [0.0, 0.0, 100.0, 100.0])
                            cur_it["rect"] = [
                                round(nx + (orect[0] - obx) * sx, 2),
                                round(ny + (orect[1] - oby) * sy, 2),
                                round(orect[2] * sx, 2),
                                round(orect[3] * sy, 2)
                            ]

                    self.lasso_bbox = [nx, ny, nw, nh]
                    self._lasso_modified = True
                    self.invalidate_page(self._active_page_idx)
                    return

            if self._lasso_polygon:
                lx, ly = self._lasso_polygon[-1]
                if math.hypot(px - lx, py - ly) >= 2.5:
                    self._lasso_polygon.append((px, py))
                    self.queue_draw()
            return

        if self._effective_tool == "select" and self._transform_mode is not None:
            item = self.get_selected_item()
            if not item or not self._transform_orig_rect:
                return
            ox, oy, ow, oh = self._transform_orig_rect
            ocx = ox + ow / 2.0
            ocy = oy + oh / 2.0
            angle = self._transform_orig_angle

            if self._transform_mode == "move":
                dx = px - self._transform_start_page_pt[0]
                dy = py - self._transform_start_page_pt[1]
                item["rect"] = [round(ox + dx, 2), round(oy + dy, 2), round(ow, 2), round(oh, 2)]
                self._transform_modified = True
                self.invalidate_page(self._active_page_idx)

            elif self._transform_mode == "rotate":
                raw_angle = math.atan2(py - ocy, px - ocx) + math.pi / 2.0
                if self._is_shift_held(is_stylus):
                    snap = math.radians(15.0)
                    raw_angle = round(raw_angle / snap) * snap
                item["angle"] = round(raw_angle, 4)
                self._transform_modified = True
                self.invalidate_page(self._active_page_idx)

            elif self._transform_mode in ("nw", "ne", "se", "sw"):
                dx = px - ocx
                dy = py - ocy
                cos_a = math.cos(-angle)
                sin_a = math.sin(-angle)
                lx = dx * cos_a - dy * sin_a
                ly = dx * sin_a + dy * cos_a

                hw, hh = ow / 2.0, oh / 2.0
                min_size = 24.0

                if self._transform_mode == "se":
                    ax, ay = -hw, -hh
                    sx, sy = 1.0, 1.0
                elif self._transform_mode == "nw":
                    ax, ay = hw, hh
                    sx, sy = -1.0, -1.0
                elif self._transform_mode == "ne":
                    ax, ay = -hw, hh
                    sx, sy = 1.0, -1.0
                else:
                    ax, ay = hw, -hh
                    sx, sy = -1.0, 1.0

                raw_w = max(min_size, (lx - ax) * sx)
                raw_h = max(min_size, (ly - ay) * sy)

                lock_aspect = self.keep_aspect_ratio ^ self._is_shift_held(is_stylus)
                if lock_aspect and ow > 0 and oh > 0:
                    aspect = ow / oh
                    scale = max(raw_w / ow, raw_h / oh)
                    min_scale = max(min_size / ow, min_size / oh)
                    scale = max(min_scale, scale)
                    new_w = ow * scale
                    new_h = new_w / aspect
                else:
                    new_w = raw_w
                    new_h = raw_h

                bx = ax + sx * new_w
                by = ay + sy * new_h
                local_cx = (ax + bx) / 2.0
                local_cy = (ay + by) / 2.0
                new_cx, new_cy = self._item_local_to_page(local_cx, local_cy, ocx, ocy, angle)

                item["rect"] = [
                    round(new_cx - new_w / 2.0, 2),
                    round(new_cy - new_h / 2.0, 2),
                    round(new_w, 2),
                    round(new_h, 2)
                ]
                self._transform_modified = True
                self.invalidate_page(self._active_page_idx)
            return

        if self._effective_tool == "eraser":
            self._eraser_cursor = (wx, wy)
            if self._erase_at(self._active_page_idx, px, py):
                self._erased_during_drag = True
            self.queue_draw()
            return

        if self._active_stroke is not None:
            samples = self._active_stroke["samples"]
            last = samples[-1]
            raw_dist = math.hypot(px - self._last_raw_pt[0], py - self._last_raw_pt[1])
            now = time.perf_counter()
            dt = max(1e-4, now - self._last_sample_time)

            if raw_dist >= 1.0:
                smooth_x = 0.62 * px + 0.38 * last[0]
                smooth_y = 0.62 * py + 0.38 * last[1]
                velocity_px_ms = (raw_dist / dt) / 1000.0

                if self._effective_tool == "velocity_pen":
                    vel_factor = 1.0 / (1.0 + 0.38 * (velocity_px_ms ** 1.15))
                    vel_pressure = max(0.12, min(1.0, 0.12 + 0.88 * vel_factor))
                    if is_stylus:
                        target_p = 0.35 * pressure + 0.65 * vel_pressure
                    else:
                        target_p = vel_pressure
                    alpha_p = 0.32
                else:
                    if not is_stylus:
                        target_p = max(0.24, min(0.95, 0.85 - (velocity_px_ms / 4.2) * 0.45))
                    else:
                        target_p = pressure
                    alpha_p = 0.42

                self._smoothed_pressure = (1.0 - alpha_p) * self._smoothed_pressure + alpha_p * target_p
                samples.append([round(smooth_x, 2), round(smooth_y, 2), round(self._smoothed_pressure, 3)])
                self._last_sample_time = now
                self._last_raw_pt = (px, py)
                self.queue_draw()

    def _end_interaction(self):
        if self._active_page_idx is None:
            return

        pages = self.document.get("pages", [])
        page = pages[self._active_page_idx]

        if self._effective_tool == "lasso":
            if self._lasso_transform_mode is not None:
                if self._lasso_modified and self._pre_drag_items_snapshot is not None:
                    self._record_undo_step(
                        self._active_page_idx,
                        self._pre_drag_items_snapshot,
                        page["items"]
                    )
                self._lasso_transform_mode = None
                self._lasso_orig_bbox = None
                self._lasso_orig_items = None
                self._lasso_modified = False
                self._active_page_idx = None
                self.queue_draw()
                return

            poly = self._lasso_polygon
            self._lasso_polygon = []
            if len(poly) >= 3:
                selected_indices = []
                for idx, item in enumerate(page.get("items", [])):
                    kind = item.get("kind") or item.get("type")
                    if kind == "stroke":
                        pts = item.get("samples") or item.get("points") or []
                        if pts:
                            inside_count = 0
                            for s in pts:
                                sx = float(s["x"]) if isinstance(s, dict) else float(s[0])
                                sy = float(s["y"]) if isinstance(s, dict) else float(s[1])
                                if self._point_in_polygon(sx, sy, poly):
                                    inside_count += 1
                            if inside_count >= max(1, int(len(pts) * 0.35)):
                                selected_indices.append(idx)
                    elif kind == "text":
                        pos = item.get("pos", [0.0, 0.0])
                        tw = float(item.get("width", 200.0))
                        th = float(item.get("point_size", 14.0)) * 1.5
                        if self._point_in_polygon(pos[0] + tw * 0.25, pos[1] + th * 0.5, poly) or self._point_in_polygon(pos[0], pos[1], poly):
                            selected_indices.append(idx)
                    elif kind in ("image", "picture"):
                        rect = item.get("rect", [0.0, 0.0, 100.0, 100.0])
                        cx = rect[0] + rect[2] / 2.0
                        cy = rect[1] + rect[3] / 2.0
                        if self._point_in_polygon(cx, cy, poly):
                            selected_indices.append(idx)

                if selected_indices:
                    self.lasso_page_idx = self._active_page_idx
                    self.lasso_item_indices = selected_indices
                    self.lasso_bbox = self._compute_items_bbox(self.lasso_page_idx, self.lasso_item_indices)
                    if self.on_selection_changed_cb:
                        self.on_selection_changed_cb(True, "lasso")
                else:
                    self.clear_selection()
            self._active_page_idx = None
            self.queue_draw()
            return

        if self._effective_tool == "select":
            if self._transform_modified and self._pre_drag_items_snapshot is not None:
                self._record_undo_step(
                    self._active_page_idx,
                    self._pre_drag_items_snapshot,
                    page["items"]
                )
            self._transform_mode = None
            self._transform_orig_rect = None
            self._transform_modified = False
            self._active_page_idx = None
            self.queue_draw()
            return

        if self._effective_tool == "eraser":
            self._eraser_cursor = None
            if self._erased_during_drag and self._pre_drag_items_snapshot is not None:
                self._record_undo_step(
                    self._active_page_idx,
                    self._pre_drag_items_snapshot,
                    page["items"]
                )
            self._erased_during_drag = False
            self._active_page_idx = None
            self.queue_draw()
            return

        if self._active_stroke is not None:
            raw_samples = self._active_stroke["samples"]
            if len(raw_samples) == 1:
                s0 = raw_samples[0]
                p0 = s0[2] if len(s0) > 2 else 0.65
                raw_samples.append([round(s0[0] + 0.4, 2), round(s0[1] + 0.4, 2), p0])
            elif len(raw_samples) >= 3:
                self._active_stroke["samples"] = self.smooth_samples_chaikin(raw_samples, iterations=2)
                self._active_stroke["smoothed"] = True

            page.setdefault("items", []).append(self._active_stroke)
            self._record_undo_step(
                self._active_page_idx,
                self._pre_drag_items_snapshot,
                page["items"]
            )
            self.invalidate_page(self._active_page_idx)

        self._active_stroke = None
        self._active_page_idx = None
        self._pre_drag_items_snapshot = None

    # --- Stylus Signals ---
    def _on_stylus_down(self, gesture, x, y):
        self._stylus_active = True
        pressure = self._get_stylus_pressure()
        self._begin_interaction(x, y, pressure=pressure, is_stylus=True)

    def _on_stylus_motion(self, gesture, x, y):
        if not self._stylus_active:
            return
        ok, axes_backlog = gesture.get_backlog()
        if ok and axes_backlog:
            for elem in axes_backlog:
                if hasattr(elem, "axes") and len(elem.axes) >= 2:
                    bx, by = elem.axes[0], elem.axes[1]
                    bp = elem.axes[2] if len(elem.axes) > 2 and elem.axes[2] > 0 else self._get_stylus_pressure()
                    if bx and by:
                        self._update_interaction(bx, by, pressure=bp, is_stylus=True)
        pressure = self._get_stylus_pressure()
        self._update_interaction(x, y, pressure=pressure, is_stylus=True)

    def _on_stylus_up(self, gesture, x, y):
        if self._stylus_active:
            self._end_interaction()
            self._stylus_active = False

    # --- Mouse / Touch Drag Signals ---
    def _on_drag_begin(self, gesture, start_x, start_y):
        if self._stylus_active:
            return
        self._begin_interaction(start_x, start_y, pressure=0.65, is_stylus=False)

    def _on_drag_update(self, gesture, offset_x, offset_y):
        if self._stylus_active or self._active_page_idx is None:
            return
        ok, start_x, start_y = gesture.get_start_point()
        if ok:
            self._update_interaction(start_x + offset_x, start_y + offset_y, pressure=0.65, is_stylus=False)

    def _on_drag_end(self, gesture, offset_x, offset_y):
        if self._stylus_active:
            return
        self._end_interaction()

    # =========================================================================
    # Spatial Eraser & Text Insertion
    # =========================================================================
    @staticmethod
    def _point_to_segment_dist(px, py, x1, y1, x2, y2):
        dx = x2 - x1
        dy = y2 - y1
        if dx == 0 and dy == 0:
            return math.hypot(px - x1, py - y1)
        t = ((px - x1) * dx + (py - y1) * dy) / (dx * dx + dy * dy)
        t = max(0.0, min(1.0, t))
        proj_x = x1 + t * dx
        proj_y = y1 + t * dy
        return math.hypot(px - proj_x, py - proj_y)

    def _erase_at(self, page_idx, px, py):
        pages = self.document.get("pages", [])
        if not (0 <= page_idx < len(pages)):
            return False
        page = pages[page_idx]
        items = page.get("items", [])
        if not items:
            return False

        radius = self.eraser_radius / max(0.5, self.zoom_level)
        kept = []
        removed = False

        for item in items:
            kind = item.get("kind") or item.get("type")
            hit = False
            if kind == "stroke":
                samples = item.get("samples") or item.get("points") or []
                half_w = float(item.get("stroke_width", 3.0)) / 2.0
                threshold = radius + half_w
                for i in range(len(samples)):
                    s1 = samples[i]
                    x1 = s1["x"] if isinstance(s1, dict) else s1[0]
                    y1 = s1["y"] if isinstance(s1, dict) else s1[1]
                    if i == 0 and math.hypot(px - x1, py - y1) <= threshold:
                        hit = True
                        break
                    elif i > 0:
                        s0 = samples[i - 1]
                        x0 = s0["x"] if isinstance(s0, dict) else s0[0]
                        y0 = s0["y"] if isinstance(s0, dict) else s0[1]
                        if self._point_to_segment_dist(px, py, x0, y0, x1, y1) <= threshold:
                            hit = True
                            break
            elif kind == "text":
                pos = item.get("pos", [0.0, 0.0])
                tx, ty = pos[0], pos[1]
                tw = float(item.get("width", 260.0))
                th = float(item.get("point_size", 14.0)) * 2.5
                if (tx - radius) <= px <= (tx + tw + radius) and (ty - radius) <= py <= (ty + th + radius):
                    hit = True

            if hit:
                removed = True
            else:
                kept.append(item)

        if removed:
            page["items"] = kept
            self.invalidate_page(page_idx)
        return removed

    def _prompt_text_insertion(self, wx, wy, page_idx, px, py):
        popover = Gtk.Popover()
        popover.set_parent(self)
        rect = Gdk.Rectangle()
        rect.x = int(wx)
        rect.y = int(wy)
        rect.width = 1
        rect.height = 1
        popover.set_pointing_to(rect)

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        box.set_margin_top(6)
        box.set_margin_bottom(6)
        box.set_margin_start(6)
        box.set_margin_end(6)

        entry = Gtk.Entry(placeholder_text="Type note text…", width_chars=24)
        add_btn = Gtk.Button(label="Add")
        add_btn.add_css_class("suggested-action")

        def commit_text(*_):
            txt = entry.get_text().strip()
            if txt and self.document:
                pages = self.document.get("pages", [])
                if 0 <= page_idx < len(pages):
                    page = pages[page_idx]
                    old_items = copy.deepcopy(page.get("items", []))
                    new_item = {
                        "kind": "text",
                        "text": txt,
                        "pos": [round(px, 2), round(py, 2)],
                        "rgba": [self.pen_rgba[0], self.pen_rgba[1], self.pen_rgba[2], 255],
                        "point_size": round(self.text_size_pt * (self.document.get("dpi", 150.0) / 72.0), 1),
                        "width": max(200.0, self.get_page_size(page_idx)[0] - px - 40.0)
                    }
                    page.setdefault("items", []).append(new_item)
                    self._record_undo_step(page_idx, old_items, page["items"])
                    self.invalidate_page(page_idx)
            popover.popdown()
            GLib.idle_add(popover.unparent)

        entry.connect("activate", commit_text)
        add_btn.connect("clicked", commit_text)
        popover.connect("closed", lambda p: GLib.idle_add(p.unparent))

        box.append(entry)
        box.append(add_btn)
        popover.set_child(box)
        popover.popup()
        entry.grab_focus()

    # =========================================================================
    # Cairo Rendering & Subtle Page Framing
    # =========================================================================
    def _rounded_rect(self, cr, x, y, w, h, r):
        cr.new_sub_path()
        cr.arc(x + w - r, y + r, r, -math.pi / 2, 0)
        cr.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
        cr.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
        cr.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
        cr.close_path()

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

            elif itype == "group":
                gx = box_x + XTemplateEvaluator.eval_length(item.get("x", 0), dpi, box_w, params)
                gy = box_y + XTemplateEvaluator.eval_length(item.get("y", 0), dpi, box_h, params)
                gw = (
                    XTemplateEvaluator.eval_length(item["w"], dpi, box_w, params)
                    if "w" in item else (box_x + box_w - gx)
                )
                gh = (
                    XTemplateEvaluator.eval_length(item["h"], dpi, box_h, params)
                    if "h" in item else (box_y + box_h - gy)
                )
                children = item.get("children", [])
                if children and gw > 0 and gh > 0:
                    self._draw_xtemplate_items(
                        cr, children, gx, gy, gw, gh, dpi, params, pattern_rgba, accent_rgba, t_info, assets, templates
                    )

            elif itype == "repeat":
                axis = item.get("axis", "y").lower()
                total_len = box_h if axis == "y" else box_w
                period = XTemplateEvaluator.eval_length(item.get("period", "8mm"), dpi, total_len, params)
                children = item.get("children", [])
                if period > 0.5 and children:
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
                ly = box_y + XTemplateEvaluator.eval_length(item.get("y", 0), dpi, box_h, params)
                x1 = box_x + XTemplateEvaluator.eval_length(item.get("x1", 0), dpi, box_w, params)
                x2 = (
                    box_x + XTemplateEvaluator.eval_length(item["x2"], dpi, box_w, params)
                    if "x2" in item else (box_x + box_w)
                )
                stroke_role = item.get("stroke", "primary")
                rgba = accent_rgba if stroke_role == "accent" else pattern_rgba
                lw = float(item.get("width", 1.25))
                cr.save()
                cr.set_source_rgba(*rgba)
                cr.set_line_width(lw)
                cr.move_to(x1, ly)
                cr.line_to(x2, ly)
                cr.stroke()
                cr.restore()

            elif itype == "vline":
                lx = box_x + XTemplateEvaluator.eval_length(item.get("x", 0), dpi, box_w, params)
                y1 = box_y + XTemplateEvaluator.eval_length(item.get("y1", 0), dpi, box_h, params)
                y2 = (
                    box_y + XTemplateEvaluator.eval_length(item["y2"], dpi, box_h, params)
                    if "y2" in item else (box_y + box_h)
                )
                stroke_role = item.get("stroke", "primary")
                rgba = accent_rgba if stroke_role == "accent" else pattern_rgba
                lw = float(item.get("width", 1.5))
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
            t_params = dict(t_meta.get("params", {}))
            for k, v in doc_style.items():
                if k in t_params:
                    t_params[k] = v
            for k, v in style.items():
                if k in t_params:
                    t_params[k] = v

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

        cr.set_source_rgba(0, 0, 0, 0.16)
        cr.set_line_width(2.5 / scale)
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

    def _draw_selection_gizmo(self, cr, offset_x, offset_y, item):
        rect = item.get("rect", [0.0, 0.0, 100.0, 100.0])
        x, y, w, h = rect[0], rect[1], rect[2], rect[3]
        angle = float(item.get("angle", 0.0))

        cr.save()
        cr.translate(offset_x, offset_y)
        cr.scale(self.zoom_level, self.zoom_level)

        cx = x + w / 2.0
        cy = y + h / 2.0
        cr.translate(cx, cy)
        cr.rotate(angle)

        hw = w / 2.0
        hh = h / 2.0
        inv_z = 1.0 / max(0.25, self.zoom_level)

        cr.set_source_rgba(0.11, 0.44, 0.85, 0.9)
        cr.set_line_width(2.0 * inv_z)
        cr.rectangle(-hw, -hh, w, h)
        cr.stroke()

        rot_dist = 32.0 * inv_z
        cr.move_to(0.0, -hh)
        cr.line_to(0.0, -hh - rot_dist)
        cr.stroke()

        handle_r = 6.5 * inv_z
        for hx, hy in [(0.0, -hh - rot_dist), (-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh)]:
            cr.set_source_rgb(1.0, 1.0, 1.0)
            cr.arc(hx, hy, handle_r, 0.0, 2.0 * math.pi)
            cr.fill()
            cr.set_source_rgba(0.11, 0.44, 0.85, 1.0)
            cr.set_line_width(2.0 * inv_z)
            cr.arc(hx, hy, handle_r, 0.0, 2.0 * math.pi)
            cr.stroke()

        cr.restore()

    def _draw_lasso_overlay(self, cr, offset_x, offset_y, page_idx):
        inv_z = 1.0 / max(0.25, self.zoom_level)

        if self._lasso_polygon and self._active_page_idx == page_idx:
            cr.save()
            cr.translate(offset_x, offset_y)
            cr.scale(self.zoom_level, self.zoom_level)
            cr.new_path()
            for i, (lx, ly) in enumerate(self._lasso_polygon):
                if i == 0:
                    cr.move_to(lx, ly)
                else:
                    cr.line_to(lx, ly)
            cr.close_path()
            cr.set_source_rgba(0.15, 0.52, 0.95, 0.12)
            cr.fill_preserve()
            cr.set_source_rgba(0.15, 0.52, 0.95, 0.9)
            cr.set_line_width(1.8 * inv_z)
            cr.set_dash([6.0 * inv_z, 4.0 * inv_z])
            cr.stroke()
            cr.restore()

        if self.has_lasso_selection() and self.lasso_page_idx == page_idx:
            bx, by, bw, bh = self.lasso_bbox
            cr.save()
            cr.translate(offset_x, offset_y)
            cr.scale(self.zoom_level, self.zoom_level)

            cr.set_source_rgba(0.15, 0.52, 0.95, 0.07)
            cr.rectangle(bx, by, bw, bh)
            cr.fill()

            cr.set_source_rgba(0.15, 0.52, 0.95, 0.92)
            cr.set_line_width(1.8 * inv_z)
            cr.set_dash([6.0 * inv_z, 4.0 * inv_z])
            cr.rectangle(bx, by, bw, bh)
            cr.stroke()
            cr.set_dash([])

            handle_r = 6.0 * inv_z
            for hx, hy in [(bx, by), (bx + bw, by), (bx + bw, by + bh), (bx, by + bh)]:
                cr.set_source_rgb(1.0, 1.0, 1.0)
                cr.arc(hx, hy, handle_r, 0.0, 2.0 * math.pi)
                cr.fill()
                cr.set_source_rgba(0.15, 0.52, 0.95, 1.0)
                cr.set_line_width(2.0 * inv_z)
                cr.arc(hx, hy, handle_r, 0.0, 2.0 * math.pi)
                cr.stroke()

            cr.restore()

    def _draw_single_page(self, cr, page_idx, page, pw, ph, offset_x, offset_y):
        scaled_w = pw * self.zoom_level
        scaled_h = ph * self.zoom_level
        rx = round(offset_x)
        ry = round(offset_y)
        rw = round(scaled_w)
        rh = round(scaled_h)

        # Draw cached page contents
        surface = self._get_cached_page_surface(page_idx, page, pw, ph)
        cr.set_source_surface(surface, rx, ry)
        cr.paint()

        # Draw live in-progress stroke
        if self._active_stroke is not None and self._active_page_idx == page_idx:
            cr.save()
            cr.translate(offset_x, offset_y)
            cr.scale(self.zoom_level, self.zoom_level)
            cr.rectangle(0, 0, pw, ph)
            cr.clip()
            self.draw_stroke(cr, self._active_stroke)
            cr.restore()

        # Crisp, subtle 1px border framing the page
        cr.save()
        cr.set_source_rgba(0.0, 0.0, 0.0, 0.14)
        cr.set_line_width(1.0)
        cr.rectangle(rx + 0.5, ry + 0.5, rw - 1.0, rh - 1.0)
        cr.stroke()
        cr.restore()

        if self.selected_page_idx == page_idx:
            sel_item = self.get_selected_item()
            if sel_item and (sel_item.get("kind") or sel_item.get("type")) in ("image", "picture"):
                self._draw_selection_gizmo(cr, offset_x, offset_y, sel_item)

        self._draw_lasso_overlay(cr, offset_x, offset_y, page_idx)

    def on_draw(self, drawing_area, cr, width, height):
        # No grey surround fill — let the clean window/view background show through
        if not self.document:
            return

        pages = self.document.get("pages", [])
        if not pages:
            return

        if self.view_mode == "paged":
            if self.current_page >= len(pages):
                return
            page = pages[self.current_page]
            pw, ph = self.get_page_size(self.current_page)
            scaled_w = pw * self.zoom_level
            scaled_h = ph * self.zoom_level
            offset_x = max(20.0, (width - scaled_w) / 2.0)
            offset_y = max(20.0, (height - 44.0 - scaled_h) / 2.0)
            self._draw_single_page(cr, self.current_page, page, pw, ph, offset_x, offset_y)
        else:
            scroll_y = self.vadj.get_value() if self.vadj else 0.0
            view_h = self.vadj.get_page_size() if self.vadj else height
            if view_h <= 0:
                view_h = height
            vis_top = scroll_y - 100.0
            vis_bottom = scroll_y + view_h + 100.0

            for idx, page in enumerate(pages):
                if idx >= len(self.page_geometries):
                    break
                y_offset, pw, ph, scaled_w, scaled_h = self.page_geometries[idx]
                if y_offset + scaled_h < vis_top or y_offset > vis_bottom:
                    continue
                offset_x = max(20.0, (width - scaled_w) / 2.0)
                self._draw_single_page(cr, idx, page, pw, ph, offset_x, y_offset)

        if self._eraser_cursor is not None:
            ex, ey = self._eraser_cursor
            cr.save()
            cr.set_source_rgba(0.85, 0.2, 0.25, 0.75)
            cr.set_line_width(2.0)
            cr.arc(ex, ey, self.eraser_radius, 0, 2 * math.pi)
            cr.stroke()
            cr.restore()

    def _resolve_asset_surface(self, asset_name, assets, templates):
        if not asset_name:
            return None
        if asset_name in assets:
            return assets[asset_name]
        base = os.path.basename(asset_name)
        if base in assets:
            return assets[base]
        t_info = XNotePackageParser.resolve_template(asset_name, templates)
        if t_info and t_info.get("surface") is not None:
            return t_info["surface"]
        return None

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

        if not stroke.get("smoothed", False) and len(raw_samples) >= 3:
            samples = self.smooth_samples_chaikin(raw_samples, iterations=1)
        else:
            samples = raw_samples

        rgba = stroke.get("stroke_rgba", stroke.get("color", [0, 0, 0, 255]))
        r, g, b, a = rgba[0] / 255.0, rgba[1] / 255.0, rgba[2] / 255.0, rgba[3] / 255.0
        base_width = float(stroke.get("stroke_width", stroke.get("width", 3.2)))
        tool = stroke.get("tool", "pen")

        first_s = samples[0]
        has_pressure = (
            stroke.get("variable_width", False)
            or tool in ("calligraphy", "velocity_pen")
            or (isinstance(first_s, (list, tuple)) and len(first_s) >= 3)
            or (isinstance(first_s, dict) and "pressure" in first_s)
        )

        if not has_pressure or tool == "highlighter":
            cr.save()
            if tool == "highlighter" or a < 0.95:
                cr.set_operator(cairo.OPERATOR_MULTIPLY if a < 0.6 else cairo.OPERATOR_OVER)
            cr.set_source_rgba(r, g, b, a)
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

        pts = []
        for s in samples:
            if isinstance(s, dict):
                px = float(s.get("x", 0.0))
                py = float(s.get("y", 0.0))
                pr = float(s.get("pressure", 0.65))
            else:
                px = float(s[0]) if len(s) > 0 else 0.0
                py = float(s[1]) if len(s) > 1 else 0.0
                pr = float(s[2]) if len(s) > 2 else 0.65
            pts.append((px, py, max(0.05, min(1.0, pr))))

        radii = []
        base_r = max(0.4, base_width / 2.0)
        is_calligraphy_nib = (tool == "calligraphy")
        is_velocity_pen = (tool == "velocity_pen")

        for i, (px, py, pr) in enumerate(pts):
            if is_velocity_pen:
                p_factor = 0.14 + 1.30 * (pr ** 1.45)
            else:
                p_factor = 0.22 + 1.15 * (pr ** 1.25)

            if i < len(pts) - 1:
                dx = pts[i + 1][0] - px
                dy = pts[i + 1][1] - py
            elif i > 0:
                dx = px - pts[i - 1][0]
                dy = py - pts[i - 1][1]
            else:
                dx, dy = 1.0, 0.0

            seg_len = math.hypot(dx, dy)
            if seg_len > 1e-3:
                angle = math.atan2(dy, dx)
                nib_influence = abs(math.sin(angle - self.nib_angle_rad))
                if is_calligraphy_nib:
                    nib_factor = 0.30 + 0.95 * nib_influence
                elif is_velocity_pen:
                    nib_factor = 1.0
                else:
                    nib_factor = 0.82 + 0.26 * nib_influence
            else:
                nib_factor = 1.0

            rad = max(0.30, base_r * p_factor * nib_factor)
            radii.append(rad)

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
