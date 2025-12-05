import os
import gi
from datetime import datetime

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, GLib, Pango

# Setup translation
import gettext

_ = gettext.gettext


class ProgressPage:
    """
    Professional progress page with integrated queue visualization.
    Clean, modern design with consistent spacing and stable layout.
    """

    def __init__(self, app):
        self.app = app

        # Create ToolbarView to wrap the content and provide headerbar
        self.toolbar_view = Adw.ToolbarView()

        # Create HeaderBar with window controls (include close button)
        self.header_bar = Adw.HeaderBar()
        
        # Title with queue counter
        self.title_label = Gtk.Label(label=_("Converting Videos"))
        self.title_label.add_css_class("title")
        self.header_bar.set_title_widget(self.title_label)
        
        # Back button (hidden during conversion, shown when complete)
        self.back_button = Gtk.Button()
        self.back_button.set_icon_name("go-previous-symbolic")
        self.back_button.connect("clicked", self._on_back_clicked)
        self.back_button.set_visible(False)
        self.header_bar.pack_start(self.back_button)
        
        # Setup custom tooltip for back button
        if hasattr(self.app, 'tooltip_helper'):
            self.app.tooltip_helper.add_tooltip(self.back_button, "progress_back")
        
        # Store original layout for restoration
        self.window_buttons_left = self.app._window_buttons_on_left()
        # Disable close button during conversion (keep minimize and maximize)
        if self.window_buttons_left:
            self.header_bar.set_decoration_layout("minimize,maximize:")
        else:
            self.header_bar.set_decoration_layout(":minimize,maximize")
        
        self.toolbar_view.add_top_bar(self.header_bar)

        # Main page container
        self.page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.page.set_vexpand(True)

        # Create main content with Clamp for responsive design
        main_scroll = Gtk.ScrolledWindow()
        main_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        main_scroll.set_vexpand(True)
        
        clamp = Adw.Clamp()
        clamp.set_maximum_size(900)
        clamp.set_tightening_threshold(600)
        clamp.set_margin_start(24)
        clamp.set_margin_end(24)
        clamp.set_margin_top(16)
        clamp.set_margin_bottom(24)
        
        self.content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        clamp.set_child(self.content_box)
        main_scroll.set_child(clamp)
        self.page.append(main_scroll)

        # ===== QUEUE LIST SECTION =====
        self.queue_group = Adw.PreferencesGroup()
        self.queue_group.set_title(_("Queue"))
        
        self.queue_listbox = Gtk.ListBox()
        self.queue_listbox.set_selection_mode(Gtk.SelectionMode.NONE)
        self.queue_listbox.add_css_class("boxed-list")
        self.queue_group.add(self.queue_listbox)
        
        self.content_box.append(self.queue_group)

        # ===== BOTTOM ACTION BAR =====
        self.action_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        self.action_bar.set_halign(Gtk.Align.CENTER)
        self.action_bar.set_margin_top(8)
        
        self.cancel_all_button = Gtk.Button(label=_("Cancel All"))
        self.cancel_all_button.add_css_class("destructive-action")
        self.cancel_all_button.add_css_class("pill")
        self.cancel_all_button.connect("clicked", self._on_cancel_all_clicked)
        self.action_bar.append(self.cancel_all_button)
        
        self.content_box.append(self.action_bar)

        # ===== COMPLETION BANNER =====
        self.completion_banner = Adw.Banner()
        self.completion_banner.set_revealed(False)
        self.completion_banner.set_button_label(_("Back"))
        self.completion_banner.connect("button-clicked", self._on_back_clicked)
        self.page.append(self.completion_banner)

        # Set the page as content of toolbar_view
        self.toolbar_view.set_content(self.page)

        # Dictionary to track all queue items
        self.queue_items = {}
        self.active_conversions = {}
        self.count = 0
        self.total_queue_items = 0
        self.completed_count = 0

        # Add CSS for styling
        self._setup_css()

    def _setup_css(self):
        """Setup CSS for progress page styling"""
        css_provider = Gtk.CssProvider()
        css_provider.load_from_data(b"""
            .queue-row {
                padding: 8px 12px;
                min-height: 60px;
            }
            .queue-row-active {
                background: alpha(@accent_bg_color, 0.08);
                border-left: 3px solid @accent_color;
            }
            .queue-row-completed {
                background: alpha(@success_bg_color, 0.06);
                border-left: 3px solid @success_color;
            }
            .queue-row-failed {
                background: alpha(@error_bg_color, 0.06);
                border-left: 3px solid @error_color;
            }
            .queue-row-cancelled {
                background: alpha(@warning_bg_color, 0.06);
                border-left: 3px solid @warning_color;
            }
            .queue-row-pending {
                opacity: 0.7;
                border-left: 3px solid transparent;
            }
            .status-success { color: @success_color; font-weight: 500; }
            .status-error { color: @error_color; font-weight: 500; }
            .status-warning { color: @warning_color; font-weight: 500; }
            .status-active { color: @accent_color; font-weight: 500; }
            .status-pending { color: @dim_label_color; }
            .time-info { 
                font-size: 0.85em; 
                color: @dim_label_color;
                font-variant-numeric: tabular-nums;
            }
            .filename-label {
                font-weight: 500;
            }
            .details-box {
                background: alpha(@card_bg_color, 0.5);
                border-radius: 6px;
                padding: 12px;
                margin-top: 8px;
            }
            .log-view {
                font-family: monospace;
                font-size: 0.9em;
                background: alpha(@view_bg_color, 0.8);
                border-radius: 4px;
                padding: 8px;
            }
            banner button {
                background-color: @accent_bg_color;
                color: @accent_fg_color;
            }
            banner button:hover {
                background-color: shade(@accent_bg_color, 1.1);
            }
        """)

        display = self.toolbar_view.get_display()
        if display is not None:
            Gtk.StyleContext.add_provider_for_display(
                display, css_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )

    def get_page(self):
        return self.toolbar_view

    def initialize_queue(self, queue_items):
        """Initialize the queue display with all items as pending"""
        self.reset()
        self.total_queue_items = len(queue_items)
        
        for file_path in queue_items:
            if file_path not in self.queue_items:
                row = QueueItemRow(self.app, file_path, self)
                self.queue_items[file_path] = row
                self.queue_listbox.append(row)
        
        self._update_overall_progress()

    def _update_overall_progress(self):
        """Update the title based on conversion progress"""
        if self.total_queue_items > 0:
            if self.completed_count == self.total_queue_items:
                self.title_label.set_label(_("Completed!"))
                # Re-enable close button when complete
                if self.window_buttons_left:
                    self.header_bar.set_decoration_layout("close,minimize,maximize:")
                else:
                    self.header_bar.set_decoration_layout(":minimize,maximize,close")
            else:
                self.title_label.set_label(
                    _("Converting Videos") + f" ({self.completed_count}/{self.total_queue_items})"
                )
        else:
            self.title_label.set_label(_("Converting Videos"))

    def add_conversion(self, command_title, input_file, process):
        """Start tracking a conversion for a file"""
        conversion_id = f"conversion_{self.count}"
        self.count += 1

        if not input_file:
            input_file = command_title or "unknown"

        if len(self.active_conversions) == 0:
            self.app.show_progress_page()

        if input_file in self.queue_items:
            row = self.queue_items[input_file]
        else:
            row = QueueItemRow(self.app, input_file, self)
            self.queue_items[input_file] = row
            self.queue_listbox.prepend(row)
            self.total_queue_items += 1

        row.start_conversion(process, conversion_id)

        self.active_conversions[conversion_id] = {
            "row": row,
            "input_file": input_file,
        }

        self._update_overall_progress()
        return row

    def mark_conversion_complete(self, conversion_id, success=True, output_file=None):
        """Mark a conversion as complete"""
        if conversion_id in self.active_conversions:
            conv_data = self.active_conversions[conversion_id]
            row = conv_data.get("row")
            
            if row and row.status not in ("completed", "failed", "cancelled"):
                row.mark_complete(success, output_file)
            
            self.completed_count += 1
            self._update_overall_progress()
            self._check_all_complete()

    def _check_all_complete(self):
        """Check if all queue items are processed"""
        all_done = all(
            row.status in ("completed", "failed", "cancelled")
            for row in self.queue_items.values()
        )
        
        if all_done and len(self.queue_items) > 0:
            GLib.timeout_add(300, self._show_completion_summary)

    def _show_completion_summary(self):
        """Show completion summary banner"""
        successful = sum(1 for r in self.queue_items.values() if r.status == "completed")
        failed = sum(1 for r in self.queue_items.values() if r.status == "failed")
        cancelled = sum(1 for r in self.queue_items.values() if r.status == "cancelled")
        
        if failed == 0 and cancelled == 0:
            self.completion_banner.set_title(_("All {} videos converted successfully!").format(successful))
            self.completion_banner.remove_css_class("error")
            self.completion_banner.add_css_class("success")
        elif successful > 0:
            self.completion_banner.set_title(
                _("{} completed, {} failed, {} cancelled").format(successful, failed, cancelled)
            )
            self.completion_banner.remove_css_class("success")
            self.completion_banner.remove_css_class("error")
        else:
            self.completion_banner.set_title(_("All conversions failed or were cancelled"))
            self.completion_banner.remove_css_class("success")
            self.completion_banner.add_css_class("error")
        
        self.completion_banner.set_revealed(True)
        self.cancel_all_button.set_visible(False)
        self.back_button.set_visible(True)  # Show back button when complete
        
        # Update title to "Completed!" and re-enable close button
        self.title_label.set_label(_("Completed!"))
        if self.window_buttons_left:
            self.header_bar.set_decoration_layout("close,minimize,maximize:")
        else:
            self.header_bar.set_decoration_layout(":minimize,maximize,close")
        
        return False

    def remove_conversion(self, conversion_id):
        if conversion_id in self.active_conversions:
            del self.active_conversions[conversion_id]

    def has_active_conversions(self):
        return len(self.active_conversions) > 0

    def _on_cancel_all_clicked(self, button):
        """Show confirmation dialog before cancelling all"""
        dialog = Adw.AlertDialog()
        dialog.set_heading(_("Cancel All Conversions?"))
        dialog.set_body(_("This will stop all active conversions and skip all pending items. This action cannot be undone."))
        dialog.add_response("cancel", _("Continue"))
        dialog.add_response("confirm", _("Cancel All"))
        dialog.set_response_appearance("confirm", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", self._on_cancel_all_response)
        # Use the app's window as parent, not the app itself
        window = self.app.get_active_window() if hasattr(self.app, 'get_active_window') else self.app
        dialog.present(window)

    def _on_cancel_all_response(self, dialog, response):
        """Handle cancel all confirmation response"""
        if response == "confirm":
            self._do_cancel_all()

    def _do_cancel_all(self):
        """Actually cancel all conversions"""
        self.app.is_cancellation_requested = True
        
        for conv_data in list(self.active_conversions.values()):
            row = conv_data.get("row")
            if row:
                row.cancel(cancel_all=True)
        
        for row in self.queue_items.values():
            if row.status == "pending":
                row.mark_cancelled()

    def _on_back_clicked(self, button):
        """Go back to main view"""
        self.app.return_to_main_view()

    def reset(self):
        self.active_conversions.clear()
        self.queue_items.clear()
        self.total_queue_items = 0
        self.completed_count = 0
        self.count = 0
        
        while True:
            row = self.queue_listbox.get_first_child()
            if row:
                self.queue_listbox.remove(row)
            else:
                break
        
        self.completion_banner.set_revealed(False)
        self.cancel_all_button.set_visible(True)
        self.back_button.set_visible(False)  # Hide back button on reset
        # Disable close button during conversion (keep minimize and maximize)
        if self.window_buttons_left:
            self.header_bar.set_decoration_layout("minimize,maximize:")
        else:
            self.header_bar.set_decoration_layout(":minimize,maximize")
        self._update_overall_progress()

    def show_completion_summary(self):
        """Public method called from main.py"""
        self._show_completion_summary()


class QueueItemRow(Gtk.ListBoxRow):
    """A clean, stable row representing one video file in the queue."""

    def __init__(self, app, file_path, progress_page):
        super().__init__()
        
        self.app = app
        self.file_path = file_path if file_path else ""
        self.progress_page = progress_page
        self.process = None
        self.conversion_id = None
        self.status = "pending"
        self.current_progress = 0.0
        self._cancelled = False
        
        # Compatibility attributes for conversion.py
        self.input_file = self.file_path
        self.input_file_path = self.file_path
        self.original_input_file = self.file_path
        self.delete_original = False
        self.expected_duration = None
        self.is_queue_processing = False
        self.is_segment_batch = False
        
        # Timing
        self.start_time = None
        self.end_time = None
        
        # Build UI
        self._build_ui()
        self._set_state("pending")

    def _build_ui(self):
        """Build the row UI with fixed layout"""
        self.main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.set_child(self.main_box)
        
        # Main content row - fixed height to prevent jumping
        content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        content.add_css_class("queue-row")
        
        # Status icon (fixed size)
        self.status_icon = Gtk.Image()
        self.status_icon.set_pixel_size(32)
        self.status_icon.set_valign(Gtk.Align.CENTER)
        content.append(self.status_icon)
        
        # Center column: filename, status, progress
        center_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        center_box.set_hexpand(True)
        center_box.set_valign(Gtk.Align.CENTER)
        
        # Filename
        filename = os.path.basename(self.file_path) if self.file_path else _("Unknown file")
        self.filename_label = Gtk.Label(label=filename)
        self.filename_label.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        self.filename_label.set_halign(Gtk.Align.START)
        self.filename_label.set_xalign(0)
        self.filename_label.add_css_class("filename-label")
        center_box.append(self.filename_label)
        
        # Status row: status text on left, FPS on right (horizontal layout)
        status_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        
        self.status_label = Gtk.Label(label=_("Waiting in queue"))
        self.status_label.set_halign(Gtk.Align.START)
        self.status_label.set_xalign(0)
        self.status_label.set_hexpand(True)
        self.status_label.add_css_class("status-pending")
        status_row.append(self.status_label)
        
        # FPS label (shown during conversion, on the right)
        self.fps_label = Gtk.Label(label="")
        self.fps_label.set_halign(Gtk.Align.END)
        self.fps_label.add_css_class("dim-label")
        self.fps_label.set_visible(False)
        status_row.append(self.fps_label)
        
        center_box.append(status_row)
        
        # Progress bar (always present, hidden when not active)
        self.progress_bar = Gtk.ProgressBar()
        self.progress_bar.set_margin_top(4)
        self.progress_bar.set_visible(False)
        center_box.append(self.progress_bar)
        
        content.append(center_box)
        
        # Right column: time info (fixed width for stability)
        time_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        time_box.set_valign(Gtk.Align.CENTER)
        time_box.set_size_request(100, -1)  # Fixed width
        
        self.time_label_1 = Gtk.Label(label="")
        self.time_label_1.add_css_class("time-info")
        self.time_label_1.set_halign(Gtk.Align.END)
        time_box.append(self.time_label_1)
        
        self.time_label_2 = Gtk.Label(label="")
        self.time_label_2.add_css_class("time-info")
        self.time_label_2.set_halign(Gtk.Align.END)
        time_box.append(self.time_label_2)
        
        content.append(time_box)
        
        # Action buttons (fixed width)
        button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        button_box.set_valign(Gtk.Align.CENTER)
        button_box.set_size_request(72, -1)  # Fixed width for 2 buttons
        
        self.details_button = Gtk.ToggleButton()
        self.details_button.set_icon_name("view-more-symbolic")
        self.details_button.add_css_class("flat")
        self.details_button.add_css_class("circular")
        self.details_button.connect("toggled", self._on_details_toggled)
        self.details_button.set_sensitive(False)
        button_box.append(self.details_button)
        
        self.cancel_button = Gtk.Button()
        self.cancel_button.set_icon_name("process-stop-symbolic")
        self.cancel_button.add_css_class("flat")
        self.cancel_button.add_css_class("circular")
        self.cancel_button.connect("clicked", lambda b: self.cancel())
        self.cancel_button.set_visible(False)
        button_box.append(self.cancel_button)
        
        # Setup custom tooltips for buttons
        if hasattr(self.app, 'tooltip_helper'):
            self.app.tooltip_helper.add_tooltip(self.details_button, "progress_show_log")
            self.app.tooltip_helper.add_tooltip(self.cancel_button, "progress_cancel_file")
        
        content.append(button_box)
        self.main_box.append(content)
        
        # Details revealer
        self.details_revealer = Gtk.Revealer()
        self.details_revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
        
        details_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        details_box.add_css_class("details-box")
        details_box.set_margin_start(56)
        details_box.set_margin_end(12)
        details_box.set_margin_bottom(8)
        
        # Command section header
        cmd_header = Gtk.Label(label=_("FFmpeg Command"))
        cmd_header.set_halign(Gtk.Align.START)
        cmd_header.add_css_class("heading")
        cmd_header.set_margin_bottom(4)
        details_box.append(cmd_header)
        
        # Command label
        self.cmd_text = Gtk.Label(label="")
        self.cmd_text.set_selectable(True)
        self.cmd_text.set_wrap(True)
        self.cmd_text.set_wrap_mode(Pango.WrapMode.CHAR)
        self.cmd_text.set_xalign(0)
        self.cmd_text.add_css_class("log-view")
        details_box.append(self.cmd_text)
        
        # Log section header
        log_header = Gtk.Label(label=_("Process Output"))
        log_header.set_halign(Gtk.Align.START)
        log_header.add_css_class("heading")
        log_header.set_margin_top(8)
        log_header.set_margin_bottom(4)
        details_box.append(log_header)
        
        # Log scroll
        log_scroll = Gtk.ScrolledWindow()
        log_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        log_scroll.set_min_content_height(120)
        log_scroll.set_max_content_height(200)
        
        self.terminal_view = Gtk.TextView()
        self.terminal_view.set_editable(False)
        self.terminal_view.set_cursor_visible(False)
        self.terminal_view.set_monospace(True)
        self.terminal_view.add_css_class("log-view")
        self.terminal_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        
        self.terminal_buffer = self.terminal_view.get_buffer()
        log_scroll.set_child(self.terminal_view)
        details_box.append(log_scroll)
        
        self.details_revealer.set_child(details_box)
        self.main_box.append(self.details_revealer)

    def _set_state(self, state):
        """Set visual state with appropriate icon and styling"""
        # Remove all state classes
        for cls in ["queue-row-pending", "queue-row-active", "queue-row-completed", 
                    "queue-row-failed", "queue-row-cancelled"]:
            self.remove_css_class(cls)
        
        # Remove status label classes
        for cls in ["status-pending", "status-active", "status-success", 
                    "status-error", "status-warning"]:
            self.status_label.remove_css_class(cls)
        
        # Hide FPS label when not active
        if state != "active":
            self.fps_label.set_visible(False)
        
        if state == "pending":
            self.add_css_class("queue-row-pending")
            self.status_icon.set_from_icon_name("content-loading-symbolic")
            self.status_label.add_css_class("status-pending")
            self.cancel_button.set_visible(True)  # Allow cancelling pending items
            # Update tooltip key for pending state
            self.cancel_button.tooltip_key = "progress_skip_file"
        elif state == "active":
            self.add_css_class("queue-row-active")
            self.status_icon.set_from_icon_name("media-playback-start-symbolic")
            self.status_label.add_css_class("status-active")
            self.cancel_button.set_visible(True)
            # Update tooltip key for active state
            self.cancel_button.tooltip_key = "progress_cancel_file"
        elif state == "completed":
            self.add_css_class("queue-row-completed")
            self.status_icon.set_from_icon_name("emblem-ok-symbolic")
            self.status_label.add_css_class("status-success")
            self.cancel_button.set_visible(False)
        elif state == "failed":
            self.add_css_class("queue-row-failed")
            self.status_icon.set_from_icon_name("dialog-error-symbolic")
            self.status_label.add_css_class("status-error")
            self.cancel_button.set_visible(False)
        elif state == "cancelled":
            self.add_css_class("queue-row-cancelled")
            self.status_icon.set_from_icon_name("action-unavailable-symbolic")
            self.status_label.add_css_class("status-warning")
            self.cancel_button.set_visible(False)

    def _on_details_toggled(self, button):
        is_active = button.get_active()
        self.details_revealer.set_reveal_child(is_active)
        button.set_icon_name("view-more-horizontal-symbolic" if is_active else "view-more-symbolic")

    def start_conversion(self, process, conversion_id):
        self.process = process
        self.conversion_id = conversion_id
        self.status = "active"
        self.start_time = datetime.now()
        
        self._set_state("active")
        self.status_label.set_text(_("Converting..."))
        self.progress_bar.set_visible(True)
        self.progress_bar.set_fraction(0)
        
        self.time_label_1.set_text(self.start_time.strftime("%H:%M:%S"))
        self.time_label_2.set_text("")
        
        self.details_button.set_sensitive(True)
        self.cancel_button.set_visible(True)

    def update_progress(self, fraction, text=None):
        self.current_progress = fraction
        self.progress_bar.set_fraction(min(1.0, fraction))
        
        if text:
            # Check if text contains FPS information (format: "status | 25 fps")
            if " | " in text and "fps" in text.lower():
                parts = text.rsplit(" | ", 1)
                self.status_label.set_text(parts[0])
                self.fps_label.set_text(parts[1])
                self.fps_label.set_visible(True)
            else:
                self.status_label.set_text(text)
        else:
            self.status_label.set_text(f"{_('Converting')}... {int(fraction * 100)}%")

    def update_fps(self, fps_text):
        """Update the FPS display separately"""
        if fps_text:
            self.fps_label.set_text(fps_text)
            self.fps_label.set_visible(True)
        else:
            self.fps_label.set_visible(False)

    def update_status(self, status):
        # Check if status contains FPS info with " | " separator
        if " | " in status:
            parts = status.split(" | ", 1)
            self.status_label.set_text(parts[0])  # GPU acceleration info
            self.fps_label.set_text(parts[1])     # FPS info
            self.fps_label.set_visible(True)
        else:
            self.status_label.set_text(status)
            # Don't hide FPS label here as it may have been set separately

    def add_output_text(self, text):
        if not text:
            return
        end_iter = self.terminal_buffer.get_end_iter()
        self.terminal_buffer.insert(end_iter, text)

    def set_command_text(self, text):
        self.cmd_text.set_text(text[:500] + "..." if len(text) > 500 else text)

    def mark_complete(self, success=True, output_file=None):
        self.end_time = datetime.now()
        self.process = None
        
        if self.start_time:
            duration = self.end_time - self.start_time
            total_seconds = int(duration.total_seconds())
            mins, secs = divmod(total_seconds, 60)
            self.time_label_2.set_text(f"{mins}m {secs}s" if mins else f"{secs}s")
        
        self.progress_bar.set_visible(False)
        self.cancel_button.set_visible(False)
        
        if success:
            self.status = "completed"
            self._set_state("completed")
            self.status_label.set_text(_("Completed"))
        else:
            self.status = "failed"
            self._set_state("failed")
            self.status_label.set_text(_("Failed"))

    def mark_cancelled(self):
        self.status = "cancelled"
        self.end_time = datetime.now()
        self._set_state("cancelled")
        self.status_label.set_text(_("Cancelled"))
        self.progress_bar.set_visible(False)
        self.cancel_button.set_visible(False)

    def cancel(self, cancel_all=False):
        if self.status not in ("active", "pending"):
            return
        
        was_active = self.status == "active"
        was_pending = self.status == "pending"
        
        self._cancelled = True
        if cancel_all:
            self.app.is_cancellation_requested = True
        
        self.cancel_button.set_sensitive(False)
        
        if was_active and self.process:
            try:
                if hasattr(self.app, "terminate_process_tree"):
                    self.app.terminate_process_tree(self.process)
                else:
                    self.process.terminate()
            except Exception as e:
                print(f"Error cancelling: {e}")
        
        # For pending items, also remove from the conversion queue
        if was_pending and self.file_path:
            if hasattr(self.app, "conversion_queue") and self.file_path in self.app.conversion_queue:
                self.app.conversion_queue.remove(self.file_path)
                print(f"Removed {os.path.basename(self.file_path)} from queue")
        
        self.mark_cancelled()
        
        # Update completed count for skipped pending items
        if was_pending and self.progress_page:
            self.progress_page.completed_count += 1
            self.progress_page._update_overall_progress()
            self.progress_page._check_all_complete()
        
        if was_active and not cancel_all and self.progress_page:
            GLib.timeout_add(300, self._notify_continue)

    def _notify_continue(self):
        if hasattr(self.app, 'conversion_completed'):
            self.app.conversion_completed(success=False)
        return False

    def set_delete_original(self, delete_original):
        self.delete_original = delete_original

    def mark_success(self):
        self.mark_complete(success=True)
        if self.progress_page:
            self.progress_page.mark_conversion_complete(self.conversion_id, success=True)

    def mark_failure(self):
        self.mark_complete(success=False)
        if self.progress_page:
            self.progress_page.mark_conversion_complete(self.conversion_id, success=False)

    def was_cancelled(self):
        return self._cancelled or self.status == "cancelled"


# Backwards compatibility
ConversionItem = QueueItemRow
