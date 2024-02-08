from __future__ import annotations

import abc
import dataclasses
import copy
import datetime
import functools
import inspect
import sys
import types

from ..compat import typing as t
from itertools import chain
from collections.abc import Collection, Mapping

if sys.version_info >= (3, 8):
    _pickle_protocol = t.SupportsIndex
else:
    # deprecated: description='always use SupportsIndex for __reduce_ex__ protocol' python_version='3.7'
    _pickle_protocol = int

if sys.version_info >= (3, 10):
    # Using slots for reduced memory usage and improved performance.
    _tag_dataclass_kwargs = dict(frozen=True, kw_only=True, slots=True)
else:
    # deprecated: description='always use dataclass slots and keyword-only args' python_version='3.9'
    _tag_dataclass_kwargs = dict(frozen=True)

_T = t.TypeVar('_T')
_TAnsibleSerializable = t.TypeVar('_TAnsibleSerializable', bound='AnsibleSerializable')
_TAnsibleDatatagBase = t.TypeVar('_TAnsibleDatatagBase', bound='AnsibleDatatagBase')
_TAnsibleTaggedObject = t.TypeVar('_TAnsibleTaggedObject', bound='AnsibleTaggedObject')

_NO_INSTANCE_STORAGE = t.cast(t.Tuple[str], tuple())
_ANSIBLE_TAGGED_OBJECT_SLOTS = tuple(('_ansible_tags_mapping',))

# shared empty frozenset for default values
_empty_frozenset: t.FrozenSet = frozenset()


class AnsibleSerializable(metaclass=abc.ABCMeta):
    __slots__ = _NO_INSTANCE_STORAGE

    _known_type_map: t.Dict[str, t.Type['AnsibleSerializable']] = {}
    _TYPE_KEY: str = '__ansible_type'

    def __init_subclass__(cls, **kwargs):
        # this is needed to call __init__subclass__ on mixins for derived types
        super().__init_subclass__(**kwargs)

        # FIXME: is there a better way to exclude non-abstract types which are base classes?
        if not inspect.isabstract(cls) and not cls.__name__.endswith('Base') and cls.__name__ != 'AnsibleTaggedObject':
            AnsibleSerializable._known_type_map[cls.__name__] = cls

    @classmethod
    @abc.abstractmethod
    def _from_dict(cls: t.Type[_TAnsibleSerializable], d: t.Dict[str, t.Any]) -> _TAnsibleSerializable:
        """Return an instance of this type, created from the given dictionary."""

    @abc.abstractmethod
    def _as_dict(self) -> t.Dict[str, t.Any]:
        """Return a serialized version of this instance as a dictionary."""

    def serialize(self) -> t.Dict[str, t.Any]:
        value = self._as_dict()
        value.update({AnsibleSerializable._TYPE_KEY: self.__class__.__name__})

        return value

    @staticmethod
    def deserialize(data: t.Dict[str, t.Any]) -> t.Optional['AnsibleSerializable']:
        source = data.copy()  # FIXME: is there a more efficient way to operate on a copy of data?
        type_name = source.pop(AnsibleSerializable._TYPE_KEY, ...)

        if type_name is ...:
            return None

        type_value = AnsibleSerializable._known_type_map.get(type_name)

        if not type_value:
            raise ValueError(f'An unknown {AnsibleSerializable._TYPE_KEY!r} value {type_name!r} was encountered during deserialization.')

        return type_value._from_dict(source)

    def _repr(self, name: str) -> str:
        args = self._as_dict()
        arg_string = ', '.join((f'{k}={v!r}' for k, v in args.items()))
        return f'{name}({arg_string})'


