# Resource lifetimes

Every window, dialog and page must finalize once it closes. A callback that
holds its own widget, or the object that owns it, keeps the whole tree alive:
the dialog, its model and its child widgets leak on every open.

`tests/test_resource_lifetimes.py` checks this. Its qdata destructors count
finalization, not just dispose. Run it in an isolated graphical session, and
wait for close animations before asserting that something was finalized.

## Rules

- **Controller callbacks.** A controller callback gets its widget from
  `controller.get_widget()`. Never pass that widget as strong signal user data.
- **Welcome dialog.** It releases its child on `closed`. Hiding its sheet is not
  enough: the button callback still owns the Python controller and its dialog.
- **Information windows.**
  - They are destroyed on close.
  - Their button subscriptions are tracked with
    `SignalConnections(..., "close-request")`.
  - Worker results that arrive after the close are ignored.
- **Extra dialog.** Child callbacks that capture it are tracked with
  `SignalConnections`, like subscriptions on long-lived settings widgets.
- **Presets dialog.**
  - Preset actions and filters must not retain the dialog that owns them.
  - Fixed subscriptions are tracked, per-card actions use weak owners, and
    banners are connected once.
- **AI prompts.** They are built on a worker, because FFmpeg probes may block.
  The clipboard is updated on the GTK thread only while the dialog has not
  emitted `closed`.
- **Settings writes.** Saving a value already stored, of the same type, writes
  nothing to disk. Real changes keep atomic persistence and rollback.
- **Network dialog.** Button closures are disconnected on close, including
  closures that reach the dialog indirectly through an asynchronous mount
  callback.
- **Video controls.** The individual controls share one refresh closure.
  Disconnect it from every emitter on close, or the controls keep each other
  alive.
- **Dependency windows.**
  - Child and button subscriptions are disconnected on close.
  - Closing is refused while an installation runs.
  - An asynchronous spawn failure must clear that state.
- **MPV render callback.** It stays registered across editor visits, and
  updates are ignored while the editor is inactive. Replacing its ctypes wrapper
  can race the native render thread.
- **Size dialog.** Its radio and spin controls share a closure. Every
  subscription is tracked with `SignalConnections`, so the controls finalize
  when the dialog closes.
