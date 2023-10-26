from __future__ import annotations

import typing as t

from ansible.errors import AnsibleError

# noinspection PyProtectedMember
from ansible.module_utils.datatag import (
    AnsibleSingletonTagBase,
    AnsibleSourcePosition,
    AnsibleTaggedObject,
    UndecryptableVaultedValue,
    _ANSIBLE_TAGGED_OBJECT_SLOTS,
    _AnsibleTagsMapping,
    _NO_INSTANCE_STORAGE,
)

# noinspection PyProtectedMember
from ansible.module_utils.datatag.access import (
    POORLY_NAMED_SENTINEL,
    _MutatingAccessContextBase,
)


# FIXME: rename
class _VaultBombPoorlyNamedTag(AnsibleSingletonTagBase):
    __slots__ = _NO_INSTANCE_STORAGE


class UndecryptableVaultError(AnsibleError):
    pass


class _VaultBomb:
    __slots__ = tuple(('_value',))

    def __init__(self, value: str) -> None:
        self._value = value

    @staticmethod
    def arm(value: str) -> _VaultBomb:
        # sample the tag, so we can preserve the ciphertext on the wrapped string
        uvv_tag = UndecryptableVaultedValue.get_tag(value)

        if not uvv_tag or not isinstance(value, str):
            raise ValueError('only strings tagged with UndecryptableVaultedValue can be armed')

        wrapped = AnsibleTaggedObject.tag_copy(value, _VaultBomb(uvv_tag.tag(str(value))))
        wrapped = AnsibleTaggedObject.untag(wrapped, UndecryptableVaultedValue)
        wrapped = AnsibleTaggedObject.tag(wrapped, _VaultBombPoorlyNamedTag())
        return wrapped

    def disarm(self) -> str:
        unwrapped = AnsibleTaggedObject.tag_copy(self, self._value)
        unwrapped = _VaultBombPoorlyNamedTag.untag(unwrapped)
        return unwrapped

    def detonate(self, *_args, **_kwargs) -> None:
        # FIXME: use central error forensics
        msg = "attempt to use undecryptable variable"
        source_pos = AnsibleSourcePosition.get_tag(self)
        if source_pos:
            msg = f'{msg} from {str(source_pos)!r}'
        raise UndecryptableVaultError(msg)


class _AnsibleTaggedVaultBomb(_VaultBomb, AnsibleTaggedObject):
    __slots__ = _ANSIBLE_TAGGED_OBJECT_SLOTS

    _detonate_methods = (
        '__delattr__',
        '__eq__',
        '__format__',
        '__ge__',
        '__getattr__',
        '__getstate__',
        '__gt__',
        '__hash__',
        '__iter__',
        '__le__',
        '__lt__',
        '__ne__',
        '__reduce__',
        '__reduce_ex__',
        '__repr__',
        '__sizeof__',
        '__str__',
    )

    @classmethod
    def _instance_factory(cls, value: t.Any, tags_mapping: _AnsibleTagsMapping) -> AnsibleTaggedObject:
        instance = cls(value._value if isinstance(value, _VaultBomb) else value)
        instance._ansible_tags_mapping = tags_mapping
        return instance

    @classmethod
    def _init_class(cls):
        # deferred imperative customization, invoked by AnsibleTaggedObject
        # explicitly set __getattr__ and most methods inherited from "object" to detonate on access
        # FIXME: __setattr__ needs to be there at least for __init__
        for name in cls._detonate_methods:
            setattr(cls, name, cls.detonate)


# tracks undecryptable values encountered while templating
class UndecryptableAccessTripwire(_MutatingAccessContextBase):
    _tag_type_interest = frozenset([UndecryptableVaultedValue])

    def __init__(self):
        self._tripped = False

    def _notify(self, o: t.Any) -> t.Any:
        # FIXME: FDI037 - is_tagged_on may not be necessary, depending on layered mutation support
        if UndecryptableVaultedValue.is_tagged_on(o):
            self._tripped = True
            return _VaultBomb.arm(o)

        return POORLY_NAMED_SENTINEL

    @property
    def is_tripped(self) -> bool:
        return self._tripped


class DetonateVaultBombsTripwire(_MutatingAccessContextBase):
    _tag_type_interest = frozenset([_VaultBombPoorlyNamedTag])

    def _notify(self, o: t.Any) -> t.Any:
        # FIXME: FDI037 - is_tagged_on may not be necessary, depending on layered mutation support
        if isinstance(o, _VaultBomb):
            o.detonate()

        return POORLY_NAMED_SENTINEL
