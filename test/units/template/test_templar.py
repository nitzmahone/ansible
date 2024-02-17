# (c) 2012-2014, Michael DeHaan <michael.dehaan@gmail.com>
#
# This file is part of Ansible
#
# Ansible is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Ansible is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Ansible.  If not, see <http://www.gnu.org/licenses/>.

from __future__ import annotations

import mock
import typing as t

from jinja2.runtime import Context

import unittest

from ansible import constants as C
from ansible.errors import AnsibleError, AnsibleUndefinedVariable, AnsibleTemplateSyntaxError, AnsibleTemplatePluginNotFoundError
from ansible.module_utils.datatag import AnsibleSourcePosition, AnsibleTaggedObject, TrustedAsTemplate, NotATemplate
from ansible.plugins.loader import init_plugin_loader
from ansible.template.templar import Templar, TemplateOptions, TemplateTrustCheckFailedError, TemplateMode
from ansible.template.jinja_bits import AnsibleEnvironment, AnsibleContext, _TEMPLATE_OVERRIDE_DEFAULT, is_possibly_template
from ansible.template.undefined_behaviors import BEST_EFFORT
from ansible.utils.display import Display
from units.mock.loader import DictDataLoader

import pytest

NOT_A_TEMPLATE = NotATemplate()
TRUST = TrustedAsTemplate()


class BaseTemplar(object):
    def setUp(self):
        init_plugin_loader()
        self.test_vars = dict(
            foo="bar",
            bam=TrustedAsTemplate().tag("{{foo}}"),
            num=1,
            var_true=True,
            var_false=False,
            var_dict=dict(a="b"),
            bad_dict="{a='b'",
            var_list=[1],
            recursive=TrustedAsTemplate().tag("{{recursive}}"),
            some_var="blip",
            some_keyword=TrustedAsTemplate().tag("{{ foo }}"),
            some_unsafe_var="unsafe_blip",
            some_unsafe_keyword=TrustedAsTemplate().tag("{{ foo }}"),
            str_with_error=TrustedAsTemplate().tag("{{ 'str' | from_json }}"),
            template_dict={TrustedAsTemplate().tag("{{ a_keyword }}"): TrustedAsTemplate().tag("{{ some_var }}")},
            template_var=TrustedAsTemplate().tag('{{ some_var }}'),
        )
        self.fake_loader = DictDataLoader({
            "/path/to/my_file.txt": "foo\n",
        })
        self.templar = Templar(loader=self.fake_loader, variables=self.test_vars)
        self._ansible_context = AnsibleContext(self.templar.environment, {}, {}, {})


class TestTemplarTemplate(BaseTemplar, unittest.TestCase):
    def test_trust_fail_raises_in_tests(self):
        """Ensure template trust check failures default to fatal for unit tests (set in units/conftest.py)"""
        from ansible.template.templar import TemplateTrustCheckFailedError

        assert Templar._raise_on_trust_check_fail is True

        with pytest.raises(TemplateTrustCheckFailedError):
            self.templar.template("{{ i_am_not_trusted }}")

    def test_trust_fail_warning_behavior(self):
        """Validate that trust checks are non-fatal when Templar's _raise_on_trust_check_fail is False"""
        untrusted_template = "{{ i_am_not_trusted }}"

        with (mock.patch.object(Templar, '_raise_on_trust_check_fail', False),
              mock.patch.object(Display, 'warning', return_value=None) as mock_warning):
            assert self.templar.template(untrusted_template) is untrusted_template

        assert mock_warning.call_count > 0
        all_args = repr(mock_warning.call_args)
        assert "skipped untrusted template" in all_args
        assert untrusted_template in all_args

    def test_is_possible_template(self):
        """This test ensures that a broken template still gets templated"""
        # Purposefully invalid jinja
        self.assertRaises(AnsibleError, self.templar.template, TrustedAsTemplate().tag('{{ foo|default(False)) }}'))

    def test_is_template_raw_string(self):
        res = self.templar.is_template('foo')
        self.assertFalse(res)

    def test_is_template_none(self):
        res = self.templar.is_template(None)
        self.assertFalse(res)

    def test_template(self):
        res = self.templar.template(TrustedAsTemplate().tag('{{foo}}'))
        self.assertTrue(res)
        self.assertEqual(res, 'bar')

    def test_template_in_data(self):
        res = self.templar.template(TrustedAsTemplate().tag('{{bam}}'))
        self.assertTrue(res)
        self.assertEqual(res, 'bar')

    def test_template_bare(self):
        res = self.templar.template('bam')
        self.assertTrue(res)
        self.assertEqual(res, 'bam')

    def test_template_to_json(self):
        res = self.templar.template(TrustedAsTemplate().tag('{{bam|to_json}}'))
        self.assertTrue(res)
        self.assertEqual(res, '"bar"')

    def test_template_untagged_string(self):
        unsafe_obj = "Hello"
        res = self.templar.template(unsafe_obj)
        assert not TrustedAsTemplate.is_tagged_on(res)

    def test_weird(self):
        data = TrustedAsTemplate().tag(u'''1 2 #}huh{# %}ddfg{% }}dfdfg{{  {%what%} {{#foo#}} {%{bar}%} {#%blip%#} {{asdfsd%} 3 4 {{foo}} 5 6 7''')
        self.assertRaisesRegex(AnsibleError,
                               'Syntax error in template',
                               self.templar.template,
                               data)

    def test_template_with_error(self):
        """Check that AnsibleError is raised, fail if an unhandled exception is raised"""
        self.assertRaises(AnsibleError, self.templar.template, TrustedAsTemplate().tag("{{ str_with_error }}"))


