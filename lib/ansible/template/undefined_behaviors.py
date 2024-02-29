"""Handling of `AnsibleUndefined` values."""

from __future__ import annotations

import abc
import collections.abc as c
import contextlib
import dataclasses
import itertools

from ..errors import AnsibleUndefinedVariable
from ..module_utils.compat import typing as t
from ..module_utils.datatag import NotATemplate
from ..utils.display import Display
from .jinja_bits import AnsibleUndefined, _finalize_template_result, FinalizeMode
from .utils import Omit, _repr_from, TemplateContext


class UndefinedBehavior(metaclass=abc.ABCMeta):
    """
    Base class to support custom handling of `AnsibleUndefined` values encountered during concatenation or finalization.
    """

    @abc.abstractmethod
    def handle_undefined(self, value: AnsibleUndefined, mode: FinalizeMode) -> t.Any:
        """Handle the given undefined value."""

    def post_finalize(self, template_result: t.Any) -> t.Any:
        """Perform any necessary tasks after template finalization has occurred."""
        return template_result


class FailOnUndefined(UndefinedBehavior):
    """
    The default behavior when encountering an `AnsibleUndefined` value during concatention or finalization.
    Raises the template-external `AnsibleUndefinedVariable` exception for top-level `template()` calls.
    In all other cases, the template-internal `AnsibleUndefinedError` exception is raised instead.
    """

    def handle_undefined(self, value: AnsibleUndefined, mode: FinalizeMode) -> t.Any:
        """Handle the given undefined value."""
        if TemplateContext.current_or_raise().is_top_level:
            raise AnsibleUndefinedVariable(value._undefined_message)  # exiting the templating system, use the external exception type

        value.trip()  # not exiting templating yet, use an internal exception which can be converted back to AnsibleUndefined


FAIL_ON_UNDEFINED: t.Final = FailOnUndefined()  # no sense in making many instances...


@dataclasses.dataclass(kw_only=True, slots=True, frozen=True)
class _UndefinedTracker:
    """
    A numbered occurrence of an `AnsibleUndefined` value for later conversion to a warning.
    """

    number: int
    value: AnsibleUndefined


class _CollectWarningsBehavior(UndefinedBehavior, metaclass=abc.ABCMeta):
    """
    Base class to support custom handling of `AnsibleUndefined` values which includes generating warnings about those values.
    """

    def __init__(self) -> None:
        self._undefined_templates: list[_UndefinedTracker] = []

    def record_undefined(self, value: AnsibleUndefined) -> t.Any:
        """Assign a sequence number to the given value and record it for later generation of warnings."""
        number = len(self._undefined_templates) + 1

        self._undefined_templates.append(_UndefinedTracker(number=number, value=value))

        return number

    @property
    def has_warnings(self) -> bool:
        """Return True if any undefined values have been encountered, otherwise False."""
        return bool(self._undefined_templates)

    def warnings(self) -> c.Generator[str, None, None]:
        """Yield warning messages from undefined values, aggregated by unique template."""
        grouped_templates = itertools.groupby(self._undefined_templates, key=lambda tracker: tracker.value._undefined_template_source)

        for template, items in grouped_templates:
            item_list = list(items)

            msg = f'Encountered {len(item_list)} error(s) templating {_repr_from(template)}:'

            for item in item_list:
                msg += f'\n{item.number} - {item.value._undefined_message}'

            yield NotATemplate().tag(msg)

    def emit_warnings(self) -> None:
        """Emit warning messages for undefined values, aggregated by unique template."""
        display = Display()

        for warning in self.warnings():
            display.warning(warning)

    @classmethod
    @contextlib.contextmanager
    def warning_context(cls) -> t.Generator[t.Self, None, None]:
        """Collect warnings for undefined values and emit warnings when the context exits."""
        instance = cls()

        try:
            yield instance
        finally:
            instance.emit_warnings()


class ReplaceUndefined(_CollectWarningsBehavior):
    """
    All `AnsibleUndefined` values are replaced with a numbered string placeholder and the message from the value.
    This feature currently exists for use by the `debug` action and templating of task names.
    """

    def handle_undefined(self, value: AnsibleUndefined, mode: FinalizeMode) -> t.Any:
        """Handle the given undefined value."""
        number = self.record_undefined(value)

        return f"<< error {number} - {value._undefined_message} >>"


class OmitUndefined(_CollectWarningsBehavior):
    """
    During concatenation, the first `AnsibleUndefined` encountered causes the result of the current `template()` call to be `Omit`.
    Values of `Omit` and `AnsibleUndefined` in containers are omitted from the container.
    If the final top-level result of the template operation is `Omit`, an `AnsibleValueOmittedError` is raised.
    This feature currently exists only to support the first_found lookup, which skips searches for files/paths which contain an undefined value.
    """

    def handle_undefined(self, value: AnsibleUndefined, mode: FinalizeMode) -> t.Any:
        """Handle the given undefined value."""
        if mode == FinalizeMode.CONCAT:
            # The caller is `concat`, which translates the `AnsibleUndefinedError` this raises back into `AnsibleUndefined`.
            # The value is not recorded here, since it will be processed during finalization.
            value.trip()

        super().record_undefined(value)

        return Omit

    def post_finalize(self, template_result: t.Any) -> t.Any:
        """Perform any necessary tasks after template finalization has occurred."""
        if not self.has_warnings:
            return template_result

        # there were warnings, which means we emitted omits that need omitting into the template result
        # do another finalize pass to clean it up
        return _finalize_template_result(template_result, mode=FinalizeMode.POST_FINALIZE)
