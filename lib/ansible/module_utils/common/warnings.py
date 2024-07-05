# -*- coding: utf-8 -*-
# Copyright (c) 2019 Ansible Project
# Simplified BSD License (see licenses/simplified_bsd.txt or https://opensource.org/licenses/BSD-2-Clause)

from __future__ import annotations

import datetime
import typing as t

from .._internal import _traceback
from ..common.messages import WarningMessageDetail, DeprecationMessageDetail


def warn(warning: str) -> None:
    """Record a warning to be returned with the module result."""
    _global_warnings[WarningMessageDetail(
        msg=warning,
        formatted_traceback=_traceback.maybe_capture_traceback(_traceback.TracebackEvent.WARNING),
    )] = None


def deprecate(msg: str, version: str | None = None, date: str | datetime.date | None = None, collection_name: str | None = None) -> None:
    """Record a deprecation warning to be returned with the module result."""
    # DTFIX-U: this may not be necessary, AnsibleSerializable can handle it
    if isinstance(date, datetime.date):
        date = str(date)

    _global_deprecations[DeprecationMessageDetail(
        msg=msg,
        version=version,
        date=date,
        collection_name=collection_name,
        formatted_traceback=_traceback.maybe_capture_traceback(_traceback.TracebackEvent.DEPRECATED),
    )] = None


def get_warning_messages() -> tuple[str, ...]:
    """Return a tuple of warning messages accumulated over this run."""
    return tuple(item.msg for item in _global_warnings)


def get_deprecation_messages() -> tuple[dict[str, t.Any], ...]:
    """Return a tuple of deprecation warning messages accumulated over this run."""
    return tuple(item._as_dict() for item in _global_deprecations)


def get_warnings() -> list[WarningMessageDetail]:
    """Return a list of warning messages accumulated over this run."""
    return list(_global_warnings)


def get_deprecations() -> list[DeprecationMessageDetail]:
    """Return a list of deprecations accumulated over this run."""
    return list(_global_deprecations)


_global_warnings: dict[WarningMessageDetail, object] = {}
"""Global, ordered, de-deplicated storage of acculumated warnings for the current module run."""

_global_deprecations: dict[DeprecationMessageDetail, object] = {}
"""Global, ordered, de-deplicated storage of acculumated deprecations for the current module run."""
