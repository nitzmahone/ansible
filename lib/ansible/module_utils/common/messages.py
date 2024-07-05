from __future__ import annotations

import sys
import dataclasses
import typing as t

from ansible.module_utils.datatag import AnsibleSerializableDataclass

if sys.version_info >= (3, 10):
    # Using slots for reduced memory usage and improved performance.
    _dataclass_kwargs = dict(frozen=True, kw_only=True, slots=True)
else:
    # deprecated: description='always use dataclass slots and keyword-only args' python_version='3.9'
    _dataclass_kwargs = dict(frozen=True)


@dataclasses.dataclass(**_dataclass_kwargs)
class MessageBase(AnsibleSerializableDataclass):
    """Base class representing a warning or error message with optional source context and help text."""

    msg: str
    formatted_source_context: t.Optional[str] = None
    help_text: t.Optional[str] = None


@dataclasses.dataclass(**_dataclass_kwargs)
class ErrorMessage(MessageBase):
    """An error message. Usually derived from an exception, but can also be created in non-exception scenarios."""


@dataclasses.dataclass(**_dataclass_kwargs)
class WarningMessageDetail(MessageBase):
    """A warning message, with optional traceback."""

    formatted_traceback: t.Optional[str] = None


@dataclasses.dataclass(**_dataclass_kwargs)
class DeprecationMessageDetail(WarningMessageDetail):
    """A deprecation variant of a warning message."""

    version: t.Optional[str] = None
    date: t.Optional[str] = None
    collection_name: t.Optional[str] = None


@dataclasses.dataclass(**_dataclass_kwargs)
class ErrorDetail(AnsibleSerializableDataclass):
    """A chain of errors (possibly derived from an exception __cause__ chain) and an optional traceback."""

    errors: t.List[ErrorMessage]
    formatted_traceback: t.Optional[str] = None
