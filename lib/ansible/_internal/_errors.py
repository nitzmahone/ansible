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
    def handle_action_exception(cls, result: dict[str, t.Any], is_action: bool) -> None:
        """Remove the `exception` key from the result, if present, raising an error if `exception` exists and is not None."""
        if not isinstance(result, dict):
            raise TypeError(f'Malformed action result. Received {type(result)} instead of {dict}.')

        if (exception := result.pop('exception', None)) is None:
            return

        if is_action:
            # deprecated: description='turn this into a deprecation warning (actions should raise, not set exception)' core_version='2.22'
            pass

        if isinstance(exception, ErrorDetail):
            error_detail = exception
        else:
            # translate non-ErrorDetail errors
            error_detail = ErrorDetail(
                errors=[ErrorMessage(msg=str(result.get('msg', 'Unknown error.')))],
                formatted_traceback=str(exception),
            )

        result.update(exception=error_detail)

        raise cls(error_detail, result)
