"""Handling of `AnsibleUndefined` values."""

from __future__ import annotations

import abc
import contextlib
import dataclasses
import itertools
import typing as t

from ..errors import AnsibleUndefinedVariable
from ..utils.display import Display
from .jinja_bits import FinalizeMode
from .jinja_common import AnsibleUndefined
from .utils import TemplateContext


class UndefinedBehavior(metaclass=abc.ABCMeta):
    """
    Base class to support custom handling of `AnsibleUndefined` values encountered during concatenation or finalization.
    """

    @abc.abstractmethod
    def handle_undefined(self, value: AnsibleUndefined, mode: FinalizeMode) -> t.Any:
        """Handle the given undefined value."""


class FailOnUndefined(UndefinedBehavior):
    """
    The default behavior when encountering an `AnsibleUndefined` value during concatention or finalization.
    Raises the template-external `AnsibleUndefinedVariable` exception for top-level `template()` calls.
    In all other cases, the template-internal `AnsibleUndefinedError` exception is raised instead.
    """

    def handle_undefined(self, value: AnsibleUndefined, mode: FinalizeMode) -> t.Any:
        """Handle the given undefined value."""
        if TemplateContext.current().is_top_level:
            # exiting the templating system, use the external exception type
            raise AnsibleUndefinedVariable(value._undefined_message, obj=value._undefined_template_source)

        value.trip()  # not exiting templating yet, use an internal exception which can be converted back to AnsibleUndefined


FAIL_ON_UNDEFINED: t.Final = FailOnUndefined()  # no sense in making many instances...


@dataclasses.dataclass(kw_only=True, slots=True, frozen=True)
class _UndefinedTracker:
    """
    A numbered occurrence of an `AnsibleUndefined` value for later conversion to a warning.
    """

    number: int
    value: AnsibleUndefined


class ReplaceUndefined(UndefinedBehavior):
    """
    All `AnsibleUndefined` values are replaced with a numbered string placeholder and the message from the value.
    This feature currently exists for use by the `debug` action and templating of task names.
    """

    def __init__(self) -> None:
        self._undefined_templates: list[_UndefinedTracker] = []

    def record_undefined(self, value: AnsibleUndefined) -> t.Any:
        """Assign a sequence number to the given value and record it for later generation of warnings."""
        number = len(self._undefined_templates) + 1

        self._undefined_templates.append(_UndefinedTracker(number=number, value=value))

        return number

    def emit_warnings(self) -> None:
        """Emit warning messages caused by undefined values, aggregated by unique template."""

        display = Display()
        grouped_templates = itertools.groupby(self._undefined_templates, key=lambda tracker: tracker.value._undefined_template_source)

        for template, items in grouped_templates:
            item_list = list(items)

            msg = f'Encountered {len(item_list)} undefined variable(s).'

            for item in item_list:
                msg += f'\nerror {item.number} - {item.value._undefined_message}'

            display.warning(msg=msg, obj=template)

    @classmethod
    @contextlib.contextmanager
    def warning_context(cls) -> t.Generator[t.Self, None, None]:
        """Collect warnings for undefined values and emit warnings when the context exits."""
        instance = cls()

        try:
            yield instance
        finally:
            instance.emit_warnings()

    def handle_undefined(self, value: AnsibleUndefined, mode: FinalizeMode) -> t.Any:
        """Handle the given undefined value."""
        number = self.record_undefined(value)

        return f"<< error {number} - {value._undefined_message} >>"
