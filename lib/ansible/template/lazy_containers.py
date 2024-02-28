from __future__ import annotations

import types

from collections import abc as c
from threading import Lock

from jinja2 import Environment
from jinja2.nodes import EvalContext
from jinja2.runtime import Undefined, Context

import ansible.module_utils.compat.typing as t

from ansible.module_utils.datatag import (
    AnsibleTaggedObject,
    _ANSIBLE_ALLOWED_SCALAR_VAR_TYPES,
    _AnsibleTaggedDict,
    _AnsibleTaggedList,
    _AnsibleTaggedSet,
    _AnsibleTaggedTuple,
    _NO_INSTANCE_STORAGE,
    _try_get_internal_tags_mapping,
)

from .utils import Omit, TemplateContext

from ansible.utils.display import Display

_ANSIBLE_LAZY_TEMPLATE_SLOTS = tuple(('_templar',))

display = Display()


@t.final
class _AnsibleRangeListAdapter(c.Sequence, AnsibleTaggedObject):
    """
    Wraps Python range objects for lazy conversion and access as list-ish objects.
    Ansible historically converted some common usages of range to lists.
    This wrapper is applied consistently at all Jinja plugin argument boundaries,
    """
    # FIXME: templatability?

    native_type = list

    _empty_tags_as_native = False  # never revert to the native type when no tags remain
    _subclasses_native_type = False  # augments range, acts like a list, subclasses Sequence

    def __init__(self, value: range) -> None:
        self._value = value

    def __iter__(self) -> t.Any:
        # FIXME: cap range size in iteration cases w/ config override?
        return iter(self._value)

    def __getitem__(self, item) -> t.Any:
        return self._value[item]

    def __contains__(self, item) -> bool:
        return item in self._value

    def __len__(self) -> int:
        return len(self._value)

    def __eq__(self, other: t.Any) -> bool:
        if isinstance(other, list):
            return list(self._value) == other

        return self._value == other

    def __repr__(self) -> str:
        return repr(self._value)

    def __bool__(self) -> bool:
        return bool(self._value)

    def __hash__(self) -> int:
        return hash(self._value)

    def __reversed__(self) -> t.Iterator:
        return reversed(self._value)

    def __getattr__(self, item) -> t.Any:
        if item in {'count', 'index', 'start', 'step', 'stop'}:
            return getattr(self._value, item)

        raise AttributeError(item)


@t.final
class _AnsibleLazyListAdapter(c.Sequence, AnsibleTaggedObject):
    """
    Wraps iterators and MappingViews for lazy conversion and access as list-ish objects.
    Ansible historically converted some common usages of these types to lists.
    This wrapper is applied consistently at all Jinja plugin argument boundaries,
    """
    # FIXME: templatability?

    native_type = list

    _empty_tags_as_native = False  # never revert to the native type when no tags remain
    _subclasses_native_type = False  # augments range, acts like a list, subclasses Sequence

    def __init__(self, source: c.Iterable | c.MappingView) -> None:
        self._source: c.Iterable = source  # type: ignore[assignment]  # mypy doesn't consider MappingView to be Iterable
        self._cached_elements_backing: list[t.Any] = []
        self._backing_lock = Lock()
        self._source_consumed = False

    @property
    def _cached_elements(self) -> list:
        if not self._source_consumed:
            with self._backing_lock:
                self._cached_elements_backing.extend(self._source)
                self._source_consumed = True

        return self._cached_elements_backing

    def __getitem__(self, item) -> t.Any:
        return self._cached_elements[item]

    def __contains__(self, item) -> bool:
        return item in self._cached_elements

    def __len__(self) -> int:
        try:
            # ask the source first, in case it knows its length
            return len(self._source)  # type: ignore[arg-type]
        except TypeError:
            # fall back to the populated cache
            return len(self._cached_elements)

    def __eq__(self, other):
        return self._cached_elements == other

    def index(self, value: t.Any, *args, **kwargs) -> int:
        return self._cached_elements.index(value, *args, **kwargs)

    def count(self, value: t.Any) -> int:
        return self._cached_elements.count(value)


