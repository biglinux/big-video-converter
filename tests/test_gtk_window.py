"""The main window's header, queue list, notifications and setting cards."""

import os

import pytest

pytestmark = pytest.mark.skipif(
    not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")),
    reason="Requires an X11 or Wayland display",
)
gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from conftest import pump, pump_until, widgets
from gi.repository import Adw, Gtk


def test_missing_dependencies_open_one_install_dialog(app, monkeypatch):
    import main

    created = []

    class Dialog:
        installation_success = False

        def __init__(self, *args):
            created.append(self)

        def connect(self, *args):
            pass

        def present(self):
            pass

    monkeypatch.setattr(main, "InstallDependencyDialog", Dialog)
    monkeypatch.setattr(
        app.dependency_checker, "are_dependencies_available", lambda: False
    )
    monkeypatch.setattr(
        app.dependency_checker,
        "get_install_command",
        lambda: {"command": ["true"], "display": "true"},
    )
    monkeypatch.setattr(app, "_dependency_prompted", False, raising=False)
    try:
        app.on_activate(app)
        app.on_activate(app)
    finally:
        app.window.set_sensitive(True)
    assert len(created) == 1


def test_collapsed_split_toggles_settings_from_header(app):
    split, header = app.split_view, app.header_bar
    toggle = app.lookup_action("toggle_sidebar")
    app.show_queue_view()
    pump()
    split.set_collapsed(True)
    try:
        assert header.sidebar_button.get_visible() and toggle.get_enabled()
        assert (
            header.sidebar_button.get_tooltip_text() == "Show conversion settings (F9)"
        )
        app.show_queue_view()
        pump()
        assert not split.get_show_sidebar()
        toggle.activate(None)
        assert split.get_show_sidebar()
    finally:
        split.set_collapsed(False)
        app.show_queue_view()
        pump()
    assert (
        split.get_show_sidebar()
        and not header.sidebar_button.get_visible()
        and not toggle.get_enabled()
    )


def test_queue_count_uses_plural_forms(app):
    header = app.header_bar
    header.update_queue_size(2)
    assert header.queue_size_label.get_text() == "2 files"
    header.update_queue_size(1)
    assert header.queue_size_label.get_text() == "1 file"
    header.update_queue_size(len(app.conversion_queue))


def test_queue_rows_show_facts_and_overrides(app, media, monkeypatch, tmp_path):
    import shutil
    from collections import deque

    from ui.progress_page import format_size

    plain, custom = str(tmp_path / "plain.mp4"), str(tmp_path / "custom.mp4")
    shutil.copyfile(media["video"], plain)
    shutil.copyfile(media["video"], custom)
    monkeypatch.setattr(app, "conversion_queue", deque([plain, custom]))
    page = app.conversion_page
    monkeypatch.setitem(page.file_metadata, custom, {"resolution_mode": "1280x720"})
    page.update_queue_display()
    pump_until(
        lambda: (
            len(page.queue_rows) == 2
            and all(r.duration_badge.get_visible() for r in page.queue_rows)
        )
    )
    rows = {row.file_path: row for row in page.queue_rows}
    assert not rows[plain].recipe_label.get_visible()
    assert rows[custom].recipe_label.get_visible()
    assert rows[custom].recipe_label.get_text().startswith("Own: ")
    size = format_size(os.path.getsize(plain))
    assert rows[plain].meta_label.get_text() == f"128 × 72 · H.264 · {size}"
    assert rows[plain].duration_badge.get_text() == "0:03"
    # Adding and converting both keep the theme's accent in the header.
    header = app.header_bar
    assert header.add_button.has_css_class("suggested-action")
    assert header.convert_button.has_css_class("suggested-action")


def test_sizes_follow_user_numeric_locale(monkeypatch):
    import locale

    from ui import progress_page

    monkeypatch.setenv("LC_NUMERIC", "pt_BR.UTF-8")
    monkeypatch.setattr(progress_page, "_DECIMAL_POINT", None)
    locale.setlocale(locale.LC_NUMERIC, "C")  # what python-mpv does on import
    if progress_page._user_decimal_point() != ",":
        pytest.skip("pt_BR locale is not installed")
    assert progress_page.format_size(10_800_000).replace("\xa0", " ") == "10,8 MB"
    assert progress_page.format_size(53_546).replace("\xa0", " ") == "53,5 kB"


def test_empty_queue_shows_a_quiet_hint(app, monkeypatch):
    from collections import deque

    monkeypatch.setattr(app, "conversion_queue", deque())
    app.conversion_page.update_queue_display()
    pump(0.1)
    empty = app.conversion_page.placeholder
    # On the pane itself; a card around it reads as a box inside the pane.
    assert isinstance(empty, Adw.StatusPage) and not empty.has_css_class("card")
    # The header already offers adding files; the empty page adds no buttons.
    assert not [w for w in widgets(empty) if isinstance(w, Gtk.Button)]
    assert app.header_bar.add_button.get_visible()
    assert app.header_bar.add_button.has_css_class("suggested-action")


