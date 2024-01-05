from __future__ import annotations

import contextlib
import copy
import dataclasses
import datetime
import inspect
import json
import mock
import pickle
import sys
import typing as t

import pytest

from collections.abc import Iterable, Mapping

from ansible.module_utils.common.json import (
    AnsibleJSONDecoder,
    AnsibleJSONEncoder,
)

from ansible.module_utils.datatag import (
    AnsibleDataclassTagBase,
    AnsibleDatatagBase,
    AnsibleSerializable,
    AnsibleSingletonTagBase,
    AnsibleSourcePosition,
    AnsibleTaggedObject,
    Deprecated,
    NotATemplate,
    NotTaggableError,
    SensitiveData,
    TrustedAsTemplate,
    UndecryptableVaultedValue,
    VaultedValue,
    _ANSIBLE_ALLOWED_NON_SCALAR_COLLECTION_VAR_TYPES,
    _AnsibleTaggedDateTime,
    _AnsibleTaggedSet,
    _AnsibleTaggedStr,
    _AnsibleTaggedBytes,
    _AnsibleTaggedDate,
    _AnsibleTaggedTuple,
    _AnsibleTaggedTime,
    _empty_frozenset,
    _try_get_internal_tags_mapping,
    _EMPTY_INTERNAL_TAGS_MAPPING,
)

try:
    from typing import Protocol
except ImportError:
    # deprecated: description='remove Protocol fallback' python_version='3.7'
    Protocol = object


class CopyProtocol(Protocol):
    def copy(self) -> t.Any:
        """Copy this instance."""


def test_tag_registration():
    assert AnsibleSerializable._known_type_map.get(Deprecated.__name__) is Deprecated
    assert AnsibleSerializable._known_type_map.get(SensitiveData.__name__) is SensitiveData


def test_tag_as_dict():
    nodata = SensitiveData()
    somedata = AnsibleSourcePosition(src="foo", line=1, col=2)

    assert nodata._as_dict() is not nodata._as_dict()  # returning a new dict each time?
    assert nodata._as_dict() == dict()
    expected_somedata_dict = dict(src="foo", line=1, col=2)
    assert somedata._as_dict() == expected_somedata_dict


def test_tag_repr():
    assert repr(SensitiveData()) == 'SensitiveData()'

    tag = AnsibleSourcePosition(src="bar", line=42, col=99)

    assert repr(tag) == "AnsibleSourcePosition(src='bar', line=42, col=99)"


# FIXME: this doesn't include the lazy template types
serializable_types = list(AnsibleSerializable._known_type_map.values()) + [AnsibleSerializable]

datatag_instances = [
    AnsibleSourcePosition(src='himom.yml', line=42, col=42),
    Deprecated(msg="hi mom, I'm deprecated", removal_date=datetime.date(2023, 1, 2), removal_version="42.42"),
    Deprecated(msg="minimal"),
    NotATemplate(),
    SensitiveData(),
    TrustedAsTemplate(),
    UndecryptableVaultedValue(),
    VaultedValue(ciphertext="hi mom I'm a secret"),
]

taggable_container_instances = [
    dict(hi="mom"),
    ['hi', 'mom'],
    {'hi mom'},  # kept as a single item set to allow repr() testing without worrying about non-deterministic order of set items
    ("hi", "mom",),
]

taggable_instances = taggable_container_instances + [
    b'hi mom',
    42.0,
    42,
    "hi mom",
    datetime.datetime(2023, 9, 15, 21, 5, 30, 1900, datetime.timezone.utc),
    datetime.date(2023, 9, 15),
    datetime.time(21, 5, 30, 1900),
]

tagged_object_instances = [AnsibleSourcePosition(src=__file__).tag(item) for item in taggable_instances]

serializable_instances = datatag_instances + tagged_object_instances

serializable_instances_with_instance_copy = [t.cast(CopyProtocol, item) for item in serializable_instances if hasattr(item, 'copy')]


