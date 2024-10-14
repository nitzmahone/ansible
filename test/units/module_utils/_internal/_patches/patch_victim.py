from __future__ import annotations

_value = 'module attr'


def patchme(value: str) -> str:
    return f'unpatched <{_value}> <{value}>'