def test_reset_all_resets_the_queue_footer(app):
    """Conversions read the footer switches, so a reset must reach them too."""
    page = app.conversion_page
    page.delete_original_check.set_active(True)
    page.folder_combo.set_selected(1)
    assert app.settings_store.load_setting("delete-original", False)
    app.settings_page._reset_all_settings()
    assert not page.delete_original_check.get_active()
    assert page.folder_combo.get_selected() == 0


def test_window_state_saves_once(app, monkeypatch):
    store = app.settings_store
    writes = []
    monkeypatch.setattr(store, "save_to_disk", lambda: writes.append(1) or True)
    monkeypatch.setattr(app.window, "is_maximized", lambda: False)
    monkeypatch.setattr(app.window, "get_width", lambda: 1234)
    monkeypatch.setattr(app.window, "get_height", lambda: 789)
    app._save_window_state()
    store.flush()
    assert writes == [1]
    assert store.get_value("window-width") == 1234


def test_only_inactive_window_gets_notified(app, monkeypatch):
    sent = []
    monkeypatch.setattr(
        app, "send_notification", lambda _id, notification: sent.append(notification)
    )
    monkeypatch.setattr(app.window, "is_active", lambda: True)
    app.send_system_notification("Done", "All queued files have been processed.")
    assert sent == []
    monkeypatch.setattr(app.window, "is_active", lambda: False)
    app.send_system_notification("Done", "All queued files have been processed.")
    assert len(sent) == 1


def test_cancelling_the_folder_chooser_changes_nothing(app):
    from gi.repository import Gio, GLib

    class Cancelled:
        def select_folder_finish(self, _result):
            raise GLib.Error.new_literal(Gio.io_error_quark(), "Dismissed by user", 0)

    page = app.conversion_page
    before = page.output_folder_entry.get_text()
    page._on_folder_chosen(Cancelled(), None)
    assert page.output_folder_entry.get_text() == before


def test_video_mode_card_picks_a_mode(app):
    cards = app.setting_cards.hover_cards
    row = app._video_mode_row
    app._radio_universal.set_active(True)
    pump()
    row.activate()
    pump()
    try:
        assert cards.pinned is row
        assert app.window.get_focus() is app._radio_universal
        app._radio_smaller.set_active(True)
        pump()
        assert app.video_codec_combo.get_selected() == 2
        assert row.value_label.get_text() == "More Efficient"
    finally:
        cards.close(row)
        app._radio_universal.set_active(True)
        pump()


def test_choice_rows_follow_their_combos(app):
    for row, combo in (
        (app._audio_row, app.audio_handling_combo),
        (app._subtitles_row, app.subtitle_combo),
    ):
        before = combo.get_selected()
        combo.set_selected(2)
        assert row.get_selected() == 2
        row.set_selected(0)
        assert combo.get_selected() == 0
        combo.set_selected(before)
    app.audio_handling_combo.set_selected(1)
    assert app._audio_options_button.get_visible()
    assert app._audio_cleaning_row.unavailable is None
    app.audio_handling_combo.set_selected(0)
    assert not app._audio_options_button.get_visible()
    assert app._audio_cleaning_row.unavailable


def test_dialog_rows_open_no_card(app):
    app._size_row.activate()
    pump(0.2)
    dialog = app.window.get_visible_dialog()
    try:
        assert dialog is not None and dialog.get_title() == "Maximum size"
        assert app.setting_cards.hover_cards.pinned is None
    finally:
        dialog.force_close()
        pump(0.2)


def test_extract_mode_grays_rows_with_reason(app):
    check = app.settings_page.only_extract_subtitles_check
    check.set_active(True)
    pump()
    try:
        assert app._extract_switch.get_active()
        for row in (app._video_mode_row, app._audio_row, app._extra_row):
            assert row.unavailable == "Off while only extracting subtitles"
            assert not row.get_sensitive()
        assert app._subtitles_row.unavailable is None
        assert app._size_row.unavailable is None
    finally:
        check.set_active(False)
        pump()
    assert app._video_mode_row.unavailable is None


def test_page_or_sidebar_change_closes_cards(app):
    cards = app.setting_cards.hover_cards
    for change in (
        lambda: app.left_stack.set_visible_child_name("editing_tools"),
        lambda: app.split_view.set_collapsed(True),
    ):
        app.left_stack.set_visible_child_name("conversion_settings")
        app.split_view.set_collapsed(False)
        pump()
        cards.open_for_keyboard(app._video_mode_row)
        pump()
        assert cards.is_open(app._video_mode_row)
        change()
        pump()
        assert not cards.is_open(app._video_mode_row)
    app.split_view.set_collapsed(False)
    app.left_stack.set_visible_child_name("conversion_settings")
    pump()


