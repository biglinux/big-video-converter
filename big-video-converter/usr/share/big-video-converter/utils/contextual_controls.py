"""Small UI policy adapters with no GTK import and no stored user settings."""


def bind_details_to_switch(switch, details, connections):
    """Reveal existing controls only when their operation is enabled.

    The switch itself and its explanatory header stay visible. Connections
    are owned by the dialog. No values or selected profiles are reset.
    """
    details = tuple(details)

    def update(*_args):
        active = bool(switch.get_active())
        for widget in details:
            widget.set_visible(active)

    connections.connect(switch, "notify::active", update)
    update()
    return update
