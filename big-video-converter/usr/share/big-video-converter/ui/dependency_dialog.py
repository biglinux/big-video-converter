"""
Dialog for installing system packages with VTE terminal.
Adapted from appimage-creator project.
"""

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Vte', '3.91')

from gi.repository import Gtk, Adw, Vte, GLib, Pango

# Setup translation
import gettext
_ = gettext.gettext


class InstallDependencyDialog(Adw.Window):
    """Dialog for installing ffmpeg with a VTE terminal."""

    def __init__(self, parent, install_info):
        super().__init__()
        self.set_transient_for(parent)
        self.set_modal(True)
        self.set_title(_("Required Dependencies"))
        self.set_default_size(700, 500)
        self.set_resizable(True)
        
        self.install_info = install_info
        self.installation_complete = False
        self.installation_success = False
        self.current_command_is_pre = False
        
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_content(main_box)

        header_bar = Adw.HeaderBar()
        main_box.append(header_bar)

        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        content_box.set_margin_top(12)
        content_box.set_margin_bottom(12)
        content_box.set_margin_start(12)
        content_box.set_margin_end(12)
        main_box.append(content_box)

        info_group = Adw.PreferencesGroup()
        info_group.set_title(_("Installation Required"))
        content_box.append(info_group)

        explanation_row = Adw.ActionRow()
        explanation_row.set_title(_("FFmpeg and MPV are required for this application to function."))
        explanation_row.set_subtitle(_("FFmpeg is needed for conversion, and MPV for the video editor preview. Due to legal restrictions, they are not bundled."))
        info_group.add(explanation_row)

        command_row = Adw.ActionRow()
        command_row.set_title(_("Command to be executed"))
        command_row.set_subtitle(install_info['display'])
        info_group.add(command_row)

        warning_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        warning_box.set_margin_top(8)
        warning_box.add_css_class("card")
        
        warning_icon = Gtk.Image.new_from_icon_name('dialog-warning-symbolic')
        warning_icon.set_margin_top(12)
        warning_icon.set_margin_bottom(12)
        warning_icon.set_margin_start(12)
        warning_box.append(warning_icon)
        
        warning_label = Gtk.Label()
        warning_label.set_markup(_("<b>Administrator privileges required</b>\nYou will be asked for your password to install system packages."))
        warning_label.set_halign(Gtk.Align.START)
        warning_label.set_margin_top(12)
        warning_label.set_margin_bottom(12)
        warning_label.set_margin_end(12)
        warning_box.append(warning_label)
        content_box.append(warning_box)

        terminal_group = Adw.PreferencesGroup()
        terminal_group.set_title(_("Installation Output"))
        terminal_group.set_margin_top(12)
        content_box.append(terminal_group)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_vexpand(True)
        scrolled.set_min_content_height(250)
        
        self.terminal = Vte.Terminal()
        self.terminal.set_scroll_on_output(True)
        self.terminal.set_scroll_on_keystroke(True)
        self.terminal.set_mouse_autohide(True)
        
        font_desc = Pango.FontDescription.from_string("Monospace 10")
        self.terminal.set_font(font_desc)
        
        self.terminal.connect("child-exited", self._on_child_exited)
        
        scrolled.set_child(self.terminal)
        terminal_group.add(scrolled)

        button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        button_box.set_halign(Gtk.Align.END)
        button_box.set_margin_top(12)
        content_box.append(button_box)

        self.cancel_button = Gtk.Button(label=_("Cancel"))
        self.cancel_button.connect("clicked", lambda btn: self.close())
        button_box.append(self.cancel_button)

        self.install_button = Gtk.Button(label=_("Install Dependencies"))
        self.install_button.add_css_class("suggested-action")
        self.install_button.connect("clicked", self._on_install_clicked)
        button_box.append(self.install_button)

        self.close_button = Gtk.Button(label=_("Close"))
        self.close_button.set_visible(False)
        self.close_button.connect("clicked", lambda btn: self.close())
        button_box.append(self.close_button)

    def _on_install_clicked(self, button):
        self.install_button.set_sensitive(False)
        self.cancel_button.set_sensitive(False)
        self._write_to_terminal(_("Starting installation...\n\n"))
        
        if 'pre_command' in self.install_info:
            self._run_command(self.install_info['pre_command'], is_pre_command=True)
        else:
            self._run_main_command()

    def _run_command(self, command, is_pre_command=False):
        self.current_command_is_pre = is_pre_command
        command_str = ' '.join(command)
        self._write_to_terminal(f"$ {command_str}\n")
        
        try:
            self.terminal.spawn_async(
                Vte.PtyFlags.DEFAULT, None, command, None, 
                GLib.SpawnFlags.DO_NOT_REAP_CHILD, None, None, -1, None, None, None
            )
        except Exception as e:
            self._write_to_terminal(f"\n{_('Error running command')}: {str(e)}\n")
            self._finish_installation(False)

    def _on_child_exited(self, terminal, exit_status):
        self._write_to_terminal("\n")
        
        if exit_status == 0:
            if self.current_command_is_pre:
                self._write_to_terminal(f"{_('Pre-command completed successfully.')}\n\n")
                self._run_main_command()
            else:
                self._write_to_terminal(f"{_('Installation completed successfully!')}\n")
                self._finish_installation(True)
        else:
            self._write_to_terminal(f"{_('Command failed with exit code')}: {exit_status}\n")
            self._finish_installation(False)

    def _run_main_command(self):
        self._run_command(self.install_info['command'], is_pre_command=False)

    def _write_to_terminal(self, text):
        self.terminal.feed(text.encode('utf-8'))

    def _finish_installation(self, success):
        self.installation_complete = True
        self.installation_success = success
        
        if success:
            self._write_to_terminal("\n" + "="*50 + "\n")
            self._write_to_terminal(_("Installation completed successfully!") + "\n")
            self._write_to_terminal(_("Please restart the application.") + "\n")
            self._write_to_terminal("="*50 + "\n")
        else:
            self._write_to_terminal("\n" + "="*50 + "\n")
            self._write_to_terminal(_("Installation failed.") + "\n")
            self._write_to_terminal(_("Please check the errors above and try again.") + "\n")
            self._write_to_terminal("="*50 + "\n")
            
        self.install_button.set_visible(False)
        self.cancel_button.set_visible(False)
        self.close_button.set_visible(True)