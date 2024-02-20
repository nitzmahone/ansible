from __future__ import annotations

import abc
import collections.abc as c
import contextlib
import dataclasses
import itertools

from jinja2.runtime import Undefined

import ansible.module_utils.compat.typing as t

from ansible.errors import AnsibleUndefinedVariable

from ansible.module_utils.datatag import NotATemplate
from ansible.utils.display import Display

from .utils import Omit
from .jinja_bits import AnsibleUndefined, _finalize_template_result

_display = Display()


# FIXME: bikeshed name/placement/access/singleton
# FIXME: these look an awful lot like Jinja finalizers (which are also owned by the environment); as part of Jinja-normalization, should we wrap this and all
#  other custom post-templating behavior into a Jinja finalizer?
class UndefinedBehavior(metaclass=abc.ABCMeta):
    @abc.abstractmethod
    def handle_undefined(self, value: AnsibleUndefined) -> t.Any:
        ...

    def post_finalize(self, template_result: t.Any) -> t.Any:
        return template_result


# FUTURE: do we want an option to let these accumulate so we can report > 1 failure at a time?
class FailOnUndefined(UndefinedBehavior):
    def handle_undefined(self, value: AnsibleUndefined) -> t.Any:
        if isinstance(value, Undefined):
            msg = value._undefined_message
        else:
            msg = '<no hint>'

        # FIXME: improve this
        raise AnsibleUndefinedVariable(msg, obj=value)


FAIL_ON_UNDEFINED: t.Final = FailOnUndefined()  # no sense in making many instances...


@dataclasses.dataclass(frozen=True)
class UndefinedTracker:
    number: int
    value: AnsibleUndefined


class BestEffort(UndefinedBehavior):
    def __init__(self) -> None:
        self._undefined_templates: list[UndefinedTracker] = []

    def handle_undefined(self, value: AnsibleUndefined) -> t.Any:
        number = len(self._undefined_templates) + 1

        self._undefined_templates.append(UndefinedTracker(number=number, value=value))

        return f"<< error #{number} - {value._undefined_message} >>"

    @property
    def has_warnings(self) -> bool:
        return bool(self._undefined_templates)

    def warnings(self) -> c.Generator[str, None, None]:
        from .templar import Templar  # FIXME: solve this recursive import some other way

        grouped_templates = itertools.groupby(self._undefined_templates, key=lambda item: item.value._undefined_template_source)

        for template, items in grouped_templates:
            items = list(items)

            msg = f'Encountered {len(items)} error(s) templating {Templar._repr_from(template)}:'

            for item in items:
                msg += f'\n{item.number}) {item.value._undefined_message}'  # FIXME: avoid using private property

            yield NotATemplate().tag(msg)

    def emit_warnings(self) -> None:
        for warning in self.warnings():
            _display.warning(warning)

    @classmethod
    @contextlib.contextmanager
    def warning_context(cls) -> t.Self:
        best_effort = cls()

        try:
            yield best_effort
        finally:
            best_effort.emit_warnings()


class BestEffortOmitUndefined(BestEffort):
    def handle_undefined(self, value: AnsibleUndefined) -> t.Any:
        super().handle_undefined(value)

        return Omit

    def post_finalize(self, template_result: t.Any) -> t.Any:
        if not self.has_warnings:
            return template_result

        # there were warnings, which means we emitted omits that need omitting into the template result
        # do another finalize pass to clean it up
        return _finalize_template_result(template_result, raise_on_unsupported_type=False)
