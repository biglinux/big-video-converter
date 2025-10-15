import os
import subprocess
import json
import gi
import threading

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, GLib, Gdk

# For translations
import gettext

_ = gettext.gettext


class VideoInfoDialog:
    """Dialog to display detailed video file information"""

    def __init__(self, parent_window, file_path):
        self.parent_window = parent_window
        self.file_path = file_path

        # Create the dialog window
        self.dialog = Adw.Window()
        self.dialog.set_default_size(780, 600)
        self.dialog.set_modal(True)
        self.dialog.set_transient_for(parent_window)
        self.dialog.set_hide_on_close(True)

        # Main content box
        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        # Add header bar with proper title
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

        # Create info box for actual content
        self.info_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24)
        self.info_box.set_hexpand(True)

        # Set up the widget hierarchy
        clamp.set_child(self.info_box)
        content_container.append(clamp)
        scrolled.set_child(content_container)
        main_box.append(scrolled)

        # Add loading state with spinner
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

        # Set dialog content
        self.dialog.set_content(content_box)

    def show(self):
        """Show the dialog and start loading file information"""
        self.dialog.present()

        # Start loading file information in background
        GLib.idle_add(self._load_file_info)

    def _load_file_info(self):
        """Load file information using ffprobe"""
        try:
            # Get file info in background thread to avoid blocking UI
            info_thread = threading.Thread(target=self._get_file_info_thread)
            info_thread.daemon = True
            info_thread.start()
            return False
        except Exception as e:
            self._show_error(str(e))
            return False

    def _get_file_info_thread(self):
        """Background thread to get file information"""
        try:
            info = get_video_file_info(self.file_path)
            GLib.idle_add(self._update_ui_with_info, info)
        except Exception as e:
            GLib.idle_add(self._show_error, str(e))

    def _update_ui_with_info(self, info):
        """Update the UI with the file information"""
        # Remove loading indicators
        self.info_box.remove(self.loading_box)

        if not info:
            self._show_error(_("Could not retrieve file information."))
            return

        # Add file information groups
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
                self._add_stream_group(_("Video Streams"), video_streams)

            if audio_streams:
                self._add_stream_group(_("Audio Streams"), audio_streams)

            if subtitle_streams:
                self._add_stream_group(_("Subtitles"), subtitle_streams)

        # Add format information
        if "format" in info:
            self._add_format_info(info["format"])

    def _add_general_info(self, info):
        """Add general file information"""
        group = Adw.PreferencesGroup(title=_("General Information"))

        # File name - show the file name first, "File Name" as subtitle
        file_name = os.path.basename(self.file_path)
        file_name_row = Adw.ActionRow(title=file_name)
        file_name_row.set_subtitle(_("File Name"))

        # Add a copy button to copy the file name
        copy_button = Gtk.Button.new_from_icon_name("edit-copy-symbolic")
        copy_button.add_css_class("flat")
        copy_button.set_tooltip_text(_("Copy file name"))
        copy_button.connect("clicked", lambda btn: self._copy_to_clipboard(file_name))
        file_name_row.add_suffix(copy_button)
        group.add(file_name_row)

        # File path (location) - show the directory first, "Location" as subtitle
        file_dir = os.path.dirname(self.file_path)
        file_path_row = Adw.ActionRow(title=file_dir)
        file_path_row.set_subtitle(_("Location"))

        # Add open folder button
        open_button = Gtk.Button.new_from_icon_name("folder-open-symbolic")
        open_button.add_css_class("flat")
        open_button.set_tooltip_text(_("Open containing folder"))
        open_button.connect(
            "clicked", lambda btn: self._open_containing_folder(file_dir)
        )
        file_path_row.add_suffix(open_button)
        group.add(file_path_row)

        # File size - show the size value first, "File Size" as subtitle
        if "format" in info and "size" in info["format"]:
            size_bytes = int(info["format"]["size"])
            size_str = format_file_size(size_bytes)
            size_row = Adw.ActionRow(title=size_str)
            size_row.set_subtitle(_("File Size"))

            # Add copy button
            copy_button = Gtk.Button.new_from_icon_name("edit-copy-symbolic")
            copy_button.add_css_class("flat")
            copy_button.set_tooltip_text(_("Copy file size"))
            copy_button.connect(
                "clicked", lambda btn: self._copy_to_clipboard(size_str)
            )
            size_row.add_suffix(copy_button)

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

            # Add copy button
            copy_button = Gtk.Button.new_from_icon_name("edit-copy-symbolic")
            copy_button.add_css_class("flat")
            copy_button.set_tooltip_text(_("Copy duration"))
            copy_button.connect(
                "clicked", lambda btn: self._copy_to_clipboard(duration_time)
            )
            duration_row.add_suffix(copy_button)

            group.add(duration_row)

        # Format - show format name first, "Format" as subtitle
        if "format_long_name" in info["format"]:
            format_name = info["format"]["format_long_name"]
            format_row = Adw.ActionRow(title=format_name)
            format_row.set_subtitle(_("Format"))

            # Add copy button
            copy_button = Gtk.Button.new_from_icon_name("edit-copy-symbolic")
            copy_button.add_css_class("flat")
            copy_button.set_tooltip_text(_("Copy format"))
            copy_button.connect(
                "clicked", lambda btn: self._copy_to_clipboard(format_name)
            )
            format_row.add_suffix(copy_button)

            group.add(format_row)

        # Bitrate - show bitrate value first, "Bitrate" as subtitle
        if "format" in info and "bit_rate" in info["format"]:
            bit_rate = int(info["format"]["bit_rate"]) / 1000
            bitrate_value = f"{bit_rate:.2f} kbps"
            bitrate_row = Adw.ActionRow(title=bitrate_value)
            bitrate_row.set_subtitle(_("Bitrate"))

            # Add copy button
            copy_button = Gtk.Button.new_from_icon_name("edit-copy-symbolic")
            copy_button.add_css_class("flat")
            copy_button.set_tooltip_text(_("Copy bitrate"))
            copy_button.connect(
                "clicked", lambda btn: self._copy_to_clipboard(bitrate_value)
            )
            bitrate_row.add_suffix(copy_button)

            group.add(bitrate_row)

        self.info_box.append(group)

    def _copy_to_clipboard(self, text):
        """Copy text to clipboard"""
        clipboard = Gdk.Display.get_default().get_clipboard()
        clipboard.set(text)

    def _open_containing_folder(self, folder_path):
        """Open the containing folder in the file manager"""
        try:
            Gtk.show_uri(self.dialog, f"file://{folder_path}", Gdk.CURRENT_TIME)
        except Exception as e:
            print(f"Error opening folder: {e}")
            # Fallback method using subprocess
            try:
                subprocess.Popen(["xdg-open", folder_path])
            except Exception as e2:
                print(f"Fallback error opening folder: {e2}")

    def _add_stream_group(self, title, streams):
        """Add a group of streams (video, audio, subtitles)"""
        group = Adw.PreferencesGroup(title=title)

        # Special handling for video streams - display directly without expanders
        if title == _("Video Streams"):
            for idx, stream in enumerate(streams):
                # Add important video info directly in the group
                if "codec_name" in stream:
                    codec_name = stream["codec_name"]
                    if "profile" in stream:
                        codec_name += f" ({stream['profile']})"
                    codec_row = Adw.ActionRow(title=codec_name)
                    codec_row.set_subtitle(_("Codec"))

                    # Add copy button
                    copy_button = Gtk.Button.new_from_icon_name("edit-copy-symbolic")
                    copy_button.add_css_class("flat")
                    copy_button.set_tooltip_text(_("Copy codec"))
                    copy_button.connect(
                        "clicked",
                        lambda btn, val=codec_name: self._copy_to_clipboard(val),
                    )
                    codec_row.add_suffix(copy_button)

                    group.add(codec_row)

                # Resolution
                if "width" in stream and "height" in stream:
                    res_value = f"{stream['width']}×{stream['height']}"
                    res_row = Adw.ActionRow(title=res_value)
                    res_row.set_subtitle(_("Resolution"))

                    # Add standard resolution label if applicable
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

                    # Add copy button
                    copy_button = Gtk.Button.new_from_icon_name("edit-copy-symbolic")
                    copy_button.add_css_class("flat")
                    copy_button.set_tooltip_text(_("Copy resolution"))
                    copy_button.connect(
                        "clicked",
                        lambda btn, val=res_value: self._copy_to_clipboard(val),
                    )
                    res_row.add_suffix(copy_button)

                    group.add(res_row)

                # Frame rate
                if "r_frame_rate" in stream:
                    try:
                        num, den = map(int, stream["r_frame_rate"].split("/"))
                        fps = num / den if den != 0 else 0
                        fps_value = f"{fps:.3f} fps"
                        fps_row = Adw.ActionRow(title=fps_value)
                        fps_row.set_subtitle(_("Frame Rate"))

                        # Add copy button
                        copy_button = Gtk.Button.new_from_icon_name(
                            "edit-copy-symbolic"
                        )
                        copy_button.add_css_class("flat")
                        copy_button.set_tooltip_text(_("Copy frame rate"))
                        copy_button.connect(
                            "clicked",
                            lambda btn, val=fps_value: self._copy_to_clipboard(val),
                        )
                        fps_row.add_suffix(copy_button)

                        group.add(fps_row)
                    except (ValueError, ZeroDivisionError):
                        pass

                # Pixel format
                if "pix_fmt" in stream:
                    pix_fmt = stream["pix_fmt"]
                    pix_row = Adw.ActionRow(title=pix_fmt)
                    pix_row.set_subtitle(_("Pixel Format"))

                    # Add copy button
                    copy_button = Gtk.Button.new_from_icon_name("edit-copy-symbolic")
                    copy_button.add_css_class("flat")
                    copy_button.set_tooltip_text(_("Copy pixel format"))
                    copy_button.connect(
                        "clicked", lambda btn, val=pix_fmt: self._copy_to_clipboard(val)
                    )
                    pix_row.add_suffix(copy_button)

                    group.add(pix_row)

                # Bit rate if present
                if "bit_rate" in stream:
                    bit_rate = int(stream["bit_rate"]) / 1000
                    bitrate_value = f"{bit_rate:.2f} kbps"
                    bitrate_row = Adw.ActionRow(title=bitrate_value)
                    bitrate_row.set_subtitle(_("Bitrate"))

                    # Add copy button
                    copy_button = Gtk.Button.new_from_icon_name("edit-copy-symbolic")
                    copy_button.add_css_class("flat")
                    copy_button.set_tooltip_text(_("Copy bitrate"))
                    copy_button.connect(
                        "clicked",
                        lambda btn, val=bitrate_value: self._copy_to_clipboard(val),
                    )
                    bitrate_row.add_suffix(copy_button)

                    group.add(bitrate_row)

                # Add language info if available
                if "tags" in stream and "language" in stream["tags"]:
                    lang_code = stream["tags"]["language"].upper()
                    lang_row = Adw.ActionRow(title=lang_code)
                    lang_row.set_subtitle(_("Language"))

                    # Add copy button
                    copy_button = Gtk.Button.new_from_icon_name("edit-copy-symbolic")
                    copy_button.add_css_class("flat")
                    copy_button.set_tooltip_text(_("Copy language code"))
                    copy_button.connect(
                        "clicked",
                        lambda btn, val=lang_code: self._copy_to_clipboard(val),
                    )
                    lang_row.add_suffix(copy_button)

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
                # ...existing code for audio and subtitle streams...
                stream_title = f"{title.rstrip('s')} {idx + 1}"
                stream_icon = None

                # Add appropriate stream icon based on type
                if stream.get("codec_type") == "video":
                    stream_icon = "video-x-generic-symbolic"
                elif stream.get("codec_type") == "audio":
                    stream_icon = "audio-x-generic-symbolic"
                elif stream.get("codec_type") == "subtitle":
                    stream_icon = "text-x-generic-symbolic"

                # Add language info to title
                if "tags" in stream:
                    if "title" in stream["tags"]:
                        stream_title += f" - {stream['tags']['title']}"
                    elif "language" in stream["tags"]:
                        lang = stream["tags"]["language"]
                        stream_title += f" - {lang.upper()}"

                expander = Adw.ExpanderRow(title=stream_title)

                # Add icon suffix if we have one
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

                    # Add copy button for codec
                    codec_copy_button = Gtk.Button.new_from_icon_name(
                        "edit-copy-symbolic"
                    )
                    codec_copy_button.add_css_class("flat")
                    codec_copy_button.set_tooltip_text(_("Copy codec"))
                    codec_copy_button.connect(
                        "clicked",
                        lambda btn, val=codec_name: self._copy_to_clipboard(val),
                    )
                    codec_row.add_suffix(codec_copy_button)

                    # Add codec icon suffix
                    codec_icon = Gtk.Image.new_from_icon_name(
                        "application-x-executable-symbolic"
                    )
                    codec_icon.add_css_class("dim-label")
                    codec_row.add_suffix(codec_icon)

                    expander.add_row(codec_row)

                # Audio-specific information
                if stream.get("codec_type") == "audio":
                    # ...existing code for audio...
                    # Sample rate - show sample rate value first, "Sample Rate" as subtitle
                    if "sample_rate" in stream:
                        sample_rate = int(stream["sample_rate"])
                        sample_value = f"{sample_rate:,} Hz"
                        sample_row = Adw.ActionRow(title=sample_value)
                        sample_row.set_subtitle(_("Sample Rate"))

                        # Add copy button for sample rate
                        sample_copy_button = Gtk.Button.new_from_icon_name(
                            "edit-copy-symbolic"
                        )
                        sample_copy_button.add_css_class("flat")
                        sample_copy_button.set_tooltip_text(_("Copy sample rate"))
                        sample_copy_button.connect(
                            "clicked",
                            lambda btn, val=sample_value: self._copy_to_clipboard(val),
                        )
                        sample_row.add_suffix(sample_copy_button)

                        # Add suffix for quality indicator
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

                        # Add copy button for channels
                        channels_copy_button = Gtk.Button.new_from_icon_name(
                            "edit-copy-symbolic"
                        )
                        channels_copy_button.add_css_class("flat")
                        channels_copy_button.set_tooltip_text(_("Copy channels"))
                        channels_copy_button.connect(
                            "clicked",
                            lambda btn, val=channels_str: self._copy_to_clipboard(val),
                        )
                        channels_row.add_suffix(channels_copy_button)

                        expander.add_row(channels_row)

                    # Bit rate - show bitrate value first, "Bitrate" as subtitle
                    if "bit_rate" in stream:
                        bit_rate = int(stream["bit_rate"]) / 1000
                        bitrate_value = f"{bit_rate:.2f} kbps"
                        bitrate_row = Adw.ActionRow(title=bitrate_value)
                        bitrate_row.set_subtitle(_("Bitrate"))

                        # Add copy button for bitrate
                        bitrate_copy_button = Gtk.Button.new_from_icon_name(
                            "edit-copy-symbolic"
                        )
                        bitrate_copy_button.add_css_class("flat")
                        bitrate_copy_button.set_tooltip_text(_("Copy bitrate"))
                        bitrate_copy_button.connect(
                            "clicked",
                            lambda btn, val=bitrate_value: self._copy_to_clipboard(val),
                        )
                        bitrate_row.add_suffix(bitrate_copy_button)

                        expander.add_row(bitrate_row)

                # ...existing code for the rest of the audio and subtitle stream info...

                # Language and other tags
                if "tags" in stream:
                    if "language" in stream["tags"]:
                        lang_code = stream["tags"]["language"].upper()
                        lang_row = Adw.ActionRow(title=lang_code)
                        lang_row.set_subtitle(_("Language"))

                        # Add copy button for language
                        lang_copy_button = Gtk.Button.new_from_icon_name(
                            "edit-copy-symbolic"
                        )
                        lang_copy_button.add_css_class("flat")
                        lang_copy_button.set_tooltip_text(_("Copy language code"))
                        lang_copy_button.connect(
                            "clicked",
                            lambda btn, val=lang_code: self._copy_to_clipboard(val),
                        )
                        lang_row.add_suffix(lang_copy_button)

                        # Try to get the full language name
                        try:
                            import locale

                            lang_code_lower = stream["tags"]["language"]
                            lang_obj = locale.setlocale(
                                locale.LC_ALL, f"{lang_code_lower}.UTF-8"
                            )
                            if lang_obj:
                                lang_name = locale.nl_langinfo(locale.LANG_NAME)
                                if lang_name and lang_name != lang_code_lower:
                                    lang_label = Gtk.Label(label=lang_name)
                                    lang_label.add_css_class("caption")
                                    lang_row.add_suffix(lang_label)
                        except:
                            pass  # Ignore language name lookup errors

                        expander.add_row(lang_row)

                    # Display other tags except title and language - value first, tag name as subtitle
                    for tag, value in stream["tags"].items():
                        if tag not in ["title", "language"] and value:
                            tag_row = Adw.ActionRow(title=str(value))
                            tag_row.set_subtitle(tag.capitalize())

                            # Add copy button for tag value
                            tag_copy_button = Gtk.Button.new_from_icon_name(
                                "edit-copy-symbolic"
                            )
                            tag_copy_button.add_css_class("flat")
                            tag_copy_button.set_tooltip_text(_("Copy value"))
                            tag_copy_button.connect(
                                "clicked",
                                lambda btn, val=value: self._copy_to_clipboard(
                                    str(val)
                                ),
                            )
                            tag_row.add_suffix(tag_copy_button)

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

        # Add metadata
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
        # Remove loading indicators if they exist
        if hasattr(self, "loading_box") and self.loading_box in self.info_box:
            self.info_box.remove(self.loading_box)

        # Add error message
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

        # Add a retry button
        retry_button = Gtk.Button(label=_("Retry"))
        retry_button.add_css_class("pill")
        retry_button.add_css_class("suggested-action")
        retry_button.set_halign(Gtk.Align.CENTER)
        retry_button.set_margin_top(12)
        retry_button.connect("clicked", self._on_retry_clicked)
        error_box.append(retry_button)

        self.info_box.append(error_box)

    def _on_retry_clicked(self, button):
        """Handle retry button click"""
        # Clear the content box
        while True:
            child = self.info_box.get_first_child()
            if child:
                self.info_box.remove(child)
            else:
                break

        # Add loading indicator back
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


