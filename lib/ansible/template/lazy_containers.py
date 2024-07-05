from __future__ import annotations

import dataclasses
import functools
import types
import weakref
import typing as t

from collections import abc as c

from jinja2 import Environment
from jinja2.nodes import EvalContext
from jinja2.runtime import Undefined, Context

from ansible.module_utils.datatag import (
    AnsibleTaggedObject,
    _ANSIBLE_ALLOWED_SCALAR_VAR_TYPES,
    _AnsibleTaggedDict,
    _AnsibleTaggedList,
    _NO_INSTANCE_STORAGE,
    _try_get_internal_tags_mapping,
    AnsibleTagHelper,
)
from .jinja_common import JinjaCallContext

from .utils import Omit, TemplateContext

from ansible.utils.display import Display
from ansible.utils.sentinel import Sentinel

from ._transform import _type_transform_mapping
from ..errors import AnsibleVariableTypeError

_ANSIBLE_LAZY_TEMPLATE_SLOTS = tuple(('_templar', '_mutator'))
_ITERATOR_TYPES: t.Final = (c.Iterator, c.ItemsView, c.KeysView, c.ValuesView, range)

display = Display()

if t.TYPE_CHECKING:
    from .templar import Templar


class _AnsibleLazyTemplateMixin:
    __slots__ = _NO_INSTANCE_STORAGE

    # static dispatch entries for scalar types are listed here
    # additional dispatch entries for container types are populated by our __init_subclass__
    _dispatch_types: dict[type, type[_AnsibleLazyTemplateMixin] | None] = {scalar_type: None for scalar_type in _ANSIBLE_ALLOWED_SCALAR_VAR_TYPES}

    # due to the way Jinja handles globals, we may encounter things like functions/methods in hooked getitem/getattr that
    # always pass through this mixin; we want to silently ignore those types
    # DTFIX-U: optimize this list by separating base types (using isinstance) from exact types using a set lookup
    _ignore_types = (
        types.FunctionType,  # DTFIX-U: global functions returned from Jinja globals __getitem__ def _lookup
        types.MethodType,  # DTFIX-U: ?
        functools.partial,  # triggered by TaskExecutor lookup partial injection as a template local
        type,  # DTFIX-U: this is a broad ignore for looking up `range` via `resolve_or_missing`; is there a better way?
        # DTFIX-U: is there a better way to include callables like these, so we're not playing whack-a-mole
        type(''.startswith),  # DTFIX-U: builtin_function_or_method
        type(Omit),
        # DTFIX-U: if we optimize to use type reference equality later, update this list to include relevant derived types
        Undefined,
        # Jinja passes these into filters/tests via @pass_environment et al.; silently ignore them
        Environment,
        Context,
        EvalContext,
    )

    _container_types: set[type] = set()  # populated by our __init_subclass__

    _templar: Templar
    _mutator: weakref.ReferenceType[JinjaCallContext] | None

    def __init_subclass__(cls, **kwargs) -> None:
        # DTFIX-U: this determination is very fragile to new layers added to the hierarchy
        tagged_type = cls.__mro__[1]
        native_type = tagged_type.__mro__[1]

        cls._dispatch_types[native_type] = cls
        cls._dispatch_types[tagged_type] = cls
        cls._dispatch_types[cls] = None

        cls._container_types.add(native_type)
        cls._empty_tags_as_native = False  # never revert to the native type when no tags remain

    def __init__(self, contents: t.Iterable | types.EllipsisType | _LazyIterator) -> None:
        if isinstance(contents, _LazyIterator):
            self._templar = contents.lazy._templar
            self._mutator = contents.lazy._mutator
        elif isinstance(contents, _AnsibleLazyTemplateMixin):
            self._templar = contents._templar
            self._mutator = contents._mutator
        else:
            self._templar = TemplateContext.current().templar  # pylint: disable=assigning-non-slot  # slot defined in derived type
            self._mutator = None  # pylint: disable=assigning-non-slot  # slot defined in derived type

    def __reduce_ex__(self, protocol):
        raise NotImplementedError("Pickling of Ansible lazy objects is not permitted.")

    @staticmethod
    def _try_create(item: t.Any) -> t.Any:
        # DTFIX-U: should we be supporting arbitrary sequences and mappings here?

        # DTFIX-U: this double-copy is very wasteful- optimize with a new "wrap_with_type" classmethod on
        #  AnsibleTaggedObject or ? Also, maybe augment AnsibleTaggedObject._tag_value with the ability to force the wrapper
        #  type or an alternate type map instead?

        # DTFIX-U: add an optimization to avoid looking at tagged types for entire categories of things we're not interested in
        # DTFIX-U: consider optimizing empty container case (return input)?

        item_type = type(item)

        # try to use exact type match first to determine which wrapper (if any) to apply; isinstance checks
        # are extremely expensive, so try to avoid them for our commonly-supported types
        if not (dispatcher_candidate := _AnsibleLazyTemplateMixin._dispatch_types.get(item_type, ...)):
            return item

        if dispatcher_candidate is ...:
            if transform := _type_transform_mapping.get(item_type):
                # send the transformed result back through _try_create to ensure lazification (where applicable)
                return _AnsibleLazyTemplateMixin._try_create(transform(item))

            # we've deferred the expensive isinstance checks as late as possible
            for container_type in _AnsibleLazyTemplateMixin._container_types:
                if isinstance(item, container_type):
                    display.warning(f'Converting unsupported {item_type} to {container_type}.', obj=item)
                    dispatcher = _AnsibleLazyTemplateMixin._dispatch_types[container_type]
                    break
            else:
                # DTFIX-U: collapse special handling here?
                if isinstance(item, _ITERATOR_TYPES):
                    raise AnsibleVariableTypeError(variable_type=type(item))

                # DTFIX-U: what do we want here? such as HostVars, HostVarsVars
                # DTFIX-U: we now have strict checking of variable types leaving templating, is this warning redundant?
                # DTFIX-U: undefined types need to be here too? (prevent warnings from with_first_found loops with undefined values)
                if not isinstance(item, _AnsibleLazyTemplateMixin._ignore_types):
                    display.warning(f'Encountered unsupported {item_type} type.')

                return item
        else:
            dispatcher = dispatcher_candidate  # type: ignore

        # Create a generator that yields the elements of `item` wrapped in a `_LazyValue` wrapper.
        # The wrapper is used to signal to the lazy container that the value must be processed before being returned.
        # Values added to the lazy container later through other means will be returned as-is, without any special processing.
        lazy_values = dispatcher._lazy_values(item)

        tags_mapping = _try_get_internal_tags_mapping(item)
        value = t.cast(AnsibleTaggedObject, dispatcher)._instance_factory(lazy_values, tags_mapping)

        return value

    def _non_lazy_copy(self) -> t.Collection:
        """
        Return a non-lazy copy of this collection.
        Any remaining lazy wrapped values will be unwrapped without further processing.
        Tags on this instance will be preserved on the returned copy.
        """
        raise NotImplementedError()  # pragma: nocover

    @staticmethod
    def _lazy_values(values: t.Any, /) -> t.Iterable:
        """
        Return an iterable that wraps each of the given elements in a lazy wrapper.
        Only elements wrapped this way will receive lazy processing when retrieved from the collection.
        """
        raise NotImplementedError()  # pragma: nocover

    def _pre_mutate(self) -> None:
        """
        Ensure that the collection values are properly lazified if we were modified by a different mutation context, then record the current mutation context.
        """
        if self._mutator:
            if self._mutator() is not (jcc := JinjaCallContext.current()):
                self._ensure_lazy_values()

                # avoid resetting mutator on repeated setitems in the same context
                self._mutator = weakref.ref(jcc)  # pylint: disable=assigning-non-slot  # slot defined in derived type
        else:
            self._mutator = weakref.ref(JinjaCallContext.current())  # pylint: disable=assigning-non-slot  # slot defined in derived type

    def _ensure_lazy_values(self) -> None:
        """Ensure that all values in the collection are properly lazified (required, abstract)."""
        raise NotImplementedError()  # pragma: nocover

    def _proxy_or_render_lazy_value(self, key: t.Any, value: t.Any) -> t.Any:
        """
        Ensure that the value is lazy-proxied or rendered, and if a key is provided, replace the original value with the result.
        """
        if type(value) is _LazyValue:  # pylint: disable=unidiomatic-typecheck
            new_value = self._templar.proxy_or_render_template(value.value)
        elif self._mutator and ((jcc := JinjaCallContext.current(optional=True)) is None or self._mutator() is not jcc):
            new_value = self._templar.proxy_or_render_template(value)

            if value is new_value:
                return value
        else:
            return value

        if key is not NoKeySentinel:
            self._native_type.__setitem__(self, key, new_value)  # type: ignore  # pylint: disable=unnecessary-dunder-call

        return new_value


