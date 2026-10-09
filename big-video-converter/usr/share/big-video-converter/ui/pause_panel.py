"""Find the pauses of the edited video, preview them and cut them out."""

import gettext
import shutil
import weakref

from gi.repository import Adw, Gtk
from utils.ffmpeg_path import get_ffmpeg_executable
from utils.pauses import PAUSE_LENGTHS, PauseFinder, find_pauses

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


class PausePopover(Gtk.Popover):
    def __init__(self, page):
        super().__init__()
        self.page = weakref.ref(page)
        self.finder = PauseFinder()
        self.source = None  # (path, audio track) the levels belong to
        self.levels = None
        self.pauses = []
        # (video, previous segments, previous output mode, applied segments)
        self.undo = None

        box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=12,
            width_request=320,
            margin_top=12,
            margin_bottom=12,
            margin_start=12,
            margin_end=12,
        )
        box.append(
            Gtk.Label(label=_("Remove pauses"), xalign=0, css_classes=["heading"])
        )
        box.append(
            Gtk.Label(
                label=_(
                    "Made for speech. Pauses are found on the voice cleaned by DPDFNet."
                ),
                xalign=0,
                wrap=True,
                max_width_chars=40,
                css_classes=["dim-label"],
            )
        )
        # Which track, when there is a choice; the cuts apply to every track.
        self.track_label = Gtk.Label(
            xalign=0, wrap=True, max_width_chars=40, visible=False
        )
        box.append(self.track_label)
        self.stack = Gtk.Stack(vhomogeneous=False)
        box.append(self.stack)

        self.stack.add_named(
            Gtk.Label(
                label=_("Unavailable: install {package}").format(
                    package="dpdfnet-native"
                ),
                xalign=0,
                wrap=True,
            ),
            "missing",
        )

        start = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        start.append(
            Gtk.Label(
                label=_("Takes about a minute for each hour of audio."),
                xalign=0,
                wrap=True,
            )
        )
        find = Gtk.Button(label=_("Find Pauses"), css_classes=["suggested-action"])
        find.connect("clicked", self._on_find)
        start.append(find)
        self.stack.add_named(start, "start")

        busy = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.progress = Gtk.ProgressBar(show_text=True)
        self.progress.update_property(
            [Gtk.AccessibleProperty.LABEL], [_("Finding pauses")]
        )
        busy.append(self.progress)
        cancel = Gtk.Button(label=_("Cancel"))
        cancel.connect("clicked", self._on_cancel)
        busy.append(cancel)
        self.stack.add_named(busy, "busy")

        failed = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.error = Gtk.Label(xalign=0, wrap=True, max_width_chars=40)
        failed.append(self.error)
        retry = Gtk.Button(label=_("Try Again"))
        retry.connect("clicked", self._on_find)
        failed.append(retry)
        self.stack.add_named(failed, "failed")

        ready = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        length_label = Gtk.Label(label=_("Pauses longer than"), xalign=0)
        ready.append(length_label)
        lengths = Gtk.Box(
            css_classes=["linked"],
            homogeneous=True,
            accessible_role=Gtk.AccessibleRole.GROUP,
        )
        lengths.update_property(
            [Gtk.AccessibleProperty.LABEL], [length_label.get_label()]
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
        ready.append(lengths)
        self.summary = Gtk.Label(xalign=0, wrap=True)
        ready.append(self.summary)
        self.apply_button = Gtk.Button(
            label=_("Cut Them Out"), css_classes=["suggested-action"]
        )
        self.apply_button.connect("clicked", self._on_apply)
        ready.append(self.apply_button)
        self.stack.add_named(ready, "ready")

        self.undo_box = Gtk.Box(spacing=12, visible=False)
        self.undo_label = Gtk.Label(xalign=0, hexpand=True, wrap=True)
        self.undo_box.append(self.undo_label)
        undo = Gtk.Button(label=_("Undo"))
        undo.connect("clicked", self.undo_apply)
        self.undo_box.append(undo)
        box.append(self.undo_box)
        self.set_child(box)

        # The button shows a spinner in place of its icon while working.
        self.icon = Gtk.Stack()
        self.icon.add_named(Gtk.Image(icon_name="edit-cut-symbolic"), "idle")
        self.icon.add_named(Gtk.Spinner(spinning=True), "busy")
        content = Gtk.Box(spacing=6)
        content.append(self.icon)
        content.append(Gtk.Label(label=_("Remove pauses")))
        self.button = Gtk.MenuButton(
            child=content, popover=self, halign=Gtk.Align.START
        )
        self.button.update_property(
            [Gtk.AccessibleProperty.LABEL], [_("Remove pauses")]
        )

        try:
            chosen = PAUSE_LENGTHS.index(
                float(page.settings.get_value("pause-length", 1.0))
            )
        except (TypeError, ValueError):
            chosen = 1
        # Connected after showing the saved choice, which is no new choice.
        self.length_buttons[chosen].set_active(True)
        for button in self.length_buttons:
            button.connect("toggled", self._on_length_toggled)
        self.connect("show", self._on_show)
        self.connect("closed", self._on_closed)
        self.sync()

    # State

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
        self.button.set_sensitive(current is not None)
        page = self.page()
        self.button.set_tooltip_text(
            None
            if current
            else _("This video has no sound to find pauses in")
            if page and page.current_video_path
            else None
        )
        if len(tracks) > 1 and current:
            self.track_label.set_label(
                _("Listening to {track}. The other tracks get the same cuts.").format(
                    track=tracks[current[1]]["label"]
                )
            )
        self.track_label.set_visible(len(tracks) > 1 and current is not None)
        if current == self.source:
            return
        self.finder.cancel()
        self.source = current
        self.levels = None
        self._show_state()

    def _length(self):
        return next(b.seconds for b in self.length_buttons if b.get_active())

    def _show_state(self, name=None):
        if name is None:
            if self.finder.busy:
                name = "busy"
            elif self.levels is not None:
                name = "ready"
            elif not shutil.which("dpdfnet-enhance"):
                name = "missing"
            else:
                name = "start"
        self.stack.set_visible_child_name(name)
        self.icon.set_visible_child_name("busy" if name == "busy" else "idle")
        if name != "busy":
            self.button.update_property([Gtk.AccessibleProperty.DESCRIPTION], [""])
        if name == "ready":
            self._find()
        self._show_undo()

    def _find(self):
        """Pauses at the chosen length, previewed on the timeline while open."""
        self.pauses = find_pauses(self.levels, self._length())
        count = len(self.pauses)
        removed = sum(p["stop"] - p["start"] for p in self.pauses)
        page = self.page()
        self.summary.set_label(
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
        self.apply_button.set_sensitive(bool(count))
        self._preview(self.pauses if self.get_visible() else [])

    def _preview(self, pauses):
        page = self.page()
        if page:
            page.proposed_pauses = pauses
            if pauses:
                page.ui._show_controls()  # the timeline that shows them
            page.ui.update_segment_markers()

    def _show_undo(self):
        page = self.page()
        here = bool(page and self.undo and self.undo[0] == page.current_video_path)
        if here and page.trim_segments != self.undo[3]:
            self.undo = None  # edited since: no longer the cut that was applied
        visible = here and self.undo is not None
        self.undo_box.set_visible(visible)
        if visible:
            count = len(self.undo[3])
            self.undo_label.set_label(
                gettext.ngettext(
                    "{count} part kept", "{count} parts kept", count
                ).format(count=count)
            )

    # Actions

    def _on_find(self, _button):
        current, _tracks = self._current()
        page = self.page()
        if current is None or page is None:
            return
        try:
            key = PauseFinder.identity(*current)
        except OSError as exc:
            self._fail(str(exc))
            return
        self.levels = self.finder.cached(key)
        if self.levels is not None:
            self._show_state()
            return
        denoiser = shutil.which("dpdfnet-enhance")
        if not denoiser:
            self._show_state("missing")
            return
        self.progress.set_fraction(0)
        self.progress.set_text(None)
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
        self.button.update_property(
            [Gtk.AccessibleProperty.DESCRIPTION],
            [_("Finding pauses: {percent}%").format(percent=percent)],
        )

    def _on_found(self, levels):
        self.levels = levels
        self._show_state()
        page = self.page()
        if page and not self.get_visible():
            # The analysis ran in the background: say so where it can be reopened.
            toast = Adw.Toast(title=_("Pauses found"), button_label=_("Review"))
            button = weakref.ref(self.button)
            toast.connect("button-clicked", lambda _t: button() and button().popup())
            page.app.toast_overlay.add_toast(toast)

    def _fail(self, message):
        self.error.set_label(message or _("The pauses could not be measured."))
        self._show_state("failed")

    def _on_cancel(self, _button):
        self.finder.cancel()
        self._show_state()

    def _on_length_toggled(self, button):
        if not button.get_active():
            return
        page = self.page()
        if page:
            page.settings.save_setting("pause-length", button.seconds)
        if self.levels is not None:
            self._find()

    def _on_show(self, _popover):
        self.sync()
        # dpdfnet-native may have been installed while the window was open.
        if self.stack.get_visible_child_name() == "missing":
            self._show_state()
        self._show_undo()
        if self.stack.get_visible_child_name() == "ready":
            self._preview(self.pauses)

    def _on_closed(self, _popover):
        self._preview([])
        page = self.page()
        if page and page.is_playing:
            page.ui._schedule_hide_controls()

    def _set_segments(self, page, segments, mode):
        page.trim_segments = [dict(s) for s in segments]
        page.output_mode = mode
        # Not a choice of the person: the general default stays as it was.
        page._restoring_metadata = True
        try:
            page.ui.output_mode_combo.set_selected({"join": 0, "split": 1}[mode])
        finally:
            page._restoring_metadata = False
        page._save_file_metadata()
        page._update_segments_listbox()

    def _on_apply(self, _button):
        page = self.page()
        if page is None or not self.pauses or not page.video_duration:
            return
        previous = ([dict(s) for s in page.trim_segments], page.output_mode)
        self._preview([])
        kept = kept_parts(self.pauses, page.video_duration)
        # The parts between the pauses are joined into one video.
        self._set_segments(page, kept, "join")
        self.undo = (page.current_video_path, *previous, [dict(s) for s in kept])
        count = len(self.pauses)
        toast = Adw.Toast(
            title=gettext.ngettext(
                "{count} pause cut out", "{count} pauses cut out", count
            ).format(count=count),
            button_label=_("Undo"),
        )
        owner = weakref.proxy(self)
        toast.connect("button-clicked", lambda _t: owner.undo_apply())
        page.app.toast_overlay.add_toast(toast)
        self._show_undo()
        self.popdown()

    def undo_apply(self, *_args):
        page = self.page()
        if (
            page is None
            or not self.undo
            or self.undo[0] != page.current_video_path
            or page.trim_segments != self.undo[3]
        ):
            return
        _video, segments, mode, _applied = self.undo
        self.undo = None
        self._set_segments(page, segments, mode)
        self._show_undo()
        self.popdown()
