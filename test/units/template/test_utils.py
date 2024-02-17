from __future__ import annotations

import pytest

from jinja2 import UndefinedError

from ansible.template.utils import AnsibleUndefined


def test_undefined_repr() -> None:
    with pytest.raises(UndefinedError):
        repr(AnsibleUndefined())


def test_undefined_str() -> None:
    with pytest.raises(UndefinedError):
        str(AnsibleUndefined())


def test_undefined_getitem() -> None:
    with pytest.raises(UndefinedError):
        _ = AnsibleUndefined().foo


def test_undefined_getattr() -> None:
    with pytest.raises(UndefinedError):
        _ = AnsibleUndefined()['foo']
