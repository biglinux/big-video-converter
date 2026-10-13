"""Hover cards decide by geometry and a clock, so these run without a display."""

import pytest
from big_gtk_kit import card_geometry, hover_intent
from big_gtk_kit import hover_cards as hc

ROW = 48
WIDTH = 300
CARD_X = 308
CARD_HEIGHT = 450


class Clock:
    """Timers that fire only when the test advances time."""

    def __init__(self):
        self.t = 0.0
        self.timers = {}
        self._next = 0

    def later(self, milliseconds, callback):
        self._next += 1
        self.timers[self._next] = (self.t + milliseconds / 1000, callback)
        return self._next

    def cancel(self, source):
        self.timers.pop(source, None)

    def advance(self, milliseconds):
        end = self.t + milliseconds / 1000
        while True:
            due = sorted(
                (when, source)
                for source, (when, _cb) in self.timers.items()
                if when <= end + 1e-9
            )
            if not due:
                break
            self.t, source = due[0]
            self.timers.pop(source)[1]()
        self.t = end


class List:
    """A list of rows with cards beside it, top-aligned to their row."""

    def __init__(self, rows=6, held=False):
        self.clock = Clock()
        self.open = set()
        self.held = held
        self.intent = hover_intent.HoverIntent(
            "right",
            self.boxes,
            self.open.add,
            self.open.discard,
            self.clock.later,
            self.clock.cancel,
            is_held=lambda _name: self.held,
        )
        self.rows = rows

    def boxes(self, row):
        anchor = (0, row * ROW, WIDTH, (row + 1) * ROW)
        card = None
        if row in self.open:
            top = row * ROW
            card = (CARD_X, top, CARD_X + 300, top + CARD_HEIGHT)
        return anchor, card

    def row_at(self, point):
        x, y = point
        if 0 <= x <= WIDTH and 0 <= y < self.rows * ROW:
            return int(y // ROW)
        return None

    def move(self, *points, step_ms=16):
        """The pointer through ``points``, one event per frame."""
        for point in points:
            self.intent.pointer(self.row_at(point), point)
            self.clock.advance(step_ms)

    def rest(self, point, milliseconds):
        self.move(point)
        self.clock.advance(milliseconds)

    def open_row(self, row):
        self.move((150, row * ROW + ROW / 2))
        assert self.open == {row}


def path(start, end, steps):
    (x0, y0), (x1, y1) = start, end
    return [
        (x0 + (x1 - x0) * i / steps, y0 + (y1 - y0) * i / steps)
        for i in range(1, steps + 1)
    ]


def test_reaching_a_row_opens_its_card():
    cards = List()
    cards.intent.pointer(0, (150, 24))
    assert cards.open == {0}


def test_moving_straight_down_switches_at_once():
    cards = List()
    cards.open_row(0)
    cards.move(*path((150, 24), (150, 72), 4))
    assert cards.open == {1}


def test_diagonal_toward_card_keeps_it_open():
    cards = List()
    cards.open_row(0)
    # 45 degrees down and right, over row 1, then a pause short of the card,
    # as a hand undershoots before correcting.
    cards.move(*path((150, 24), (290, 70), 8))
    assert cards.row_at((290, 70)) == 1
    cards.clock.advance(hover_intent.TRIANGLE_REST_MS - 50)
    assert cards.open == {0}
    cards.move(*path((290, 70), (330, 90), 3))
    cards.intent.entered_card(0)
    cards.clock.advance(1000)
    assert cards.open == {0}


def test_resting_midway_hands_over_the_card():
    cards = List()
    cards.open_row(0)
    cards.move(*path((150, 24), (290, 70), 8))
    cards.clock.advance(hover_intent.TRIANGLE_REST_MS + 10)
    assert cards.open == {1}


def test_tremor_still_counts_as_resting():
    cards = List()
    cards.open_row(0)
    cards.move(*path((150, 24), (290, 70), 8))
    # +-3 px of tremor, one event per frame, past the rest's wait.
    for offset in (0, 3, -3, 2, -2, 3, 0, -3, 3, 0, 2, -2, 3, -3, 0, 1) * 2:
        cards.move((290 + offset, 70))
    assert cards.open == {1}
    other = List()
    other.open_row(0)
    other.move(*path((150, 24), (290, 70), 8))
    for _ in range(10):
        other.clock.advance(hover_intent.TRIANGLE_REST_MS - 100)
        other.move((284, 70), step_ms=0)  # a real move restarts the wait
        other.clock.advance(hover_intent.TRIANGLE_REST_MS - 100)
        other.move((290, 70), step_ms=0)
    assert other.open == {0}


def test_leaving_the_triangle_switches_at_once():
    cards = List()
    cards.open_row(0)
    cards.move(*path((150, 24), (290, 70), 8))
    cards.move(*path((290, 70), (100, 80), 4))  # back left, out of the triangle
    assert cards.open == {1}


def test_fast_flick_leaves_nothing_open():
    cards = List()
    cards.move(*path((150, 5), (150, 280), 6), step_ms=8)
    cards.move((900, 500))
    cards.clock.advance(hover_intent.CLOSE_MS + 10)
    assert cards.open == set()


def test_leaving_closes_after_a_while():
    cards = List()
    cards.open_row(0)
    cards.move((900, 500))  # far from the row, its card and the way between
    cards.clock.advance(hover_intent.CLOSE_MS - 50)
    assert cards.open == {0}
    cards.clock.advance(60)
    assert cards.open == set()


def test_leaving_window_closes_unless_card_entered():
    cards = List()
    cards.open_row(0)
    cards.intent.left_window()
    cards.intent.entered_card(0)  # the card is a surface of its own
    cards.clock.advance(1000)
    assert cards.open == {0}
    cards.intent.left_card(0)
    cards.clock.advance(hover_intent.CLOSE_MS + 10)
    assert cards.open == set()


def test_pointer_never_replaces_a_pinned_card():
    cards = List()
    cards.open_row(0)
    cards.intent.pin(2)
    assert cards.open == set()  # the pointer's card closed for the pinned one
    cards.open.add(2)
    cards.rest((150, 4 * ROW + 24), 2000)
    assert cards.open == {2}
    cards.open.discard(2)
    cards.intent.closed(2)
    cards.move((150, 4 * ROW + 24))
    assert cards.open == {4}


def test_escape_closes_card_until_pointer_leaves():
    cards = List()
    cards.open_row(1)
    assert cards.intent.escape()
    assert cards.open == set()
    cards.rest((160, 72), 2000)
    assert cards.open == set()
    cards.move((150, 4 * ROW + 24))  # another row
    cards.move((150, 72))
    assert cards.open == {1}
    assert not List().intent.escape()  # nothing to close: Escape is not taken


def test_card_in_use_stays_open():
    cards = List(held=True)
    cards.open_row(0)
    cards.move((900, 500))
    cards.clock.advance(2000)
    assert cards.open == {0}
    cards.held = False
    cards.move((901, 500))
    cards.clock.advance(hover_intent.CLOSE_MS + 10)
    assert cards.open == set()


def test_scrolling_closes_card_at_once():
    cards = List()
    cards.open_row(0)
    cards.intent.close_now()
    assert cards.open == set()


@pytest.mark.parametrize(
    ("point", "inside"),
    [((5, 5), True), ((0, 0), True), ((10, 0), True), ((6, 6), False)],
)
def test_triangle_contains_its_edges(point, inside):
    assert card_geometry.in_triangle(point, (0, 0), (10, 0), (0, 10)) is inside


def test_a_degenerate_triangle_holds_nothing():
    assert not card_geometry.in_triangle((1, 1), (0, 0), (2, 2), (4, 4))
    assert not card_geometry.in_triangle((0, 0), (0, 0), (0, 0), (0, 0))


def test_region_joins_anchor_card_and_path():
    anchor, card = (0, 0, 300, 48), (308, 0, 608, 450)
    way = card_geometry.safe_triangle((150, 24), card, "right")
    assert card_geometry.contains(anchor, (150, 24))
    assert card_geometry.contains(card, (400, 300))
    assert card_geometry.in_triangle((290, 70), *way)
    point = (150, 72)
    assert not card_geometry.contains(anchor, point) and not card_geometry.contains(
        card, point
    )
    assert not card_geometry.in_triangle(point, *way)


def test_side_card_stays_level_inside_window():
    window = (1280, 800)
    # At the edge it is given, level with its row.
    assert card_geometry.card_box(
        "right", (0, 100, 300, 148), 312, (300, 200), window
    ) == (
        312,
        100,
        612,
        300,
    )
    # Too tall for the room below, but the room holds three rows: it stays
    # level and is cut to the room, which then scrolls.
    assert card_geometry.card_box(
        "right", (0, 500, 300, 548), 312, (300, 400), window
    ) == (
        312,
        500,
        612,
        792,
    )
    # Less room than that: it rises to stay 8 px inside.
    _x0, top, _x1, bottom = card_geometry.card_box(
        "right", (0, 700, 300, 748), 312, (300, 300), window
    )
    assert (top, bottom) == (492, 792)
    # Taller than the window: cut to the window less the margins.
    _x0, top, _x1, bottom = card_geometry.card_box(
        "right", (0, 0, 300, 48), 312, (300, 2000), window
    )
    assert (top, bottom) == (8, 792)
    # Wider than the room beside the row: narrowed to end 8 px inside.
    x0, _y0, x1, _y1 = card_geometry.card_box(
        "right", (0, 100, 300, 148), 312, (360, 200), (640, 800)
    )
    assert (x0, x1) == (312, 632)


def test_card_above_bar_is_centered():
    window = (1280, 800)
    assert card_geometry.card_box(
        "top", (600, 740, 640, 780), 728, (120, 220), window
    ) == (
        560,
        508,
        680,
        728,
    )
    # Pushed inside the window by the margin.
    x0, _y0, _x1, _y1 = card_geometry.card_box(
        "top", (12, 740, 52, 780), 728, (120, 220), window
    )
    assert x0 == 8
    # Room above is the edge less the margin.
    _x0, y0, _x1, y1 = card_geometry.card_box(
        "top", (600, 740, 640, 780), 100, (120, 220), window
    )
    assert (y0, y1) == (8, 100)


def _filled(polygons):
    """Points on a 1 px grid inside the shape, as GSK would fill it."""
    gi = pytest.importorskip("gi")
    gi.require_version("Gsk", "4.0")
    gi.require_version("Graphene", "1.0")
    from gi.repository import Graphene, Gsk

    path = hc._path(polygons)
    x0, y0, x1, y1 = card_geometry.shape_bounds(polygons)
    return {
        (x, y)
        for x in range(int(x0), int(x1) + 1)
        for y in range(int(y0), int(y1) + 1)
        if path.in_fill(Graphene.Point().init(x + 0.5, y + 0.5), Gsk.FillRule.WINDING)
    }


def test_bar_card_arrow_points_at_control():
    control, card = (600, 740, 640, 780), (560, 508, 680, 728)
    x0, y0, x1, y1 = card_geometry.shape_bounds(
        [card_geometry.arrow_shape("top", control, card)]
    )
    # Centered on the control, from inside the card toward it.
    assert (x0, x1) == (608, 632)
    assert (y0, y1) == (727, 728 + card_geometry.ARROW_HEIGHT)
    # A control near the card's corner: the arrow stays on its straight edge.
    x0, _y0, _x1, _y1 = card_geometry.shape_bounds(
        [card_geometry.arrow_shape("top", (540, 740, 560, 780), card)]
    )
    assert x0 == 560 + card_geometry.TAB_RADIUS


def test_bridge_carries_row_ring_into_card():
    ring, card = (12, 103, 300, 157), (318, 103, 640, 400)
    polygons, (top, bottom), joined_start, joined_end, shade = (
        card_geometry.bridge_shape("right", ring, card)
    )
    assert joined_start and not joined_end
    filled = _filled(polygons)
    # As tall as the ring, from inside it (its band and 4 px more) over the
    # card's own ring.
    assert all((x, y) in filled for x in range(293, 320) for y in range(103, 157))
    assert (300, 102) not in filled and (300, 157) not in filled
    # Below, the card goes on: the bridge meets it through a 12 px corner.
    assert (317, 165) in filled and (310, 165) not in filled
    w = card_geometry.RING / 2
    assert top == [("M", 293, 103 + w), ("L", 318 + card_geometry.RING, 103 + w)]
    assert bottom[0] == ("M", 293, 157 - w) and bottom[-1][-2:] == (318 + w, 169)
    # Its shadow grows from where it leaves the list to where it meets the
    # card.
    assert shade == ((300, 130), (318, 130))
