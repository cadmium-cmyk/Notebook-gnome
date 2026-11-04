import math


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

    @classmethod
    def _get_or_compute_stroke_bbox(cls, item):
        """Return cached [min_x, min_y, max_x, max_y] for a stroke or compute and cache it."""
        bbox = item.get("_bbox")
        if bbox is not None and len(bbox) == 4:
            return bbox
        samples = item.get("samples") or item.get("points") or []
        if not samples:
            return None
        hw = cls._get_stroke_base_width(item) * 0.65
        min_x, min_y = float("inf"), float("inf")
        max_x, max_y = float("-inf"), float("-inf")
        for s in samples:
            sx = float(s["x"]) if isinstance(s, dict) else float(s[0])
            sy = float(s["y"]) if isinstance(s, dict) else float(s[1])
            if sx - hw < min_x:
                min_x = sx - hw
            if sy - hw < min_y:
                min_y = sy - hw
            if sx + hw > max_x:
                max_x = sx + hw
            if sy + hw > max_y:
                max_y = sy + hw
        if min_x == float("inf"):
            return None
        bbox = [min_x, min_y, max_x, max_y]
        item["_bbox"] = bbox
        return bbox

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
                sb = self._get_or_compute_stroke_bbox(item)
                if sb:
                    min_x = min(min_x, sb[0])
                    min_y = min(min_y, sb[1])
                    max_x = max(max_x, sb[2])
                    max_y = max(max_y, sb[3])
            elif kind == "text":
                pos = item.get("pos", [0.0, 0.0])
                tw = float(item.get("width", 240.0))
                th = float(item.get("point_size", 14.0)) * 2.2
                min_x = min(min_x, pos[0])
                min_y = min(min_y, pos[1])
                max_x = max(max_x, pos[0] + tw)
                max_y = max(max_y, pos[1] + th)
            elif kind in ("image", "picture", "shape", "text", "audio"):
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
        old_items = self._clone_items_list(items)
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
            elif kind == "shape":
                item["rgba"] = [rgba[0], rgba[1], rgba[2], 255]
                changed = True

        if changed:
            self._record_undo_step(self.lasso_page_idx, old_items, self._clone_items_list(items), clone=False)
            self.invalidate_page(self.lasso_page_idx, clear_bg=False)
        return changed

    def resize_lasso_stroke_width(self, new_width):
        if not self.has_lasso_selection() or not self.document:
            return False
        pages = self.document.get("pages", [])
        if not (0 <= self.lasso_page_idx < len(pages)):
            return False

        page = pages[self.lasso_page_idx]
        items = page.get("items", [])
        old_items = self._clone_items_list(items)
        changed = False

        for idx in self.lasso_item_indices:
            if 0 <= idx < len(items):
                item = items[idx]
                k = item.get("kind") or item.get("type")
                if k == "stroke":
                    if isinstance(item.get("config"), dict):
                        item["config"]["base_width"] = round(float(new_width), 2)
                    if "stroke_width" in item:
                        item["stroke_width"] = round(float(new_width), 2)
                    item.pop("_bbox", None)
                    item.pop("_render_cache", None)
                    changed = True
                elif k == "shape":
                    item["stroke_width"] = round(float(new_width), 2)
                    changed = True

        if changed:
            self.lasso_bbox = self._compute_items_bbox(self.lasso_page_idx, self.lasso_item_indices)
            self._record_undo_step(self.lasso_page_idx, old_items, self._clone_items_list(items), clone=False)
            self.invalidate_page(self.lasso_page_idx, clear_bg=False)
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
        sel_item = self.get_selected_item()
        kind = (sel_item.get("kind") or sel_item.get("type")) if sel_item else None
        if self.on_selection_changed_cb:
            self.on_selection_changed_cb(sel_item is not None, kind or "image")
        self.queue_draw()

    def add_image_to_page(self, page_idx, asset_name, surface):
        if not self.document:
            return
        pages = self.document.get("pages", [])
        if not (0 <= page_idx < len(pages)):
            return

        page = pages[page_idx]
        old_items = self._clone_items_list(page.get("items", []))
        iw = float(surface.get_width())
        ih = float(surface.get_height())

        if getattr(self, "is_infinite_canvas", lambda: False)():
            view = self.document.get("view", {})
            cx = float(view.get("cx", 0.0))
            cy = float(view.get("cy", 0.0))
            w = round(iw, 2)
            h = round(ih, 2)
            x = round(cx - w / 2.0, 2)
            y = round(cy - h / 2.0, 2)
        else:
            pw, ph = self.get_page_size(page_idx)
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
        self._record_undo_step(page_idx, old_items, self._clone_items_list(page["items"]), clone=False)
        self.invalidate_page(page_idx, clear_bg=False)
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
        old_items = self._clone_items_list(page.get("items", []))

        x, y, w, h = item.get("rect", [0.0, 0.0, 100.0, 100.0])
        cx = x + w / 2.0
        cy = y + h / 2.0
        native_ratio = float(surf.get_width()) / float(surf.get_height())
        new_w = w
        new_h = round(new_w / native_ratio, 2)
        item["rect"] = [round(cx - new_w / 2.0, 2), round(cy - new_h / 2.0, 2), round(new_w, 2), new_h]

        self._record_undo_step(self.selected_page_idx, old_items, self._clone_items_list(page["items"]), clone=False)
        self.invalidate_page(self.selected_page_idx, clear_bg=False)

    def rotate_selected_image(self, delta_radians):
        item = self.get_selected_item()
        if not item:
            return
        pages = self.document.get("pages", [])
        page = pages[self.selected_page_idx]
        old_items = self._clone_items_list(page.get("items", []))
        item["angle"] = round(float(item.get("angle", 0.0)) + delta_radians, 4)
        self._record_undo_step(self.selected_page_idx, old_items, self._clone_items_list(page["items"]), clone=False)
        self.invalidate_page(self.selected_page_idx, clear_bg=False)

    def flip_selected_image(self, horizontal=True):
        item = self.get_selected_item()
        if not item:
            return
        pages = self.document.get("pages", [])
        page = pages[self.selected_page_idx]
        old_items = self._clone_items_list(page.get("items", []))
        if horizontal:
            item["flip_h"] = not bool(item.get("flip_h", False))
        else:
            item["flip_v"] = not bool(item.get("flip_v", False))
        self._record_undo_step(self.selected_page_idx, old_items, self._clone_items_list(page["items"]), clone=False)
        self.invalidate_page(self.selected_page_idx, clear_bg=False)

    def reorder_selected_image(self, to_front=True):
        item = self.get_selected_item()
        if not item:
            return
        pages = self.document.get("pages", [])
        page = pages[self.selected_page_idx]
        items = page.get("items", [])
        if len(items) <= 1:
            return
        old_items = self._clone_items_list(items)
        popped = items.pop(self.selected_item_idx)
        if to_front:
            items.append(popped)
            self.selected_item_idx = len(items) - 1
        else:
            items.insert(0, popped)
            self.selected_item_idx = 0
        self._record_undo_step(self.selected_page_idx, old_items, self._clone_items_list(items), clone=False)
        self.invalidate_page(self.selected_page_idx, clear_bg=False)

    def delete_selected_item(self):
        if self.has_lasso_selection() and self.document:
            pages = self.document.get("pages", [])
            if 0 <= self.lasso_page_idx < len(pages):
                page = pages[self.lasso_page_idx]
                old_items = list(page.get("items", []))
                remove_set = set(self.lasso_item_indices)
                page["items"] = [it for i, it in enumerate(page.get("items", [])) if i not in remove_set]
                p_idx = self.lasso_page_idx
                self.clear_selection()
                self._record_undo_step(p_idx, old_items, list(page["items"]), clone=False)
                self.invalidate_page(p_idx, clear_bg=False)
            return

        item = self.get_selected_item()
        if not item:
            return
        pages = self.document.get("pages", [])
        page = pages[self.selected_page_idx]
        old_items = list(page.get("items", []))
        page["items"].pop(self.selected_item_idx)
        p_idx = self.selected_page_idx
        self.clear_selection()
        self._record_undo_step(p_idx, old_items, list(page["items"]), clone=False)
        self.invalidate_page(p_idx, clear_bg=False)

    def copy_selection(self):
        import copy
        import page_sidebar
        sel_items = []
        p_idx = None
        if self.has_lasso_selection() and self.document:
            pages = self.document.get("pages", [])
            if 0 <= self.lasso_page_idx < len(pages):
                p_idx = self.lasso_page_idx
                page = pages[p_idx]
                items = page.get("items", [])
                for idx in sorted(self.lasso_item_indices):
                    if 0 <= idx < len(items):
                        sel_items.append(copy.deepcopy(items[idx]))
        else:
            item = self.get_selected_item()
            if item and self.selected_page_idx is not None:
                p_idx = self.selected_page_idx
                sel_items.append(copy.deepcopy(item))

        if not sel_items:
            return False

        raw_assets = self.document.get("_raw_assets", {}) if self.document else {}
        assets_payload = {}
        for it in sel_items:
            ref = it.get("asset") or it.get("src")
            if ref and ref in raw_assets:
                assets_payload[ref] = raw_assets[ref]

        page_sidebar._APP_ITEMS_CLIPBOARD = {
            "type": "xnote_items",
            "items": sel_items,
            "assets": assets_payload
        }
        return True

    def cut_selection(self):
        if self.copy_selection():
            self.delete_selected_item()
            return True
        return False

    def paste_selection(self):
        import copy
        import page_sidebar
        payload = page_sidebar._APP_ITEMS_CLIPBOARD
        if not payload or payload.get("type") != "xnote_items" or not self.document:
            return False

        p_idx = self.current_page
        pages = self.document.get("pages", [])
        if not (0 <= p_idx < len(pages)):
            return False

        items_to_paste = copy.deepcopy(payload["items"])
        assets_dict = payload.get("assets", {})

        doc_raw_assets = self.document.setdefault("_raw_assets", {})
        for k, v in assets_dict.items():
            doc_raw_assets[k] = v

        page = pages[p_idx]
        old_items = list(page.get("items", []))

        for it in items_to_paste:
            if "rect" in it and len(it["rect"]) >= 4:
                it["rect"] = [it["rect"][0] + 20.0, it["rect"][1] + 20.0, it["rect"][2], it["rect"][3]]
            elif "samples" in it:
                for s in it["samples"]:
                    s[0] += 20.0
                    s[1] += 20.0
            page.setdefault("items", []).append(it)

        self._record_undo_step(p_idx, old_items, list(page["items"]), clone=False)
        self.invalidate_page(p_idx, clear_bg=False)
        new_idx = len(page["items"]) - 1
        self.select_item(p_idx, new_idx)
        self.queue_draw()
        return True

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
        if not item or (item.get("kind") or item.get("type")) not in ("image", "picture", "shape", "text", "audio"):
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
        tol = 10.0 / max(0.25, getattr(self, "zoom_level", 1.0))
        for idx in range(len(items) - 1, -1, -1):
            item = items[idx]
            kind = item.get("kind") or item.get("type")
            if kind in ("image", "picture", "shape", "text", "audio"):
                if "rect" not in item:
                    pos = item.get("pos", [0.0, 0.0])
                    item["rect"] = [float(pos[0]), float(pos[1]), float(item.get("width", 200.0)), float(item.get("height", 50.0))]
                rect = item.get("rect", [0.0, 0.0, 100.0, 100.0])
                hw, hh = rect[2] / 2.0, rect[3] / 2.0
                lx, ly = self._page_to_item_local(px, py, item)
                if -hw - tol <= lx <= hw + tol and -hh - tol <= ly <= hh + tol:
                    return idx
        return None