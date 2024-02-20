from __future__ import annotations

import typing as t

from jinja2 import TemplateRuntimeError, UndefinedError
from jinja2.utils import missing
from jinja2.runtime import StrictUndefined

from ansible.module_utils import datatag
from ansible.module_utils.datatag import AnsibleTaggedObject
from ansible.module_utils.datatag.access import AmbientContextBase

if t.TYPE_CHECKING:
    from .templar import Templar, TemplateOptions


class TemplateContext(AmbientContextBase):
    def __init__(self, *, template_value: t.Any, templar: Templar, options: TemplateOptions, stop_on_template: bool):
        self._template_value = template_value
        self._templar = templar
        self._options = options
        self._stop_on_template = stop_on_template
        self._parent_ctx = TemplateContext.current()

    @property
    def template_value(self) -> t.Any:
        return self._template_value

    @property
    def templar(self) -> Templar:
        return self._templar

    @property
    def options(self) -> TemplateOptions:
        return self._options

    @property
    def stop_on_template(self) -> bool:
        return self._stop_on_template


class AnsibleUndefinedError(UndefinedError):
    """
    An Ansible specific subclass of Jinja's UndefinedError, used to preserve and later restore the original AnsibleUndefined value that raised the error.
    This error is only raised by AnsibleUndefined and should never escape the templating system.
    """
    def __init__(self, message: str, source: AnsibleUndefined):
        super().__init__(message)

        self.source = source


class AnsibleUndefined(StrictUndefined):
    """A custom Undefined class, which returns further Undefined objects on access, rather than throwing an exception."""

    __slots__ = ('_undefined_template_source',)

    def __init__(
        self,
        hint: t.Optional[str] = None,
        obj: t.Any = missing,
        name: t.Optional[str] = None,
        exc: t.Type[TemplateRuntimeError] = UndefinedError,
        *args,
        **kwargs,
    ) -> None:
        if not hint and name and obj is not missing:
            obj_type_name = (obj.native_type if isinstance(obj, AnsibleTaggedObject) else type(obj)).__name__
            hint = f"object of type {obj_type_name!r} has no attribute {name!r}"

        kwargs.update(
            hint=hint,
            obj=obj,
            name=name,
            exc=exc,
        )

        super().__init__(*args, **kwargs)

        self._undefined_template_source = TemplateContext.current_or_raise().template_value

    # FIXME: we should probably intercept the dunder methods calling this instead -- and then make sure this function complains loudly if it is called
    def _fail_with_undefined_error(self, *args: t.Any, **kwargs: t.Any) -> t.NoReturn:
        raise AnsibleUndefinedError(self._undefined_message, self)

    def __getattr__(self, name):
        if name[:2] == "__":
            raise AttributeError(name)

        return self

    def __getitem__(self, key):
        return self

    # FIXME: do this right, have thorough tests to catch anything that slips through
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


class _OmitType:
    """
    A placeholder singleton used to dynamically omit items from a dict/list/tuple/set when the value is `Omit`.

    The Omit singleton value is accessible from all Ansible templating contexts via the Jinja global
    name `omit`. Item removal occurs during final recursive processing of template results. The singleton
    `Omit` placeholder value will be visible to plugins during templating. The only time a template result
    will include `Omit` outside a templating context is when the template renders to the scalar value `Omit`.
    """
    __slots__ = ()

    # FIXME: this keeps pickle happy, but not JSON/YAML for callbacks; just teach them about it?
    def __new__(cls):
        return Omit

    def __repr__(self):
        return "<<Omit>>"


Omit = object.__new__(_OmitType)

# FIXME: decide if these should be taggable; do we need to support other kinds of Undefineds, etc
datatag._untaggable_types |= {AnsibleUndefined, type(Omit)}
