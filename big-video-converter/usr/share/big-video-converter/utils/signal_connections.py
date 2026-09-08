"""Tie subscriptions on long-lived settings widgets to a dialog's lifetime."""


class SignalConnections:
    def __init__(self, dialog):
        self._handlers = []
        self._closed = False
        self._closed_id = dialog.connect("closed", self._on_closed)

    def connect(self, emitter, signal, callback, *args):
        if self._closed:
            raise RuntimeError("Cannot subscribe a closed dialog")
        handler_id = emitter.connect(signal, callback, *args)
        self._handlers.append((emitter, handler_id))
        return handler_id

    def close(self):
        self._closed = True
        handlers, self._handlers = self._handlers, []
        for emitter, handler_id in handlers:
            if emitter.handler_is_connected(handler_id):
                emitter.disconnect(handler_id)

    def _on_closed(self, dialog):
        self.close()
        if dialog.handler_is_connected(self._closed_id):
            dialog.disconnect(self._closed_id)
