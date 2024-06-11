from __future__ import annotations

import dataclasses
import typing as t

from ansible.module_utils.datatag import AnsibleSingletonTagBase, _tag_dataclass_kwargs
from ansible.module_utils.datatag.tags import Deprecated
from ansible.utils.datatag.tags import AnsibleSourcePosition, TrustedAsTemplate, NotATemplate
from ansible.module_utils.datatag.access import _NotifiableAccessContextBase, _MutatingAccessContextBase, POORLY_NAMED_SENTINEL
from ansible.template.utils import TemplateContext
from ansible.utils.display import Display


display = Display()


@dataclasses.dataclass(**_tag_dataclass_kwargs)
class _JinjaConstTemplate(AnsibleSingletonTagBase):
    # deprecated: description='embedded Jinja constant string template support' core_version='2.21'
    # FIXME: this isn't covered by ansible-test unit tests (PyCharm finds it by accident)
    pass


class _RenderJinjaConstAsTemplate(_MutatingAccessContextBase):
    # deprecated: description='embedded Jinja constant string template support' core_version='2.21'
    _tag_type_interest = frozenset([_JinjaConstTemplate])

    def _notify(self, o: t.Any) -> t.Any:
        return TemplateContext.current().templar.proxy_or_render_template(TrustedAsTemplate().tag(_JinjaConstTemplate.untag(o)))


class DeprecatedAccessAuditContext(_NotifiableAccessContextBase):
    _tag_type_interest = frozenset([Deprecated])

    def __init__(self) -> None:
        self._tripped_deprecation_info: list[tuple[str, Deprecated]] = []

    def _notify(self, o: t.Any) -> t.Any:
        deprecated = Deprecated.get_tag(o)

        if deprecated:
            template_ctx = TemplateContext.current(optional=True)
            # FIXME: in cases of indirection/lazy, we need to walk back up to a string template, not a data structure
            template = template_ctx.template_value if template_ctx else None

            # when the current template input is a container, provide a descriptive string with source position propagated (if possible)
            if not isinstance(template, str):
                # FIXME: ascend the template stack to try and find the nearest string source template
                src_pos = AnsibleSourcePosition.get_tag(template)

                template = '<<container>>'

                if src_pos:
                    src_pos.tag(template)

            # FIXME: use something better than a tuple here, once we have proper intermediate object/template contexts to reference
            self._tripped_deprecation_info.append((NotATemplate().tag(template), deprecated))

        return POORLY_NAMED_SENTINEL

    @property
    def deprecated_access(self) -> tuple[tuple[str, Deprecated], ...]:
        return tuple(self._tripped_deprecation_info)
