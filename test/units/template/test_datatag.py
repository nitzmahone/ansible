# FIXME: while this tests taggable types vs their native counterparts, it doesn't have testing of templating for lazy containers, particularly around repr
from __future__ import annotations

import collections.abc as c
import typing as t

import pytest

from ansible.module_utils.datatag import (
    AnsibleSerializable,
)

from ansible.template.templar import Templar, TemplateOptions
from ansible.template.utils import TemplateContext
from ansible.template.lazy_containers import _AnsibleLazyTemplateMixin

from ..module_utils.datatag.test_datatag import (
    container_values_and_types,
    container_test_ids,
    taggable_container_instances,
    assert_repr,
    assert_str,
    test_slots as assert_slots,
    test_tag as assert_tag,
    test_tag_copy as assert_tag_copy,
    test_untag as assert_untag,
)

lazy_serializable_types = tuple(t.cast(c.Collection, known_type) for known_type in AnsibleSerializable._known_type_map.values()
                                if issubclass(known_type, _AnsibleLazyTemplateMixin))


# FIXME: restructure the tests so we're not sharing values that get mutated (lazy templates for example)
#        this may also apply to the non-lazy datatag tests
def values_and_types() -> list[tuple[t.Any, t.Optional[type], type]]:
    templar = Templar(None)

    with TemplateContext(template_value=None, templar=templar, options=TemplateOptions()):
        lazy_serializable_instances = [_AnsibleLazyTemplateMixin.try_create(instance) for instance in taggable_container_instances]

    return container_values_and_types(lazy_serializable_types, lazy_serializable_instances)


def value_and_types_ids() -> list[str]:
    return container_test_ids(values_and_types())


@pytest.mark.parametrize("taggable_instance", taggable_container_instances, ids=[type(instance).__name__ for instance in taggable_container_instances])
def test_repr(taggable_instance) -> None:
    """Ensure the repr() of tagged instance is identical to the repr() returned by the underlying native Python type."""
    templar = Templar(None)

    with TemplateContext(template_value=None, templar=templar, options=TemplateOptions()):
        tagged_instance = _AnsibleLazyTemplateMixin.try_create(taggable_instance)

        assert tagged_instance
        assert_repr(tagged_instance, taggable_instance)


@pytest.mark.parametrize("taggable_instance", taggable_container_instances, ids=[type(instance).__name__ for instance in taggable_container_instances])
def test_str(taggable_instance) -> None:
    """Ensure the str() of tagged instance is identical to the str() returned by the underlying native Python type."""
    templar = Templar(None)

    with TemplateContext(template_value=None, templar=templar, options=TemplateOptions()):
        tagged_instance = _AnsibleLazyTemplateMixin.try_create(taggable_instance)

        assert tagged_instance
        assert_str(tagged_instance, taggable_instance)


@pytest.mark.parametrize("taggable_instance", taggable_container_instances, ids=[type(instance).__name__ for instance in taggable_container_instances])
def test_untag(taggable_instance):
    """Ensure tagging and then untagging a taggable instance returns new instances as appropriate, with the correct tags and type."""
    templar = Templar(None)

    with TemplateContext(template_value=None, templar=templar, options=TemplateOptions()):
        tagged_instance = _AnsibleLazyTemplateMixin.try_create(taggable_instance)

        assert tagged_instance
        assert_untag(tagged_instance)


@pytest.mark.parametrize("serializable_type", lazy_serializable_types, ids=[instance_type.__name__ for instance_type in lazy_serializable_types])
def test_slots(serializable_type: type) -> None:
    """Ensure __slots__ are properly defined on all serializable types."""
    assert_slots(serializable_type)


@pytest.mark.parametrize("value, value_type, type_under_test", values_and_types(), ids=value_and_types_ids())
def test_tag(value: t.Any, value_type: t.Optional[type], type_under_test: type) -> None:
    templar = Templar(None)

    # FIXME: Is it valid to use a different TemplateContext (and Templar) from the one used to create the value originally?
    with TemplateContext(template_value=None, templar=templar, options=TemplateOptions()):
        if isinstance(value, _AnsibleLazyTemplateMixin):
            assert value._templar
            value._templar = None  # remove the templar, forcing an error if lazy behavior is triggered during tagging

        assert_tag(value, value_type, type_under_test)

        if isinstance(value, _AnsibleLazyTemplateMixin):
            with pytest.raises(AttributeError):
                str(value)  # verify using the templar fails


@pytest.mark.parametrize("value, value_type, type_under_test", values_and_types(), ids=value_and_types_ids())
def test_tag_copy(value: t.Any, value_type: t.Optional[type], type_under_test: type) -> None:
    templar = Templar(None)

    # FIXME: Is it valid to use a different TemplateContext (and Templar) from the one used to create the value originally?
    with TemplateContext(template_value=None, templar=templar, options=TemplateOptions()):
        if isinstance(value, _AnsibleLazyTemplateMixin):
            assert value._templar
            value._templar = None  # remove the templar, forcing an error if lazy behavior is triggered during tagging

        assert_tag_copy(value, value_type, type_under_test)

        if isinstance(value, _AnsibleLazyTemplateMixin):
            with pytest.raises(AttributeError):
                str(value)  # verify using the templar fails
