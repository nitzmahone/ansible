"""Test the patching infrastructure."""

from __future__ import annotations

import importlib
import sys
import typing as t

import pytest

from ansible.module_utils._internal import _patches


def get_patch_required_test_cases() -> list:
    """
    Return a list of test cases for checking if patches have been applied.
    If a patch is not needed for a given Python version it will be marked as xfail.
    """
    xfail_patch_when: dict[type[_patches.CallablePatch], bool] = {
        # Example:
        # _patches._some_patch_module.SomePatchClass: sys.version_info >= (3, 13),
    }

    patches = sorted(_patches.CallablePatch._concrete_patch_types, key=lambda item: item.__name__)
    patches = [pytest.param(patch, marks=pytest.mark.xfail) if xfail_patch_when.get(patch) else patch for patch in patches]

    return patches


@pytest.mark.parametrize("patch", get_patch_required_test_cases())
def test_patch_required(patch: _patches.CallablePatch) -> None:
    """
    Verify the patch is actually required on the currently running Python version.
    It's possible a future Python version may no longer require the patch.
    If this test fails, verify the patch is not required before ignoring the failure for the affected Python versions.
    """
    assert patch.is_patched()


@pytest.mark.parametrize("patch", sorted(_patches.CallablePatch._concrete_patch_types, key=lambda item: item.__name__))
def test_reload_patch(patch: _patches.CallablePatch) -> None:
    """Make sure that reloading the patch doesn't end up double patching."""
    original_patch = patch
    original_patch_module = sys.modules[original_patch.__module__]
    original_callable = original_patch._get_current_value()

    current_patch_module = importlib.reload(original_patch_module)

    current_patch = getattr(current_patch_module, original_patch.__name__)
    current_patch.patch()
    current_callable = current_patch._get_current_value()

    assert current_patch is not original_patch
    assert current_patch_module is original_patch_module
    assert current_callable is original_callable


@pytest.mark.parametrize("patch", sorted(_patches.CallablePatch._concrete_patch_types, key=lambda item: item.__name__))
def test_delete_and_import_patch(patch: _patches.CallablePatch) -> None:
    """Make sure that deleting and importing the patch doesn't end up double patching."""
    original_patch = patch
    original_patch_module = sys.modules[original_patch.__module__]
    original_callable = original_patch._get_current_value()

    del sys.modules[original_patch_module.__name__]

    current_patch_module = importlib.import_module(original_patch_module.__name__)

    current_patch = getattr(current_patch_module, original_patch.__name__)
    current_patch.patch()
    current_callable = current_patch._get_current_value()

    assert current_patch is not original_patch
    assert current_patch_module is not original_patch_module
    assert current_callable is original_callable


def test_ineffective_patch() -> None:
    """Verify an ineffective patch results in an error being raised."""
    class PatchVictim:
        def patch_me(self) -> bool:
            return self is not None

    class IneffectivePatch(_patches.CallablePatch):
        _container = PatchVictim
        _attr = 'patch_me'
        _patch_type = _patches.PatchType.InstanceMethod

        @classmethod
        def _needs_patch(cls) -> bool:
            return True

        @staticmethod
        def _patched_impl(*args, **kwargs) -> t.Any:
            return False

        @classmethod
        def _get_patch(cls):
            class Class:
                def patchme(self) -> bool:
                    return False

            return Class.patchme

    with pytest.raises(RuntimeError, match='patching .* had no effect'):
        IneffectivePatch.patch()


def test_disable_patch():
    """Verify patches can be disabled after being applied."""
    class PatchVictim:
        def patchme(self) -> None:
            return None

    class Patch(_patches.CallablePatch):
        _container = PatchVictim
        _attr = 'patchme'
        _patch_type = _patches.PatchType.InstanceMethod

        @classmethod
        def _needs_patch(cls) -> bool:
            return PatchVictim().patchme() is None

        @classmethod
        def _patched_impl(cls, self) -> t.Any:
            return "i am patched"

        @classmethod
        def _get_patch(cls):
            class Class:
                def patchme(self) -> str:
                    return "i am patched"

            return Class.patchme

    Patch.patch()

    assert PatchVictim().patchme() == "i am patched"

    with Patch.disable_patch():
        assert PatchVictim().patchme() is None

    assert PatchVictim().patchme() == "i am patched"


def test_function_patch() -> None:
    from . import patch_victim

    class Patch(_patches.CallablePatch):
        _container = patch_victim
        _attr = 'patchme'
        _something = 'patch attr'
        _patch_type = _patches.PatchType.Function

        @classmethod
        def _needs_patch(cls) -> bool:
            return patch_victim.patchme('needs arg') == 'unpatched <module attr> <needs arg>'

        @classmethod
        def _patched_impl(cls, value: str) -> str:
            return f'patched <{cls._something}> <{patch_victim._value}> <{value}>'

        @classmethod
        def _get_patch(cls):
            def func(value: str) -> str:
                return f'patched <{cls._something}> <{patch_victim._value}> <{value}>'

            return func

    Patch.patch()

    assert patch_victim.patchme('test arg') == f'patched <patch attr> <module attr> <test arg>'