# FIXME: need caution tape about adding new tag types being a bad idea
#  (eg, since propagation behavior has to be considered for each type every place it happens)
class AnsibleDatatagBase(AnsibleSerializable, metaclass=abc.ABCMeta):
    __slots__ = _NO_INSTANCE_STORAGE

    # used by the datatag Ansible/Jinja test plugin to find tags by name
    _known_tag_type_map: t.Dict[str, t.Type['AnsibleDatatagBase']] = {}
    _known_tag_types: t.Set[t.Type['AnsibleDatatagBase']] = set()

    def __init_subclass__(cls, **kwargs):
        # NOTE: This method is called twice when the datatag type is a dataclass.
        super().__init_subclass__(**kwargs)

        # FIXME: "freeze" this after module init has completed to discourage custom external tag subclasses

        # FIXME: is there a better way to exclude non-abstract types which are base classes?
        if not inspect.isabstract(cls) and not cls.__name__.endswith('Base'):
            existing = AnsibleDatatagBase._known_tag_type_map.get(cls.__name__)

            if existing:
                # When the datatag type is a dataclass, the first instance will be the non-dataclass type.
                # It must be removed from the known tag types before adding the dataclass version.
                AnsibleDatatagBase._known_tag_types.remove(existing)

            AnsibleDatatagBase._known_tag_type_map[cls.__name__] = cls
            AnsibleDatatagBase._known_tag_types.add(cls)

    @classmethod
    def is_tagged_on(cls, value: t.Any) -> bool:
        return cls in _try_get_internal_tags_mapping(value)

    @classmethod
    def get_tag(cls: t.Type[_TAnsibleDatatagBase], value: t.Any) -> t.Optional[_TAnsibleDatatagBase]:
        return _try_get_internal_tags_mapping(value).get(cls)  # type: ignore[return-value]

    @classmethod
    def untag(cls, value: _T) -> _T:
        return AnsibleTaggedObject.untag(value, cls)

    @classmethod
    def _from_dict(cls: t.Type[_TAnsibleDatatagBase], d: t.Dict[str, t.Any]) -> _TAnsibleDatatagBase:
        return cls(**d)

    def tag(self, value: _T) -> _T:
        return AnsibleTaggedObject.tag(value, self)

    def __repr__(self) -> str:
        return AnsibleSerializable._repr(self, self.__class__.__name__)


if sys.version_info >= (3, 9):
    # Include the key and value types in the type hints on Python 3.9 and later.
    # Earlier versions do not support subscriptable dict.
    # deprecated: description='always use subscriptable dict' python_version='3.8'
    class _AnsibleTagsMapping(dict[type[AnsibleDatatagBase], AnsibleDatatagBase]):
        __slots__ = _NO_INSTANCE_STORAGE

        # FIXME: do we want to try to implement read-only dict support?
        #        what we have below works for deepcopy, but not pickle
        #        it's also not perfect, since __init__ still mutates the dict
        # def update(self, *args, **kwargs) -> None:
        #     raise NotImplementedError()
        #
        # def clear(self, *args, **kwargs) -> None:
        #     raise NotImplementedError()
        #
        # def pop(self, *args, **kwargs) -> None:
        #     raise NotImplementedError()
        #
        # def popitem(self, *args, **kwargs) -> None:
        #     raise NotImplementedError()
        #
        # def setdefault(self, *args, **kwargs) -> None:
        #     raise NotImplementedError()
        #
        # def __ior__(self, *args, **kwargs) -> None:
        #     raise NotImplementedError()
        #
        # def __delitem__(self, *args, **kwargs):
        #     raise NotImplementedError()
        #
        # def __setitem__(self, *args, **kwargs) -> None:
        #     raise NotImplementedError(f'{args} {kwargs}')
        #
        # def __deepcopy__(self, *args, **kwargs) -> t.Any:
        #     return self
else:
    class _AnsibleTagsMapping(dict):
        __slots__ = _NO_INSTANCE_STORAGE


_EMPTY_INTERNAL_TAGS_MAPPING = t.cast(_AnsibleTagsMapping, types.MappingProxyType({}))
"""
An empty read-only mapping of tags.
Also used as a sentinel to cheaply determine that a type is not tagged by using a reference equality check.
"""


# FIXME: This should probably reside elsewhere.
def is_non_scalar_collection_type(value: type) -> bool:
    """Returns True if the value is a non-scalar collection type, otherwise returns False."""
    # FIXME: this includes _AnsibleTaggedVaultBomb and thus _VaultBomb
    return issubclass(value, Collection) and not issubclass(value, str) and not issubclass(value, bytes)


def _try_get_internal_tags_mapping(value: t.Any) -> _AnsibleTagsMapping:
    """Return the internal tag mapping of the given value, or a sentinel value if it is not tagged."""
    # noinspection PyBroadException
    try:
        # noinspection PyProtectedMember
        tags = value._ansible_tags_mapping
    except Exception:
        # try/except is a cheap way to determine if this is a tagged object without using isinstance
        # handling Exception accounts for types that may raise something other than AttributeError
        return _EMPTY_INTERNAL_TAGS_MAPPING

    # handle cases where the instance always returns something, such as AnsibleUndefined or MagicMock
    if type(tags) is not _AnsibleTagsMapping:  # pylint: disable=unidiomatic-typecheck
        return _EMPTY_INTERNAL_TAGS_MAPPING

    return tags


