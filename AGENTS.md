# Resource lifetime rules

Use an isolated GTK session. Resource regressions live in
`tests/test_resource_lifetimes.py`; its qdata destructors count finalization,
not merely dispose. Wait for close animations before asserting finalization.

- A controller callback obtains its widget through `controller.get_widget()`;
  never pass that widget as strong signal user data.
