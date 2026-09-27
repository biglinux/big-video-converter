"""Maximum output size: where the video is going, and how it may be fitted."""

import gettext

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk

from utils.size_target import TARGETS, shown_size
from utils.signal_connections import SignalConnections

_ = gettext.gettext

# Translated again where shown: this runs at import, possibly before the
# catalogue is bound.
STRATEGY_TEXTS = (
    ("auto", _("Automatic"),
     _("Copies what already fits, lowers the bitrate and the resolution, and splits only when nothing else fits")),
    ("keep_resolution", _("Keep the resolution"),
     _("Only the bitrate goes down; refuses a video that would need a smaller picture")),
    ("split", _("Split into parts"),
     _("Keeps the quality and makes several files, each under the size")),
)


def size_summary(settings) -> str:
    """One line for the sidebar: the target, and the strategy when not automatic."""
    target_id = settings.load_setting("size-target", "")
    if not target_id:
        return _("No limit")
    shown = shown_size(target_id, settings.load_setting("size-target-mb", 50.0))
    if target_id == "custom":
        text = _("Up to {size}").format(size=shown)
    else:
        text = f"{_(next(t[1] for t in TARGETS if t[0] == target_id))} · {shown}"
    strategy = settings.load_setting("size-strategy", "auto")
    if strategy != "auto":
        text += " · " + _(next(t[1] for t in STRATEGY_TEXTS if t[0] == strategy))
    return text


def _radio_row(group, title, subtitle, first):
    check = Gtk.CheckButton(group=first)
    row = Adw.ActionRow(title=title, subtitle=subtitle)
    row.add_prefix(check)
    row.set_activatable_widget(check)
    group.add(row)
    return check, row


def show_size_dialog(parent_window, app) -> None:
    settings = app.settings_manager
    dialog = Adw.Dialog()
    connections = SignalConnections(dialog)
    dialog.set_title(_("Maximum size"))
    dialog.set_content_width(600)
    dialog.set_content_height(640)
    dialog.set_presentation_mode(Adw.DialogPresentationMode.FLOATING)

    toolbar = Adw.ToolbarView()
    toolbar.add_top_bar(Adw.HeaderBar())
    page = Adw.PreferencesPage()
    toolbar.set_content(page)
    dialog.set_child(toolbar)

    targets = Adw.PreferencesGroup(
        title=_("Where the video is going"),
        description=_("Every converted video comes out no bigger than this."))
    page.add(targets)
    current = settings.load_setting("size-target", "")
    radios = {}
    none, _row = _radio_row(targets, _("No limit"), _("The size follows the quality"), None)
    radios[""] = none
    for target_id, label, _bytes, shown in TARGETS:
        radios[target_id], _row = _radio_row(targets, _(label), shown, none)
    radios["custom"], custom_row = _radio_row(targets, _("Another size"), _("In megabytes"), none)
    custom = Gtk.SpinButton.new_with_range(1, 1_000_000, 1)
    custom.set_value(settings.load_setting("size-target-mb", 50.0))
    custom.set_valign(Gtk.Align.CENTER)
    custom.update_property([Gtk.AccessibleProperty.LABEL], [_("Size in megabytes")])
    custom_row.add_suffix(custom)
    radios.get(current, none).set_active(True)

    strategies = Adw.PreferencesGroup(title=_("How to fit it"))
    page.add(strategies)
    chosen = settings.load_setting("size-strategy", "auto")
    first = None
    strategy_radios = {}
    for strategy_id, title, subtitle in STRATEGY_TEXTS:
        strategy_radios[strategy_id], _row = _radio_row(strategies, _(title), _(subtitle), first)
        first = first or strategy_radios[strategy_id]
    strategy_radios.get(chosen, first).set_active(True)

    def sync(*_args):
        target_id = next(key for key, radio in radios.items() if radio.get_active())
        settings.save_setting("size-target", target_id)
        settings.save_setting("size-target-mb", float(custom.get_value()))
        settings.save_setting("size-strategy", next(
            key for key, radio in strategy_radios.items() if radio.get_active()))
        custom.set_sensitive(target_id == "custom")
        strategies.set_sensitive(bool(target_id))
        app._update_size_subtitle()

    for radio in (*radios.values(), *strategy_radios.values()):
        connections.connect(radio, "toggled", lambda radio: radio.get_active() and sync())
    connections.connect(custom, "value-changed", sync)
    custom.set_sensitive(current == "custom")
    strategies.set_sensitive(bool(current))
    dialog.present(parent_window)