class NotTaggableError(TypeError):
    def __init__(self, value):
        super(NotTaggableError, self).__init__('{} is not taggable'.format(value))


class AnsibleSingletonTagBase(AnsibleDatatagBase):
    __slots__ = _NO_INSTANCE_STORAGE

    def __new__(cls):
        try:
            # noinspection PyUnresolvedReferences
            return cls._instance
        except AttributeError:
            cls._instance = AnsibleDatatagBase.__new__(cls)

        # noinspection PyUnresolvedReferences
        return cls._instance

    def _as_dict(self) -> t.Dict[str, t.Any]:
        return {}


@dataclasses.dataclass(**_tag_dataclass_kwargs)
class AnsibleDataclassTagBase(AnsibleDatatagBase):
    def _as_dict(self) -> t.Dict[str, t.Any]:
        # omit None values when None is the field default
        fields = ((field, getattr(self, field.name)) for field in dataclasses.fields(self))
        return {field.name: value for field, value in fields if value is not None or field.default is not None}


@dataclasses.dataclass(**_tag_dataclass_kwargs)
class AnsibleSourcePosition(AnsibleDataclassTagBase):
    src: str
    line: t.Optional[int] = None
    col: t.Optional[int] = None

    def __str__(self) -> str:
        if self.line is not None:
            if self.col is not None:
                return f'{self.src}:{self.line}:{self.col}'

            return f'{self.src}:{self.line}'

        return f'{self.src}'


@dataclasses.dataclass(**_tag_dataclass_kwargs)
class Deprecated(AnsibleDataclassTagBase):
    msg: str
    removal_date: t.Optional[datetime.date] = None
    removal_version: t.Optional[str] = None

    def __post_init__(self):
        # FIXME: we should probably have more strict type checks here for the other fields

        if type(self.removal_date) not in (type(None), datetime.date):
            raise TypeError(f'removal_date must be {datetime.date} instead of {type(self.removal_date)}')

    @classmethod
    def _from_dict(cls, d: t.Dict[str, t.Any]) -> Deprecated:
        source = d
        removal_date = source.get('removal_date')

        if removal_date is not None:
            source = source.copy()
            source['removal_date'] = datetime.date.fromisoformat(removal_date)

        return cls(**source)

    def _as_dict(self) -> t.Dict[str, t.Any]:
        value = AnsibleDataclassTagBase._as_dict(self)

        if self.removal_date is not None:
            value['removal_date'] = self.removal_date.isoformat()

        return value


@dataclasses.dataclass(**_tag_dataclass_kwargs)
class VaultedValue(AnsibleDataclassTagBase):
    ciphertext: str


# separate tag for vaulted values we couldn't decrypt on load
class UndecryptableVaultedValue(AnsibleSingletonTagBase):
    __slots__ = _NO_INSTANCE_STORAGE


class TrustedAsTemplate(AnsibleSingletonTagBase):
    __slots__ = _NO_INSTANCE_STORAGE


# used for internal things like error messages that might contain a template-ish looking thing but that we don't
# want to spam users with untrusted warnings or unnecessarily recurse into containers we know shouldn't be templated (for performance, not security)
class NotATemplate(AnsibleSingletonTagBase):
    __slots__ = _NO_INSTANCE_STORAGE


