from __future__ import annotations

import typing as t

from ansible.errors import AnsibleFilterError
from ansible.module_utils.datatag import AnsibleTaggedObject, AnsibleDatatagBase


# FIXME: Is this test needed?
def tagged(value: t.Any) -> bool:
    return isinstance(value, AnsibleTaggedObject)


# FIXME: FDI014: Should there be separate tests for each tag type, like `is untrusted_as_template`?
def tagged_with(value: t.Any, tag_name: str) -> bool:
    # noinspection PyProtectedMember
    if tag_type := AnsibleDatatagBase._known_tag_type_map.get(tag_name):
        return tag_type.is_tagged_on(value)

    raise AnsibleFilterError(f"Unknown tag name: {tag_name}")


class TestModule(object):
    """Ansible data tagging test plugins."""

    def tests(self):
        return {
            'tagged': tagged,
            'tagged_with': tagged_with,
        }
