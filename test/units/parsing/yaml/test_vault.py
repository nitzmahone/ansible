from __future__ import annotations

import json

from ansible.utils.datatag.tags import VaultedValue, UndecryptableVaultedValue
from ansible.parsing.utils.yaml import from_yaml
from ansible.parsing.vault import VaultSecret, VaultLib


def test_from_yaml() -> None:
    secret = VaultSecret(b'my secret')
    encrypted_value = VaultLib().encrypt('mom', secret)
    vault_secrets: list[tuple[str, VaultSecret]] = [("default", secret)]
    ciphertext = encrypted_value.decode()
    data = dict(hi=dict(
        __ansible_vault=ciphertext,
    ))
    file_name = '/nope'

    result = from_yaml(data=json.dumps(data), file_name=file_name, vault_secrets=vault_secrets, json_only=True)

    assert result == dict(hi='mom')
    assert (vv := VaultedValue.get_tag(result['hi']))
    assert vv.ciphertext == ciphertext


def test_from_yaml_invalid_vaulted_value() -> None:
    data = dict(hi=dict(
        __ansible_vault='bogus, cannot decrypt, wrong format',
    ))

    result = from_yaml(data=json.dumps(data), json_only=True)

    assert result == dict(hi='bogus, cannot decrypt, wrong format')
    assert (uvv := UndecryptableVaultedValue.get_tag(result['hi']))
    assert uvv.reason == 'Input is not vault encrypted data.'