class AnsibleTaggedObject(AnsibleSerializable):
    __slots__ = _NO_INSTANCE_STORAGE

    native_type: type
    item_source: t.Optional[t.Callable] = None

    _tagged_type_map: t.Dict[type, t.Type['AnsibleTaggedObject']] = {}
    _collection_types: t.Set[t.Type[Collection]] = set()

    _empty_tags_as_native = True  # by default, untag will revert to the native type when no tags remain
    _ansible_tags_mapping = _EMPTY_INTERNAL_TAGS_MAPPING
    """
    Efficient internal storage of tags, indexed by tag type.
    Contains no more than one instance of each tag type.
    This is defined as a class attribute to support type hinting and documentation.
    It is overwritten with an instance attribute during instance creation.
    """

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)

        try:
            cls._init_class()  # type: ignore[attr-defined]
        except AttributeError:
            pass

        try:
            cls.native_type
        except AttributeError:
            cls.native_type = cls.__bases__[0]

        if cls.item_source is None and is_non_scalar_collection_type(cls.native_type):
            cls.item_source = cls.native_type.__iter__  # type: ignore[attr-defined]

        if cls.item_source:
            cls._instance_factory = cls._instance_factory_collection

        AnsibleTaggedObject._tagged_type_map[cls.__mro__[1]] = cls

        if is_non_scalar_collection_type(cls):
            AnsibleTaggedObject._collection_types.update({cls, cls.__mro__[1]})

    def native_copy(self) -> t.Any:
        return self.native_type(self)  # pylint: disable=abstract-class-instantiated

    @classmethod
    def _instance_factory(cls, value: t.Any, tags_mapping: _AnsibleTagsMapping) -> t.Self:
        # There's no way to indicate cls is callable with a single arg without defining a useless __init__.
        instance = cls(value)  # type: ignore[call-arg]
        instance._ansible_tags_mapping = tags_mapping

        return instance

    @staticmethod
    def tag_types(value: t.Any) -> t.FrozenSet[t.Type[AnsibleDatatagBase]]:
        tags = _try_get_internal_tags_mapping(value)

        if tags is _EMPTY_INTERNAL_TAGS_MAPPING:
            return _empty_frozenset

        return frozenset(tags)

    @staticmethod
    def tags(value: t.Any) -> t.FrozenSet[AnsibleDatatagBase]:
        tags = _try_get_internal_tags_mapping(value)

        if tags is _EMPTY_INTERNAL_TAGS_MAPPING:
            return _empty_frozenset

        return frozenset(tags.values())

    @staticmethod
    @t.overload
    def tag_copy(src: t.Any, value: _T) -> _T:
        ...  # pragma: nocover

    @staticmethod
    @t.overload
    def tag_copy(src: t.Any, value: t.Any, *, value_type: type[_T]) -> _T:
        ...  # pragma: nocover

    @staticmethod
    @t.overload
    def tag_copy(src: t.Any, value: _T, *, value_type: None = None) -> _T:
        ...  # pragma: nocover

    @staticmethod
    def tag_copy(src: t.Any, value: _T, *, value_type: t.Optional[type] = None) -> _T:
        """Return a copy of `value`, with tags copied from `src`, overwriting any existing tags of the same types."""
        return AnsibleTaggedObject.tag(value, AnsibleTaggedObject.tags(src), value_type=value_type)

    @staticmethod
    @t.overload
    def tag(value: _T, tags: t.Union[AnsibleDatatagBase, t.Iterable[AnsibleDatatagBase]]) -> _T:
        ...  # pragma: nocover

    @staticmethod
    @t.overload
    def tag(value: t.Any, tags: t.Union[AnsibleDatatagBase, t.Iterable[AnsibleDatatagBase]], *, value_type: type[_T]) -> _T:
        ...  # pragma: nocover

    @staticmethod
    @t.overload
    def tag(value: _T, tags: t.Union[AnsibleDatatagBase, t.Iterable[AnsibleDatatagBase]], *, value_type: None = None) -> _T:
        ...  # pragma: nocover

    @staticmethod
    def tag(value: _T, tags: t.Union[AnsibleDatatagBase, t.Iterable[AnsibleDatatagBase]], *, value_type: t.Optional[type] = None) -> _T:
        """
        Return a copy of `value`, with `tags` applied, overwriting any existing tags of the same types.
        If the `value` is not taggable, or `tags` is empty, the original `value` will be returned.
        If `value_type` was given, that type will be returned instead.
        """
        if value_type is None:
            value_type_specified = False
            value_type = type(value)
        else:
            value_type_specified = True

        # if no tags to apply, just return what we got
        # NB: this only works because the untaggable types are singletons (and thus direct type comparison works)
        if not tags or value_type in _untaggable_types:
            if value_type_specified:
                return value_type(value)

            return value

        tag_list: list[AnsibleDatatagBase]

        # noinspection PyProtectedMember
        if type(tags) in AnsibleDatatagBase._known_tag_types:
            tag_list = [tags]  # type: ignore[list-item]
        else:
            tag_list = list(tags)  # type: ignore[arg-type]

            for idx, tag in enumerate(tag_list):
                # noinspection PyProtectedMember
                if type(tag) not in AnsibleDatatagBase._known_tag_types:
                    # noinspection PyProtectedMember
                    raise TypeError(f'tags[{idx}] of type {type(tag)} is not one of {AnsibleDatatagBase._known_tag_types}')

        existing_internal_tags_mapping = _try_get_internal_tags_mapping(value)

        if existing_internal_tags_mapping is not _EMPTY_INTERNAL_TAGS_MAPPING:
            # include the existing tags first so new tags of the same type will overwrite
            tag_list = list(chain(existing_internal_tags_mapping.values(), tag_list))

        tags_mapping = _AnsibleTagsMapping((type(tag), tag) for tag in tag_list)
        tagged_type = AnsibleTaggedObject._get_tagged_type(value_type)

        return t.cast(_T, tagged_type._instance_factory(value, tags_mapping))

    @staticmethod
    def untag(value: _T, tag_type: t.Type[AnsibleDatatagBase]) -> _T:
        tag_set = AnsibleTaggedObject.tags(value)

        if not tag_set:
            return value

        tags_mapping = _AnsibleTagsMapping((type(tag), tag) for tag in tag_set if type(tag) is not tag_type)  # pylint: disable=unidiomatic-typecheck

        if not tags_mapping:
            if t.cast(AnsibleTaggedObject, value)._empty_tags_as_native:
                return t.cast(AnsibleTaggedObject, value).native_copy()

            tags_mapping = _EMPTY_INTERNAL_TAGS_MAPPING

        tagged_type = AnsibleTaggedObject._get_tagged_type(type(value))

        return t.cast(_T, tagged_type._instance_factory(value, tags_mapping))

    @staticmethod
    def _get_tagged_type(value_type: type) -> type[AnsibleTaggedObject]:
        tagged_type: t.Optional[type[AnsibleTaggedObject]]

        if issubclass(value_type, AnsibleTaggedObject):
            tagged_type = value_type
        else:
            tagged_type = AnsibleTaggedObject._tagged_type_map.get(value_type)

        if not tagged_type:
            raise NotTaggableError(value_type)

        return tagged_type

    def _as_dict(self) -> t.Dict[str, t.Any]:
        # FIXME: this is probably incomplete; don't we need a full deep copy (possibly with templating and access)?
        # This isn't a problem for consumers that are inherently recursive already (eg JSON serialization, repr, YAML)
        # Maybe just docstring clarification that it's not recursive and that returned nested containers may still have tagged types inside?
        return {
            'value': self.native_copy(),
            'tags': list(self._ansible_tags_mapping.values()),
        }

    @classmethod
    def _from_dict(cls: t.Type[_TAnsibleTaggedObject], d: t.Dict[str, t.Any]) -> _TAnsibleTaggedObject:
        return AnsibleTaggedObject.tag(**d)

    @classmethod
    def _instance_factory_collection(
            cls,
            value: t.Iterable,
            tags_mapping: _AnsibleTagsMapping,
    ) -> t.Self:
        if type(value) in AnsibleTaggedObject._collection_types:
            # use the underlying iterator to avoid access/iteration side effects (e.g. templating/wrapping on Lazy subclasses)
            instance = cls(cls.item_source(value))  # type: ignore[call-arg]
        else:
            # this is used when the value is a generator
            instance = cls(value)  # type: ignore[call-arg]

        instance._ansible_tags_mapping = tags_mapping

        return instance

    def _copy_collection(self) -> AnsibleTaggedObject:
        # use the underlying iterator to avoid access/iteration side effects (e.g. templating/wrapping on Lazy subclasses)
        return AnsibleTaggedObject.tag_copy(self, self.item_source(), value_type=type(self))

    @classmethod
    def _new(cls, value: t.Any, *args, **kwargs) -> t.Self:
        if type(value) is _AnsibleTagsMapping:  # pylint: disable=unidiomatic-typecheck
            self = cls.native_type.__new__(cls, *args, **kwargs)
            self._ansible_tags_mapping = value
            return self

        return cls.native_type.__new__(cls, value, *args, **kwargs)

    def _reduce(self, reduced: t.Union[str, tuple[t.Any, ...]]) -> tuple:
        if type(reduced) is not tuple:  # pylint: disable=unidiomatic-typecheck
            raise TypeError()

        updated: list[t.Any] = list(reduced)
        updated[1] = (self._ansible_tags_mapping,) + updated[1]

        return tuple(updated)


