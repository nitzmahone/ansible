from __future__ import annotations

import typing as t

import pytest

from ansible.parsing.vault import VaultSecretsContext, VaultSecret

# DTFIX-MERGE: it'd be nice not to need to sync all controller-only fixtures here, but sunder values are excluded from `import *`,
#  and if we don't sunder the fixtures, they'll be unused args. Could also be handled with a managed import, module getattr, or others.
__all__ = ['_zap_vault_secrets_context', '_empty_vault_secrets_context', '_vault_secrets_context']


@pytest.fixture
def _zap_vault_secrets_context() -> t.Generator[None]:
    """A fixture that disables any existing VaultSecretsContext both before and after the test."""
    # DTFIX-FUTURE: come up with a decent way to share this kind of fixture between controller contexts; we can't put it in top-level conftest since
    #  modules can't use it. Import from a shared controller fixture location, area-specific fixture support files, or ?
    VaultSecretsContext._current = None

    try:
        yield
    finally:
        VaultSecretsContext._current = None


@pytest.fixture
def _empty_vault_secrets_context(_zap_vault_secrets_context) -> t.Generator[None]:
    """A fixture that provides a `VaultSecretsContext` with no secrets."""
    VaultSecretsContext.initialize(VaultSecretsContext([]))

    yield


@pytest.fixture
def _vault_secrets_context(_zap_vault_secrets_context) -> t.Generator[None]:
    """A fixture that provides a `VaultSecretsContext` populated with a single default secret under the default id."""
    secret = VaultSecret(b'secretbytesblah')

    VaultSecretsContext.initialize(VaultSecretsContext(secrets=[('default', secret)]))

    yield
