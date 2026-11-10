"""GTK4 building blocks shared by the BigLinux Python programs."""


def install():
    """Turn on what every program gets without changing a call site: the
    tooltip card, the closing-dialog focus fix and the popup-parent release.
    Call once, in the application's ``startup``."""
    from big_gtk_kit import dialog, surface, tooltip

    tooltip.install()
    dialog.install()
    surface.install()


def init_class(gtype):
    """Initialise a GObject class, so its signals exist before any instance.

    PyGObject 3.50 added ``GObject.TypeClass.get`` and deprecated
    ``GObject.type_class_ref``; the 3.48 of Ubuntu 24.04, which AppImages are
    built on, only has the latter."""
    from gi.repository import GObject

    get = getattr(GObject.TypeClass, "get", None)
    if get is not None:
        get(gtype)
    else:
        GObject.type_class_ref(gtype)
