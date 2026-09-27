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