class TestTemplarMisc(BaseTemplar, unittest.TestCase):
    def test_templar_simple(self):

        templar = self.templar
        # test some basic templating
        self.assertEqual(templar.template(TrustedAsTemplate().tag("{{foo}}")), "bar")
        self.assertEqual(templar.template(TrustedAsTemplate().tag("{{foo}}\n")), "bar\n")
        self.assertEqual(templar.template(TrustedAsTemplate().tag("{{foo}}\n"), options=TemplateOptions(preserve_trailing_newlines=True)), "bar\n")
        self.assertEqual(templar.template(TrustedAsTemplate().tag("{{foo}}\n"), options=TemplateOptions(preserve_trailing_newlines=False)), "bar")
        self.assertEqual(templar.template(TrustedAsTemplate().tag("{{bam}}")), "bar")
        self.assertEqual(templar.template(TrustedAsTemplate().tag("{{num}}")), 1)
        self.assertEqual(templar.template(TrustedAsTemplate().tag("{{var_true}}")), True)
        self.assertEqual(templar.template(TrustedAsTemplate().tag("{{var_false}}")), False)
        self.assertEqual(templar.template(TrustedAsTemplate().tag("{{var_dict}}")), dict(a="b"))
        self.assertEqual(templar.template(TrustedAsTemplate().tag("{{bad_dict}}")), "{a='b'")
        self.assertEqual(templar.template(TrustedAsTemplate().tag("{{var_list}}")), [1])

        # force errors
        self.assertRaises(AnsibleUndefinedVariable, templar.template, TrustedAsTemplate().tag("{{bad_var}}"))
        self.assertRaises(AnsibleUndefinedVariable, templar.template, TrustedAsTemplate().tag("{{lookup('file', bad_var)}}"))
        self.assertRaises(AnsibleError, templar.template, TrustedAsTemplate().tag("{{lookup('bad_lookup')}}"))
        self.assertRaises(AnsibleError, templar.template, TrustedAsTemplate().tag("{{recursive}}"))
        self.assertRaises(AnsibleUndefinedVariable, templar.template, TrustedAsTemplate().tag("{{foo-bar}}"))

        # FIXME: this currently expects the best effort result to match the hint, which is a reconstructed version of the original template with additional
        #        spaces, which may not be what we want (or what we end up with after refactoring)
        self.assertEqual(templar.template(TrustedAsTemplate().tag("{{bad_var}}"), options=TemplateOptions(undefined_behavior=BEST_EFFORT)), "{{ bad_var }}")

        # test setting available_variables
        templar.available_variables = dict(foo="bam")
        self.assertEqual(templar.template(TrustedAsTemplate().tag("{{foo}}")), "bam")
        # variables must be a dict() for available_variables setter
        # FIXME Use assertRaises() as a context manager (added in 2.7) once we do not run tests on Python 2.6 anymore.
        try:
            templar.available_variables = "foo=bam"
        except AssertionError:
            pass

    def test_templar_escape_backslashes(self):
        # Rule of thumb: If escape backslashes is True you should end up with
        # the same number of backslashes as when you started.
        self.assertEqual(self.templar.template(TrustedAsTemplate().tag("\t{{foo}}"), options=TemplateOptions(escape_backslashes=True)), "\tbar")
        self.assertEqual(self.templar.template(TrustedAsTemplate().tag("\t{{foo}}"), options=TemplateOptions(escape_backslashes=False)), "\tbar")
        self.assertEqual(self.templar.template(TrustedAsTemplate().tag("\\{{foo}}"), options=TemplateOptions(escape_backslashes=True)), "\\bar")
        self.assertEqual(self.templar.template(TrustedAsTemplate().tag("\\{{foo}}"), options=TemplateOptions(escape_backslashes=False)), "\\bar")
        self.assertEqual(self.templar.template(TrustedAsTemplate().tag("\\{{foo + '\t' }}"), options=TemplateOptions(escape_backslashes=True)), "\\bar\t")
        self.assertEqual(self.templar.template(TrustedAsTemplate().tag("\\{{foo + '\t' }}"), options=TemplateOptions(escape_backslashes=False)), "\\bar\t")
        self.assertEqual(self.templar.template(TrustedAsTemplate().tag("\\{{foo + '\\t' }}"), options=TemplateOptions(escape_backslashes=True)), "\\bar\\t")
        self.assertEqual(self.templar.template(TrustedAsTemplate().tag("\\{{foo + '\\t' }}"), options=TemplateOptions(escape_backslashes=False)), "\\bar\t")
        self.assertEqual(self.templar.template(TrustedAsTemplate().tag("\\{{foo + '\\\\t' }}"), options=TemplateOptions(escape_backslashes=True)), "\\bar\\\\t")
        self.assertEqual(self.templar.template(TrustedAsTemplate().tag("\\{{foo + '\\\\t' }}"), options=TemplateOptions(escape_backslashes=False)), "\\bar\\t")

    def test_template_jinja2_extensions(self):
        fake_loader = DictDataLoader({})
        templar = Templar(loader=fake_loader)

        old_exts = C.DEFAULT_JINJA2_EXTENSIONS
        try:
            C.DEFAULT_JINJA2_EXTENSIONS = "foo,bar"
            self.assertEqual(templar._get_extensions(), ['foo', 'bar'])
        finally:
            C.DEFAULT_JINJA2_EXTENSIONS = old_exts


