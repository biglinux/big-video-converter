# Native GTK CI: documented decisions

Documentation reviewed on 2026-09-08. This note covers the Actions workflow,
its execution resources, and the failures observed during this delivery. It is
not a claim that every application API or hardware backend was re-audited.

## Root cause and regression coverage

Run [34189932214](https://github.com/biglinux/big-video-converter/actions/runs/34189932214)
reached the first native application test after 24 audio/CLI cases. GLib
terminated the process because `org.gnome.desktop.wm.preferences` was missing.
Exit 133 and missing JUnit XML were consequences of that native abort.

GIO requires checking optional schemas before constructing settings. The
application now checks the source, schema, `button-layout` key and fixed path
before `Gio.Settings.new_full(schema, None, None)`. Without them it preserves
the existing right-side default. Existing GNOME settings still take precedence.
This is an application fix, not merely installing a dependency to hide a crash.

The suite contains the original 128 cases plus ten desktop-integration cases:
four absent-schema prerequisites, five existing layouts, and real application
startup in a fresh subprocess with empty schema search paths. The normal CI
container includes desktop schemas and the AT-SPI accessibility bus; the fresh
subprocess explicitly verifies behavior without the GNOME schema.

Run [34190837897](https://github.com/biglinux/big-video-converter/actions/runs/34190837897)
reported 138 passed and zero skipped, but its native log exposed an additional
`AttributeError` from an incomplete MPV test double. That test used `__new__`,
omitting constructor fields needed by its delayed render callback. The test
now supplies render state and waits for the real GLib callback, checking that
it renders once. All previous assertions remain; production MPV code is not
changed to hide an invalid test object. A green count alone is not proof that
callbacks or native stderr are clean.

## Decisions and primary documentation

| Resource | Decision | Documentation |
| --- | --- | --- |
| GitHub container job | Ubuntu hosts Debian 13. Do not assume host programs exist inside the container. Explicitly use Bash; container run steps otherwise default to sh. | [Container jobs](https://docs.github.com/en/actions/how-tos/write-workflows/choose-where-workflows-run/run-jobs-in-a-container), [workflow syntax and shell semantics](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax) |
| Events and checkout | Keep pull_request's default merge-result checkout for integration testing. Record the checked-out commit/tree; it is not an actual merge into main. Keep main push and workflow_dispatch. | [Trigger events](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows) |
| Permissions and action versions | contents: read; no persisted Git credentials, branch publication or unsafe-checkout override. Pin verified upstream checkout/upload-artifact v7.0.1 SHAs. Their Node 24 runtime is supported by the observed runner 2.337.0. | [Secure use](https://docs.github.com/en/actions/reference/security/secure-use), [checkout](https://github.com/actions/checkout/tree/v7.0.1), [upload-artifact](https://github.com/actions/upload-artifact/tree/v7.0.1) |
| Debian and PyGObject | Use Debian's Python with matching GI modules and explicit APT dependencies. Record installed package versions instead of assuming the moving image/APT repositories are fully locked. | [APT](https://manpages.debian.org/trixie/apt/apt-get.8.en.html), [PyGObject setup](https://pygobject.gnome.org/getting_started.html) |
| GSettings and AT-SPI | Install the normal desktop prerequisites and check their availability. Separately test missing schemas in a fresh process. The gsettings CLI belongs to libglib2.0-bin; a Python GI installation does not imply that executable is present. | [gsettings executable package](https://packages.debian.org/trixie/amd64/libglib2.0-bin/filelist), [at-spi2-core dependencies](https://packages.debian.org/trixie/at-spi2-core) |
| Xvfb and D-Bus | Install xauth, choose a free X display, expose Xvfb stderr and run a private D-Bus session. Retain wrapper/test failure status. | [xvfb-run](https://manpages.debian.org/trixie/xvfb/xvfb-run.1.en.html), [dbus-run-session](https://dbus.freedesktop.org/doc/dbus-run-session.1.html) |
| GTK and Mesa | Set X11 for Xvfb, the documented gl renderer, and Mesa software rendering. Keep the accessibility bus enabled rather than setting GTK_A11Y=none. | [GTK runtime](https://docs.gtk.org/gtk4/running.html), [Mesa environment variables](https://docs.mesa3d.org/envvars.html) |
| Runtime files | Use a private XDG_RUNTIME_DIR with mode 0700 and remove only that directory on exit. | [XDG Base Directory specification](https://specifications.freedesktop.org/basedir/latest/) |
| pytest and native diagnostics | Verbose test IDs plus tee-sys keep Python output and native stderr visible. Outer tee saves the log; Bash pipefail preserves a failing test's exit status. | [pytest capture](https://docs.pytest.org/en/stable/how-to/capture-stdout-stderr.html), [Bash](https://www.gnu.org/software/bash/manual/html_node/Pipelines.html) |
| Python fault handlers | Enable faulthandler, but do not rely on it alone for SIGTRAP: that signal is not in Python 3.13's default handler set. Preserve GLib's error output. | [faulthandler](https://docs.python.org/3.13/library/faulthandler.html) |
| JUnit and artifacts | Require pytest success plus at least 138 cases with zero errors, failures or skips. Report after test success/failure but not cancellation; retain XML, test logs and package/revision logs for 14 days. | [pytest JUnit](https://docs.pytest.org/en/8.3.x/how-to/output.html), [status expressions](https://docs.github.com/en/actions/reference/workflows-and-actions/expressions), [artifacts](https://github.com/actions/upload-artifact/tree/v7.0.1) |
| Syntax checks | bash -n, ShellCheck and compileall are preliminary checks, not substitutes for media/native runtime tests. | [ShellCheck](https://manpages.debian.org/trixie/shellcheck/shellcheck.1.en.html), [compileall](https://docs.python.org/3.13/library/compileall.html) |
| Media and process supervision | Retain real FFmpeg/FFprobe fixtures and GLib supervision tests. Commands use argv, with explicit process/timeouts and stream validation contracts. | [FFmpeg](https://ffmpeg.org/ffmpeg.html), [FFprobe](https://ffmpeg.org/ffprobe.html), [subprocess](https://docs.python.org/3.13/library/subprocess.html), [PyGObject threading](https://pygobject.gnome.org/guide/threading.html) |
| Delayed callbacks and test doubles | A GLib timeout runs on its main context and stops on SOURCE_REMOVE. Test doubles that bypass __init__ must supply the fields actually used; drive the context to verify completion within the owning test. | [GLib.timeout_add](https://docs.gtk.org/glib/func.timeout_add.html), [Python object construction](https://docs.python.org/3.13/reference/datamodel.html#object.__new__) |

## Optional schema API references

- [Gio.Settings.new](https://docs.gtk.org/gio/ctor.Settings.new.html): optional-schema lookup requirement.
- [SettingsSchemaSource.lookup](https://docs.gtk.org/gio/method.SettingsSchemaSource.lookup.html): nullable lookup.
- [SettingsSchema.has_key](https://docs.gtk.org/gio/method.SettingsSchema.has_key.html): validate the required key.
- [SettingsSchema.get_path](https://docs.gtk.org/gio/method.SettingsSchema.get_path.html): fixed versus relocatable schema.
- [Settings.new_full](https://docs.gtk.org/gio/ctor.Settings.new_full.html): a relocatable schema requires an explicit path.
- [GLib.error](https://docs.gtk.org/glib/func.error.html): fatal errors terminate the process, not a Python try/except path.

## Scope and limitations

Documentation supports API decisions; it does not replace testing the actual
revision. Current upstream manuals can describe newer releases than Debian's
installed libraries; results apply to the versions recorded by each run.

Native tests use real GTK widgets under Xvfb, not physical GPU drivers, a
Wayland session or human visual assessment. Having the AT-SPI bus available
does not certify screen-reader usability. GTCRN pipeline tests use a pass-through
filter, not actual model-quality evaluation. HDR, translation completeness and
external-editor compatibility retain the limits in backend-stability.md.

The existing Gtk.CssProvider.load_from_data deprecation warning is separate from
the fatal schema error. This CI change does not declare every deprecation fixed.

Branch edits are performed outside CI, using the current parent and a non-forced
fast-forward. Concurrent updates require re-reading and integrating changes,
not force-pushing over them. [Git references](https://docs.github.com/en/rest/git/refs#update-a-reference),
[Git trees](https://docs.github.com/en/rest/git/trees#create-a-tree).