class _AnsibleLazyTemplateMixin:
    __slots__ = _NO_INSTANCE_STORAGE

    # static dispatch entries for scalar types are listed here
    # additional dispatch entries for container types are populated by our __init_subclass__
    _dispatch_types: dict[type, type[AnsibleTaggedObject] | None] = {scalar_type: None for scalar_type in _ANSIBLE_ALLOWED_SCALAR_VAR_TYPES}

    # due to the way Jinja handles globals, we may encounter things like functions/methods in hooked getitem/getattr that
    # always pass through this mixin; we want to silently ignore those types
    # FIXME: optimize this list by separating base types (using isinstance) from exact types using a set lookup
    _ignore_types = (
        types.MethodType,
        type,  # FIXME: this is a broad ignore for looking up `range` via `resolve_or_missing`; is there a better way?
        # FIXME: is there a better way to include callables like these, so we're not playing whack-a-mole
        type(''.startswith),  # builtin_function_or_method
        type(Omit),
        # FIXME: if we optimize to use type reference equality later, update this list to include relevant derived types
        Undefined,
        # Jinja passes these into filters/tests via @pass_environment et al; silently ignore them
        Environment,
        Context,
        EvalContext,
        _AnsibleLazyListAdapter,
        _AnsibleRangeListAdapter,
    )

    _container_types: set[type] = set()  # populated by our __init_subclass__

    def __init_subclass__(cls, **kwargs) -> None:
        # FIXME: this determination is very fragile to new layers added to the hierarchy
        tagged_type = cls.__mro__[1]
        native_type = tagged_type.__mro__[1]

        cls._dispatch_types[native_type] = t.cast(type[AnsibleTaggedObject], cls)
        cls._dispatch_types[tagged_type] = t.cast(type[AnsibleTaggedObject], cls)
        cls._dispatch_types[cls] = None

        cls._container_types.add(native_type)
        cls._empty_tags_as_native = False  # never revert to the native type when no tags remain

    def __init__(self):
        self._templar = TemplateContext.current_or_raise().templar  # pylint: disable=assigning-non-slot  # slot defined in derived type

    @staticmethod
    def try_create(item: t.Any) -> t.Any:
        # FIXME: should we be supporting arbitrary sequences and mappings here?

        # FIXME: this double-copy is very wasteful- optimize with a new "wrap_with_type" classmethod on
        #  AnsibleTaggedObject or ? Also, maybe augment AnsibleTaggedObject._tag_value with the ability to force the wrapper
        #  type or an alternate type map instead?

        # FIXME: add an optimization to avoid looking at tagged types for entire categories of things we're not interested in
        # FIXME: consider optimizing empty container case (return input)?

        item_type = type(item)

        # try to use exact type match first to determine which wrapper (if any) to apply; isinstance checks
        # are extremely expensive, so try to avoid them for our commonly-supported types
        if not (dispatcher := _AnsibleLazyTemplateMixin._dispatch_types.get(item_type, ...)):
            return item

        # from this point on, we're always going to create a taggable type
        if dispatcher is ...:
            # we've deferred the expensive isinstance checks as late as possible
            for container_type in _AnsibleLazyTemplateMixin._container_types:
                if isinstance(item, container_type):
                    display.warning(f'Converting unsupported {item_type} to {container_type}.')
                    dispatcher = _AnsibleLazyTemplateMixin._dispatch_types[container_type]
                    break
            else:
                if type(item) is range:  # pylint: disable=unidiomatic-typecheck
                    return _AnsibleRangeListAdapter(item)

                # FIXME: use a better/cheaper identification mechanism
                if isinstance(item, (c.Iterator, c.MappingView)):
                    return _AnsibleLazyListAdapter(item)

                # FIXME: what do we want here? such as HostVars, HostVarsVars
                # FIXME: we now have strict checking of variable types leaving templating, is this warning redundant?
                # FIXME: undefined types need to be here too? (prevent warnings from with_first_found loops with undefined values)
                if not isinstance(item, _AnsibleLazyTemplateMixin._ignore_types):
                    display.warning(f'Encountered unsupported {item_type} type.')

                return item

        tags_mapping = _try_get_internal_tags_mapping(item)
        value = dispatcher._instance_factory(item, tags_mapping)

        return value

    def untemplated_tagged_copy(self) -> t.Collection:
        raise NotImplementedError()


@t.final
class _AnsibleLazyTemplateDict(_AnsibleTaggedDict, _AnsibleLazyTemplateMixin):
    __slots__ = _ANSIBLE_LAZY_TEMPLATE_SLOTS

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _AnsibleLazyTemplateMixin.__init__(self)

    def __getitem__(self, key: t.Any, /) -> t.Any:
        # FIXME: better access pattern for this?
        # FIXME: internally cache templated item responses for the lifetime of this wrapper so we don't repeatedly
        #  template the same values?
        return self._templar.proxy_or_render_template(super().__getitem__(key), key)

    # FIXME: fully implement iteration support
    # FIXME: do we need to implement templated key support?
    #        probably not, since lazy templates can be volatile, and thus not hashable

    def __str__(self):
        return self.__repr__()

    def __repr__(self):
        # delegate to the base class __repr__ impl
        return dict.__repr__(dict(self.items()))

    def items(self):
        for key, value in super().items():
            # FIXME: internally cache templated item responses for the lifetime of this wrapper so we don't repeatedly
            #  template the same values?
            yield key, self._templar.proxy_or_render_template(value, key)

    def values(self):
        for _key, value in self.items():
            yield value

    def native_copy(self) -> dict:
        return dict(self.items())

    def untemplated_tagged_copy(self) -> dict:
        return AnsibleTaggedObject.tag_copy(self, dict.items(self), value_type=dict)

    def __eq__(self, other):
        # FIXME: optimize this
        return dict(self.items()) == other

    def __ne__(self, other):
        # FIXME: optimize this
        return dict(self.items()) != other


