# Copyright (c) 2024 Ansible Project
# Simplified BSD License (see licenses/simplified_bsd.txt or https://opensource.org/licenses/BSD-2-Clause)

"""Internal error handling logic for targets. Not for use on the controller."""

from __future__ import annotations

from . import _traceback
from ..common.messages import ErrorMessage, ErrorDetail


def create_error_detail(exception: BaseException) -> ErrorDetail:
    """Return an `ErrorDetail` created from the given exception."""
    return ErrorDetail(
        errors=_create_error_chain(exception),
        formatted_traceback=_traceback.maybe_extract_traceback(exception, _traceback.TracebackEvent.ERROR),
    )


def _create_error_chain(exception: BaseException) -> list[ErrorMessage]:
    """Return an `ErrorMessage` list created from the given exception."""
    target_exception: BaseException | None = exception
    error_chain: list[ErrorMessage] = []

    while target_exception:
        error_chain.append(ErrorMessage(msg=str(target_exception).strip()))

        target_exception = target_exception.__cause__

    return error_chain