def get_video_file_info(file_path):
    """
    Get detailed information about a video file using ffprobe

    Args:
        file_path: Path to the video file

    Returns:
        Dictionary containing file information or None on error
    """
    try:
        # Ensure file exists
        if not os.path.exists(file_path):
            return None

        # Run ffprobe with JSON output
        command = [
            "ffprobe",
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            "-show_chapters",
            file_path,
        ]

        result = subprocess.run(command, capture_output=True, text=True, check=True)

        # Parse JSON output
        info = json.loads(result.stdout)

        # Calculate bitrate if not provided by ffprobe
        if "format" in info:
            if "bit_rate" not in info["format"] or info["format"]["bit_rate"] == "N/A":
                if "duration" in info["format"] and "size" in info["format"]:
                    duration = float(info["format"]["duration"])
                    size = float(info["format"]["size"])
                    if duration > 0:
                        bitrate = (size * 8) / duration
                        info["format"]["bit_rate"] = str(int(bitrate))

        return info

    except subprocess.CalledProcessError:
        # ffprobe command failed
        return None
    except json.JSONDecodeError:
        # Invalid JSON output
        return None
    except Exception as e:
        print(f"Error getting file info: {e}")
        return None

def format_file_size(size_bytes):
    """Format file size in bytes to a human-readable string"""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.2f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"


def has_audio_streams(file_path):
    """
    Check if a video file has audio streams.

    Args:
        file_path: Path to the video file

    Returns:
        bool: True if the file has at least one audio stream, False otherwise
    """
    try:
        # Ensure file exists
        if not os.path.exists(file_path):
            return False

        # Run ffprobe to check for audio streams
        command = [
            "ffprobe",
            "-v",
            "quiet",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=codec_type",
            "-of",
            "csv=p=0",
            file_path,
        ]

        result = subprocess.run(command, capture_output=True, text=True)

        # If we get output, it means there's at least one audio stream
        return bool(result.stdout.strip())

    except Exception as e:
        print(f"Error checking audio streams: {e}")
        # On error, assume there might be audio to avoid accidentally removing it
        return True