class _AnsibleTaggedStr(str, AnsibleTaggedObject):
    __slots__ = _ANSIBLE_TAGGED_OBJECT_SLOTS

    # FIXME: implement more methods here
    _scalar_str_methods = ('lstrip', 'strip', 'rstrip', 'encode', 'removeprefix', 'removesuffix')
    _iterable_str_methods = ('partition', 'rsplit', 'split')

    # deferred imperative customization, invoked by AnsibleTaggedObject
    @classmethod
    def _init_class(cls):
        for name in cls._scalar_str_methods:
            method = getattr(str, name, None)

            if method is not None:
                setattr(cls, name, functools.partialmethod(cls._delegate_scalar_result, method))

        for name in cls._iterable_str_methods:
            method = getattr(str, name, None)

            if method is not None:
                setattr(cls, name, functools.partialmethod(cls._delegate_iterable_result, method))

    def _delegate_scalar_result(self, target: t.Callable, *args, **kwargs) -> t.Any:
        result = target(self, *args, **kwargs)
        return AnsibleTaggedObject.tag_copy(self, result)

    def _delegate_iterable_result(self, target: t.Callable, *args, **kwargs) -> t.Iterable[str]:
        result = target(self, *args, **kwargs)
        tags = AnsibleTaggedObject.tags(self)
        return type(result)((AnsibleTaggedObject.tag(v, tags) for v in result))


