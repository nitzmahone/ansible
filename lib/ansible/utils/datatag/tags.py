from __future__ import annotations

import dataclasses
import os
import types
import typing as t

from ansible.module_utils.datatag import _tag_dataclass_kwargs, AnsibleDatatagBase, AnsibleSingletonTagBase


@dataclasses.dataclass(**_tag_dataclass_kwargs)
class AnsibleSourcePosition(AnsibleDatatagBase):
    """
    A tag that stores origin information for the tagged value.
    Since these are created very frequently, we do not ensure the validity of the inputs at creation-time.
    """
    # DTFIX-MERGE: is this public or not?
    # DTFIX-MERGE: rename class to Origin, and rename src_pos/source_position/etc. args to origin
    # DTFIX-MERGE: should we require one of path(src)/description?
    src: str | None = None  # DTFIX-MERGE: rename src to path
    """The path from which the tagged content originated. If not available, description should be provided."""
    description: str | None = None
    """A description of the origin, for display to users. Primarily used when path is not set."""
    # DTFIX-MERGE: should these end with no/num/_no/_num
    line: t.Optional[int] = None
    col: t.Optional[int] = None
    redact: bool = False
    """If set to True, content associated with this origin will not be displayed unless overriden by the user."""

    @classmethod
    def get_or_create_tag(cls, value: t.Any, path: str | os.PathLike | None) -> AnsibleSourcePosition:
        """Return the tag from the given value, creating a tag from the provided path if no tag was found."""
        tag = cls.get_tag(value)

        if not tag:
            if path is not None:
                path = str(path)

            tag = AnsibleSourcePosition(src=path)  # convert tagged strings and path-like values to a native str

        return tag

    def replace(
            self,
            src: str | types.EllipsisType = ...,
            description: str | types.EllipsisType = ...,
            line: t.Optional[int] | types.EllipsisType = ...,
            col: t.Optional[int] | types.EllipsisType = ...,
    ) -> t.Self:
        return dataclasses.replace(self,
                                   **{key: value for key, value in dict(
                                       src=src,
                                       description=description,
                                       line=line,
                                       col=col,
                                   ).items() if value is not ...})  # type: ignore[arg-type]

    def _post_validate(self) -> None:
        # DTFIX-MERGE: temporary hack to track down overlooked src='<...' cases, relative paths, and other things that aren't absolute paths
        if self.src and not self.src.startswith('/'):
            raise RuntimeError(f'OOPS, is {self.src!r} actually a path? -- it does not start with "/"')

    def __str__(self) -> str:
        """Renders the source position in the form of file:line:col, omitting missing/invalid elements from the right."""
        if self.src:
            value = self.src
        elif self.description:
            value = self.description
        else:
            value = '<unknown>'

        if self.line and self.line > 0:
            value += f':{self.line}'

            if self.col and self.col > 0:
                value += f':{self.col}'

        if self.src and self.description:
            value += f' ({self.description})'

        return value


@dataclasses.dataclass(**_tag_dataclass_kwargs)
class VaultedValue(AnsibleDatatagBase):
    """Tag for vault-encrypted strings that carries the original ciphertext for round-tripping."""
    ciphertext: str


@dataclasses.dataclass(**_tag_dataclass_kwargs)
class UndecryptableVaultedValue(AnsibleDatatagBase):
    """Additional tag for vaulted values we couldn't decrypt on load that may contain forensic detail."""
    reason: str
    traceback: str | None = None


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


@dataclasses.dataclass(**_tag_dataclass_kwargs)
class _EncryptedSource(AnsibleSingletonTagBase):
    """
    For internal use only.
    Indicates the tagged value was sourced from an encrypted file.
    Currently applied only by DataLoader.load_from_file().
    """
