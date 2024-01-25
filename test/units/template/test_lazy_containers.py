# FIXME: more thorough tests are needed here, this is just a starting point

from __future__ import annotations

import typing as t

import pytest

from ansible.module_utils.datatag import TrustedAsTemplate
from ansible.template.utils import TemplateContext
from ansible.template.templar import Templar
from ansible.template.lazy_containers import _AnsibleLazyTemplateMixin

VALUE_TO_TEMPLATE = TrustedAsTemplate().tag("{{ 'hello' | default('goodbye') }}")

CONTAINER_VALUES = (
    dict(hello=VALUE_TO_TEMPLATE),
    [VALUE_TO_TEMPLATE],
    (VALUE_TO_TEMPLATE,),
)


@pytest.mark.parametrize("value", CONTAINER_VALUES, ids=[type(value).__name__ for value in CONTAINER_VALUES])
def test_container_equality(value: t.Any) -> None:
    templar = Templar(loader=None, variables=dict())

    rendered = templar.template(value)

    with TemplateContext(template_value=None, templar=templar):
        # NOTE: Assertion failure helper text may be misleading, since repr() will show rendered templates, which will appear to match expected values.

        lazy = _AnsibleLazyTemplateMixin.try_create(value)

        assert lazy == lazy  # pylint: disable=comparison-with-itself

        assert lazy == rendered
        assert rendered == lazy

        assert lazy != value
        assert value != lazy


@pytest.mark.parametrize("value", CONTAINER_VALUES, ids=[type(value).__name__ for value in CONTAINER_VALUES])
def test_container_format(value: t.Any) -> None:
    templar = Templar(loader=None, variables=dict())

    rendered = templar.template(value)

    with TemplateContext(template_value=None, templar=templar):
        # NOTE: Assertion failure helper text may be misleading, since repr() will show rendered templates, which will appear to match expected values.

        lazy = _AnsibleLazyTemplateMixin.try_create(value)

        assert "{0}".format(lazy) == "{0}".format(rendered)


@pytest.mark.parametrize("container_type", (
    list,
    tuple,
))
def test_container_contains(container_type: type) -> None:
    templar = Templar(loader=None, variables=dict())

    # including default('goodbye') as canary for flattening to a string
    value = container_type([VALUE_TO_TEMPLATE])
    rendered = templar.template(value)

    with TemplateContext(template_value=None, templar=templar):
        # NOTE: Assertion failure helper text may be misleading, since repr() will show rendered templates, which will appear to match expected values.

        lazy = _AnsibleLazyTemplateMixin.try_create(value)

        for src in (lazy, rendered):
            assert 'hello' in src
            assert 'goodbye' not in src


@pytest.mark.parametrize("container_type", (
    list,
    tuple,
))
def test_container_comparison(container_type: type) -> None:
    templar = Templar(loader=None, variables=dict())

    # including default('goodbye') as canary for flattening to a string
    value = container_type([VALUE_TO_TEMPLATE])
    rendered = templar.template(value)

    with TemplateContext(template_value=None, templar=templar):
        # NOTE: Assertion failure helper text may be misleading, since repr() will show rendered templates, which will appear to match expected values.

        lazy = _AnsibleLazyTemplateMixin.try_create(value)

        assert value > rendered
        assert not (lazy > rendered)

        assert value >= rendered
        assert lazy >= rendered

        assert rendered < value
        assert not (rendered < lazy)

        assert rendered <= value
        assert rendered <= lazy


def test_list_sort() -> None:
    templar = Templar(loader=None, variables=dict())

    with TemplateContext(template_value=None, templar=templar):
        lazy: list = _AnsibleLazyTemplateMixin.try_create([])

        with pytest.raises(NotImplementedError):
            lazy.sort()


def test_list_index() -> None:
    templar = Templar(loader=None, variables=dict())

    rendered = templar.template(VALUE_TO_TEMPLATE)

    with TemplateContext(template_value=None, templar=templar):
        lazy: list = _AnsibleLazyTemplateMixin.try_create([VALUE_TO_TEMPLATE])

        assert lazy.index(rendered) == 0


def test_list_remove() -> None:
    templar = Templar(loader=None, variables=dict())

    rendered = templar.template(VALUE_TO_TEMPLATE)

    with TemplateContext(template_value=None, templar=templar):
        lazy: list = _AnsibleLazyTemplateMixin.try_create([VALUE_TO_TEMPLATE])

        assert rendered in lazy

        lazy.remove(rendered)

        assert lazy == []


def test_dict_items_and_values() -> None:
    templar = Templar(loader=None, variables=dict())

    value = dict(key=VALUE_TO_TEMPLATE)
    rendered = templar.template(value)

    with TemplateContext(template_value=None, templar=templar):
        lazy: dict = _AnsibleLazyTemplateMixin.try_create(value)

        assert list(lazy.items()) == list(rendered.items())
        assert list(lazy.values()) == list(rendered.values())