# FIXME: same treatment and tests as _AnsibleTaggedStr for common utility methods; share setup code
class _AnsibleTaggedBytes(bytes, AnsibleTaggedObject):
    # nonempty __slots__ not supported for subtype of 'bytes'

    def decode(self, *args, **kwargs) -> str:
        return AnsibleTaggedObject.tag_copy(self, super().decode(*args, **kwargs))


class _AnsibleTaggedInt(int, AnsibleTaggedObject):
    # nonempty __slots__ not supported for subtype of 'int'
    pass


class _AnsibleTaggedFloat(float, AnsibleTaggedObject):
    __slots__ = _ANSIBLE_TAGGED_OBJECT_SLOTS


class _AnsibleTaggedDateTime(datetime.datetime, AnsibleTaggedObject):
    __slots__ = _ANSIBLE_TAGGED_OBJECT_SLOTS

    @classmethod
    def _instance_factory(cls, value: datetime.datetime, tags_mapping: _AnsibleTagsMapping) -> _AnsibleTaggedDateTime:
        instance = cls(
            year=value.year,
            month=value.month,
            day=value.day,
            hour=value.hour,
            minute=value.minute,
            second=value.second,
            microsecond=value.microsecond,
            tzinfo=value.tzinfo,
            fold=value.fold,
        )

        instance._ansible_tags_mapping = tags_mapping

        return instance

    def native_copy(self) -> datetime.datetime:
        return datetime.datetime(
            year=self.year,
            month=self.month,
            day=self.day,
            hour=self.hour,
            minute=self.minute,
            second=self.second,
            microsecond=self.microsecond,
            tzinfo=self.tzinfo,
            fold=self.fold,
        )

    def __new__(cls, year, *args, **kwargs):
        return super()._new(year, *args, **kwargs)

    def __reduce_ex__(self, protocol: _pickle_protocol) -> tuple:
        return super()._reduce(super().__reduce_ex__(protocol))

    def __repr__(self) -> str:
        return self.native_copy().__repr__()


class _AnsibleTaggedDate(datetime.date, AnsibleTaggedObject):
    __slots__ = _ANSIBLE_TAGGED_OBJECT_SLOTS

    @classmethod
    def _instance_factory(cls, value: datetime.date, tags_mapping: _AnsibleTagsMapping) -> _AnsibleTaggedDate:
        instance = cls(
            year=value.year,
            month=value.month,
            day=value.day,
        )

        instance._ansible_tags_mapping = tags_mapping

        return instance

    def native_copy(self) -> datetime.date:
        return datetime.date(
            year=self.year,
            month=self.month,
            day=self.day,
        )

    def __new__(cls, year, *args, **kwargs):
        return super()._new(year, *args, **kwargs)

    def __reduce__(self) -> tuple:
        return super()._reduce(super().__reduce__())

    def __repr__(self) -> str:
        return self.native_copy().__repr__()


