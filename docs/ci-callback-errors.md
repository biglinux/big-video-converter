# Native callback exceptions must fail CI

Follow-up to [the native CI decisions](ci-native-gtk.md), reviewed on 2026-09-08.
The workflow and existing 138 cases are preserved. Three new guard regressions
bring the required minimum to **141 tests**, with no failures, errors or skips.
This supersedes the earlier 138-case minimum recorded in ci-native-gtk.md.

PyGObject can dispatch Python callbacks from C and report their exceptions
through `sys.excepthook`, without propagating them to the Python code driving
the main loop. Upstream PyGObject tests explicitly guard against this situation:
[upstream test hook](https://gnome.pages.gitlab.gnome.org/pygobject/coverage/tests/conftest.py.gcov.html).
A green pytest exit status alone must not hide such an exception.

The project now observes `sys.excepthook` in the `pytest_runtest_setup`,
`pytest_runtest_call` and `pytest_runtest_teardown` wrappers. The original hook
is restored in a finally block. Unhandled callback tracebacks cause a pytest
failure or fixture error in the corresponding phase. No production handler is
replaced, and no callback is disabled to make tests pass.

Three isolated child pytest processes deliberately raise a RuntimeError in a
real GLib source callback, during setup, call or teardown. The parent tests
require exit status 1 and a matching JUnit failure/error. The intentional errors
remain confined to those child processes; the main suite must finish successfully.
The already-corrected crop test continues to check both the crop property and
exactly one deferred rendering request before the test ends.

This guards Python callback exceptions dispatched during the test phases. It is
not a universal detector of native crashes, arbitrary C-library warnings or
callbacks that are never dispatched. Verbose logs, wrapper exit statuses and
JUnit/artifact validation remain necessary. Hardware, Wayland, screen-reader,
GTCRN-quality and GTK deprecation limitations remain as documented in the base note.

Primary references: [pytest wrappers](https://docs.pytest.org/en/stable/how-to/writing_hook_functions.html),
[sys.excepthook](https://docs.python.org/3.13/library/sys.html#sys.excepthook),
[GLib sources](https://docs.gtk.org/glib/struct.Source.html),
[GLib timeouts](https://docs.gtk.org/glib/func.timeout_add.html),
[subprocess](https://docs.python.org/3.13/library/subprocess.html).
