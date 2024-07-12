from __future__ import annotations

import typing as t

from ansible.errors import AnsibleRuntimeError
from ansible.module_utils.common.messages import ErrorDetail, ErrorMessage


class AnsibleModuleCapturedError(AnsibleRuntimeError):
    """
    An error that occurred during module execution on the target host which has been re-created on the controller.
    Actions that directly set `exception` in their result dictionary will also trigger this exception.
    """

    default_prefix = 'Module failed.'

    def __init__(self, error_detail: ErrorDetail, result: dict[str, t.Any]) -> None:
        super().__init__()

        self._error_detail = error_detail
        self._result = result

    @property
    def additional_error_detail(self) -> ErrorDetail | None:
        return self._error_detail

    @classmethod
    def find_first_remoted_error(cls, exception: BaseException) -> t.Self | None:
        """Find the first captured module error in the cause chain, starting with the given exception, returning None if not found."""
        while exception:
            if isinstance(exception, cls):
                return exception

            exception = exception.__cause__

        return None

    @classmethod
    def normalize_result_exception(cls, result: dict[str, t.Any]) -> ErrorDetail | None:
        """
        Normalize the result `exception`, if any, to be an `ErrorDetail` instance.
        The `exception` key will be removed if falsey.
        An `ErrorDetail` instance will be returned if `failed` is truthy.
        """
        if not isinstance(result, dict):
            raise TypeError(f'Malformed result. Received {type(result)} instead of {dict}.')

        failed = result.get('failed')  # DTFIX-FUTURE: warn if failed is present and not a bool, or exception is present without failed being True
        exception = result.pop('exception', None)

        if not failed and not exception:
            return None

        if isinstance(exception, ErrorDetail):
            error_detail = exception
        else:
            # translate non-ErrorDetail errors
            error_detail = ErrorDetail(
                errors=[ErrorMessage(msg=str(result.get('msg', 'Unknown error.')))],
                formatted_traceback=str(exception) if exception else None,
            )

        result.update(exception=error_detail)

        return error_detail if failed else None  # even though error detail was normalized, only return it if the result indicated failure

    @classmethod
    def maybe_raise_on_result(cls, result: dict[str, t.Any]) -> None:
        """Normalize the result and raise an exception if the result indicated failure."""
        if error_detail := cls.normalize_result_exception(result):
            raise cls(error_detail, result)
