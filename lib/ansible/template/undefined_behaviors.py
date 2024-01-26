from __future__ import annotations

import abc
import collections.abc as c
import itertools

from jinja2.runtime import Undefined

import ansible.module_utils.compat.typing as t

from ansible.errors import AnsibleUndefinedVariable

from ansible.module_utils.datatag import NotATemplate

from .utils import Omit
from .lazy_containers import _finalize_template_result


# FIXME: bikeshed name/placement/access/singleton
# FIXME: these look an awful lot like Jinja finalizers (which are also owned by the environment); as part of Jinja-normalization, should we wrap this and all
#  other custom post-templating behavior into a Jinja finalizer?
class UndefinedBehavior(metaclass=abc.ABCMeta):
    @abc.abstractmethod
    def handle_undefined(self, value: Undefined) -> t.Any:
        ...

    def post_finalize(self, template_result: t.Any) -> t.Any:
        return template_result


# FUTURE: do we want an option to let these accumulate so we can report > 1 failure at a time?
class FailOnUndefined(UndefinedBehavior):
    def handle_undefined(self, value):
        if isinstance(value, Undefined):
            hint = value._undefined_hint
        else:
            hint = None

        if not hint:
            hint = '<no hint>'

        raise AnsibleUndefinedVariable(f"undefined BLAH FIXME: {hint}", obj=value)


FAIL_ON_UNDEFINED: t.Final = FailOnUndefined()  # no sense in making many instances...


class BestEffort(UndefinedBehavior):
    @staticmethod
    def _hint(value: Undefined):
        try:
            # FIXME: need to come up with a better way to capture the context when Jinja gives us not much to go on, or maybe inject our own
            hint = value._undefined_template_source or value._undefined_hint

            if not hint and (hint := value._undefined_name):
                hint = f'{{{{ {hint} }}}}'
        except AttributeError:
            hint = None

        if not hint:
            hint = "FIXME shrug"

        return hint

    def handle_undefined(self, value: Undefined) -> t.Any:
        return NotATemplate().tag(self._hint(value))


BEST_EFFORT: t.Final = BestEffort()  # no sense in making many instances...


class BestEffortWithWarnings(BestEffort):
    def __init__(self) -> None:
        self._undefined_templates: list[Undefined] = []

    def handle_undefined(self, value: Undefined) -> t.Any:
        self._undefined_templates.append(value)
        # FIXME: figure out how/where to propagate this as a failure to the TemplateResult

        return super().handle_undefined(value)

    @property
    def has_warnings(self) -> bool:
        return bool(self._undefined_templates)

    def warnings(self, max_count: int | None = None) -> c.Generator[str, None, None]:
        # blah = list(f'FIXME busted template {self._hint(w)}' for w in islice(self._undefined_templates, max_count))
        # yield from blah
        for w in itertools.islice(self._undefined_templates, max_count):
            try:
                yield NotATemplate().tag(f'FIXME busted template {self._hint(w)}')
            except Exception as exi:
                raise


class BestEffortOmitUndefined(BestEffortWithWarnings):
    def handle_undefined(self, value: Undefined) -> t.Any:
        self._undefined_templates.append(value)

        return Omit

    def post_finalize(self, template_result: t.Any) -> t.Any:
        if not self.has_warnings:
            return template_result

        # there were warnings, which means we emitted omits that need omitting into the template result
        # do another finalize pass to clean it up
        return _finalize_template_result(template_result, raise_on_unsupported_type=False)
