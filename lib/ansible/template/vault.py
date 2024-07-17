from __future__ import annotations

import dataclasses
import typing as t

from ansible._internal import _errors
from ansible.module_utils.common.messages import ErrorDetail, ErrorMessage

# noinspection PyProtectedMember
from ansible.module_utils.datatag import (
    AnsibleSingletonTagBase,
    AnsibleTaggedObject,
    Tripwire,
    _ANSIBLE_TAGGED_OBJECT_SLOTS,
    _AnsibleTagsMapping,
    AnsibleTagHelper,
)
from ansible.utils.datatag.tags import AnsibleSourcePosition, UndecryptableVaultedValue, _tag_dataclass_kwargs

# noinspection PyProtectedMember
from ansible.module_utils.datatag.access import (
    POORLY_NAMED_SENTINEL,
    _MutatingAccessContextBase,
    _NotifiableAccessContextBase,
)


# DTFIX-U: rename
@dataclasses.dataclass(**_tag_dataclass_kwargs)
class _VaultBombPoorlyNamedTag(AnsibleSingletonTagBase):
    pass


class UndecryptableVaultError(_errors.AnsibleCapturedError):
    """Error raised by VaultBomb when an undecryptable variable is accessed."""

    context = 'vault'
    default_prefix = "Attempt to use undecryptable variable."


class _VaultBomb(Tripwire):
    __slots__ = tuple(('_value',))

    def __init__(self, value: str) -> None:
        self._value = value

    @staticmethod
    def arm(value: str) -> _VaultBomb:
        # sample the tag, so we can preserve the ciphertext on the wrapped string
        uvv_tag = UndecryptableVaultedValue.get_tag(value)

        if not uvv_tag or not isinstance(value, str):
            raise ValueError('only strings tagged with UndecryptableVaultedValue can be armed')

        untagged_value = AnsibleTagHelper.as_untagged_type(value)

        # Propagate the original value's tags to the VaultBomb wrapper, keeping only the undecryptable tag on the inner value
        # DTFIX-U: Why not leave the inner value tagged as it was? Avoiding AccessContexts maybe?
        wrapped = AnsibleTagHelper.tag_copy(value, _VaultBomb(uvv_tag.tag(untagged_value)))
        wrapped = UndecryptableVaultedValue.untag(wrapped)
        wrapped = AnsibleTagHelper.tag(wrapped, _VaultBombPoorlyNamedTag())
        return wrapped

    def disarm(self) -> str:
        unwrapped = AnsibleTagHelper.tag_copy(self, self._value)
        unwrapped = _VaultBombPoorlyNamedTag.untag(unwrapped)
        return unwrapped

    def trip(self) -> t.NoReturn:
        """Detonate this VaultBomb via the generic Tripwire mixin."""
        self.detonate()

    def detonate(self, *_args, **_kwargs) -> t.NoReturn:
        """
        Immediately raise UndecryptableVaultError.
        This method accepts (and ignores) arbitrary args/kwargs, as it stands in for a number of dunder methods with varying signatures.
        """
        obj = AnsibleTagHelper.as_untagged_type(self._value)

        if source_pos := AnsibleSourcePosition.get_tag(self):
            obj = source_pos.tag(obj)

        uvv_tag = UndecryptableVaultedValue.get_tag(self._value)

        raise UndecryptableVaultError(
            obj=obj,
            error_detail=ErrorDetail(
                errors=[ErrorMessage(msg=uvv_tag.reason)],
                formatted_traceback=uvv_tag.traceback,
            ),
        )


class _AnsibleTaggedVaultBomb(_VaultBomb, AnsibleTaggedObject):
    __slots__ = _ANSIBLE_TAGGED_OBJECT_SLOTS

    _detonate_methods = (
        # Intercept `str` dunder methods.
        # This ensures any attempted usage as a `str` will detonate.
        '__add__',
        '__contains__',
        '__delattr__',
        '__eq__',
        '__format__',
        '__ge__',
        '__getitem__',
        '__getstate__',
        '__gt__',
        '__hash__',
        '__iter__',
        '__le__',
        '__len__',
        '__lt__',
        '__mod__',
        '__mul__',
        '__ne__',
        '__reduce__',
        '__reduce_ex__',
        '__repr__',
        '__rmod__',
        '__rmul__',
        '__sizeof__',
        '__str__',
        # Ensure that attempted usage of any undefined method will also detonate.
        '__getattr__',
    )

    @classmethod
    def _instance_factory(cls, value: t.Any, tags_mapping: _AnsibleTagsMapping) -> _AnsibleTaggedVaultBomb:
        instance = cls(value._value if isinstance(value, _VaultBomb) else value)
        instance._ansible_tags_mapping = tags_mapping
        return instance

    @classmethod
    def _init_class(cls):
        # deferred imperative customization, invoked by AnsibleTaggedObject
        # DTFIX-U: __setattr__ needs to be there at least for __init__
        for name in cls._detonate_methods:
            setattr(cls, name, cls.detonate)


# tracks undecryptable values encountered while templating
class UndecryptableAccessMutator(_MutatingAccessContextBase):
    _tag_type_interest = frozenset([UndecryptableVaultedValue])

    def _notify(self, o: t.Any) -> t.Any:
        # DTFIX-U: FDI037 - is_tagged_on may not be necessary, depending on layered mutation support
        if UndecryptableVaultedValue.is_tagged_on(o):
            self._tripped = True
            return _VaultBomb.arm(o)

        return POORLY_NAMED_SENTINEL


class DetonateVaultBombsTripwire(_NotifiableAccessContextBase):
    # DTFIX-U: we might be able to kill this off now, since template finalize always trips these anyway; if not, explain why
    _tag_type_interest = frozenset([_VaultBombPoorlyNamedTag])

    def _notify(self, o: t.Any) -> t.Any:
        if isinstance(o, _VaultBomb):
            o.detonate()

        return POORLY_NAMED_SENTINEL
