import os
import subprocess
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, GLib, Pango

# Setup translation
import gettext

_ = gettext.gettext


class ProgressPage:
    """
    Page to display conversion progress in the main UI.
    Manages active conversions and their progress display.
    """

    def __init__(self, app):
        self.app = app

        # Create ToolbarView to wrap the content and provide headerbar
        self.toolbar_view = Adw.ToolbarView()

        # Create HeaderBar with window controls
        header_bar = Adw.HeaderBar()
        header_bar.set_title_widget(Gtk.Label(label=_("Conversion Progress")))
        # Show only minimize and maximize buttons, disable close button
        # Users must use the Cancel button to stop conversions
        # Check if window buttons are on the left or right
        window_buttons_left = self.app._window_buttons_on_left()
        if window_buttons_left:
            header_bar.set_decoration_layout("minimize,maximize:")
        else:
            header_bar.set_decoration_layout(":minimize,maximize")
        self.toolbar_view.add_top_bar(header_bar)

        # Root container using Box for vertical layout
        self.page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        # Create an overlay for proper vertical centering
        overlay = Gtk.Overlay()
        overlay.set_vexpand(True)
        self.page.append(overlay)

        # Main content container with vertical centering
        centered_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        centered_box.set_valign(Gtk.Align.CENTER)  # Center vertically
        centered_box.set_halign(Gtk.Align.FILL)  # Fill horizontally
        overlay.add_overlay(centered_box)

        # Create a scrolled window with appropriate padding
        self.scrolled_window = Gtk.ScrolledWindow()
        self.scrolled_window.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.scrolled_window.set_propagate_natural_height(True)  # Use natural height
        centered_box.append(self.scrolled_window)

        # Main content box - no vertical expansion to allow centering
        self.main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.scrolled_window.set_child(self.main_box)

        # Use Adw.Clamp for responsive width constraints
        self.clamp = Adw.Clamp()
        self.clamp.set_maximum_size(1100)
        self.clamp.set_tightening_threshold(600)
        self.main_box.append(self.clamp)

        # Content box with proper GNOME margins (24px)
        self.content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.clamp.set_child(self.content_box)

        # Set the page as content of toolbar_view
        self.toolbar_view.set_content(self.page)

        # Dictionary to track active conversion processes
        self.active_conversions = {}

        # Counter for conversion IDs
        self.count = 0

    def get_page(self):
        """Return the toolbar_view widget (which contains the page with headerbar)"""
        return self.toolbar_view

    def add_conversion(self, command_title, input_file, process):
        """Add a new conversion to be tracked on the progress page"""
        # Create a unique ID for this conversion
        conversion_id = f"conversion_{self.count}"
        self.count += 1

        # If this is the first conversion, show the progress page
        if len(self.active_conversions) == 0:
            # Switch to progress page and disable tab navigation
            self.app.show_progress_page()

        # Create a box container styled as a card for the conversion item
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        card.add_css_class("card")
        card.set_margin_bottom(12)

        # Create the conversion item
        conversion_item = ConversionItem(
            self.app, command_title, input_file, process, conversion_id
        )

        # Add the item to the card
        card.append(conversion_item)

        # Add the card to the content box
        self.content_box.append(card)

        # Store reference to the item and container
        self.active_conversions[conversion_id] = {
            "item": conversion_item,
            "container": card,
        }

        # Return the item for tracking
        return conversion_item

    def remove_conversion(self, conversion_id):
        """Remove a conversion from the progress page"""
        if conversion_id in self.active_conversions:
            # Get the container
            container = self.active_conversions[conversion_id]["container"]

            # Remove from the UI
            self.content_box.remove(container)

            # Remove from the dictionary
            del self.active_conversions[conversion_id]

            # If no more conversions, return to the previous page
            if len(self.active_conversions) == 0:
                self.app.return_to_previous_page()

    def has_active_conversions(self):
        """Check if there are any active conversions"""
        return len(self.active_conversions) > 0


