from __future__ import annotations

import abc
import inspect
import typing as t

from contextvars import ContextVar

from . import (
    AnsibleDatatagBase,
    AnsibleTagHelper,
)

POORLY_NAMED_SENTINEL = object()

_T = t.TypeVar("_T")


class _NotifiableAccessContextBase(metaclass=abc.ABCMeta):
    _tag_type_interest: t.FrozenSet[t.Type[AnsibleDatatagBase]] = frozenset()

    def __init_subclass__(cls, **kwargs) -> None:
        if not cls._tag_type_interest and not inspect.isabstract(cls):
            raise NotImplementedError(f'concrete class {cls!r} must declare _tag_type_interest')

    def __enter__(self):
        # noinspection PyProtectedMember
        AnsibleAccessContext.current()._register_interest(self)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        # noinspection PyProtectedMember
        AnsibleAccessContext.current()._unregister_interest(self)

    @abc.abstractmethod
    def _notify(self, o: t.Any) -> t.Any:
        pass


class _MutatingAccessContextBase(_NotifiableAccessContextBase, metaclass=abc.ABCMeta):
    pass


class AnsibleAccessContext:
    _contextvar: 'ContextVar[AnsibleAccessContext]' = ContextVar('AnsibleAccessContext')

    @staticmethod
    def current() -> 'AnsibleAccessContext':
        try:
            ctx: 'AnsibleAccessContext' = AnsibleAccessContext._contextvar.get()
        except LookupError:
            # didn't exist; create it
            ctx = AnsibleAccessContext()
            AnsibleAccessContext._contextvar.set(ctx)  # we ignore the token, since this should live for the life of the thread/async ctx
        return ctx

    def __init__(self) -> None:
        # NB: we really want an OrderedSet, but dict is the closest thing in stdlib
        # ordered dictionary of active contexts to notify (bottom to top)
        self._notify_contexts: t.Dict[_NotifiableAccessContextBase, None] = {}

    def _register_interest(self, context: _NotifiableAccessContextBase) -> None:
        if context in self._notify_contexts:
            raise ValueError('AnsibleAccessContext stack already contains {0}'.format(context))

        self._notify_contexts[context] = None

    def _unregister_interest(self, context: _NotifiableAccessContextBase) -> None:
        try:
            del self._notify_contexts[context]
        except KeyError:
            raise ValueError('AnsibleAccessContext stack does not contain {0}'.format(context))

    def access(self, o):
        if not self._notify_contexts:
            return o  # short circuit if nothing's listening...

        tagtypes = AnsibleTagHelper.tag_types(o)

        if not tagtypes:
            return o  # short circuit if the object has no tags

        value = POORLY_NAMED_SENTINEL
        # DTFIX-U: store exceptions from notifications, warn/raise at end?
        #  * store results from _notify calls to mutating contexts, notify all, return the innermost mutation, or warn on > 1?
        for ctx in reversed(self._notify_contexts):
            # noinspection PyProtectedMember
            if ctx._tag_type_interest.intersection(tagtypes):  # this context is interested in one or more of our tags
                # DTFIX-U: come up with a cheaper better way to only keep the innermost mutation
                # noinspection PyProtectedMember
                # DTFIX-U: FDI037 - should we be chaining "res", passing original "o", or both?
                res = ctx._notify(o)
                if res is not POORLY_NAMED_SENTINEL and isinstance(ctx, _MutatingAccessContextBase) and value is POORLY_NAMED_SENTINEL:
                    value = res
                # DTFIX-U: otherwise warn? use a different method name?

        if value is POORLY_NAMED_SENTINEL:
            value = o

        return value
