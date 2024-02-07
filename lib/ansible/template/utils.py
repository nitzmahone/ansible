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
    def __init__(self, *, template_value: t.Any, templar: Templar, options: TemplateOptions):
        self._template_value = template_value
        self._templar = templar
        self._options = options

    @property
    def template_value(self) -> t.Any:
        return self._template_value

    @property
    def templar(self) -> Templar:
        return self._templar

    @property
    def options(self):
        return self._options


class AnsibleUndefined(StrictUndefined):
    """A custom Undefined class, which returns further Undefined objects on access, rather than throwing an exception."""

    __slots__ = ('_undefined_template_source',)

    def __init__(
            self,
            hint: t.Optional[str] = None,
            obj: t.Any = missing,
            name: t.Optional[str] = None,
            *args,
            template_source: str | None = None,
            **kwargs,
    ):
        if not hint and name and obj is not missing:
            obj_type_name = (obj.native_type if isinstance(obj, AnsibleTaggedObject) else type(obj)).__name__
            hint = f"object of type {obj_type_name!r} has no attribute {name!r}"

        kwargs.update(hint=hint, obj=obj, name=name)
        super().__init__(*args, **kwargs)
        self._undefined_template_source = template_source

    def __getattr__(self, name):
        # Return original Undefined object to preserve the first failure context
        return self

    def __getitem__(self, key):
        # Return original Undefined object to preserve the first failure context
        return self

    def __repr__(self):
        return 'AnsibleUndefined(hint={0!r}, obj={1!r}, name={2!r})'.format(
            self._undefined_hint,
            self._undefined_obj,
            self._undefined_name
        )

    def __contains__(self, item):
        # Return original Undefined object to preserve the first failure context
        return self


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
