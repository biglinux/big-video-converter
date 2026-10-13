"""Video editor picture mixin: crop, rotation, flips, color and effects."""

import os

from engine.crop_geometry import ASPECT_RATIOS, centered_crop
from engine.job_options import EFFECT_LEVELS, SOURCE_HDR_MODES
from engine.video_settings import SHADER_SUFFIXES, effects_graph

from ui.crop_overlay import display_to_source, displayed_size, source_to_display


class VideoEditPictureMixin:
    """Mixin applying the picture edits to the metadata and the preview."""

    def on_brightness_changed(self, scale) -> None:
        self.brightness = scale.get_value()
        self._save_file_metadata()
        if self.mpv_player:
            self.mpv_player.set_brightness(self.brightness)

    def on_contrast_changed(self, scale) -> None:
        self.contrast = scale.get_value()
        self._save_file_metadata()
        if self.mpv_player:
            self.mpv_player.set_contrast(self.contrast)

    def on_crop_value_changed(self, spinbutton) -> None:
        """Handle crop value changes - ensures preview updates immediately"""
        if self._updating_crop_spins:
            return
        if not self.mpv_player or not hasattr(self.ui, "crop_left_spin"):
            return

        # The spins name the edges as the preview shows them.
        left, right, top, bottom = display_to_source(
            (
                int(self.ui.crop_left_spin.get_value()),
                int(self.ui.crop_right_spin.get_value()),
                int(self.ui.crop_top_spin.get_value()),
                int(self.ui.crop_bottom_spin.get_value()),
            ),
            self.rotation,
            self.flip_h,
            self.flip_v,
        )

        self.crop_aspect = "free"
        if hasattr(self.ui, "crop_aspect_combo"):
            self.ui.crop_aspect_combo.set_selected(0)

        self.crop_left = left
        self.crop_right = right
        self.crop_top = top
        self.crop_bottom = bottom

        self._save_file_metadata()
        # The opposite edge's bound follows this one.
        self.update_crop_spinbuttons()

        if self.crop_edit_mode:
            self.ui.crop_overlay.set_crop_values(
                int(left), int(right), int(top), int(bottom)
            )
        else:
            # Apply crop to MPV only when not in edit mode
            self.mpv_player.set_crop(left, right, top, bottom)

    def on_crop_aspect_changed(self, combo, _pspec=None) -> None:
        if self._updating_crop_aspect:
            return
        values = tuple(ASPECT_RATIOS)
        selected = combo.get_selected()
        if selected >= len(values):
            return
        self.crop_aspect = values[selected]
        if self.crop_aspect in {"free", "original"}:
            if self.crop_aspect == "original":
                self.crop_left = self.crop_right = 0
                self.crop_top = self.crop_bottom = 0
                self._apply_crop_state()
            self._save_file_metadata()
            return
        if self.video_width <= 1 or self.video_height <= 1:
            return
        margins = centered_crop(
            self.video_width,
            self.video_height,
            self.crop_aspect,
            rotation=self.rotation,
        )
        (
            self.crop_left,
            self.crop_right,
            self.crop_top,
            self.crop_bottom,
        ) = margins
        self._apply_crop_state()
        self._save_file_metadata()

    def _apply_crop_state(self) -> None:
        self._updating_crop_spins = True
        try:
            self.update_crop_spinbuttons()
        finally:
            self._updating_crop_spins = False
        if self.crop_edit_mode:
            self.ui.crop_overlay.set_crop_values(
                int(self.crop_left),
                int(self.crop_right),
                int(self.crop_top),
                int(self.crop_bottom),
            )
        elif self.mpv_player:
            self.mpv_player.set_crop(
                self.crop_left,
                self.crop_right,
                self.crop_top,
                self.crop_bottom,
            )

    def _reapply_crop_aspect(self) -> None:
        if self.crop_aspect in {"free", "original"}:
            return
        if hasattr(self.ui, "crop_aspect_combo"):
            self.on_crop_aspect_changed(self.ui.crop_aspect_combo)

    def on_crop_edit_toggled(self, active: bool) -> None:
        """Toggle visual crop editor mode."""
        self.crop_edit_mode = active
        self.ui.crop_overlay.set_visible(active)

        _CROP_MARGIN = 20

        if active:
            if hasattr(self.ui, "overlay_controls"):
                self.ui.overlay_controls.set_visible(False)

            # Add margin around the video area to prevent accidental window resize
            self.ui.video_overlay.set_margin_start(_CROP_MARGIN)
            self.ui.video_overlay.set_margin_end(_CROP_MARGIN)
            self.ui.video_overlay.set_margin_top(_CROP_MARGIN)
            self.ui.video_overlay.set_margin_bottom(_CROP_MARGIN)

            # Enter crop edit mode: remove crop from MPV, show overlay
            self.mpv_player.clear_crop()

            # The overlay maps crop margins in displayed pixels (display_size),
            # not mpv's decoded size, which is sideways for phone recordings,
            # and shows them turned and mirrored like the preview.
            self.ui.crop_overlay.set_video_dimensions(
                self.video_width, self.video_height
            )
            self.ui.crop_overlay.set_transform(self.rotation, self.flip_h, self.flip_v)

            self.ui.crop_overlay.set_crop_values(
                int(self.crop_left),
                int(self.crop_right),
                int(self.crop_top),
                int(self.crop_bottom),
            )

            self.ui.crop_overlay.set_on_crop_changed(self._on_crop_overlay_changed)
        else:
            self.ui.video_overlay.set_margin_start(0)
            self.ui.video_overlay.set_margin_end(0)
            self.ui.video_overlay.set_margin_top(0)
            self.ui.video_overlay.set_margin_bottom(0)

            # Restore overlay controls
            if hasattr(self.ui, "overlay_controls"):
                self.ui.overlay_controls.set_visible(True)

            # Exit crop edit mode: re-apply crop to MPV
            self.ui.crop_overlay.set_on_crop_changed(None)
            self.mpv_player.set_crop(
                self.crop_left,
                self.crop_right,
                self.crop_top,
                self.crop_bottom,
            )

    def _on_crop_overlay_changed(
        self, left: int, right: int, top: int, bottom: int
    ) -> None:
        """Called when user drags crop boundaries on the overlay."""
        self.crop_aspect = "free"
        if hasattr(self.ui, "crop_aspect_combo"):
            self.ui.crop_aspect_combo.set_selected(0)
        self.crop_left = left
        self.crop_right = right
        self.crop_top = top
        self.crop_bottom = bottom
        self._save_file_metadata()
        self.update_crop_spinbuttons()

    def on_rotate(self, degrees: int) -> None:
        """Rotate video preview by given degrees (cumulative)."""
        self.rotation = (self.rotation + degrees) % 360
        self._save_file_metadata()
        if self.mpv_player:
            self.mpv_player.set_rotation(self.rotation)
        self._update_transform_state()
        self._reapply_crop_aspect()

    def on_flip(self, direction: str) -> None:
        """Toggle horizontal or vertical flip."""
        if direction == "horizontal":
            self.flip_h = not self.flip_h
        else:
            self.flip_v = not self.flip_v
        self._save_file_metadata()
        self._update_transform_state()
        self._apply_video_flip()

    def on_reset_transform(self) -> None:
        """Reset rotation and flip to default."""
        self.rotation = 0
        self.flip_h = False
        self.flip_v = False
        self._save_file_metadata()
        if self.mpv_player:
            self.mpv_player.set_rotation(0)
            self.mpv_player.set_video_flip(False, False)
        self._update_transform_state()
        self._reapply_crop_aspect()

    def on_effect_changed(self, name: str, value) -> None:
        """Noise reduction, sharpening, stabilization or the effect file."""
        if self._restoring_metadata:
            return
        if name in ("denoise", "sharpen") and value not in EFFECT_LEVELS:
            return
        setattr(self, name, value)
        self._save_file_metadata()
        self.ui.update_effects_state(
            self.denoise, self.sharpen, self.stabilize, self.effect_file
        )
        self._apply_video_effects()

    def on_source_hdr_changed(self, mode: str) -> None:
        if self._restoring_metadata or mode not in SOURCE_HDR_MODES:
            return
        self.source_hdr = mode
        self._save_file_metadata()
        self.ui.update_source_hdr(mode)
        self._apply_video_effects()

    def _apply_video_effects(self) -> None:
        """Show the effects in the preview; stabilization needs the whole
        file analyzed first, so only the conversion applies it."""
        if not self.mpv_player:
            return
        shader = (
            self.effect_file
            if os.path.splitext(self.effect_file)[1].lower() in SHADER_SUFFIXES
            else ""
        )
        self.mpv_player.set_video_effects(
            effects_graph(self.denoise, self.sharpen, self.effect_file),
            shader,
            self.source_hdr,
        )

    def _update_transform_state(self) -> None:
        if hasattr(self.ui, "rotation_row"):
            self.ui.update_transform_state(self.rotation, self.flip_h, self.flip_v)
        # The crop controls name the edges as the preview shows them.
        if hasattr(self.ui, "crop_overlay"):
            self.ui.crop_overlay.set_transform(self.rotation, self.flip_h, self.flip_v)
        self.update_crop_spinbuttons()

    def _apply_video_flip(self) -> None:
        if self.mpv_player:
            self.mpv_player.set_video_flip(self.flip_h, self.flip_v)

    def update_crop_spinbuttons(self) -> None:
        """Show the crop as displayed, each edge bounded so that at least
        two pixels of the loaded video remain between it and its opposite."""
        if not hasattr(self.ui, "crop_left_spin"):
            return
        transform = (self.rotation, self.flip_h, self.flip_v)
        width, height = displayed_size(
            self.video_width, self.video_height, self.rotation
        )
        left, right, top, bottom = source_to_display(
            (self.crop_left, self.crop_right, self.crop_top, self.crop_bottom),
            *transform,
        )
        previous = self._updating_crop_spins
        self._updating_crop_spins = True
        try:
            for spin, value, extent, opposite in (
                (self.ui.crop_left_spin, left, width, right),
                (self.ui.crop_right_spin, right, width, left),
                (self.ui.crop_top_spin, top, height, bottom),
                (self.ui.crop_bottom_spin, bottom, height, top),
            ):
                # Before a video is loaded its size is unknown.
                upper = max(0, extent - 2 - opposite) if extent > 2 else 9999
                spin.set_range(0, max(upper, value))
                spin.set_value(value)
        finally:
            self._updating_crop_spins = previous

    def on_saturation_changed(self, scale) -> None:
        self.saturation = scale.get_value()
        self._save_file_metadata()
        if self.mpv_player:
            self.mpv_player.set_saturation(self.saturation)

    def on_hue_changed(self, scale) -> None:
        self.hue = scale.get_value()
        self._save_file_metadata()
        if self.mpv_player:
            self.mpv_player.set_hue(self.hue)

    def reset_brightness(self) -> None:
        self.brightness = 0.0
        self.ui.brightness_scale.set_value(self.brightness)
        self._save_file_metadata()
        if self.mpv_player:
            self.mpv_player.set_brightness(self.brightness)
            if not self.is_playing:
                self._refresh_preview()

    def reset_contrast(self) -> None:
        self.contrast = 0.0
        self.ui.contrast_scale.set_value(self.contrast)
        self._save_file_metadata()
        if self.mpv_player:
            self.mpv_player.set_contrast(self.contrast)
            if not self.is_playing:
                self._refresh_preview()

    def reset_saturation(self) -> None:
        self.saturation = 1.0
        self.ui.saturation_scale.set_value(self.saturation)
        self._save_file_metadata()
        if self.mpv_player:
            self.mpv_player.set_saturation(self.saturation)
            if not self.is_playing:
                self._refresh_preview()

    def reset_hue(self) -> None:
        self.hue = 0.0
        self.ui.hue_scale.set_value(self.hue)
        self._save_file_metadata()
        if self.mpv_player:
            self.mpv_player.set_hue(self.hue)
            if not self.is_playing:
                self._refresh_preview()
