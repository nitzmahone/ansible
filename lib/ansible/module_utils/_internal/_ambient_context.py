# Copyright (c) 2024 Ansible Project
# Simplified BSD License (see licenses/simplified_bsd.txt or https://opensource.org/licenses/BSD-2-Clause)

from __future__ import annotations

import contextlib
import contextvars

# deprecated: description='typing.Self exists in Python 3.11+' python_version='3.10'
from ..compat import typing as t


class AmbientContextBase:
    """
    An abstract base context manager that, once entered, will be accessible via its `current` classmethod to any code in the same
    `contextvars` context (e.g. same thread/coroutine), until it is exited.
    """
    __slots__ = ('_contextvar_token',)

    # FIXME: this class should enforce strict nesting of contexts; overlapping context lifetimes leads to incredibly difficult to
    #        debug situations with undefined behavior, so it should fail fast.
    # FIXME: make frozen=True dataclass subclasses work (fix the mutability of the contextvar instance)

    _contextvar: t.ClassVar[contextvars.ContextVar]
    _contextvar_token: contextvars.Token

    def __init_subclass__(cls, **kwargs) -> None:
        cls._contextvar = contextvars.ContextVar(cls.__name__)

    @classmethod
    def maybe(cls, *, create: bool) -> t.Self | contextlib.nullcontext:
        """
        Return an instance of the context if `create` is `True`, otherwise return a `nullcontext` instance.
        """
        return cls() if create else contextlib.nullcontext()

    @classmethod
    def current(cls, optional: bool = False) -> t.Self | None:
        """
        Return the currently active context value for the current thread or coroutine.
        Raises ReferenceError if a context is not active, unless `optional` is `True`.
        """
        try:
            return cls._contextvar.get()
        except LookupError:
            if optional:
                return None

            raise ReferenceError(f"A required {cls.__name__} context is not active.")

    def __enter__(self) -> t.Self:
        # FIXME: actively block multiple entry
        self._contextvar_token = self.__class__._contextvar.set(self)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.__class__._contextvar.reset(self._contextvar_token)
        del self._contextvar_token
