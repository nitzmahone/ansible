from __future__ import annotations

import pytest

from ansible.module_utils.datatag import TrustedAsTemplate
from ansible.template.templar import Templar
from jinja2.loaders import DictLoader


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
