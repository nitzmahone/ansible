from __future__ import annotations

import pytest

from ansible.module_utils.datatag import UndecryptableVaultedValue
from ansible.template.vault import _VaultBomb, UndecryptableVaultError


def check_methods() -> list[str, ...]:
    """
    Return a list of _VaultBomb method names that are expected to detonate on use.
    When new methods are added in future Python versions, they need to be added to _VaultBomb or the ignore list below.
    """
    object_methods = set(dir(object()))

    ignore_methods = {
        '__class__',
        '__doc__',
        '__getattribute__',
        '__init__',
        '__init_subclass__',
        '__new__',
        '__setattr__',
        '__subclasshook__',
    }

    added_methods = {
        '__getattr__',
    }

    return sorted(object_methods - ignore_methods | added_methods)


@pytest.mark.parametrize("name", check_methods())
def test_detonate_methods(name):
    """Verify all expected methods on _VaultBomb detonate."""
    bomb = _VaultBomb.arm(UndecryptableVaultedValue().tag(""))
    method = getattr(bomb, name)

    with pytest.raises(UndecryptableVaultError):
        method()
