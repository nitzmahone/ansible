from __future__ import annotations

import importlib
import sys

import pytest

from ansible.module_utils._internal._patches import _sys_intern_patch


@pytest.fixture(autouse=True)
def sys_intern_patch():
    """Ensure the `sys.intern` patch is installed, if needed."""
    _sys_intern_patch.patch_sys_intern()


def test_sys_intern_reload() -> None:
    """Make sure that reloading the `sys.intern` monkey-patch doesn't end up wrapping itself."""
    sys_intern = sys.intern

    sys_intern_patch = importlib.reload(_sys_intern_patch)
    sys_intern_patch.patch_sys_intern()

    assert sys_intern_patch is _sys_intern_patch
    assert sys.intern is sys_intern


def test_delete_and_import_sys_intern_patching() -> None:
    """Make sure that deleting and importing the `sys.intern` monkey-patch doesn't end up double patching `sys.intern`."""
    sys_intern = sys.intern

    del sys.modules[_sys_intern_patch.__name__]

    sys_intern_patch = importlib.import_module(_sys_intern_patch.__name__)
    sys_intern_patch.patch_sys_intern()

    assert sys_intern_patch is not _sys_intern_patch
    assert sys.intern is sys_intern


def test_sys_intern() -> None:
    """Make sure that our monkey-patched `sys.intern` returns identical string references for the same string value."""
    class CustomStr(str):
        pass

    custom_str = CustomStr('hello')
    plain_str = "hello"

    assert sys.intern(custom_str) is sys.intern(plain_str)


def test_sys_intern_patch_required() -> None:
    """
    Verify the `sys.intern` patch is actually required on the currently running Python version.
    It's possible a future Python version may no longer require the patch.
    """

    assert 'ansible' in sys.intern.__name__
