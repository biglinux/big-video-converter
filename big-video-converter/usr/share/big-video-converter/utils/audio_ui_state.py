"""Pure presentation state for audio handling controls.

The conversion backend remains the source of truth.  This module only maps the
three audio handling choices to UI applicability, so dialogs can be tested
without importing GTK.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AudioModePresentation:
    """User-facing applicability for one audio handling mode."""

    key: str
    details_sensitive: bool
    result_title_key: str
    result_description_key: str


_PRESENTATIONS = {
    0: AudioModePresentation(
        key="copy",
        details_sensitive=False,
        result_title_key="keep-original",
        result_description_key="keep-original-description",
    ),
    1: AudioModePresentation(
        key="reencode",
        details_sensitive=True,
        result_title_key="convert-audio",
        result_description_key="convert-audio-description",
    ),
    2: AudioModePresentation(
        key="none",
        details_sensitive=False,
        result_title_key="remove-audio",
        result_description_key="remove-audio-description",
    ),
}


def audio_mode_presentation(selected_index: int) -> AudioModePresentation:
    """Return a safe presentation state for a combo selection.

    Unknown or transient GTK selections fall back to preserving the source
    audio, the least destructive mode.
    """

    try:
        index = int(selected_index)
    except (TypeError, ValueError):
        index = 0
    return _PRESENTATIONS.get(index, _PRESENTATIONS[0])
