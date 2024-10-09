from __future__ import annotations

import typing as t

import pytest

from ansible.errors import AnsibleError
from ansible.module_utils.common._utils import get_all_subclasses
from ansible.template.jinja_common import DeferredMarker, DeferredTruncationMarker, DeferredCapturedExceptionMarker
from ansible.template.templar import Templar, TemplateOptions
from ansible.template.utils import TemplateContext
from ansible.template.vault import DeferredVaultExceptionMarker
from ansible.utils.datatag.tags import UndecryptableVaultedValue


@pytest.fixture
def template_context() -> t.Iterator[TemplateContext]:
    """A fixture that provides a TemplateContext for the duration of a test."""
    with TemplateContext(template_value=None, templar=Templar(), options=TemplateOptions.DEFAULT, stop_on_template=False) as ctx:
        yield ctx


def get_concrete_marker_types() -> list[type[DeferredMarker]]:
    """Return a sorted list of DeferredMarker and its derived types."""
    return sorted(get_all_subclasses(DeferredMarker, include_abstract=False, consider_self=True), key=lambda au: au.__name__)


@pytest.fixture(params=get_concrete_marker_types())
def deferred_marker(request, template_context: TemplateContext) -> t.Iterator[DeferredMarker]:
    """
    A multiplying parameterized fixture that will yield an instance of each DeferredMarker-derived type.
    Depends on the template_context fixture, since these types can only be created under templating.
    """
    request_type = request.param

    if issubclass(request_type, DeferredTruncationMarker):
        yield request_type()
    elif issubclass(request_type, DeferredVaultExceptionMarker):
        yield request_type(UndecryptableVaultedValue(reason='i am an undecryptable reason').tag('i am undecryptable'))
    elif issubclass(request_type, DeferredCapturedExceptionMarker):
        try:
            try:
                raise Exception('bang')
            except Exception as ex:
                defer = ex

            raise AnsibleError('big bang') from defer  # pylint: disable=used-before-assignment  # false positive
        except Exception as ex2:
            yield request_type(ex2)
    else:
        yield request_type(hint="a hint", obj="obj", name="name")
