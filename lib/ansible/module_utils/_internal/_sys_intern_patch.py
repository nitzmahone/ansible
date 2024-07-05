"""Patch `sys.intern` so that it works with types derived from `str`."""

from __future__ import annotations

import sys


def patch_sys_intern() -> None:
    if not _is_patch_needed():
        return  # pragma: nocover

    sys_intern = sys.intern

    def ansible_sys_intern(value: str) -> str:
        """This is a monkey patch for `sys.intern` that converts any `str` derived type to `str` before calling the actual `sys.intern` function."""
        if type(value) is not str and isinstance(value, str):  # pylint: disable=unidiomatic-typecheck
            value = str(value)

        return sys_intern(value)

    sys.intern = ansible_sys_intern

    try:
        if _is_patch_needed():
            raise RuntimeError("patching had no effect")  # pragma: nocover
    except Exception as ex:  # pragma: nocover
        sys.intern = sys_intern
        raise RuntimeError("sys.intern string subclass support is still broken after patching") from ex


def _is_patch_needed() -> bool:
    class CustomStr(str):
        pass

    value = CustomStr('')

    try:
        sys.intern(value)
    except TypeError:
        return True

    return False