class ConversionItem(Gtk.Box):
    """Individual conversion item widget to display progress for a single conversion"""

    def __init__(self, app, title, input_file, process, conversion_id):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12)

        self.app = app
        self.process = process
        self.input_file = input_file
        self.delete_original = False
        self.conversion_id = conversion_id
        self.cancelled = False
        self.success = False

        # Remove the command pattern detection as it's now handled in conversion.py
        self.current_encode_mode = _("Unknown")

        self.set_margin_top(12)
        self.set_margin_bottom(12)
        self.set_margin_start(12)
        self.set_margin_end(12)

        # Header with filename
        file_name = title
        if input_file and os.path.exists(input_file):
            file_name = os.path.basename(input_file)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        file_label = Gtk.Label()
        file_label.set_markup(f"<b>{GLib.markup_escape_text(file_name)}</b>")
        file_label.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        file_label.set_halign(Gtk.Align.START)
        file_label.set_hexpand(True)
        header.append(file_label)
        self.append(header)

        # Status row
        status_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        status_box.set_margin_top(4)
        status_box.set_margin_bottom(4)

        # Status label
        self.status_label = Gtk.Label(label=_("Starting conversion..."))
        self.status_label.set_halign(Gtk.Align.START)
        self.status_label.set_hexpand(True)
        self.status_label.set_wrap(True)
        self.status_label.set_xalign(0)
        status_box.append(self.status_label)

        self.append(status_box)

        # Progress bar
        progress_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        progress_box.set_margin_top(4)
        progress_box.set_margin_bottom(8)

        # Create progress bar with proper styling
        self.progress_bar = Gtk.ProgressBar()
        self.progress_bar.set_show_text(True)
        self.progress_bar.set_text("0%")
        self.progress_bar.set_valign(Gtk.Align.CENTER)
        self.progress_bar.set_hexpand(True)
        progress_box.append(self.progress_bar)

        self.append(progress_box)

        # Add CSS styling for command and terminal areas using GNOME/Adwaita guidelines
        # Note: This is safe now because widget creation is always done on the main thread
        css_provider = Gtk.CssProvider()
        css_provider.load_from_data(b"""
            .terminal-text { 
                font-weight: normal;
                background-color: transparent;
            }
            .command-bg {
                background-color: alpha(@secondary_sidebar_bg_color, 1);
                border: 1px solid @borders;
                border-radius: 6px;
            }
            .terminal-bg {
                background-color: alpha(@secondary_sidebar_bg_color, 1);
                border: 1px solid @borders;
                border-radius: 6px;
            }
            /* Add margins to the text content inside ScrolledWindow to prevent scrollbar overlap */
            textview, label {
                margin-right: 8px;
            }
        """)

        display = self.get_display()
        if display is not None:
            Gtk.StyleContext.add_provider_for_display(
                display, css_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )

        # Add FFmpeg command display in an expander
        self.cmd_expander = Gtk.Expander()
        self.cmd_expander.set_label(_("Command"))
        self.append(self.cmd_expander)

        # Command details box
        cmd_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        cmd_box.add_css_class("command-bg")
        cmd_box.set_margin_top(8)
        cmd_box.set_margin_bottom(8)

        # Command text (scrollable for long commands)
        cmd_scroll = Gtk.ScrolledWindow()
        cmd_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        cmd_scroll.set_max_content_height(180)
        cmd_scroll.set_min_content_height(60)

        # Add padding to the scrolled window container
        cmd_scroll.set_margin_start(4)
        cmd_scroll.set_margin_end(4)
        cmd_scroll.set_margin_top(4)
        cmd_scroll.set_margin_bottom(4)

        self.cmd_text = Gtk.Label()
        self.cmd_text.set_text(_("Waiting for command..."))
        self.cmd_text.set_selectable(True)
        self.cmd_text.set_wrap(True)
        self.cmd_text.set_wrap_mode(Pango.WrapMode.CHAR)  # Wrap at character level
        self.cmd_text.set_justify(Gtk.Justification.LEFT)
        self.cmd_text.set_xalign(0)
        self.cmd_text.set_yalign(0)  # Align to top
        self.cmd_text.add_css_class("terminal-text")

        cmd_scroll.set_child(self.cmd_text)
        cmd_box.append(cmd_scroll)

        self.cmd_expander.set_child(cmd_box)

        # Terminal output in an expander
        self.terminal_expander = Gtk.Expander()
        self.terminal_expander.set_label(_("Command Output"))
        self.append(self.terminal_expander)

        # Connect to expanded signal to handle scrolling when opened
        self.terminal_expander.connect("notify::expanded", self._on_terminal_expanded)

        # Terminal output area
        terminal_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        terminal_box.add_css_class("terminal-bg")
        terminal_box.set_margin_start(4)
        terminal_box.set_margin_end(4)
        terminal_box.set_margin_top(4)
        terminal_box.set_margin_bottom(4)

        # Create scrolled window for the terminal
        self.terminal_scroll = Gtk.ScrolledWindow()
        self.terminal_scroll.set_min_content_height(250)

        # Add padding to the terminal scrolled window container
        self.terminal_scroll.set_margin_start(4)
        self.terminal_scroll.set_margin_end(4)
        self.terminal_scroll.set_margin_top(4)
        self.terminal_scroll.set_margin_bottom(4)

        # Create terminal-like TextView with monospace font
        self.terminal_view = Gtk.TextView()
        self.terminal_view.set_editable(False)
        self.terminal_view.set_cursor_visible(False)
        self.terminal_view.set_monospace(True)
        self.terminal_view.add_css_class("terminal-text")

        self.terminal_buffer = self.terminal_view.get_buffer()

        # Add text view to scrolled window
        self.terminal_scroll.set_child(self.terminal_view)
        terminal_box.append(self.terminal_scroll)

        self.terminal_expander.set_child(terminal_box)

        # Flag to track if user is at the bottom of the text view
        self.auto_scroll = True

        # Add a scroll controller to detect when user manually scrolls
        self.vadjustment = self.terminal_scroll.get_vadjustment()
        self.vadjustment.connect("value-changed", self._on_scroll_value_changed)

        # Bottom action area with cancel button
        button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        button_box.set_halign(Gtk.Align.END)
        button_box.set_margin_top(8)

        self.cancel_button = Gtk.Button(label=_("Cancel"))
        self.cancel_button.connect("clicked", self.on_cancel_clicked)
        self.cancel_button.add_css_class("destructive-action")
        self.cancel_button.add_css_class("pill")
        button_box.append(self.cancel_button)

        self.append(button_box)

    def _on_terminal_expanded(self, expander, param):
        """Handle terminal expander state change to scroll to bottom when expanded"""
        if expander.get_expanded():
            # Schedule multiple scroll attempts with increasing delays
            # First attempt - immediate
            self._scroll_terminal_to_bottom()

            # Second attempt - after layout is likely complete (100ms)
            GLib.timeout_add(100, self._scroll_terminal_to_bottom)

            # Third attempt - as a fallback (300ms)
            GLib.timeout_add(300, self._scroll_terminal_to_bottom)

    def _scroll_terminal_to_bottom(self):
        """Scroll terminal to the bottom to show latest output"""
        if (
            not self.terminal_scroll
            or not self.terminal_view
            or not self.terminal_buffer
        ):
            return False

        # Get the end position
        end_iter = self.terminal_buffer.get_end_iter()

        # Create a mark at the end
        end_mark = self.terminal_buffer.create_mark("end", end_iter, False)

        # Scroll to the mark
        self.terminal_view.scroll_to_mark(end_mark, 0.0, False, 0.0, 0.0)

        # Set auto_scroll to True since we're now at the bottom
        self.auto_scroll = True

        # Delete the temporary mark
        self.terminal_buffer.delete_mark(end_mark)

        return False  # Remove the timeout

    def _on_scroll_value_changed(self, adjustment):
        """Detect if user has manually scrolled away from bottom"""
        if (
            adjustment.get_value() + adjustment.get_page_size()
            < adjustment.get_upper() - 10
        ):
            # User has scrolled up (with a 10px threshold for rounding errors)
            self.auto_scroll = False
        else:
            # User is at the bottom
            self.auto_scroll = True

    def add_output_text(self, text):
        """Add text to the terminal view."""
        if not text:
            return

        # Insert text at the end
        end_iter = self.terminal_buffer.get_end_iter()
        self.terminal_buffer.insert(end_iter, text)

        # Only auto-scroll if user is at the bottom
        if self.auto_scroll and self.terminal_expander.get_expanded():
            # Scroll to the bottom on next idle cycle (after rendering)
            GLib.idle_add(self._scroll_to_end_if_needed)

    def _scroll_to_end_if_needed(self):
        """Helper method to scroll to the end only if needed"""
        if self.auto_scroll:
            end_iter = self.terminal_buffer.get_end_iter()
            end_mark = self.terminal_buffer.create_mark("end", end_iter, False)
            self.terminal_view.scroll_to_mark(end_mark, 0.0, False, 0.0, 0.0)
            self.terminal_buffer.delete_mark(end_mark)
        return False  # Remove from idle queue

    def on_cancel_clicked(self, button):
        """Handle cancel button click with simplified process termination"""
        # Set global cancellation flag
        self.app.is_cancellation_requested = True

        # Set local cancelled flag first to prevent error messages
        self.cancelled = True
        print("Cancel button clicked, setting cancelled flag")
        self.cancel_button.set_sensitive(False)

        # Kill process
        if self.process:
            try:
                print(f"Terminating process with PID {self.process.pid}")

                # Use the app's terminate_process_tree method if available
                if hasattr(self.app, "terminate_process_tree"):
                    self.app.terminate_process_tree(self.process)
                else:
                    # Fallback to old termination method
                    self.process.terminate()
                    try:
                        self.process.wait(timeout=1.0)
                        print("Process terminated gracefully")
                    except subprocess.TimeoutExpired:
                        print("Process didn't terminate in time, killing forcefully")
                        self.process.kill()

            except Exception as e:
                print(f"Error killing process: {e}")

        self.status_label.set_text(_("Conversion cancelled"))

    def update_progress(self, fraction, text=None):
        """Update progress bar"""
        self.progress_bar.set_fraction(fraction)
        if text:
            self.progress_bar.set_text(text)
        else:
            self.progress_bar.set_text(f"{int(fraction * 100)}%")

    def update_status(self, status):
        """Update status message text"""
        self.status_label.set_text(status)

    def set_delete_original(self, delete_original):
        """Set whether to delete original file after conversion"""
        self.delete_original = delete_original

    def mark_success(self):
        """Mark conversion as successful and update UI"""
        self.success = True

    def was_cancelled(self):
        """Return whether the conversion was cancelled by the user"""
        return self.cancelled
