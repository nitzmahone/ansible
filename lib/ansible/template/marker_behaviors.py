"""Handling of `DeferredMarker` values."""

from __future__ import annotations

import abc
import contextlib
import dataclasses
import itertools
import typing as t

from ..utils.display import Display
from .jinja_common import DeferredMarker


class MarkerBehavior(metaclass=abc.ABCMeta):
    """Base class to support custom handling of `DeferredMarker` values encountered during concatenation or finalization."""

    @abc.abstractmethod
    def handle_marker(self, value: DeferredMarker) -> t.Any:
        """Handle the given `DeferredMarker` value."""


class FailingMarkerBehavior(MarkerBehavior):
    """
    The default behavior when encountering a `DeferredMarker` value during concatention or finalization.
    This always raises the template-internal `DeferredMarkerError` exception.
    """

    def handle_marker(self, value: DeferredMarker) -> t.Any:
        value.trip()


# FAIL_ON_MARKER_BEHAVIOR
# _DETONATE_MARKER_BEHAVIOR - internal singleton since it's the default and nobody should need to reference it, or make it an actual singleton
FAIL_ON_UNDEFINED: t.Final = FailingMarkerBehavior()  # no sense in making many instances...


@dataclasses.dataclass(kw_only=True, slots=True, frozen=True)
class _DeferredMarkerTracker:
    """A numbered occurrence of a `DeferredMarker` value for later conversion to a warning."""

    number: int
    value: DeferredMarker


class ReplacingMarkerBehavior(MarkerBehavior):
    """All `DeferredMarker` values are replaced with a numbered string placeholder and the message from the value."""

    def __init__(self) -> None:
        self._trackers: list[_DeferredMarkerTracker] = []

    def record_marker(self, value: DeferredMarker) -> t.Any:
        """Assign a sequence number to the given value and record it for later generation of warnings."""
        number = len(self._trackers) + 1

        self._trackers.append(_DeferredMarkerTracker(number=number, value=value))

        return number

    def emit_warnings(self) -> None:
        """Emit warning messages caused by Marker values, aggregated by unique template."""

        display = Display()
        grouped_templates = itertools.groupby(self._trackers, key=lambda tracker: tracker.value._marker_template_source)

        for template, items in grouped_templates:
            item_list = list(items)

            msg = f'Encountered {len(item_list)} template error{"s" if len(item_list) > 1 else ""}.'

            for item in item_list:
                msg += f'\nerror {item.number} - {item.value._as_message()}'

            display.warning(msg=msg, obj=template)

    @classmethod
    @contextlib.contextmanager
    def warning_context(cls) -> t.Generator[t.Self, None, None]:
        """Collect warnings for `DeferredMarker` values and emit warnings when the context exits."""
        instance = cls()

        try:
            yield instance
        finally:
            instance.emit_warnings()

    def handle_marker(self, value: DeferredMarker) -> t.Any:
        number = self.record_marker(value)

        return f"<< error {number} - {value._as_message()} >>"


class RoutingMarkerBehavior(MarkerBehavior):
    """Routes instances of DeferredMarker (by type reference) to another MarkerBehavior, defaulting to FailingMarkerBehavior."""

    def __init__(self, dispatch_table: dict[type[DeferredMarker], MarkerBehavior]) -> None:
        self._dispatch_table = dispatch_table

    def handle_marker(self, value: DeferredMarker) -> t.Any:
        behavior = self._dispatch_table.get(type(value), FAIL_ON_UNDEFINED)

        return behavior.handle_marker(value)