@t.final
@dataclasses.dataclass(frozen=True, slots=True)
class _LazyValue:
    """Wrapper around values to indicate lazy behavior has not yet been applied."""

    value: t.Any


class NoKeySentinel(Sentinel):
    ...


@t.final
class _AnsibleLazyTemplateDict(_AnsibleTaggedDict, _AnsibleLazyTemplateMixin):
    __slots__ = _ANSIBLE_LAZY_TEMPLATE_SLOTS

    def __init__(self, contents: t.Iterable | types.EllipsisType | _LazyIterator = ..., /, **kwargs):
        _AnsibleLazyTemplateMixin.__init__(self, contents)

        if isinstance(contents, types.EllipsisType):
            super().__init__(**kwargs)
        elif isinstance(contents, _AnsibleLazyTemplateDict):
            super().__init__(dict.items(contents), **kwargs)
        elif isinstance(contents, _LazyIterator):
            super().__init__(contents.iterator, **kwargs)
        else:
            super().__init__(contents, **kwargs)

    def get(self, key: t.Any, default: t.Any = None) -> t.Any:
        if (value := super().get(key, NoKeySentinel)) is NoKeySentinel:
            return default

        return self._proxy_or_render_lazy_value(key, value)

    def __getitem__(self, key: t.Any, /) -> t.Any:
        return self._proxy_or_render_lazy_value(key, super().__getitem__(key))

    def __str__(self):
        return str(self.copy()._native_copy())  # inefficient, but avoids mutating the current instance (to make debugging practical)

    def __repr__(self):
        return repr(self.copy()._native_copy())  # inefficient, but avoids mutating the current instance (to make debugging practical)

    def __iter__(self):
        # We're using the base implementation, but must override `__iter__` to skip `dict` fast-path copy, which would bypass lazy behavior.
        # See: https://github.com/python/cpython/blob/ffcc450a9b8b6927549b501eff7ac14abc238448/Objects/dictobject.c#L3861-L3864
        return super().__iter__()

    def _ensure_lazy_values(self):
        for key, value in super().items():
            if type(value) is not _LazyValue:  # pylint: disable=unidiomatic-typecheck
                super().__setitem__(key, _LazyValue(value))

    def __setitem__(self, key, value):
        self._pre_mutate()
        super().__setitem__(key, value)

    def setdefault(self, key, default=None, /) -> t.Any:
        if (value := self.get(key, NoKeySentinel)) is not NoKeySentinel:
            return value

        self._pre_mutate()

        super().__setitem__(key, default)

        return default

    def update(self, *args, **kwargs) -> None:
        self._pre_mutate()
        super().update(*args, **kwargs)

    def clear(self) -> None:
        self._mutator = None
        super().clear()

    def items(self):
        for key, value in super().items():
            yield [key, self._proxy_or_render_lazy_value(key, value)]

    def values(self):
        for _key, value in self.items():
            yield value

    def pop(self, key, default=NoKeySentinel, /) -> t.Any:
        if (value := super().get(key, NoKeySentinel)) is NoKeySentinel:
            if default is NoKeySentinel:
                raise KeyError(key)

            return default

        value = self._proxy_or_render_lazy_value(NoKeySentinel, value)

        del self[key]

        return value

    def popitem(self) -> t.Any:
        try:
            key = next(reversed(self))
        except StopIteration:
            raise KeyError("popitem(): dictionary is empty")

        value = self._proxy_or_render_lazy_value(NoKeySentinel, self[key])

        del self[key]

        return [key, value]  # return a list to avoid automatic tuple conversion later (which may trigger a warning)

    def _native_copy(self) -> dict:
        return dict(self.items())

    def _item_source(self):
        return _LazyIterator(dict.items(self), self)

    def _non_lazy_copy(self) -> dict:
        # pylint: disable=unidiomatic-typecheck
        return AnsibleTagHelper.tag_copy(self, ((k, v.value if type(v) is _LazyValue else v) for k, v in dict.items(self)), value_type=dict)

    @staticmethod
    def _lazy_values(values: dict, /) -> t.Generator:
        yield from ((k, _LazyValue(v)) for k, v in values.items())

    @staticmethod
    def _proxy_or_render_other(other: t.Any | None) -> None:
        """Call `_proxy_or_render_lazy_values` if `other` is a lazy dict. Used internally by comparison methods."""
        if type(other) is _AnsibleLazyTemplateDict:  # pylint: disable=unidiomatic-typecheck
            other._proxy_or_render_lazy_values()

    def _proxy_or_render_lazy_values(self) -> None:
        """Ensure all `_LazyValue` wrapped values have been processed."""
        for _unused in self.values():
            pass

    def __eq__(self, other):
        self._proxy_or_render_lazy_values()
        self._proxy_or_render_other(other)
        return super().__eq__(other)

    def __ne__(self, other):
        self._proxy_or_render_lazy_values()
        self._proxy_or_render_other(other)
        return super().__ne__(other)

    def __or__(self, other):
        # Both sides end up going through _proxy_or_render_lazy_value, so there's no Templar preservation needed.
        # In the future this could be made more lazy when both Templar instances are the same, or if per-value Templar tracking was used.
        return _AnsibleLazyTemplateDict(super().__or__(other))

    def __ror__(self, other):
        # Both sides end up going through _proxy_or_render_lazy_value, so there's no Templar preservation needed.
        # In the future this could be made more lazy when both Templar instances are the same, or if per-value Templar tracking was used.
        return _AnsibleLazyTemplateDict(super().__ror__(other))

    def __deepcopy__(self, memo):
        # DTFIX-U: once lazies are wrappers, implement this as a basic deepcopy that preserves laziness
        raise NotImplementedError("Deep copy of Ansible lazy types is not supported.")


