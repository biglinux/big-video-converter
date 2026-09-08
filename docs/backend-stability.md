# Backend stability, with the existing GTK interface

This change keeps the existing screens, controls, presets and GTK4/libadwaita
workflow. It changes their implementation and failure semantics, not their
layout. No TOML preset catalog, new formats, file-size targeting or UI redesign
is included.

## Safety contracts

* Commands are argument arrays, not shell programs. Additional FFmpeg options
  have known arity; positional inputs/outputs, incomplete flags and unknown
  options are rejected by the same parser in the GUI and the Bash backend.
* Input/output paths are explicit. Each job owns private temporary directories.
  A destination name is claimed with an exclusive create before the encode
  starts and published by renaming over that claim, so publication cannot
  overwrite another job's output, a symlink or the original, cannot fail for
  lack of space after the work is done, and does not need a filesystem that
  supports hard links.
* A zero exit code and a non-empty file do not by themselves mean success.
  The supervisor checks the output's stream inventory, and its video duration
  against the requested interval. A duration that cannot be confirmed is
  reported and still counts as a successful conversion, because a stream copy
  cuts on keyframes and a variable frame rate source gets re-timed; it only
  withholds permission to remove the original.
* An explicitly false delete preference stays false. The original is deleted
  permanently, as the interface says, and only after: unchanged source and
  output identities, a confirmed duration, every mapped stream carrying
  packets, a packet count that reaches the frame count FFmpeg reported
  writing, and a decode of the output. Media under thirty seconds is decoded
  whole; longer media is decoded at both edges and three interior points,
  which keeps the check's cost independent of the file's length. Anything
  unconfirmed preserves the original and says why. Subtitle-only extraction
  never authorizes removing the video.
* Completion is identified by job ID and is idempotent. A callback without an
  unambiguous identity does not finish another active job. Cancellation applies
  to the parent segment job and all supervised subprocesses, including children
  that keep pipes open after their parent exits.

## Media and editor corrections

Embedded subtitle streams are explicitly mapped. For a trimmed output, A/V is
trimmed before a separate subtitle mux. The subtitle input is read without
seeking past overlapping cues. FFmpeg's `noise` bitstream filter uses `amount=0`
(no payload corruption) and a numeric packet-drop predicate; `setts` clips and
rebases packet timestamps. Finite validated numbers, not arbitrary expressions
from an imported profile, are used to construct these filters.

SRT sidecars have stream IDs in their names, are extracted once per stream for
joined segments, include cues intersecting cut boundaries, and are renumbered
in milliseconds after clipping. The batch has one result, not one successful
notification per stage. Failure/cancellation prevents the next stage.

FFprobe metadata uses named JSON fields for validation. Requested video filters
are no longer silently skipped in the HEVC 10-bit path. A video without audio
no longer gains a duplicate video map. Dotted directories and quotes, spaces,
newlines, Unicode and literal shell expressions in filenames are handled as
path data.

Editor adjustments are saved in the per-file model immediately. Removing a
segment uses the selected object's identity, not its start time. MPV hue is
mapped to its documented -100..100 range; failed property writes do not poison
the cache. Crop state is reapplied after temporarily clearing the preview.
Generation IDs prevent metadata from a previous editor session updating the
current one. Dialog subscriptions to persistent controls are disconnected when
the dialog closes. Selecting Custom during a manual EQ edit does not reset the
other bands.

Profile JSON remains compatible with the existing format, but imports are
validated before changing settings and reject unknown data/version/type/ranges.
Portable profiles do not carry deletion preferences or machine/window/preview
state. Settings use XDG_CONFIG_HOME, unique atomic temporary files and a
known-good backup; nested batch/suspension scopes preserve their previous state.

## Intentional behavior changes

1. The CLI refuses an existing destination instead of overwriting with `-y`.
2. Invalid additional-option grammar fails before starting FFmpeg.
3. Missing GTCRN, failed channel processing or missing processed tracks fail the
   requested operation rather than silently producing degraded success.
