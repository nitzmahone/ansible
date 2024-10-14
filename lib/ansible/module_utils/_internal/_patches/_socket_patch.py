"""Patch `socket.socket` so that it works with types derived from `str`."""

from __future__ import annotations

import contextlib
import socket

from ...datatag import _AnsibleTaggedInt
from . import UntagArgsPatch, PatchType


class GetAddrInfoPatch(UntagArgsPatch):
    # DTFIX-U: install a lazy `socket` stub in sys.modules (at least on targets) so this patch can be auto-applied on import of `socket`
    _container = socket
    _attr = 'getaddrinfo'
    _patch_type = PatchType.Function

    @classmethod
    def _needs_patch(cls) -> bool:
        with contextlib.suppress(OSError):
            socket.getaddrinfo('127.0.0.1', _AnsibleTaggedInt(22))
            return False

        return True
