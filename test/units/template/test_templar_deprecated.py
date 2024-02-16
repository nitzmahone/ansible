from __future__ import annotations

import typing as t

from ansible.errors import AnsibleTemplatePluginNotFoundError, AnsibleTemplateSyntaxError, AnsibleBrokenConditionalError
from ansible.module_utils.datatag import TrustedAsTemplate
from ansible.template.jinja_bits import is_possibly_all_template, _TEMPLATE_OVERRIDE_DEFAULT
from ansible.template.templar import Templar
from ansible.utils.display import Display

import pytest

TRUST = TrustedAsTemplate()
display = Display()


def as_template(value: str) -> str:
    return f"{{{{ {value} }}}}"


TEMPLATED_LOOKUP_NAME_TEST_VALUES = [
    ("""lookup('{{ "pipe" }}', 'echo hi')""", "hi"),
    ("""query('{{ "pipe" }}', 'echo hi')""", ["hi"]),
]


@pytest.mark.parametrize("value", [v[0] for v in TEMPLATED_LOOKUP_NAME_TEST_VALUES])
def test_lookup_query_name_is_not_templated_non_conditional(value: str) -> None:
    with pytest.raises(AnsibleTemplatePluginNotFoundError):
        Templar().template(TRUST.tag(as_template(value)))


@pytest.mark.parametrize("value", [v[0] for v in TEMPLATED_LOOKUP_NAME_TEST_VALUES])
def test_lookup_query_name_is_not_templated_conditional_nested_template(value: str) -> None:
    with pytest.raises(AnsibleTemplatePluginNotFoundError):
        Templar().evaluate_conditional(TRUST.tag(as_template(value)))


@pytest.mark.parametrize("value, expected_result", TEMPLATED_LOOKUP_NAME_TEST_VALUES)
def test_lookup_query_name_is_not_templated_conditional_expression(value: str, expected_result: t.Any, mocker) -> None:
    deprecated_spy = mocker.spy(display, 'deprecated')

    assert Templar().evaluate_conditional(TRUST.tag(f'{value} == {expected_result!r}'))
    assert deprecated_spy.call_count == 1
    assert "should not contain embedded templates" in deprecated_spy.call_args_list[0].kwargs['msg']


@pytest.mark.parametrize("value, expected_result", [
    ("""{{ lookup('pipe', '{{ "echo hi" }}') }}""", "hi"),
    ("""{{ query('pipe', '{{ "echo hi" }}') }}""", ["hi"]),
])
def test_lookup_query_args_are_templated(value: str, expected_result: t.Any, mocker) -> None:
    deprecated_spy = mocker.spy(display, 'deprecated')

    assert Templar().template(TRUST.tag(value)) == expected_result
    assert deprecated_spy.call_count == 1
    assert "should not contain embedded templates" in deprecated_spy.call_args_list[0].kwargs['msg']


@pytest.mark.parametrize("value", [
    "foo(",
    "'a' == {{ 'b' }}",
])
def test_conditional_syntax_error(value: str) -> None:
    with pytest.raises(AnsibleTemplateSyntaxError):
        Templar().evaluate_conditional(TRUST.tag(value))


BROKEN_CONDITIONAL_VALUES = [
    (None, True),  # stupid backward-compat
    ("", True),  # stupid backward-compat
    ("''", False),
    ("0", False),
    ("0.0", False),
    ("1", True),
    ("1.1", True),
    ("'abc'", True),
    ("{{ 'dude' }}", True),
    ("{{ '' }}", False),
    ("{{ None }}", False),
    ("{{ 0 }}", False),
    ("{{ 0.0 }}", False),
    ("{{ [] }}", False),
    ("{{ {} }}", False),
    ([], False),
    ([TRUST.tag("{{ omit }}")], False),
    ({}, False),
    (dict(a=TRUST.tag("{{ omit }}")), False),
    (["abc", TRUST.tag("{{ omit }}")], True),
    (dict(a="b", omitted=TRUST.tag("{{ omit }}")), True),
    (0, False),
    (0.0, False),
    (1, True),
    (1.1, True),
]


@pytest.mark.parametrize("value", [v[0] for v in BROKEN_CONDITIONAL_VALUES], ids=repr)
def test_broken_conditionals_as_error(value: t.Any) -> None:
    with pytest.raises(AnsibleBrokenConditionalError):
        Templar().evaluate_conditional(TRUST.tag(value))


@pytest.mark.parametrize("value, expected_result", BROKEN_CONDITIONAL_VALUES, ids=repr)
def test_broken_conditionals_as_warning(value: t.Any, expected_result: bool, mocker) -> None:
    deprecated_spy = mocker.spy(display, 'deprecated')
    templar = Templar()
    templar._allow_broken_conditionals = True

    assert templar.evaluate_conditional(TRUST.tag(value)) == expected_result

    if isinstance(value, str) and is_possibly_all_template(value, _TEMPLATE_OVERRIDE_DEFAULT):
        assert deprecated_spy.call_count == 2
        assert "should not be surrounded" in deprecated_spy.call_args_list[0].kwargs['msg']
    else:
        assert deprecated_spy.call_count == 1

    if value in (None, ''):
        assert "Empty conditional" in deprecated_spy.call_args_list[-1].kwargs['msg']
    else:
        assert "must have a boolean result" in deprecated_spy.call_args_list[-1].kwargs['msg']
