from __future__ import annotations

import typing as t

from collections.abc import Callable

from ansible.errors import AnsibleVariableTypeError

from ansible.module_utils.datatag import (AnsibleSourcePosition, AnsibleTaggedObject, TrustedAsTemplate, _ANSIBLE_ALLOWED_VAR_TYPES,
                                          _ANSIBLE_ALLOWED_NON_SCALAR_COLLECTION_VAR_TYPES, _ANSIBLE_ALLOWED_MAPPING_VAR_TYPES, _AnsibleTaggedStr)


_T = t.TypeVar('_T')


# FIXME: bikeshed name and home
class AnsibleVariableVisitor:
    # FIXME: consider adding an option to convert containers to supported types for third-party compatibility scenarios
    def __init__(self, *, trusted_as_template: bool = False, source_position: AnsibleSourcePosition | None = None):
        self.trusted_as_template = trusted_as_template
        self.source_position = source_position

    def visit(self, value: _T) -> _T:
        """
        Enforces Ansible's variable type system restrictions before a var is accepted in inventory. Also conditionally implements template trust
        compatibility, depending on the plugin's declared understanding (or lack thereof). This always recursively copies inputs to fully isolate
        inventory data from what the plugin provided, and prevent any later mutation.
        """
        value_type = type(value)

        if value_type not in _ANSIBLE_ALLOWED_VAR_TYPES:
            raise AnsibleVariableTypeError(variable_type=value_type)

        if value_type in (str, _AnsibleTaggedStr):
            # apply compatibility behavior
            if self.trusted_as_template:
                result = TrustedAsTemplate().tag(value)
            else:
                result = value
        elif value_type in _ANSIBLE_ALLOWED_MAPPING_VAR_TYPES:  # check mappings first, because they're also collections
            result = AnsibleTaggedObject.tag_copy(value, ((k, self.visit(v)) for k, v in value.items()), value_type=value_type)
        elif value_type in _ANSIBLE_ALLOWED_NON_SCALAR_COLLECTION_VAR_TYPES:
            result = AnsibleTaggedObject.tag_copy(value, (self.visit(v) for v in value), value_type=value_type)
        else:
            # supported scalar type that requires no special handling, just return as-is
            result = value

        if self.source_position and not AnsibleSourcePosition.is_tagged_on(result):
            # apply shared instance default source position tag
            result = self.source_position.tag(result)

        return result
