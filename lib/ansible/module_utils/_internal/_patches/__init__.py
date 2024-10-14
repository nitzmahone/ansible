from __future__ import annotations

import abc
import contextlib
import enum
import inspect
import functools

import typing as t


@t.runtime_checkable
class PatchedTarget(t.Protocol):
    """Protocol for patch functions to allow access to the owning Patch class implementation."""
    patch_enabled: CallablePatch


class PatchType(enum.Enum):
    Function = enum.auto()
    InstanceMethod = enum.auto()
    ClassMethod = enum.auto()
    GetterProperty = enum.auto()
    StaticMethod = enum.auto()


class CallablePatch(abc.ABC):
    patch_enabled: t.ClassVar[bool] = False

    _unpatched: t.ClassVar[t.Callable | None] = None
    _concrete_patch_types: t.ClassVar[set[type[CallablePatch]]] = set()
    _container: t.ClassVar[t.Any]
    _attr: t.ClassVar[str]
    _patch_type: t.ClassVar[PatchType]

    def __get__(self, instance, owner=None):
        if owner is None:
            owner = type(owner)
        if self._patch_type == PatchType.ClassMethod:
            return functools.partial(self, owner)
        if self._patch_type == PatchType.InstanceMethod:
            return functools.partial(self, instance)
        if self._patch_type == PatchType.GetterProperty:
            return self(instance)
        if self._patch_type == PatchType.StaticMethod:
            return self

        raise NotImplementedError()

    def __call__(self, *args, **kwargs) -> t.Any:
        if self.patch_enabled:
            return self._patched_impl(*args, **kwargs)

        # FIXME: if we ever end up using this, need to account for popping cls from args when directly calling the unpatched method
        return self._unpatched(*args, **kwargs)

    @classmethod
    def is_patched(cls) -> bool:
        return isinstance(cls._container.__dict__[cls._attr], PatchedTarget)  # using a protocol lets us be more resilient to module unload weirdness

    @classmethod
    @abc.abstractmethod
    def _needs_patch(cls) -> bool: ...

    @classmethod
    @abc.abstractmethod
    def _patched_impl(cls, *args, **kwargs) -> t.Any: ...

    @classmethod
    def _get_current_value(cls) -> t.Any:
        return getattr(cls._container, cls._attr)

    @classmethod
    def _prepare_patch(cls) -> t.Any:
        return cls()

    @classmethod
    def _set_patch(cls, patch: t.Callable) -> None:
        setattr(cls._container, cls._attr, patch)

    @classmethod
    def _unpatch(cls) -> None:
        cls._set_patch(cls._unpatched)

    @classmethod
    def patch(cls) -> None:
        current = cls._get_current_value()

        if cls.is_patched():
            return

        cls._unpatched = current

        if cls._needs_patch():
            cls._set_patch(patch=cls._prepare_patch())

            if not cls.is_patched():
                raise RuntimeError('oops')

            cls.patch_enabled = True

            if cls._needs_patch():
                cls._unpatch()
                raise RuntimeError(f"patching {cls._container.__name__}.{cls._attr} had no effect")

    @classmethod
    def __init_subclass__(cls, **kwargs):
        if not inspect.isabstract(cls):
            cls._concrete_patch_types.add(cls)

    @classmethod
    @contextlib.contextmanager
    def disable_patch(cls) -> t.Iterator[None]:
        cls.patch_enabled = False

        try:
            yield
        finally:
            cls.patch_enabled = True


class UntagArgsPatch(CallablePatch, abc.ABC):
    @classmethod
    def _patched_impl(cls, *args, **kwargs):
        from ...datatag import AnsibleTagHelper

        return cls._unpatched(
            *AnsibleTagHelper.as_untagged_type(args, recursive=True),
            **AnsibleTagHelper.as_untagged_type(kwargs, recursive=True)
        )

    @classmethod
    def _get_patch(cls):
        def func(*args, **kwargs) -> str:
            from ...datatag import AnsibleTagHelper

            return cls._unpatched(
                *AnsibleTagHelper.as_untagged_type(args, recursive=True),
                **AnsibleTagHelper.as_untagged_type(kwargs, recursive=True)
            )

        return func
