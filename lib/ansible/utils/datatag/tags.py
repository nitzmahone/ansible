from __future__ import annotations

import dataclasses
import types
import typing as t

from ansible.module_utils.datatag import _tag_dataclass_kwargs, AnsibleDatatagBase, AnsibleSingletonTagBase


@dataclasses.dataclass(**_tag_dataclass_kwargs)
class AnsibleSourcePosition(AnsibleDatatagBase):
    """
    A tag that stores origin location information of a string.
    Since these are created very frequently, we do not ensure the validity of the inputs at creation-time.
    """
    # DTFIX-U: come up with a standard non-magic-string way to denote non-file sources (eg, CLI extra args, envvar, `<string>`)
    src: str
    # DTFIX-U: should these end with no/num/_no/_num?
    line: t.Optional[int] = None
    col: t.Optional[int] = None

    def replace(
            self,
            src: str | types.EllipsisType = ...,
            line: t.Optional[int] | types.EllipsisType = ...,
            col: t.Optional[int] | types.EllipsisType = ...,
    ) -> t.Self:
        return dataclasses.replace(self,
                                   **{key: value for key, value in dict(src=src, line=line, col=col).items() if value is not ...})  # type: ignore[arg-type]

    def __str__(self) -> str:
        """Renders the source position in the form of file:line:col, omitting missing/invalid elements from the right."""
        if self.line and self.line > 0:
            if self.col and self.col > 0:
                return f'{self.src}:{self.line}:{self.col}'

            return f'{self.src}:{self.line}'

        return f'{self.src}'


@dataclasses.dataclass(**_tag_dataclass_kwargs)
class VaultedValue(AnsibleDatatagBase):
    """Tag for vault-encrypted strings that carries the original ciphertext for round-tripping."""
    ciphertext: str


@dataclasses.dataclass(**_tag_dataclass_kwargs)
class UndecryptableVaultedValue(AnsibleDatatagBase):
    """Additional tag for vaulted values we couldn't decrypt on load that may contain forensic detail."""
    reason: str


@dataclasses.dataclass(**_tag_dataclass_kwargs)
class TrustedAsTemplate(AnsibleSingletonTagBase):
    """
    Indicates the tagged string is trusted to parse and render as a template.
    Do *NOT* apply this tag to data from untrusted sources, as this would allow code injection during templating.
    """


@dataclasses.dataclass(**_tag_dataclass_kwargs)
class NotATemplate(AnsibleSingletonTagBase):
    """
    Used for internal things like error messages that might contain a template-ish looking thing but that we don't
    want to spam users with untrusted warnings or unnecessarily recurse into containers we know shouldn't be templated (for performance, not security).
    """
