from __future__ import annotations

import collections.abc as c
import dataclasses
import itertools
import typing as t

from jinja2 import UndefinedError, StrictUndefined, TemplateRuntimeError
from jinja2.utils import missing

from ansible.constants import config
from ansible.module_utils import datatag as _mu_datatag
from ansible.module_utils.datatag import Tripwire, AnsibleTagHelper

from .utils import TemplateContext
from ..module_utils._internal import _ambient_context


class _TemplateConfig:
    allow_embedded_templates = config.get_config_value("ALLOW_EMBEDDED_TEMPLATES")
    allow_broken_conditionals = config.get_config_value('ALLOW_BROKEN_CONDITIONALS')
    jinja_extensions = config.get_config_value('DEFAULT_JINJA2_EXTENSIONS')
    raise_on_trust_check_fail = False  # DTFIX-U: make this configurable with multiple options (warn, warn_with_trace, error, ignore, etc)


class AnsibleUndefinedError(UndefinedError):
    """
    An Ansible specific subclass of Jinja's UndefinedError, used to preserve and later restore the original AnsibleUndefined value that raised the error.
    This error is only raised by AnsibleUndefined and should never escape the templating system.
    """

    # DTFIX-U: give this class a name that reflects its usage as an internal-only flow control exception

    def __init__(self, message: str, source: AnsibleUndefined):
        super().__init__(message)

        self.source = source


class AnsibleUndefined(StrictUndefined, Tripwire):
    """A custom Undefined class, which returns further Undefined objects on access, rather than throwing an exception."""

    __slots__ = ('_undefined_template_source',)

    def __init__(
        self,
        hint: t.Optional[str] = None,
        obj: t.Any = missing,
        name: t.Optional[str] = None,
        exc: t.Type[TemplateRuntimeError] = UndefinedError,
        *args,
        _no_template_source=False,
        **kwargs,
    ) -> None:
        if not hint and name and obj is not missing:
            hint = f"object of type {AnsibleTagHelper.get_friendly_type_name(obj)!r} has no attribute {name!r}"

        kwargs.update(
            hint=hint,
            obj=obj,
            name=name,
            exc=exc,
        )

        super().__init__(*args, **kwargs)

        if _no_template_source:
            self._undefined_template_source = None
        else:
            self._undefined_template_source = TemplateContext.current().template_value

    # DTFIX-U: we should probably intercept the dunder methods calling this instead -- and then make sure this function complains loudly if it is called
    def _fail_with_undefined_error(self, *args: t.Any, **kwargs: t.Any) -> t.NoReturn:
        raise AnsibleUndefinedError(self._undefined_message, self)

    def trip(self) -> t.NoReturn:
        self._fail_with_undefined_error()

    def __getattr__(self, name):
        if name[:2] == "__":
            raise AttributeError(name)

        return self

    def __getitem__(self, key):
        return self

    # DTFIX-U: do this right, have thorough tests to catch anything that slips through
    __repr__ = _fail_with_undefined_error
    __iter__ = __str__ = __len__ = _fail_with_undefined_error
    __eq__ = __ne__ = __bool__ = __hash__ = _fail_with_undefined_error
    __contains__ = _fail_with_undefined_error
    __add__ = __radd__ = __sub__ = __rsub__ = _fail_with_undefined_error
    __mul__ = __rmul__ = __div__ = __rdiv__ = _fail_with_undefined_error
    __truediv__ = __rtruediv__ = _fail_with_undefined_error
    __floordiv__ = __rfloordiv__ = _fail_with_undefined_error
    __mod__ = __rmod__ = _fail_with_undefined_error
    __pos__ = __neg__ = _fail_with_undefined_error
    __call__ = _fail_with_undefined_error
    __lt__ = __le__ = __gt__ = __ge__ = _fail_with_undefined_error
    __int__ = __float__ = __complex__ = _fail_with_undefined_error
    __pow__ = __rpow__ = _fail_with_undefined_error


def get_first_undefined_arg(args: c.Sequence, kwargs: dict[str, t.Any]) -> AnsibleUndefined | None:
    """Utility method to inspect plugin args and return the first undefined encountered."""
    # DTFIX-U: this may or may not need to be public API, move back to utils or once usage is wrapped in a decorator?
    for arg in itertools.chain(args, kwargs.values()):
        if type(arg) is AnsibleUndefined:  # pylint:disable=unidiomatic-typecheck
            # DTFIX-U: global config for deprecation warning + return None
            return arg

    return None


@dataclasses.dataclass(kw_only=True)
class JinjaCallContext(_ambient_context.AmbientContextBase):
    """
    A context that wraps all Jinja plugin and method invocations to propagate per-call behaviors to consumers underneath.
    When `eager_trip_undefined=True`, undefined values are automatically "tripped" on retrieval or access when running Jinja
    plugins/functions/methods that have not declared understanding of embedded Undefined objects with `accept_undefined_args`.
    """
    eager_trip_undefined: bool
    _te_invoking_action_name: str | None = None


# DTFIX-U: decide if these should be taggable; do we need to support other kinds of Undefineds, etc
_mu_datatag._untaggable_types |= {AnsibleUndefined}
