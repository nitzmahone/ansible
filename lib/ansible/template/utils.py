from __future__ import annotations

import typing as t

from ansible.module_utils import datatag
from ansible.module_utils.datatag.access import AmbientContextBase
from ansible.module_utils.datatag import AnsibleSourcePosition

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
datatag._untaggable_types |= {type(Omit)}


def _repr_from(value: t.Any) -> str:
    """Return the repr() of the given value, appending attribution of the source position, if available."""
    # FIXME: FDI028 - initial prototype, is this what we want?
    #        should it be part of our public interface?
    #        should this be part of AnsibleSourcePosition or otherwise in the datatag module_utils?

    # FIXME: need to elide container values and large strings
    src_pos = AnsibleSourcePosition.get_tag(value)

    if src_pos:
        return f'{value!r} from {str(src_pos)!r}'

    return f'{value!r}'
