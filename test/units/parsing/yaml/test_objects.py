from __future__ import annotations

import pytest

from ansible.parsing.yaml import objects


@pytest.mark.parametrize("class_type", (
    objects.AnsibleBaseYAMLObject,
    objects.AnsibleMapping,
    objects.AnsibleUnicode,
    objects.AnsibleSequence,
    objects.AnsibleVaultEncryptedUnicode,
))
def test_classes(class_type: type) -> None:
    with pytest.raises(NotImplementedError):
        class_type()

    with pytest.raises(NotImplementedError):
        class_type(True, named_arg=True)  # verify args and kwargs are swallowed