def test_serializable_instances_cover_all_concrete_impls():
    tested_types = {type(instance_type) for instance_type in serializable_instances}

    # don't require instances for types marked abstract or types that are clearly intended to be so (but can't be marked as such)
    required_types = {instance_type for instance_type in serializable_types
                      if not inspect.isabstract(instance_type) and not instance_type.__name__.endswith('Base') and instance_type is not AnsibleTaggedObject}

    missing_types = required_types.difference(tested_types)

    assert not missing_types


@pytest.mark.parametrize("tagged_object_instance", tagged_object_instances, ids=[type(instance).__name__ for instance in tagged_object_instances])
def test_native_copy(tagged_object_instance: AnsibleTaggedObject) -> None:
    native_copy = tagged_object_instance.native_copy()

    assert type(tagged_object_instance) is not type(native_copy)
    assert isinstance(tagged_object_instance, type(native_copy))

    if not isinstance(native_copy, int):
        assert native_copy is not tagged_object_instance.native_copy()

    assert native_copy == tagged_object_instance
    assert native_copy == tagged_object_instance.native_copy()


def assert_round_trip(original_value, round_tripped_value):
    assert original_value == round_tripped_value
    assert AnsibleTaggedObject.tags(original_value) == AnsibleTaggedObject.tags(round_tripped_value)

    # singleton values should rehydrate as the shared singleton instance, all others should be a new instance
    if isinstance(original_value, AnsibleSingletonTagBase):
        assert original_value is round_tripped_value
    else:
        assert original_value is not round_tripped_value


json_serializable_instances = [pytest.param(item, marks=pytest.mark.xfail(reason="FDI035: not yet supported")) if type(item) in (
    _AnsibleTaggedDateTime,
    _AnsibleTaggedSet,
    _AnsibleTaggedBytes,
    _AnsibleTaggedDate,
    _AnsibleTaggedTuple,
    _AnsibleTaggedTime,
) else item for item in serializable_instances]


@pytest.mark.parametrize("serializable_instance", json_serializable_instances, ids=[type(instance).__name__ for instance in serializable_instances])
def test_json_roundtrip(serializable_instance):
    round_tripped_value = json.loads(json.dumps(serializable_instance, cls=AnsibleJSONEncoder, preserve_datatags=True), cls=AnsibleJSONDecoder)

    # FIXME: ensure items in collections are copies

    assert_round_trip(serializable_instance, round_tripped_value)


@pytest.mark.parametrize("serializable_instance", serializable_instances, ids=[type(instance).__name__ for instance in serializable_instances])
def test_pickle_roundtrip(serializable_instance):
    round_tripped_value = pickle.loads(pickle.dumps(serializable_instance))

    # FIXME: ensure items in collections are copies

    assert_round_trip(serializable_instance, round_tripped_value)


@pytest.mark.parametrize("serializable_instance", serializable_instances, ids=[type(instance).__name__ for instance in serializable_instances])
def test_deepcopy_roundtrip(serializable_instance):
    round_tripped_value = copy.deepcopy(serializable_instance)

    # FIXME: ensure items in collections are copies

    assert_round_trip(serializable_instance, round_tripped_value)


@pytest.mark.parametrize("serializable_instance", serializable_instances, ids=[type(instance).__name__ for instance in serializable_instances])
def test_copy_roundtrip(serializable_instance):
    round_tripped_value = copy.copy(serializable_instance)

    # FIXME: ensure items in collections are not copies

    assert_round_trip(serializable_instance, round_tripped_value)


@pytest.mark.parametrize("serializable_instance", serializable_instances_with_instance_copy,
                         ids=[type(instance).__name__ for instance in serializable_instances_with_instance_copy])
def test_instance_copy_roundtrip(serializable_instance: CopyProtocol):
    round_tripped_value = serializable_instance.copy()

    # FIXME: ensure items in collections are not copies

    assert_round_trip(serializable_instance, round_tripped_value)


@pytest.mark.parametrize("taggable_instance", taggable_instances, ids=[type(instance).__name__ for instance in taggable_instances])
def test_repr(taggable_instance) -> None:
    """Ensure the repr() of tagged instance is identical to the repr() returned by the underlying native Python type."""
    tagged_instance = NotATemplate().tag(taggable_instance)

    assert_repr(tagged_instance, taggable_instance)


