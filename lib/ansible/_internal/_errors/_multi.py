from __future__ import annotations

import collections.abc as c
import textwrap
import typing as t

from ansible._internal._errors._captured import CapturedErrorSummary
from ansible._internal._errors._utils import SEPARATOR
from ansible.module_utils._internal import _traceback

from ansible.module_utils.common.messages import SummaryBase, ErrorSummary, WarningSummary, Detail

_TSummary = t.TypeVar('_TSummary', bound=SummaryBase)


def aggregate(msg: str, items: list[_TSummary]) -> _TSummary:
    """
    Aggregate a list of ErrorSummary or WarningSummary into a single summary.
    Does not support DeprecationSummary.
    """
    if not items:
        raise ValueError('At least one summary item is required.')

    item_types: set[type] = set(type(item) for item in items)

    if len(item_types) > 1:
        raise ValueError(f'Cannot aggregate multiple summary types: {item_types}')

    item_type = item_types.pop()

    if item_type is CapturedErrorSummary:
        item_type = ErrorSummary

    if item_type not in (ErrorSummary, WarningSummary):
        raise TypeError(f'Cannot aggregate {item_type}.')

    details: list[Detail] = [Detail(msg=msg)]

    for item in items:
        details.append(SEPARATOR)
        details.extend(item.details)

    result = item_type(
        details=tuple(details),
        formatted_traceback=_aggregate_traceback(items),
    )

    return result


def _aggregate_traceback(items: list[_TSummary]) -> str | None:
    """Aggregate tracebacks from a list of error/warning summary items."""
    if isinstance(items[0], ErrorSummary):
        event = _traceback.TracebackEvent.ERROR
    else:
        event = _traceback.TracebackEvent.WARNING

    if not _traceback.is_traceback_enabled(event):
        return None

    formatted_tracebacks = [item.formatted_traceback or '(traceback unavailable)' for item in items]

    formatted_traceback_lines = [
        f'{_traceback.maybe_capture_traceback(event, ignore_frame_count=3)}\n',
    ]

    for idx, tb in enumerate(formatted_tracebacks):
        formatted_traceback_lines.extend([
            f'+---[ Aggregated Traceback {idx + 1} of {len(formatted_tracebacks)} ]---\n',
            textwrap.indent(f'\n{tb}\n', '| ', lambda value: True),
        ])

    formatted_traceback_lines.extend([
        '+---[ End Aggregated Traceback ]---\n',
    ])

    return ''.join(formatted_traceback_lines)


def separate(details: c.Sequence[Detail]) -> list[list[Detail]]:
    """Convert a sequence of details into groups of details, where each group is delimited in the original sequence by separator details."""
    results: list[list[Detail]] = []
    result: list[Detail] = []

    for idx, detail in enumerate(details):
        if detail == SEPARATOR:
            if idx:  # if the separator is the first item there are no results
                results.append(result)
                result = []
        else:
            result.append(detail)

    results.append(result)

    return results
