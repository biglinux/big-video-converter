"""Find the pauses of the edited video and keep the speech between them.

The segments are what is exported, so the pauses go by making the speech the
segments, joined into one video. The panel sits in the editor's sidebar, so
an analysis of a long video can be followed and cancelled while the rest of
the editor stays usable.
"""

import gettext
import shutil
import weakref
from typing import NamedTuple

from engine.ffmpeg_path import get_ffmpeg_executable
from engine.pauses import (
    DEFAULT_VOICE_SENSITIVITY,
    PAUSE_LENGTHS,
    STEP,
    PauseFinder,
    find_pauses,
)
from gi.repository import Adw, Gtk

_ = gettext.gettext


def kept_parts(pauses, end):
    """The parts of 0..end between the pauses, as trim segments."""
    kept, cursor = [], 0.0
    for pause in pauses:
        if pause["start"] > cursor:
            kept.append({"start": cursor, "end": pause["start"]})
        cursor = max(cursor, pause["stop"])
    if cursor < end:
        kept.append({"start": cursor, "end": end})
    return kept


class Undo(NamedTuple):
    """What marking replaced, kept until the video or its segments change."""

    video: str
    segments: list
    output_mode: str
    applied: list


class PausePanel(Adw.PreferencesGroup):
    """The controls in the "Remove pauses" card; its row shows ``summary()``."""

    def __init__(self, page):
        super().__init__()
        self.page = weakref.ref(page)
        self.on_changed = None  # told when summary() may have changed
        self._state = None
        self.finder = PauseFinder()
        self.source = None  # (path, audio track) the levels belong to
        self.levels = None
        self.pauses = []
        self.undo = None
        owner = weakref.proxy(self)

        lengths = Gtk.Box(
            css_classes=["linked"],
            valign=Gtk.Align.CENTER,
            accessible_role=Gtk.AccessibleRole.GROUP,
        )
        lengths.update_property(
            [Gtk.AccessibleProperty.LABEL], [_("Pauses longer than")]
        )
        self.length_buttons = []
        for seconds, label in zip(
            PAUSE_LENGTHS, (_("0.5 s"), _("1 s"), _("2 s")), strict=True
        ):
            button = Gtk.ToggleButton(label=label)
            if self.length_buttons:
                button.set_group(self.length_buttons[0])
            button.seconds = seconds
            lengths.append(button)
            self.length_buttons.append(button)
        self.length_row = Adw.ActionRow(title=_("Pauses longer than"))
        self.length_row.add_suffix(lengths)
        self.add(self.length_row)

        # Five named steps from 1, very sensitive, to 5, very strict: their
        # names side by side would be twice the card's width, so a slider
        # picks the step and the row names it.
        self.sensitivity_names = (
            _("Very sensitive"),
            _("Sensitive"),
            _("Normal"),
            _("Strict"),
            _("Very strict"),
        )
        self.sensitivity = Gtk.Scale(
            adjustment=Gtk.Adjustment(
                lower=1, upper=5, step_increment=1, page_increment=1
            ),
            round_digits=0,
            hexpand=True,
        )
        for level in range(1, 6):
            self.sensitivity.add_mark(level, Gtk.PositionType.BOTTOM, None)
        help_text = _(
            "Sensitive keeps quiet or unusual voices and lets more noise and "
            "music through; strict removes more music and may cut faint words."
        )
        self.sensitivity.update_property(
            [Gtk.AccessibleProperty.LABEL, Gtk.AccessibleProperty.DESCRIPTION],
            [_("Voice detection"), help_text],
        )
        self.sensitivity_label = Gtk.Label(css_classes=["dim-label"])
        heading = Gtk.Box(spacing=12)
        heading.append(Gtk.Label(label=_("Voice detection"), xalign=0, hexpand=True))
        heading.append(self.sensitivity_label)
        sensitivity_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=4,
            margin_start=12,
            margin_end=12,
            margin_top=10,
            margin_bottom=10,
        )
        sensitivity_box.append(heading)
        sensitivity_box.append(self.sensitivity)
        sensitivity_box.append(
            Gtk.Label(
                label=help_text,
                xalign=0,
                wrap=True,
                css_classes=["caption", "dim-label"],
            )
        )
        # Not a stop of its own: the keyboard goes straight to the slider.
        self.sensitivity_row = Gtk.ListBoxRow(
            child=sensitivity_box,
            activatable=False,
            selectable=False,
            focusable=False,
        )

        # One row says where the analysis stands and offers its next step.
        self.status_row = Adw.ActionRow(
            title_lines=3, subtitle_lines=3, use_markup=False
        )
        self.actions = Gtk.Stack(valign=Gtk.Align.CENTER, hhomogeneous=False)
        for name, label, handler, suggested in (
            ("find", _("Find Pauses"), lambda _b: owner._on_find(), True),
            ("cancel", _("Cancel"), lambda _b: owner._on_cancel(), False),
            ("retry", _("Try Again"), lambda _b: owner._on_find(), False),
        ):
            button = Gtk.Button(label=label)
            if suggested:
                button.add_css_class("suggested-action")
            button.connect("clicked", handler)
            self.actions.add_named(button, name)
        self.status_row.add_suffix(self.actions)
        self.add(self.status_row)

        self.progress = Gtk.ProgressBar(
            show_text=True,
            margin_start=12,
            margin_end=12,
            margin_top=10,
            margin_bottom=10,
        )
        self.progress.update_property(
            [Gtk.AccessibleProperty.LABEL], [_("Finding pauses")]
        )
        self.progress_row = Gtk.ListBoxRow(
            child=self.progress, activatable=False, selectable=False, visible=False
        )
        self.add(self.progress_row)

        self.undo_row = Adw.ActionRow(visible=False, subtitle_lines=4, use_markup=False)
        undo = Gtk.Button(label=_("Undo"), valign=Gtk.Align.CENTER)
        undo.connect("clicked", lambda _b: owner.undo_apply())
        self.undo_row.add_suffix(undo)
        self.add(self.undo_row)
        # Under the action, which stays in view on short screens: the detection
        # is chosen once, the analysis run for every file.
        self.add(self.sensitivity_row)

        # Connected after showing the saved choices, which are no new choice.
        self._show_saved()
        for button in self.length_buttons:
            button.connect("toggled", lambda b: owner._on_length_toggled(b))
        self.sensitivity.connect(
            "value-changed", lambda _s: owner._on_sensitivity_changed()
        )
        # Opening the card re-checks the video, dpdfnet-native and the saved
        # choices, which a reset or an imported profile may have changed.
        self.connect("map", lambda _panel: owner._on_map())
        self.sync()

    def _on_map(self):
        self._show_saved()
        self.sync()

    def _show_saved(self):
        page = self.page()
        if page is None:
            return
        try:
            chosen = PAUSE_LENGTHS.index(
                float(page.settings.get_value("pause-length", 1.0))
            )
        except (TypeError, ValueError):
            chosen = 1
        try:
            level = round(
                float(
                    page.settings.get_value(
                        "voice-sensitivity", DEFAULT_VOICE_SENSITIVITY
                    )
                )
            )
        except (TypeError, ValueError):
            level = DEFAULT_VOICE_SENSITIVITY
        self.length_buttons[chosen].set_active(True)
        self.sensitivity.set_value(level)
        self._show_sensitivity()

    def _current(self):
        """The edited video and its audio track in the preview, 0-based."""
        page = self.page()
        player = getattr(page, "mpv_player", None) if page else None
        if not page or not page.current_video_path or not player:
            return None, []
        tracks = player.get_audio_tracks()
        if not tracks:
            return None, tracks
        ids = [track["index"] for track in tracks]
        chosen = player.current_audio_track
        return (
            page.current_video_path,
            ids.index(chosen) if chosen in ids else 0,
        ), tracks

    def sync(self):
        """Follow the edited video and track: drop what belongs to another."""
        current, tracks = self._current()
        self.set_sensitive(current is not None)
        self.status_row.set_subtitle(
            _("Listening to {track}. The other tracks get the same cuts.").format(
                track=tracks[current[1]]["label"]
            )
            if current and len(tracks) > 1
            else ""
        )
        if current == self.source:
            # dpdfnet-native may have been installed since the last look.
            if self.levels is None and not self.finder.busy:
                self._show_state()
            return
        self.finder.cancel()
        self.source = current
        self.levels = None
        self.undo = None
        self._show_state()

    def _length(self):
        return next(b.seconds for b in self.length_buttons if b.get_active())

    def _sensitivity(self):
        return round(self.sensitivity.get_value())

    def _show_sensitivity(self):
        name = self.sensitivity_names[self._sensitivity() - 1]
        self.sensitivity_label.set_text(name)
        self.sensitivity.update_property([Gtk.AccessibleProperty.VALUE_TEXT], [name])

    def _show_state(self, name=None):
        page = self.page()
        self._show_undo()
        if name is None:
            if self.source is None:
                name = "silent" if page and page.current_video_path else "idle"
            elif self.finder.busy:
                name = "busy"
            elif self.levels is not None:
                name = "ready"
            elif not shutil.which("dpdfnet-enhance"):
                name = "missing"
            else:
                name = "start"
        self._state = name
        self.progress_row.set_visible(name == "busy")
        # Once edited, the segments are the user's; finding again marks anew.
        self.actions.set_visible(
            name in ("start", "busy", "failed")
            or (name == "ready" and bool(self.pauses) and self.undo is None)
        )
        self.length_row.set_sensitive(name != "missing")
        self.sensitivity_row.set_sensitive(name != "missing")
        if name in ("start", "ready"):
            self.actions.set_visible_child_name("find")
        if name == "start":
            self.status_row.set_title(_("Takes about a minute for each hour of audio."))
        elif name == "busy":
            self.status_row.set_title(_("Finding pauses"))
            self.actions.set_visible_child_name("cancel")
        elif name == "missing":
            self.status_row.set_title(
                _("Unavailable: install {package}").format(package="dpdfnet-native")
            )
        elif name == "silent":
            self.status_row.set_title(_("This video has no sound to find pauses in"))
        elif name == "idle":
            self.status_row.set_title("")
        elif name == "ready":
            self.status_row.set_title(self._pause_summary())
        self._changed()

    def summary(self):
        """A short line for the row, and whether the found parts are applied."""
        if self.undo is not None:
            return self.undo_row.get_title(), True
        if self._state in ("busy", "failed", "missing"):
            return self.status_row.get_title(), False
        if self.levels is not None and self.pauses:
            return self._pause_summary(), False
        return "", False

    def _changed(self):
        if self.on_changed is not None:
            self.on_changed()

    def _pause_summary(self):
        count = len(self.pauses)
        removed = sum(p["stop"] - p["start"] for p in self.pauses)
        page = self.page()
        return (
            gettext.ngettext(
                "{count} pause · {duration} shorter",
                "{count} pauses · {duration} shorter",
                count,
            ).format(
                count=count,
                duration=page.format_time_precise(removed).split(".")[0]
                if page
                else "",
            )
            if count
            else _("No pauses found. Nothing would change.")
        )

    def _show_undo(self):
        page = self.page()
        if self.undo and (
            page is None
            or self.undo.video != page.current_video_path
            or page.trim_segments != self.undo.applied
        ):
            self.undo = None  # edited since: not the segments it made
        visible = self.undo is not None
        self.undo_row.set_visible(visible)
        # Once marked, one row says it all: the result, what it replaced and
        # that it can be undone.
        self.status_row.set_visible(not visible)
        if visible:
            count = len(self.undo.applied)
            self.undo_row.set_title(
                gettext.ngettext(
                    "{count} part kept", "{count} parts kept", count
                ).format(count=count)
            )
            notes = [self._pause_summary()]
            replaced = len(self.undo.segments)
            if replaced:
                notes.append(
                    gettext.ngettext(
                        "This replaces the {count} segment you had.",
                        "This replaces the {count} segments you had.",
                        replaced,
                    ).format(count=replaced)
                )
            notes.append(_("Nothing is deleted from the original file."))
            self.undo_row.set_subtitle("\n".join(notes))

    def segments_changed(self):
        """An edit after marking hands the segments back to the user."""
        if self.undo:
            self._show_state()

    def _on_find(self):
        current, _tracks = self._current()
        page = self.page()
        if current is None or page is None:
            return
        try:
            key = PauseFinder.identity(*current, self._sensitivity())
        except OSError as exc:
            self._fail(str(exc))
            return
        self.levels = self.finder.cached(key)
        if self.levels is not None:
            self._mark()
            return
        denoiser = shutil.which("dpdfnet-enhance")
        if not denoiser:
            self._show_state("missing")
            return
        self.progress.set_fraction(0)
        self.progress.set_text(_("{percent}%").format(percent="0"))
        owner = weakref.proxy(self)
        self.finder.start(
            key,
            get_ffmpeg_executable(),
            denoiser,
            page.video_duration,
            lambda fraction: owner._on_progress(fraction),
            lambda _key, levels: owner._on_found(levels),
            lambda message: owner._fail(message),
        )
        self._show_state("busy")

    def _on_progress(self, fraction):
        self.progress.set_fraction(fraction)
        percent = f"{fraction * 100:.0f}"
        self.progress.set_text(_("{percent}%").format(percent=percent))
        self.status_row.set_title(
            _("Finding pauses: {percent}%").format(percent=percent)
        )
        self._changed()

    def _on_found(self, levels):
        self.levels = levels
        self._mark()

    def _fail(self, message):
        self._show_state("failed")
        self.status_row.set_title(message or _("The pauses could not be measured."))
        self.actions.set_visible_child_name("retry")
        self._changed()

    def _on_cancel(self):
        self.finder.cancel()
        self._show_state()

    def _on_length_toggled(self, button):
        if not button.get_active():
            return
        page = self.page()
        if page:
            page.settings.save_setting("pause-length", button.seconds)
        if self.levels is not None:
            self._mark()

    def _on_sensitivity_changed(self):
        self._show_sensitivity()
        page = self.page()
        if page:
            page.settings.save_setting("voice-sensitivity", self._sensitivity())
        if self.levels is None and not self.finder.busy:
            return
        # The levels heard the voice at the old step: the segments they made
        # go and the video is measured again, or taken from the cache.
        self.undo_apply()
        self.levels = None
        self._on_find()

    def _set_segments(self, page, segments, mode):
        page.trim_segments = [dict(s) for s in segments]
        page.output_mode = mode
        # Not the user's choice: the general default stays as it was.
        page._restoring_metadata = True
        try:
            page.ui.output_mode_combo.set_selected({"join": 0, "split": 1}[mode])
        finally:
            page._restoring_metadata = False
        page._save_file_metadata()
        page._update_segments_listbox()

    def _mark(self):
        """Replace the segments with the speech between the pauses, joined.

        A new length replaces only the segments the last one made, never the
        user's.
        """
        page = self.page()
        if page is None:
            return
        self.undo_apply()
        self.pauses = find_pauses(self.levels, self._length())
        if self.pauses:
            previous = ([dict(s) for s in page.trim_segments], page.output_mode)
            # The probed length; the levels end where the audio track does.
            end = page.video_duration or round(len(self.levels) * STEP, 3)
            kept = kept_parts(self.pauses, end)
            self._set_segments(page, kept, "join")
            self.undo = Undo(page.current_video_path, *previous, kept)
        self._show_state()

    def undo_apply(self):
        page = self.page()
        if (
            page is None
            or not self.undo
            or self.undo.video != page.current_video_path
            or page.trim_segments != self.undo.applied
        ):
            return
        segments, mode = self.undo.segments, self.undo.output_mode
        self.undo = None
        self._set_segments(page, segments, mode)
        self._show_state()
