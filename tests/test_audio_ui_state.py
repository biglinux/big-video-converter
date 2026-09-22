from pathlib import Path
import importlib.util


MODULE = (
    Path(__file__).resolve().parents[1]
    / "big-video-converter/usr/share/big-video-converter/utils/audio_ui_state.py"
)
spec = importlib.util.spec_from_file_location("audio_ui_state", MODULE)
audio_ui_state = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audio_ui_state)


def test_copy_preserves_audio_and_disables_reencoding_details():
    state = audio_ui_state.audio_mode_presentation(0)
    assert state.key == "copy"
    assert state.details_sensitive is False
    assert state.result_title_key == "keep-original"


def test_reencode_enables_codec_quality_and_channel_controls():
    state = audio_ui_state.audio_mode_presentation(1)
    assert state.key == "reencode"
    assert state.details_sensitive is True
    assert state.result_title_key == "convert-audio"


def test_remove_audio_disables_irrelevant_details():
    state = audio_ui_state.audio_mode_presentation(2)
    assert state.key == "none"
    assert state.details_sensitive is False
    assert state.result_title_key == "remove-audio"


def test_invalid_selection_uses_non_destructive_fallback():
    state = audio_ui_state.audio_mode_presentation(999)
    assert state.key == "copy"
    assert state.details_sensitive is False
