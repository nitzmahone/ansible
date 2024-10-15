from __future__ import annotations

import typing as t

from ansible._internal import _errors
from ansible.module_utils.common.messages import ErrorDetail, ErrorMessage
from ansible.template.jinja_common import ExceptionMarker
from ansible.utils.datatag.tags import UndecryptableVaultedValue

# noinspection PyProtectedMember
from ansible.module_utils.datatag.access import POORLY_NAMED_SENTINEL, _MutatingAccessContextBase


class UndecryptableVaultError(_errors.AnsibleCapturedError):
    """Template-external error raised by VaultExceptionMarker when an undecryptable variable is accessed."""

    context = 'vault'
    default_prefix = "Attempt to use undecryptable variable."


class UndecryptableAccessMutator(_MutatingAccessContextBase):
    """Track undecryptable values encountered while templating."""

    _tag_type_interest = frozenset([UndecryptableVaultedValue])

    def _notify(self, o: t.Any) -> t.Any:
        # DTFIX-FUTURE: FDI037 - is_tagged_on may not be necessary, depending on layered mutation support
        if UndecryptableVaultedValue.is_tagged_on(o):
            self._tripped = True
            return VaultExceptionMarker(o)

        return POORLY_NAMED_SENTINEL


class VaultExceptionMarker(ExceptionMarker):
    """A `Marker` value that represents an error accessing a vaulted value during templating."""

    __slots__ = ('_marker_undecryptable_vaulted_value',)

    def __init__(self, value: str) -> None:
        # DTFIX-MERGE: when does this show up, should it contain more details?
        #          see also CapturedExceptionMarker for a similar issue
        super().__init__(hint='A vault exception marker was tripped.')

        self._marker_undecryptable_vaulted_value = value

    def _as_exception(self) -> Exception:
        uvv_tag = UndecryptableVaultedValue.get_tag(self._marker_undecryptable_vaulted_value)

        return UndecryptableVaultError(
            obj=self._marker_undecryptable_vaulted_value,
            error_detail=ErrorDetail(
                errors=[ErrorMessage(msg=uvv_tag.reason)],
                formatted_traceback=uvv_tag.traceback,
            ),
        )

    def _disarm(self) -> str:
        return self._marker_undecryptable_vaulted_value
