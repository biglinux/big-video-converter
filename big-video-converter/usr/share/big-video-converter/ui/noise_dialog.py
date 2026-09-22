"""Audio-cleaning dialog with causal, progressively disclosed controls.

Every feature keeps its saved values while inactive.  The dialog mirrors the
existing data-holder widgets used by the conversion backend and never resets a
preference merely because its processing mode is temporarily unavailable.
"""

from __future__ import annotations

import gettext

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk

from utils.signal_connections import SignalConnections

_ = gettext.gettext


def _clone_model(source_combo) -> Gtk.StringList:
    source = source_combo.get_model()
    result = Gtk.StringList()
    for index in range(source.get_n_items()):
        result.append(source.get_string(index))
    return result


def _sync_combo(source, target, connections: SignalConnections) -> None:
    def target_changed(row, _pspec):
        selected = row.get_selected()
        if source.get_selected() != selected:
            source.set_selected(selected)

    def source_changed(row, _pspec):
        selected = row.get_selected()
        if target.get_selected() != selected:
            target.set_selected(selected)

    target.connect("notify::selected", target_changed)
    connections.connect(source, "notify::selected", source_changed)


def _sync_active(source, target, connections: SignalConnections) -> None:
    target.set_active(source.get_active())

    def target_changed(row, _pspec):
        active = row.get_active()
        if source.get_active() != active:
            source.set_active(active)

    def source_changed(row, _pspec):
        active = row.get_active()
        if target.get_active() != active:
            target.set_active(active)

    target.connect("notify::active", target_changed)
    connections.connect(source, "notify::active", source_changed)


def _slider_row(
    title: str,
    subtitle: str,
    source_adjustment,
    connections: SignalConnections,
    formatter=None,
):
    local = Gtk.Adjustment(
        value=source_adjustment.get_value(),
        lower=source_adjustment.get_lower(),
        upper=source_adjustment.get_upper(),
        step_increment=source_adjustment.get_step_increment(),
        page_increment=source_adjustment.get_page_increment(),
    )
    row = Adw.ActionRow(title=title, subtitle=subtitle)
    scale = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL, adjustment=local)
    scale.set_draw_value(False)
    scale.set_hexpand(True)
    scale.set_size_request(230, -1)
    scale.set_valign(Gtk.Align.CENTER)
    scale.update_property([Gtk.AccessibleProperty.LABEL], [title])

    value = Gtk.Label()
    value.add_css_class("numeric")
    value.add_css_class("caption")
    value.set_width_chars(7)
    value.set_xalign(1)

    if formatter is None:
        if local.get_lower() >= 0 and local.get_upper() <= 1.01:
            formatter = lambda number: f"{number * 100:.0f}%"
        else:
            formatter = lambda number: f"{number:.0f}"

    def update_label(adjustment, _pspec=None):
        value.set_text(formatter(adjustment.get_value()))

    def local_changed(adjustment, _pspec):
        number = adjustment.get_value()
        if abs(source_adjustment.get_value() - number) > 0.0001:
            source_adjustment.set_value(number)
        update_label(adjustment)

    def source_changed(adjustment, _pspec):
        number = adjustment.get_value()
        if abs(local.get_value() - number) > 0.0001:
            local.set_value(number)

    local.connect("notify::value", local_changed)
    connections.connect(source_adjustment, "notify::value", source_changed)
    update_label(local)

    row.add_suffix(scale)
    row.add_suffix(value)
    row.set_activatable_widget(scale)
    return row, local


def _switch_row(title: str, subtitle: str, source, connections):
    row = Adw.SwitchRow(title=title, subtitle=subtitle)
    _sync_active(source, row, connections)
    return row


