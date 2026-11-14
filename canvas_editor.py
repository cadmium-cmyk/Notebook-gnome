import os
import time
import math
import copy
import cairo
import gi

gi.require_version('Gtk', '4.0')
from gi.repository import Gtk, GLib, Gdk, Pango, PangoCairo

from xnote_io import XNotePackageParser
from canvas_renderer import CanvasRendererMixin
from canvas_selection import CanvasSelectionMixin


class XNoteCanvas(Gtk.DrawingArea, CanvasRendererMixin, CanvasSelectionMixin):

    def _hit_test_item_point(self, item, px, py, tol=14.0):
        kind = item.get("kind") or item.get("type")
        if kind == "stroke":
            for pt in item.get("samples", []):
                if math.hypot(pt[0] - px, pt[1] - py) <= tol:
                    return True
            return False
        elif "rect" in item:
            rect = item["rect"]
            return (rect[0] - tol <= px <= rect[0] + rect[2] + tol and
                    rect[1] - tol <= py <= rect[1] + rect[3] + tol)
        return False
    """Interactive Cairo canvas supporting Android .xnote stroke configs, xtemplate v1, PDF pages, and images."""

    def __init__(self, on_modified_cb=None, on_history_changed_cb=None, on_selection_changed_cb=None, on_tool_change_cb=None):
        super().__init__()
        self.set_halign(Gtk.Align.CENTER)
        self.set_valign(Gtk.Align.CENTER)
        self.set_focusable(True)
        self.set_cursor_from_name("none")
        self.set_draw_func(self.on_draw)

        self.on_modified_cb = on_modified_cb
        self.on_history_changed_cb = on_history_changed_cb
        self.on_selection_changed_cb = on_selection_changed_cb
        self.on_tool_change_cb = on_tool_change_cb

        # Document state
        self.document = None
        self.assets = {}
        self.templates = {}
        self.current_page = 0
        self.zoom_level = 1.0
        self.view_mode = "paged"  # "paged" or "scrolled"
        self.viewing_mode = "single"  # "single" or "double"
        self.zoom_locked = False
        self.page_geometries = []
        self.page_sizes = []
        self.page_cache = {}
        self.bg_cache = {}  # Caches static PDF + template backgrounds separately from items
        self.thumb_cache = {}  # Caches rendered page thumbnail surfaces: page_idx -> cairo.ImageSurface
        self.vadj = None

        # Stylus / Tool settings:
        # "select", "lasso", "pen", "dashed", "calligraphy", "speed", "taper", "shape", "highlighter", "eraser", "text"
        self.active_tool = "pen"
        self.pen_rgba = [213, 68, 37, 255]
        self.pen_width = 9.3
        self.highlighter_width = 27.65
        self.eraser_radius = 16.0
        self.text_size_pt = 14.0
        self.text_font_family = "Sans"
        self._active_text_box = None
        self._sized_text_box = None
        self._minimap_surface_cache = None
        self.show_minimap = True
        self.nib_angle_rad = -math.radians(35.0)

        # Shapes tool settings
        self.shape_type = "rectangle"
        self.shape_stroke_width = 3.5
        self.shape_fill = False
        self._active_shape = None

        # Comprehensive tool settings dictionary for quick customization
        self.tool_settings = {
            "pen": {
                "width": 9.3,
                "pressure_enabled": True,
                "pressure_min_factor": 0.453,
            },
            "speed": {
                "width": 9.3,
                "pressure_enabled": True,
                "pressure_min_factor": 0.393,
                "speed_scale": 0.592,
                "speed_strength": 0.869,
            },
            "calligraphy": {
                "width": 9.3,
                "pressure_enabled": True,
                "pressure_min_factor": 0.312,
                "direction_strength": 0.78,
                "nib_angle_deg": -35.0,
            },
            "dashed": {
                "width": 6.0,
                "dash_length": 18.0,
                "dash_gap": 12.0,
            },
            "taper": {
                "width": 9.3,
                "pressure_enabled": True,
                "pressure_min_factor": 0.519,
                "taper_min_factor": 0.345,
            },
            "shape": {
                "shape_type": "rectangle",
                "stroke_width": 3.5,
                "fill": False,
            },
            "highlighter": {
                "width": 27.65,
                "alpha": 0.5,
            },
            "eraser": {
                "radius": 16.0,
            },
            "neon": {
                "width": 9.3,
                "neon": True,
                "neon_strength": 0.6,
                "pressure_enabled": True,
                "pressure_min_factor": 0.453,
            },
            "tape": {
                "width": 32.0,
                "pattern": "stripes",
                "rgba": [255, 230, 100, 255],
                "peeled": False,
            },
            "connector": {
                "stroke_width": 2.5,
                "routing": "curved",
                "head_end": "arrow",
                "head_start": "none",
                "stroke_rgba": [80, 80, 80, 255],
                "dashed": False,
                "neon": False,
                "neon_strength": 0.6,
            },
            "insert_space": {
                "gap": 100.0,
                "direction": "vertical",
            },
        }

        self.snap_shapes = False
        self._active_tape = None
        self._active_connector = None
        self._active_space_cut = None

        # Active stroke & adaptive 1-Euro filter state
        self._active_stroke = None
        self._active_page_idx = None
        self._stylus_active = False
        self._effective_tool = "pen"
        self._eraser_cursor = None
        self._pointer_pos = None
        self._last_erase_pt = None
        self._erased_during_drag = False
        self._smoothed_pressure = 0.65
        self._last_sample_time = 0.0
        self._last_raw_pt = (0.0, 0.0)
        self._filter_vel = (0.0, 0.0)

        # Transient skip set for zero-lag drag transforms
        self._transient_skip_page = None
        self._transient_skip_indices = None

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

        # Undo / Redo stacks storing (page_idx, old_items_snapshot, new_items_snapshot)
        self.undo_stack = []
        self.redo_stack = []
        self._pre_drag_items_snapshot = None

        # Paginated page slide animation state
        self._page_transition = None
        self._tick_cb_id = None

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

        self.middle_drag = Gtk.GestureDrag.new()
        self.middle_drag.set_button(Gdk.BUTTON_MIDDLE)
        self.middle_drag.connect("drag-begin", self._on_middle_drag_begin)
        self.middle_drag.connect("drag-update", self._on_middle_drag_update)
        self.middle_drag.connect("drag-end", self._on_middle_drag_end)
        self.add_controller(self.middle_drag)

        self.scroll_controller = Gtk.EventControllerScroll.new(
            Gtk.EventControllerScrollFlags.VERTICAL | Gtk.EventControllerScrollFlags.HORIZONTAL
        )
        self.scroll_controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        self.scroll_controller.connect("scroll", self._on_scroll_event)
        self.add_controller(self.scroll_controller)

        self.motion_controller = Gtk.EventControllerMotion.new()
        self.motion_controller.connect("enter", self._on_mouse_enter)
        self.motion_controller.connect("motion", self._on_mouse_motion)
        self.motion_controller.connect("leave", self._on_mouse_leave)
        self.add_controller(self.motion_controller)

        self.click_gesture = Gtk.GestureClick.new()
        self.click_gesture.set_button(Gdk.BUTTON_PRIMARY)
        self.click_gesture.connect("pressed", self._on_primary_click_pressed)
        self.add_controller(self.click_gesture)


    def _on_mouse_enter(self, controller, x, y):
        self._on_mouse_motion(controller, x, y)

    def _on_mouse_motion(self, controller, x, y):
        if getattr(self, "_middle_drag_start_screen", None) is not None:
            return

        if self.view_mode == "scrolled" and getattr(self, "_new_page_btn_rect", None):
            bx, by, bw, bh = self._new_page_btn_rect
            if bx <= x <= bx + bw and by <= y <= by + bh:
                if not getattr(self, "_hover_new_page_btn", False):
                    self._hover_new_page_btn = True
                    self.set_cursor_from_name("pointer")
                if self._pointer_pos is not None:
                    self._pointer_pos = None
                    self.queue_draw()
                return
            elif getattr(self, "_hover_new_page_btn", False):
                self._hover_new_page_btn = False

        if self.is_infinite_canvas() and getattr(self, "show_minimap", True):
            m_rect = self._get_minimap_rect()
            if m_rect and self._hit_test_minimap(x, y, m_rect):
                self.set_cursor_from_name("pointer")
                if self._pointer_pos is not None:
                    self._pointer_pos = None
                    self.queue_draw()
                return

        # Check selection / transform handles
        if getattr(self, "document", None):
            page_idx, px, py = self.widget_to_page_coords(x, y)
            if page_idx is not None:
                handle = None
                if getattr(self, "selected_item_idx", None) is not None and getattr(self, "selected_page_idx", None) == page_idx:
                    handle = self._hit_test_selection_handles(px, py)
                elif hasattr(self, "has_lasso_selection") and self.has_lasso_selection() and getattr(self, "lasso_page_idx", None) == page_idx:
                    handle = self._hit_test_lasso_handles(px, py)

                if handle:
                    if handle in ("nw", "se"):
                        self.set_cursor_from_name("nwse-resize")
                    elif handle in ("ne", "sw"):
                        self.set_cursor_from_name("nesw-resize")
                    elif handle == "rotate":
                        self.set_cursor_from_name("grab")
                    elif handle == "move":
                        self.set_cursor_from_name("move")
                    else:
                        self.set_cursor_from_name("pointer")
                    if self._pointer_pos is not None:
                        self._pointer_pos = None
                        self.queue_draw()
                    return

        self._pointer_pos = (x, y)
        self.set_cursor_from_name("none")
        self.queue_draw()

    def _on_mouse_leave(self, controller):
        if getattr(self, "_hover_new_page_btn", False):
            self._hover_new_page_btn = False
        self._pointer_pos = None
        self.set_cursor(None)
        self.queue_draw()

    def _draw_pointer_cursor(self, cr):
        if getattr(self, "_pointer_pos", None) is None:
            return
        if getattr(self, "_middle_drag_start_screen", None) is not None:
            return
        if getattr(self, "_is_minimap_dragging", False):
            return
        if getattr(self, "_hover_new_page_btn", False):
            return
        if getattr(self, "_eraser_cursor", None) is not None:
            return

        px, py = self._pointer_pos

        cr.save()
        tool = getattr(self, "_effective_tool", None) or getattr(self, "active_tool", "pen")
        if tool == "eraser":
            r = getattr(self, "eraser_radius", 18.0)
            cr.set_line_width(2.0)
            cr.set_source_rgba(0.85, 0.2, 0.25, 0.75)
            cr.arc(px, py, r, 0, 2 * math.pi)
            cr.stroke()
        else:
            r = 4.0
            # Subtle pen ink color fill
            pen_col = getattr(self, "pen_rgba", [0, 0, 0, 255])
            if pen_col and len(pen_col) >= 3:
                cr.set_source_rgba(pen_col[0] / 255.0, pen_col[1] / 255.0, pen_col[2] / 255.0, 0.4)
                cr.arc(px, py, r, 0, 2 * math.pi)
                cr.fill_preserve()
            else:
                cr.arc(px, py, r, 0, 2 * math.pi)

            # Outer contrast outline (dark)
            cr.set_source_rgba(0.0, 0.0, 0.0, 0.75)
            cr.set_line_width(1.8)
            cr.stroke_preserve()

            # Inner crisp highlight (white)
            cr.set_source_rgba(1.0, 1.0, 1.0, 0.95)
            cr.set_line_width(0.9)
            cr.stroke()
        cr.restore()

    def _dismiss_window_popovers(self):
        root = self.get_root()
        if not root:
            return False
        closed = False
        for p_name in ("tool_settings_popover", "template_popover"):
            p = getattr(root, p_name, None)
            if p and p.get_visible():
                p.popdown()
                closed = True
        return closed

    def _on_primary_click_pressed(self, gesture, n_press, x, y):
        if self._dismiss_window_popovers():
            gesture.set_state(Gtk.EventSequenceState.DENIED)
            return
        if self.view_mode == "scrolled" and getattr(self, "_new_page_btn_rect", None):
            bx, by, bw, bh = self._new_page_btn_rect
            if bx <= x <= bx + bw and by <= y <= by + bh:
                root = self.get_root()
                if hasattr(root, "on_add_page_clicked"):
                    root.on_add_page_clicked()
                gesture.set_state(Gtk.EventSequenceState.CLAIMED)
                return
        if n_press == 2:
            p_idx, px, py = self.widget_to_page_coords(x, y)
            if p_idx is not None and self.document:
                pages = self.document.get("pages", [])
                if 0 <= p_idx < len(pages):
                    hit_idx = self._find_topmost_image_at(p_idx, px, py)
                    if hit_idx is not None:
                        item = pages[p_idx]["items"][hit_idx]
                        if item.get("kind") == "text":
                            self.select_item(p_idx, hit_idx)
                            rect = item.get("rect", [px, py, 200, 50])
                            wx, wy = self.page_to_widget_coords(p_idx, rect[0], rect[1])
                            self._prompt_text_box_creation(wx, wy, p_idx, rect, existing_item=item)

    # =========================================================================
    # Infinite Canvas & Middle-Click Pan Controller
    # =========================================================================
    def is_infinite_canvas(self):
        if not self.document:
            return False
        fmt = self.document.get("format")
        return fmt == "xcanvas" or bool(self.document.get("is_canvas", False))

    def _on_middle_drag_begin(self, gesture, x, y):
        self._middle_drag_start_screen = (x, y)
        self._pointer_pos = None
        self.set_cursor_from_name("grabbing")
        self.queue_draw()
        if self.is_infinite_canvas():
            view = self.document.setdefault("view", {})
            self._middle_drag_start_view = (float(view.get("cx", 0.0)), float(view.get("cy", 0.0)))
        else:
            self._middle_drag_start_vadj = self.vadj.get_value() if self.vadj else 0.0
            self._middle_drag_start_hadj = self.hadj.get_value() if hasattr(self, "hadj") and self.hadj else 0.0

    def _on_middle_drag_update(self, gesture, offset_x, offset_y):
        if self.is_infinite_canvas():
            orig_cx, orig_cy = getattr(self, "_middle_drag_start_view", (0.0, 0.0))
            zoom = max(0.05, self.zoom_level)
            view = self.document.setdefault("view", {})
            view["cx"] = orig_cx - (offset_x / zoom)
            view["cy"] = orig_cy - (offset_y / zoom)
            self.queue_draw()
        else:
            if self.vadj:
                start_v = getattr(self, "_middle_drag_start_vadj", 0.0)
                max_v = max(self.vadj.get_lower(), self.vadj.get_upper() - self.vadj.get_page_size())
                self.vadj.set_value(max(self.vadj.get_lower(), min(max_v, start_v - offset_y)))
            if hasattr(self, "hadj") and self.hadj:
                start_h = getattr(self, "_middle_drag_start_hadj", 0.0)
                max_h = max(self.hadj.get_lower(), self.hadj.get_upper() - self.hadj.get_page_size())
                self.hadj.set_value(max(self.hadj.get_lower(), min(max_h, start_h - offset_x)))

    def _on_middle_drag_end(self, gesture, offset_x, offset_y):
        self._middle_drag_start_screen = None
        self.set_cursor_from_name("none")
        if self.is_infinite_canvas():
            if self.on_modified_cb:
                self.on_modified_cb(True)
            self.queue_draw()

    def _on_scroll_event(self, controller, dx, dy):
        state = controller.get_current_event_state()
        has_ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)

        if has_ctrl or self.is_infinite_canvas():
            if getattr(self, "zoom_locked", False):
                return True

            delta = dy if dy != 0 else dx
            if delta < 0:
                factor = 1.15
            elif delta > 0:
                factor = 1.0 / 1.15
            else:
                return False

            if self.is_infinite_canvas():
                self.zoom_infinite_canvas(factor)
            else:
                root = self.get_root()
                if hasattr(root, "set_zoom"):
                    root.set_zoom(self.zoom_level * factor)
            return True

        return False

    def zoom_infinite_canvas(self, factor, pivot_x=None, pivot_y=None):
        if getattr(self, "zoom_locked", False):
            return
        if not self.is_infinite_canvas():
            return
        width = self.get_width()
        height = self.get_height()
        view = self.document.setdefault("view", {})
        cx = float(view.get("cx", 0.0))
        cy = float(view.get("cy", 0.0))
        old_zoom = max(0.05, self.zoom_level)
        new_zoom = max(0.1, min(10.0, old_zoom * factor))
        if abs(new_zoom - old_zoom) < 1e-4:
            return

        if pivot_x is not None and pivot_y is not None:
            canvas_pivot_x = cx + (pivot_x - width / 2.0) / old_zoom
            canvas_pivot_y = cy + (pivot_y - height / 2.0) / old_zoom
            new_cx = canvas_pivot_x - (pivot_x - width / 2.0) / new_zoom
            new_cy = canvas_pivot_y - (pivot_y - height / 2.0) / new_zoom
            view["cx"] = new_cx
            view["cy"] = new_cy

        self.zoom_level = new_zoom
        view["zoom"] = new_zoom
        if self.on_modified_cb:
            self.on_modified_cb(True)
        self.queue_draw()

    # =========================================================================
    # Fast Item Cloning (Avoids expensive copy.deepcopy on large stroke lists)
    # =========================================================================
    @staticmethod
    def _clone_item_fast(item):
        if not isinstance(item, dict):
            return copy.deepcopy(item)
        c = dict(item)
        if "config" in c and isinstance(c["config"], dict):
            c["config"] = dict(c["config"])
            if "rgba" in c["config"] and isinstance(c["config"]["rgba"], list):
                c["config"]["rgba"] = list(c["config"]["rgba"])
        if "samples" in c and isinstance(c["samples"], list):
            c["samples"] = [list(s) if isinstance(s, list) else dict(s) for s in c["samples"]]
        if "points" in c and isinstance(c["points"], list):
            c["points"] = [list(s) if isinstance(s, list) else dict(s) for s in c["points"]]
        if "rect" in c and isinstance(c["rect"], list):
            c["rect"] = list(c["rect"])
        if "pos" in c and isinstance(c["pos"], list):
            c["pos"] = list(c["pos"])
        if "rgba" in c and isinstance(c["rgba"], list):
            c["rgba"] = list(c["rgba"])
        if "start_pt" in c and isinstance(c["start_pt"], (list, tuple)):
            c["start_pt"] = list(c["start_pt"])
        if "end_pt" in c and isinstance(c["end_pt"], (list, tuple)):
            c["end_pt"] = list(c["end_pt"])
        if "_render_cache" in item:
            c["_render_cache"] = item["_render_cache"]
        return c

    @classmethod
    def _clone_items_list(cls, items):
        return [cls._clone_item_fast(it) for it in items]

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
                bbox = self._get_or_compute_stroke_bbox(item)
                if bbox:
                    max_x = max(max_x, bbox[2] + 50.0)
                    max_y = max(max_y, bbox[3] + 50.0)
            elif kind == "text":
                pos = item.get("pos", [0, 0])
                width = item.get("width", 300.0)
                if len(pos) >= 2:
                    max_x = max(max_x, pos[0] + width + 50.0)
                    max_y = max(max_y, pos[1] + 100.0)
            elif kind in ("image", "picture", "shape", "audio"):
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
        if self.is_infinite_canvas():
            view = self.document.get("view", {})
            cx = float(view.get("cx", 0.0))
            cy = float(view.get("cy", 0.0))
            zoom = max(0.05, self.zoom_level)
            width = self.get_width()
            height = self.get_height()
            return width / 2.0 - cx * zoom, height / 2.0 - cy * zoom

        width = self.get_width()
        height = self.get_height()
        pages = self.document.get("pages", []) if self.document else []
        if not (0 <= page_idx < len(pages)):
            return 20.0, 20.0

        pw, ph = self.get_page_size(page_idx)
        scaled_w = pw * self.zoom_level
        scaled_h = ph * self.zoom_level

        if self.view_mode == "paged":
            if getattr(self, "viewing_mode", "single") == "double":
                left = self.current_page - (self.current_page % 2)
                right = left + 1 if left + 1 < len(pages) else None
                pw_l, ph_l = self.get_page_size(left)
                sw_l = pw_l * self.zoom_level
                sh_l = ph_l * self.zoom_level
                if right is not None:
                    pw_r, ph_r = self.get_page_size(right)
                    sw_r = pw_r * self.zoom_level
                    sh_r = ph_r * self.zoom_level
                    gap = 24.0
                    spread_w = sw_l + gap + sw_r
                    max_h = max(sh_l, sh_r)
                    start_x = max(20.0, (width - spread_w) / 2.0)
                    base_y = max(20.0, (height - max_h) / 2.0)
                    if page_idx == left:
                        return start_x, base_y + (max_h - sh_l) / 2.0
                    elif page_idx == right:
                        return start_x + sw_l + gap, base_y + (max_h - sh_r) / 2.0
                else:
                    ox = max(20.0, (width - sw_l) / 2.0)
                    oy = max(20.0, (height - sh_l) / 2.0)
                    if page_idx == left:
                        return ox, oy
            # Single page view centered on screen
            offset_x = max(20.0, (width - scaled_w) / 2.0)
            offset_y = max(20.0, (height - scaled_h) / 2.0)
            return offset_x, offset_y
        else:
            # Scrolled mode: horizontally centered at each page's Y position
            offset_x = max(20.0, (width - scaled_w) / 2.0)
            if 0 <= page_idx < len(self.page_geometries):
                geom = self.page_geometries[page_idx]
                offset_y = geom[1] if len(geom) >= 6 else geom[0]
            else:
                offset_y = 20.0
            return offset_x, offset_y

    def widget_to_page_coords(self, wx, wy):
        if not self.document:
            return None, 0.0, 0.0

        if self.is_infinite_canvas():
            view = self.document.get("view", {})
            cx = float(view.get("cx", 0.0))
            cy = float(view.get("cy", 0.0))
            zoom = max(0.05, self.zoom_level)
            width = self.get_width()
            height = self.get_height()
            px = cx + (wx - width / 2.0) / zoom
            py = cy + (wy - height / 2.0) / zoom
            return 0, px, py

        pages = self.document.get("pages", [])
        if not pages:
            return None, 0.0, 0.0

        if self.view_mode == "paged":
            pages_to_check = []
            if getattr(self, "viewing_mode", "single") == "double":
                left = self.current_page - (self.current_page % 2)
                pages_to_check.append(left)
                if left + 1 < len(pages):
                    pages_to_check.append(left + 1)
            else:
                pages_to_check.append(self.current_page)

            for p_idx in pages_to_check:
                if 0 <= p_idx < len(pages):
                    ox, oy = self.get_page_widget_offset(p_idx)
                    pw, ph = self.get_page_size(p_idx)
                    scaled_w = pw * self.zoom_level
                    scaled_h = ph * self.zoom_level
                    if ox <= wx <= ox + scaled_w and oy <= wy <= oy + scaled_h:
                        px = (wx - ox) / self.zoom_level
                        py = (wy - oy) / self.zoom_level
                        return p_idx, px, py
            return None, 0.0, 0.0
        else:
            for idx in range(len(pages)):
                ox, oy = self.get_page_widget_offset(idx)
                pw, ph = self.get_page_size(idx)
                scaled_w = pw * self.zoom_level
                scaled_h = ph * self.zoom_level
                if ox <= wx <= ox + scaled_w and oy <= wy <= oy + scaled_h:
                    px = (wx - ox) / self.zoom_level
                    py = (wy - oy) / self.zoom_level
                    return idx, px, py
            return None, 0.0, 0.0

    def page_to_widget_coords(self, page_idx, px, py):
        if self.is_infinite_canvas():
            view = self.document.get("view", {})
            cx = float(view.get("cx", 0.0))
            cy = float(view.get("cy", 0.0))
            zoom = max(0.05, self.zoom_level)
            width = self.get_width()
            height = self.get_height()
            return width / 2.0 + (px - cx) * zoom, height / 2.0 + (py - cy) * zoom

        ox, oy = self.get_page_widget_offset(page_idx)
        return ox + px * self.zoom_level, oy + py * self.zoom_level

    # =========================================================================
    # Undo / Redo & Cache Management
    # =========================================================================
    def _record_undo_step(self, page_idx, old_items, new_items, clone=True):
        if clone:
            self.undo_stack.append((page_idx, self._clone_items_list(old_items), self._clone_items_list(new_items)))
        else:
            self.undo_stack.append((page_idx, old_items, new_items))
        if len(self.undo_stack) > 60:
            self.undo_stack.pop(0)
        self.redo_stack.clear()
        self._minimap_surface_cache = None
        if self.on_history_changed_cb:
            self.on_history_changed_cb(len(self.undo_stack) > 0, len(self.redo_stack) > 0)
        if self.on_modified_cb:
            self.on_modified_cb(True)

    def save_undo_state(self, page_idx=None, old_items=None):
        """Records an undo step for the current document state."""
        if not self.document:
            return
        pages = self.document.get("pages", [])
        if not pages:
            return
        if page_idx is None:
            page_idx = getattr(self, "_active_page_idx", None)
            if page_idx is None or not (0 <= page_idx < len(pages)):
                page_idx = 0
        if 0 <= page_idx < len(pages):
            curr = self._clone_items_list(pages[page_idx].get("items", []))
            prev = old_items if old_items is not None else getattr(self, "_pre_drag_items_snapshot", None)
            if prev is not None:
                self._record_undo_step(page_idx, prev, curr, clone=False)
            else:
                self._record_undo_step(page_idx, curr, curr, clone=True)

    def undo(self):
        if not self.undo_stack or not self.document:
            return
        self.clear_selection()
        page_idx, old_items, new_items = self.undo_stack.pop()
        self.redo_stack.append((page_idx, old_items, new_items))
        pages = self.document.get("pages", [])
        if 0 <= page_idx < len(pages):
            pages[page_idx]["items"] = self._clone_items_list(old_items)
            if self.is_infinite_canvas() and page_idx == 0:
                self.document["items"] = pages[page_idx]["items"]
            self.invalidate_page(page_idx, clear_bg=False)
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
            pages[page_idx]["items"] = self._clone_items_list(new_items)
            if self.is_infinite_canvas() and page_idx == 0:
                self.document["items"] = pages[page_idx]["items"]
            self.invalidate_page(page_idx, clear_bg=False)
        if self.on_history_changed_cb:
            self.on_history_changed_cb(len(self.undo_stack) > 0, len(self.redo_stack) > 0)
        if self.on_modified_cb:
            self.on_modified_cb(True)

    def invalidate_page(self, page_idx=None, clear_bg=True):
        self._minimap_surface_cache = None
        if page_idx is None:
            self.page_cache.clear()
            self.thumb_cache.clear()
            if clear_bg:
                self.bg_cache.clear()
        else:
            for k in [k for k in self.page_cache if k[0] == page_idx]:
                del self.page_cache[k]
            self.thumb_cache.pop(page_idx, None)
            if clear_bg:
                for k in [k for k in self.bg_cache if k[0] == page_idx]:
                    del self.bg_cache[k]
        self.queue_draw()

    def invalidate_thumb(self, page_idx=None):
        if page_idx is None:
            self.thumb_cache.clear()
        else:
            self.thumb_cache.pop(page_idx, None)

    # =========================================================================
    # Stylus & Mouse Input Handling (Zero-Latency 1-Euro Filtered Sampling)
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

        ts = getattr(self, "tool_settings", {})
        if tool_name == "dashed":
            d_cfg = ts.get("dashed", {})
            dash_len = float(d_cfg.get("dash_length", max(6.0, round(bw * 3.2, 1))))
            dash_gap = float(d_cfg.get("dash_gap", max(5.0, round(bw * 2.5, 1))))
            cfg = {
                "base_width": bw,
                "pressure_enabled": False,
                "pressure_min_factor": 1.0,
                "direction_strength": 0.0,
                "rgba": rgba,
                "dash_length": dash_len,
                "dash_gap": dash_gap,
            }
        elif tool_name == "calligraphy":
            c_cfg = ts.get("calligraphy", {})
            nib_deg = float(c_cfg.get("nib_angle_deg", -35.0))
            cfg = {
                "base_width": bw,
                "pressure_enabled": bool(c_cfg.get("pressure_enabled", True)),
                "pressure_min_factor": float(c_cfg.get("pressure_min_factor", 0.312)),
                "direction_strength": float(c_cfg.get("direction_strength", 0.78)),
                "nib_angle_rad": math.radians(nib_deg),
                "rgba": rgba,
            }
        elif tool_name == "speed":
            s_cfg = ts.get("speed", {})
            cfg = {
                "base_width": bw,
                "pressure_enabled": bool(s_cfg.get("pressure_enabled", True)),
                "pressure_min_factor": float(s_cfg.get("pressure_min_factor", 0.393)),
                "direction_strength": 0.0,
                "rgba": rgba,
                "speed_strength": float(s_cfg.get("speed_strength", 0.869)),
            }
        elif tool_name == "taper":
            t_cfg = ts.get("taper", {})
            cfg = {
                "base_width": bw,
                "pressure_enabled": bool(t_cfg.get("pressure_enabled", True)),
                "pressure_min_factor": float(t_cfg.get("pressure_min_factor", 0.519)),
                "direction_strength": 0.0,
                "rgba": rgba,
                "taper_enabled": True,
                "taper_min_factor": float(t_cfg.get("taper_min_factor", 0.345)),
            }
        elif tool_name == "highlighter":
            h_cfg = ts.get("highlighter", {})
            cfg = {
                "base_width": bw,
                "pressure_enabled": False,
                "pressure_min_factor": 1.0,
                "direction_strength": 0.0,
                "rgba": rgba,
                "highlighter_alpha": float(h_cfg.get("alpha", 0.5)),
            }
        elif tool_name == "neon":
            n_cfg = ts.get("neon", {})
            cfg = {
                "base_width": bw,
                "pressure_enabled": bool(n_cfg.get("pressure_enabled", True)),
                "pressure_min_factor": float(n_cfg.get("pressure_min_factor", 0.453)),
                "direction_strength": 0.0,
                "rgba": rgba,
                "neon": True,
                "neon_strength": float(n_cfg.get("neon_strength", 0.6)),
            }
        else:  # "pen"
            p_cfg = ts.get("pen", {})
            cfg = {
                "base_width": bw,
                "pressure_enabled": bool(p_cfg.get("pressure_enabled", True)),
                "pressure_min_factor": float(p_cfg.get("pressure_min_factor", 0.453)),
                "direction_strength": 0.0,
                "rgba": rgba,
            }

        first_sample = (
            [round(px, 2), round(py, 2), round(pressure, 3), 0]
            if tool_name == "speed"
            else [round(px, 2), round(py, 2), round(pressure, 3)]
        )
        hw = bw * 0.65
        stroke = {
            "kind": "stroke",
            "tool": tool_name,
            "config": cfg,
            "samples": [first_sample],
            "smooth_scale": 0.7950984251968506,
            "_bbox": [px - hw, py - hw, px + hw, py + hw],
        }
        if cfg.get("neon"):
            stroke["neon"] = True
            stroke["neon_strength"] = cfg.get("neon_strength", 0.6)
        if tool_name == "speed":
            s_cfg = ts.get("speed", {})
            stroke["speed_scale"] = float(s_cfg.get("speed_scale", 0.5918616115704284))
        return stroke

    def _begin_interaction(self, wx, wy, pressure=0.55, is_stylus=False, timestamp_sec=None):
        if self.is_infinite_canvas() and not is_stylus and getattr(self, "show_minimap", True):
            m_rect = self._get_minimap_rect()
            if m_rect and self._hit_test_minimap(wx, wy, m_rect):
                self._is_minimap_dragging = True
                self._pan_to_minimap_coord(wx, wy, m_rect)
                return

        page_idx, px, py = self.widget_to_page_coords(wx, wy)
        if page_idx is None:
            self.clear_selection()
            return

        self._effective_tool = self._detect_effective_tool(is_stylus)
        self._active_page_idx = page_idx
        self._smoothed_pressure = pressure
        self._last_sample_time = timestamp_sec if timestamp_sec is not None else time.perf_counter()
        self._last_raw_pt = (px, py)
        self._filter_vel = (0.0, 0.0)

        pages = self.document.get("pages", [])
        page = pages[page_idx]
        page.setdefault("items", [])

        if self._effective_tool not in ("select", "lasso"):
            self.clear_selection()

        # --- Lasso Tool or Active Lasso Transform ---
        if self._effective_tool == "lasso":
            handle = self._hit_test_lasso_handles(px, py) if self.lasso_page_idx == page_idx else None
            if handle is not None:
                self._effective_tool = "lasso"
                self._lasso_transform_mode = handle
                self._lasso_start_pt = (px, py)
                self._lasso_orig_bbox = list(self.lasso_bbox)
                self._lasso_orig_items = {idx: self._clone_item_fast(page["items"][idx]) for idx in self.lasso_item_indices}
                self._pre_drag_items_snapshot = self._clone_items_list(page["items"])
                self._lasso_modified = False
                # Cache static page without moving items so drag updates don't re-render the full page
                self._transient_skip_page = page_idx
                self._transient_skip_indices = set(self.lasso_item_indices)
                self.invalidate_page(page_idx, clear_bg=False)
                return

            self.clear_selection()
            self._effective_tool = "lasso"
            self._lasso_polygon = [(px, py)]
            self.queue_draw()
            return

        # --- Single Item Select / Transform (checked first when in select tool) ---
        if self._effective_tool == "select":
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
                self._pre_drag_items_snapshot = self._clone_items_list(page["items"])
                self._transform_modified = False
                self._transient_skip_page = page_idx
                self._transient_skip_indices = {self.selected_item_idx}
                self.invalidate_page(page_idx, clear_bg=False)
            return

        # Check for interactive TapeItem tap (Active Recall toggle: covered <-> peeled)
        for it_idx in range(len(page.get("items", [])) - 1, -1, -1):
            it = page["items"][it_idx]
            if it.get("kind") == "tape":
                pts = it.get("points") or []
                t_w = float(it.get("width", 32.0))
                hit_tape = False
                if len(pts) >= 2:
                    for si in range(len(pts) - 1):
                        p1x, p1y = float(pts[si][0]), float(pts[si][1])
                        p2x, p2y = float(pts[si+1][0]), float(pts[si+1][1])
                        dx, dy = p2x - p1x, p2y - p1y
                        l2 = dx * dx + dy * dy
                        if l2 <= 1e-6:
                            dist = math.hypot(px - p1x, py - p1y)
                        else:
                            t = max(0.0, min(1.0, ((px - p1x) * dx + (py - p1y) * dy) / l2))
                            dist = math.hypot(px - (p1x + t * dx), py - (p1y + t * dy))
                        if dist <= t_w / 2.0 + 4.0:
                            hit_tape = True
                            break
                else:
                    rect = it.get("rect")
                    if rect and len(rect) >= 4:
                        rx, ry, rw, rh = float(rect[0]), float(rect[1]), float(rect[2]), float(rect[3])
                        if rx <= px <= rx + rw and ry <= py <= ry + rh:
                            hit_tape = True
                if hit_tape:
                    old_items = self._clone_items_list(page.get("items", []))
                    it["peeled"] = not bool(it.get("peeled", False))
                    new_items = self._clone_items_list(page.get("items", []))
                    self._record_undo_step(page_idx, old_items, new_items, clone=False)
                    self.invalidate_page(page_idx, clear_bg=False)
                    self.queue_draw()
                    self._active_page_idx = None
                    return

        # Check for interactive audio item click (Play/Pause disc or waveform seek) when not in select tool
        for it_idx in range(len(page.get("items", [])) - 1, -1, -1):
            it = page["items"][it_idx]
            if it.get("kind") == "audio":
                rect = it.get("rect", [0, 0, 230, 48])
                x0, y0, w0, h0 = float(rect[0]), float(rect[1]), float(rect[2]), float(rect[3])
                if x0 <= px <= x0 + w0 and y0 <= py <= y0 + h0:
                    r_half = h0 / 2.0
                    disc_cx, disc_cy = x0 + r_half, y0 + r_half
                    disc_r = r_half * 0.75
                    dist = math.hypot(px - disc_cx, py - disc_cy)
                    if dist <= disc_r + 6.0:
                        from audio_manager import AudioManager
                        am = AudioManager.get_default()
                        def _on_tick(item):
                            self._active_audio_fade = (item.get("rid"), item.get("playhead", -1))
                            self.invalidate_page(page_idx, clear_bg=False)
                            self.queue_draw()
                        def _on_finished(item):
                            self._active_audio_fade = None
                            self.invalidate_page(page_idx, clear_bg=False)
                            self.queue_draw()
                        am.toggle_play(it, self.document,
                                       on_tick=_on_tick,
                                       on_finished=_on_finished)
                        self._active_audio_fade = (it.get("rid"), it.get("playhead", -1))
                        self.invalidate_page(page_idx, clear_bg=False)
                        self.queue_draw()
                        return
                    elif px > x0 + h0:
                        wave_x0 = x0 + 2.0 * r_half + 4.0
                        wave_x1 = x0 + w0 - r_half
                        dur = it.get("duration_ms", 0)
                        if wave_x1 > wave_x0 and dur > 0:
                            frac = max(0.0, min(1.0, (px - wave_x0) / (wave_x1 - wave_x0)))
                            target_ms = int(frac * dur)
                            from audio_manager import AudioManager
                            am = AudioManager.get_default()
                            am.seek_playback(it, target_ms, self.document)
                            self._active_audio_fade = (it.get("rid"), target_ms)
                            self.invalidate_page(page_idx, clear_bg=False)
                            self.queue_draw()
                            return

        # Check for ink tap-to-seek during audio playback
        if getattr(self, "_active_audio_fade", None) is not None:
            f_rid, _ = self._active_audio_fade
            if f_rid is not None:
                for it in page.get("items", []):
                    if it.get("kind") != "audio":
                        rec_id = it.get("rec") or (it.get("sync", {}).get("rid") if isinstance(it.get("sync"), dict) else None)
                        if rec_id == f_rid:
                            rms = it.get("rec_ms") or (it.get("sync", {}).get("ms") if isinstance(it.get("sync"), dict) else None)
                            if rms is not None and self._hit_test_item_point(it, px, py):
                                from audio_manager import AudioManager
                                am = AudioManager.get_default()
                                if am._active_audio_item:
                                    seek_to = max(0, rms - 500)
                                    am.seek_playback(am._active_audio_item, seek_to, self.document)
                                    self._active_audio_fade = (f_rid, seek_to)
                                    self.invalidate_page(page_idx, clear_bg=False)
                                    self.queue_draw()
                                    return

        self.clear_selection()

        if self._effective_tool == "text":
            self.clear_selection()
            self._sized_text_box = None
            self._pre_drag_items_snapshot = self._clone_items_list(page.get("items", []))
            self._active_text_box = {
                "start_pt": [px, py],
                "rect": [px, py, 1.0, 1.0],
                "page_idx": page_idx,
            }
            self.queue_draw()
            return

        if self._effective_tool == "shape":
            self.clear_selection()
            self._pre_drag_items_snapshot = self._clone_items_list(page.get("items", []))
            s_type = getattr(self, "shape_type", "rectangle")
            s_width = float(getattr(self, "shape_stroke_width", 3.5))
            s_fill = bool(getattr(self, "shape_fill", False))
            self._active_shape = {
                "kind": "shape",
                "shape_type": s_type,
                "rect": [px, py, 1.0, 1.0],
                "start_pt": [px, py],
                "end_pt": [px, py],
                "rgba": list(self.pen_rgba),
                "stroke_width": s_width,
                "fill": s_fill,
                "angle": 0.0,
            }
            self.queue_draw()
            return

        if self._effective_tool == "tape":
            self.clear_selection()
            self._pre_drag_items_snapshot = self._clone_items_list(page.get("items", []))
            t_cfg = self.tool_settings.get("tape", {})
            self._active_tape = {
                "kind": "tape",
                "points": [[round(px, 2), round(py, 2)], [round(px, 2), round(py, 2)]],
                "width": float(t_cfg.get("width", 32.0)),
                "pattern": t_cfg.get("pattern", "stripes"),
                "rgba": list(t_cfg.get("rgba", [255, 230, 100, 255])),
                "peeled": False,
            }
            self.queue_draw()
            return

        if self._effective_tool == "connector":
            self.clear_selection()
            self._pre_drag_items_snapshot = self._clone_items_list(page.get("items", []))
            c_cfg = self.tool_settings.get("connector", {})
            self._active_connector = {
                "kind": "connector",
                "points": [[round(px, 2), round(py, 2)], [round(px, 2), round(py, 2)]],
                "stroke_width": float(c_cfg.get("stroke_width", 2.5)),
                "routing": c_cfg.get("routing", "curved"),
                "head_end": c_cfg.get("head_end", "arrow"),
                "head_start": c_cfg.get("head_start", "none"),
                "stroke_rgba": list(c_cfg.get("stroke_rgba", [80, 80, 80, 255])),
                "dashed": bool(c_cfg.get("dashed", False)),
                "neon": bool(c_cfg.get("neon", False)),
                "neon_strength": float(c_cfg.get("neon_strength", 0.6)),
            }
            self.queue_draw()
            return

        if self._effective_tool == "insert_space":
            self.clear_selection()
            self._pre_drag_items_snapshot = self._clone_items_list(page.get("items", []))
            self._active_space_cut = {
                "start_y": py,
                "current_y": py,
                "page_idx": page_idx,
            }
            self.queue_draw()
            return

        if self._effective_tool == "eraser":
            # Shallow list copy is sufficient since eraser only removes items without mutating them
            self._pre_drag_items_snapshot = list(page["items"])
            self._eraser_cursor = (wx, wy)
            self._last_erase_pt = (px, py)
            self._erased_during_drag = self._erase_at(page_idx, px, py, px, py)
            self.queue_draw()
            return

        # For normal drawing, avoid deep-copying existing strokes on pen-down!
        self._pre_drag_items_snapshot = list(page["items"])
        self._active_stroke = self._build_android_stroke_dict(self._effective_tool, px, py, pressure)
        self.queue_draw()

    def _append_filtered_stroke_sample(self, px, py, pressure, now_sec):
        """Adaptive 1-Euro style low-pass filter for jitter-free, zero-lag stroke sampling."""
        samples = self._active_stroke["samples"]
        last = samples[-1]
        dx_raw = px - self._last_raw_pt[0]
        dy_raw = py - self._last_raw_pt[1]
        raw_dist = math.hypot(dx_raw, dy_raw)

        # Sub-pixel deadzone scaled by zoom level
        min_step = 0.45 / max(0.5, self.zoom_level)
        if raw_dist < min_step:
            return False

        dt_sec = max(1e-4, now_sec - self._last_sample_time)
        dt_ms = max(1, int(round(dt_sec * 1000.0)))

        # Compute instantaneous velocity (page units / sec)
        vx = (px - last[0]) / dt_sec
        vy = (py - last[1]) / dt_sec
        vel = math.hypot(vx, vy)

        # 1-Euro adaptive cutoff: low cutoff at slow speed (removes jitter), high cutoff at high speed (zero lag)
        min_cutoff = 2.4
        beta = 0.035
        cutoff = min_cutoff + beta * vel
        tau = 1.0 / (2.0 * math.pi * cutoff)
        alpha = 1.0 / (1.0 + tau / dt_sec)
        alpha = max(0.35, min(0.94, alpha))

        smooth_x = alpha * px + (1.0 - alpha) * last[0]
        smooth_y = alpha * py + (1.0 - alpha) * last[1]
        self._smoothed_pressure = 0.62 * pressure + 0.38 * self._smoothed_pressure

        # If stylus moved very fast across a large gap, subdivide intermediate points along the tangent
        step_dist = math.hypot(smooth_x - last[0], smooth_y - last[1])
        is_speed = self._effective_tool == "speed"

        if step_dist > 18.0 and len(samples) >= 2:
            prev = samples[-2]
            tx = last[0] - prev[0]
            ty = last[1] - prev[1]
            mid_x = 0.5 * (last[0] + smooth_x) + 0.12 * tx
            mid_y = 0.5 * (last[1] + smooth_y) + 0.12 * ty
            mid_p = 0.5 * (last[2] + self._smoothed_pressure)
            half_dt = max(1, dt_ms // 2)
            if is_speed:
                samples.append([round(mid_x, 2), round(mid_y, 2), round(mid_p, 3), half_dt])
                samples.append([round(smooth_x, 2), round(smooth_y, 2), round(self._smoothed_pressure, 3), dt_ms - half_dt])
            else:
                samples.append([round(mid_x, 2), round(mid_y, 2), round(mid_p, 3)])
                samples.append([round(smooth_x, 2), round(smooth_y, 2), round(self._smoothed_pressure, 3)])
        else:
            if is_speed:
                samples.append([round(smooth_x, 2), round(smooth_y, 2), round(self._smoothed_pressure, 3), dt_ms])
            else:
                samples.append([round(smooth_x, 2), round(smooth_y, 2), round(self._smoothed_pressure, 3)])

        # Update cached bounding box & invalidate stroke render cache
        bbox = self._active_stroke.get("_bbox")
        if bbox:
            hw = self._get_stroke_base_width(self._active_stroke) * 0.65
            if smooth_x - hw < bbox[0]:
                bbox[0] = smooth_x - hw
            if smooth_y - hw < bbox[1]:
                bbox[1] = smooth_y - hw
            if smooth_x + hw > bbox[2]:
                bbox[2] = smooth_x + hw
            if smooth_y + hw > bbox[3]:
                bbox[3] = smooth_y + hw

        self._active_stroke.pop("_render_cache", None)
        self._last_sample_time = now_sec
        self._last_raw_pt = (px, py)
        return True

    def _update_interaction(self, wx, wy, pressure=0.55, is_stylus=False, timestamp_sec=None, queue_redraw=True):
        if getattr(self, "_is_minimap_dragging", False):
            m_rect = self._get_minimap_rect()
            if m_rect:
                self._pan_to_minimap_coord(wx, wy, m_rect)
            return True

        if self._active_page_idx is None:
            return False

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
                            cur_it.pop("_bbox", None)
                            cur_it.pop("_render_cache", None)
                        elif kind == "text":
                            opos = orig_it.get("pos", [0.0, 0.0])
                            cur_it["pos"] = [round(opos[0] + dx, 2), round(opos[1] + dy, 2)]
                        elif kind in ("image", "picture"):
                            orect = orig_it.get("rect", [0.0, 0.0, 100.0, 100.0])
                            cur_it["rect"] = [round(orect[0] + dx, 2), round(orect[1] + dy, 2), orect[2], orect[3]]
                        elif kind == "shape":
                            orect = orig_it.get("rect", [0.0, 0.0, 10.0, 10.0])
                            cur_it["rect"] = [round(orect[0] + dx, 2), round(orect[1] + dy, 2), orect[2], orect[3]]
                            if orig_it.get("start_pt"):
                                cur_it["start_pt"] = [round(orig_it["start_pt"][0] + dx, 2), round(orig_it["start_pt"][1] + dy, 2)]
                            if orig_it.get("end_pt"):
                                cur_it["end_pt"] = [round(orig_it["end_pt"][0] + dx, 2), round(orig_it["end_pt"][1] + dy, 2)]
                            if orig_it.get("points"):
                                cur_it["points"] = [[round(p[0] + dx, 2), round(p[1] + dy, 2)] for p in orig_it["points"]]
                        elif kind in ("tape", "connector"):
                            if orig_it.get("points"):
                                cur_it["points"] = [[round(p[0] + dx, 2), round(p[1] + dy, 2)] for p in orig_it["points"]]
                            if orig_it.get("rect"):
                                orect = orig_it["rect"]
                                cur_it["rect"] = [round(orect[0] + dx, 2), round(orect[1] + dy, 2), orect[2], orect[3]]
                        elif kind == "audio":
                            orect = orig_it.get("rect", [0.0, 0.0, 230.0, 48.0])
                            cur_it["rect"] = [round(orect[0] + dx, 2), round(orect[1] + dy, 2), orect[2], orect[3]]

                    self.lasso_bbox = [obx + dx, oby + dy, obw, obh]
                    self._lasso_modified = True
                    if queue_redraw:
                        self.queue_draw()
                    return True

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
                            cur_it.pop("_bbox", None)
                            cur_it.pop("_render_cache", None)
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
                        elif kind == "shape":
                            orect = orig_it.get("rect", [0.0, 0.0, 10.0, 10.0])
                            cur_it["rect"] = [
                                round(nx + (orect[0] - obx) * sx, 2),
                                round(ny + (orect[1] - oby) * sy, 2),
                                round(max(2.0, orect[2] * sx), 2),
                                round(max(2.0, orect[3] * sy), 2),
                            ]
                            if orig_it.get("start_pt"):
                                cur_it["start_pt"] = [round(nx + (orig_it["start_pt"][0] - obx) * sx, 2), round(ny + (orig_it["start_pt"][1] - oby) * sy, 2)]
                            if orig_it.get("end_pt"):
                                cur_it["end_pt"] = [round(nx + (orig_it["end_pt"][0] - obx) * sx, 2), round(ny + (orig_it["end_pt"][1] - oby) * sy, 2)]
                            if orig_it.get("points"):
                                cur_it["points"] = [[round(nx + (p[0] - obx) * sx, 2), round(ny + (p[1] - oby) * sy, 2)] for p in orig_it["points"]]
                        elif kind in ("tape", "connector"):
                            if orig_it.get("points"):
                                cur_it["points"] = [[round(nx + (p[0] - obx) * sx, 2), round(ny + (p[1] - oby) * sy, 2)] for p in orig_it["points"]]
                            if orig_it.get("rect"):
                                orect = orig_it["rect"]
                                cur_it["rect"] = [
                                    round(nx + (orect[0] - obx) * sx, 2),
                                    round(ny + (orect[1] - oby) * sy, 2),
                                    round(max(2.0, orect[2] * sx), 2),
                                    round(max(2.0, orect[3] * sy), 2),
                                ]
                        elif kind == "audio":
                            orect = orig_it.get("rect", [0.0, 0.0, 230.0, 48.0])
                            cur_it["rect"] = [
                                round(nx + (orect[0] - obx) * sx, 2),
                                round(ny + (orect[1] - oby) * sy, 2),
                                orect[2],
                                orect[3],
                            ]

                    self.lasso_bbox = [nx, ny, nw, nh]
                    self._lasso_modified = True
                    if queue_redraw:
                        self.queue_draw()
                    return True

            if self._lasso_polygon:
                lx, ly = self._lasso_polygon[-1]
                if math.hypot(px - lx, py - ly) >= 2.5:
                    self._lasso_polygon.append((px, py))
                    if queue_redraw:
                        self.queue_draw()
                    return True
            return False

        # --- Single Image Transform (Rendered dynamically over static page cache) ---
        if self._effective_tool == "select" and self._transform_mode is not None:
            item = self.get_selected_item()
            if not item or not self._transform_orig_rect:
                return False
            ox, oy, ow, oh = self._transform_orig_rect
            ocx = ox + ow / 2.0
            ocy = oy + oh / 2.0
            angle = self._transform_orig_angle

            if self._transform_mode == "move":
                dx = px - self._transform_start_page_pt[0]
                dy = py - self._transform_start_page_pt[1]
                item["rect"] = [round(ox + dx, 2), round(oy + dy, 2), round(ow, 2), round(oh, 2)]
                if item.get("kind") == "text":
                    item["pos"] = [item["rect"][0], item["rect"][1]]
                self._transform_modified = True
                if queue_redraw:
                    self.queue_draw()
                return True

            elif self._transform_mode == "rotate":
                raw_angle = math.atan2(py - ocy, px - ocx) + math.pi / 2.0
                if self._is_shift_held(is_stylus):
                    snap = math.radians(15.0)
                    raw_angle = round(raw_angle / snap) * snap
                item["angle"] = round(raw_angle, 4)
                self._transform_modified = True
                if queue_redraw:
                    self.queue_draw()
                return True

            elif self._transform_mode in ("nw", "ne", "se", "sw"):
                dx = px - ocx
                dy = py - ocy
                cos_a = math.cos(-angle)
                sin_a = math.sin(-angle)
                lx = dx * cos_a - dy * sin_a
                ly = dx * sin_a + dy * cos_a

                hw, hh = ow / 2.0, oh / 2.0
                min_size = 24.0
                min_w = 110.0 if item.get("kind") == "audio" else min_size
                min_h = 32.0 if item.get("kind") == "audio" else min_size

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

                raw_w = max(min_w, (lx - ax) * sx)
                raw_h = max(min_h, (ly - ay) * sy)

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
                if item.get("kind") == "text":
                    item["pos"] = [item["rect"][0], item["rect"][1]]
                    item["width"] = item["rect"][2]
                    item["height"] = item["rect"][3]
                self._transform_modified = True
                if queue_redraw:
                    self.queue_draw()
                return True
            return False

        # --- Active Shape Tool ---
        if self._effective_tool == "shape" and self._active_shape is not None:
            start_x, start_y = self._active_shape["start_pt"]
            shape_type = self._active_shape.get("shape_type", "rectangle")
            shift_held = self._is_shift_held(is_stylus)

            if shape_type in ("line", "arrow"):
                dx = px - start_x
                dy = py - start_y
                if shift_held:
                    angle = math.atan2(dy, dx)
                    snap = math.radians(15.0)
                    snapped_angle = round(angle / snap) * snap
                    dist = math.hypot(dx, dy)
                    cur_px = start_x + dist * math.cos(snapped_angle)
                    cur_py = start_y + dist * math.sin(snapped_angle)
                else:
                    cur_px, cur_py = px, py
                self._active_shape["end_pt"] = [cur_px, cur_py]
                min_x = min(start_x, cur_px)
                min_y = min(start_y, cur_py)
                w = max(2.0, abs(cur_px - start_x))
                h = max(2.0, abs(cur_py - start_y))
                self._active_shape["rect"] = [round(min_x, 2), round(min_y, 2), round(w, 2), round(h, 2)]
            else:
                w = abs(px - start_x)
                h = abs(py - start_y)
                if shift_held or shape_type == "circle":
                    s = max(w, h)
                    w = h = s
                min_x = start_x if px >= start_x else start_x - w
                min_y = start_y if py >= start_y else start_y - h
                self._active_shape["end_pt"] = [px, py]
                self._active_shape["rect"] = [round(min_x, 2), round(min_y, 2), round(max(2.0, w), 2), round(max(2.0, h), 2)]

            if queue_redraw:
                self.queue_draw()
            return True

        # --- Active Text Box Drag-to-Define ---
        if self._effective_tool == "text" and self._active_text_box is not None:
            start_x, start_y = self._active_text_box["start_pt"]
            min_x = min(start_x, px)
            min_y = min(start_y, py)
            w = max(10.0, abs(px - start_x))
            h = max(10.0, abs(py - start_y))
            self._active_text_box["rect"] = [round(min_x, 2), round(min_y, 2), round(w, 2), round(h, 2)]
            if queue_redraw:
                self.queue_draw()
            return True

        # --- Active Tape Tool Drag ---
        if self._effective_tool == "tape" and getattr(self, "_active_tape", None) is not None:
            self._active_tape["points"][1] = [round(px, 2), round(py, 2)]
            if queue_redraw:
                self.queue_draw()
            return True

        # --- Active Connector Tool Drag ---
        if self._effective_tool == "connector" and getattr(self, "_active_connector", None) is not None:
            self._active_connector["points"][1] = [round(px, 2), round(py, 2)]
            if queue_redraw:
                self.queue_draw()
            return True

        # --- Active Insert Space Cut Drag ---
        if self._effective_tool == "insert_space" and getattr(self, "_active_space_cut", None) is not None:
            self._active_space_cut["current_y"] = py
            if queue_redraw:
                self.queue_draw()
            return True

        # --- Spatial Swept Eraser ---
        if self._effective_tool == "eraser":
            self._eraser_cursor = (wx, wy)
            p0 = self._last_erase_pt or (px, py)
            self._last_erase_pt = (px, py)
            if self._erase_at(self._active_page_idx, p0[0], p0[1], px, py):
                self._erased_during_drag = True
            if queue_redraw:
                self.queue_draw()
            return True

        # --- Active Brush Stroke ---
        if self._active_stroke is not None:
            now_sec = timestamp_sec if timestamp_sec is not None else time.perf_counter()
            changed = self._append_filtered_stroke_sample(px, py, pressure, now_sec)
            if changed and queue_redraw:
                self.queue_draw()
            return changed

        return False

    def _end_interaction(self):
        if getattr(self, "_is_minimap_dragging", False):
            self._is_minimap_dragging = False
            if self.on_modified_cb:
                self.on_modified_cb(True)
            self.queue_draw()
            return

        if self._active_page_idx is None:
            return

        pages = self.document.get("pages", [])
        page = pages[self._active_page_idx]

        # Clear any transient transform skip masks
        if self._transient_skip_page is not None:
            p_idx = self._transient_skip_page
            self._transient_skip_page = None
            self._transient_skip_indices = None
            self.invalidate_page(p_idx, clear_bg=False)

        if self._effective_tool == "lasso":
            if self._lasso_transform_mode is not None:
                if self._lasso_modified and self._pre_drag_items_snapshot is not None:
                    self._record_undo_step(
                        self._active_page_idx,
                        self._pre_drag_items_snapshot,
                        self._clone_items_list(page["items"]),
                        clone=False
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
                poly_xs = [p[0] for p in poly]
                poly_ys = [p[1] for p in poly]
                p_min_x, p_max_x = min(poly_xs), max(poly_xs)
                p_min_y, p_max_y = min(poly_ys), max(poly_ys)

                selected_indices = []
                for idx, item in enumerate(page.get("items", [])):
                    kind = item.get("kind") or item.get("type")
                    if kind == "stroke":
                        bbox = self._get_or_compute_stroke_bbox(item)
                        if bbox and (bbox[2] < p_min_x or bbox[0] > p_max_x or bbox[3] < p_min_y or bbox[1] > p_max_y):
                            continue
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
                    elif kind in ("image", "picture", "shape", "audio"):
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
                    self._clone_items_list(page["items"]),
                    clone=False
                )
            elif not self._transform_modified and self._active_page_idx is not None:
                sel_it = self.get_selected_item()
                if sel_it and (sel_it.get("kind") or sel_it.get("type")) == "audio":
                    rect = sel_it.get("rect", [0, 0, 230, 48])
                    x0, y0, w0, h0 = float(rect[0]), float(rect[1]), float(rect[2]), float(rect[3])
                    r_half = h0 / 2.0
                    disc_cx, disc_cy = x0 + r_half, y0 + r_half
                    disc_r = r_half * 0.75
                    if math.hypot(px - disc_cx, py - disc_cy) <= disc_r + 6.0:
                        from audio_manager import AudioManager
                        am = AudioManager.get_default()
                        p_idx = self._active_page_idx
                        def _on_tick(item):
                            self._active_audio_fade = (item.get("rid"), item.get("playhead", -1))
                            self.invalidate_page(p_idx, clear_bg=False)
                            self.queue_draw()
                        def _on_finished(item):
                            self._active_audio_fade = None
                            self.invalidate_page(p_idx, clear_bg=False)
                            self.queue_draw()
                        am.toggle_play(sel_it, self.document,
                                       on_tick=_on_tick,
                                       on_finished=_on_finished)
                        self._active_audio_fade = (sel_it.get("rid"), sel_it.get("playhead", -1))
                        self.invalidate_page(p_idx, clear_bg=False)
                        self.queue_draw()
            self._transform_mode = None
            self._transform_orig_rect = None
            self._transform_modified = False
            self._active_page_idx = None
            self.queue_draw()
            return

        if self._effective_tool == "eraser":
            self._eraser_cursor = None
            self._last_erase_pt = None
            if self._erased_during_drag and self._pre_drag_items_snapshot is not None:
                self._record_undo_step(
                    self._active_page_idx,
                    self._pre_drag_items_snapshot,
                    list(page["items"]),
                    clone=False
                )
            self._erased_during_drag = False
            self._active_page_idx = None
            self.queue_draw()
            return

        if self._effective_tool == "shape" and self._active_shape is not None:
            shape_to_place = self._active_shape
            self._active_shape = None
            rect = shape_to_place.get("rect", [0, 0, 0, 0])
            start_pt = shape_to_place.get("start_pt", [0, 0])
            end_pt = shape_to_place.get("end_pt", [0, 0])
            dist = math.hypot(end_pt[0] - start_pt[0], end_pt[1] - start_pt[1])

            if (rect[2] >= 4.0 and rect[3] >= 4.0) or dist >= 6.0:
                from audio_manager import AudioManager
                am = AudioManager.get_default()
                if am.is_recording and hasattr(am, "_active_rid"):
                    shape_to_place["rec"] = am._active_rid
                    shape_to_place["rec_ms"] = int((time.time() - am._record_start_time) * 1000)
                page.setdefault("items", []).append(shape_to_place)
                self._record_undo_step(
                    self._active_page_idx,
                    self._pre_drag_items_snapshot,
                    self._clone_items_list(page["items"]),
                    clone=False
                )
                self.invalidate_page(self._active_page_idx, clear_bg=False)
                new_idx = len(page["items"]) - 1
                self.select_item(self._active_page_idx, new_idx)
                if getattr(self, "on_tool_change_cb", None):
                    self.on_tool_change_cb("select")
                else:
                    self.active_tool = "select"

            self._active_page_idx = None
            self._pre_drag_items_snapshot = None
            self.queue_draw()
            return

        if self._effective_tool == "text" and self._active_text_box is not None:
            tb = self._active_text_box
            self._active_text_box = None
            rect = tb["rect"]
            p_idx = tb["page_idx"]
            if rect[2] < 30.0 or rect[3] < 20.0:
                pw, ph = self.get_page_size(p_idx)
                rect = [rect[0], rect[1], min(260.0, pw - rect[0] - 20.0), 60.0]

            self._sized_text_box = {
                "page_idx": p_idx,
                "rect": rect,
                "text": "",
                "font_family": getattr(self, "text_font_family", "Sans"),
                "point_size": float(getattr(self, "text_size_pt", 14.0)),
            }
            self.queue_draw()
            wx_pt, wy_pt = self.page_to_widget_coords(p_idx, rect[0], rect[1])
            self._prompt_text_box_creation(wx_pt, wy_pt, p_idx, rect)
            self._active_page_idx = None
            return

        if self._effective_tool == "tape" and getattr(self, "_active_tape", None) is not None:
            tape_to_place = self._active_tape
            self._active_tape = None
            pts = tape_to_place.get("points", [])
            if len(pts) >= 2:
                dist = math.hypot(pts[1][0] - pts[0][0], pts[1][1] - pts[0][1])
                if dist >= 6.0:
                    page.setdefault("items", []).append(tape_to_place)
                    self._record_undo_step(
                        self._active_page_idx,
                        self._pre_drag_items_snapshot,
                        self._clone_items_list(page["items"]),
                        clone=False
                    )
                    self.invalidate_page(self._active_page_idx, clear_bg=False)
            self._active_page_idx = None
            self._pre_drag_items_snapshot = None
            self.queue_draw()
            return

        if self._effective_tool == "connector" and getattr(self, "_active_connector", None) is not None:
            conn_to_place = self._active_connector
            self._active_connector = None
            pts = conn_to_place.get("points", [])
            if len(pts) >= 2:
                dist = math.hypot(pts[1][0] - pts[0][0], pts[1][1] - pts[0][1])
                if dist >= 6.0:
                    page.setdefault("items", []).append(conn_to_place)
                    self._record_undo_step(
                        self._active_page_idx,
                        self._pre_drag_items_snapshot,
                        self._clone_items_list(page["items"]),
                        clone=False
                    )
                    self.invalidate_page(self._active_page_idx, clear_bg=False)
            self._active_page_idx = None
            self._pre_drag_items_snapshot = None
            self.queue_draw()
            return

        if self._effective_tool == "insert_space" and getattr(self, "_active_space_cut", None) is not None:
            sc = self._active_space_cut
            self._active_space_cut = None
            start_y = sc["start_y"]
            delta_y = sc["current_y"] - start_y
            if abs(delta_y) >= 8.0:
                for it in page.get("items", []):
                    kind = it.get("kind") or it.get("type")
                    if kind == "stroke":
                        pts = it.get("samples") or it.get("points") or []
                        ys = [float(s["y"]) if isinstance(s, dict) else float(s[1]) for s in pts]
                        if ys and min(ys) >= start_y:
                            for s in pts:
                                if isinstance(s, dict):
                                    s["y"] = round(s["y"] + delta_y, 2)
                                else:
                                    s[1] = round(s[1] + delta_y, 2)
                            it.pop("_bbox", None)
                            it.pop("_render_cache", None)
                    elif kind in ("shape", "image", "picture", "text", "audio"):
                        rect = it.get("rect")
                        if rect and len(rect) >= 4 and rect[1] >= start_y:
                            rect[1] = round(rect[1] + delta_y, 2)
                            if it.get("start_pt") and it["start_pt"][1] >= start_y:
                                it["start_pt"][1] = round(it["start_pt"][1] + delta_y, 2)
                            if it.get("end_pt") and it["end_pt"][1] >= start_y:
                                it["end_pt"][1] = round(it["end_pt"][1] + delta_y, 2)
                            if it.get("pos") and it["pos"][1] >= start_y:
                                it["pos"][1] = round(it["pos"][1] + delta_y, 2)
                    elif kind in ("tape", "connector"):
                        pts = it.get("points") or []
                        ys = [float(p[1]) for p in pts]
                        if ys and min(ys) >= start_y:
                            for p in pts:
                                p[1] = round(p[1] + delta_y, 2)
                self._record_undo_step(
                    self._active_page_idx,
                    self._pre_drag_items_snapshot,
                    self._clone_items_list(page["items"]),
                    clone=False
                )
                self.invalidate_page(self._active_page_idx, clear_bg=False)
            self._active_page_idx = None
            self._pre_drag_items_snapshot = None
            self.queue_draw()
            return

        if self._active_stroke is not None:
            if getattr(self, "snap_shapes", False):
                snapped_shape = self._try_snap_stroke_to_shape(self._active_stroke)
                if snapped_shape is not None:
                    page.setdefault("items", []).append(snapped_shape)
                    self._record_undo_step(
                        self._active_page_idx,
                        self._pre_drag_items_snapshot,
                        self._clone_items_list(page["items"]),
                        clone=False
                    )
                    self.invalidate_page(self._active_page_idx, clear_bg=False)
                    self._active_stroke = None
                    self._active_page_idx = None
                    self._pre_drag_items_snapshot = None
                    self.queue_draw()
                    return

            raw_samples = self._active_stroke["samples"]
            if len(raw_samples) == 1:
                s0 = raw_samples[0]
                if len(s0) >= 4:
                    raw_samples.append([round(s0[0] + 0.35, 2), round(s0[1] + 0.35, 2), s0[2], 5])
                else:
                    raw_samples.append([round(s0[0] + 0.35, 2), round(s0[1] + 0.35, 2), s0[2]])
                self._active_stroke.pop("_render_cache", None)

            from audio_manager import AudioManager
            am = AudioManager.get_default()
            if am.is_recording and hasattr(am, "_active_rid"):
                self._active_stroke["rec"] = am._active_rid
                self._active_stroke["rec_ms"] = int((time.time() - am._record_start_time) * 1000)
            page.setdefault("items", []).append(self._active_stroke)
            self._record_undo_step(
                self._active_page_idx,
                self._pre_drag_items_snapshot,
                list(page["items"]),
                clone=False
            )
            # Incrementally paint finished stroke onto cached surface instead of re-rendering the entire page!
            self.append_stroke_to_cached_page(self._active_page_idx, self._active_stroke)

        self._active_stroke = None
        self._active_page_idx = None
        self._pre_drag_items_snapshot = None
        self.queue_draw()

    def _try_snap_stroke_to_shape(self, stroke):
        """Hold-to-snap shape recognizer (parity with Android ShapeRecognizer)."""
        raw_samples = stroke.get("samples") or stroke.get("points") or []
        if len(raw_samples) < 8:
            return None
        pts = [(float(s["x"]), float(s["y"])) if isinstance(s, dict) else (float(s[0]), float(s[1])) for s in raw_samples]
        cfg = stroke.get("config", {})
        bw = float(cfg.get("base_width", stroke.get("stroke_width", 3.5)))
        rgba = cfg.get("rgba") or stroke.get("stroke_rgba") or stroke.get("color") or list(self.pen_rgba)
        is_neon = bool(stroke.get("neon") or cfg.get("neon"))
        neon_s = float(stroke.get("neon_strength", cfg.get("neon_strength", 0.6)))

        x0, y0 = pts[0]
        xN, yN = pts[-1]
        chord_len = math.hypot(xN - x0, yN - y0)
        total_len = 0.0
        for i in range(len(pts) - 1):
            total_len += math.hypot(pts[i+1][0] - pts[i][0], pts[i+1][1] - pts[i][1])

        if total_len < 16.0:
            return None

        # 1. Check for straight line
        if chord_len / total_len > 0.94:
            min_x = min(x0, xN)
            min_y = min(y0, yN)
            w = max(2.0, abs(xN - x0))
            h = max(2.0, abs(yN - y0))
            return {
                "kind": "shape",
                "shape_type": "line",
                "start_pt": [round(x0, 2), round(y0, 2)],
                "end_pt": [round(xN, 2), round(yN, 2)],
                "rect": [round(min_x, 2), round(min_y, 2), round(w, 2), round(h, 2)],
                "rgba": list(rgba),
                "stroke_width": bw,
                "fill": False,
                "neon": is_neon,
                "neon_strength": neon_s,
            }

        # 2. Check for closed loop (Circle, Ellipse, Rectangle)
        if chord_len / total_len < 0.22:
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            min_x, max_x = min(xs), max(xs)
            min_y, max_y = min(ys), max(ys)
            w = max(10.0, max_x - min_x)
            h = max(10.0, max_y - min_y)
            cx = (min_x + max_x) / 2.0
            cy = (min_y + max_y) / 2.0
            rx = w / 2.0
            ry = h / 2.0

            ellipse_err = 0.0
            for px, py in pts:
                val = ((px - cx) / rx) ** 2 + ((py - cy) / ry) ** 2
                ellipse_err += abs(val - 1.0)
            avg_ellipse_err = ellipse_err / len(pts)

            if avg_ellipse_err < 0.26:
                is_circle = abs(w - h) / max(w, h) < 0.20
                stype = "circle" if is_circle else "ellipse"
                if is_circle:
                    dim = (w + h) / 2.0
                    min_x = cx - dim / 2.0
                    min_y = cy - dim / 2.0
                    w = h = dim
                return {
                    "kind": "shape",
                    "shape_type": stype,
                    "start_pt": [round(min_x, 2), round(min_y, 2)],
                    "end_pt": [round(min_x + w, 2), round(min_y + h, 2)],
                    "rect": [round(min_x, 2), round(min_y, 2), round(w, 2), round(h, 2)],
                    "rgba": list(rgba),
                    "stroke_width": bw,
                    "fill": False,
                    "neon": is_neon,
                    "neon_strength": neon_s,
                }

            rect_margin = 0.22 * min(w, h)
            near_edges = 0
            for px, py in pts:
                if (abs(px - min_x) <= rect_margin or abs(px - max_x) <= rect_margin or
                    abs(py - min_y) <= rect_margin or abs(py - max_y) <= rect_margin):
                    near_edges += 1
            if near_edges / len(pts) > 0.82:
                return {
                    "kind": "shape",
                    "shape_type": "rectangle",
                    "start_pt": [round(min_x, 2), round(min_y, 2)],
                    "end_pt": [round(max_x, 2), round(max_y, 2)],
                    "rect": [round(min_x, 2), round(min_y, 2), round(w, 2), round(h, 2)],
                    "rgba": list(rgba),
                    "stroke_width": bw,
                    "fill": False,
                    "neon": is_neon,
                    "neon_strength": neon_s,
                }

        return None

    # --- Stylus Signals (Coalesced High-Rate Hardware Backlog Processing) ---
    def _on_stylus_down(self, gesture, x, y):
        if self._dismiss_window_popovers():
            gesture.set_state(Gtk.EventSequenceState.DENIED)
            return
        self._stylus_active = True
        pressure = self._get_stylus_pressure()
        self._begin_interaction(x, y, pressure=pressure, is_stylus=True)

    def _on_stylus_motion(self, gesture, x, y):
        if not self._stylus_active:
            return
        now = time.perf_counter()
        ok, axes_backlog = gesture.get_backlog()
        if ok and axes_backlog:
            n_back = len(axes_backlog)
            dt_step = min(0.008, max(0.001, (now - self._last_sample_time) / max(1, n_back + 1)))
            t_cursor = now - dt_step * n_back
            for elem in axes_backlog:
                if hasattr(elem, "axes") and len(elem.axes) >= 2:
                    bx, by = elem.axes[0], elem.axes[1]
                    bp = elem.axes[2] if len(elem.axes) > 2 and elem.axes[2] >= 0 else self._get_stylus_pressure()
                    if bx and by:
                        self._update_interaction(
                            bx, by, pressure=bp, is_stylus=True,
                            timestamp_sec=t_cursor, queue_redraw=False
                        )
                    t_cursor += dt_step
        pressure = self._get_stylus_pressure()
        self._update_interaction(x, y, pressure=pressure, is_stylus=True, timestamp_sec=now, queue_redraw=False)
        self.queue_draw()

    def _on_stylus_up(self, gesture, x, y):
        if self._stylus_active:
            self._end_interaction()
            self._stylus_active = False

    # --- Mouse / Touch Drag Signals ---
    def _on_drag_begin(self, gesture, start_x, start_y):
        if self._stylus_active:
            return
        if self._dismiss_window_popovers():
            gesture.set_state(Gtk.EventSequenceState.DENIED)
            return
        if self.view_mode == "scrolled" and getattr(self, "_new_page_btn_rect", None):
            bx, by, bw, bh = self._new_page_btn_rect
            if bx <= start_x <= bx + bw and by <= start_y <= by + bh:
                gesture.set_state(Gtk.EventSequenceState.DENIED)
                return
        self._pointer_pos = (start_x, start_y)
        self.set_cursor_from_name("none")
        self._begin_interaction(start_x, start_y, pressure=0.55, is_stylus=False)

    def _on_drag_update(self, gesture, offset_x, offset_y):
        if self._stylus_active:
            return
        if self._active_page_idx is None and not getattr(self, "_is_minimap_dragging", False):
            return
        ok, start_x, start_y = gesture.get_start_point()
        if ok:
            cur_x = start_x + offset_x
            cur_y = start_y + offset_y
            self._pointer_pos = (cur_x, cur_y)
            self._update_interaction(cur_x, cur_y, pressure=0.55, is_stylus=False)

    def _on_drag_end(self, gesture, offset_x, offset_y):
        if self._stylus_active:
            return
        ok, start_x, start_y = gesture.get_start_point()
        if ok:
            self._pointer_pos = (start_x + offset_x, start_y + offset_y)
        self._end_interaction()

    # =========================================================================
    # Spatial Swept Eraser & Text Insertion
    # =========================================================================
    @staticmethod
    def _point_to_segment_dist(px, py, x1, y1, x2, y2):
        dx = x2 - x1
        dy = y2 - y1
        if dx == 0.0 and dy == 0.0:
            return math.hypot(px - x1, py - y1)
        t = ((px - x1) * dx + (py - y1) * dy) / (dx * dx + dy * dy)
        t = max(0.0, min(1.0, t))
        return math.hypot(px - (x1 + t * dx), py - (y1 + t * dy))

    @classmethod
    def _segments_distance(cls, ax, ay, bx, by, cx, cy, dx, dy):
        """Minimum distance between segment AB and segment CD for continuous swept erasing."""
        if ax == bx and ay == by:
            return cls._point_to_segment_dist(ax, ay, cx, cy, dx, dy)
        if cx == dx and cy == dy:
            return cls._point_to_segment_dist(cx, cy, ax, ay, bx, by)
        return min(
            cls._point_to_segment_dist(ax, ay, cx, cy, dx, dy),
            cls._point_to_segment_dist(bx, by, cx, cy, dx, dy),
            cls._point_to_segment_dist(cx, cy, ax, ay, bx, by),
            cls._point_to_segment_dist(dx, dy, ax, ay, bx, by),
        )

    def _erase_at(self, page_idx, x0_erase, y0_erase, px, py):
        pages = self.document.get("pages", [])
        if not (0 <= page_idx < len(pages)):
            return False
        page = pages[page_idx]
        items = page.get("items", [])
        if not items:
            return False

        radius = self.eraser_radius / max(0.5, self.zoom_level)
        e_min_x = min(x0_erase, px) - radius
        e_min_y = min(y0_erase, py) - radius
        e_max_x = max(x0_erase, px) + radius
        e_max_y = max(y0_erase, py) + radius

        kept = []
        removed = False

        for item in items:
            kind = item.get("kind") or item.get("type")
            hit = False
            if kind == "stroke":
                bbox = self._get_or_compute_stroke_bbox(item)
                if bbox and (bbox[2] < e_min_x or bbox[0] > e_max_x or bbox[3] < e_min_y or bbox[1] > e_max_y):
                    kept.append(item)
                    continue

                samples = item.get("samples") or item.get("points") or []
                half_w = self._get_stroke_base_width(item) / 2.0
                threshold = radius + half_w
                for i in range(len(samples)):
                    s1 = samples[i]
                    x1 = s1["x"] if isinstance(s1, dict) else s1[0]
                    y1 = s1["y"] if isinstance(s1, dict) else s1[1]
                    if i == 0:
                        if self._point_to_segment_dist(x1, y1, x0_erase, y0_erase, px, py) <= threshold:
                            hit = True
                            break
                    else:
                        s0 = samples[i - 1]
                        x0 = s0["x"] if isinstance(s0, dict) else s0[0]
                        y0 = s0["y"] if isinstance(s0, dict) else s0[1]
                        if self._segments_distance(x0_erase, y0_erase, px, py, x0, y0, x1, y1) <= threshold:
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
            self.invalidate_page(page_idx, clear_bg=False)
        return removed

    def _prompt_text_insertion(self, wx, wy, page_idx, px, py):
        pw, ph = self.get_page_size(page_idx)
        default_rect = [px, py, min(260.0, pw - px - 20.0), 60.0]
        self._prompt_text_box_creation(wx, wy, page_idx, default_rect)

    def _prompt_text_box_creation(self, wx, wy, page_idx, rect, existing_item=None):
        popover = Gtk.Popover()
        popover.set_autohide(True)
        popover.set_parent(self)
        rg = Gdk.Rectangle()
        rg.x = max(0, int(wx))
        rg.y = max(0, int(wy))
        rg.width = max(1, int(rect[2] * self.zoom_level))
        rg.height = max(1, int(rect[3] * self.zoom_level))
        popover.set_pointing_to(rg)
        popover.set_position(Gtk.PositionType.BOTTOM)

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        vbox.set_margin_top(8)
        vbox.set_margin_bottom(8)
        vbox.set_margin_start(8)
        vbox.set_margin_end(8)
        vbox.set_size_request(280, -1)

        if existing_item:
            title_lbl = Gtk.Label(
                label="Edit Text Box",
                xalign=0.0
            )
            title_lbl.add_css_class("heading")
            vbox.append(title_lbl)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_size_request(-1, 80)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        tv = Gtk.TextView()
        tv.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        buf = tv.get_buffer()
        if existing_item:
            buf.set_text(existing_item.get("text", ""))
        scrolled.set_child(tv)
        vbox.append(scrolled)

        opt_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)

        fonts = ["Sans", "Serif", "Monospace", "Cantarell", "DejaVu Sans", "Liberation Sans", "Ubuntu"]
        cur_font = existing_item.get("font_family", getattr(self, "text_font_family", "Sans")) if existing_item else getattr(self, "text_font_family", "Sans")
        font_dd = Gtk.DropDown.new_from_strings(fonts)
        font_dd.set_hexpand(True)
        if cur_font in fonts:
            font_dd.set_selected(fonts.index(cur_font))
        opt_box.append(font_dd)

        cur_size = float(existing_item.get("point_size", getattr(self, "text_size_pt", 14.0))) if existing_item else getattr(self, "text_size_pt", 14.0)
        size_spin = Gtk.SpinButton.new_with_range(8.0, 72.0, 1.0)
        size_spin.set_value(cur_size)
        size_spin.set_tooltip_text("Font Size (pt)")
        opt_box.append(size_spin)

        vbox.append(opt_box)

        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        cancel_btn = Gtk.Button(label="Cancel")
        save_btn = Gtk.Button(label="Done")
        save_btn.add_css_class("suggested-action")
        btn_box.append(Gtk.Box(hexpand=True))
        btn_box.append(cancel_btn)
        btn_box.append(save_btn)
        vbox.append(btn_box)

        def _on_buf_changed(b):
            s_iter, e_iter = b.get_bounds()
            live_txt = b.get_text(s_iter, e_iter, False)
            if self._sized_text_box:
                self._sized_text_box["text"] = live_txt
                self.queue_draw()

        buf.connect("changed", _on_buf_changed)

        def _on_font_or_size_changed(*_):
            if self._sized_text_box:
                s_font = font_dd.get_selected_item()
                self._sized_text_box["font_family"] = s_font.get_string() if s_font else "Sans"
                self._sized_text_box["point_size"] = float(size_spin.get_value())
                self.queue_draw()

        font_dd.connect("notify::selected", _on_font_or_size_changed)
        size_spin.connect("value-changed", _on_font_or_size_changed)

        def commit_box(*_):
            start, end = buf.get_bounds()
            txt = buf.get_text(start, end, False).strip()
            sel_font_item = font_dd.get_selected_item()
            sel_font = sel_font_item.get_string() if sel_font_item else "Sans"
            sel_sz = float(size_spin.get_value())

            self.text_font_family = sel_font
            self.text_size_pt = sel_sz

            if not txt and not existing_item:
                txt = "Text"

            if self.document:
                pages = self.document.get("pages", [])
                if 0 <= page_idx < len(pages):
                    page = pages[page_idx]
                    old_items = self._clone_items_list(page.get("items", []))
                    if existing_item:
                        existing_item["text"] = txt
                        existing_item["font_family"] = sel_font
                        existing_item["point_size"] = sel_sz
                    else:
                        new_item = {
                            "kind": "text",
                            "text": txt,
                            "rect": [float(rect[0]), float(rect[1]), float(rect[2]), float(rect[3])],
                            "pos": [float(rect[0]), float(rect[1])],
                            "width": float(rect[2]),
                            "height": float(rect[3]),
                            "point_size": float(sel_sz),
                            "font_family": sel_font,
                            "rgba": list(self.pen_rgba),
                            "angle": 0.0,
                        }
                        page.setdefault("items", []).append(new_item)
                        new_idx = len(page["items"]) - 1
                        self.select_item(page_idx, new_idx)
                        if getattr(self, "on_tool_change_cb", None):
                            self.on_tool_change_cb("select")
                        else:
                            self.active_tool = "select"

                    self._record_undo_step(page_idx, old_items, self._clone_items_list(page["items"]), clone=False)
                    self.invalidate_page(page_idx, clear_bg=False)

            self._sized_text_box = None
            self.queue_draw()
            popover.popdown()
            GLib.idle_add(popover.unparent)

        def _on_popover_closed(p):
            self._sized_text_box = None
            self.queue_draw()
            GLib.idle_add(p.unparent)

        cancel_btn.connect("clicked", lambda *_: popover.popdown())
        save_btn.connect("clicked", commit_box)
        popover.connect("closed", _on_popover_closed)

        popover.set_child(vbox)
        popover.popup()
        tv.grab_focus()

    def _draw_visual_text_box(self, cr, rect, text="", font_family="Sans", point_size=14.0):
        x, y, w, h = float(rect[0]), float(rect[1]), float(rect[2]), float(rect[3])
        zoom = max(0.05, getattr(self, "zoom_level", 1.0))
        inv_z = 1.0 / zoom

        cr.save()
        # 1. Translucent background tint
        cr.set_source_rgba(0.12, 0.44, 0.86, 0.08)
        cr.rectangle(x, y, w, h)
        cr.fill()

        # 2. Crisp dashed border
        cr.set_source_rgba(0.12, 0.44, 0.86, 0.85)
        cr.set_line_width(1.5 * inv_z)
        cr.set_dash([5.0 * inv_z, 3.0 * inv_z])
        cr.rectangle(x, y, w, h)
        cr.stroke()

        # 3. Four corner handles
        cr.set_dash([])
        handle_sz = 6.0 * inv_z
        corners = [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
        for cx, cy in corners:
            cr.set_source_rgb(1.0, 1.0, 1.0)
            cr.rectangle(cx - handle_sz / 2.0, cy - handle_sz / 2.0, handle_sz, handle_sz)
            cr.fill()
            cr.set_source_rgba(0.12, 0.44, 0.86, 0.9)
            cr.set_line_width(1.2 * inv_z)
            cr.rectangle(cx - handle_sz / 2.0, cy - handle_sz / 2.0, handle_sz, handle_sz)
            cr.stroke()

        # 4. Preview text or placeholder inside the visual text box
        if text:
            cr.set_source_rgba(self.pen_rgba[0] / 255.0, self.pen_rgba[1] / 255.0, self.pen_rgba[2] / 255.0, 1.0)
            layout = PangoCairo.create_layout(cr)
            layout.set_text(text, -1)
            font_desc = self._get_cached_font_desc(font_family, point_size)
            layout.set_font_description(font_desc)
            if w > 0:
                layout.set_width(int(w * Pango.SCALE))
                layout.set_wrap(Pango.WrapMode.WORD_CHAR)
            cr.move_to(x + 4 * inv_z, y + 4 * inv_z)
            PangoCairo.show_layout(cr, layout)
        else:
            cr.set_source_rgba(0.12, 0.44, 0.86, 0.45)
            layout = PangoCairo.create_layout(cr)
            layout.set_text("Text Box", -1)
            font_desc = self._get_cached_font_desc(font_family, max(10.0, point_size * 0.9))
            layout.set_font_description(font_desc)
            cr.move_to(x + 6 * inv_z, y + 6 * inv_z)
            PangoCairo.show_layout(cr, layout)

        cr.restore()

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

    def start_page_slide_animation(self, old_page, new_page):
        if self.view_mode != "paged" or old_page == new_page or not self.document:
            return
        pages = self.document.get("pages", [])
        if not pages:
            return

        is_double = (getattr(self, "viewing_mode", "single") == "double")
        if is_double:
            from_left = old_page - (old_page % 2)
            to_left = new_page - (new_page % 2)
            if from_left == to_left:
                return
            from_pages = [from_left]
            if from_left + 1 < len(pages):
                from_pages.append(from_left + 1)
            to_pages = [to_left]
            if to_left + 1 < len(pages):
                to_pages.append(to_left + 1)
            direction = 1 if to_left > from_left else -1
        else:
            from_pages = [old_page]
            to_pages = [new_page]
            direction = 1 if new_page > old_page else -1

        self._page_transition = {
            "from_pages": from_pages,
            "to_pages": to_pages,
            "direction": direction,
            "start_time": time.perf_counter(),
            "duration": 0.24,
        }

        if getattr(self, "_tick_cb_id", None) is not None:
            self.remove_tick_callback(self._tick_cb_id)
            self._tick_cb_id = None

        def _on_anim_tick(widget, frame_clock):
            trans = getattr(self, "_page_transition", None)
            if not trans:
                self._tick_cb_id = None
                return GLib.SOURCE_REMOVE
            now = time.perf_counter()
            if now - trans["start_time"] >= trans["duration"]:
                self._page_transition = None
                self._tick_cb_id = None
                self.queue_draw()
                return GLib.SOURCE_REMOVE
            self.queue_draw()
            return GLib.SOURCE_CONTINUE

        self._tick_cb_id = self.add_tick_callback(_on_anim_tick)
        self.queue_draw()

    def _draw_page_shadow(self, cr, rx, ry, rw, rh):
        pass

    def _draw_single_page(self, cr, page_idx, page, pw, ph, offset_x, offset_y):
        scaled_w = pw * self.zoom_level
        scaled_h = ph * self.zoom_level
        rx = round(offset_x)
        ry = round(offset_y)

        surface = self._get_cached_page_surface(page_idx, page, pw, ph)
        cr.set_source_surface(surface, rx, ry)
        cr.paint()

        # Draw any actively transforming items dynamically on top of the static cache
        if self._transient_skip_page == page_idx and self._transient_skip_indices:
            items = page.get("items", [])
            cr.save()
            cr.translate(offset_x, offset_y)
            cr.scale(self.zoom_level, self.zoom_level)
            cr.rectangle(0, 0, pw, ph)
            cr.clip()
            for idx in sorted(self._transient_skip_indices):
                if 0 <= idx < len(items):
                    self.draw_page_item(cr, items[idx], self.assets, self.templates)
            cr.restore()

        if self._active_stroke is not None and self._active_page_idx == page_idx:
            cr.save()
            cr.translate(offset_x, offset_y)
            cr.scale(self.zoom_level, self.zoom_level)
            cr.rectangle(0, 0, pw, ph)
            cr.clip()
            self.draw_stroke(cr, self._active_stroke, is_active=True)
            cr.restore()

        if self._active_shape is not None and self._active_page_idx == page_idx:
            cr.save()
            cr.translate(offset_x, offset_y)
            cr.scale(self.zoom_level, self.zoom_level)
            cr.rectangle(0, 0, pw, ph)
            cr.clip()
            self.draw_shape(cr, self._active_shape, is_preview=True)
            cr.restore()

        if getattr(self, "_active_tape", None) is not None and self._active_page_idx == page_idx:
            cr.save()
            cr.translate(offset_x, offset_y)
            cr.scale(self.zoom_level, self.zoom_level)
            cr.rectangle(0, 0, pw, ph)
            cr.clip()
            self.draw_tape(cr, self._active_tape)
            cr.restore()

        if getattr(self, "_active_connector", None) is not None and self._active_page_idx == page_idx:
            cr.save()
            cr.translate(offset_x, offset_y)
            cr.scale(self.zoom_level, self.zoom_level)
            cr.rectangle(0, 0, pw, ph)
            cr.clip()
            self.draw_connector(cr, self._active_connector)
            cr.restore()

        if getattr(self, "_active_space_cut", None) is not None and self._active_page_idx == page_idx:
            sc = self._active_space_cut
            y0 = sc["start_y"]
            y1 = sc["current_y"]
            cr.save()
            cr.translate(offset_x, offset_y)
            cr.scale(self.zoom_level, self.zoom_level)
            cr.set_source_rgba(0.2, 0.5, 0.9, 0.85)
            cr.set_line_width(2.0 / max(0.1, self.zoom_level))
            cr.set_dash([6.0, 4.0])
            cr.move_to(0, y0)
            cr.line_to(pw, y0)
            cr.stroke()
            cr.set_source_rgba(0.2, 0.5, 0.9, 0.15)
            cr.rectangle(0, min(y0, y1), pw, abs(y1 - y0))
            cr.fill()
            cr.set_source_rgba(0.2, 0.5, 0.9, 0.85)
            cr.move_to(0, y1)
            cr.line_to(pw, y1)
            cr.stroke()
            cr.restore()

        # Clean 2px border around the page (1px larger, no shadow)
        cr.save()
        cr.set_source_rgba(0.0, 0.0, 0.0, 0.22)
        cr.set_line_width(2.0)
        cr.rectangle(rx + 1.0, ry + 1.0, round(scaled_w) - 2.0, round(scaled_h) - 2.0)
        cr.stroke()
        cr.restore()

        if self._sized_text_box is not None and self._sized_text_box.get("page_idx") == page_idx:
            cr.save()
            cr.translate(offset_x, offset_y)
            cr.scale(self.zoom_level, self.zoom_level)
            tb = self._sized_text_box
            self._draw_visual_text_box(
                cr,
                tb["rect"],
                text=tb.get("text", ""),
                font_family=tb.get("font_family", "Sans"),
                point_size=tb.get("point_size", 14.0)
            )
            cr.restore()
        elif self._active_text_box is not None and self._active_page_idx == page_idx:
            cr.save()
            cr.translate(offset_x, offset_y)
            cr.scale(self.zoom_level, self.zoom_level)
            rect = self._active_text_box["rect"]
            self._draw_visual_text_box(cr, rect)
            cr.restore()

        if self.selected_page_idx == page_idx:
            sel_item = self.get_selected_item()
            if sel_item and (sel_item.get("kind") or sel_item.get("type")) in ("image", "picture", "shape", "text", "audio"):
                self._draw_selection_gizmo(cr, offset_x, offset_y, sel_item)

        self._draw_lasso_overlay(cr, offset_x, offset_y, page_idx)

    def on_draw(self, drawing_area, cr, width, height):
        if not self.document:
            return

        if self.is_infinite_canvas():
            self._draw_infinite_canvas(cr, width, height)
            if self._eraser_cursor is not None:
                ex, ey = self._eraser_cursor
                cr.save()
                cr.set_source_rgba(0.85, 0.2, 0.25, 0.75)
                cr.set_line_width(2.0)
                cr.arc(ex, ey, self.eraser_radius, 0, 2 * math.pi)
                cr.stroke()
                cr.restore()
            if getattr(self, "show_minimap", True):
                self._draw_minimap(cr, width, height)
            self._draw_pointer_cursor(cr)
            return

        pages = self.document.get("pages", [])
        if not pages:
            return

        if self.view_mode == "paged":
            self._new_page_btn_rect = None
            trans = getattr(self, "_page_transition", None)
            if trans:
                now = time.perf_counter()
                t = min(1.0, max(0.0, (now - trans["start_time"]) / trans["duration"]))
                ease = 1.0 - math.pow(1.0 - t, 3)
                shift = (1.0 - ease) * width * trans["direction"]

                out_shift = shift - (width * trans["direction"])
                for p_idx in trans["from_pages"]:
                    if 0 <= p_idx < len(pages):
                        page = pages[p_idx]
                        pw, ph = self.get_page_size(p_idx)
                        ox, oy = self.get_page_widget_offset(p_idx)
                        self._draw_single_page(cr, p_idx, page, pw, ph, ox + out_shift, oy)

                for p_idx in trans["to_pages"]:
                    if 0 <= p_idx < len(pages):
                        page = pages[p_idx]
                        pw, ph = self.get_page_size(p_idx)
                        ox, oy = self.get_page_widget_offset(p_idx)
                        self._draw_single_page(cr, p_idx, page, pw, ph, ox + shift, oy)

                if t >= 1.0:
                    self._page_transition = None
                self._draw_pointer_cursor(cr)
                return

            pages_to_draw = []
            if getattr(self, "viewing_mode", "single") == "double":
                left = self.current_page - (self.current_page % 2)
                pages_to_draw.append(left)
                if left + 1 < len(pages):
                    pages_to_draw.append(left + 1)
            else:
                pages_to_draw.append(self.current_page)

            for p_idx in pages_to_draw:
                if 0 <= p_idx < len(pages):
                    page = pages[p_idx]
                    pw, ph = self.get_page_size(p_idx)
                    ox, oy = self.get_page_widget_offset(p_idx)
                    self._draw_single_page(cr, p_idx, page, pw, ph, ox, oy)
        else:
            scroll_y = self.vadj.get_value() if self.vadj else 0.0
            view_h = self.vadj.get_page_size() if self.vadj else height
            if view_h <= 0:
                view_h = height
            vis_top = scroll_y - 100.0
            vis_bottom = scroll_y + view_h + 100.0

            for idx, page in enumerate(pages):
                pw, ph = self.get_page_size(idx)
                ox, oy = self.get_page_widget_offset(idx)
                scaled_h = ph * self.zoom_level
                if oy + scaled_h < vis_top or oy > vis_bottom:
                    continue
                self._draw_single_page(cr, idx, page, pw, ph, ox, oy)

            # Draw "+ New Page" button beneath the last page in scrolled mode
            if pages:
                last_idx = len(pages) - 1
                _, last_oy = self.get_page_widget_offset(last_idx)
                _, last_ph = self.get_page_size(last_idx)
                last_bottom = last_oy + last_ph * self.zoom_level

                btn_w = 140.0
                btn_h = 36.0
                btn_x = max(20.0, (width - btn_w) / 2.0)
                btn_y = last_bottom + 24.0
                self._new_page_btn_rect = (btn_x, btn_y, btn_w, btn_h)
                self._draw_new_page_button(cr, btn_x, btn_y, btn_w, btn_h)
            else:
                self._new_page_btn_rect = None

        if self._eraser_cursor is not None:
            ex, ey = self._eraser_cursor
            cr.save()
            cr.set_source_rgba(0.85, 0.2, 0.25, 0.75)
            cr.set_line_width(2.0)
            cr.arc(ex, ey, self.eraser_radius, 0, 2 * math.pi)
            cr.stroke()
            cr.restore()

        self._draw_pointer_cursor(cr)


    def _draw_new_page_button(self, cr, x, y, w, h):
        cr.save()
        r = h / 2.0
        cr.new_sub_path()
        cr.arc(x + w - r, y + r, r, -math.pi / 2.0, math.pi / 2.0)
        cr.arc(x + r, y + r, r, math.pi / 2.0, 3.0 * math.pi / 2.0)
        cr.close_path()

        hovered = getattr(self, "_hover_new_page_btn", False)
        if hovered:
            cr.set_source_rgba(0.25, 0.45, 0.9, 0.22)
        else:
            cr.set_source_rgba(0.5, 0.5, 0.5, 0.12)
        cr.fill_preserve()

        cr.set_source_rgba(0.5, 0.5, 0.5, 0.28)
        cr.set_line_width(1.0)
        cr.stroke()

        # Text and plus icon using GTK widget foreground color
        fg = self.get_color()
        cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.85)

        # Plus sign
        plus_cx = x + 24.0
        plus_cy = y + r
        cr.set_line_width(2.0)
        cr.move_to(plus_cx - 5.0, plus_cy)
        cr.line_to(plus_cx + 5.0, plus_cy)
        cr.move_to(plus_cx, plus_cy - 5.0)
        cr.line_to(plus_cx, plus_cy + 5.0)
        cr.stroke()

        # Label "New Page"
        layout = PangoCairo.create_layout(cr)
        layout.set_text("New Page", -1)
        desc = Pango.FontDescription.from_string("Sans Bold 10")
        layout.set_font_description(desc)
        cr.move_to(x + 36.0, y + (h - 18.0) / 2.0)
        PangoCairo.show_layout(cr, layout)
        cr.restore()

    # =========================================================================
    # Infinite Canvas & Minimap Cairo Rendering
    # =========================================================================

    def _get_minimap_rect(self):
        mw = 190.0
        mh = 135.0
        pad = 18.0
        w = float(self.get_width())
        h = float(self.get_height())
        return (w - mw - pad, h - mh - pad, mw, mh)

    def _hit_test_minimap(self, x, y, rect):
        mx, my, mw, mh = rect
        return mx <= x <= mx + mw and my <= y <= my + mh

    def _pan_to_minimap_coord(self, x, y, rect):
        if not hasattr(self, "_minimap_transform") or not self._minimap_transform:
            return
        map_ox, map_oy, s, bx1, by1, bw, bh = self._minimap_transform
        if s <= 0:
            return
        canvas_x = (x - map_ox) / s
        canvas_y = (y - map_oy) / s
        view = self.document.setdefault("view", {})
        view["cx"] = float(canvas_x)
        view["cy"] = float(canvas_y)
        self.queue_draw()

    def _draw_infinite_canvas(self, cr, width, height):
        bg = self.document.get("background", {}) if isinstance(self.document.get("background"), dict) else {}
        paper_col = bg.get("paper_color", [255, 255, 255, 255])
        cr.set_source_rgba(paper_col[0] / 255.0, paper_col[1] / 255.0, paper_col[2] / 255.0, paper_col[3] / 255.0)
        cr.rectangle(0, 0, width, height)
        cr.fill()

        view = self.document.setdefault("view", {})
        cx = float(view.get("cx", 0.0))
        cy = float(view.get("cy", 0.0))
        zoom = max(0.05, self.zoom_level)

        left_c = cx - (width / 2.0) / zoom
        right_c = cx + (width / 2.0) / zoom
        top_c = cy - (height / 2.0) / zoom
        bottom_c = cy + (height / 2.0) / zoom

        pattern = bg.get("pattern", "none")
        if pattern != "none":
            pat_col = bg.get("pattern_color", [150, 150, 150, 64])
            cr.set_source_rgba(pat_col[0] / 255.0, pat_col[1] / 255.0, pat_col[2] / 255.0, pat_col[3] / 255.0)
            spacing = float(bg.get("spacing", 64.0))

            if pattern in ("grid", "lines"):
                cr.set_line_width(max(0.5, 1.0 * min(1.0, zoom)))
                first_y = math.floor(top_c / spacing) * spacing
                y = first_y
                while y <= bottom_c + spacing:
                    sy = height / 2.0 + (y - cy) * zoom
                    cr.move_to(0, sy)
                    cr.line_to(width, sy)
                    y += spacing
                if pattern == "grid":
                    first_x = math.floor(left_c / spacing) * spacing
                    x = first_x
                    while x <= right_c + spacing:
                        sx = width / 2.0 + (x - cx) * zoom
                        cr.move_to(sx, 0)
                        cr.line_to(sx, height)
                        x += spacing
                cr.stroke()
            elif pattern == "dots":
                r = max(1.2, 1.8 * min(1.2, zoom))
                first_x = math.floor(left_c / spacing) * spacing
                first_y = math.floor(top_c / spacing) * spacing
                cr.new_path()
                y = first_y
                while y <= bottom_c + spacing:
                    sy = height / 2.0 + (y - cy) * zoom
                    x = first_x
                    while x <= right_c + spacing:
                        sx = width / 2.0 + (x - cx) * zoom
                        cr.new_sub_path()
                        cr.arc(sx, sy, r, 0, 2 * math.pi)
                        x += spacing
                    y += spacing
                cr.fill()
            elif pattern in ("isometric", "isometric_grid"):
                cr.set_line_width(max(0.5, 1.0 * min(1.0, zoom)))
                dx = spacing * (math.sqrt(3.0) / 2.0)
                first_x = math.floor(left_c / dx) * dx
                x = first_x
                while x <= right_c + dx:
                    sx = width / 2.0 + (x - cx) * zoom
                    cr.move_to(sx, 0)
                    cr.line_to(sx, height)
                    x += dx

                tan30 = math.tan(math.radians(30))
                b_min = top_c - right_c * tan30
                b_max = bottom_c - left_c * tan30
                first_b = math.floor(b_min / spacing) * spacing
                b = first_b
                while b <= b_max + spacing:
                    sy1 = height / 2.0 + (left_c * tan30 + b - cy) * zoom
                    sy2 = height / 2.0 + (right_c * tan30 + b - cy) * zoom
                    cr.move_to(0, sy1)
                    cr.line_to(width, sy2)
                    b += spacing

                b_min = top_c + left_c * tan30
                b_max = bottom_c + right_c * tan30
                first_b = math.floor(b_min / spacing) * spacing
                b = first_b
                while b <= b_max + spacing:
                    sy1 = height / 2.0 + (-left_c * tan30 + b - cy) * zoom
                    sy2 = height / 2.0 + (-right_c * tan30 + b - cy) * zoom
                    cr.move_to(0, sy1)
                    cr.line_to(width, sy2)
                    b += spacing
                cr.stroke()
            elif pattern in ("isometric_dots", "isometric-dots"):
                r = max(1.2, 1.8 * min(1.2, zoom))
                dy = spacing * (math.sqrt(3.0) / 2.0)
                first_row = math.floor(top_c / dy)
                last_row = math.ceil(bottom_c / dy)
                cr.new_path()
                for row_idx in range(first_row, last_row + 1):
                    y = row_idx * dy
                    sy = height / 2.0 + (y - cy) * zoom
                    row_offset = (spacing / 2.0) if (row_idx % 2 != 0) else 0.0
                    first_x = math.floor((left_c - row_offset) / spacing) * spacing + row_offset
                    x = first_x
                    while x <= right_c + spacing:
                        sx = width / 2.0 + (x - cx) * zoom
                        cr.new_sub_path()
                        cr.arc(sx, sy, r, 0, 2 * math.pi)
                        x += spacing
                cr.fill()

        # Draw items
        if self.document.get("pages"):
            items = self.document["pages"][0].get("items", [])
            self.document["items"] = items
        else:
            items = self.document.get("items", [])

        cr.save()
        cr.translate(width / 2.0, height / 2.0)
        cr.scale(zoom, zoom)
        cr.translate(-cx, -cy)

        skip_indices = getattr(self, "_transient_skip_indices", set()) if getattr(self, "_transient_skip_page", None) == 0 else set()

        # Viewport bounds in canvas coordinate space for viewport culling
        vis_min_x = left_c - 100.0
        vis_max_x = right_c + 100.0
        vis_min_y = top_c - 100.0
        vis_max_y = bottom_c + 100.0

        for idx, item in enumerate(items):
            if idx in skip_indices:
                continue
            if not isinstance(item, dict):
                continue

            # Viewport culling: skip off-screen strokes/shapes/images/text
            kind = item.get("kind") or item.get("type")
            if kind == "stroke":
                bb = self._get_or_compute_stroke_bbox(item)
                if bb and (bb[2] < vis_min_x or bb[0] > vis_max_x or bb[3] < vis_min_y or bb[1] > vis_max_y):
                    continue
            elif kind in ("image", "picture", "shape", "text"):
                r = item.get("rect")
                if not r and kind == "text":
                    pos = item.get("pos", [0, 0])
                    r = [pos[0], pos[1], float(item.get("width", 200)), 50]
                if r and (r[0] + r[2] < vis_min_x or r[0] > vis_max_x or r[1] + r[3] < vis_min_y or r[1] > vis_max_y):
                    continue

            self.draw_page_item(cr, item, self.assets, self.templates)

        if self._active_stroke:
            self.draw_stroke(cr, self._active_stroke, is_active=True)

        if self._active_shape:
            self.draw_shape(cr, self._active_shape, is_preview=True)

        if getattr(self, "_active_tape", None) is not None:
            self.draw_tape(cr, self._active_tape)

        if getattr(self, "_active_connector", None) is not None:
            self.draw_connector(cr, self._active_connector)

        if getattr(self, "_active_space_cut", None) is not None:
            sc = self._active_space_cut
            y0 = sc["start_y"]
            y1 = sc["current_y"]
            cr.save()
            cr.set_source_rgba(0.2, 0.5, 0.9, 0.85)
            cr.set_line_width(2.0 / max(0.1, self.zoom_level))
            cr.set_dash([6.0, 4.0])
            cr.move_to(-10000.0, y0)
            cr.line_to(10000.0, y0)
            cr.stroke()
            cr.set_source_rgba(0.2, 0.5, 0.9, 0.15)
            cr.rectangle(-10000.0, min(y0, y1), 20000.0, abs(y1 - y0))
            cr.fill()
            cr.set_source_rgba(0.2, 0.5, 0.9, 0.85)
            cr.move_to(-10000.0, y1)
            cr.line_to(10000.0, y1)
            cr.stroke()
            cr.restore()

        if self._sized_text_box and self._sized_text_box.get("rect"):
            tb = self._sized_text_box
            self._draw_visual_text_box(
                cr,
                tb["rect"],
                text=tb.get("text", ""),
                font_family=tb.get("font_family", "Sans"),
                point_size=tb.get("point_size", 14.0)
            )
        elif self._active_text_box and self._active_text_box.get("rect"):
            rect = self._active_text_box["rect"]
            self._draw_visual_text_box(cr, rect)

        cr.restore()

        # Selection gizmo & lasso overlay in widget coordinates
        page_screen_x, page_screen_y = self.get_page_widget_offset(0)
        if self.selected_page_idx == 0:
            sel_item = self.get_selected_item()
            if sel_item and (sel_item.get("kind") or sel_item.get("type")) in ("image", "picture", "shape", "text", "audio"):
                self._draw_selection_gizmo(cr, page_screen_x, page_screen_y, sel_item)

        self._draw_lasso_overlay(cr, page_screen_x, page_screen_y, 0)

    def _draw_minimap(self, cr, width, height):
        mx, my, mw, mh = self._get_minimap_rect()
        if mx < 0 or my < 0:
            return

        # Background card
        self._rounded_rect(cr, mx, my, mw, mh, 10.0)
        cr.set_source_rgba(0.97, 0.97, 0.98, 0.92)
        cr.fill_preserve()
        cr.set_source_rgba(0.0, 0.0, 0.0, 0.18)
        cr.set_line_width(1.0)
        cr.stroke()

        # Header title
        cr.save()
        cr.set_source_rgba(0.35, 0.35, 0.4, 0.9)
        cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
        cr.set_font_size(9.5)
        cr.move_to(mx + 10.0, my + 15.0)
        cr.show_text("Minimap")
        cr.restore()

        # Inner mapping area
        ix = mx + 8.0
        iy = my + 22.0
        iw = mw - 16.0
        ih = mh - 30.0

        # Background of inner map
        self._rounded_rect(cr, ix, iy, iw, ih, 4.0)
        cr.set_source_rgba(1.0, 1.0, 1.0, 0.95)
        cr.fill_preserve()
        cr.set_source_rgba(0.0, 0.0, 0.0, 0.08)
        cr.set_line_width(0.75)
        cr.stroke()

        cr.save()
        self._rounded_rect(cr, ix, iy, iw, ih, 4.0)
        cr.clip()

        view = self.document.setdefault("view", {})
        cx = float(view.get("cx", 0.0))
        cy = float(view.get("cy", 0.0))
        zoom = max(0.05, self.zoom_level)

        vp_w = width / zoom
        vp_h = height / zoom
        vp_x = cx - vp_w / 2.0
        vp_y = cy - vp_h / 2.0

        if self.document.get("pages"):
            items = self.document["pages"][0].get("items", [])
            self.document["items"] = items
        else:
            items = self.document.get("items", [])

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

        if min_x > max_x:
            min_x, min_y = cx - 800, cy - 600
            max_x, max_y = cx + 800, cy + 600

        bx1 = min(min_x, vp_x) - 150.0
        by1 = min(min_y, vp_y) - 150.0
        bx2 = max(max_x, vp_x + vp_w) + 150.0
        by2 = max(max_y, vp_y + vp_h) + 150.0

        bw = max(100.0, bx2 - bx1)
        bh = max(100.0, by2 - by1)
        s = min(iw / bw, ih / bh)

        map_ox = ix + (iw - bw * s) / 2.0 - bx1 * s
        map_oy = iy + (ih - bh * s) / 2.0 - by1 * s
        self._minimap_transform = (map_ox, map_oy, s, bx1, by1, bw, bh)

        # Render mini strokes/shapes using cached ImageSurface if valid
        cached = getattr(self, "_minimap_surface_cache", None)
        cache_valid = False
        if cached is not None:
            c_surf, c_ox, c_oy, c_s, c_items_len = cached
            if c_items_len == len(items) and abs(c_ox - map_ox) < 0.5 and abs(c_oy - map_oy) < 0.5 and abs(c_s - s) < 1e-4:
                cache_valid = True
                cr.set_source_surface(c_surf, ix, iy)
                cr.paint()

        if not cache_valid:
            m_surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, max(1, int(iw)), max(1, int(ih)))
            m_cr = cairo.Context(m_surf)
            # Offset m_cr so drawing coordinates relative to (ix, iy) match
            m_cr.translate(-ix, -iy)

            m_cr.save()
            m_cr.set_source_rgba(0.2, 0.2, 0.25, 0.45)
            m_cr.set_line_width(1.0)
            m_cr.new_path()
            for it in items:
                kind = it.get("kind") or it.get("type")
                if kind == "stroke":
                    samples = it.get("samples") or it.get("points") or []
                    if len(samples) >= 2:
                        step = max(1, len(samples) // 25)
                        for idx_s, pt in enumerate(samples[::step]):
                            px = pt["x"] if isinstance(pt, dict) else pt[0]
                            py = pt["y"] if isinstance(pt, dict) else pt[1]
                            mx_pt = map_ox + px * s
                            my_pt = map_oy + py * s
                            if idx_s == 0:
                                m_cr.move_to(mx_pt, my_pt)
                            else:
                                m_cr.line_to(mx_pt, my_pt)
            m_cr.stroke()

            m_cr.new_path()
            for it in items:
                kind = it.get("kind") or it.get("type")
                if kind in ("image", "picture", "shape", "text", "audio"):
                    r = it.get("rect", [0, 0, 100, 100]) if kind != "text" else [it.get("pos", [0, 0])[0], it.get("pos", [0, 0])[1], it.get("width", 150), 40]
                    rx = map_ox + r[0] * s
                    ry = map_oy + r[1] * s
                    rw = max(2.0, r[2] * s)
                    rh = max(2.0, r[3] * s)
                    m_cr.rectangle(rx, ry, rw, rh)
            m_cr.fill()
            m_cr.restore()
            m_surf.flush()

            self._minimap_surface_cache = (m_surf, map_ox, map_oy, s, len(items))
            cr.set_source_surface(m_surf, ix, iy)
            cr.paint()

        # Viewport rectangle
        vrx = map_ox + vp_x * s
        vry = map_oy + vp_y * s
        vrw = max(4.0, vp_w * s)
        vrh = max(4.0, vp_h * s)

        cr.set_source_rgba(0.21, 0.52, 0.89, 0.2)
        cr.rectangle(vrx, vry, vrw, vrh)
        cr.fill_preserve()
        cr.set_source_rgba(0.21, 0.52, 0.89, 0.85)
        cr.set_line_width(1.5)
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