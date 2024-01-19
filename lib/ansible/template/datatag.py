from __future__ import annotations

from ansible.module_utils.compat import typing as t
from ansible.module_utils.datatag import Deprecated
from ansible.module_utils.datatag.access import _NotifiableAccessContextBase, POORLY_NAMED_SENTINEL
from ansible.template.utils import TemplateContext


class DeprecatedAccessAuditContext(_NotifiableAccessContextBase):
    _tag_type_interest = frozenset([Deprecated])

    def __init__(self) -> None:
        self._tripped_deprecation_info: t.List[t.Tuple[t.Any, Deprecated]] = []

    def _notify(self, o: t.Any) -> t.Any:
        deprecated = Deprecated.get_tag(o)

        if deprecated:
            current_template = TemplateContext.current()
            template = current_template.template_value if current_template else None
            self._tripped_deprecation_info.append((template, deprecated))

        return POORLY_NAMED_SENTINEL

    @property
    def deprecated_access(self) -> t.Tuple[t.Tuple[t.Any, Deprecated], ...]:
        return tuple(self._tripped_deprecation_info)
