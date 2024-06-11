from __future__ import annotations

from ansible.utils.native_jinja import NativeJinjaText


def test_native_jinja_shim():
    value = NativeJinjaText("hi mom")
    assert value == "hi mom"
    assert type(value) is str  # pylint: disable=unidiomatic-typecheck
