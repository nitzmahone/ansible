from __future__ import annotations

import typing as t

import pytest

from ansible.module_utils._internal._patches import CallablePatch

def test_ineffective_patch():
    class PatchVictim:
        def patch_me(self) -> bool:
            return self is not None

    with pytest.raises(RuntimeError, match='patching .* had no effect'):
        class IneffectivePatch(CallablePatch):
            _container = PatchVictim
            _attr = 'patch_me'

            @classmethod
            def _needs_patch(cls) -> bool:
                return True

            @classmethod
            def _patched_impl(cls, *args, **kwargs) -> t.Any:
                return False

        assert IneffectivePatch  # pragma: nocover  # avoid static analysis complaints about unused class, this line will never be reached

def test_disable_patch_context():
    # DTFIX-U: this is currently borked for patching instance methods (losing 'self')
    class PatchVictim:
        def patchme1(self): ...
        def patchme2(self): ...

    class Patch1(CallablePatch):
        _container = PatchVictim
        _attr = 'patchme1'

        @classmethod
        def _needs_patch(cls) -> bool:
            return PatchVictim().patchme1() != cls.__name__

        @classmethod
        def _patched_impl(cls, *args, **kwargs) -> t.Any:
            return cls.__name__

    class Patch2(CallablePatch):
        _container = PatchVictim
        _attr = 'patchme2'

        @classmethod
        def _needs_patch(cls) -> bool:
            return PatchVictim().patchme2() != cls.__name__

        @classmethod
        def _patched_impl(cls, *args, **kwargs) -> t.Any:
            return cls.__name__

    assert PatchVictim().patchme1() == Patch1.__name__
    assert PatchVictim().patchme2() == Patch2.__name__

    with CallablePatch.disable_patches():
        assert PatchVictim().patchme1() is None
        assert PatchVictim().patchme2() is None

