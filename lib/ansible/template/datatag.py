from __future__ import annotations

import dataclasses
import typing as t

from ansible.module_utils.datatag import AnsibleSingletonTagBase, _tag_dataclass_kwargs
from ansible.module_utils.datatag.tags import Deprecated
from ansible.utils.datatag.tags import AnsibleSourcePosition, NotATemplate
from ansible.template._access import NotifiableAccessContextBase
from ansible.template.utils import TemplateContext
from ansible.utils.display import Display


display = Display()


@dataclasses.dataclass(**_tag_dataclass_kwargs)
class _JinjaConstTemplate(AnsibleSingletonTagBase):
    # deprecated: description='embedded Jinja constant string template support' core_version='2.21'
    # DTFIX-MERGE: this isn't covered by ansible-test unit tests (running unit tests in PyCharm finds it due to lack of isolation)
    pass


@dataclasses.dataclass(frozen=True)
class TrippedDeprecationInfo:
    template: str
    deprecated: Deprecated


class DeprecatedAccessAuditContext(NotifiableAccessContextBase):
    """When active, captures metadata about managed accesses to `Deprecated` tagged objects."""
    _type_interest = frozenset([Deprecated])

    def __init__(self) -> None:
        self._tripped_deprecation_info: list[TrippedDeprecationInfo] = []

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        result = super().__exit__(exc_type, exc_val, exc_tb)

        for item in self._tripped_deprecation_info:
            if AnsibleSourcePosition.is_tagged_on(item.template):
                msg = item.deprecated.msg
            else:
                # without a source position, we need to include what context we do have (the template)
                msg = f'While processing {item.template!r}: {item.deprecated.msg}'

            display.deprecated(
                msg=msg,
                version=item.deprecated.removal_version,
                date=item.deprecated.removal_date,
                obj=item.template,
            )

        return result

    def _notify(self, o: t.Any) -> None:
        deprecated = Deprecated.get_required_tag(o)

        template_ctx = TemplateContext.current(optional=True)
        # DTFIX-FUTURE: in cases of indirection/lazy, we need to walk back up to a string template, not a data structure
        template = template_ctx.template_value if template_ctx else None

        # when the current template input is a container, provide a descriptive string with source position propagated (if possible)
        if not isinstance(template, str):
            # DTFIX-FUTURE: ascend the template stack to try and find the nearest string source template
            src_pos = AnsibleSourcePosition.get_tag(template)

            # DTFIX-MERGE: not clear if this is reachable from playbook scenarios; if so, it should probably use a synthesized description value on the tag
            template = '<<container>>'

            if src_pos:
                src_pos.tag(template)

        self._tripped_deprecation_info.append(TrippedDeprecationInfo(
            template=NotATemplate().tag(template),
            deprecated=deprecated,
        ))