@t.final
class _AnsibleLazyTemplateList(_AnsibleTaggedList, _AnsibleLazyTemplateMixin):
    __slots__ = _ANSIBLE_LAZY_TEMPLATE_SLOTS

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _AnsibleLazyTemplateMixin.__init__(self)

    def __getitem__(self, key: t.Any, /) -> t.Any:
        # FIXME: better access pattern for this?
        # FIXME: internally cache templated item responses for the lifetime of this wrapper so we don't repeatedly
        #  template the same values?
        return self._templar.proxy_or_render_template(super().__getitem__(key), key)

    def __iter__(self):
        for value in super().__iter__():
            yield self._templar.proxy_or_render_template(value)

    def __str__(self):
        return self.__repr__()

    def __repr__(self):
        # delegate to the base class __repr__ impl
        return list.__repr__(list(self.__iter__()))

    def native_copy(self) -> list:
        return list(iter(self))

    def untemplated_tagged_copy(self) -> list:
        return AnsibleTaggedObject.tag_copy(self, list.__iter__(self), value_type=list)

    def __eq__(self, other):
        # FIXME: optimize this
        return list(self) == other

    def __ne__(self, other):
        # FIXME: optimize this
        return list(self) != other

    def __gt__(self, other):
        # FIXME: optimize this
        return list(self) > other

    def __contains__(self, item):
        # FIXME: optimize this
        return item in list(self)

    def index(self, *args, **kwargs) -> int:
        # FIXME: optimize this
        # FIXME: when writing the docstring for this, include a note that mentions the input args are *NOT* templated by this method
        return list(self).index(*args, **kwargs)

    def remove(self, value) -> None:
        # FIXME: when writing the docstring for this, include a note that mentions the input args are *NOT* templated by this method
        self.pop(self.index(value))

    def sort(self, *args, **kwargs):
        # FIXME: do we possibly want to implement this?
        raise NotImplementedError('In-place sorting of a lazy templated list is not supported. Sort into a new list instead.')


# FIXME: we're considering removing this, reasons include:
#        lazy templates can be volatile, and thus not immutable, breaking hashing of the tuple
#        when including/dropping this, document rationale (cost/benefit analysis)
@t.final
class _AnsibleLazyTemplateTuple(_AnsibleTaggedTuple, _AnsibleLazyTemplateMixin):
    # nonempty __slots__ not supported for subtype of 'tuple'

    def __init__(self, *_args, **_kwargs):
        # NB: we're explicitly not calling super().__init__ here, since our hierarchy doesn't need it, and tuple's init is
        # object.__init__, which accepts no args beyond "self"
        _AnsibleLazyTemplateMixin.__init__(self)

    def __getitem__(self, key: t.Any, /) -> t.Any:
        # FIXME: better access pattern for this?
        # FIXME: internally cache templated item responses for the lifetime of this wrapper so we don't repeatedly
        #  template the same values?
        return self._templar.proxy_or_render_template(super().__getitem__(key), key)

    def __iter__(self):
        for value in super().__iter__():
            yield self._templar.proxy_or_render_template(value)

    def __str__(self):
        return self.__repr__()

    def __repr__(self):
        # delegate to the base class __repr__ impl
        return tuple.__repr__(tuple(self.__iter__()))

    def native_copy(self) -> tuple:
        return tuple(iter(self))

    def untemplated_tagged_copy(self) -> tuple:
        return AnsibleTaggedObject.tag_copy(self, tuple.__iter__(self), value_type=tuple)

    def __eq__(self, other):
        # FIXME: optimize this
        return tuple(self) == other

    def __ne__(self, other):
        # FIXME: optimize this
        return tuple(self) != other

    def __gt__(self, other):
        # FIXME: optimize this
        return tuple(self) > other

    def __contains__(self, item):
        # FIXME: optimize this
        return item in tuple(self)


# FIXME: we're considering removing this, reasons include:
#        lazy templates can be volatile, and thus not immutable, breaking set hashing
#        sets need a lot of work to deal with set operations
#        when including/dropping this, document rationale (cost/benefit analysis)
#        if keeping, more tests needed (see list for ideas)
@t.final
class _AnsibleLazyTemplateSet(_AnsibleTaggedSet, _AnsibleLazyTemplateMixin):
    __slots__ = _ANSIBLE_LAZY_TEMPLATE_SLOTS

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _AnsibleLazyTemplateMixin.__init__(self)

    def __iter__(self):
        for value in super().__iter__():
            yield self._templar.proxy_or_render_template(value)

    def __str__(self):
        return self.__repr__()

    def __repr__(self):
        # delegate to the base class __repr__ impl
        return set.__repr__(set(self.__iter__()))

    def native_copy(self) -> set:
        return set(iter(self))

    def untemplated_tagged_copy(self) -> set:
        return AnsibleTaggedObject.tag_copy(self, set.__iter__(self), value_type=set)
