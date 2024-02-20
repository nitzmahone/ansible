from __future__ import annotations

import pytest

from jinja2 import UndefinedError

from ansible.template.templar import Templar, _DEFAULT_TEMPLATE_OPTIONS
from ansible.template.utils import TemplateContext
from ansible.template.jinja_bits import AnsibleUndefined


@pytest.fixture
def template_context():
    with TemplateContext(template_value=None, templar=Templar(), options=_DEFAULT_TEMPLATE_OPTIONS, stop_on_template=False):
        yield


def test_undefined_repr(template_context) -> None:
    with pytest.raises(UndefinedError):
        repr(AnsibleUndefined())


def test_undefined_str(template_context) -> None:
    with pytest.raises(UndefinedError):
        str(AnsibleUndefined())


def test_undefined_getattr(template_context) -> None:
    value = AnsibleUndefined()
    assert value.foo is value


def test_undefined_getattr_dunder(template_context) -> None:
    with pytest.raises(AttributeError):
        _ = AnsibleUndefined().__dunder_that_is_not_defined__


def test_undefined_getitem(template_context) -> None:
    value = AnsibleUndefined()
    assert value['foo'] is value