class TestTemplarLookup(BaseTemplar, unittest.TestCase):
    def test_lookup_missing_plugin(self):
        self.assertRaisesRegex(AnsibleTemplatePluginNotFoundError,
                               "lookup plugin 'not_a_real_lookup_plugin' not found",
                               self.templar._lookup,
                               'not_a_real_lookup_plugin',
                               'an_arg', a_keyword_arg='a_keyword_arg_value')

    def test_lookup_list(self):
        res = self.templar._lookup('list', 'an_arg', 'another_arg')
        self.assertEqual(res, 'an_arg,another_arg')

    def test_lookup_jinja_undefined(self):
        self.assertRaisesRegex(AnsibleUndefinedVariable,
                               "undefined BLAH FIXME",  # FIXME: update with correct message once we know what it should be
                               self.templar.template,
                               TrustedAsTemplate().tag('{{ lookup("list", an_undefined_jinja_var) }}'))

    def test_lookup_jinja_defined(self):
        res = self.templar._lookup('list', '{{ some_var }}')
        assert not TrustedAsTemplate.is_tagged_on(res)

    def test_lookup_jinja_dict_string_passed(self):
        self.assertRaisesRegex(AnsibleError,
                               "with_dict expects a dict",
                               self.templar._lookup,
                               'dict',
                               '{{ some_var }}')

    def test_lookup_jinja_dict_list_passed(self):
        self.assertRaisesRegex(AnsibleError,
                               "with_dict expects a dict",
                               self.templar._lookup,
                               'dict',
                               ['foo', 'bar'])

    def test_lookup_jinja_kwargs(self):
        res = self.templar._lookup('list', 'blip', random_keyword='12345')
        assert not TrustedAsTemplate.is_tagged_on(res)

    def test_lookup_jinja_list_wantlist(self):
        res = self.templar.template(TrustedAsTemplate().tag("{{ lookup('list', template_var, wantlist=True) }}"))
        self.assertEqual(res, ["blip"])

    def test_lookup_jinja_list_wantlist_undefined(self):
        self.assertRaisesRegex(AnsibleUndefinedVariable,
                               "undefined BLAH FIXME",  # FIXME: update with correct message once we know what it should be
                               self.templar.template,
                               TrustedAsTemplate().tag('{{ lookup("list", some_undefined_var, wantlist=True) }}'))

    def test_lookup_jinja_list_wantlist_unsafe(self):
        res = self.templar._lookup('list', '{{ some_unsafe_var }}', wantlist=True)
        for lookup_result in res:
            assert not TrustedAsTemplate.is_tagged_on(lookup_result)

        assert not TrustedAsTemplate.is_tagged_on(res)

    def test_lookup_jinja_dict(self):
        res = self.templar.template(TrustedAsTemplate().tag('{{ lookup("list", template_dict) }}'))
        self.assertEqual(res['{{ a_keyword }}'], "blip")
        assert not TrustedAsTemplate.is_tagged_on(res)

    def test_lookup_jinja_dict_unsafe(self):
        res = self.templar._lookup('list', {'{{ some_unsafe_key }}': '{{ some_unsafe_var }}'})
        assert not TrustedAsTemplate.is_tagged_on(res['{{ some_unsafe_key }}'])
        assert not TrustedAsTemplate.is_tagged_on(res)

    def test_lookup_jinja_dict_unsafe_value(self):
        res = self.templar._lookup('list', {'{{ a_keyword }}': '{{ some_unsafe_var }}'})
        assert not TrustedAsTemplate.is_tagged_on(res['{{ a_keyword }}'])
        assert not TrustedAsTemplate.is_tagged_on(res)

    def test_lookup_jinja_none(self):
        res = self.templar._lookup('list', None)
        self.assertIsNone(res)


