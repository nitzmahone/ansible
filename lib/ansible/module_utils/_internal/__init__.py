from __future__ import annotations

import typing as t

is_controller: bool = False
"""Set to True automatically when this module is imported into an Ansible controller context."""


def get_controller_types() -> t.Sequence[type]:
    """Called to augment serialization maps; this implementation is replaced with the one from ansible._internal in controller contexts."""
    return ()
