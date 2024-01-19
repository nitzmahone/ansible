from __future__ import annotations

import types

from jinja2.runtime import StrictUndefined, Undefined

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

from .utils import Omit, AnsibleUndefined, TemplateContext

from ansible.utils.display import Display

_ANSIBLE_LAZY_TEMPLATE_SLOTS = tuple(('_templar',))

display = Display()


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
        # FIXME: is there a better way to include callables like these, so we're not playing whack-a-mole
        type(''.startswith),  # builtin_function_or_method
        type(Omit),
        Undefined,
        StrictUndefined,
        AnsibleUndefined,
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
        if not (tc := TemplateContext.current()):
            # FIXME: better exception type?
            raise ReferenceError("no TemplateContext is available")

        self._templar = tc.templar  # pylint: disable=assigning-non-slot  # slot defined in derived type

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
                # FIXME: what do we want here? such as HostVars, HostVarsVars
                # FIXME: we now have strict checking of variable types leaving templating, is this warning redundant?
                # FIXME: undefined types need to be here too? (prevent warnings from with_first_found loops with undefined values)
                if not isinstance(item, _AnsibleLazyTemplateMixin._ignore_types):
                    display.warning(f'Encountered unsupported {item_type} type.')

                return item

        tags_mapping = _try_get_internal_tags_mapping(item)
        value = dispatcher._instance_factory(item, tags_mapping)

        return value


@t.final
class _AnsibleLazyTemplateDict(_AnsibleTaggedDict, _AnsibleLazyTemplateMixin):
    __slots__ = _ANSIBLE_LAZY_TEMPLATE_SLOTS

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _AnsibleLazyTemplateMixin.__init__(self)

    def __getitem__(self, item: t.Any) -> t.Any:
        # FIXME: better access pattern for this?
        # FIXME: internally cache templated item responses for the lifetime of this wrapper so we don't repeatedly
        #  template the same values?
        return self._templar.environment._proxy_or_render_template(super().__getitem__(item), item)

    # FIXME: fully implement iteration support
    # FIXME: do we need to implement templated key support?

    def __str__(self):
        return self.__repr__()

    def __repr__(self):
        # delegate to the base class __repr__ impl
        return dict.__repr__(dict(self.items()))

    def items(self):
        for key, value in super().items():
            # FIXME: internally cache templated item responses for the lifetime of this wrapper so we don't repeatedly
            #  template the same values?
            yield key, self._templar.environment._proxy_or_render_template(value, key)

    def native_copy(self) -> dict:
        return dict(dict.items(self))


@t.final
class _AnsibleLazyTemplateList(_AnsibleTaggedList, _AnsibleLazyTemplateMixin):
    __slots__ = _ANSIBLE_LAZY_TEMPLATE_SLOTS

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _AnsibleLazyTemplateMixin.__init__(self)

    def __getitem__(self, item):
        # FIXME: better access pattern for this?
        # FIXME: internally cache templated item responses for the lifetime of this wrapper so we don't repeatedly
        #  template the same values?
        return self._templar.environment._proxy_or_render_template(super().__getitem__(item), item)

    def __iter__(self):
        for value in super().__iter__():
            yield self._templar.environment._proxy_or_render_template(value)

    def __str__(self):
        return self.__repr__()

    def __repr__(self):
        # delegate to the base class __repr__ impl
        return list.__repr__(list(self.__iter__()))

    def native_copy(self) -> list:
        return list(list.__iter__(self))


@t.final
class _AnsibleLazyTemplateTuple(_AnsibleTaggedTuple, _AnsibleLazyTemplateMixin):
    # nonempty __slots__ not supported for subtype of 'tuple'

    def __init__(self, *_args, **_kwargs):
        # NB: we're explicitly not calling super().__init__ here, since our hierarchy doesn't need it, and tuple's init is
        # object.__init__, which accepts no args beyond "self"
        _AnsibleLazyTemplateMixin.__init__(self)

    def __getitem__(self, item):
        # FIXME: better access pattern for this?
        # FIXME: internally cache templated item responses for the lifetime of this wrapper so we don't repeatedly
        #  template the same values?
        return self._templar.environment._proxy_or_render_template(super().__getitem__(item), item)

    def __iter__(self):
        for value in super().__iter__():
            yield self._templar.environment._proxy_or_render_template(value)

    def __str__(self):
        return self.__repr__()

    def __repr__(self):
        # delegate to the base class __repr__ impl
        return tuple.__repr__(tuple(self.__iter__()))

    def native_copy(self) -> tuple:
        return tuple(tuple.__iter__(self))


@t.final
class _AnsibleLazyTemplateSet(_AnsibleTaggedSet, _AnsibleLazyTemplateMixin):
    __slots__ = _ANSIBLE_LAZY_TEMPLATE_SLOTS

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _AnsibleLazyTemplateMixin.__init__(self)

    def __iter__(self):
        for value in super().__iter__():
            yield self._templar.environment._proxy_or_render_template(value)

    def __str__(self):
        return self.__repr__()

    def __repr__(self):
        # delegate to the base class __repr__ impl
        return set.__repr__(set(self.__iter__()))

    def native_copy(self) -> set:
        return set(set.__iter__(self))
