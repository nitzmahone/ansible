from __future__ import annotations

import dataclasses
import typing as t

import pytest

from typing import ClassVar

from ansible.module_utils.compat import typing as typing_surrogate
from ansible.module_utils.compat.typing import ClassVar as ClassVarFromSurrogate
from ansible.module_utils.datatag import _tag_dataclass_kwargs


@pytest.mark.parametrize("test_with_patch", (False, True))
def test_classvar_fields(test_with_patch: bool, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify that typing.ClassVar works with dataclasses when patched (if needed) and fails without the patch (unless the built-in implementation works)."""
    if test_with_patch:
        expect_working_dataclasses = True  # patch has been installed (or bypassed, if underlying impl already worked)
    else:
        _is_type = getattr(dataclasses, '_is_type', None)

        if _is_type and hasattr(_is_type, '_orig_impl'):
            expect_working_dataclasses = False  # patch was installed, removing it should result in non-working dataclasses
            monkeypatch.setattr(dataclasses, '_is_type', _is_type._orig_impl)
        else:  # pragma: nocover
            expect_working_dataclasses = True  # patch was not installed, the built-in Python implementation should be working

    @dataclasses.dataclass(**_tag_dataclass_kwargs)
    class ExerciseClassVar:
        real_local: ClassVar[int]
        real_from_module: t.ClassVar[int]
        surrogate_local: ClassVarFromSurrogate
        # this is the case that was actually broken; treated as an instance field when ClassVar was dot-referenced from a non-typing module
        surrogate_from_module: typing_surrogate.ClassVar[int]

        instance_field: int = 42

    fields = dataclasses.fields(ExerciseClassVar)

    if expect_working_dataclasses:
        assert len(fields) == 1
        assert ExerciseClassVar()

        # ensure that the classvars are all settable; some forms of the failure prevent this
        ExerciseClassVar.real_local = 42
        ExerciseClassVar.real_from_module = 42
        ExerciseClassVar.surrogate_local = 42
        ExerciseClassVar.surrogate_from_module = 42
    else:
        # when broken, the surrogate_from_module classvar field shows up as an instance field
        assert len(fields) == 2

    assert fields[-1].name == 'instance_field'