class TestAnsibleContext(BaseTemplar, unittest.TestCase):
    def _context(self, variables=None):
        variables = variables or {}

        env = AnsibleEnvironment()
        context = AnsibleContext(env, parent={}, name='some_context',
                                 blocks={})

        for key, value in variables.items():
            context.vars[key] = value

        return context

    def test(self):
        context = self._context()
        self.assertIsInstance(context, AnsibleContext)
        self.assertIsInstance(context, Context)

    def test_resolve_unsafe(self):
        context = self._context(variables={'some_unsafe_key': 'some_unsafe_string'})
        res = context.resolve('some_unsafe_key')
        assert not TrustedAsTemplate.is_tagged_on(res)

    def test_resolve_unsafe_list(self):
        context = self._context(variables={'some_unsafe_key': ['some unsafe string 1']})
        res = context.resolve('some_unsafe_key')
        assert not TrustedAsTemplate.is_tagged_on(res[0])
        assert not TrustedAsTemplate.is_tagged_on(res)

    def test_resolve_unsafe_dict(self):
        context = self._context(variables={'some_unsafe_key':
                                           {'an_unsafe_dict': 'some unsafe string 1'}
                                           })
        res = context.resolve('some_unsafe_key')
        assert not TrustedAsTemplate.is_tagged_on(res['an_unsafe_dict'])

    def test_resolve(self):
        context = self._context(variables={'some_key': 'some_string'})
        res = context.resolve('some_key')
        self.assertEqual(res, 'some_string')

    def test_resolve_none(self):
        context = self._context(variables={'some_key': None})
        res = context.resolve('some_key')
        self.assertEqual(res, None)


def test_unsafe_lookup():
    res = Templar(
        None,
        variables={
            'var0': TrustedAsTemplate().tag('{{ var1 }}'),
            'var1': ['unsafe'],
        }
    ).template(TrustedAsTemplate().tag('{{ lookup("list", var0) }}'))
    assert not TrustedAsTemplate.is_tagged_on(res[0])


def test_unsafe_lookup_no_conversion():
    res = Templar(
        None,
        variables={
            'var0': TrustedAsTemplate().tag('{{ var1 }}'),
            'var1': ['unsafe'],
        }
    ).template(
        TrustedAsTemplate().tag('{{ lookup("list", var0) }}'),
    )
    assert not TrustedAsTemplate.is_tagged_on(res)


@pytest.mark.parametrize("tagged", (
    False,
    True,
))
def test_dict_template(tagged: bool) -> None:
    """Verify that templar.template can round-trip both tagged and untagged values in a dict."""
    key1 = "key1"
    val1 = "val1"

    if tagged:
        key1 = AnsibleSourcePosition(src="key1.py", line=1, col=2).tag(key1)
        val1 = AnsibleSourcePosition(src="val1.py", line=3, col=4).tag(val1)

    test1 = {
        key1: val1,
    }

    variables = dict(
        test1=test1,
    )

    templar = Templar(loader=None, variables=variables)

    result = templar.template(TrustedAsTemplate().tag('{{test1}}'))

    assert result == test1
    assert AnsibleTaggedObject.tags(result) == AnsibleTaggedObject.tags(test1)


@pytest.mark.parametrize("expr,expected,variables", [
    ("'constant'", "constant", None),
    (NOT_A_TEMPLATE.tag("non-template expression"), "non-template expression", None),
    # FIXME: add more test cases
])
def test_evaluate_expression(expr: str, expected: t.Any, variables: dict[str, t.Any] | None):
    assert Templar().evaluate_expression(TRUST.tag(expr)) == expected


