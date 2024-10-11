from __future__ import annotations

import abc
import contextlib
import inspect

import typing as t


@t.runtime_checkable
class PatchedTarget(t.Protocol):
    """Protocol for objects with a close method."""
    patch_enabled: bool


class CallablePatch(abc.ABC):
    patch_enabled: t.ClassVar[bool] = False
    _unpatched: t.ClassVar[t.Callable | None] = None
    _patch_types: set[type[CallablePatch]] = set()

    _container: t.ClassVar[t.Any]
    _attr: t.ClassVar[str]

    def __new__(cls, *args, **kwargs) -> t.Any:
        # HACK: this should really be classmethod __call__
        if cls.patch_enabled:
            return cls._patched_impl(*args, **kwargs)

        return cls._unpatched(*args, **kwargs)

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
    def _set_patch(cls, patch: t.Callable) -> None:
        setattr(cls._container, cls._attr, patch)

    @classmethod
    def _unpatch(cls) -> None:
        cls._set_patch(cls._unpatched)

    @classmethod
    def patch(cls) -> None:
        maybe_unpatched = cls._get_current_value()

        if isinstance(maybe_unpatched, PatchedTarget):  # using a protocol lets us be more resilient to module unload weirdness
            return

        cls._unpatched = maybe_unpatched

        if cls._needs_patch():
            cls._set_patch(patch=cls)
            cls.patch_enabled = True

            if cls._needs_patch():
                cls._unpatch()
                raise RuntimeError(f"patching {cls._container.__name__}.{cls._attr} had no effect")

    @classmethod
    def __init_subclass__(cls, **kwargs):
        CallablePatch._patch_types.add(cls)

        if not inspect.isabstract(cls):
            cls.patch()

    @classmethod
    @contextlib.contextmanager
    def disable_patches(cls) -> t.Iterable[None]:
        for patch_type in cls._patch_types:
            patch_type.patch_enabled = False

        try:
            yield
        finally:
            for patch_type in cls._patch_types:
                patch_type.patch_enabled = True


class UntagArgsPatch(CallablePatch, abc.ABC):
    @classmethod
    def _patched_impl(cls, *args, **kwargs):
        from ...datatag import AnsibleTagHelper

        return cls._unpatched(
            *AnsibleTagHelper.as_untagged_type(args, recursive=True),
            **AnsibleTagHelper.as_untagged_type(kwargs, recursive=True)
        )
