"""Visual crop overlay widget for the video editor.

Draws semi-transparent dark regions over cropped areas and allows
the user to drag edges/corners to adjust crop boundaries.
"""

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gtk

# Edge/corner hit zone size in pixels
_HANDLE_SIZE = 12
_EDGE_ZONE = 8

# Drag targets
_NONE = 0
_LEFT = 1
_RIGHT = 2
_TOP = 3
_BOTTOM = 4
_TOP_LEFT = 5
_TOP_RIGHT = 6
_BOTTOM_LEFT = 7
_BOTTOM_RIGHT = 8
_MOVE = 9


class CropOverlay(Gtk.DrawingArea):
    """Transparent overlay that visualizes and allows interactive crop editing."""

    def __init__(self) -> None:
        super().__init__()
        self.set_can_target(True)
        self.set_hexpand(True)
        self.set_vexpand(True)

        # Crop values in video pixels
        self._crop_left = 0
        self._crop_right = 0
        self._crop_top = 0
        self._crop_bottom = 0

        # Video dimensions (set externally)
        self._video_w = 0
        self._video_h = 0

        # Drag state
        self._drag_target = _NONE
        self._drag_start_crops = (0, 0, 0, 0)

        # Callback for when crop values change via drag
        self._on_crop_changed = None

        # Drawing function
        self.set_draw_func(self._draw)

        # Gesture: drag for moving edges/corners
        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self._on_drag_begin)
        drag.connect("drag-update", self._on_drag_update)
        drag.connect("drag-end", self._on_drag_end)
        self.add_controller(drag)

        # Motion controller for cursor changes
        motion = Gtk.EventControllerMotion()
        motion.connect("motion", self._on_motion)
        motion.connect("leave", self._on_leave)
        self.add_controller(motion)

    def set_crop_values(self, left: int, right: int, top: int, bottom: int) -> None:
        """Update crop values (video pixels) and redraw."""
        self._crop_left = left
        self._crop_right = right
        self._crop_top = top
        self._crop_bottom = bottom
        self.queue_draw()

    def set_video_dimensions(self, width: int, height: int) -> None:
        """Set the original video dimensions for coordinate mapping."""
        self._video_w = width
        self._video_h = height
        self.queue_draw()

    def set_on_crop_changed(self, callback) -> None:
        """Set callback: callback(left, right, top, bottom) in video pixels."""
        self._on_crop_changed = callback

    # --- Coordinate mapping ---

    def _get_video_rect(self) -> tuple[float, float, float, float]:
        """Return (x, y, w, h) of the video area within this widget."""
        widget_w = self.get_width()
        widget_h = self.get_height()
        if self._video_w <= 0 or self._video_h <= 0 or widget_w <= 0 or widget_h <= 0:
            return (0, 0, widget_w, widget_h)

        video_aspect = self._video_w / self._video_h
        widget_aspect = widget_w / widget_h

        if video_aspect > widget_aspect:
            disp_w = widget_w
            disp_h = widget_w / video_aspect
            x = 0
            y = (widget_h - disp_h) / 2
        else:
            disp_h = widget_h
            disp_w = widget_h * video_aspect
            x = (widget_w - disp_w) / 2
            y = 0

        return (x, y, disp_w, disp_h)

    def _video_to_widget(self, vx: float, vy: float) -> tuple[float, float]:
        """Convert video pixel coordinates to widget coordinates."""
        rx, ry, rw, rh = self._get_video_rect()
        if self._video_w <= 0 or self._video_h <= 0:
            return (0, 0)
        wx = rx + (vx / self._video_w) * rw
        wy = ry + (vy / self._video_h) * rh
        return (wx, wy)

    def _widget_to_video(self, wx: float, wy: float) -> tuple[float, float]:
        """Convert widget coordinates to video pixel coordinates."""
        rx, ry, rw, rh = self._get_video_rect()
        if rw <= 0 or rh <= 0:
            return (0, 0)
        vx = ((wx - rx) / rw) * self._video_w
        vy = ((wy - ry) / rh) * self._video_h
        return (vx, vy)

    # --- Drawing ---

    def _draw(self, area, cr, width, height) -> None:
        """Draw the crop overlay."""
        if self._video_w <= 0 or self._video_h <= 0:
            return

        vr_x, vr_y, vr_w, vr_h = self._get_video_rect()

        # Crop boundary positions in widget coords
        left_x, top_y = self._video_to_widget(self._crop_left, self._crop_top)
        right_x, bottom_y = self._video_to_widget(
            self._video_w - self._crop_right,
            self._video_h - self._crop_bottom,
        )

        # Clamp to video rect
        left_x = max(left_x, vr_x)
        top_y = max(top_y, vr_y)
        right_x = min(right_x, vr_x + vr_w)
        bottom_y = min(bottom_y, vr_y + vr_h)

        has_crop = (
            self._crop_left > 0
            or self._crop_right > 0
            or self._crop_top > 0
            or self._crop_bottom > 0
        )

        # Draw semi-transparent dark overlay on cropped regions
        if has_crop:
            cr.set_source_rgba(0, 0, 0, 0.55)

            # Top strip
            if top_y > vr_y:
                cr.rectangle(vr_x, vr_y, vr_w, top_y - vr_y)
                cr.fill()

            # Bottom strip
            if bottom_y < vr_y + vr_h:
                cr.rectangle(vr_x, bottom_y, vr_w, (vr_y + vr_h) - bottom_y)
                cr.fill()

            # Left strip (between top and bottom)
            if left_x > vr_x:
                cr.rectangle(vr_x, top_y, left_x - vr_x, bottom_y - top_y)
                cr.fill()

            # Right strip (between top and bottom)
            if right_x < vr_x + vr_w:
                cr.rectangle(right_x, top_y, (vr_x + vr_w) - right_x, bottom_y - top_y)
                cr.fill()

        # Draw crop boundary lines (blue)
        cr.set_source_rgba(0.2, 0.6, 1.0, 0.9)
        cr.set_line_width(1.5)
        cr.rectangle(left_x, top_y, right_x - left_x, bottom_y - top_y)
        cr.stroke()

        # Draw rule-of-thirds lines (dashed, lighter blue)
        cr.set_source_rgba(0.2, 0.6, 1.0, 0.35)
        cr.set_dash([4, 4])
        crop_w = right_x - left_x
        crop_h = bottom_y - top_y
        for i in range(1, 3):
            # Vertical thirds
            x = left_x + crop_w * i / 3
            cr.move_to(x, top_y)
            cr.line_to(x, bottom_y)
            # Horizontal thirds
            y = top_y + crop_h * i / 3
            cr.move_to(left_x, y)
            cr.line_to(right_x, y)
        cr.stroke()
        cr.set_dash([])

        # Draw corner handles
        cr.set_source_rgba(1, 1, 1, 0.95)
        cr.set_line_width(2.5)
        hl = 16  # Handle line length

        corners = [
            (left_x, top_y, 1, 1),
            (right_x, top_y, -1, 1),
            (left_x, bottom_y, 1, -1),
            (right_x, bottom_y, -1, -1),
        ]
        for cx, cy, dx, dy in corners:
            cr.move_to(cx, cy)
            cr.line_to(cx + hl * dx, cy)
            cr.move_to(cx, cy)
            cr.line_to(cx, cy + hl * dy)
        cr.stroke()

    # --- Hit detection ---

    def _hit_test(self, wx: float, wy: float) -> int:
        """Determine which crop edge/corner is under the pointer."""
        if self._video_w <= 0 or self._video_h <= 0:
            return _NONE

        left_x, top_y = self._video_to_widget(self._crop_left, self._crop_top)
        right_x, bottom_y = self._video_to_widget(
            self._video_w - self._crop_right,
            self._video_h - self._crop_bottom,
        )

        near_left = abs(wx - left_x) < _HANDLE_SIZE
        near_right = abs(wx - right_x) < _HANDLE_SIZE
        near_top = abs(wy - top_y) < _HANDLE_SIZE
        near_bottom = abs(wy - bottom_y) < _HANDLE_SIZE

        # Corners first (higher priority)
        if near_left and near_top:
            return _TOP_LEFT
        if near_right and near_top:
            return _TOP_RIGHT
        if near_left and near_bottom:
            return _BOTTOM_LEFT
        if near_right and near_bottom:
            return _BOTTOM_RIGHT

        # Edges
        if near_left and top_y <= wy <= bottom_y:
            return _LEFT
        if near_right and top_y <= wy <= bottom_y:
            return _RIGHT
        if near_top and left_x <= wx <= right_x:
            return _TOP
        if near_bottom and left_x <= wx <= right_x:
            return _BOTTOM

        # Inside crop area → move
        if left_x <= wx <= right_x and top_y <= wy <= bottom_y:
            return _MOVE

        return _NONE

    def _get_cursor_name(self, target: int) -> str:
        """Return CSS cursor name for a drag target."""
        cursors = {
            _LEFT: "w-resize",
            _RIGHT: "e-resize",
            _TOP: "n-resize",
            _BOTTOM: "s-resize",
            _TOP_LEFT: "nw-resize",
            _TOP_RIGHT: "ne-resize",
            _BOTTOM_LEFT: "sw-resize",
            _BOTTOM_RIGHT: "se-resize",
            _MOVE: "grab",
        }
        return cursors.get(target, "default")

    # --- Gesture handlers ---

    def _on_motion(self, controller, x, y) -> None:
        """Update cursor based on what's under the pointer."""
        target = self._hit_test(x, y)
        cursor_name = self._get_cursor_name(target)
        cursor = Gdk.Cursor.new_from_name(cursor_name)
        self.set_cursor(cursor)

    def _on_leave(self, controller) -> None:
        self.set_cursor(None)

    def _on_drag_begin(self, gesture, start_x, start_y) -> None:
        """Start dragging a crop edge/corner or moving the crop area."""
        self._drag_target = self._hit_test(start_x, start_y)
        if self._drag_target == _NONE:
            gesture.set_state(Gtk.EventSequenceState.DENIED)
            return
        self._drag_start_crops = (
            self._crop_left,
            self._crop_right,
            self._crop_top,
            self._crop_bottom,
        )
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)

        if self._drag_target == _MOVE:
            self.set_cursor(Gdk.Cursor.new_from_name("grabbing"))

    def _on_drag_update(self, gesture, offset_x, offset_y) -> None:
        """Update crop values during drag."""
        if self._drag_target == _NONE:
            return

        # Convert pixel offset to video pixel delta
        _, _, vr_w, vr_h = self._get_video_rect()
        if vr_w <= 0 or vr_h <= 0:
            return

        dx_video = (offset_x / vr_w) * self._video_w
        dy_video = (offset_y / vr_h) * self._video_h

        sl, sr, st, sb = self._drag_start_crops
        new_l, new_r, new_t, new_b = sl, sr, st, sb

        target = self._drag_target

        if target == _MOVE:
            # Move entire crop region
            dx = int(dx_video)
            dy = int(dy_video)

            # Clamp horizontal movement
            new_l = sl + dx
            new_r = sr - dx
            if new_l < 0:
                new_l = 0
                new_r = sr + sl
            if new_r < 0:
                new_r = 0
                new_l = sl + sr

            # Clamp vertical movement
            new_t = st + dy
            new_b = sb - dy
            if new_t < 0:
                new_t = 0
                new_b = sb + st
            if new_b < 0:
                new_b = 0
                new_t = st + sb
        else:
            # Horizontal adjustments
            if target in (_LEFT, _TOP_LEFT, _BOTTOM_LEFT):
                new_l = max(0, int(sl + dx_video))
                new_l = min(new_l, self._video_w - sr - 2)
            if target in (_RIGHT, _TOP_RIGHT, _BOTTOM_RIGHT):
                new_r = max(0, int(sr - dx_video))
                new_r = min(new_r, self._video_w - new_l - 2)

            # Vertical adjustments
            if target in (_TOP, _TOP_LEFT, _TOP_RIGHT):
                new_t = max(0, int(st + dy_video))
                new_t = min(new_t, self._video_h - sb - 2)
            if target in (_BOTTOM, _BOTTOM_LEFT, _BOTTOM_RIGHT):
                new_b = max(0, int(sb - dy_video))
                new_b = min(new_b, self._video_h - new_t - 2)

        self._crop_left = new_l
        self._crop_right = new_r
        self._crop_top = new_t
        self._crop_bottom = new_b

        self.queue_draw()

        if self._on_crop_changed:
            self._on_crop_changed(new_l, new_r, new_t, new_b)

    def _on_drag_end(self, gesture, offset_x, offset_y) -> None:
        """Finish dragging."""
        self._drag_target = _NONE
        self.set_cursor(None)
