from __future__ import annotations

import typing as t

from contextlib import nullcontext

import pytest
import pytest_mock

from ansible.errors import AnsibleTemplatePluginRuntimeError, AnsibleUndefinedVariable
from ansible.module_utils.datatag import AnsibleTaggedObject
from ansible.utils.datatag.tags import TrustedAsTemplate
from ansible.template.jinja_bits import AnsibleEnvironment, TemplateOverrides, _TEMPLATE_OVERRIDE_FIELD_NAMES
from ansible.template.templar import Templar, TemplateOptions
from jinja2.loaders import DictLoader

TRUST = TrustedAsTemplate()


@pytest.mark.parametrize("template,expected,variables,sources,options", [
    # no change; non-template data should be ignored
    (r'c:\newdir', r'c:\newdir', None, None, None),
    # default behavior always escapes backslashes in string constants in template expressions
    (r'{{ "c:\newdir" }}', r'c:\newdir', None, None, None),
    # escaping applies only to string literals in expressions in the current template; includes are never escaped
    (r'{{ "c:\newdir" }} {% include "foo" %}', r'c:\newdir c:\newdir', None,
     dict(foo=TRUST.tag(r'{{ "c:\\newdir" }}')), None),
    # escaping applies only to string literals in expressions in the current template; imports are never escaped
    (r'{{ "c:\newdir" }} {% import "foo" as foo %}{{ foo.m() }}', r'c:\newdir c:\newdir', None,
     dict(foo=TRUST.tag(r'{% macro m() %}{{ "c:\\newdir" }}{% endmacro %}')), None),
    # escape disable only applies to the current template; includes are still never escaped
    (r'{{ "c:\\newdir" }} {% include "foo" %}', r'c:\newdir c:\newdir', None,
     dict(foo=TRUST.tag(r'{{ "c:\\newdir" }}')), TemplateOptions(escape_backslashes=False)),
    # escape disable only applies to the current template; imports are still never escaped
    (r'{{ "c:\\newdir" }} {% import "foo" as foo %}{{ foo.m() }}', r'c:\newdir c:\newdir', None,
     dict(foo=TRUST.tag(r'{% macro m() %}{{ "c:\\newdir" }}{% endmacro %}')), TemplateOptions(escape_backslashes=False)),
    # escaping behavior should not apply to string constants in non-expression blocks (eg, `set`)
    (r'{% set foo="c:\\newdir" %}{{ foo }}', r'c:\newdir', None, None, None),
    # default behavior escapes indirect templates
    (r'{{ indirect }}', r'c:\newdir', dict(indirect=TRUST.tag(r'{{ "c:\newdir" }}')), None, None),
    # disable does *not* propagate to indirect template rendering
    (r'{{ "c:\\newdir" }} {{ indirect }}', r'c:\newdir c:\newdir',
     dict(indirect=TRUST.tag(r'{{ "c:\newdir" }}')), None, TemplateOptions(escape_backslashes=False)),
    # default escaping works on input containers
    (dict(key=TRUST.tag(r'{{ "c:\newdir" }}')), dict(key=r'c:\newdir'), None, None, None),
    # disable only applies to string templar inputs; templates in containers are always escaped
    (dict(key=TRUST.tag(r'{{ "c:\newdir" }}')), dict(key=r'c:\newdir'), None, None, TemplateOptions(escape_backslashes=False)),

])
def test_escape_backslashes(template: t.Any, expected: t.Any, variables: dict[str, t.Any], sources: dict[str, str], options: TemplateOptions | None) -> None:
    template = TRUST.tag(template)

    templar = Templar(loader=None, variables=variables)
    templar.environment.loader = DictLoader(sources or {})

    assert templar.template(template, options=options) == expected


