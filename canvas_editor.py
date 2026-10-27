import os
import time
import math
import copy
import gi

gi.require_version('Gtk', '4.0')
from gi.repository import Gtk, GLib, Gdk

from xnote_io import XNotePackageParser
from canvas_renderer import CanvasRendererMixin
from canvas_selection import CanvasSelectionMixin


class XNoteCanvas(Gtk.DrawingArea, CanvasRendererMixin, CanvasSelectionMixin):
    """Interactive Cairo canvas supporting Android .xnote stroke configs, xtemplate v1, PDF pages, and images."""

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
        # "select", "lasso", "pen", "dashed", "calligraphy", "speed", "taper", "highlighter", "eraser", "text"
        self.active_tool = "pen"
        self.pen_rgba = [213, 68, 37, 255]
        self.pen_width = 9.3
        self.highlighter_width = 27.65
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
        self._lasso_polygon = []
        self.lasso_page_idx = None
        self.lasso_item_indices = []
        self.lasso_bbox = None
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
    # Stylus & Mouse Input Handling (Produces Android-Compatible Stroke Schema)
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
        if self.active_tool == "velocity_pen":
            return "speed"
        return self.active_tool

    def _get_stylus_pressure(self):
        ok, pressure = self.stylus_gesture.get_axis(Gdk.AxisUse.PRESSURE)
        if ok and pressure is not None and pressure >= 0.0:
            return max(0.0, min(1.0, float(pressure)))
        return 0.55

    def _build_android_stroke_dict(self, tool_name, px, py, pressure):
        rgba = [self.pen_rgba[0], self.pen_rgba[1], self.pen_rgba[2], 255]
        bw = float(self.highlighter_width if tool_name == "highlighter" else self.pen_width)

        if tool_name == "dashed":
            cfg = {
                "base_width": bw,
                "pressure_enabled": False,
                "pressure_min_factor": 1.0,
                "direction_strength": 0.0,
                "rgba": rgba,
                "dash_length": max(6.0, round(bw * 3.2, 1)),
                "dash_gap": max(5.0, round(bw * 2.5, 1)),
            }
        elif tool_name == "calligraphy":
            cfg = {
                "base_width": bw,
                "pressure_enabled": True,
                "pressure_min_factor": 0.312,
                "direction_strength": 0.576,
                "rgba": rgba,
            }
        elif tool_name == "speed":
            cfg = {
                "base_width": bw,
                "pressure_enabled": True,
                "pressure_min_factor": 0.393,
                "direction_strength": 0.0,
                "rgba": rgba,
                "speed_strength": 0.869,
            }
        elif tool_name == "taper":
            cfg = {
                "base_width": bw,
                "pressure_enabled": True,
                "pressure_min_factor": 0.519,
                "direction_strength": 0.0,
                "rgba": rgba,
                "taper_enabled": True,
                "taper_min_factor": 0.345,
            }
        elif tool_name == "highlighter":
            cfg = {
                "base_width": bw,
                "pressure_enabled": False,
                "pressure_min_factor": 1.0,
                "direction_strength": 0.0,
                "rgba": rgba,
                "highlighter_alpha": 0.5,
            }
        else:  # "pen"
            cfg = {
                "base_width": bw,
                "pressure_enabled": True,
                "pressure_min_factor": 0.453,
                "direction_strength": 0.0,
                "rgba": rgba,
            }

        first_sample = (
            [round(px, 2), round(py, 2), round(pressure, 3), 0]
            if tool_name == "speed"
            else [round(px, 2), round(py, 2), round(pressure, 3)]
        )
        stroke = {
            "kind": "stroke",
            "tool": tool_name,
            "config": cfg,
            "samples": [first_sample],
            "smooth_scale": 0.7950984251968506,
        }
        if tool_name == "speed":
            stroke["speed_scale"] = 0.5918616115704284
        return stroke

    def _begin_interaction(self, wx, wy, pressure=0.55, is_stylus=False):
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

        self._active_stroke = self._build_android_stroke_dict(self._effective_tool, px, py, pressure)
        self.queue_draw()

    def _update_interaction(self, wx, wy, pressure=0.55, is_stylus=False):
        if self._active_page_idx is None:
            return

        page_idx, px, py = self.widget_to_page_coords(wx, wy)
        if page_idx != self._active_page_idx:
            pw, ph = self.get_page_size(self._active_page_idx)
            offset_x, offset_y = self.get_page_widget_offset(self._active_page_idx)
            px = max(0.0, min(pw, (wx - offset_x) / self.zoom_level))
            py = max(0.0, min(ph, (wy - offset_y) / self.zoom_level))

        # --- Lasso Freehand Drawing or Lasso Group Transform ---
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
                                    new_s.append([round(s["x"] + dx, 2), round(s["y"] + dy, 2), s.get("pressure", 0.5)])
                                else:
                                    pt = [round(s[0] + dx, 2), round(s[1] + dy, 2)]
                                    if len(s) > 2:
                                        pt.append(s[2])
                                    if len(s) > 3:
                                        pt.append(s[3])
                                    new_s.append(pt)
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
                    else:
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
                                pt = [
                                    round(nx + (ox_pt - obx) * sx, 2),
                                    round(ny + (oy_pt - oby) * sy, 2),
                                ]
                                if not isinstance(s, dict):
                                    if len(s) > 2:
                                        pt.append(s[2])
                                    if len(s) > 3:
                                        pt.append(s[3])
                                new_s.append(pt)
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

        # --- Single Image Transform ---
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

        # --- Active Brush Stroke ---
        if self._active_stroke is not None:
            samples = self._active_stroke["samples"]
            last = samples[-1]
            raw_dist = math.hypot(px - self._last_raw_pt[0], py - self._last_raw_pt[1])
            now = time.perf_counter()
            dt_sec = max(1e-4, now - self._last_sample_time)
            dt_ms = max(1, int(round(dt_sec * 1000.0)))

            if raw_dist >= 0.8:
                smooth_x = 0.65 * px + 0.35 * last[0]
                smooth_y = 0.65 * py + 0.35 * last[1]
                self._smoothed_pressure = 0.6 * self._smoothed_pressure + 0.4 * pressure

                if self._effective_tool == "speed":
                    samples.append([round(smooth_x, 2), round(smooth_y, 2), round(self._smoothed_pressure, 3), dt_ms])
                else:
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
                if len(s0) >= 4:
                    raw_samples.append([round(s0[0] + 0.4, 2), round(s0[1] + 0.4, 2), s0[2], 5])
                else:
                    raw_samples.append([round(s0[0] + 0.4, 2), round(s0[1] + 0.4, 2), s0[2]])

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
                    bp = elem.axes[2] if len(elem.axes) > 2 and elem.axes[2] >= 0 else self._get_stylus_pressure()
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
        self._begin_interaction(start_x, start_y, pressure=0.55, is_stylus=False)

    def _on_drag_update(self, gesture, offset_x, offset_y):
        if self._stylus_active or self._active_page_idx is None:
            return
        ok, start_x, start_y = gesture.get_start_point()
        if ok:
            self._update_interaction(start_x + offset_x, start_y + offset_y, pressure=0.55, is_stylus=False)

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
                half_w = self._get_stroke_base_width(item) / 2.0
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

        surface = self._get_cached_page_surface(page_idx, page, pw, ph)
        cr.set_source_surface(surface, rx, ry)
        cr.paint()

        if self._active_stroke is not None and self._active_page_idx == page_idx:
            cr.save()
            cr.translate(offset_x, offset_y)
            cr.scale(self.zoom_level, self.zoom_level)
            cr.rectangle(0, 0, pw, ph)
            cr.clip()
            self.draw_stroke(cr, self._active_stroke)
            cr.restore()

        # Subtle 1px border around the page
        cr.save()
        cr.set_source_rgba(0.0, 0.0, 0.0, 0.16)
        cr.set_line_width(1.0)
        cr.rectangle(rx + 0.5, ry + 0.5, round(scaled_w) - 1.0, round(scaled_h) - 1.0)
        cr.stroke()
        cr.restore()

        if self.selected_page_idx == page_idx:
            sel_item = self.get_selected_item()
            if sel_item and (sel_item.get("kind") or sel_item.get("type")) in ("image", "picture"):
                self._draw_selection_gizmo(cr, offset_x, offset_y, sel_item)

        self._draw_lasso_overlay(cr, offset_x, offset_y, page_idx)

    def on_draw(self, drawing_area, cr, width, height):
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
