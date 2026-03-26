"""
Educational dialog for audio noise reduction options.
Groups controls into illustrative cards: AI cleaning, noise gate, audio enhancement.
"""

import gettext
import os

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk

_ = gettext.gettext

_ILLUSTRATIONS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "icons",
    "illustrations",
)


def _svg(filename: str, size: int = 92) -> Gtk.Picture:
    path = os.path.join(_ILLUSTRATIONS_DIR, filename)
    pic = Gtk.Picture.new_for_filename(path)
    pic.set_size_request(size, int(size * 80 / 120))
    pic.set_can_shrink(True)
    pic.set_content_fit(Gtk.ContentFit.CONTAIN)
    return pic


def _section_header(title: str, margin_top: int = 0) -> Gtk.Label:
    lbl = Gtk.Label(label=title)
    lbl.add_css_class("title-3")
    lbl.set_halign(Gtk.Align.START)
    lbl.set_margin_top(margin_top)
    lbl.set_margin_bottom(8)
    return lbl


def _card_box() -> Gtk.Box:
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
    box.add_css_class("card")
    box.set_margin_bottom(12)
    return box


def _card_header(svg_file: str, title: str, desc: str, control: Gtk.Widget) -> Gtk.Box:
    """Reusable top row: [SVG | title+desc | control]."""
    row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
    row.set_margin_top(16)
    row.set_margin_bottom(12)
    row.set_margin_start(16)
    row.set_margin_end(16)

    pic = _svg(svg_file)
    pic.set_valign(Gtk.Align.CENTER)
    row.append(pic)

    text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    text.set_hexpand(True)
    text.set_valign(Gtk.Align.CENTER)

    t = Gtk.Label(label=title)
    t.add_css_class("heading")
    t.set_halign(Gtk.Align.START)
    t.set_wrap(True)
    text.append(t)

    d = Gtk.Label(label=desc)
    d.set_wrap(True)
    d.set_xalign(0)
    d.add_css_class("dim-label")
    text.append(d)

    row.append(text)

    control.set_valign(Gtk.Align.CENTER)
    row.append(control)

    return row


