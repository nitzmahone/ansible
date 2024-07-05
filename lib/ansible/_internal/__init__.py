from __future__ import annotations

import typing as t

from ansible.module_utils import _internal


def get_controller_types() -> t.Sequence[type]:
    """Injected into module_utils code to augment serialization maps with controller-only types."""
    from ansible.template import lazy_containers

    return lazy_containers._AnsibleLazyTemplateDict, lazy_containers._AnsibleLazyTemplateList


_internal.get_controller_types = get_controller_types
_internal.is_controller = True