4. Any check that cannot be completed keeps the original and reports the reason.
5. Timeline overrides (`-copyts`, `-itsoffset`, `-sseof`, `-start_at_zero`) cannot
   be combined with ordinary subtitle clipping: that combination fails clearly
   instead of using an ambiguous time origin.
6. The monitor retains a bounded diagnostic tail in the UI. It does not promise
   unlimited in-memory logs. The existing log view and controls are unchanged.

## Tests

Debian 13 amd64 is the exercised environment: Python 3.13.5, FFmpeg 7.1.5,
GTK 4.18.6, libadwaita 1.7.6, MPV 0.40.0 and Mesa/llvmpipe. Use the distribution
Python carrying PyGObject (`/usr/bin/python3`), or a matching virtual environment
with access to those system packages.

```sh
bash -n big-video-converter/usr/bin/big-video-converter
shellcheck -S warning big-video-converter/usr/bin/big-video-converter
/usr/bin/python3 -m compileall -q big-video-converter/usr/share/big-video-converter tests
/usr/bin/python3 -m pytest -q tests

# Native widgets and the real application, with a private D-Bus session/display:
export XDG_RUNTIME_DIR="$(mktemp -d)"
chmod 700 "$XDG_RUNTIME_DIR"
LIBGL_ALWAYS_SOFTWARE=1 GSK_RENDERER=opengl \
  xvfb-run -a -s '-screen 0 1440x1000x24' \
  dbus-run-session -- /usr/bin/python3 -m pytest -q tests
rm -rf "$XDG_RUNTIME_DIR"
```

The suite creates its own small media fixtures. Native GTK tests skip only
when neither DISPLAY nor WAYLAND_DISPLAY is set, so they also run inside a
nested Wayland session instead of being silently dropped there. Subprocess supervision uses real GLib with lightweight UI doubles;
`test_gtk.py` separately opens the actual application/dialogs and performs a
conversion with a real progress row. MPV property/cache regressions also use
controlled property doubles; those are not a pixel-quality comparison.

## Remaining validation / scope limits

This is not closure of every item in the earlier project audit. Physical
NVENC/VAAPI/QSV/Vulkan devices, the actual GTCRN plugin/models, full screen-reader
usability, translation completeness, HDR/color fidelity and external-editor
compatibility still require their own fixtures and environments. The existing
translation/publication workflow is not replaced by this test workflow.

FFmpeg 7.1's MOV muxer did not retain a forced subtitle disposition even with
an explicit `-disposition` in a control command; MP4 and MKV did retain it.
Bitmap subtitle/container compatibility and formats with in-payload timing need
additional validation. Timestamp clipping does not convert an ASS animation's
internal timing instructions. SRT extraction does not preserve ASS styling.
Stream-copy cuts remain constrained by packet/keyframe boundaries.

Slow FFprobe calls are bounded (15 seconds) but are not instantaneously
cancellable. Sampled decoding cannot see damage between the windows it reads;
it is paired with a packet count over the whole file, which catches a
truncated or empty stream but not a frame that decodes to the wrong picture.
Each decode is bounded by its own timeout, and a timeout preserves the
original. A killed job's leftovers are removed by the paths it announced
rather than by waiting longer for its own cleanup, which cannot be guaranteed
after SIGKILL. These checks are not a
sandbox against an attacker who can simultaneously replace files in the user's
own destination directories.

## Contracts checked against upstream documentation

* FFmpeg command-line and stream selection: https://ffmpeg.org/ffmpeg.html
* FFprobe writers and named output: https://ffmpeg.org/ffprobe.html
* Bitstream filters (`noise`, `setts`): https://ffmpeg.org/ffmpeg-bitstream-filters.html
* MPV properties: https://mpv.io/manual/master/
* PyGObject threading: https://pygobject.gnome.org/guide/threading.html
* Python subprocess lifecycle: https://docs.python.org/3/library/subprocess.html
* GObject signals: https://docs.gtk.org/gobject/signals.html

The documentation was consulted during this change. Runtime guarantees are
limited to the tested versions and cases, not all builds described by upstream.
