from __future__ import annotations

from ansible.module_utils.compat import typing as t
from ansible.module_utils.datatag import Deprecated, NotATemplate, AnsibleSourcePosition
from ansible.module_utils.datatag.access import _NotifiableAccessContextBase, POORLY_NAMED_SENTINEL

from .utils import TemplateContext


class DeprecatedAccessAuditContext(_NotifiableAccessContextBase):
    _tag_type_interest = frozenset([Deprecated])

    def __init__(self) -> None:
        self._tripped_deprecation_info: list[tuple[str, Deprecated]] = []

    def _notify(self, o: t.Any) -> t.Any:
        deprecated = Deprecated.get_tag(o)

        if deprecated:
            template_ctx = TemplateContext.current()
            # FIXME: in cases of indirection/lazy, we need to walk back up to a string template, not a data structure
            template = template_ctx.template_value if template_ctx else None

            # when the current template input is a container, provide a descriptive string with source position propagated (if possible)
            if not isinstance(template, str):
                # FIXME: ascend the template stack to try and find the nearest string source template
                src_pos = AnsibleSourcePosition.get_tag(template)

                template = f'<<container>>'

                if src_pos:
                    src_pos.tag(template)

            self._tripped_deprecation_info.append((NotATemplate().tag(template), deprecated))

        return POORLY_NAMED_SENTINEL

    @property
    def deprecated_access(self) -> tuple[tuple[str, Deprecated], ...]:
        return tuple(self._tripped_deprecation_info)
