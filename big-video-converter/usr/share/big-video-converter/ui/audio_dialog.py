"""Contextual audio settings; persistent settings remain owned by the app.

A dialog change affects future conversions. Hiding unavailable parameters never
resets their values. Native rows keep names, focus and keyboard interaction.
"""

import gettext

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk

from constants import AUDIO_VALUES
from utils.signal_connections import SignalConnections

_ = gettext.gettext


def _clone_model(source_combo):
    model = source_combo.get_model()
    return Gtk.StringList.new([
        model.get_string(index) for index in range(model.get_n_items())
    ])


def _operation_model():
    """Use concise choices here; the result label explains each consequence."""
    return Gtk.StringList.new([
        _("Keep original audio"),
        _("Convert audio"),
        _("Remove audio"),
    ])


def _make_combo_sync(source, dropdown, connections):
    """Keep both directions scoped to the dialog, including local widgets."""
    def to_source(widget, _pspec):
        selected = widget.get_selected()
        if source.get_selected() != selected:
            source.set_selected(selected)

    def from_source(widget, _pspec):
        selected = widget.get_selected()
        if dropdown.get_selected() != selected:
            dropdown.set_selected(selected)

    connections.connect(dropdown, "notify::selected", to_source)
    connections.connect(source, "notify::selected", from_source)


def _make_entry_sync(source, entry, connections):
    def to_source(widget):
        text = widget.get_text()
        if source.get_text() != text:
            source.set_text(text)

    def from_source(widget):
        text = source.get_text()
        if entry.get_text() != text:
            entry.set_text(text)

    connections.connect(entry, "changed", to_source)
    connections.connect(source, "changed", from_source)


class AudioDialog(Adw.Dialog):
    """Show only audio parameters which can affect the selected operation."""

    def __init__(self, app):
        super().__init__()
        self.app = app
        self.connections = SignalConnections(self)
        self.set_title(_("Audio"))
        # Disable the all-or-nothing natural sizing mode before setting the
        # per-axis policy: a stable width and a natural, content-driven height.
        self.set_follows_content_size(False)
        self.set_content_width(600)
        self.set_content_height(-1)
        self.set_presentation_mode(Adw.DialogPresentationMode.AUTO)

        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(Adw.HeaderBar())
        self.scroll = Gtk.ScrolledWindow()
        self.scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.scroll.set_min_content_width(568)
        self.scroll.set_max_content_height(620)
        self.scroll.set_propagate_natural_height(True)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24)
        for edge in ("start", "end", "top", "bottom"):
            getattr(content, "set_margin_" + edge)(16)

        self.operation_group = Adw.PreferencesGroup()
        self.operation_group.set_description(
            _("Choose what the converted video should contain.")
        )
        self.operation_row = self._combo(
            _("Audio operation"),
            app.audio_handling_combo,
            model=_operation_model(),
        )
        self.operation_group.add(self.operation_row)
        content.append(self.operation_group)

        self.result_label = Gtk.Label(wrap=True, xalign=0)
        self.result_label.set_selectable(True)
        self.result_label.add_css_class("dim-label")
        content.append(self.result_label)

        self.details_group = Adw.PreferencesGroup(title=_("Audio conversion"))
        self.codec_row = self._combo(
            _("Audio Codec"), app.settings_page.audio_codec_combo
        )
        self.bitrate_row = self._combo(
            _("Audio Bitrate"), app.settings_page.audio_bitrate_combo
        )
        self.channels_row = self._combo(
            _("Audio Channels"), app.settings_page.audio_channels_combo
        )
        self.custom_bitrate_row = self._entry(
            _("Custom bitrate"), app.settings_page.custom_bitrate_row
        )
        self.custom_channels_row = self._entry(
            _("Custom channels"), app.settings_page.custom_channels_row
        )
        for row in (
            self.codec_row, self.bitrate_row, self.custom_bitrate_row,
            self.channels_row, self.custom_channels_row,
        ):
            self.details_group.add(row)
        content.append(self.details_group)

        note = Gtk.Label(
            label=_("Changes are saved automatically for future conversions."),
            wrap=True, xalign=0,
        )
        note.add_css_class("caption")
        note.add_css_class("dim-label")
        content.append(note)
        for row in (self.operation_row, self.bitrate_row, self.channels_row):
            self.connections.connect(row, "notify::selected", self._update_state)
        self._update_state()
        self.scroll.set_child(content)
        toolbar.set_content(self.scroll)
        self.set_child(toolbar)

    def _combo(self, title, source, model=None):
        row_model = model if model is not None else _clone_model(source)
        row = Adw.ComboRow(title=title, model=row_model)
        row.set_selected(source.get_selected())
        row.set_title_lines(0)
        _make_combo_sync(source, row, self.connections)
        return row

    def _entry(self, title, source):
        row = Adw.EntryRow(title=title)
        row.set_text(source.get_text())
        _make_entry_sync(source, row, self.connections)
        return row

    def _update_state(self, *_args):
        mode = AUDIO_VALUES.get(self.operation_row.get_selected())
        reencode = mode == "reencode"
        self.details_group.set_visible(reencode)
        self.custom_bitrate_row.set_visible(
            reencode and self.bitrate_row.get_selected()
            == len(self.app.settings_page.bitrate_values) - 1
        )
        self.custom_channels_row.set_visible(
            reencode and self.channels_row.get_selected()
            == len(self.app.settings_page.channels_values) - 1
        )
        messages = {
            "copy": _(
                "Original audio is kept. Codec, bitrate and channel settings "
                "do not apply."
            ),
            "reencode": _("Audio will be converted using the settings below."),
            "none": _("The converted video will have no audio."),
        }
        self.result_label.set_label(
            messages.get(mode, _("Select an audio operation."))
        )


def show_audio_dialog(parent_window, app) -> None:
    AudioDialog(app).present(parent_window)