@pytest.mark.xfail(reason="template local propagation to nested templar calls is not implemented")
def test_context_local_propagation():
    trusted = TrustedAsTemplate()
    trusted_scalar = trusted.tag("{{ hi_from }}")
    play_vars = dict(
        hi_from="play var",
        templated_scalar=trusted_scalar,
        templated_dict=dict(
            templated_scalar=trusted_scalar,
        ),
    )
    templar = Templar(loader=None, variables=play_vars)

    # shared template fragment that we'll use both directly and in an imported template
    validate_me = "[hi_from, templated_scalar, templated_dict.templated_scalar, templated_dict]"

    # Imports are one of the rare cases where Jinja calls (Ansible)Template.new_context() itself and directly consumes/concats the results; need to ensure
    # that whatever solution gets implemented can handle that case as well (since it's easier to handle the cases where we own the new_context call).
    templar.environment.loader = DictLoader(dict(importme=trusted.tag(
        "{% set hi_from='imported template local' %}"
        "{% macro validate_this() %}"
        "{{ " + validate_me + " }}"
        "{% endmacro %}"
    )))

    # The template-local variable hi_from should mask the one passed in; it currently does not whenever a nested template call is made, because template locals
    # are only available in AnsibleContext.vars, and Jinja's getattr and getitem are implemented on (Ansible)Environment, which are context-agnostic. We're
    # hopeful this could be supported by Jinja in the future, at which point we can implement template-local var propagation to nested Templars/template calls.
    # The getitem/getattr calls would need to be implemented on (Ansible)Context, or otherwise gain the ability to consult the context vars to safely
    # propagate locals. A ContextVar is insufficient to handle this problem with a new context, since the possibility of overlapping contexts from
    # abandoned-but-not-closed generators is real. PEP568 would solve this problem, if it's ever implemented...
    res = templar.template(trusted.tag(
        "{% set hi_from='template local' %}"
        "{% from 'importme' import validate_this with context %}"
        "{{ " + validate_me + " + validate_this() }}"
    ))

    assert res == [
        "template local",
        "template local",
        "template local",
        dict(templated_scalar="template local"),
        "imported template local",
        "template local",
        "template local",
        dict(templated_scalar="template local"),
    ]


@pytest.mark.parametrize("key", _TEMPLATE_OVERRIDE_FIELD_NAMES)
def test_template_overrides_defaults(key: str) -> None:
    overrides = TemplateOverrides()
    env = AnsibleEnvironment()

    assert getattr(overrides, key) == getattr(env, key)


@pytest.mark.parametrize("value, expected_overrides", [
    ("#jinja2:newline_sequence:'\\r\\n'\n", TemplateOverrides(newline_sequence='\r\n')),
    ("#jinja2:trim_blocks:False\n", TemplateOverrides(trim_blocks=False)),
    ("#jinja2:line_statement_prefix:None\n{{'template constant'}}\n{{'another'}}\n", TemplateOverrides.DEFAULT),
    ("#jinja2:line_statement_prefix:'!!'\n{{'template constant'}}\n{{'another'}}\n", TemplateOverrides(line_statement_prefix="!!")),
], ids=lambda value: repr(value.overlay_kwargs() if isinstance(value, TemplateOverrides) else value))
def test_template_override_extract_success(value: str, expected_overrides: TemplateOverrides):
    expected_template = value.split('\n', maxsplit=1)[1]
    template, overrides = TemplateOverrides.DEFAULT.extract_template_overrides(value)

    assert template == expected_template
    assert overrides == expected_overrides


@pytest.mark.parametrize("value", [
    "#jinja2:newline_sequence:'\\n\\r'\n",
    "#jinja2:bogus_key:''\n",
    "#jinja2:variable_start_string:2\n",
    "#jinja2:variable_start_string:'{{'",
    "#jinja2:variable_start_string\n",
    "#jinja2:\n",
    "#jinja2:,\n",
    "#jinja2:variable_start_string:'boo',\n",
])
def test_template_override_extract_failure(value: str):
    with pytest.raises(tuple([TypeError, ValueError])):
        TemplateOverrides.DEFAULT.extract_template_overrides(value)


def test_filter_plugin_error_wrap():
    expected_error_cause = Exception("bang")

    def raises_error(_input):
        raise expected_error_cause

    templar = Templar()
    templar.environment.filters['raises_error'] = raises_error

    with pytest.raises(AnsibleTemplatePluginRuntimeError) as err:
        templar.template(TRUST.tag("{{ true | raises_error }}"))

    assert err.value.__cause__ is expected_error_cause