def show_noise_dialog(parent_window, app) -> bool:
    """Present cleaning tools with explicit applicability and saved-state rules."""

    dialog = Adw.Dialog()
    connections = SignalConnections(dialog)
    dialog.set_title(_("Audio cleaning"))
    dialog.set_content_width(720)
    dialog.set_content_height(760)
    dialog.set_presentation_mode(Adw.DialogPresentationMode.FLOATING)

    toolbar = Adw.ToolbarView()
    toolbar.add_top_bar(Adw.HeaderBar())

    scroll = Gtk.ScrolledWindow()
    scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    clamp = Adw.Clamp()
    clamp.set_maximum_size(720)
    content = Gtk.Box(
        orientation=Gtk.Orientation.VERTICAL,
        spacing=18,
        margin_top=18,
        margin_bottom=24,
        margin_start=18,
        margin_end=18,
    )
    clamp.set_child(content)

    intro = Gtk.Label(
        label=_(
            "Enable only the processing your recording needs. Inactive tools "
            "keep their saved values and do not affect the output."
        ),
        wrap=True,
        xalign=0,
    )
    intro.add_css_class("dim-label")
    content.append(intro)

    availability_group = Adw.PreferencesGroup()
    availability_row = Adw.ActionRow()
    availability_icon = Gtk.Image.new_from_icon_name("dialog-information-symbolic")
    availability_icon.set_pixel_size(18)
    availability_row.add_prefix(availability_icon)
    availability_group.add(availability_row)
    content.append(availability_group)

    ai_group = Adw.PreferencesGroup()
    ai_group.set_title(_("AI noise removal"))
    ai_group.set_description(
        _("Separate voice from continuous background noise such as fans or traffic.")
    )
    ai_switch = _switch_row(
        _("Remove background noise"),
        _("Uses a neural model; enable it only when the source contains noise"),
        app.noise_reduction_switch,
        connections,
    )
    ai_group.add(ai_switch)
    content.append(ai_group)

    ai_details = Adw.PreferencesGroup()
    ai_details.set_title(_("Noise-removal details"))
    ai_details.set_description(
        _("These values are preserved while noise removal is turned off.")
    )

    strength_row, _strength = _slider_row(
        _("Strength"),
        _("How aggressively background noise is removed"),
        app.noise_strength_adj,
        connections,
    )
    ai_details.add(strength_row)

    model_row = Adw.ComboRow(
        title=_("AI model"),
        subtitle=_("Choose maximum cleaning, natural voice or a balanced combination"),
        model=Gtk.StringList.new(app._noise_model_list),
    )
    model_row.set_selected(app.noise_model_row.get_selected())
    _sync_combo(app.noise_model_row, model_row, connections)
    ai_details.add(model_row)

    speech_row, _speech = _slider_row(
        _("Speech protection"),
        _("Preserve voice components that may resemble background noise"),
        app.noise_speech_strength_adj,
        connections,
    )
    ai_details.add(speech_row)

    lookahead_row, _lookahead = _slider_row(
        _("Lookahead"),
        _("Additional analysis time used before processing each moment"),
        app.noise_lookahead_adj,
        connections,
        formatter=lambda number: f"{number:.0f} ms",
    )
    ai_details.add(lookahead_row)

    recovery_row, _recovery = _slider_row(
        _("Voice recovery"),
        _("Restore natural voice detail after stronger cleaning"),
        app.noise_voice_recovery_adj,
        connections,
    )
    ai_details.add(recovery_row)
    content.append(ai_details)

    gate_group = Adw.PreferencesGroup()
    gate_group.set_title(_("Quiet moments"))
    gate_switch = _switch_row(
        _("Silence low sounds"),
        _("Mute faint room noise between words and other intentional sounds"),
        app.gate_switch,
        connections,
    )
    gate_group.add(gate_switch)
    gate_intensity_row, _gate = _slider_row(
        _("Gate intensity"),
        _("Higher values silence more low-level sound"),
        app.gate_intensity_adj,
        connections,
    )
    gate_group.add(gate_intensity_row)
    content.append(gate_group)

    enhancement_group = Adw.PreferencesGroup()
    enhancement_group.set_title(_("Voice and loudness"))
    enhancement_group.set_description(
        _("Optional corrections for rumble, changing volume and overall consistency.")
    )

    hpf_switch = _switch_row(
        _("Remove low rumble"),
        _("Reduce wind, handling vibration and air-conditioning noise"),
        app.hpf_row,
        connections,
    )
    enhancement_group.add(hpf_switch)
    hpf_frequency_row, _hpf = _slider_row(
        _("Low-cut frequency"),
        _("Frequencies below this point are reduced"),
        app.hpf_freq_adj,
        connections,
        formatter=lambda number: f"{number:.0f} Hz",
    )
    enhancement_group.add(hpf_frequency_row)

    compressor_switch = _switch_row(
        _("Balance loud and quiet parts"),
        _("Reduce peaks and make softer speech easier to hear"),
        app.compressor_switch,
        connections,
    )
    enhancement_group.add(compressor_switch)
    compressor_row, _compressor = _slider_row(
        _("Compression intensity"),
        _("How strongly the difference between loud and quiet parts is reduced"),
        app.compressor_intensity_adj,
        connections,
    )
    enhancement_group.add(compressor_row)

    normalize_switch = _switch_row(
        _("Uniform final volume"),
        _("Adjust the completed audio toward broadcast and streaming loudness"),
        app.normalize_row,
        connections,
    )
    enhancement_group.add(normalize_switch)
    content.append(enhancement_group)

    equalizer_group = Adw.PreferencesGroup()
    equalizer_group.set_title(_("Equalizer"))
    equalizer_group.set_description(
        _("Use a named preset first; open the frequency bands only for fine tuning.")
    )

    eq_switch = _switch_row(
        _("Shape the sound"),
        _("Adjust clarity, warmth, sibilance and low-frequency energy"),
        app.eq_switch,
        connections,
    )
    equalizer_group.add(eq_switch)

    eq_preset = Adw.ComboRow(
        title=_("Equalizer preset"),
        subtitle=_("A safe starting point for common speech and music tasks"),
        model=_clone_model(app.eq_preset_row),
    )
    eq_preset.set_selected(app.eq_preset_row.get_selected())
    _sync_combo(app.eq_preset_row, eq_preset, connections)
    equalizer_group.add(eq_preset)

    bands_expander = Adw.ExpanderRow(
        title=_("Frequency bands"),
        subtitle=_("Fine tune ten bands from 31 Hz to 16 kHz"),
    )
    bands_expander.set_expanded(False)
    equalizer_group.add(bands_expander)

    frequencies = [31, 63, 125, 250, 500, 1000, 2000, 4000, 8000, 16000]
    saved_text = app.settings_manager.load_setting(
        "eq-bands", "0,0,0,0,0,0,0,0,0,0"
    )
    try:
        saved = [float(value) for value in saved_text.split(",")]
    except (AttributeError, ValueError):
        saved = [0.0] * 10
    if len(saved) != 10:
        saved = [0.0] * 10

    band_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
    band_box.set_halign(Gtk.Align.CENTER)
    scales = []
    value_labels = []
    guard = {"active": False}

    def save_bands(scale, index):
        if guard["active"]:
            return
        value_labels[index].set_text(f"{scale.get_value():+.0f}")
        if eq_preset.get_selected() != 9:
            guard["active"] = True
            eq_preset.set_selected(9)
            app.eq_preset_row.set_selected(9)
            guard["active"] = False
        values = [item.get_value() for item in scales]
        app.settings_manager.save_setting(
            "eq-bands", ",".join(str(value) for value in values)
        )
        if hasattr(app, "video_edit_page") and app.video_edit_page:
            app.video_edit_page._apply_audio_filters()

    for index, frequency in enumerate(frequencies):
        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        column.set_size_request(42, -1)
        value_label = Gtk.Label(label=f"{saved[index]:+.0f}")
        value_label.add_css_class("numeric")
        value_label.add_css_class("caption")
        column.append(value_label)
        value_labels.append(value_label)

        adjustment = Gtk.Adjustment(
            value=saved[index],
            lower=-40.0,
            upper=40.0,
            step_increment=0.5,
            page_increment=2.0,
        )
        scale = Gtk.Scale(
            orientation=Gtk.Orientation.VERTICAL,
            adjustment=adjustment,
        )
        scale.set_inverted(True)
        scale.set_draw_value(False)
        scale.set_size_request(-1, 120)
        label = f"{frequency // 1000} kHz" if frequency >= 1000 else f"{frequency} Hz"
        scale.update_property([Gtk.AccessibleProperty.LABEL], [_("Equalizer {0}").format(label)])
        scale.connect("value-changed", lambda item, idx=index: save_bands(item, idx))
        column.append(scale)
        scales.append(scale)

        frequency_label = Gtk.Label(label=(f"{frequency // 1000}k" if frequency >= 1000 else str(frequency)))
        frequency_label.add_css_class("caption")
        frequency_label.add_css_class("dim-label")
        column.append(frequency_label)
        band_box.append(column)

    bands_expander.add_row(band_box)
    content.append(equalizer_group)

    def load_equalizer_preset(*_args):
        if guard["active"]:
            return
        index = eq_preset.get_selected()
        if index >= len(app._eq_preset_keys):
            return
        key = app._eq_preset_keys[index]
        values = app._eq_presets.get(key, [0.0] * 10)
        guard["active"] = True
        try:
            for band_index, scale in enumerate(scales):
                scale.set_value(values[band_index])
                value_labels[band_index].set_text(f"{values[band_index]:+.0f}")
        finally:
            guard["active"] = False

    eq_preset.connect("notify::selected", load_equalizer_preset)

    def update_applicability(*_args):
        audio_available = app.audio_handling_combo.get_selected() == 1
        if audio_available:
            availability_row.set_title(_("Audio cleaning will be applied"))
            availability_row.set_subtitle(
                _("Only enabled tools below will change the final audio.")
            )
        else:
            availability_row.set_title(_("Audio cleaning is currently unavailable"))
            availability_row.set_subtitle(
                _(
                    "Choose “Re-encode audio” in Audio to apply these tools. "
                    "Your saved values are preserved."
                )
            )

        ai_group.set_sensitive(audio_available)
        ai_details.set_sensitive(audio_available and ai_switch.get_active())
        gate_group.set_sensitive(audio_available)
        gate_intensity_row.set_sensitive(audio_available and gate_switch.get_active())
        enhancement_group.set_sensitive(audio_available)
        hpf_frequency_row.set_sensitive(audio_available and hpf_switch.get_active())
        compressor_row.set_sensitive(audio_available and compressor_switch.get_active())
        equalizer_group.set_sensitive(audio_available)
        eq_preset.set_sensitive(audio_available and eq_switch.get_active())
        bands_expander.set_sensitive(audio_available and eq_switch.get_active())

    for control in (
        ai_switch,
        gate_switch,
        hpf_switch,
        compressor_switch,
        eq_switch,
    ):
        control.connect("notify::active", update_applicability)
    connections.connect(
        app.audio_handling_combo,
        "notify::selected",
        update_applicability,
    )
    update_applicability()

    scroll.set_child(clamp)
    toolbar.set_content(scroll)
    dialog.set_child(toolbar)
    dialog.present(parent_window)
    return True
