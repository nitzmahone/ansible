from __future__ import annotations

from ansible.module_utils.datatag import TrustedAsTemplate
from ansible.template.templar import Templar

trust = TrustedAsTemplate()


# FIXME: this is a work-in-progress to explore access, complete it or rip it out


def test_scalar_indirect() -> None:
    # 1) resolve('access_me') -> proxy_or_render_template -> access("{{ 'hi mom' }}")
    # 2) resolve('access_me') -> resolve_or_missing -> access('hi mom')
    templar = Templar(None, dict(
        access_me=trust.tag("{{ 'hi mom' }}"),
    ))
    templar.template(trust.tag('{{ access_me }}'))


def test_scalar_indirect2() -> None:
    # 1) resolve('access_me') -> proxy_or_render_template -> access("{{ 'hi mom' + something }}")
    # 2) resolve('something') -> proxy_or_render_template -> access('hello')
    # 3) resolve('something') -> resolve_or_missing -> access('hello')
    # 4) resolve('access_me') -> resolve_or_missing -> access('hi momhello')
    templar = Templar(None, dict(
        access_me=trust.tag("{{ 'hi mom' + something }}"),
        something='hello',
    ))
    templar.template(trust.tag('{{ access_me }}'))


def test_scalar_result() -> None:
    # 1) resolve('access_me') -> proxy_or_render_template -> access('{{ indirected_var }}')
    # 2) resolve('indirected_var') -> proxy_or_render_template -> access('I am indirected')
    # 3) resolve('indirected_var') -> resolve_or_missing -> access('I am indirected')
    # 4) resolve('access_me') -> resolve_or_missing -> access('I am indirected')
    templar = Templar(None, dict(
        access_me=trust.tag("{{ indirected_var }}"),
        indirected_var="I am indirected",
    ))
    templar.template(trust.tag('{{ access_me }}'))


def test_deeply_nested_scalar_result() -> None:
    # 1) resolve('access_me') -> proxy_or_render_template -> access('{{ indirected_var }}')
    # 2) resolve('indirected_var') -> proxy_or_render_template -> access('{{ another_indirected_var }}')
    # 3) resolve('another_indirected_var') -> proxy_or_render_template -> access('I am deeply nested')
    # 4) resolve('another_indirected_var') -> resolve_or_missing -> access('I am deeply nested')
    # 5) resolve('indirected_var') -> resolve_or_missing -> access('I am deeply nested')
    # 6) resolve('access_me') -> resolve_or_missing -> access('I am deeply nested')
    templar = Templar(None, dict(
        access_me=trust.tag("{{ indirected_var }}"),
        indirected_var=trust.tag("{{ another_indirected_var }}"),
        another_indirected_var='I am deeply nested',
    ))
    templar.template(trust.tag('{{ access_me }}'))


def test_non_templated_scalar() -> None:
    # 1) resolve('access_me') -> proxy_or_render_template -> access('hi mom')
    # 2) resolve('access_me') -> resolve_or_missing -> access('hi mom')
    templar = Templar(None, dict(
        access_me='hi mom',
    ))
    templar.template(trust.tag('{{ access_me }}'))


def test_scalar_no_variables() -> None:
    # no access calls
    templar = Templar(None, {})
    templar.template(trust.tag('{{ "hi mom" }}'))