def assert_repr(tagged_instance, taggable_instance) -> None:
    assert tagged_instance is not taggable_instance
    assert repr(tagged_instance) == repr(taggable_instance)


@pytest.mark.parametrize("taggable_instance", taggable_instances, ids=[type(instance).__name__ for instance in taggable_instances])
def test_str(taggable_instance) -> None:
    """Ensure the str() of tagged instance is identical to the str() returned by the underlying native Python type."""
    tagged_instance = NotATemplate().tag(taggable_instance)

    assert_str(tagged_instance, taggable_instance)


def assert_str(tagged_instance, taggable_instance) -> None:
    assert tagged_instance is not taggable_instance
    assert str(tagged_instance) == str(taggable_instance)


@pytest.mark.parametrize("taggable_instance", taggable_instances, ids=[type(instance).__name__ for instance in taggable_instances])
def test_untag(taggable_instance):
    """Ensure tagging and then untagging a taggable instance returns new instances as appropriate, with the correct tags and type."""
    tagged_instance = SensitiveData().tag(NotATemplate().tag(taggable_instance))

    one_less_tag = NotATemplate.untag(tagged_instance)

    assert one_less_tag is not tagged_instance
    assert type(one_less_tag) is type(tagged_instance)  # pylint: disable=unidiomatic-typecheck
    assert AnsibleTaggedObject.tags(one_less_tag) == frozenset((SensitiveData(),))

    no_tags = SensitiveData.untag(one_less_tag)

    assert no_tags is not one_less_tag
    assert type(no_tags) is type(taggable_instance)
    assert AnsibleTaggedObject.tags(no_tags) is _empty_frozenset

    still_no_tags = SensitiveData.untag(no_tags)

    assert still_no_tags is no_tags


@pytest.mark.parametrize("serializable_type", serializable_types, ids=[instance_type.__name__ for instance_type in serializable_types])
def test_slots(serializable_type: type) -> None:
    """Ensure __slots__ are properly defined on all serializable types."""
    if serializable_type in (AnsibleSerializable, AnsibleDatatagBase, AnsibleSingletonTagBase, AnsibleTaggedObject):
        expect_slots = True  # non-dataclass base types have no attributes, but still use slots
    elif issubclass(serializable_type, AnsibleSingletonTagBase):
        expect_slots = True  # singletons have no attributes, but still use slots
    elif issubclass(serializable_type, (int, bytes, tuple)):
        expect_slots = False  # non-empty slots are not supported by these variable-length data types, see: https://docs.python.org/3/reference/datamodel.html
    elif issubclass(serializable_type, AnsibleDataclassTagBase) or serializable_type == AnsibleDataclassTagBase:
        assert dataclasses.is_dataclass(serializable_type)  # everything extending AnsibleDataclassTagBase must be a dataclass
        expect_slots = sys.version_info >= (3, 10)  # 3.10+ dataclasses have attributes (and support slots)
    else:
        expect_slots = True  # normal types have attributes (and slots)

    # check for slots on the type itself, ignoring slots on parents
    has_slots = '__slots__' in serializable_type.__dict__
    assert has_slots == expect_slots

    # instances of concrete types using __slots__ should not have __dict__ (which would indicate missing __slots__ definitions in the class hierarchy)
    serializable_instance = {type(instance): instance for instance in serializable_instances}.get(serializable_type)

    if serializable_instance:
        has_dict = hasattr(serializable_instance, '__dict__')
        assert has_dict != expect_slots


@pytest.mark.parametrize("untaggable_instance", [
    None,
    True,
    False,
])
def test_silent_untaggable(untaggable_instance):
    post_tag = SensitiveData().tag(untaggable_instance)

    assert post_tag is untaggable_instance


def no_op() -> None:
    """No-op function."""


@pytest.mark.parametrize("untaggable_instance", [
    object(),
    no_op,
])
def test_fatal_untaggable(untaggable_instance):
    with pytest.raises(NotTaggableError):
        SensitiveData().tag(untaggable_instance)


