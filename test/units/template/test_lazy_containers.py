# FIXME: more thorough tests are needed here, this is just a starting point

from __future__ import annotations

import typing as t

import pytest

from ansible.errors import AnsibleUndefinedVariable, AnsibleTemplateError
from ansible.module_utils.datatag import TrustedAsTemplate
from ansible.template.utils import TemplateContext
from ansible.template.templar import Templar, TemplateOptions
from ansible.template.lazy_containers import _AnsibleLazyTemplateMixin, _AnsibleLazyListAdapter

TRUST = TrustedAsTemplate()

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

    with TemplateContext(template_value=None, templar=templar, options=TemplateOptions(), stop_on_template=False):
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

    with TemplateContext(template_value=None, templar=templar, options=TemplateOptions(), stop_on_template=False):
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

    with TemplateContext(template_value=None, templar=templar, options=TemplateOptions(), stop_on_template=False):
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

    with TemplateContext(template_value=None, templar=templar, options=TemplateOptions(), stop_on_template=False):
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

    with TemplateContext(template_value=None, templar=templar, options=TemplateOptions(), stop_on_template=False):
        lazy: list = _AnsibleLazyTemplateMixin.try_create([])

        with pytest.raises(NotImplementedError):
            lazy.sort()


def test_list_index() -> None:
    templar = Templar(loader=None, variables=dict())

    rendered = templar.template(VALUE_TO_TEMPLATE)

    with TemplateContext(template_value=None, templar=templar, options=TemplateOptions(), stop_on_template=False):
        lazy: list = _AnsibleLazyTemplateMixin.try_create([VALUE_TO_TEMPLATE])

        assert lazy.index(rendered) == 0


def test_list_remove() -> None:
    templar = Templar(loader=None, variables=dict())

    rendered = templar.template(VALUE_TO_TEMPLATE)

    with TemplateContext(template_value=None, templar=templar, options=TemplateOptions(), stop_on_template=False):
        lazy: list = _AnsibleLazyTemplateMixin.try_create([VALUE_TO_TEMPLATE])

        assert rendered in lazy

        lazy.remove(rendered)

        assert lazy == []


def test_dict_items_and_values() -> None:
    templar = Templar(loader=None, variables=dict())

    value = dict(key=VALUE_TO_TEMPLATE)
    rendered = templar.template(value)

    with TemplateContext(template_value=None, templar=templar, options=TemplateOptions(), stop_on_template=False):
        lazy: dict = _AnsibleLazyTemplateMixin.try_create(value)

        assert list(lazy.items()) == list(rendered.items())
        assert list(lazy.values()) == list(rendered.values())


def test_lazy_generator() -> None:
    value = [1, 2, 3, 4]
    raw_gen_iterator = iter(v for v in value)
    gen = _AnsibleLazyListAdapter(raw_gen_iterator)

    # ensure we didn't consume the iterator on construction by snarfing its first value
    assert next(raw_gen_iterator) == 1

    # check that the state flag matches reality
    assert not gen._source_consumed

    # ensure we only see the last three values
    assert list(gen) == value[1:]

    # check that the state flag matches reality
    assert gen._source_consumed

    # ensure the raw iterator was fully consumed
    with pytest.raises(StopIteration):
        next(raw_gen_iterator)

    assert 3 in gen
    assert gen.index(2) == 0
    assert gen.count(2) == 1


def test_generator_length_passthru(mocker) -> None:
    value = dict(a=1, b=2)
    raw_gen_iterator = value.items()
    gen = _AnsibleLazyListAdapter(raw_gen_iterator)

    assert len(gen) == len(value)

    # ensure we didn't consume anything by checking the length on a generator that supports length passthru
    assert list(raw_gen_iterator) == list(value.items())


def test_lazy_generator_laziness() -> None:
    def go_bang(arg):
        raise Exception("BANG")

    def generator_goes_bang(arg):
        if False:
            yield None

        go_bang(arg)

    templar = Templar()
    templar.environment.filters['generator_bang'] = generator_goes_bang
    templar.environment.filters['non_generator_bang'] = go_bang

    # first, ensure that template nodes are processed before undefined exceptions trip
    with pytest.raises(AnsibleTemplateError):
        templar.template(TRUST.tag("{{ bang }} {{ true | non_generator_bang }}"))

    # now that we know that the later nodes are being created, ensure that the generator is truly lazy and not consumed (undefined bombs concat first)
    with pytest.raises(AnsibleUndefinedVariable):
        templar.template(TRUST.tag("{{ bang }} {{ true | generator_bang }}"))


def test_wrapped_range():
    big_range_len = 10000000000000
    big_range = range(big_range_len)
    wrapped_big_range = _AnsibleLazyTemplateMixin.try_create(big_range)
    small_range = range(3)
    wrapped_small_range = _AnsibleLazyTemplateMixin.try_create(small_range)
    templar = Templar(variables=dict(bigrange=big_range))

    assert isinstance(templar.template(TRUST.tag("{{ bigrange | random }}")), int)
    assert isinstance(templar.template(TRUST.tag("{{ range(2) | reverse | random }}")), int)

    assert len(wrapped_big_range) == len(big_range)
    assert repr(wrapped_big_range) == repr(big_range)
    assert wrapped_big_range[0] == big_range[0]
    assert wrapped_big_range[-1] == big_range[-1] == big_range_len - 1
    assert wrapped_small_range == list(small_range)
    assert hash(wrapped_big_range) == hash(big_range)
    assert list(reversed(wrapped_small_range)) == [2, 1, 0]
    assert wrapped_small_range.count(2) == 1
    assert wrapped_small_range.index(2) == 2
    assert wrapped_small_range.start == 0
    assert wrapped_small_range.stop == 3
    assert wrapped_small_range.step == 1
    assert bool(wrapped_small_range)
    assert 1 in wrapped_small_range
    assert wrapped_small_range == small_range

    assert wrapped_big_range == big_range
    assert big_range == wrapped_big_range


def test_list_adapter_equality():
    templar = Templar(variables=dict(adict=dict(a=1, b=2)))

    assert templar.template(TRUST.tag("{{ range(1, 4) == [1, 2, 3]}}"))
    assert templar.template(TRUST.tag("{{ adict.items() == [('a', 1), ('b', 2)]}}"))
    assert templar.template(TRUST.tag("{{ [1, 1, 2, 3, 3, 2, 1] | unique == [1, 2, 3] }}"))
