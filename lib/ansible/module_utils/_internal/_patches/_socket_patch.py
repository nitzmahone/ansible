"""Patch `socket.socket` so that it works with types derived from `str`."""

from __future__ import annotations

import contextlib
import socket

from ...datatag import _AnsibleTaggedInt
from . import UntagArgsPatch


class GetAddrInfoPatch(UntagArgsPatch):
    _container = socket
    _attr = 'getaddrinfo'

    @classmethod
    def _needs_patch(cls) -> bool:
        with contextlib.suppress(OSError):
            socket.getaddrinfo('127.0.0.1', _AnsibleTaggedInt(22))
            return False

        return True