def test_deserialize_unknown_type() -> None:
    with pytest.raises(ValueError):
        AnsibleSerializable.deserialize({AnsibleSerializable._TYPE_KEY: 'bogus'})


def test_get_tags_mapping_from_magicmock() -> None:
    assert _try_get_internal_tags_mapping(mock.MagicMock()) is _EMPTY_INTERNAL_TAGS_MAPPING


@pytest.mark.parametrize("sp, value", (
    (AnsibleSourcePosition(src="hi"), "hi"),
    (AnsibleSourcePosition(src="hi", line=1), "hi:1"),
    (AnsibleSourcePosition(src="hi", line=1, col=2), "hi:1:2"),
    (AnsibleSourcePosition(src="hi", col=2), "hi"),
    (AnsibleSourcePosition(src="hi", line=0), "hi:0"),
    (AnsibleSourcePosition(src="hi", line=0, col=0), "hi:0:0"),
    (AnsibleSourcePosition(src="hi", col=0), "hi"),
))
def test_ansible_source_position_str(sp: AnsibleSourcePosition, value: str) -> None:
    assert str(sp) == value


def test_unexpected_reduce_type() -> None:
    with pytest.raises(TypeError):
        NotATemplate().tag("")._reduce("str")  # type: ignore


_str_override_method_args: dict[str, tuple[tuple, dict[str, t.Any]]] = {
    'partition': ((' ',), {}),
    'removeprefix': ((' ',), {}),
    'removesuffix': ((' ',), {}),
}


# FIXME: query all available str methods and ensure they're implemented and tested or on an ignore list
@pytest.mark.parametrize('method_name', _AnsibleTaggedStr._scalar_str_methods + _AnsibleTaggedStr._iterable_str_methods)
def test_tagged_str_overrides(method_name: str) -> None:
    plain_value = ' hi mom '
    tagged_value = NotATemplate().tag(plain_value)

    args, kwargs = _str_override_method_args.get(method_name, (tuple(), {}))

    has_method = hasattr(str, method_name)

    def maybe_raises():
        return contextlib.nullcontext() if has_method else pytest.raises(AttributeError)

    with maybe_raises():
        plain_result = getattr(plain_value, method_name)(*args, **kwargs)

    with maybe_raises():
        tagged_result = getattr(tagged_value, method_name)(*args, **kwargs)

    if not has_method:
        # if the method isn't implemented, the rest of the test is N/A
        return

    assert plain_result == tagged_result
    assert plain_result is not plain_value  # ensure the input test string is always transformed; if not, the test needs a more complex test value
    assert plain_result is not tagged_result

    if isinstance(plain_result, Iterable) and not isinstance(plain_result, (str, bytes)):
        assert all(NotATemplate.is_tagged_on(value) for value in tagged_result)
        assert AnsibleTaggedObject.tags(tagged_result) is _empty_frozenset
    else:
        assert NotATemplate.is_tagged_on(tagged_result)


def test_tagged_bytes_decode() -> None:
    value = NotATemplate().tag(b'hi mom').decode()

    assert value == 'hi mom'
    assert NotATemplate.is_tagged_on(value)


def test_tag_types() -> None:
    value = SensitiveData().tag(NotATemplate().tag("hi"))

    assert AnsibleTaggedObject.tag_types(value) == {SensitiveData, NotATemplate}
    assert AnsibleTaggedObject.tag_types("hi") is _empty_frozenset


def test_deprecated_invalid_date_type() -> None:
    with pytest.raises(TypeError):
        Deprecated(msg="test", removal_date="wrong")  # type: ignore


def test_tag_with_invalid_tag_type() -> None:
    with pytest.raises(TypeError):
        AnsibleTaggedObject.tag("", ["not a tag"])  # type: ignore


def test_tag_value_type_specified_untagged() -> None:
    value = AnsibleTaggedObject.tag(iter((1, 2, 3)), tuple(), value_type=list)

    assert isinstance(value, list)
    assert value == [1, 2, 3]


