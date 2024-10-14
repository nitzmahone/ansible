"""Patch `sys.intern` so that it works with types derived from `str`."""

from __future__ import annotations

import contextlib
import sys

from . import CallablePatch, PatchType


class SysInternPatch(CallablePatch):
    _container = sys
    _attr = 'intern'
    _patch_type = PatchType.Function

    @classmethod
    def _needs_patch(cls) -> bool:
        class CustomStr(str): ...

        with contextlib.suppress(TypeError):
            sys.intern(CustomStr("x"))
            return False

        return True

    @classmethod
    def _patched_impl(cls, value: str, *args, **kwargs):
        if type(value) is not str and isinstance(value, str):  # pylint: disable=unidiomatic-typecheck
            value = str(value)

        return cls._unpatched(value)

    @classmethod
    def _get_patch(cls):
        def func(value: str) -> str:
            if type(value) is not str and isinstance(value, str):  # pylint: disable=unidiomatic-typecheck
                value = str(value)

            return cls._unpatched(value)

        return func
