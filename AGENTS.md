# Resource lifetime rules

Use an isolated GTK session. Resource regressions live in
`tests/test_resource_lifetimes.py`; its qdata destructors count finalization,
not merely dispose. Wait for close animations before asserting finalization.

- A controller callback obtains its widget through `controller.get_widget()`;
  never pass that widget as strong signal user data.
- The welcome dialog releases its child on `closed`; hiding its sheet alone
  leaves the button callback owning the Python controller and its dialog.
- Information windows are destroyed on close. Track their button subscriptions
  with `SignalConnections(..., "close-request")` and ignore worker results after close.
- Track child callbacks capturing the Extra dialog with `SignalConnections`,
  just like subscriptions on long-lived settings widgets.
- Preset actions and filters must not retain the owning dialog. Track fixed
  subscriptions, use weak owners for per-card actions, and connect banners once.
- Build AI prompts on a worker: FFmpeg probes may block. Publish clipboard
  updates on GTK only while the dialog has not emitted `closed`.
- Saving an already stored value of the same type must perform no disk write.
  Preserve atomic persistence and rollback for actual changes.
- Network dialog button closures are disconnected on close, including closures
  that capture the dialog indirectly through an asynchronous mount callback.
- Individual video controls share a refresh closure; disconnect all of its
  emitters on close so the controls cannot keep each other alive.
- Dependency windows disconnect child and button subscriptions on close. Refuse
  close during installation; asynchronous spawn failure must clear that state.
- Keep the MPV render callback registered across editor visits. Ignore updates
  while inactive; replacing its ctypes wrapper can race the native render thread.
- Size dialog radio and spin controls share a closure; track every subscription
  with SignalConnections so the controls finalize when the dialog closes.
