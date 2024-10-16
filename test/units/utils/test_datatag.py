from __future__ import annotations

import pytest

from ansible.module_utils.datatag import AnsibleTagHelper
from ansible.utils.datatag.tags import AnsibleSourcePosition, NotATemplate, TrustedAsTemplate, UndecryptableVaultedValue, VaultedValue
from ..module_utils.datatag.test_datatag import TestDatatagTarget as _TestDatatagTarget


class TestDatatagController(_TestDatatagTarget):
    tag_instances_with_reprs = [
        (AnsibleSourcePosition(src='/himom.yml', line=42, col=42), "AnsibleSourcePosition(src='/himom.yml', line=42, col=42)"),
        (NotATemplate(), "NotATemplate()"),
        (TrustedAsTemplate(), "TrustedAsTemplate()"),
        (UndecryptableVaultedValue(reason="because i said so"), "UndecryptableVaultedValue(reason='because i said so')"),
        (VaultedValue(ciphertext="hi mom I am a secret"), "VaultedValue(ciphertext='hi mom I am a secret')"),
    ]

    test_dataclass_tag_base_field_validation_fail_instances = [
        (AnsibleSourcePosition, dict(src=NotATemplate().tag(''))),
        (AnsibleSourcePosition, dict(line=NotATemplate().tag(1), src='')),
        (AnsibleSourcePosition, dict(col=NotATemplate().tag(1), src='')),
        (VaultedValue, dict(ciphertext=NotATemplate().tag(''))),
    ]

    test_dataclass_tag_base_field_validation_pass_instances = [
        (AnsibleSourcePosition, dict(src='/something')),
        (AnsibleSourcePosition, dict(src='/something', line=1)),
        (AnsibleSourcePosition, dict(src='/something', col=1)),
        (VaultedValue, dict(ciphertext='')),
    ]

    # DTFIX-MERGE: ensure we're calculating the correct set of values for this context
    @classmethod
    def container_test_cases(cls) -> list:
        return []

    # HACK: avoid `SKIPPED` notifications for inherited tests with no data
    def test_tag_copy(self) -> None:
        pass

    def test_instance_copy_roundtrip(self) -> None:
        pass

    def test_tag(self) -> None:
        pass


@pytest.mark.parametrize("sp, value", (
    (AnsibleSourcePosition(src="/hi"), "/hi"),
    (AnsibleSourcePosition(src="/hi", line=1), "/hi:1"),
    (AnsibleSourcePosition(src="/hi", line=1, col=2), "/hi:1:2"),
    (AnsibleSourcePosition(src="/hi", col=2), "/hi"),
    (AnsibleSourcePosition(src="/hi", line=0), "/hi"),
    (AnsibleSourcePosition(src="/hi", line=0, col=0), "/hi"),
    (AnsibleSourcePosition(src="/hi", col=0), "/hi"),
    (AnsibleSourcePosition(src="/hi", line=-1), "/hi"),
    (AnsibleSourcePosition(src="/hi", line=1, col=-1), "/hi:1"),
    (AnsibleSourcePosition(description='<something>'), "<something>"),
    (AnsibleSourcePosition(description='<something>', line=1), "<something>:1"),
    (AnsibleSourcePosition(src="/hi", description='<something>'), "/hi (<something>)"),
    (AnsibleSourcePosition(src="/hi", description='<something>', line=1), "/hi:1 (<something>)"),
), ids=str)
def test_ansible_source_position_str(sp: AnsibleSourcePosition, value: str) -> None:
    assert str(sp) == value


def test_tag_builtins():
    values = [123, 123.45, 'a string value', tuple([1, 2, 3]), [1, 2, 3], {1, 2, 3}, dict(one=1, two=2)]

    for original_val in values:
        tagged_val = TrustedAsTemplate().tag(original_val)
        zero_tagged_val = AnsibleTagHelper.tag(original_val, [])  # should return original value, not an empty tagged obj

        assert original_val == tagged_val  # equality should pass
        assert not TrustedAsTemplate.is_tagged_on(original_val)  # immutable original value via bool check
        assert TrustedAsTemplate.get_tag(original_val) is None  # immutable original value via get_tag
        assert not AnsibleTagHelper.tags(original_val)  # immutable original value via tags

        assert TrustedAsTemplate.is_tagged_on(tagged_val)
        assert TrustedAsTemplate.get_tag(tagged_val) is TrustedAsTemplate()  # singleton tag type, should be reference-equal
        assert original_val is zero_tagged_val  # original value should reference-equal the zero-tagged value

        somedata_tag = AnsibleSourcePosition(src="/foo", line=12, col=34)

        multi_tagged_val = somedata_tag.tag(tagged_val)
        assert tagged_val is not multi_tagged_val
        assert TrustedAsTemplate.is_tagged_on(multi_tagged_val)
        assert AnsibleSourcePosition.is_tagged_on(multi_tagged_val)
        assert TrustedAsTemplate.get_tag(multi_tagged_val) is TrustedAsTemplate()  # singleton tag type, should be reference-equal
        assert AnsibleSourcePosition.get_tag(multi_tagged_val) is somedata_tag