def test_test_plugin_error_wrap():
    expected_error_cause = Exception("bang")

    def raises_error(_input):
        raise expected_error_cause

    templar = Templar()
    templar.environment.tests['raises_error'] = raises_error

    with pytest.raises(AnsibleTemplatePluginRuntimeError) as err:
        templar.template(TRUST.tag("{{ true is raises_error }}"))

    assert err.value.__cause__ is expected_error_cause


def test_lookup_plugin_error_wrap(mocker: pytest_mock.MockerFixture):
    expected_error_cause = Exception("bang")

    from ansible.plugins.lookup import LookupBase

    class RaisesError(LookupBase):
        def run(self, _input, *args, **kwargs):
            raise expected_error_cause

    def mock_lookup_get(name, *args, **kwargs) -> t.Any:
        return RaisesError()

    templar = Templar()
    mock_lookup_loader = mocker.MagicMock()
    mock_lookup_loader.get = mock_lookup_get

    mocker.patch('ansible.template.jinja_plugins.lookup_loader', mock_lookup_loader)

    with pytest.raises(AnsibleTemplatePluginRuntimeError) as err:
        templar.template(TRUST.tag("{{ lookup('raises_error') }}"))

    assert err.value.__cause__ is expected_error_cause


ok = "ok"
undefined_with_unsafe = AnsibleUndefinedVariable("is unsafe")


@pytest.mark.parametrize("expr, expected", (
    ('on_dict["_native_copy"]', ok),  # [] prefers getitem, matching dict key present (no attr lookup)
    ('on_dict._native_copy', ok),  # . matches `_` prefixed` method on _AnsibleTaggedDict, custom fallback to getitem with valid key
    ('on_dict["get"]', ok),  # [] prefers getitem, matching dict key present (no attr lookup)
    ('on_dict.get("_native_copy")', ok),  # . matches safe method on dict, should be callable to fetch a valid key
    ('on_dict["clear"]', ok),  # [] prefers getitem, matching dict key present (no attr lookup)
    ('on_dict.clear', ok),  # . matches known-mutating method on _AnsibleTaggedDict, custom fallback to getitem with valid key
    ('on_dict["setdefault"]', undefined_with_unsafe),  # [] finds no matching dict key, getattr fallback matches known-mutating method, fails
    ('on_dict.setdefault', undefined_with_unsafe),  # . finds a known-mutating method, getitem fallback finds no matching dict key, fails
    ('on_dict["_non_method_or_attr"]', ok),  # [] prefers getitem, sunder key ok
    ('on_dict._non_method_or_attr', ok),  # . finds nothing, getattr fallback finds dict key, `_` prefix has no effect
    ('on_list.sort', undefined_with_unsafe),  # . matches known-mutating method on list, fails
    ('on_list["sort"]', undefined_with_unsafe),  # [] gets TypeError, getattr fallback matches known-mutating method on list, fails
    ('on_list._native_copy', undefined_with_unsafe),  # . matches sunder-named method on list, fails
    ('on_list["_native_copy"]', undefined_with_unsafe),  # [] gets TypeError, getattr fallback matches sunder-named method on list, fails
    ('on_list.0', 42),  # . gets AttributeError, getitem fallback succeeds
    ('on_list[0]', 42),  # [] prefers getitem, succeeds
))
def test_jinja_getattr(expr: str, expected: object) -> None:
    """Validate expected behavior from Jinja environment getattr/getitem methods, including Ansible-customized fallback behavior."""
    assert AnsibleTaggedObject._native_copy  # validate that the underlying type has the method we're expecting to collide with

    templar = Templar(variables=dict(
        on_dict=dict(
            _native_copy=ok,  # same key as sunder method
            get=ok,  # same key as a safe method
            clear=ok,  # same key as an unsafe method
            _non_method_or_attr=ok,  # key with sunder prefix, no matching method
        ),
        on_list=[42],
    ))

    with (pytest.raises(type(expected), match=expected.message) if isinstance(expected, AnsibleUndefinedVariable) else nullcontext()):
        result = templar.evaluate_expression(TRUST.tag(expr))
        assert result == expected