class _LazyIterator:
    def __init__(self, iterator: t.Iterable, lazy: _AnsibleLazyTemplateMixin) -> None:
        self.iterator = iterator
        self.lazy = lazy


@t.final
class _AnsibleLazyTemplateList(_AnsibleTaggedList, _AnsibleLazyTemplateMixin):
    __slots__ = _ANSIBLE_LAZY_TEMPLATE_SLOTS

    def __init__(self, contents: t.Iterable | types.EllipsisType | _LazyIterator = ..., /):
        _AnsibleLazyTemplateMixin.__init__(self, contents)

        if isinstance(contents, types.EllipsisType):
            super().__init__()
        elif isinstance(contents, _AnsibleLazyTemplateList):
            super().__init__(list.__iter__(contents))
        elif isinstance(contents, _LazyIterator):
            super().__init__(contents.iterator)
        else:
            super().__init__(contents)

    def __getitem__(self, key: t.SupportsIndex | slice, /) -> t.Any:
        if type(key) is slice:  # pylint: disable=unidiomatic-typecheck
            return _AnsibleLazyTemplateList(_LazyIterator(super().__getitem__(key), self))

        return self._proxy_or_render_lazy_value(key, super().__getitem__(key))

    def __iter__(self):
        for key, value in enumerate(super().__iter__()):
            yield self._proxy_or_render_lazy_value(key, value)

    def insert(self, *args, **kwargs) -> None:
        self._pre_mutate()
        super().insert(*args, **kwargs)

    def clear(self) -> None:
        self._mutator = None
        super().clear()

    def append(self, *args, **kwargs) -> None:
        self._pre_mutate()
        super().append(*args, **kwargs)

    def extend(self, *args, **kwargs) -> None:
        self._pre_mutate()
        super().extend(*args, **kwargs)

    def __setitem__(self, *args, **kwargs) -> None:
        self._pre_mutate()
        super().__setitem__(*args, **kwargs)

    def pop(self, idx: t.SupportsIndex = -1, /) -> t.Any:
        if not self:
            raise IndexError('pop from empty list')

        try:
            value = self[idx]
        except IndexError:
            raise IndexError('pop index out of range')

        value = self._proxy_or_render_lazy_value(NoKeySentinel, value)

        del self[idx]

        return value

    def __str__(self):
        return str(self.copy()._native_copy())  # inefficient, but avoids mutating the current instance (to make debugging practical)

    def __repr__(self):
        return repr(self.copy()._native_copy())  # inefficient, but avoids mutating the current instance (to make debugging practical)

    def _item_source(self):
        return _LazyIterator(list.__iter__(self), self)

    def _non_lazy_copy(self) -> list:
        # pylint: disable=unidiomatic-typecheck
        return AnsibleTagHelper.tag_copy(self, (v.value if type(v) is _LazyValue else v for v in list.__iter__(self)), value_type=list)

    @staticmethod
    def _lazy_values(values: list, /) -> t.Generator:
        yield from (_LazyValue(v) for v in values)

    def _ensure_lazy_values(self) -> None:
        for idx, value in enumerate(super().__iter__()):
            if type(value) is not _LazyValue:  # pylint: disable=unidiomatic-typecheck
                super().__setitem__(idx, _LazyValue(value))

    @staticmethod
    def _proxy_or_render_other(other: t.Any | None) -> None:
        """Call `_proxy_or_render_lazy_values` if `other` is a lazy list. Used internally by comparison methods."""
        if type(other) is _AnsibleLazyTemplateList:  # pylint: disable=unidiomatic-typecheck
            other._proxy_or_render_lazy_values()

    def _proxy_or_render_lazy_values(self) -> None:
        """Ensure all `_LazyValue` wrapped values have been processed."""
        for _unused in self:
            pass

    def __eq__(self, other):
        self._proxy_or_render_lazy_values()
        self._proxy_or_render_other(other)
        return super().__eq__(other)

    def __ne__(self, other):
        self._proxy_or_render_lazy_values()
        self._proxy_or_render_other(other)
        return super().__ne__(other)

    def __gt__(self, other):
        self._proxy_or_render_lazy_values()
        self._proxy_or_render_other(other)
        return super().__gt__(other)

    def __ge__(self, other):
        self._proxy_or_render_lazy_values()
        self._proxy_or_render_other(other)
        return super().__ge__(other)

    def __lt__(self, other):
        self._proxy_or_render_lazy_values()
        self._proxy_or_render_other(other)
        return super().__lt__(other)

    def __le__(self, other):
        self._proxy_or_render_lazy_values()
        self._proxy_or_render_other(other)
        return super().__le__(other)

    def __contains__(self, item):
        self._proxy_or_render_lazy_values()
        return super().__contains__(item)

    def __reversed__(self):
        for idx in range(self.__len__() - 1, -1, -1):
            yield self[idx]

    def __add__(self, other):
        if isinstance(other, _AnsibleLazyTemplateList) and self._templar is not other._templar:
            # with different templars, delazify both, new collection picks up the ambient templar
            self._proxy_or_render_lazy_values()

            return _AnsibleLazyTemplateList(super().__add__(other))  # other is delazified by its __iter__

        # For all other cases, the new list inherits our templar and all values stay lazy.
        # We use list.__add__ to avoid implementing all its error behavior.
        return _AnsibleLazyTemplateList(_LazyIterator(super().__add__(other), self))

    def __radd__(self, other):
        if not (other_add := getattr(other, '__add__', None)):
            raise TypeError(f'unsupported operand type(s) for +: {type(other).__name__!r} and {type(self).__name__!r}') from None

        return _AnsibleLazyTemplateList(_LazyIterator(other_add(self), self))

    def __mul__(self, other):
        return _AnsibleLazyTemplateList(_LazyIterator(super().__mul__(other), self))

    def __rmul__(self, other):
        return _AnsibleLazyTemplateList(_LazyIterator(super().__rmul__(other), self))

    def index(self, *args, **kwargs) -> int:
        self._proxy_or_render_lazy_values()
        return super().index(*args, **kwargs)

    def remove(self, *args, **kwargs) -> None:
        self._proxy_or_render_lazy_values()
        super().remove(*args, **kwargs)

    def sort(self, *args, **kwargs) -> None:
        self._proxy_or_render_lazy_values()
        super().sort(*args, **kwargs)

    def __deepcopy__(self, memo):
        # DTFIX-U: once lazies are wrappers, implement this as a basic deepcopy that preserves laziness
        raise NotImplementedError("Deep copy of Ansible lazy types is not supported.")


def proxy_jinja_constant_container(value: t.Any) -> t.Any:
    """
    Return the given value as a lazy container if it is a Jinja constant container, otherwise return the value as-is.
    Since variables and plugin output are already lazy, it's safe to assume native containers are Jinja constants.
    The native container type check avoids calling _try_create on types that could trigger unwanted warnings/errors.
    """
    if type(value) in (list, tuple, dict):
        return _AnsibleLazyTemplateMixin._try_create(value)

    return value


def proxy_args(item: tuple) -> tuple:
    """
    Return the given args with values wrapped in lazy containers as needed.
    This is used to ensure lazy container behavior is applied to Jinja constant containers.
    No templating is attempted, since any kwargs with templates should have already been rendered.
    """
    return tuple(proxy_jinja_constant_container(value) for value in item)


def proxy_kwargs(item: dict[str, t.Any]) -> dict[str, t.Any]:
    """
    Return the given kwargs with values wrapped in lazy containers as needed.
    This is used to ensure lazy container behavior is applied to Jinja constant containers.
    No templating is attempted, since any kwargs with templates should have already been rendered.
    """
    return {key: proxy_jinja_constant_container(value) for key, value in item.items()}
