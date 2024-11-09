"""Runtime projections to provide template/var-visible views of objects that are not natively allowed in Ansible's type system."""

from __future__ import annotations

import typing as t

from ansible.errors import get_chained_message
from ansible.module_utils._internal import _traceback
from ansible.module_utils.common.messages import ErrorDetail, WarningMessageDetail, DeprecationMessageDetail
from ansible.template.jinja_common import VaultExceptionMarker
from ansible.parsing.vault import EncryptedString, VaultHelper
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


def decrypt_string(value: EncryptedString) -> str | VaultExceptionMarker:
    """Decrypt an encrypted string and return its value, or a VaultExceptionMarker if decryption fails."""
    try:
        return value._decrypt()
    except Exception as ex:
        return VaultExceptionMarker(
            ciphertext=VaultHelper.get_ciphertext(value, preserve_tags=True),
            reason=get_chained_message(ex),
            traceback=_traceback.maybe_extract_traceback(ex, _traceback.TracebackEvent.ERROR),
        )


_type_transform_mapping: dict[type, t.Callable[[t.Any], t.Any]] = {
    ErrorDetail: error_detail,
    WarningMessageDetail: warning_message_detail,
    DeprecationMessageDetail: deprecation_message_detail,
    EncryptedString: decrypt_string,
}
"""This mapping is consulted by `Templar.template` to provide custom views of some objects."""