def _slider_row(label: str, adj: Gtk.Adjustment, format_func=None) -> Gtk.Box:
    """Simple labelled slider packaged in a horizontal box.
    
    If format_func is provided, it receives (value) and returns a string.
    Otherwise, 0–1 ranges auto-format as percentage.
    """
    row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
    row.set_margin_start(16)
    row.set_margin_end(16)
    row.set_margin_bottom(12)

    lbl = Gtk.Label(label=label)
    lbl.set_xalign(0)
    lbl.set_size_request(120, -1)
    row.append(lbl)

    scale = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL, adjustment=adj)
    scale.set_hexpand(True)
    scale.set_valign(Gtk.Align.CENTER)

    # Value label on the right side
    val_label = Gtk.Label()
    val_label.set_size_request(55, -1)
    val_label.set_xalign(1.0)
    val_label.add_css_class("numeric")
    val_label.add_css_class("caption")

    # Determine formatter
    if format_func is None:
        if adj.get_upper() <= 1.01 and adj.get_lower() >= -0.01:
            def format_func(v):
                return f"{v * 100:.0f}%"
            scale.set_digits(2)
        elif adj.get_lower() < 0:
            def format_func(v):
                return f"{abs(v) * 100:.0f}%"
            scale.set_digits(1)
        else:
            def format_func(v):
                return f"{v:.0f}"
            scale.set_digits(0)
    else:
        scale.set_digits(2 if adj.get_upper() <= 1.0 else 0)

    # Set initial value
    val_label.set_text(format_func(adj.get_value()))

    # Update label when value changes
    def _on_value_changed(adj, _pspec, fmt=format_func):
        val_label.set_text(fmt(adj.get_value()))

    adj.connect("notify::value", _on_value_changed)

    scale.set_draw_value(False)
    row.append(scale)
    row.append(val_label)

    return row


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def show_noise_dialog(parent_window, app) -> bool:
    """Present the educational noise-cleaning dialog."""
    dialog = Adw.Dialog()
    dialog.set_title(_("Audio Settings"))
    dialog.set_content_width(700)
    dialog.set_content_height(920)
    dialog.set_presentation_mode(Adw.DialogPresentationMode.FLOATING)

    toolbar = Adw.ToolbarView()
    toolbar.add_top_bar(Adw.HeaderBar())

    scroll = Gtk.ScrolledWindow()
    scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

    content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
    content.set_margin_start(24)
    content.set_margin_end(24)
    content.set_margin_top(12)
    content.set_margin_bottom(24)

    intro = Gtk.Label()
    intro.set_text(
        _(
            "Improve your audio by removing noise, controlling volume "
            "and enhancing voice clarity."
        )
    )
    intro.set_wrap(True)
    intro.set_xalign(0)
    intro.set_margin_bottom(20)
    intro.add_css_class("dim-label")
    content.append(intro)

    # ===== Section 1: AI Noise Cleaning =====
    content.append(_section_header(_("AI Noise Cleaning")))

    # Main switch card
    nr_switch = Gtk.Switch(valign=Gtk.Align.CENTER)
    nr_switch.set_active(app.noise_reduction_switch.get_active())

    card1 = _card_box()
    card1.append(
        _card_header(
            "noise_reduction.svg",
            _("Remove Background Noise"),
            _(
                "Uses a neural network to separate voice from noise. "
                "Strength controls how aggressively noise is removed."
            ),
            nr_switch,
        )
    )

    # Strength slider — synced with sidebar
    strength_adj = Gtk.Adjustment(
        value=app.noise_strength_adj.get_value(),
        lower=0.0, upper=1.0,
        step_increment=0.05, page_increment=0.1,
    )
    card1.append(_slider_row(_("Strength"), strength_adj))

    def _sync_strength_to_sidebar(adj, _pspec):
        v = adj.get_value()
        if abs(app.noise_strength_adj.get_value() - v) > 0.001:
            app.noise_strength_adj.set_value(v)

    def _sync_strength_from_sidebar(adj, _pspec):
        v = adj.get_value()
        if abs(strength_adj.get_value() - v) > 0.001:
            strength_adj.set_value(v)

    strength_adj.connect("notify::value", _sync_strength_to_sidebar)
    app.noise_strength_adj.connect("notify::value", _sync_strength_from_sidebar)

    # AI Model dropdown
    model_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
    model_box.set_margin_start(16)
    model_box.set_margin_end(16)
    model_box.set_margin_bottom(12)

    model_lbl = Gtk.Label(label=_("AI Model"))
    model_lbl.set_xalign(0)
    model_lbl.set_size_request(120, -1)
    model_box.append(model_lbl)

    model_dd = Gtk.DropDown()
    model_list = Gtk.StringList()
    for item in app._noise_model_list:
        model_list.append(item)
    model_dd.set_model(model_list)
    model_dd.set_selected(app.noise_model_row.get_selected())
    model_dd.set_hexpand(True)
    model_box.append(model_dd)
    card1.append(model_box)

    def _sync_model_to_sidebar(dd, _pspec):
        sel = dd.get_selected()
        if app.noise_model_row.get_selected() != sel:
            app.noise_model_row.set_selected(sel)

    def _sync_model_from_sidebar(row, _pspec):
        sel = row.get_selected()
        if model_dd.get_selected() != sel:
            model_dd.set_selected(sel)

    model_dd.connect("notify::selected", _sync_model_to_sidebar)
    app.noise_model_row.connect("notify::selected", _sync_model_from_sidebar)

    # Speech Strength slider
    speech_adj = Gtk.Adjustment(
        value=app.noise_speech_strength_adj.get_value(),
        lower=0.0, upper=1.0,
        step_increment=0.05, page_increment=0.1,
    )
    card1.append(_slider_row(_("Speech Strength"), speech_adj))

    def _sync_speech_to(adj, _p):
        v = adj.get_value()
        if abs(app.noise_speech_strength_adj.get_value() - v) > 0.001:
            app.noise_speech_strength_adj.set_value(v)

    def _sync_speech_from(adj, _p):
        v = adj.get_value()
        if abs(speech_adj.get_value() - v) > 0.001:
            speech_adj.set_value(v)

    speech_adj.connect("notify::value", _sync_speech_to)
    app.noise_speech_strength_adj.connect("notify::value", _sync_speech_from)

    # Lookahead slider
    look_adj = Gtk.Adjustment(
        value=app.noise_lookahead_adj.get_value(),
        lower=0, upper=200,
        step_increment=5, page_increment=20,
    )
    card1.append(_slider_row(_("Lookahead (ms)"), look_adj,
                              format_func=lambda v: f"{v:.0f} ms"))

    def _sync_look_to(adj, _p):
        v = adj.get_value()
        if abs(app.noise_lookahead_adj.get_value() - v) > 0.5:
            app.noise_lookahead_adj.set_value(v)

    def _sync_look_from(adj, _p):
        v = adj.get_value()
        if abs(look_adj.get_value() - v) > 0.5:
            look_adj.set_value(v)

    look_adj.connect("notify::value", _sync_look_to)
    app.noise_lookahead_adj.connect("notify::value", _sync_look_from)

    # Voice Recovery slider
    vr_adj = Gtk.Adjustment(
        value=app.noise_voice_recovery_adj.get_value(),
        lower=0.0, upper=1.0,
        step_increment=0.05, page_increment=0.1,
    )
    card1.append(_slider_row(_("Voice Recovery"), vr_adj))

    def _sync_vr_to(adj, _p):
        v = adj.get_value()
        if abs(app.noise_voice_recovery_adj.get_value() - v) > 0.001:
            app.noise_voice_recovery_adj.set_value(v)

    def _sync_vr_from(adj, _p):
        v = adj.get_value()
        if abs(vr_adj.get_value() - v) > 0.001:
            vr_adj.set_value(v)

    vr_adj.connect("notify::value", _sync_vr_to)
    app.noise_voice_recovery_adj.connect("notify::value", _sync_vr_from)

    content.append(card1)

    # Sync main switch
    def _sync_nr_switch(sw, state):
        if app.noise_reduction_switch.get_active() != state:
            app.noise_reduction_switch.set_active(state)
        return False

    def _sync_nr_from_sidebar(sw, _pspec):
        active = sw.get_active()
        if nr_switch.get_active() != active:
            nr_switch.set_active(active)

    nr_switch.connect("state-set", _sync_nr_switch)
    app.noise_reduction_switch.connect("notify::active", _sync_nr_from_sidebar)

    # ===== Section 2: Noise Gate =====
    content.append(_section_header(_("Noise Gate"), margin_top=16))

    gate_switch = Gtk.Switch(valign=Gtk.Align.CENTER)
    gate_switch.set_active(app.gate_switch.get_active())

    card2 = _card_box()
    card2.append(
        _card_header(
            "noise_gate.svg",
            _("Silence Low Sounds"),
            _(
                "Mutes audio below a threshold — great for removing "
                "faint hum or room noise between speech."
            ),
            gate_switch,
        )
    )

    gate_adj = Gtk.Adjustment(
        value=app.gate_intensity_adj.get_value(),
        lower=0.0, upper=1.0,
        step_increment=0.05, page_increment=0.1,
    )
    card2.append(_slider_row(_("Intensity"), gate_adj))

    def _sync_gate_adj(adj, _pspec):
        v = adj.get_value()
        if abs(app.gate_intensity_adj.get_value() - v) > 0.001:
            app.gate_intensity_adj.set_value(v)

    def _sync_gate_adj_back(adj, _pspec):
        v = adj.get_value()
        if abs(gate_adj.get_value() - v) > 0.001:
            gate_adj.set_value(v)

    gate_adj.connect("notify::value", _sync_gate_adj)
    app.gate_intensity_adj.connect("notify::value", _sync_gate_adj_back)

    def _sync_gate_switch(sw, state):
        if app.gate_switch.get_active() != state:
            app.gate_switch.set_active(state)
        return False

    def _sync_gate_from(sw, _pspec):
        if gate_switch.get_active() != sw.get_active():
            gate_switch.set_active(sw.get_active())

    gate_switch.connect("state-set", _sync_gate_switch)
    app.gate_switch.connect("notify::active", _sync_gate_from)

    content.append(card2)

    # ===== Section 3: Audio Enhancement =====
    content.append(_section_header(_("Audio Enhancement"), margin_top=16))

    # HPF card
    hpf_switch = Gtk.Switch(valign=Gtk.Align.CENTER)
    hpf_switch.set_active(app.hpf_row.get_active())

    card3 = _card_box()
    card3.append(
        _card_header(
            "hpf_rumble.svg",
            _("Remove Low Rumble"),
            _(
                "Cuts low-frequency noise like wind, air conditioning "
                "or handling vibrations."
            ),
            hpf_switch,
        )
    )

    hpf_adj = Gtk.Adjustment(
        value=app.hpf_freq_adj.get_value(),
        lower=20, upper=500,
        step_increment=5, page_increment=20,
    )
    card3.append(_slider_row(_("Frequency (Hz)"), hpf_adj,
                              format_func=lambda v: f"{v:.0f} Hz"))

    def _sync_hpf_adj(adj, _pspec):
        v = adj.get_value()
        if abs(app.hpf_freq_adj.get_value() - v) > 0.5:
            app.hpf_freq_adj.set_value(v)

    def _sync_hpf_adj_back(adj, _pspec):
        v = adj.get_value()
        if abs(hpf_adj.get_value() - v) > 0.5:
            hpf_adj.set_value(v)

    hpf_adj.connect("notify::value", _sync_hpf_adj)
    app.hpf_freq_adj.connect("notify::value", _sync_hpf_adj_back)

    def _sync_hpf_switch(sw, state):
        if app.hpf_row.get_active() != state:
            app.hpf_row.set_active(state)
        return False

    def _sync_hpf_from(row, _pspec):
        if hpf_switch.get_active() != row.get_active():
            hpf_switch.set_active(row.get_active())

    hpf_switch.connect("state-set", _sync_hpf_switch)
    app.hpf_row.connect("notify::active", _sync_hpf_from)

    content.append(card3)

    # Normalize card
    norm_switch = Gtk.Switch(valign=Gtk.Align.CENTER)
    norm_switch.set_active(app.normalize_row.get_active())

    card4 = _card_box()
    card4.append(
        _card_header(
            "uniform_volume.svg",
            _("Uniform Volume"),
            _(
                "Adjusts the volume so the entire video sounds consistent, "
                "following TV and streaming standards."
            ),
            norm_switch,
        )
    )
    content.append(card4)

    def _sync_norm_switch(sw, state):
        if app.normalize_row.get_active() != state:
            app.normalize_row.set_active(state)
        return False

    def _sync_norm_from(row, _pspec):
        if norm_switch.get_active() != row.get_active():
            norm_switch.set_active(row.get_active())

    norm_switch.connect("state-set", _sync_norm_switch)
    app.normalize_row.connect("notify::active", _sync_norm_from)

    # Compressor card
    comp_switch = Gtk.Switch(valign=Gtk.Align.CENTER)
    comp_switch.set_active(app.compressor_switch.get_active())

    card6 = _card_box()
    card6.append(
        _card_header(
            "compressor.svg",
            _("Compressor"),
            _(
                "Reduces loud peaks and raises quiet parts, "
                "making the overall volume more consistent."
            ),
            comp_switch,
        )
    )

    comp_adj = Gtk.Adjustment(
        value=app.compressor_intensity_adj.get_value(),
        lower=0.0, upper=1.0,
        step_increment=0.05, page_increment=0.1,
    )
    card6.append(_slider_row(_("Intensity"), comp_adj))

    def _sync_comp_adj(adj, _p):
        v = adj.get_value()
        if abs(app.compressor_intensity_adj.get_value() - v) > 0.001:
            app.compressor_intensity_adj.set_value(v)

    def _sync_comp_adj_back(adj, _p):
        v = adj.get_value()
        if abs(comp_adj.get_value() - v) > 0.001:
            comp_adj.set_value(v)

    comp_adj.connect("notify::value", _sync_comp_adj)
    app.compressor_intensity_adj.connect("notify::value", _sync_comp_adj_back)

    def _sync_comp_switch(sw, state):
        if app.compressor_switch.get_active() != state:
            app.compressor_switch.set_active(state)
        return False

    def _sync_comp_from(sw, _pspec):
        if comp_switch.get_active() != sw.get_active():
            comp_switch.set_active(sw.get_active())

    comp_switch.connect("state-set", _sync_comp_switch)
    app.compressor_switch.connect("notify::active", _sync_comp_from)

    content.append(card6)

    # Equalizer card
    eq_switch = Gtk.Switch(valign=Gtk.Align.CENTER)
    eq_switch.set_active(app.eq_switch.get_active())

    card7 = _card_box()
    card7.append(
        _card_header(
            "equalizer.svg",
            _("Equalizer"),
            _(
                "Shape the sound spectrum: boost clarity, cut rumble "
                "or apply ready-made presets like Podcast or De-esser."
            ),
            eq_switch,
        )
    )

    # EQ content wrapper (visible only when switch is active)
    eq_content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
    eq_content.set_visible(app.eq_switch.get_active())

    # EQ Preset dropdown
    eq_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
    eq_box.set_margin_start(16)
    eq_box.set_margin_end(16)
    eq_box.set_margin_bottom(12)

    eq_lbl = Gtk.Label(label=_("Preset"))
    eq_lbl.set_xalign(0)
    eq_lbl.set_size_request(120, -1)
    eq_box.append(eq_lbl)

    eq_dd = Gtk.DropDown()
    eq_list = Gtk.StringList()
    for j in range(app.eq_preset_row.get_model().get_n_items()):
        eq_list.append(app.eq_preset_row.get_model().get_string(j))
    eq_dd.set_model(eq_list)
    eq_dd.set_selected(app.eq_preset_row.get_selected())
    eq_dd.set_hexpand(True)
    eq_box.append(eq_dd)
    eq_content.append(eq_box)

    # ── EQ Band Sliders (10 bands) ──
    EQ_FREQS = [31, 63, 125, 250, 500, 1000, 2000, 4000, 8000, 16000]
    _eq_slider_guard = {"active": False}

    # Load saved band values
    saved_bands_str = app.settings_manager.load_setting("eq-bands", "0,0,0,0,0,0,0,0,0,0")
    try:
        saved_bands = [float(v) for v in saved_bands_str.split(",")]
    except (ValueError, AttributeError):
        saved_bands = [0.0] * 10
    if len(saved_bands) != 10:
        saved_bands = [0.0] * 10

    bands_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
    bands_container.set_margin_start(16)
    bands_container.set_margin_end(16)
    bands_container.set_margin_bottom(12)

    sliders_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
    sliders_box.set_halign(Gtk.Align.CENTER)

    eq_scales: list[Gtk.Scale] = []
    eq_value_labels: list[Gtk.Label] = []

    def _on_eq_band_changed(scale: Gtk.Scale, idx: int) -> None:
        if _eq_slider_guard["active"]:
            return
        val = scale.get_value()
        eq_value_labels[idx].set_text(f"{val:+.0f}")
        # Switch preset to Custom (index 9)
        if eq_dd.get_selected() != 9:
            _eq_slider_guard["active"] = True
            eq_dd.set_selected(9)
            app.eq_preset_row.set_selected(9)
            _eq_slider_guard["active"] = False
        # Save all band values
        bands = [s.get_value() for s in eq_scales]
        app.settings_manager.save_setting(
            "eq-bands", ",".join(str(b) for b in bands)
        )
        # Refresh mpv preview
        if hasattr(app, "video_edit_page") and app.video_edit_page:
            if hasattr(app.video_edit_page, "_apply_audio_filters"):
                app.video_edit_page._apply_audio_filters()

    for i, freq in enumerate(EQ_FREQS):
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        col.set_size_request(38, -1)

        val_label = Gtk.Label(label=f"{saved_bands[i]:+.0f}")
        val_label.add_css_class("caption")
        val_label.add_css_class("numeric")
        col.append(val_label)
        eq_value_labels.append(val_label)

        adj = Gtk.Adjustment(
            value=saved_bands[i], lower=-40.0, upper=40.0,
            step_increment=0.5, page_increment=2.0,
        )
        scale = Gtk.Scale(orientation=Gtk.Orientation.VERTICAL, adjustment=adj)
        scale.set_inverted(True)
        scale.set_draw_value(False)
        scale.set_size_request(-1, 120)
        scale.set_vexpand(False)
        scale.add_mark(0.0, Gtk.PositionType.RIGHT, None)
        freq_str = f"{freq // 1000}k Hz" if freq >= 1000 else f"{freq} Hz"
        scale.update_property(
            [Gtk.AccessibleProperty.LABEL], [f"EQ {freq_str}"],
        )
        _idx = i
        scale.connect("value-changed", lambda s, idx=_idx: _on_eq_band_changed(s, idx))
        col.append(scale)
        eq_scales.append(scale)

        freq_label = Gtk.Label()
        freq_label.set_text(f"{freq // 1000}k" if freq >= 1000 else str(freq))
        freq_label.add_css_class("caption")
        freq_label.add_css_class("dim-label")
        col.append(freq_label)

        sliders_box.append(col)

    bands_container.append(sliders_box)
    eq_content.append(bands_container)
    card7.append(eq_content)

    # ── Sync logic ──
    def _update_sliders_from_preset() -> None:
        """Set sliders to match the currently selected preset."""
        idx = eq_dd.get_selected()
        if idx < len(app._eq_preset_keys):
            key = app._eq_preset_keys[idx]
            bands = app._eq_presets.get(key, [0.0] * 10)
            _eq_slider_guard["active"] = True
            for j, s in enumerate(eq_scales):
                s.set_value(bands[j])
                eq_value_labels[j].set_text(f"{bands[j]:+.0f}")
            _eq_slider_guard["active"] = False

    def _sync_eq_dd(dd, _p):
        sel = dd.get_selected()
        if app.eq_preset_row.get_selected() != sel:
            app.eq_preset_row.set_selected(sel)
        _update_sliders_from_preset()

    def _sync_eq_from(row, _p):
        sel = row.get_selected()
        if eq_dd.get_selected() != sel:
            eq_dd.set_selected(sel)
        _update_sliders_from_preset()

    eq_dd.connect("notify::selected", _sync_eq_dd)
    app.eq_preset_row.connect("notify::selected", _sync_eq_from)

    def _sync_eq_switch(sw, state):
        if app.eq_switch.get_active() != state:
            app.eq_switch.set_active(state)
        eq_content.set_visible(state)
        return False

    def _sync_eq_from_switch(sw, _pspec):
        active = sw.get_active()
        if eq_switch.get_active() != active:
            eq_switch.set_active(active)
        eq_content.set_visible(active)

    eq_switch.connect("state-set", _sync_eq_switch)
    app.eq_switch.connect("notify::active", _sync_eq_from_switch)

    content.append(card7)

    scroll.set_child(content)
    toolbar.set_content(scroll)
    dialog.set_child(toolbar)
    dialog.present(parent_window)
