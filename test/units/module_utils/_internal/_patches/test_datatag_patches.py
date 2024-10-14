"""
Testing for functions that need to be patched to accept tagged types.
Includes tests for related functions that might have required patching, as a means to verify patches for them are not required.
"""

from __future__ import annotations

import contextlib
import errno
import functools
import select
import socket
import sys
import typing as t

import pytest

from ansible.module_utils._internal import _patches
from ansible.module_utils.datatag.tags import Deprecated


T = t.TypeVar('T')


@t.runtime_checkable
class Closable(t.Protocol):
    """Protocol for objects with a close method."""
    def close(self): ...


@contextlib.contextmanager
def disable_patches() -> t.Iterable[None]:
    """
    Disable all patches.
    Used for tests which need to operate on unpatched functions, but don't necessarily know which patches apply.
    """
    with contextlib.ExitStack() as stack:
        _p = [stack.enter_context(patch.disable_patch()) for patch in _patches.CallablePatch._concrete_patch_types]

        yield


class HighPort:
    """Allocate a unique high port number to test cases."""

    _current_port = 10000
    _instance: HighPort | None = None

    @classmethod
    def next(cls) -> T:
        """Return the next available high port."""
        cls._current_port += 1

        return cls._current_port

    def __repr__(self) -> str:
        return '<< HIGH PORT >>'

    def __new__(cls) -> HighPort:
        """Make HighPort a singleton."""
        if not cls._instance:
            cls._instance = object.__new__(cls)

        return cls._instance


class FailsWhenTagged:
    """Wraps a value which is expected to trigger an error when used as a tagged value in a test case."""

    def __init__(self, value: t.Any) -> None:
        self.value = value


def recursively_unmark(value: T) -> T:
    """Recursively remove `Tag` wrappers from the given value and process any `HighPort` markers."""
    if value is HighPort():
        return HighPort.next()

    if isinstance(value, FailsWhenTagged):
        return recursively_unmark(value.value)

    if isinstance(value, tuple):
        return tuple(recursively_unmark(item) for item in value)

    if isinstance(value, list):
        return [recursively_unmark(v) for v in value]

    if isinstance(value, dict):
        return {k: recursively_unmark(v) for k, v in value.items()}

    return value


def render_arg(arg: t.Any) -> str:
    """Render test case arguments for use in a test case ID."""
    if isinstance(arg, functools.partial):
        return arg.func.__name__

    if callable(arg):
        return arg.__name__

    return str(arg)


def create_tag_matrix(
        test_cases: t.Iterable[tuple[t.Callable, dict[str, t.Any]]],
        only_failed: bool = False,
) -> list[tuple[t.Callable, dict[str, t.Any], str, bool]]:
    """
    Expand the given test cases by creating permutations where only one value in each case is tagged.
    If values are wrapped in a `Tag` instance they are expected to fail when tagged, otherwise they are expected to pass when tagged.
    Values which are `HighPort` markers are converted to a high port number unique to each matrix entry.
    """
    matrix: list[tuple[t.Callable, dict[str, t.Any], str, bool]] = []
    tag = Deprecated(msg='test')

    for func, args in test_cases:
        # create one test case without any tagged args
        matrix.append((func, recursively_unmark(args), 'none', False))

        # create one test case for each arg tagged
        for key, value in args.items():
            if isinstance(value, tuple):
                for idx, item in enumerate(value):
                    if isinstance(item, FailsWhenTagged):
                        item = item.value
                        expect_failure = True
                    else:
                        expect_failure = False

                    tagged_args = recursively_unmark(args)
                    tagged_args[key] = recursively_unmark(value[:idx] + (tag.tag(recursively_unmark(item)),) + value[idx + 1:])
                    matrix.append((func, tagged_args,  f'{key}[{idx}]', expect_failure))

            if key != '_args':  # included tagged arg, even if it's a tuple, unless it's '_args' (positional args)
                if isinstance(value, FailsWhenTagged):
                    value = value.value
                    expect_failure = True
                else:
                    expect_failure = False

                tagged_args = recursively_unmark(args)
                tagged_args[key] = tag.tag(recursively_unmark(recursively_unmark(value)))
                matrix.append((func, tagged_args, key, expect_failure))

    if only_failed:
        matrix = [item for item in matrix if item[3]]

    return matrix


def socket_context(func: t.Callable) -> t.Callable:
    """Generate a function that provides a socket instance and calls the given socket method."""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        with socket.socket(socket.AddressFamily.AF_INET, socket.SocketKind.SOCK_DGRAM, socket.IPPROTO_UDP) as sock:
            func(sock, *args, **kwargs)

    return wrapper


def socket_create_connection(*args, **kwargs):
    """
    Wrapper around `socket.create_connection` that ignores address availability errors.
    This allows testing of argument validation without requiring the endpoint be accessible.
    """
    try:
        return socket.create_connection(*args, **kwargs)
    except OSError as ex:
        # macOS raises EADDRNOTAVAIL
        # Linux raises ECONNREFUSED
        if ex.errno not in (errno.EADDRNOTAVAIL, errno.ECONNREFUSED):
            raise


scenarios: tuple[tuple[t.Callable, dict[str, t.Any]], ...] = (
    (socket_create_connection, dict(address=('localhost', FailsWhenTagged(0)), timeout=1, source_address=('localhost', HighPort()))),
    (socket.create_server, dict(address=('localhost', HighPort()))),
    (socket.getaddrinfo, dict(host='localhost', port=FailsWhenTagged(22))),
    (socket.getservbyport, dict(_args=(22,))),
    (socket.setdefaulttimeout, dict(_args=(10,))),
    (socket_context(socket.socket.bind), dict(_args=(('localhost', HighPort()),))),
    (socket_context(socket.socket.connect), dict(_args=(('localhost', 1),))),
    (socket_context(socket.socket.connect_ex), dict(_args=(('localhost', 1),))),
    (socket_context(socket.socket.sendto), dict(_args=(b'', ('localhost', 1),))),
    (socket_context(socket.socket.sendmsg), dict(_args=([b''], [], 0, ('localhost', 1),))),
    (socket_context(socket.socket.settimeout), dict(_args=(1,))),
    (functools.partial(select.select, (0,), tuple(), tuple()), dict(_args=(1,))),
    (sys.intern, dict(_args=(FailsWhenTagged(''),))),
)


@pytest.mark.parametrize('func,kwargs,tagged_arg,expect_failure', create_tag_matrix(scenarios), ids=render_arg)
def test_accepts_tagged_args_before_patching(func: t.Callable, kwargs: dict[str, t.Any], tagged_arg: str, expect_failure: bool) -> None:
    """
    Verify whether various standard library functions accept tagged values or not.
    Those that do not should be patched to avoid errors when passing tagged args.
    """
    with disable_patches():
        with pytest.raises(Exception) if expect_failure else contextlib.nullcontext():
            result = func(*kwargs.pop('_args', ()), **kwargs)

            if isinstance(result, Closable):
                result.close()


@pytest.mark.parametrize('func,kwargs,tagged_arg,expect_failure', create_tag_matrix(scenarios, only_failed=True), ids=render_arg)
def test_accepts_tagged_args_after_patching(func: t.Callable, kwargs: dict[str, t.Any], tagged_arg: str, expect_failure: bool) -> None:
    """Verify that various standard library functions accept tagged args after they have been patched."""
    result = func(*kwargs.pop('_args', ()), **kwargs)

    if isinstance(result, Closable):
        result.close()
