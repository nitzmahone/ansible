from __future__ import annotations

import pytest

from ansible.utils.datatag.tags import UndecryptableVaultedValue
from ansible.template.vault import _VaultBomb, UndecryptableVaultError


def check_methods() -> list[str]:
    """
    Return a list of _VaultBomb method names that are expected to detonate on use.
    When new methods are added in future Python versions, they need to be added to _VaultBomb or the ignore list below.
    """
    str_dunder_methods = set(name for name in dir('') if name.startswith('__') and name.endswith('__'))

    # Some methods on `str` are necessary and must not call detonate, thus they are excluded from this test.
    ignore_methods = {
        '__class__',
        '__doc__',
        '__getattribute__',
        '__getnewargs__',
        '__init__',
        '__init_subclass__',
        '__new__',
        '__setattr__',
        '__subclasshook__',
    }

    # Some methods not found on `str` should also detonate.
    added_methods = {
        '__getattr__',
    }

    return sorted(str_dunder_methods - ignore_methods | added_methods)


@pytest.mark.parametrize("name", check_methods())
def test_detonate_methods(name: str) -> None:
    """Verify all expected methods on _VaultBomb detonate and that the reason is propagated to the exception from the tag."""
    reason = "because i said so"
    traceback = 'fake traceback'
    bomb = _VaultBomb.arm(UndecryptableVaultedValue(reason=reason, traceback=traceback).tag(""))
    method = getattr(bomb, name)

    with pytest.raises(UndecryptableVaultError) as err:
        method()

    assert reason in err.value.message
    assert traceback == err.value.additional_error_detail.formatted_traceback