def container_values_and_types(types: t.Iterable[type[t.Collection]], instances: list[t.Collection]) -> list[tuple[t.Any, t.Optional[type], type]]:
    sources = []

    for type_under_test in sorted(types, key=lambda item: item.__name__):
        value = [instance for instance in instances if isinstance(instance, type_under_test)][0]

        # This test creates a generator to source items from the value to facilitate optimized creation of collections when tagging and copying tags.
        # To avoid triggering special behavior during iteration, a native copy is used when the value is a tagged object.
        if isinstance(value, AnsibleTaggedObject):
            native_value = value.native_copy()
        else:
            native_value = value

        if isinstance(value, Mapping):
            generator = ((k, v) for k, v in native_value.items())
        else:
            generator = (item for item in native_value)

        sources.extend((
            (value, None, type_under_test),  # testing the actual type without specifying value_type
            (generator, type_under_test, type_under_test),  # testing via a generator, which requires use of value_type
        ))

    return sources


def container_test_ids(values: list[tuple[t.Any, t.Optional[type], type]]) -> list[str]:
    return [f'{type_under_test.__name__} from {type(value).__name__}' for value, _value_type, type_under_test in values]


def values_and_types() -> list[tuple[t.Any, t.Optional[type], type]]:
    return container_values_and_types(_ANSIBLE_ALLOWED_NON_SCALAR_COLLECTION_VAR_TYPES, taggable_instances + tagged_object_instances)


def value_and_types_ids() -> list[str]:
    return container_test_ids(values_and_types())


@pytest.mark.parametrize("value, value_type, type_under_test", values_and_types(), ids=value_and_types_ids())
def test_tag(value: t.Any, value_type: t.Optional[type], type_under_test: type) -> None:
    """Ensure tagging a value returns the correct type and tags."""
    tag = SensitiveData()

    result = AnsibleTaggedObject.tag(value, tags=tag, value_type=value_type)

    assert isinstance(result, type_under_test)
    assert tag in AnsibleTaggedObject.tags(result)


@pytest.mark.parametrize("value, value_type, type_under_test", values_and_types(), ids=value_and_types_ids())
def test_tag_copy(value: t.Any, value_type: t.Optional[type], type_under_test: type) -> None:
    """Ensure copying tags returns the correct type and tags."""
    tag = SensitiveData()
    src = tag.tag("sensitive")

    result = AnsibleTaggedObject.tag_copy(src, value, value_type=value_type)

    assert isinstance(result, type_under_test)
    assert tag in AnsibleTaggedObject.tags(result)


def test_tag_builtins():
    values = [123, 123.45, 'a string value', tuple([1, 2, 3]), [1, 2, 3], {1, 2, 3}, dict(one=1, two=2)]

    for original_val in values:
        tagged_val = SensitiveData().tag(original_val)
        zero_tagged_val = AnsibleTaggedObject.tag(original_val, [])  # should return original value, not an empty tagged obj

        assert original_val == tagged_val  # equality should pass
        assert not SensitiveData.is_tagged_on(original_val)  # immutable original value via bool check
        assert SensitiveData.get_tag(original_val) is None  # immutable original value via get_tag
        assert not AnsibleTaggedObject.tags(original_val)  # immutable original value via tags

        assert SensitiveData.is_tagged_on(tagged_val)
        assert SensitiveData.get_tag(tagged_val) is SensitiveData()  # singleton tag type, should be reference-equal
        assert original_val is zero_tagged_val  # original value should reference-equal the zero-tagged value

        somedata_tag = AnsibleSourcePosition(src="foo", line=12, col=34)

        multi_tagged_val = somedata_tag.tag(tagged_val)
        assert tagged_val is not multi_tagged_val
        assert SensitiveData.is_tagged_on(multi_tagged_val)
        assert AnsibleSourcePosition.is_tagged_on(multi_tagged_val)
        assert SensitiveData.get_tag(multi_tagged_val) is SensitiveData()  # singleton tag type, should be reference-equal
        assert AnsibleSourcePosition.get_tag(multi_tagged_val) is somedata_tag
