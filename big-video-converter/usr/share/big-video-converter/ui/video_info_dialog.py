"""The details dialog the queue opens for one video: container, streams, tags."""

import gettext
import logging
import os
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, GLib, Gtk
from utils.file_info import get_video_file_info
from utils.signal_connections import SignalConnections

from ui.progress_page import format_size, launch_file

logger = logging.getLogger(__name__)

_ = gettext.gettext


class VideoInfoDialog:
    """Dialog to display detailed video file information"""

    def __init__(self, parent_window, file_path):
        self.parent_window = parent_window
        self.file_path = file_path

        self.dialog = Adw.Window()
        self.dialog.set_default_size(780, 600)
        self.dialog.set_modal(True)
        self.dialog.set_transient_for(parent_window)
        self._connections = SignalConnections(self.dialog, "close-request")

        # Main content box
        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        header_bar = Adw.HeaderBar()
        title_label = Gtk.Label(label=_("File Information"))
        title_label.add_css_class("title")
        header_bar.set_title_widget(title_label)

        content_box.append(header_bar)

        # Create main box for content with proper structure for scrolling
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        main_box.set_hexpand(True)  # Ensure box takes full width
        main_box.set_vexpand(True)  # Ensure box takes full height

        # Create a scrolled window that extends to the edges of the main window
        # No margins on scrolled window itself
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_vexpand(True)
        scrolled.set_hexpand(True)
        scrolled.set_margin_start(0)
        scrolled.set_margin_end(0)
        scrolled.set_margin_top(0)
        scrolled.set_margin_bottom(0)

        # Use a content container inside the scrolled window to apply proper margins
        content_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        content_container.set_margin_start(24)
        content_container.set_margin_end(24)  # Leave space for scrollbar
        content_container.set_margin_top(24)
        content_container.set_margin_bottom(24)
        content_container.set_spacing(24)
        content_container.set_hexpand(True)

        # Use Adw.Clamp inside the content container
        clamp = Adw.Clamp()
        clamp.set_maximum_size(800)
        clamp.set_tightening_threshold(600)
        clamp.set_hexpand(True)

        self.info_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24)
        self.info_box.set_hexpand(True)

        clamp.set_child(self.info_box)
        content_container.append(clamp)
        scrolled.set_child(content_container)
        main_box.append(scrolled)

        self.loading_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.loading_box.set_margin_top(48)
        self.loading_box.set_margin_bottom(48)
        self.loading_box.set_valign(Gtk.Align.CENTER)
        self.loading_box.set_halign(Gtk.Align.CENTER)

        self.spinner = Gtk.Spinner()
        self.spinner.set_size_request(32, 32)
        self.spinner.start()
        self.loading_box.append(self.spinner)

        self.loading_label = Gtk.Label(label=_("Analyzing video file..."))
        self.loading_box.append(self.loading_label)

        self.info_box.append(self.loading_box)

        content_box.append(main_box)

        self.dialog.set_content(content_box)

    def show(self) -> None:
        """Show the dialog and start loading file information"""
        self.dialog.present()

        GLib.idle_add(self._load_file_info)

    def _load_file_info(self):
        """Load file information using ffprobe"""
        if not self.dialog.get_visible():
            return False
        try:
            # Get file info in background thread to avoid blocking UI
            info_thread = threading.Thread(target=self._get_file_info_thread)
            info_thread.daemon = True
            info_thread.start()
            return False
        except (GLib.Error, OSError) as e:
            self._show_error(str(e))
            return False

    def _get_file_info_thread(self):
        """Background thread to get file information"""
        try:
            info = get_video_file_info(self.file_path)
            GLib.idle_add(self._update_ui_with_info, info)
        except Exception as e:  # anything else would leave the spinner forever
            logger.exception("Error reading file information")
            GLib.idle_add(self._show_error, str(e))

    def _update_ui_with_info(self, info):
        """Update the UI with the file information"""
        if not self.dialog.get_visible():
            return
        self.info_box.remove(self.loading_box)

        if not info:
            self._show_error(_("Could not retrieve file information."))
            return

        self._add_general_info(info)

        if "streams" in info:
            # Group streams by type
            video_streams = [
                s for s in info["streams"] if s.get("codec_type") == "video"
            ]
            audio_streams = [
                s for s in info["streams"] if s.get("codec_type") == "audio"
            ]
            subtitle_streams = [
                s for s in info["streams"] if s.get("codec_type") == "subtitle"
            ]

            if video_streams:
                self._add_stream_group(_("Video Streams"), "video", video_streams)

            if audio_streams:
                self._add_stream_group(_("Audio Streams"), "audio", audio_streams)

            if subtitle_streams:
                self._add_stream_group(_("Subtitles"), "subtitle", subtitle_streams)

        if "format" in info:
            self._add_format_info(info["format"])

    def _add_general_info(self, info):
        """Add general file information"""
        group = Adw.PreferencesGroup(title=_("General Information"))

        # File name - show the file name first, "File Name" as subtitle
        file_name = os.path.basename(self.file_path)
        file_name_row = Adw.ActionRow(title=file_name)
        file_name_row.set_subtitle(_("File Name"))

        file_name_row.add_suffix(self._make_copy_button(_("Copy file name"), file_name))
        group.add(file_name_row)

        # File path (location) - show the directory first, "Location" as subtitle
        file_dir = os.path.dirname(self.file_path)
        file_path_row = Adw.ActionRow(title=file_dir)
        file_path_row.set_subtitle(_("Location"))

        open_button = Gtk.Button.new_from_icon_name("folder-open-symbolic")
        open_button.add_css_class("flat")
        open_button.set_tooltip_text(_("Open containing folder"))
        self._connections.connect(
            open_button,
            "clicked",
            lambda button: launch_file(None, button, self.file_path, containing=True),
        )
        file_path_row.add_suffix(open_button)
        group.add(file_path_row)

        # File size - show the size value first, "File Size" as subtitle
        if "format" in info and "size" in info["format"]:
            size_bytes = int(info["format"]["size"])
            size_str = format_size(size_bytes)
            size_row = Adw.ActionRow(title=size_str)
            size_row.set_subtitle(_("File Size"))

            size_row.add_suffix(self._make_copy_button(_("Copy file size"), size_str))

            group.add(size_row)

        # Duration - show the duration value first, "Duration" as subtitle
        if "format" in info and "duration" in info["format"]:
            duration_secs = float(info["format"]["duration"])
            hours = int(duration_secs // 3600)
            minutes = int((duration_secs % 3600) // 60)
            seconds = duration_secs % 60
            duration_time = f"{hours:02d}:{minutes:02d}:{seconds:06.3f}"

            duration_row = Adw.ActionRow(title=duration_time)
            duration_row.set_subtitle(_("Duration"))

            duration_row.add_suffix(
                self._make_copy_button(_("Copy duration"), duration_time)
            )

            group.add(duration_row)

        # Format - show format name first, "Format" as subtitle
        if "format_long_name" in info["format"]:
            format_name = info["format"]["format_long_name"]
            format_row = Adw.ActionRow(title=format_name)
            format_row.set_subtitle(_("Format"))

            format_row.add_suffix(self._make_copy_button(_("Copy format"), format_name))

            group.add(format_row)

        # Bitrate - show bitrate value first, "Bitrate" as subtitle
        if "format" in info and "bit_rate" in info["format"]:
            bit_rate = int(info["format"]["bit_rate"]) / 1000
            bitrate_value = f"{bit_rate:.2f} kbps"
            bitrate_row = Adw.ActionRow(title=bitrate_value)
            bitrate_row.set_subtitle(_("Bitrate"))

            bitrate_row.add_suffix(
                self._make_copy_button(_("Copy bitrate"), bitrate_value)
            )

            group.add(bitrate_row)

        self.info_box.append(group)

    def _copy_to_clipboard(self, text):
        """Copy text to clipboard"""
        clipboard = Gdk.Display.get_default().get_clipboard()
        clipboard.set(text)

    def _make_copy_button(self, tooltip, value):
        """Create a flat copy button that copies value to clipboard on click."""
        btn = Gtk.Button.new_from_icon_name("edit-copy-symbolic")
        btn.add_css_class("flat")
        btn.set_tooltip_text(tooltip)
        self._connections.connect(
            btn, "clicked", lambda b, v=value: self._copy_to_clipboard(v)
        )
        return btn

    def _add_stream_group(self, title, kind, streams):
        """Streams of one kind: "video" shown directly, others in expanders."""
        group = Adw.PreferencesGroup(title=title)

        if kind == "video":
            for idx, stream in enumerate(streams):
                if "codec_name" in stream:
                    codec_name = stream["codec_name"]
                    if "profile" in stream:
                        codec_name += f" ({stream['profile']})"
                    codec_row = Adw.ActionRow(title=codec_name)
                    codec_row.set_subtitle(_("Codec"))

                    codec_row.add_suffix(
                        self._make_copy_button(_("Copy codec"), codec_name)
                    )

                    group.add(codec_row)

                # Resolution
                if "width" in stream and "height" in stream:
                    res_value = f"{stream['width']}×{stream['height']}"
                    res_row = Adw.ActionRow(title=res_value)
                    res_row.set_subtitle(_("Resolution"))

                    if stream["height"] in [480, 720, 1080, 2160, 4320]:
                        resolution_labels = {
                            480: "SD (480p)",
                            720: "HD (720p)",
                            1080: "Full HD (1080p)",
                            2160: "4K UHD",
                            4320: "8K UHD",
                        }
                        res_label = Gtk.Label(label=resolution_labels[stream["height"]])
                        res_label.add_css_class("caption")
                        res_label.add_css_class("accent")
                        res_row.add_suffix(res_label)

                    res_row.add_suffix(
                        self._make_copy_button(_("Copy resolution"), res_value)
                    )

                    group.add(res_row)

                # Frame rate
                if "r_frame_rate" in stream:
                    try:
                        num, den = map(int, stream["r_frame_rate"].split("/"))
                        fps = num / den if den != 0 else 0
                        fps_value = f"{fps:.3f} fps"
                        fps_row = Adw.ActionRow(title=fps_value)
                        fps_row.set_subtitle(_("Frame Rate"))

                        fps_row.add_suffix(
                            self._make_copy_button(_("Copy frame rate"), fps_value)
                        )

                        group.add(fps_row)
                    except (ValueError, ZeroDivisionError):
                        pass

                # Pixel format
                if "pix_fmt" in stream:
                    pix_fmt = stream["pix_fmt"]
                    pix_row = Adw.ActionRow(title=pix_fmt)
                    pix_row.set_subtitle(_("Pixel Format"))

                    pix_row.add_suffix(
                        self._make_copy_button(_("Copy pixel format"), pix_fmt)
                    )

                    group.add(pix_row)

                # Bit rate if present
                if "bit_rate" in stream:
                    bit_rate = int(stream["bit_rate"]) / 1000
                    bitrate_value = f"{bit_rate:.2f} kbps"
                    bitrate_row = Adw.ActionRow(title=bitrate_value)
                    bitrate_row.set_subtitle(_("Bitrate"))

                    bitrate_row.add_suffix(
                        self._make_copy_button(_("Copy bitrate"), bitrate_value)
                    )

                    group.add(bitrate_row)

                if "tags" in stream and "language" in stream["tags"]:
                    lang_code = stream["tags"]["language"].upper()
                    lang_row = Adw.ActionRow(title=lang_code)
                    lang_row.set_subtitle(_("Language"))

                    lang_row.add_suffix(
                        self._make_copy_button(_("Copy language code"), lang_code)
                    )

                    group.add(lang_row)

                # Add a separator between multiple video streams if needed
                if idx < len(streams) - 1:
                    separator = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
                    separator.set_margin_top(8)
                    separator.set_margin_bottom(8)
                    group.add(separator)
        else:
            # For audio and subtitle streams, keep using expanders
            for idx, stream in enumerate(streams):
                stream_title = (
                    _("Audio track {number}")
                    if kind == "audio"
                    else _("Subtitle {number}")
                ).format(number=idx + 1)
                stream_icon = None

                if stream.get("codec_type") == "video":
                    stream_icon = "video-x-generic-symbolic"
                elif stream.get("codec_type") == "audio":
                    stream_icon = "audio-x-generic-symbolic"
                elif stream.get("codec_type") == "subtitle":
                    stream_icon = "text-x-generic-symbolic"

                if "tags" in stream:
                    if "title" in stream["tags"]:
                        stream_title += f" - {stream['tags']['title']}"
                    elif "language" in stream["tags"]:
                        lang = stream["tags"]["language"]
                        stream_title += f" - {lang.upper()}"

                expander = Adw.ExpanderRow(title=stream_title)

                if stream_icon:
                    icon = Gtk.Image.new_from_icon_name(stream_icon)
                    icon.add_css_class("dim-label")
                    expander.add_prefix(icon)

                # Codec with icon - show codec name first, "Codec" as subtitle
                if "codec_name" in stream:
                    codec_name = stream["codec_name"]
                    if "profile" in stream:
                        codec_name += f" ({stream['profile']})"
                    codec_row = Adw.ActionRow(title=codec_name)
                    codec_row.set_subtitle(_("Codec"))

                    codec_row.add_suffix(
                        self._make_copy_button(_("Copy codec"), codec_name)
                    )

                    codec_icon = Gtk.Image.new_from_icon_name(
                        "application-x-executable-symbolic"
                    )
                    codec_icon.add_css_class("dim-label")
                    codec_row.add_suffix(codec_icon)

                    expander.add_row(codec_row)

                # Audio-specific information
                if stream.get("codec_type") == "audio":
                    # Sample rate - show sample rate value first, "Sample Rate" as subtitle
                    if "sample_rate" in stream:
                        sample_rate = int(stream["sample_rate"])
                        sample_value = f"{sample_rate:,} Hz"
                        sample_row = Adw.ActionRow(title=sample_value)
                        sample_row.set_subtitle(_("Sample Rate"))

                        sample_row.add_suffix(
                            self._make_copy_button(_("Copy sample rate"), sample_value)
                        )

                        if sample_rate >= 44100:
                            quality_label = Gtk.Label(
                                label=_("CD Quality")
                                if sample_rate == 44100
                                else _("Hi-Res Audio")
                            )
                            quality_label.add_css_class("caption")
                            quality_label.add_css_class("accent")
                            sample_row.add_suffix(quality_label)

                        expander.add_row(sample_row)

                    # Channels - show channel value first, "Channels" as subtitle
                    if "channels" in stream:
                        channels = stream["channels"]
                        channels_str = str(channels)
                        if channels == 1:
                            channels_str += " " + _("(Mono)")
                        elif channels == 2:
                            channels_str += " " + _("(Stereo)")
                        elif channels == 6:
                            channels_str += " " + _("(5.1 Surround)")
                        elif channels == 8:
                            channels_str += " " + _("(7.1 Surround)")

                        channels_row = Adw.ActionRow(title=channels_str)
                        channels_row.set_subtitle(_("Channels"))

                        channels_row.add_suffix(
                            self._make_copy_button(_("Copy channels"), channels_str)
                        )

                        expander.add_row(channels_row)

                    # Bit rate - show bitrate value first, "Bitrate" as subtitle
                    if "bit_rate" in stream:
                        bit_rate = int(stream["bit_rate"]) / 1000
                        bitrate_value = f"{bit_rate:.2f} kbps"
                        bitrate_row = Adw.ActionRow(title=bitrate_value)
                        bitrate_row.set_subtitle(_("Bitrate"))

                        bitrate_row.add_suffix(
                            self._make_copy_button(_("Copy bitrate"), bitrate_value)
                        )

                        expander.add_row(bitrate_row)

                # Language and other tags
                if "tags" in stream:
                    if "language" in stream["tags"]:
                        lang_code = stream["tags"]["language"].upper()
                        lang_row = Adw.ActionRow(title=lang_code)
                        lang_row.set_subtitle(_("Language"))

                        lang_row.add_suffix(
                            self._make_copy_button(_("Copy language code"), lang_code)
                        )

                        # Unknown ISO language codes are metadata, not an
                        # instruction to change the process-wide locale.

                        expander.add_row(lang_row)

                    # Display other tags except title and language - value first, tag name as subtitle
                    for tag, value in stream["tags"].items():
                        if tag not in ["title", "language"] and value:
                            tag_row = Adw.ActionRow(title=str(value))
                            tag_row.set_subtitle(tag.capitalize())

                            tag_row.add_suffix(
                                self._make_copy_button(_("Copy value"), str(value))
                            )

                            expander.add_row(tag_row)

                group.add(expander)

        self.info_box.append(group)

    def _add_format_info(self, format_data):
        """Add format-specific information"""
        group = Adw.PreferencesGroup(title=_("Format Details"))

        # Add selected format fields - value first, field name as subtitle
        format_fields = [
            ("format_long_name", _("Format")),
            ("bit_rate", _("Bitrate (bps)")),
            ("probe_score", _("Detection Score")),
        ]

        for field, title in format_fields:
            if field in format_data:
                value = str(format_data[field])
                row = Adw.ActionRow(title=value)
                row.set_subtitle(title)
                group.add(row)

        if "tags" in format_data:
            metadata_expander = Adw.ExpanderRow(title=_("Metadata"))

            # Flag to track if any rows were added
            rows_added = False

            for tag, value in format_data["tags"].items():
                if value:  # Only add non-empty values
                    # Value first, tag name as subtitle
                    tag_row = Adw.ActionRow(title=str(value))
                    tag_row.set_subtitle(tag.capitalize())
                    metadata_expander.add_row(tag_row)
                    rows_added = True

            # Only add the expander if there are metadata rows
            if rows_added:
                group.add(metadata_expander)

        self.info_box.append(group)

    def _show_error(self, message):
        """Show error message in the dialog"""
        if not self.dialog.get_visible():
            return
        if hasattr(self, "loading_box") and self.loading_box in self.info_box:
            self.info_box.remove(self.loading_box)

        error_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        error_box.set_margin_top(48)
        error_box.set_margin_bottom(48)
        error_box.set_valign(Gtk.Align.CENTER)
        error_box.set_halign(Gtk.Align.CENTER)

        error_icon = Gtk.Image.new_from_icon_name("dialog-error-symbolic")
        error_icon.set_pixel_size(48)
        error_icon.add_css_class("error")
        error_box.append(error_icon)

        error_label = Gtk.Label(label=_("Error retrieving file information"))
        error_box.append(error_label)

        error_details = Gtk.Label(label=message)
        error_details.set_wrap(True)
        error_box.append(error_details)

        retry_button = Gtk.Button(label=_("Retry"))
        retry_button.add_css_class("pill")
        retry_button.add_css_class("suggested-action")
        retry_button.set_halign(Gtk.Align.CENTER)
        retry_button.set_margin_top(12)
        self._connections.connect(retry_button, "clicked", self._on_retry_clicked)
        error_box.append(retry_button)

        self.info_box.append(error_box)

    def _on_retry_clicked(self, button):
        """Handle retry button click"""
        while True:
            child = self.info_box.get_first_child()
            if child:
                self.info_box.remove(child)
            else:
                break

        self.loading_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.loading_box.set_margin_top(48)
        self.loading_box.set_margin_bottom(48)
        self.loading_box.set_valign(Gtk.Align.CENTER)
        self.loading_box.set_halign(Gtk.Align.CENTER)

        self.spinner = Gtk.Spinner()
        self.spinner.set_size_request(32, 32)
        self.spinner.start()
        self.loading_box.append(self.spinner)

        self.loading_label = Gtk.Label(label=_("Analyzing video file..."))
        self.loading_box.append(self.loading_label)

        self.info_box.append(self.loading_box)

        # Retry loading file info
        GLib.idle_add(self._load_file_info)