class _AnsibleTaggedTime(datetime.time, AnsibleTaggedObject):
    __slots__ = _ANSIBLE_TAGGED_OBJECT_SLOTS

    @classmethod
    def _instance_factory(cls, value: datetime.time, tags_mapping: _AnsibleTagsMapping) -> _AnsibleTaggedTime:
        instance = cls(
            hour=value.hour,
            minute=value.minute,
            second=value.second,
            microsecond=value.microsecond,
            tzinfo=value.tzinfo,
            fold=value.fold,
        )

        instance._ansible_tags_mapping = tags_mapping

        return instance

    def native_copy(self) -> datetime.time:
        return datetime.time(
            hour=self.hour,
            minute=self.minute,
            second=self.second,
            microsecond=self.microsecond,
            tzinfo=self.tzinfo,
            fold=self.fold,
        )

    def __new__(cls, hour, *args, **kwargs):
        return super()._new(hour, *args, **kwargs)

    def __reduce_ex__(self, protocol: _pickle_protocol) -> tuple:
        return super()._reduce(super().__reduce_ex__(protocol))

    def __repr__(self) -> str:
        return self.native_copy().__repr__()


class _AnsibleTaggedDict(dict, AnsibleTaggedObject):
    __slots__ = _ANSIBLE_TAGGED_OBJECT_SLOTS

    item_source = dict.items

    def __copy__(self):
        return super()._copy_collection()

    def copy(self):
        return copy.copy(self)


class _AnsibleTaggedList(list, AnsibleTaggedObject):
    __slots__ = _ANSIBLE_TAGGED_OBJECT_SLOTS

    def __copy__(self):
        return super()._copy_collection()

    def copy(self):
        return copy.copy(self)


# FIXME: do we want frozenset too?
class _AnsibleTaggedSet(set, AnsibleTaggedObject):
    __slots__ = _ANSIBLE_TAGGED_OBJECT_SLOTS

    def __copy__(self):
        return super()._copy_collection()

    def copy(self):
        return copy.copy(self)

    def __init__(self, value=None, *args, **kwargs):
        if type(value) is _AnsibleTagsMapping:  # pylint: disable=unidiomatic-typecheck
            super().__init__(*args, **kwargs)
        else:
            super().__init__(value, *args, **kwargs)

    def __new__(cls, value=None, *args, **kwargs):
        return super()._new(value, *args, **kwargs)

    def __reduce_ex__(self, protocol: _pickle_protocol) -> tuple:
        return super()._reduce(super().__reduce_ex__(protocol))

    def __str__(self) -> str:
        return self.native_copy().__str__()

    def __repr__(self) -> str:
        return self.native_copy().__repr__()


class _AnsibleTaggedTuple(tuple, AnsibleTaggedObject):
    # nonempty __slots__ not supported for subtype of 'tuple'

    def __copy__(self):
        return super()._copy_collection()


# This set gets augmented with additional types when some controller-only types are imported.
# While we could proxy or subclass builtin singletons, they're idiomatically compared with "is" reference
# equality, which we can't customize.
_untaggable_types = frozenset({type(None), bool})

# noinspection PyProtectedMember
_ANSIBLE_ALLOWED_VAR_TYPES = _untaggable_types | set(AnsibleTaggedObject._tagged_type_map) | set(AnsibleTaggedObject._tagged_type_map.values())
"""These are the only types supported by Ansible's variable storage. Subclasses are not permitted."""


_ANSIBLE_ALLOWED_NON_SCALAR_COLLECTION_VAR_TYPES = frozenset(item for item in _ANSIBLE_ALLOWED_VAR_TYPES if is_non_scalar_collection_type(item))
_ANSIBLE_ALLOWED_MAPPING_VAR_TYPES = frozenset(item for item in _ANSIBLE_ALLOWED_VAR_TYPES if issubclass(item, Mapping))
_ANSIBLE_ALLOWED_SCALAR_VAR_TYPES = _ANSIBLE_ALLOWED_VAR_TYPES - _ANSIBLE_ALLOWED_NON_SCALAR_COLLECTION_VAR_TYPES
