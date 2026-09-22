"""Native component tests; not a screenshot or a full usability certification.

Run explicitly under Xvfb. Missing GTK is an error, never a passing skip.
"""
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'big-video-converter/usr/share/big-video-converter'))
import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gtk
from ui.audio_dialog import AudioDialog
from utils.contextual_controls import bind_details_to_switch
from utils.signal_connections import SignalConnections


def combo(values, selected=0):
    widget = Adw.ComboRow(model=Gtk.StringList.new(values))
    widget.set_selected(selected)
    return widget


def model():
    return SimpleNamespace(
        audio_handling_combo=combo([
            'Copy without changes (fastest)',
            'Re-encode audio',
            'Remove audio',
        ]),
        settings_page=SimpleNamespace(
            audio_codec_combo=combo(['AAC', 'Opus', 'AC3'], 1),
            audio_bitrate_combo=combo(['Default', '128k', 'Custom'], 2),
            audio_channels_combo=combo(['Default', 'Stereo', 'Custom'], 2),
            custom_bitrate_row=Adw.EntryRow(text='160k'),
            custom_channels_row=Adw.EntryRow(text='6'),
            bitrate_values=['', '128k', 'custom'],
            channels_values=['', '2', 'custom'],
        ),
    )


class AudioDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Adw.init()

    def setUp(self):
        self.app = model()
        self.dialog = AudioDialog(self.app)

    def tearDown(self):
        self.dialog.connections.close()

    def test_copy_hides_inapplicable_parameters(self):
        self.assertFalse(self.dialog.details_group.get_visible())
        self.assertIn('Original audio', self.dialog.result_label.get_label())

    def test_reencode_reveals_parameters_without_resetting_values(self):
        self.dialog.operation_row.set_selected(1)
        self.assertTrue(self.dialog.details_group.get_visible())
        self.assertEqual(self.app.audio_handling_combo.get_selected(), 1)
        self.assertEqual(self.dialog.codec_row.get_selected(), 1)
        self.assertEqual(self.dialog.custom_bitrate_row.get_text(), '160k')

    def test_remove_hides_parameters_and_describes_output(self):
        self.dialog.operation_row.set_selected(2)
        self.assertFalse(self.dialog.details_group.get_visible())
        self.assertIn('no audio', self.dialog.result_label.get_label())

    def test_custom_rows_require_reencode_and_custom_selection(self):
        self.assertFalse(self.dialog.custom_bitrate_row.get_visible())
        self.dialog.operation_row.set_selected(1)
        self.assertTrue(self.dialog.custom_bitrate_row.get_visible())
        self.dialog.bitrate_row.set_selected(0)
        self.assertFalse(self.dialog.custom_bitrate_row.get_visible())
        self.dialog.operation_row.set_selected(0)
        self.assertFalse(self.dialog.custom_channels_row.get_visible())

    def test_external_settings_change_is_reflected(self):
        self.app.audio_handling_combo.set_selected(1)
        self.assertEqual(self.dialog.operation_row.get_selected(), 1)
        self.assertTrue(self.dialog.details_group.get_visible())
        self.app.settings_page.audio_codec_combo.set_selected(2)
        self.assertEqual(self.dialog.codec_row.get_selected(), 2)

    def test_entries_sync_in_both_directions(self):
        self.dialog.custom_bitrate_row.set_text('192k')
        self.assertEqual(self.app.settings_page.custom_bitrate_row.get_text(), '192k')
        self.app.settings_page.custom_channels_row.set_text('2')
        self.assertEqual(self.dialog.custom_channels_row.get_text(), '2')

    def test_close_detaches_model_subscriptions(self):
        self.dialog.connections.close()
        self.app.audio_handling_combo.set_selected(1)
        self.assertEqual(self.dialog.operation_row.get_selected(), 0)
        self.dialog.codec_row.set_selected(2)
        self.assertEqual(self.app.settings_page.audio_codec_combo.get_selected(), 1)

    def test_mode_roundtrip_preserves_custom_values(self):
        for index in (1, 2, 0, 1):
            self.dialog.operation_row.set_selected(index)
        self.assertEqual(self.dialog.custom_bitrate_row.get_text(), '160k')
        self.assertEqual(self.dialog.custom_channels_row.get_text(), '6')

    def test_native_adaptive_presentation(self):
        self.assertEqual(
            self.dialog.get_presentation_mode(),
            Adw.DialogPresentationMode.AUTO,
        )
        self.assertTrue(self.dialog.get_follows_content_size())
        self.assertEqual(self.dialog.scroll.get_size_request(), (600, -1))
        self.assertTrue(self.dialog.scroll.get_propagate_natural_height())
        self.assertEqual(self.dialog.scroll.get_min_content_width(), 568)
        self.assertEqual(self.dialog.scroll.get_max_content_height(), 620)

    def test_compact_operation_labels_do_not_change_saved_model(self):
        local_model = self.dialog.operation_row.get_model()
        self.assertEqual(
            [local_model.get_string(i) for i in range(local_model.get_n_items())],
            ['Keep original audio', 'Convert audio', 'Remove audio'],
        )
        source_model = self.app.audio_handling_combo.get_model()
        self.assertEqual(
            source_model.get_string(0),
            'Copy without changes (fastest)',
        )
        self.dialog.operation_row.set_selected(1)
        self.assertEqual(self.app.audio_handling_combo.get_selected(), 1)


class NoiseDetailsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Adw.init()

    def test_disabled_operation_keeps_header_and_hides_details(self):
        owner = Adw.Dialog()
        connections = SignalConnections(owner)
        switch = Gtk.Switch()
        entry = Gtk.Entry(text='retained')
        slider = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 1, .05)
        slider.set_value(.65)
        bind_details_to_switch(switch, [entry, slider], connections)
        self.assertFalse(entry.get_visible())
        self.assertTrue(switch.get_visible())
        switch.set_active(True)
        self.assertTrue(entry.get_visible())
        self.assertAlmostEqual(slider.get_value(), .65)
        switch.set_active(False)
        self.assertEqual(entry.get_text(), 'retained')
        connections.close()
        switch.set_active(True)
        self.assertFalse(entry.get_visible())

    def test_slider_labels_and_binding_reach_production_source(self):
        source = (ROOT / 'big-video-converter/usr/share/big-video-converter/ui/noise_dialog.py').read_text()
        self.assertIn('bind_details_to_switch(switch, details, connections)', source)
        self.assertIn('scale.update_property([Gtk.AccessibleProperty.LABEL], [label])', source)
        self.assertIn('Adw.DialogPresentationMode.AUTO', source)


class EvidenceMatrixTests(unittest.TestCase):
    def test_named_states_are_wired_into_demo_and_workflow(self):
        demo = (ROOT / 'tools/ui_demo.py').read_text()
        workflow = (ROOT / '.github/workflows/exact-ui-review-r2.yml').read_text()
        states = (
            'audio-copy',
            'audio-convert',
            'audio-remove',
            'noise-off',
            'noise-ai',
        )
        for state in states:
            with self.subTest(state=state):
                self.assertIn(f'"{state}"', demo)
                self.assertIn(state, workflow)
        self.assertIn('test "$actual" -eq "$expected"', workflow)


if __name__ == '__main__':
    unittest.main(verbosity=2)
