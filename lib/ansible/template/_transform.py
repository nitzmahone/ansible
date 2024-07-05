"""Runtime projections to provide template/var-visible views of objects that are not natively allowed in Ansible's type system."""

from __future__ import annotations

import typing as t

from ansible.module_utils.common.messages import ErrorDetail, WarningMessageDetail, DeprecationMessageDetail
from ansible.module_utils.datatag import _AnsibleTaggedSet, _AnsibleTaggedTuple, AnsibleTagHelper
from ansible.utils.display import Display

display = Display()


def error_detail(value: ErrorDetail) -> str:
    """Render ErrorDetail as a formatted traceback for backward-compatibility with pre-2.18 TaskResult.exception."""
    return value.formatted_traceback or '(traceback unavailable)'


def warning_message_detail(value: WarningMessageDetail) -> str:
    """Render WarningMessageDetail as a simple message string for backward-compatibility with pre-2.18 TaskResult.warnings."""
    return value.msg


def deprecation_message_detail(value: DeprecationMessageDetail) -> dict[str, t.Any]:
    """Render DeprecationMessageDetail as dict values for backward-compatibility with pre-2.18 TaskResult.deprecations."""
    if value.date is not None:
        return dict(msg=value.msg, date=value.date, collection_name=value.collection_name)

    return dict(msg=value.msg, version=value.version, collection_name=value.collection_name)


def as_list(value: set | tuple) -> list:
    """Render sets and tuples as lists for backward-compatibility with pre-2.18 behavior."""
    display.deprecated(f'Variables of type {type(value)} are not supported. Converted to a {list}.', obj=value, version='2.22')

    return AnsibleTagHelper.tag_copy(value, iter(value), value_type=list)


_type_transform_mapping: dict[type, t.Callable[[t.Any], t.Any]] = {
    ErrorDetail: error_detail,
    WarningMessageDetail: warning_message_detail,
    DeprecationMessageDetail: deprecation_message_detail,
    set: as_list,
    _AnsibleTaggedSet: as_list,
    tuple: as_list,
    _AnsibleTaggedTuple: as_list,
}
"""This mapping is consulted by _AnsibleLazyTemplateMixin._try_create to provide custom views of some objects to templating/vars."""
