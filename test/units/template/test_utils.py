from __future__ import annotations

import pytest

from ansible.template.jinja_common import DeferredMarker, DeferredMarkerError


def test_marker_repr(deferred_marker: DeferredMarker) -> None:
    with pytest.raises(DeferredMarkerError):
        repr(deferred_marker)


def test_marker_str(deferred_marker: DeferredMarker) -> None:
    with pytest.raises(DeferredMarkerError):
        str(deferred_marker)


def test_marker_getattr(deferred_marker: DeferredMarker) -> None:
    assert deferred_marker.foo is deferred_marker


def test_marker_getattr_dunder(deferred_marker: DeferredMarker) -> None:
    with pytest.raises(AttributeError):
        _unused = deferred_marker.__dunder_that_is_not_defined__


def test_marker_getitem(deferred_marker: DeferredMarker) -> None:
    assert deferred_marker['foo'] is deferred_marker
