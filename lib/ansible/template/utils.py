from __future__ import annotations

import typing as t

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
    def options(self):
        return self._options

    @property
    def stop_on_template(self):
        return self._stop_on_template


class AnsibleUndefined(StrictUndefined):
    """A custom Undefined class, which returns further Undefined objects on access, rather than throwing an exception."""

    __slots__ = ('_undefined_template_source',)
    __repr__ = __getattr__ = __getitem__ = StrictUndefined._fail_with_undefined_error

    def __init__(
            self,
            hint: t.Optional[str] = None,
            obj: t.Any = missing,
            name: t.Optional[str] = None,
            *args,
            **kwargs,
    ):
        if not hint and name and obj is not missing:
            obj_type_name = (obj.native_type if isinstance(obj, AnsibleTaggedObject) else type(obj)).__name__
            hint = f"object of type {obj_type_name!r} has no attribute {name!r}"

        kwargs.update(hint=hint, obj=obj, name=name)
        super().__init__(*args, **kwargs)

        # FIXME: figure out how to preserve the template context where the undefined originated when we're simply re-creating an AnsibleUndefined after
        #        catching an UndefinedError raised from touching an AnsibleUndefined, specifically in jinja_bits._flatten_nodes
        self._undefined_template_source = TemplateContext.current_or_raise().template_value


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
