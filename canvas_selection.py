import math
import copy


class CanvasSelectionMixin:
    """Mixin class for XNoteCanvas handling selection, lasso polygons, hit testing, and item transforms."""

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

    @staticmethod
    def _get_stroke_base_width(item):
        cfg = item.get("config")
        if isinstance(cfg, dict) and "base_width" in cfg:
            return float(cfg["base_width"])
        return float(item.get("stroke_width", item.get("width", 3.2)))

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
                hw = self._get_stroke_base_width(item) / 2.0
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
                cfg = item.get("config")
                if isinstance(cfg, dict):
                    old_rgba = cfg.get("rgba", [0, 0, 0, 255])
                    alpha = old_rgba[3] if len(old_rgba) >= 4 else 255
                    cfg["rgba"] = [rgba[0], rgba[1], rgba[2], alpha]
                if "stroke_rgba" in item:
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
                    if isinstance(item.get("config"), dict):
                        item["config"]["base_width"] = round(float(new_width), 2)
                    if "stroke_width" in item:
                        item["stroke_width"] = round(float(new_width), 2)
                    changed = True

        if changed:
            self.lasso_bbox = self._compute_items_bbox(self.lasso_page_idx, self.lasso_item_indices)
            self._record_undo_step(self.lasso_page_idx, old_items, items)
            self.invalidate_page(self.lasso_page_idx)
        return changed

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
