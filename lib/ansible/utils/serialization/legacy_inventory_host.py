"""
Backwards compatibility profile for serialization for persisted ansible-inventory output for the legacy `--host` output from inventory scripts.
Behavior is equivalent to pre 2.18 `AnsibleJSONEncoder` with vault_to_text=True.
"""

from __future__ import annotations

from ..._internal import _serialization
from . import legacy as _legacy


class _InventoryVariableVisitor(_legacy._LegacyVariableVisitor, _serialization.StateTrackingMixIn):
    """State-tracking visitor implementation that applies blanket trust to all hostvars from inventory script legacy `--host` output."""

    @property
    def _allow_trust(self) -> bool:
        return True


class _Profile(_legacy._Profile):
    visitor_type = _InventoryVariableVisitor
    encode_strings_as_utf8 = True


class Encoder(_legacy.Encoder):
    _profile = _Profile


class Decoder(_legacy.Decoder):
    _profile = _Profile