@pytest.mark.parametrize("expr,error_type", [
    ("fhdgsfk#$76&@#$&", AnsibleTemplateSyntaxError),
    ("bogusvar", AnsibleUndefinedVariable),
    ("untrusted expression", TemplateTrustCheckFailedError),
    (dict(hi="{{'mom'}}"), TypeError),
])
def test_evaluate_expression_errors(expr: str, error_type: type[Exception]):
    if error_type is not TemplateTrustCheckFailedError:
        expr = TRUST.tag(expr)

    with pytest.raises(error_type):
        Templar().evaluate_expression(expr)


@pytest.mark.parametrize("conditional,expected,variables", [
    ("1 == 2", False, None),
    ("test2_name | default(True)", True, None),
    # FIXME: more success cases?
])
def test_evaluate_conditional(conditional: str, expected: t.Any, variables: dict[str, t.Any] | None):
    assert Templar().evaluate_conditional(TRUST.tag(conditional)) == expected


@pytest.mark.parametrize("conditional,error_type", [
    ("fkjhs$#@^%$*& ldfkjds", AnsibleTemplateSyntaxError),
    ("#jinja2:variable_start_string:2\n{{blah}}", AnsibleTemplateSyntaxError),
    ("#jinja2:bogus_key:'val'\n{{blah}}", AnsibleTemplateSyntaxError),
    ("bogusvar", AnsibleUndefinedVariable),
    ("not trusted", TemplateTrustCheckFailedError),
])
def test_evaluate_conditional_errors(conditional: t.Any, error_type: type[Exception]):
    if error_type is not TemplateTrustCheckFailedError:
        conditional = TRUST.tag(conditional)

    with pytest.raises(error_type):
        Templar().evaluate_conditional(conditional)


@pytest.mark.parametrize("value", (
    '{{ foo }}',
    '{% foo %}',
    '{# foo #}',
    '{# {{ foo }} #}',
    '{# {{ nothing }} {# #}',
    '{# {{ nothing }} {# #} #}',
    '{% raw %}{{ foo }}{% endraw %}',
    # in 2.16 and earlier these were not considered templates due to syntax errors
    # now syntax errors in templates are still reported as templates, since is_template no longer compiles the template
    '{{ foo',
    '{% foo',
    '{# foo',
    '{{ foo %}',
    '{{ foo #}',
    '{% foo }}',
    '{% foo #}',
    '{# foo %}',
    '{# foo }}',
    '{{ foo {{',
    '{% raw %}{% foo %}',
))
def test_is_template_true(value: str) -> None:
    assert Templar().is_template(TRUST.tag(value))


@pytest.mark.parametrize("value", (
    'foo',
))
def test_is_template_false(value: str) -> None:
    assert not Templar().is_template(TRUST.tag(value))


@pytest.mark.parametrize("value", (
    '{{ foo }}',
    '{% foo %}',
    '{# foo #}',
    '{# {{ foo }} #}',
    '{# {{ nothing }} {# #}',
    '{# {{ nothing }} {# #} #}',
    '{% raw %}{{ foo }}{% endraw %}',
    '{{',
    '{%',
    '{#',
    '{% raw',
))
def test_is_possibly_template_true(value: str) -> None:
    assert is_possibly_template(value, _TEMPLATE_OVERRIDE_DEFAULT)


@pytest.mark.parametrize("value", (
    '{',
    '%',
    '#',
    'foo',
    '}}',
    '%}',
    'raw %}',
    '#}',
))
def test_is_possibly_template_false(value: str) -> None:
    assert not is_possibly_template(value, _TEMPLATE_OVERRIDE_DEFAULT)


def test_stop_on_container() -> None:
    # FIXME: add more test cases
    assert Templar().template(TRUST.tag('{{ [ 1 ] }}'), mode=TemplateMode.STOP_ON_CONTAINER) == [1]


@pytest.mark.parametrize("value", [True, False])
def test_stripped_conditionals(value: bool) -> None:
    assert Templar().evaluate_conditional(TRUST.tag(f"""\n \r\n \t{{{{ {value} }}}} \n\n  \t \t\t  """)) == value


@pytest.mark.parametrize("template, variables", (
    ("{{ undefined_var.undefined_attribute }}", {}),
    ("{{ some_dict.undefined_key }}", dict(some_dict={})),
))
def test_jinja_sourced_undefined(template: str, variables: dict[str, t.Any]) -> None:
    """
    Ensure when Jinja encounters AnsibleUndefined and raises UndefinedError,
    that we turn it back into AnsibleUndefined so undefined_behavior can handle it during finalization.
    """
    assert Templar(variables=variables).template(TRUST.tag(template), options=TemplateOptions(undefined_behavior=BEST_EFFORT)) == template
