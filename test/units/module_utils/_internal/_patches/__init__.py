from __future__ import annotations

import pkgutil
import importlib

from ansible.module_utils._internal import _patches


def enable_patches() -> None:
    """Import all patch modules and enable them."""
    for module_info in pkgutil.iter_modules(_patches.__path__, f'{_patches.__name__}.'):
        importlib.import_module(module_info.name)

    for patch in _patches.CallablePatch._concrete_patch_types:
        patch.patch()


enable_patches()
