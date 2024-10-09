from __future__ import annotations

import typing as t

from ansible.utils.datatag.tags import NotATemplate, TrustedAsTemplate


def apply_trust(value: t.Any) -> t.Any:
    """
    Filter that returns a tagged copy of the input value with TrustedAsTemplate and removes NotATemplate (if present).
    Tags are not managed recursively for containers.
    """
    return NotATemplate.untag(TrustedAsTemplate().tag(value))


class FilterModule:
    def filters(self) -> dict[str, t.Callable]:
        return dict(apply_trust=apply_trust)
