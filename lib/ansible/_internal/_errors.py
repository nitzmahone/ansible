from __future__ import annotations

import dataclasses
import typing as t

from ansible.errors import AnsibleRuntimeError
from ansible.module_utils.common.messages import ErrorDetail, ErrorMessage, _dataclass_kwargs


class AnsibleCapturedError(AnsibleRuntimeError):
    """An exception representing error detail captured in a foreign context (e.g., worker process, remote module target)."""

    context: t.ClassVar[str]

    def __init__(self, error_detail: ErrorDetail, result: dict[str, t.Any]) -> None:
        super().__init__()

        self._error_detail = error_detail
        self._result = result

    @property
    def additional_error_detail(self) -> ErrorDetail:
        return self._error_detail

    @classmethod
    def maybe_raise_on_result(cls, result: dict[str, t.Any]) -> None:
        """Normalize the result and raise an exception if the result indicated failure."""
        if error_detail := cls.normalize_result_exception(result):
            raise error_detail.error_type(error_detail, result)

    @classmethod
    def find_first_remoted_error(cls, exception: BaseException) -> t.Self | None:
        """Find the first captured module error in the cause chain, starting with the given exception, returning None if not found."""
        while exception:
            if isinstance(exception, cls):
                return exception

            exception = exception.__cause__

        return None

    @classmethod
    def normalize_result_exception(cls, result: dict[str, t.Any]) -> CapturedErrorDetail | None:
        """
        Normalize the result `exception`, if any, to be a `CapturedErrorDetail` instance.
        If a new `CapturedErrorDetail` was created, the `error_type` will be `cls`.
        The `exception` key will be removed if falsey.
        An `CapturedErrorDetail` instance will be returned if `failed` is truthy.
        """
        if type(cls) is AnsibleCapturedError:  # pylint: disable=unidiomatic-typecheck
            raise TypeError('The normalize_result_exception method cannot be called on the AnsibleCapturedError base type, use a derived type.')

        if not isinstance(result, dict):
            raise TypeError(f'Malformed result. Received {type(result)} instead of {dict}.')

        failed = result.get('failed')  # DTFIX-FUTURE: warn if failed is present and not a bool, or exception is present without failed being True
        exception = result.pop('exception', None)

        if not failed and not exception:
            return None

        if isinstance(exception, CapturedErrorDetail):
            error_detail = exception
        elif isinstance(exception, ErrorDetail):
            error_detail = CapturedErrorDetail(
                errors=exception.errors,
                formatted_traceback=cls._normalize_traceback(exception.formatted_traceback),
                error_type=cls,
            )
        else:
            # translate non-ErrorDetail errors
            error_detail = CapturedErrorDetail(
                errors=[ErrorMessage(msg=str(result.get('msg', 'Unknown error.')))],
                formatted_traceback=cls._normalize_traceback(exception),
                error_type=cls,
            )

        result.update(exception=error_detail)

        return error_detail if failed else None  # even though error detail was normalized, only return it if the result indicated failure

    @classmethod
    def _normalize_traceback(cls, value: object | None) -> str | None:
        """Normalize the provided traceback value, returning None if it is falsey."""
        if not value:
            return None

        value = str(value).rstrip()

        if not value:
            return None

        return value + '\n'


class AnsibleActionCapturedError(AnsibleCapturedError):
    """An exception representing error detail sourced directly by an action in its result dictionary."""

    default_prefix = 'Action failed.'
    context = 'action'


class AnsibleModuleCapturedError(AnsibleCapturedError):
    """An exception representing error detail captured in a module context and returned from an action's result dictionary."""

    default_prefix = 'Module failed.'
    context = 'target'


@dataclasses.dataclass(**_dataclass_kwargs)
class CapturedErrorDetail(ErrorDetail):
    # DTFIX-MERGE: where to put this, name, etc. since it shows up in results, it's not exactly private (and contains a type ref to an internal type)
    error_type: type[AnsibleCapturedError] | None = None