def test_class_method_patch() -> None:
    class PatchVictim:
        _value = 'victim attr'

        @classmethod
        def patchme(cls, value: str) -> str:
            return f'unpatched <{cls._value}> <{value}>'

    class Patch(_patches.CallablePatch):
        _container = PatchVictim
        _attr = 'patchme'
        _something = 'patch attr'
        _patch_type = _patches.PatchType.ClassMethod

        @classmethod
        def _needs_patch(cls) -> bool:
            return PatchVictim.patchme('needs arg') == f"unpatched <victim attr> <needs arg>"

        @classmethod
        def _patched_impl(cls, patched_cls: PatchVictim, value: str) -> str:
            return f'patched <{cls._something}> <{patched_cls._value}> <{value}>'

        @classmethod
        def _get_patch(_cls):
            class Class:
                @classmethod
                def patchme(cls, value: str) -> str:
                    return f'patched <{_cls._something}> <{cls._value}> <{value}>'

            return Class.patchme

    Patch.patch()

    assert PatchVictim.patchme('test arg') == f'patched <patch attr> <victim attr> <test arg>'


def test_derived_class_method_patch() -> None:
    class PatchVictim:
        _value = 'victim attr'

        @classmethod
        def patchme(cls, value: str) -> str:
            return f'unpatched <{cls._value}> <{value}>'

    class DerivedPatchVictim(PatchVictim):
        _value = 'derived victim attr'

    class Patch(_patches.CallablePatch):
        _container = PatchVictim
        _attr = 'patchme'
        _something = 'patch attr'
        _patch_type = _patches.PatchType.ClassMethod

        @classmethod
        def _needs_patch(cls) -> bool:
            return PatchVictim.patchme('needs arg') == f"unpatched <victim attr> <needs arg>"

        @classmethod
        def _patched_impl(cls, patched_cls: PatchVictim, value: str) -> str:
            return f'patched <{cls._something}> <{patched_cls._value}> <{value}>'

        @classmethod
        def _get_patch(_cls):
            class Class:
                @classmethod
                def patchme(cls, value: str) -> str:
                    return f'patched <{_cls._something}> <{cls._value}> <{value}>'

            return Class.patchme

    Patch.patch()

    assert DerivedPatchVictim.patchme('test arg') == f'patched <patch attr> <derived victim attr> <test arg>'


def test_instance_method_patch() -> None:
    class PatchVictim:
        def __init__(self, value: str) -> None:
            self._value = value

        def patchme(self, value: str) -> str:
            return f'unpatched <{self._value}> <{value}>'

    class Patch(_patches.CallablePatch):
        _container = PatchVictim
        _attr = 'patchme'
        _something = 'patch attr'
        _patch_type = _patches.PatchType.InstanceMethod

        @classmethod
        def _needs_patch(cls) -> bool:
            return PatchVictim('needs init').patchme('needs arg') == 'unpatched <needs init> <needs arg>'

        @classmethod
        def _patched_impl(cls, self: PatchVictim, value: str) -> str:
            return f'patched <{cls._something}> <{self._value}> <{value}>'

        @classmethod
        def _get_patch(cls):
            class Class:
                def patchme(self: PatchVictim, value: str) -> str:
                    return f'patched <{cls._something}> <{self._value}> <{value}>'

            return Class.patchme

    Patch.patch()

    assert PatchVictim('test init').patchme('test arg') == f'patched <patch attr> <test init> <test arg>'


def test_static_method_patch() -> None:
    class PatchVictim:
        @staticmethod
        def patchme(value: str) -> str:
            return f'unpatched <{value}>'

    class Patch(_patches.CallablePatch):
        _container = PatchVictim
        _attr = 'patchme'
        _something = 'patch attr'
        _patch_type = _patches.PatchType.StaticMethod

        @classmethod
        def _needs_patch(cls) -> bool:
            return PatchVictim.patchme('needs arg') == 'unpatched <needs arg>'

        @classmethod
        def _patched_impl(cls, value: str) -> str:
            return f'patched <{cls._something}> <{value}>'

        @classmethod
        def _get_patch(cls):
            class Class:
                @staticmethod
                def patchme(value: str) -> str:
                    return f'patched <{cls._something}> <{value}>'

            return Class.patchme

    Patch.patch()

    assert PatchVictim.patchme('test arg') == f'patched <patch attr> <test arg>'


def test_getter_property_patch() -> None:
    class PatchVictim:
        def __init__(self, value: str) -> None:
            self._value = value

        @property
        def patchme(self) -> str:
            return f'unpatched <{self._value}>'

    class Patch(_patches.CallablePatch):
        _container = PatchVictim
        _attr = 'patchme'
        _something = 'patch attr'
        _patch_type = _patches.PatchType.GetterProperty

        @classmethod
        def _needs_patch(cls) -> bool:
            return PatchVictim('needs init').patchme == 'unpatched <needs init>'

        @classmethod
        def _patched_impl(cls, self: PatchVictim) -> str:
            return f'patched <{cls._something}> <{self._value}>'

        @classmethod
        def _get_patch(cls):
            class Class:
                @property
                def patchme(self) -> str:
                    return f'patched <{cls._something}> <{self._value}>'

            return Class.patchme

    Patch.patch()

    assert PatchVictim('test init').patchme == f'patched <patch attr> <test init>'


# DTFIX-U: test unpatch actually restores the original behavior -- we aren't testing that and had it wrong (doing partialmethod for example on restore)