def test_collapsed_sidebar_card_stays_in_window(app):
    cards = app.setting_cards.hover_cards
    app.split_view.set_collapsed(True)
    app.split_view.set_show_sidebar(True)
    pump(0.3)
    try:
        cards.open_for_keyboard(app._subtitles_row)
        pump(0.3)
        x0, _y0, x1, _y1 = cards._card_box(app._subtitles_row)
        assert 0 <= x0 and x1 <= app.window.get_width()
    finally:
        cards.close(app._subtitles_row)
        app.split_view.set_collapsed(False)
        pump()


def test_editor_rows_show_adjustments(app):
    ui = app.video_edit_page.ui
    row, scale = ui.brightness_row, ui.brightness_scale
    scale.set_value(0.1)
    try:
        assert row.value_label.get_text() == "+10%"
        assert row.applied
        row.reset_button.emit("clicked")
        assert abs(scale.get_value()) < 1e-6
        assert not row.applied
    finally:
        scale.set_value(0.0)


def test_card_rows_lead_both_sidebars(app):
    groups = app.setting_cards.groups
    main = groups[app._video_mode_row.group_index][0]
    assert main == [app._video_mode_row, app._audio_row, app._subtitles_row]
    ui = app.video_edit_page.ui
    editor = groups[ui.brightness_row.group_index][0]
    assert editor[0] is ui.brightness_row
    assert editor[-2:] == [ui.rotation_row, ui.pause_row]


def test_setting_cards_cleanup_empties_the_layer():
    from big_gtk_kit.hover_cards import CardLayer
    from ui.setting_cards import SettingCards, SettingRow

    layer = CardLayer()
    cards = SettingCards(Gtk.ScrolledWindow(), layer)
    _box, listbox = cards.add_group("Group")
    cards.add_row(
        listbox, SettingRow("image-x-generic-symbolic", "Row", None, Gtk.Label())
    )
    assert layer.get_first_child() is not None
    cards.cleanup()
    assert layer.get_first_child() is None


def test_welcome_enter_closes_it(app):
    from ui.welcome_dialog import WelcomeDialog

    welcome = WelcomeDialog(app.window, app.settings_store)
    assert welcome.dialog.get_title() == "Welcome to Big Video Converter"
    assert welcome.show_switch.get_accessible_role() == Gtk.AccessibleRole.SWITCH
    welcome.present()
    pump(0.3)
    try:
        focus = welcome.dialog.get_focus()
        # Enter activates the focused default button, never the switch.
        assert isinstance(focus, Gtk.Button) and focus.get_label() == "Let's Start"
        assert welcome.dialog.get_default_widget() is focus
        shown = welcome.show_switch.get_active()
        focus.activate()
        # Adw.Dialog unmaps when its closing animation ends.
        pump_until(lambda: not welcome.dialog.get_mapped(), timeout=5)
        assert welcome.show_switch.get_active() == shown
    finally:
        welcome.dialog.force_close()
        pump(0.1)


def test_same_choice_notifies_no_one(app):
    from ui.setting_cards import ChoiceRow

    row = ChoiceRow("video-display-symbolic", "Colors", None, ["A", "B"])
    seen = []
    row.connect("notify::selected", lambda *_a: seen.append(row.get_selected()))
    row.set_selected(1)
    row.set_selected(1)
    row.tiles.set_selected(1)
    assert seen == [1]


def test_skip_trash_menu_switches_row_delete(app, media, monkeypatch, tmp_path):
    import shutil
    from pathlib import Path

    from conftest import DATA_HOME

    video = tmp_path / "row.mp4"
    shutil.copyfile(media["video"], video)
    app.clear_queue()
    app.add_file_to_queue(str(video))
    page = app.conversion_page
    pump_until(lambda: page.queue_rows and page.queue_rows[0].file_path == str(video))
    dialogs = []
    monkeypatch.setattr(Adw.AlertDialog, "present", lambda d, _w: dialogs.append(d))
    action = app.lookup_action("skip_trash")
    assert not action.get_state().get_boolean()
    assert "go to the Trash" in page.delete_label.get_tooltip_text()
    page.queue_rows[0]._on_delete_from_disk()
    assert dialogs[-1].get_heading() == "Move this video to the Trash?"
    dialogs[-1].emit("response", "trash")
    pump_until(lambda: not video.exists())
    assert (Path(DATA_HOME) / "Trash/files/row.mp4").exists()
    action.activate(None)
    try:
        assert app.settings_store.load_setting("skip-trash") is True
        assert "deleted permanently" in page.delete_label.get_tooltip_text()
    finally:
        action.activate(None)
        app.clear_queue()
    assert app.settings_store.load_setting("skip-trash") is False
